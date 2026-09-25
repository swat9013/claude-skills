"""`slice_sessions` tool の実装 (session id 名指しの transcript slice, ADR 0074)。

dispatcher-debrief が dispatcher の `log.jsonl` に載った orchestrator / worker の
session id から transcript を読むための入口。既存 tool は skill 名
(`find_invocations`) か統計 (`scan_*`) で引くだけで、id を名指しする経路が無かった。

slice に載せるのは契約逸脱の述語を評価できる最小限に絞る:

- tool_use の時系列 (tool 名 + 主要引数 1 つ + outcome)
- permission / hook に止められた tool_use (deny の種別と文言の抜粋)
- assistant の最終 text (text を持つ最後の message の text block を全文で連結)
- session ごとの meta と、**store に 1 行も無かった session id の欠落一覧**

**未観測と観測して空を返し分ける**: 見つからない id を error にしないのは、log に
載った id の一部が lake に無い (subagent の入れ子 transcript / 別 lake / 書き出し前)
のは運用上ふつうに起きるからで、1 件の欠落で全体の観測を捨てさせない。代わりに
`missing_session_ids` へ必ず出し、`sessions` に空の timeline で載る「観測して何も
しなかった session」と混ぜない。

出力: output_dir に sessions-<timestamp>-<id digest>.json を書き、**path と meta だけを返す**。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from adapter.transcript import resolve_now, truncate
from artifacts import prepare_output_dir
from marts import load_statements
from store import ingest
from store import store as store_mod

from . import udf

QUERY_PATH = Path(__file__).resolve().parent / "query.sql"

DEFAULT_TRANSCRIPTS_DIR = Path("~/.claude/projects").expanduser()
DEFAULT_OUTPUT_DIR = Path("/tmp/dispatcher-debrief")

# 主要引数 (`target`) の上限 (字)。Bash は command の最初の空でない行だけを載せる — 述語が
# 見るのは「何を撃ったか」の頭で、heredoc の本文までは要らない。
TARGET_LIMIT = 200

# deny された tool_use に添える結果文言の上限 (字)。deny の理由は文言の先頭に出る。
RESULT_EXCERPT_LIMIT = 500

DENY_PREFIX = "deny_"

# 最終 message の text block を連結する区切り (block 間は段落として読む)。
FINAL_TEXT_SEPARATOR = "\n\n"

# `target_path` より input 全体の抜粋を採る tool。検索系の `path` は探した場所で、
# 何を探したか (pattern) が落ちると主要引数にならない。
PATTERN_TOOLS = ("Grep", "Glob")

NOTES = [
    "missing_session_ids は store (project 直下の transcript) に 1 行も無かった id。"
    "subagent の入れ子 transcript は ingest 対象外なので、そこにしか無い id もここに出る",
    "sessions に載った session の tool_uses / denials が空なら、観測した上で"
    "該当が 0 件 (未観測ではない)",
    "outcome は permissions mart の refined 語彙 + deny_hook。deny_ で始まるものが"
    "denials に並ぶ",
    "PreToolUse hook の deny は transcript 上 permission-rule 種別で記録される実測が"
    "ある (deny_hook に来ない)。hook か permission entry かは result_excerpt の文言"
    "(`PreToolUse:` 前置の有無) で見分ける",
    "target は tool の主要引数 1 つ: Bash は command の最初の空でない行 / Grep・Glob は "
    "input の抜粋 (pattern を含む) / file 系は path / URL 系は URL / Skill・Agent は "
    "skill 名・subagent_type / それ以外は input の抜粋",
    "session 内の順序は file の開始 ts 順 → file 内の行順。seq は 1 始まり",
]


class SessionIdsRejected(ValueError):
    """session id の列が空、または空白だけの id を含む。

    空白の id を通すと session id を持たない record (`session_id = ''`) 全部に一致して
    しまうため、黙って落とさず失敗させる。
    """


def requested_ids(session_ids: list[str]) -> list[str]:
    """前後の空白を落とし、重複を除いた依頼順の id 列 (境界の検証)。"""
    stripped = [str(sid).strip() for sid in session_ids]
    if not stripped:
        raise SessionIdsRejected("session_ids が空。slice する session id を 1 つ以上渡す")
    if any(not sid for sid in stripped):
        raise SessionIdsRejected("空の session id が含まれている")
    return list(dict.fromkeys(stripped))


def target_of(row: Any) -> str:
    """tool_use の主要引数 1 つ。store の列は tool ごとに排他なので先に埋まった列を採る。"""
    if row["command"]:
        first_line = next(
            (line.strip() for line in row["command"].splitlines() if line.strip()), "")
        return truncate(first_line, TARGET_LIMIT)
    if row["tool"] in PATTERN_TOOLS:
        return truncate(row["input_excerpt"], TARGET_LIMIT)
    for column in ("target_path", "target_url", "unit_id", "input_excerpt"):
        if row[column]:
            return truncate(row[column], TARGET_LIMIT)
    return ""


def query_requested_sessions(conn: Any, requested: list[str]) -> tuple[list, list, list]:
    """依頼 session の (file, tool_use, 最終 text block) の行を store から引く。

    後続 3 文は接続ローカルの temp 表 `requested_file` を参照するので、作成と参照を
    本関数 1 箇所に閉じ込める (呼び出し側に実行順を知らせない)。順序を崩すと
    SQLite は `no such table` で落ちる — 黙って空を返す経路は無い。
    """
    statements = load_statements(QUERY_PATH)
    conn.execute(statements["requested_file"], {"session_ids": json.dumps(requested)})
    return (
        conn.execute(statements["session_files"]).fetchall(),
        conn.execute(statements["tool_uses"]).fetchall(),
        conn.execute(statements["final_text_blocks"]).fetchall(),
    )


def build(session_ids: list[str], transcripts_dir: Path,
          now: str | None) -> dict:
    """ファイル出力を伴わない slice 構築まで (I/O は store の差分 sync のみ)。"""
    requested = requested_ids(session_ids)
    generated = resolve_now(now)

    conn, sync_report = ingest.open_synced(transcripts_dir, now=generated)
    try:
        udf.register(conn)
        files, tool_rows, final_rows = query_requested_sessions(conn, requested)
        anomalies = store_mod.anomalies(conn)
    finally:
        conn.close()

    paths: dict[str, list[str]] = {}
    for row in files:
        paths.setdefault(row["session_id"], []).append(row["path"])

    tool_uses: dict[str, list[dict]] = {sid: [] for sid in paths}
    for row in tool_rows:
        timeline = tool_uses[row["session_id"]]
        timeline.append({
            "seq": len(timeline) + 1,
            "ts": row["ts"],
            "tool": row["tool"],
            "target": target_of(row),
            "outcome": row["outcome"],
            "result_excerpt": truncate(row["result_text"], RESULT_EXCERPT_LIMIT),
        })

    final_blocks: dict[str, list[Any]] = {}
    for row in final_rows:
        final_blocks.setdefault(row["session_id"], []).append(row)
    final_texts = {
        sid: {"ts": rows[0]["ts"],
              "text": FINAL_TEXT_SEPARATOR.join(row["text"] for row in rows)}
        for sid, rows in final_blocks.items()
    }

    sessions = []
    for sid in requested:
        if sid not in paths:
            continue
        timeline = tool_uses[sid]
        sessions.append({
            "session_id": sid,
            "tool_uses": [{key: value for key, value in event.items()
                           if key != "result_excerpt"} for event in timeline],
            "denials": [event for event in timeline
                        if event["outcome"].startswith(DENY_PREFIX)],
            "final_text": final_texts.get(sid),
        })

    return {
        "meta": {
            "generated_at": generated.isoformat().replace("+00:00", "Z"),
            "transcripts_dir": str(transcripts_dir),
            "requested_session_ids": requested,
            "missing_session_ids": [sid for sid in requested if sid not in paths],
            "sessions": [
                {
                    "session_id": session["session_id"],
                    "transcript_paths": paths[session["session_id"]],
                    "tool_use_count": len(session["tool_uses"]),
                    "denial_count": len(session["denials"]),
                    "has_final_text": session["final_text"] is not None,
                }
                for session in sessions
            ],
            "store": {
                **anomalies,
                "skipped_nested_files": sync_report.skipped_nested_files,
                "synced_files": sync_report.ingested_files,
            },
            "notes": NOTES,
        },
        "sessions": sessions,
    }


def emit(slice_json: dict, output_dir: Path) -> str:
    """sessions-<timestamp>-<id digest>.json を 0700 の dir へ書いて path を返す。

    file 名に依頼 id の digest を混ぜるのは、session ごとに続けて呼ぶ用途で同じ秒の
    別依頼が互いの slice を上書きしないため (stamp だけでは 1 秒単位で衝突する)。
    """
    resolved = prepare_output_dir(output_dir)
    meta = slice_json["meta"]
    generated = dt.datetime.fromisoformat(meta["generated_at"])
    digest = hashlib.sha256(
        "\n".join(meta["requested_session_ids"]).encode("utf-8")).hexdigest()[:8]
    out = resolved / f"sessions-{generated.strftime('%Y%m%dT%H%M%SZ')}-{digest}.json"
    out.write_text(json.dumps(slice_json, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    return str(out)


def run(
    session_ids: list[str],
    transcripts_dir: str = str(DEFAULT_TRANSCRIPTS_DIR),
    output_dir: str = str(DEFAULT_OUTPUT_DIR),
    now: str | None = None,
) -> dict[str, Any]:
    """tool 側の入口。slice は返さず、書いた path と件数 meta だけを返す。

    注記・transcript path・store の劣化シグナルは slice 側にだけ置く (返り値は
    呼び出し元の context に載るので、読む段階でしか要らないものを載せない)。
    """
    slice_json = build(session_ids, Path(transcripts_dir), now)
    meta = slice_json["meta"]
    return {
        "path": emit(slice_json, Path(output_dir)),
        "meta": {
            "requested_session_ids": meta["requested_session_ids"],
            "missing_session_ids": meta["missing_session_ids"],
            "sessions": [
                {key: value for key, value in session.items()
                 if key != "transcript_paths"}
                for session in meta["sessions"]
            ],
        },
    }
