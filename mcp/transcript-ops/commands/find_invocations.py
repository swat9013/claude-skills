"""`find_invocations` tool の実装 (skill-usage-audit 向け実呼出 transcript の特定)。

指定 skill が**実際に呼び出された** transcript を transcript lake から特定し、
呼出ごとの anchor (timestamp / uuid / channel / args) 付きの slice JSON を出す。

本 tool の責務は決定的な特定まで — **監査基準との突合・逸脱の分類・改善の要否は
一切判定しない** (skill-usage-audit の SKILL.md 手順 3 以降が担う)。

**grep ではなく record 構造で判定する**のが本 tool の要点。SKILL.md が抱えていた
罠表 (invocation マーカー完全一致 / queue-operation 二重記録 / wrapper transcript
除外) は、いずれも「行を文字列として grep する」ことに起因する。JSONL を record と
して読み、呼出しが載る 2 channel だけを見れば構造的に消える:

- `skill_tool`: assistant record の `Skill` tool_use (`input.skill` が対象名)
- `command`: user record の slash 展開 (判定は
  `adapter.transcript.parse_slash_invocation` に一本化。行頭一致 + wrapper 除外)

そのため assistant の text や Bash 出力に literal 引用されたマーカーは、そもそも
判定対象に入らない。一方で「引用が何件あったか」は監査者が知りたい情報なので、
除外した hit は `meta.excluded` に理由別で残す (silent に落とさない)。

出力: output_dir に invocations-<timestamp>.json を書き、**path だけを返す**
(slice 本体は context に載せない)。

`parse_args` / `main(argv)` を残してあるのは、slice schema を固定するテストが CLI
形の entrypoint を通して観測契約を検査しているため。tool 側の入口は `run()`。
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

from adapter.transcript import (
    WRAPPER_TAG,
    _iter_jsonl,
    is_slash_expansion_record,
    parse_slash_invocation,
    resolve_now,
    resolve_repo_at,
    session_id_of,
    truncate,
)
from artifacts import prepare_output_dir

# --- 定数 --------------------------------------------------------------------

DEFAULT_TRANSCRIPTS_DIR = Path("~/.claude/projects").expanduser()
DEFAULT_OUTPUT_DIR = Path("/tmp/skill-usage-audit")

# 監査する transcript 数の既定 (SKILL.md の `[n]` 既定と揃える)。
DEFAULT_LIMIT = 3

# 呼出しの args / user_prompt を slice に載せるときの上限 (字)。
ARGS_TEXT_LIMIT = 300

# 呼出しが載る channel。skill load の 3 channel (scan_invocations) とは別語彙で、
# **SKILL.md への Read は含めない** — 監査対象は「呼び出された実行」であって
# 「本文が読まれたこと」ではない。
CHANNEL_SKILL_TOOL = "skill_tool"
CHANNEL_COMMAND = "command"
INVOCATION_CHANNELS = (CHANNEL_SKILL_TOOL, CHANNEL_COMMAND)

# 除外理由。**この順で評価する** (hit ごとに 1 理由へ確定させるため順序が仕様)。
# いずれも「マーカーは在るが実呼出しではない」もので、grep 方式が呼出回数を
# 過大に見せていた原因そのもの。
EXCLUSION_REASONS = (
    "queue_operation",    # queue-operation record への二重記録 (同一呼出しの再掲)
    "local_command_echo",  # system.local_command (built-in slash の echo)
    "wrapper_embedded",   # 別 session を <transcript-data> として内包した wrapper
    "quoted_marker",      # 上記以外の literal 引用 (SKILL.md 本文 / regex 例)
)

# WRAPPER_TAG と実呼出しの判定 (parse_slash_invocation / is_slash_expansion_record) は
# adapter.transcript にある。除外**理由の分類**だけが本 tool の観測契約なのでここに残す。


# --- データ ------------------------------------------------------------------

@dataclasses.dataclass
class Invocation:
    timestamp: str
    uuid: str
    channel: str
    args: str
    is_sidechain: bool


@dataclasses.dataclass
class TranscriptHit:
    path: str
    session_id: str
    cwd: str
    repo: str | None
    scope_key: str
    mtime: str
    invocations: list[Invocation]


# --- CLI ---------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--skill", type=str, required=True,
                   help="対象 skill 名。plugin prefix (`swat-skills:`) は付けても外しても可")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"slice に載せる transcript 数の上限。default {DEFAULT_LIMIT}")
    p.add_argument("--transcripts-dir", type=Path, default=DEFAULT_TRANSCRIPTS_DIR,
                   help="transcript の data lake。default ~/.claude/projects")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help="slice 出力先。default /tmp/skill-usage-audit")
    p.add_argument("--now", type=str, default=None,
                   help="ISO timestamp。出力ファイル名の stamp を固定 (テスト用)")
    p.add_argument("--stdout-slice", action="store_true",
                   help="ファイルに書かず slice JSON を stdout に出す (テスト用)")
    return p.parse_args(argv)


# --- 名前の解決 --------------------------------------------------------------

def bare_skill_name(skill: str) -> str:
    """plugin prefix を落とした skill 名。`swat-skills:adr` → `adr`。"""
    return skill.split(":")[-1].strip()


def marker_regex(skill: str) -> re.Pattern:
    """literal 引用の件数を数えるための完全一致マーカー。

    **判定には使わない** (判定は record 構造)。`meta.excluded` の内訳を出すために、
    「マーカーが現れた行」を数える用途にだけ使う。閉じタグ / 閉じ `"` まで含めるのは
    類似名 sibling (`foo` と `foo-bar`) の混入を防ぐため。
    """
    name = re.escape(bare_skill_name(skill))
    return re.compile(
        rf"<command-name>/(?:[A-Za-z0-9_-]+:)?{name}</command-name>"
        rf'|"skill"\s*:\s*"(?:[A-Za-z0-9_-]+:)?{name}"'
    )


def matches_skill(value: Any, bare: str) -> bool:
    """`Skill` tool_use の `input.skill` / slash 名が対象 skill か。

    plugin prefix の有無は問わないが、**prefix を外した名前は完全一致**を要求する
    (前方一致にすると `foo` が `foo-bar` を拾う)。
    """
    if not isinstance(value, str):
        return False
    return bare_skill_name(value) == bare


# --- 抽出 --------------------------------------------------------------------

def _classify_excluded(rec: dict, marker: re.Pattern) -> str | None:
    """実呼出しでないマーカー hit の理由を返す。hit が無ければ None。

    text 系 field だけを見る (record 全体を dumps すると input schema の説明文まで
    走査対象になり、走査コストが hit 数に見合わない)。
    """
    rtype = rec.get("type")
    if rtype == "queue-operation":
        content = rec.get("content")
        if isinstance(content, str) and marker.search(content):
            return "queue_operation"
        return None
    if rtype == "system" and rec.get("subtype") == "local_command":
        content = rec.get("content")
        if isinstance(content, str) and marker.search(content):
            return "local_command_echo"
        return None
    if rtype == "assistant":
        # assistant の text block へのマーカー引用 (SKILL.md 本文の提示 / Bash 出力の
        # 再掲)。**同 record が tool_use を持つ可能性があるので抽出は止めない**。
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            return None
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                if marker.search(str(block.get("text") or "")):
                    return "quoted_marker"
        return None
    if rtype != "user":
        return None
    content = (rec.get("message") or {}).get("content")
    text = content if isinstance(content, str) else ""
    if not text or not marker.search(text):
        return None
    if WRAPPER_TAG in text:
        return "wrapper_embedded"
    if is_slash_expansion_record(text):
        return None  # 実呼出し (extract 側が計上する)
    return "quoted_marker"


def extract_invocations(
    jsonl_path: Path,
    skill: str,
) -> tuple[list[Invocation], collections.Counter, dict[str, str]]:
    """1 transcript file から対象 skill の実呼出しと除外内訳を取り出す。

    返り値の 3 つ目は file 単位の anchor (session_id / cwd) で、最後に観測した
    record の値を採る (同一 file 内で session_id は一定、cwd は relocate で動く)。
    """
    bare = bare_skill_name(skill)
    marker = marker_regex(skill)
    invocations: list[Invocation] = []
    excluded: collections.Counter = collections.Counter()
    anchor = {"session_id": "", "cwd": ""}

    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as fp:
            for rec in _iter_jsonl(fp):
                rtype = rec.get("type")
                reason = _classify_excluded(rec, marker)
                if reason is not None:
                    excluded[reason] += 1
                    # assistant は引用と tool_use が同 record に同居しうるので
                    # 抽出を続ける。それ以外は実呼出しを載せない record 型。
                    if rtype != "assistant":
                        continue
                if rtype not in ("user", "assistant"):
                    continue
                sid = session_id_of(rec)
                if sid:
                    anchor["session_id"] = sid
                cwd = str(rec.get("cwd") or "")
                if cwd:
                    anchor["cwd"] = cwd
                timestamp = str(rec.get("timestamp") or "")
                uuid = str(rec.get("uuid") or "")
                is_sidechain = rec.get("isSidechain") is True
                content = (rec.get("message") or {}).get("content")

                if rtype == "user":
                    text = content if isinstance(content, str) else ""
                    slash = parse_slash_invocation(content)
                    if slash is None or not matches_skill(slash, bare):
                        continue
                    invocations.append(Invocation(
                        timestamp=timestamp, uuid=uuid, channel=CHANNEL_COMMAND,
                        args=truncate(_command_args(text), ARGS_TEXT_LIMIT),
                        is_sidechain=is_sidechain,
                    ))
                    continue

                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") != "Skill":
                        continue
                    blk_input = block.get("input")
                    if not isinstance(blk_input, dict):
                        continue
                    if not matches_skill(blk_input.get("skill"), bare):
                        continue
                    invocations.append(Invocation(
                        timestamp=timestamp, uuid=uuid, channel=CHANNEL_SKILL_TOOL,
                        args=truncate(str(blk_input.get("args") or ""), ARGS_TEXT_LIMIT),
                        is_sidechain=is_sidechain,
                    ))
    except OSError:
        return [], collections.Counter(), anchor
    return invocations, excluded, anchor


COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)


def _command_args(text: str) -> str:
    m = COMMAND_ARGS_RE.search(text)
    return m.group(1).strip() if m else ""


# --- 選定 --------------------------------------------------------------------

def resolve_scope(cwd: str) -> tuple[str | None, str]:
    """呼出元の (repo, 環境差の分散キー) を返す。

    repo は cwd が**実在するときだけ** git から解決する。消えた worktree の祖先
    遡りは `scan_prompts` の観測契約なので持ち込まない ([ADR 0013](https://github.com/swat9013/swat-skills/blob/main/docs/adr/0013-intra-subsystem-implementation-sharing.md))
    — 代わりに分散キーは `repo or cwd` に倒し、repo 未解決でも別環境の呼出しが
    1 件も選ばれない事態を避ける。
    """
    if not cwd:
        return None, ""
    path = Path(cwd)
    repo = resolve_repo_at(path) if path.is_dir() else None
    return repo, repo or cwd


def select_transcripts(hits: list[TranscriptHit], limit: int) -> list[TranscriptHit]:
    """環境差を確保した読み順で `limit` 件を選ぶ。

    呼出元が複数 scope にまたがるなら**各 scope の最新 1 件を先に取り**、残り slot を
    全体の新しい順で埋める (SKILL.md 手順 2 の選定規約)。1 環境の連続呼出しで枠が
    埋まると、環境差に起因する逸脱が観測されないため。

    採用済みかは **index** で覚える。`hit in selected` は dataclass の等値比較なので
    invocations list まで毎回舐めるうえ、値の等しい別 file を同一視してしまう。
    """
    ordered = sorted(hits, key=lambda h: (h.mtime, h.path), reverse=True)
    selected: list[TranscriptHit] = []
    taken: set[int] = set()
    seen_scopes: set[str] = set()
    for index, hit in enumerate(ordered):
        if len(selected) >= limit:
            break
        if hit.scope_key in seen_scopes:
            continue
        seen_scopes.add(hit.scope_key)
        taken.add(index)
        selected.append(hit)
    for index, hit in enumerate(ordered):
        if len(selected) >= limit:
            break
        if index in taken:
            continue
        selected.append(hit)
    return selected


# --- slice 生成 --------------------------------------------------------------

def _channels(invocations: list[Invocation]) -> dict[str, int]:
    counts = {channel: 0 for channel in INVOCATION_CHANNELS}
    for inv in invocations:
        if inv.channel in counts:
            counts[inv.channel] += 1
    return counts


def build_slice(
    args: argparse.Namespace,
    hits: list[TranscriptHit],
    selected: list[TranscriptHit],
    excluded: collections.Counter,
    scanned_files: int,
    generated_at: str,
) -> dict:
    return {
        "meta": {
            "generated_at": generated_at,
            "skill": args.skill,
            "skill_name": bare_skill_name(args.skill),
            "transcripts_dir": str(args.transcripts_dir),
            "limit": args.limit,
            "scanned_files": scanned_files,
            "matched_files": len(hits),
            "emitted_files": len(selected),
            "total_invocations": sum(len(h.invocations) for h in hits),
            "distinct_scopes": len({h.scope_key for h in hits}),
            "excluded": {reason: excluded.get(reason, 0)
                         for reason in EXCLUSION_REASONS},
            "read_order": (
                "scope (repo or cwd) ごとの最新 1 件を先取り → 残りを mtime 降順"
            ),
            # 読み方の注記は slice 側に持つ (tool docstring は全利用セッションの
            # context に常駐するが、注記が要るのは slice を読む段階だけ。ADR 0031)
            "notes": [
                "判定は record 構造 (assistant の Skill tool_use / user の slash 展開) で"
                "行い行 grep はしない。計上されなかった件数は excluded に理由別で残る",
                "total_invocations が呼出回数、matched_files は file 数 (混同しない)",
                "観測窓を持たない — 監査対象は「最後に呼ばれた n 件」であって期間ではない。"
                "matched_files が 0 なら実呼出なしで監査は成立しない",
            ],
        },
        "transcripts": [
            {
                "path": hit.path,
                "session_id": hit.session_id,
                "cwd": hit.cwd,
                "repo": hit.repo,
                "mtime": hit.mtime,
                "invocation_count": len(hit.invocations),
                "channels": _channels(hit.invocations),
                "invocations": [dataclasses.asdict(inv) for inv in hit.invocations],
            }
            for hit in selected
        ],
    }


# --- main --------------------------------------------------------------------

def collect(args: argparse.Namespace) -> dict:
    """ファイル出力を伴わない slice 構築まで (テスト用 entrypoint)。

    観測窓を持たない (mtime cutoff をかけない) のは、監査対象が「最後に呼ばれた
    n 件」であって期間ではないため。呼出しが 30 日以上前でも監査は成立する。
    """
    generated = resolve_now(args.now)
    hits: list[TranscriptHit] = []
    excluded: collections.Counter = collections.Counter()
    scanned = 0

    root = args.transcripts_dir
    project_dirs = sorted(root.iterdir()) if root.is_dir() else []
    for project_dir in project_dirs:
        if not project_dir.is_dir():
            continue
        for jsonl in sorted(project_dir.glob("*.jsonl")):
            scanned += 1
            invocations, file_excluded, anchor = extract_invocations(jsonl, args.skill)
            excluded.update(file_excluded)
            if not invocations:
                continue
            try:
                mtime = dt.datetime.fromtimestamp(
                    jsonl.stat().st_mtime, dt.timezone.utc
                ).isoformat().replace("+00:00", "Z")
            except OSError:
                mtime = ""
            repo, scope_key = resolve_scope(anchor["cwd"])
            hits.append(TranscriptHit(
                path=str(jsonl),
                session_id=anchor["session_id"],
                cwd=anchor["cwd"],
                repo=repo,
                scope_key=scope_key or str(jsonl),
                mtime=mtime,
                invocations=sorted(invocations, key=lambda i: (i.timestamp, i.uuid)),
            ))

    selected = select_transcripts(hits, args.limit)
    return build_slice(
        args, hits, selected, excluded, scanned,
        generated.isoformat().replace("+00:00", "Z"),
    )


def emit(slice_json: dict, args: argparse.Namespace) -> str:
    """invocations-<timestamp>.json を書いて path を返す。"""
    output_dir = prepare_output_dir(args.output_dir)
    generated = dt.datetime.fromisoformat(slice_json["meta"]["generated_at"])
    out = output_dir / f"invocations-{generated.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(
        json.dumps(slice_json, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(out)


def run(
    skill: str,
    limit: int = DEFAULT_LIMIT,
    transcripts_dir: str = str(DEFAULT_TRANSCRIPTS_DIR),
    output_dir: str = str(DEFAULT_OUTPUT_DIR),
    now: str | None = None,
) -> dict:
    """tool 側の入口。slice は返さず、書いた path と特定結果の meta だけを返す。

    **呼出し値は argv を経由させない** — `-` で始まる skill 名 / path が argparse の
    option 解釈に晒される。CLI 形の entrypoint は schema 固定テストのために残すが、
    ここから受け取るのは default 値だけにする。
    """
    args = parse_args(["--skill", ""])
    args.skill = skill
    args.limit = limit
    args.transcripts_dir = Path(transcripts_dir)
    args.output_dir = Path(output_dir)
    args.now = now

    slice_json = collect(args)
    return {"path": emit(slice_json, args), "meta": slice_json["meta"]}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    slice_json = collect(args)
    if args.stdout_slice:
        json.dump(slice_json, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    print(emit(slice_json, args))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
