"""`sum_stage_usage` tool の実装 (skill-usage-audit 向け工程ごとの消費の集計)。

監査した transcript 1 件について、呼び出し元が渡す工程の境界 (工程名と、最初と最後の
record の uuid) の内側を数え、工程ごとの所要・subagent 数・token を slice JSON に書く。

**工程の区切りは決めない** — 区切りは監査対象 skill の手順を読む判断で、監査 subagent が
transcript を読んで決める。本 tool が持つのは境界の内側の算術だけ。監査 subagent に
集計させないのは、transcript に混ざる外部の文に仕込まれた命令を実行する経路 (Bash) を
監査 subagent に持たせないため。

数え方 (record の形の知識は `adapter.transcript` が持つ):
- token は assistant record の usage の input + output + cache_creation。cache_read は
  足さずに別掲する (同じ context を turn ごとに再計上するので、足すと工程の大小が
  context の長さで決まる)
- `message.id` ごとに file 順で最後の record だけを数える (前の record は書きかけの値)。
  1 つの応答の record が工程の境界をまたぐときは、最初の record がある工程に帰属させる
- subagent は工程内の起動 tool_use に対応する体の transcript を同じ規則で数え、入れ子の
  体は親に畳む。subagent 数に入れ子は含めない
- workflow の subagent (`subagents/workflows/<run>/`) は、工程内の `Workflow` tool_use の結果が
  持つ run の id で run の体を引き、その tool_use のある工程に帰属させる。subagent 数には meta を
  読めた run の体を 1 体ずつ足す。meta の読めない体は path から run が分かっても入れ子かどうかが
  分からないので数に足さず、その起動は invariant 8 のとおり null と理由で返す。同じ run を指す
  起動が重なったら、transcript で最初の起動にだけ帰属させる (工程ごとに足すと工程の合計で
  二重になる)。結果が run の id を持たない `Workflow` (拒否・失敗) は run を起こしていないので
  数えず、run の dir があって体が 0 体の run は 0 体と数える
- 起動元に辿れない subagent (meta が無い・読めない・親が無い・親の連鎖が循環する・どの
  `Workflow` の結果にも結び付かない run の体・agent id が重なる体) はどの工程にも帰属できない
  ので、工程の値に入れず `meta.unresolved_agent_ids` に出す
- session dir の下で subagent の transcript にも workflow の進行ログにも当たらない jsonl は、
  列挙から外れる体がありうる兆候なので `meta.unrecognized_nested_files` に件数を出す

対応が取れない値は 0 にせず `null` と理由で返す (0 だと消費が無かった工程と区別できない)。

invariant 一覧 (テストが項目ごとに拒否か値の検査を 1 本持つ):
1. token は message.id ごとの最後の record だけを数え、cache_read は別掲する
2. subagent は起動元の工程に帰属させ、入れ子は親に畳む
3. 対応が取れない subagent は 0 にせず `null` と理由で返す
4. 起動元に辿れない subagent は `meta.unresolved_agent_ids` に出す
5. 境界の内側の record だけを数え、応答の最終 usage はその応答の最初の record が属する工程に帰属させる
6. 不正な stage 入力 (未知 uuid・境界の逆転) は当該 stage の `error` に出し、他の stage を止めない
7. transcript lake の外の path・.jsonl 以外は `StageUsageRejected` で拒否する
8. workflow の subagent は run を指す最初の `Workflow` tool_use の工程に帰属させ、run の体を引けない (dir が無い・transcript の無い体がある・meta の読めない体がある・agent id が重なる) workflow は 3 と同じく `null` と理由で返す
9. session dir の下で置き場に当たらない jsonl は `meta.unrecognized_nested_files` に件数を出す

出力: output_dir に stage-usage-<timestamp>.json を書き、**path と meta だけを返す**。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from adapter.transcript import (
    _iter_jsonl,
    message_id_of,
    resolve_now,
    subagent_files_of,
    subagent_launch_ids_of,
    unrecognized_subagent_files_of,
    usage_of,
    workflow_run_dir_exists,
    workflow_launch_ids_of,
    workflow_run_of,
)
from artifacts import prepare_output_dir

DEFAULT_TRANSCRIPTS_DIR = Path("~/.claude/projects").expanduser()
DEFAULT_OUTPUT_DIR = Path("/tmp/skill-usage-audit")

COUNTED_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens")


class StageUsageRejected(ValueError):
    """受け付けない入力 (lake の外の path・形の崩れた境界)。文言は LLM に届く。"""


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fp:
        return list(_iter_jsonl(fp))


def sum_tokens(records: list[dict], start: int = 0, end: int | None = None) -> dict[str, int]:
    """records[start:end+1] の消費を数える。書きかけの値を拾わないよう、`message.id` の
    最終値と最初の位置は file 全体から取る。"""
    end = len(records) - 1 if end is None else end
    latest: dict[str, dict[str, int]] = {}
    first_at: dict[str, int] = {}
    counted: list[dict[str, int]] = []
    for position, rec in enumerate(records):
        usage = usage_of(rec)
        if usage is None:
            continue
        message_id = message_id_of(rec)
        if message_id:
            latest[message_id] = usage
            first_at.setdefault(message_id, position)
        elif start <= position <= end:
            counted.append(usage)
    counted += [latest[i] for i, position in first_at.items() if start <= position <= end]
    return {
        "tokens": sum(usage[key] for usage in counted for key in COUNTED_KEYS),
        "cache_read_input_tokens": sum(usage["cache_read_input_tokens"] for usage in counted),
    }


def resolve_roots(files: list, linked_runs: set[str]) -> dict[str, str | None]:
    """agent id → 起動元の top-level の agent id。辿れない体は None。

    meta が無い・読めない体、親の meta が無い体、親の連鎖が循環する体、辿り着いた先が
    起動の tool_use id も、`Workflow` の結果に結び付いた run (`linked_runs`) も持たない体は、
    どの起動に属するかが決まらない。agent id が重なる体は親の連鎖も起動も 1 つに決まらない。
    """
    by_id: dict[str, object] = {}
    duplicated = set()
    for f in files:
        if f.agent_id in by_id:
            duplicated.add(f.agent_id)
        by_id[f.agent_id] = f
    roots: dict[str, str | None] = {}
    for f in files:
        current, seen = f, set()
        while current is not None and current.meta_readable and current.parent_agent_id:
            if current.agent_id in seen:
                current = None
                break
            seen.add(current.agent_id)
            current = by_id.get(current.parent_agent_id)
        reached_launch = current is not None and current.meta_readable and (
            current.tool_use_id or current.workflow_run_id in linked_runs)
        resolved = reached_launch and not (seen | {f.agent_id, current.agent_id}) & duplicated
        roots[f.agent_id] = current.agent_id if resolved else None
    return roots


def sum_subagents(launch_ids: list[str], workflow_runs: dict[str, str], transcript: Path,
                  linked_runs: set[str]) -> dict:
    """工程内の起動 (`Task` / `Agent` の tool_use id と、run を指す最初の `Workflow` の tool_use id
    → run の id) に対応する体を数える。`linked_runs` は transcript 全体で結果に結び付いた run。"""
    result: dict = {"count": len(launch_ids), "tokens": None,
                    "cache_read_input_tokens": None, "unmatched_tool_use_ids": []}
    if not launch_ids and not workflow_runs:
        result.update(tokens=0, cache_read_input_tokens=0)
        return result
    files = subagent_files_of(transcript)
    if files is None:
        result["unmatched_tool_use_ids"] = [*launch_ids, *workflow_runs]
        result["reason"] = "subagents dir が無い"
        return result
    # 起動元へ直接対応する体 (入れ子でなく meta を読めた体)
    top = [f for f in files if f.meta_readable and not f.parent_agent_id]
    by_tool_use: dict[str, list] = {}
    for f in top:
        if f.tool_use_id:
            by_tool_use.setdefault(f.tool_use_id, []).append(f)
    # 起動に対応した体の agent id → 起動の tool_use id。agent id が重なる体は複数の起動に対応しうる
    launch_of: dict[str, set[str]] = {}
    for launch_id in launch_ids:
        found = by_tool_use.get(launch_id, [])
        for f in found:
            launch_of.setdefault(f.agent_id, set()).add(launch_id)
        # 対応する体が無いか、複数あってどれがその起動の体かが決まらない
        if len(found) != 1:
            result["unmatched_tool_use_ids"].append(launch_id)
    for launch_id, run_id in workflow_runs.items():
        # run の体は path から決まるので、meta の読めない体も入れる (起動元へ解決できず数えられない
        # 体として下で起動ごと null にする)
        run = [f for f in files if f.workflow_run_id == run_id and not f.parent_agent_id]
        if not run and not workflow_run_dir_exists(transcript, run_id):
            result["unmatched_tool_use_ids"].append(launch_id)
        for f in run:
            launch_of.setdefault(f.agent_id, set()).add(launch_id)
        # meta の読めない体は入れ子かどうかも分からず起動元に辿れないので、subagent 数に足さず
        # `meta.unresolved_agent_ids` の側で出す (数に足すと unresolved と二重に数えられる)
        result["count"] += sum(1 for f in run if f.meta_readable)
    roots = resolve_roots(files, linked_runs)
    # 起動に対応したのに起動元へ解決できない体と、起動に属するのに transcript の無い体は数えられない
    unreadable = {launch_id for agent_id, ids in launch_of.items() if roots[agent_id] is None
                  for launch_id in ids}
    unreadable |= {launch_id for f in files
                   if roots[f.agent_id] in launch_of and not f.transcript.is_file()
                   for launch_id in launch_of[roots[f.agent_id]]}
    result["unmatched_tool_use_ids"] += sorted(unreadable - set(result["unmatched_tool_use_ids"]))
    if result["unmatched_tool_use_ids"]:
        result["reason"] = ("起動した tool_use に対応する agent の file か meta が見つからないか、1 つに決まらない"
                            " (workflow は run の dir か体の transcript か meta。agent id・tool_use id の重なりを含む)")
        return result
    records = []
    for f in files:
        if roots[f.agent_id] in launch_of:
            records.extend(_read(f.transcript))
    result.update(sum_tokens(records))
    return result


def unresolved_agent_ids(transcript: Path, linked_runs: set[str]) -> list[str]:
    """起動元に辿れない subagent。どの工程にも帰属できないので transcript の meta に出す。"""
    files = subagent_files_of(transcript) or []
    return sorted({agent_id for agent_id, root in resolve_roots(files, linked_runs).items()
                   if root is None})


def _timestamp(rec: dict) -> dt.datetime | None:
    value = rec.get("timestamp")
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def summarize_stage(stage: dict, records: list[dict], index: dict[str, int],
                    first_launch_runs: dict[str, str], linked_runs: set[str], transcript: Path) -> dict:
    out = {key: stage.get(key) for key in ("stage", "from_uuid", "to_uuid")}
    start, end = index.get(stage.get("from_uuid")), index.get(stage.get("to_uuid"))
    missing = [key for key, pos in (("from_uuid", start), ("to_uuid", end)) if pos is None]
    if missing:
        out["error"] = f"transcript に無い uuid: {', '.join(missing)}"
        return out
    if start > end:
        out["error"] = "from_uuid が to_uuid より後ろにある"
        return out
    span = records[start:end + 1]
    first, last = _timestamp(span[0]), _timestamp(span[-1])
    out["duration_seconds"] = (last - first).total_seconds() if first and last else None
    out.update(sum_tokens(records, start, end))
    launch_ids = list(dict.fromkeys(i for rec in span for i in subagent_launch_ids_of(rec)))
    workflow_runs = {i: first_launch_runs[i] for rec in span for i in workflow_launch_ids_of(rec)
                     if i in first_launch_runs}
    out["subagents"] = sum_subagents(launch_ids, workflow_runs, transcript, linked_runs)
    return out


def _validate(transcript: str, stages: list, transcripts_dir: Path) -> Path:
    path = Path(transcript).expanduser().resolve()
    if path.suffix != ".jsonl" or not path.is_relative_to(transcripts_dir.expanduser().resolve()):
        raise StageUsageRejected(
            f"transcript は transcript lake ({transcripts_dir}) の下の .jsonl を渡す: {transcript}")
    if not path.is_file():
        raise StageUsageRejected(f"transcript が無い: {transcript}")
    if not all(isinstance(s, dict) and {"stage", "from_uuid", "to_uuid"} <= set(s) for s in stages):
        raise StageUsageRejected("stages は {stage, from_uuid, to_uuid} の object の配列で渡す")
    return path


def build_slice(path: Path, stages: list[dict], now: dt.datetime) -> dict:
    records = _read(path)
    index: dict[str, int] = {}
    for position, rec in enumerate(records):
        uuid = rec.get("uuid")
        if isinstance(uuid, str) and uuid not in index:
            index[uuid] = position
    # run の id は結果の record にあり、工程の境界の外に出うるので file 全体から引く。結果が run の
    # id を持たない Workflow (拒否・失敗) は run を起こしていないので入らない
    run_ids = dict(run for rec in records if (run := workflow_run_of(rec)))
    linked_runs = set(run_ids.values())
    # 同じ run を指す起動が重なったら最初の起動だけに帰属させる
    first_launch_runs: dict[str, str] = {}
    for rec in records:
        for launch_id in workflow_launch_ids_of(rec):
            if launch_id in run_ids and run_ids[launch_id] not in first_launch_runs.values():
                first_launch_runs[launch_id] = run_ids[launch_id]
    summaries = [summarize_stage(stage, records, index, first_launch_runs, linked_runs, path)
                 for stage in stages]
    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
    return {
        "meta": {
            "generated_at": now.isoformat(),
            "transcript": str(path),
            "mtime": mtime.isoformat(),
            "stages": len(summaries),
            "stage_errors": sum(1 for s in summaries if "error" in s),
            # 起動元に辿れない subagent の消費は、どの工程の subagent の token にも入らない
            "unresolved_agent_ids": unresolved_agent_ids(path, linked_runs),
            # 置き場に当たらない jsonl。列挙から外れた体がいれば subagent の token は部分和になる
            "unrecognized_nested_files": unrecognized_subagent_files_of(path),
        },
        "stages": summaries,
    }


def run(
    transcript: str,
    stages: list,
    transcripts_dir: str = str(DEFAULT_TRANSCRIPTS_DIR),
    output_dir: str = str(DEFAULT_OUTPUT_DIR),
    now: str | None = None,
) -> dict:
    """tool 側の入口。slice は返さず、書いた path と meta だけを返す。"""
    path = _validate(transcript, stages, Path(transcripts_dir))
    generated = resolve_now(now)
    slice_json = build_slice(path, stages, generated)
    out_dir = prepare_output_dir(output_dir)
    out = out_dir / f"stage-usage-{generated.strftime('%Y%m%dT%H%M%SZ')}-{path.stem}.json"
    out.write_text(json.dumps(slice_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(out), "meta": slice_json["meta"]}
