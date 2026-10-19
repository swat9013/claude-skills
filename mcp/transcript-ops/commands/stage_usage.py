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
- 起動元に辿れない subagent (meta が無い・読めない・親が無い・親の連鎖が循環する) は
  どの工程にも帰属できないので、工程の値に入れず `meta.unresolved_agent_ids` に出す

対応が取れない値は 0 にせず `null` と理由で返す (0 だと消費が無かった工程と区別できない)。

invariant 一覧 (テストが項目ごとに拒否か値の検査を 1 本持つ):
1. token は message.id ごとの最後の record だけを数え、cache_read は別掲する
2. subagent は起動元の工程に帰属させ、入れ子は親に畳む
3. 対応が取れない subagent は 0 にせず `null` と理由で返す
4. 起動元に辿れない subagent は `meta.unresolved_agent_ids` に出す
5. 境界の内側の record だけを数え、応答の最終 usage はその応答の最初の record が属する工程に帰属させる
6. 不正な stage 入力 (未知 uuid・境界の逆転) は当該 stage の `error` に出し、他の stage を止めない
7. transcript lake の外の path・.jsonl 以外は `StageUsageRejected` で拒否する

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
    usage_of,
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


def resolve_roots(files: list) -> dict[str, str | None]:
    """agent id → 起動元の top-level の agent id。辿れない体は None。

    meta が無い・読めない体、親の meta が無い体、親の連鎖が循環する体、辿り着いた先が
    起動の tool_use id を持たない体は、どの起動に属するかが決まらない。
    """
    by_id = {f.agent_id: f for f in files}
    roots: dict[str, str | None] = {}
    for f in files:
        current, seen = f, set()
        while current is not None and current.meta_readable and current.parent_agent_id:
            if current.agent_id in seen:
                current = None
                break
            seen.add(current.agent_id)
            current = by_id.get(current.parent_agent_id)
        reached_launch = current is not None and current.meta_readable and current.tool_use_id
        roots[f.agent_id] = current.agent_id if reached_launch else None
    return roots


def sum_subagents(launch_ids: list[str], transcript: Path) -> dict:
    result: dict = {"count": len(launch_ids), "tokens": None,
                    "cache_read_input_tokens": None, "unmatched_tool_use_ids": []}
    if not launch_ids:
        result.update(tokens=0, cache_read_input_tokens=0)
        return result
    files = subagent_files_of(transcript)
    if files is None:
        result["unmatched_tool_use_ids"] = list(launch_ids)
        result["reason"] = "subagents dir が無い"
        return result
    top = {f.tool_use_id: f for f in files
           if f.meta_readable and f.tool_use_id and not f.parent_agent_id}
    launched = set()
    for launch_id in launch_ids:
        found = top.get(launch_id)
        if found and found.transcript.is_file():
            launched.add(found.agent_id)
        else:
            result["unmatched_tool_use_ids"].append(launch_id)
    if result["unmatched_tool_use_ids"]:
        result["reason"] = "起動した tool_use に対応する agent の file か meta が見つからない"
        return result
    roots = resolve_roots(files)
    records = []
    for f in files:
        if roots[f.agent_id] in launched and f.transcript.is_file():
            records.extend(_read(f.transcript))
    result.update(sum_tokens(records))
    return result


def unresolved_agent_ids(transcript: Path) -> list[str]:
    """起動元に辿れない subagent。どの工程にも帰属できないので transcript の meta に出す。"""
    files = subagent_files_of(transcript) or []
    return sorted(agent_id for agent_id, root in resolve_roots(files).items() if root is None)


def _timestamp(rec: dict) -> dt.datetime | None:
    value = rec.get("timestamp")
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def summarize_stage(stage: dict, records: list[dict], index: dict[str, int], transcript: Path) -> dict:
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
    out["subagents"] = sum_subagents(launch_ids, transcript)
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
    summaries = [summarize_stage(stage, records, index, path) for stage in stages]
    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
    return {
        "meta": {
            "generated_at": now.isoformat(),
            "transcript": str(path),
            "mtime": mtime.isoformat(),
            "stages": len(summaries),
            "stage_errors": sum(1 for s in summaries if "error" in s),
            # 起動元に辿れない subagent の消費は、どの工程の subagent の token にも入らない
            "unresolved_agent_ids": unresolved_agent_ids(path),
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
