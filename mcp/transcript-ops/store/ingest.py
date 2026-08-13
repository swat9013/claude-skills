"""lake (`~/.claude/projects/**/*.jsonl`) → store の ingest (ADR 0031)。

本 module と `adapter/transcript.py` だけが on-disk 形式を知る。query 層 (mart) は
store の列しか見ない (`scripts/gate/verify-query-format-isolation.py` が機械検査)。

## 不変条件

- **I1 (冪等)**: 同じ file を何度 ingest しても store の内容は変わらない。主キーは
  `(file_id, line_no)` で、file の再取得は「全 row 削除 → 全 row 挿入」の全置換
- **I2 (差分 sync = full rebuild)**: 差分 sync 後の store と、捨てて作り直した
  store は同じ内容になる。incremental のバグはすべてここに落ちる
- **I3 (format isolation)**: gate script が query 層 SQL を検査する
- **I4 (未知 type の保守的取り込み)**: 知らない `type` の record も spine に 1 行
  入れる。読み方を知らないことと、存在しなかったことを混同しない

## race に対する順序保証

fingerprint `(mtime_ns, size_bytes)` は **read の前に stat し、読込が完了してから
記録する**。読込中に file が伸びると記録した fingerprint は次回の stat と一致せず、
必ず再取得される。検査ではなく順序で保証しているので、判定漏れが起きない。

## プロセス間の直列化

sync は `lock.sync_lock` の中でだけ走る。SQLite の busy_timeout は full ingest の
所要時間 (実測 22.5 秒) を賄えず、cold start で複数プロセスが並ぶと後続が
`database is locked` で落ちるため。lock は store file 単位なので、別 lake の観測は
互いを待たない。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from adapter.transcript import (
    attachment_of,
    attachment_text_of,
    classify_base_outcome,
    compact_boundary_of,
    count_chars,
    extract_user_text,
    flatten_result_text,
    hook_firing_of,
    memory_injection_of,
    parse_slash_invocation,
    PRESENTED_NAME_FIELDS,
    session_id_of,
    STATIC_PAYLOAD_FIELDS,
    tool_use_result_of,
    truncate,
    usage_of,
)

from . import lock
from . import store as store_mod

# tool 入力の抜粋上限。**原型復元用ではない** — 復元が要るなら lake を読む。
# 生 input を丸ごと持つと Write / Edit の file 全文が store に入る。
INPUT_EXCERPT_LIMIT = 200

# tool 実行結果の文言の保存上限。mart 側の細分 (permission-rule / automode の
# 文言 fallback、automode の Reason label) はいずれも文言の先頭側に出る。
RESULT_TEXT_LIMIT = 4000

# matcher が file path 系 tool の照合に使う input key (先に見つかった 1 つを採る)。
TARGET_PATH_KEYS = ("file_path", "path", "notebook_path")

# tool_use.unit_id の抽出元 input key。Skill / Agent の識別引数は input JSON の
# 中にしか無く、command / target_path の枠に収まらないため専用 key 表を持つ
# (v2, #498)。
UNIT_ID_KEYS: dict[str, str] = {"Skill": "skill", "Agent": "subagent_type"}


@dataclasses.dataclass
class SyncReport:
    """差分 sync 1 回の結果。件数だけを持ち、判断はしない。"""

    ingested_files: int = 0
    unchanged_files: int = 0
    removed_files: int = 0
    unreadable_files: int = 0
    # `<project>/<session>/subagents/*.jsonl` 等、1 階層目より深い位置にある
    # transcript。現行の観測範囲 (project 直下) を広げないため ingest しないが、
    # **黙って落とさず件数を出す** (silent skip の可視化)
    skipped_nested_files: int = 0
    records: int = 0
    broken_lines: int = 0


def iter_lake_files(transcripts_dir: Path) -> Iterator[tuple[str, Path]]:
    """観測対象の transcript を (project_dir 名, path) で yield する。

    lake の dir 名エンコードは lossy なので directory 名から cwd を復元しない。
    scope 絞りは record の cwd で行う。
    """
    if not transcripts_dir.is_dir():
        return
    for project_dir in sorted(transcripts_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        for jsonl in sorted(project_dir.glob("*.jsonl")):
            yield project_dir.name, jsonl


def count_nested_files(transcripts_dir: Path) -> int:
    """1 階層目より深い transcript の数 (観測対象外だが件数は出す)。"""
    if not transcripts_dir.is_dir():
        return 0
    total = len(list(transcripts_dir.glob("*/**/*.jsonl")))
    direct = len(list(transcripts_dir.glob("*/*.jsonl")))
    return total - direct


def sync(conn: sqlite3.Connection, transcripts_dir: Path,
         now: dt.datetime | None = None) -> SyncReport:
    """lake と store の差分を解消する。

    lake から消えた file の row は store からも消す — 「store を消しても結果は
    変わらない」を文字通りに保ち、削除済み transcript の本文が store に残留する
    経路を塞ぐ (ADR 0031)。
    """
    stamp = _stamp(now or dt.datetime.now(dt.timezone.utc))
    report = SyncReport(skipped_nested_files=count_nested_files(transcripts_dir))
    # 「読めなかった」は今回の観測結果として作り直す (前回読めなかった file が
    # 今回読めた場合に古い row が残らないように)
    conn.execute("DELETE FROM unreadable_file")

    known: dict[str, tuple[int, int, int]] = {
        str(row["path"]): (int(row["file_id"]), int(row["mtime_ns"]),
                           int(row["size_bytes"]))
        for row in conn.execute(
            "SELECT file_id, path, mtime_ns, size_bytes FROM file")
    }
    seen: set[str] = set()

    for project_dir, jsonl in iter_lake_files(transcripts_dir):
        key = str(jsonl)
        seen.add(key)
        try:
            # fingerprint は read の**前**に採る (docstring の順序保証)
            stat = jsonl.stat()
        except OSError as exc:
            _note_unreadable(conn, jsonl, project_dir, f"stat: {exc.strerror}", stamp)
            report.unreadable_files += 1
            continue
        fingerprint = (stat.st_mtime_ns, stat.st_size)
        existing = known.get(key)
        if existing is not None and (existing[1], existing[2]) == fingerprint:
            report.unchanged_files += 1
            continue
        outcome = _ingest_file(conn, jsonl, project_dir, fingerprint, stamp)
        if outcome is None:
            report.unreadable_files += 1
            continue
        report.ingested_files += 1
        report.records += outcome[0]
        report.broken_lines += outcome[1]

    for path, (file_id, _mtime, _size) in known.items():
        if path not in seen:
            _forget_file(conn, file_id)
            report.removed_files += 1
    conn.commit()
    return report


def _note_unreadable(conn: sqlite3.Connection, path: Path, project_dir: str,
                     reason: str, stamp: str) -> None:
    conn.execute(
        "INSERT INTO unreadable_file (path, project_dir, reason, observed_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT (path) DO UPDATE SET "
        "reason = excluded.reason, observed_at = excluded.observed_at",
        (str(path), project_dir, reason, stamp),
    )


def _delete_projection_rows(conn: sqlite3.Connection, file_id: int) -> None:
    """spine + projection の row を落とす (file 行は残す)。file 再取得の前処理。

    `_forget_file` (lake から消えた file の row 削除) もこの関数を経由する。
    `user_prompt` を漏らすと、削除済み transcript の手入力 prompt 原文が store に
    残留する privacy 経路が復活する (ADR 0031 の cache invariant)。
    """
    for table in (*store_mod.PROJECTION_TABLES, "record"):
        conn.execute(f"DELETE FROM {table} WHERE file_id = ?", (file_id,))


def _forget_file(conn: sqlite3.Connection, file_id: int) -> None:
    """lake から消えた file を store からも消す。"""
    _delete_projection_rows(conn, file_id)
    conn.execute("DELETE FROM file WHERE file_id = ?", (file_id,))


def _ingest_file(conn: sqlite3.Connection, path: Path, project_dir: str,
                 fingerprint: tuple[int, int], stamp: str) -> tuple[int, int] | None:
    """1 file を全置換で取り込む。読めなければ None。"""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fp:
            lines = fp.readlines()
    except OSError as exc:
        _note_unreadable(conn, path, project_dir, f"read: {exc.strerror}", stamp)
        return None

    rows = _parse_lines(lines)
    row = conn.execute("SELECT file_id FROM file WHERE path = ?", (str(path),)).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO file (path, project_dir, mtime_ns, size_bytes, "
            "record_count, broken_lines, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(path), project_dir, fingerprint[0], fingerprint[1],
             len(rows.records), rows.broken_lines, stamp),
        )
        file_id = int(cur.lastrowid)
    else:
        file_id = int(row["file_id"])
        _delete_projection_rows(conn, file_id)
        conn.execute(
            "UPDATE file SET project_dir = ?, mtime_ns = ?, size_bytes = ?, "
            "record_count = ?, broken_lines = ?, ingested_at = ? WHERE file_id = ?",
            (project_dir, fingerprint[0], fingerprint[1], len(rows.records),
             rows.broken_lines, stamp, file_id),
        )

    conn.executemany(
        "INSERT INTO record (file_id, line_no, record_uuid, record_type, subtype, "
        "session_id, ts, ts_epoch, cwd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(file_id, *r) for r in rows.records],
    )
    conn.executemany(
        "INSERT INTO tool_use (file_id, line_no, block_no, tool_use_id, tool, "
        "command, target_path, input_excerpt, outcome_base, denial_kind, "
        "result_text, paired, unit_id, attribution_skill) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(file_id, *t.as_row()) for t in rows.tool_uses],
    )
    conn.executemany(
        "INSERT INTO hook_firing (file_id, line_no, hook_name, hook_event, "
        "attachment_type, command, exit_code, duration_ms, timed_out) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(file_id, *h) for h in rows.hook_firings],
    )
    conn.executemany(
        "INSERT INTO user_prompt (file_id, line_no, text, has_tool_result, "
        "is_compact_summary, is_meta, is_sidechain, prompt_source, "
        "origin_is_dict, origin_kind, git_branch, cli_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(file_id, *u) for u in rows.user_prompts],
    )
    conn.executemany(
        "INSERT INTO slash_invocation (file_id, line_no, command_name) "
        "VALUES (?, ?, ?)",
        [(file_id, *s) for s in rows.slash_invocations],
    )
    conn.executemany(
        "INSERT INTO user_turn (file_id, line_no, text_excerpt) VALUES (?, ?, ?)",
        [(file_id, *u) for u in rows.user_excerpts],
    )
    conn.executemany(
        "INSERT INTO assistant_turn (file_id, line_no, attribution_skill, "
        "input_tokens, output_tokens, cache_creation_input_tokens, "
        "cache_read_input_tokens) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(file_id, *a) for a in rows.assistant_turns],
    )
    conn.executemany(
        "INSERT INTO presented_name (file_id, line_no, seq, attachment_type, name) "
        "VALUES (?, ?, ?, ?, ?)",
        [(file_id, *p) for p in rows.presented_names],
    )
    conn.executemany(
        "INSERT INTO static_payload (file_id, line_no, attachment_type, chars, "
        "cjk_chars) VALUES (?, ?, ?, ?, ?)",
        [(file_id, *s) for s in rows.static_payloads],
    )
    conn.executemany(
        "INSERT INTO memory_injection (file_id, line_no, path, display_path, "
        "memory_type, globs, chars, cjk_chars, lines, differs_from_disk) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(file_id, *m) for m in rows.memory_injections],
    )
    conn.executemany(
        "INSERT INTO compact_boundary (file_id, line_no, trigger, pre_tokens, "
        "post_tokens, cumulative_dropped_tokens, duration_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(file_id, *c) for c in rows.compact_boundaries],
    )
    return len(rows.records), rows.broken_lines


@dataclasses.dataclass
class _ToolUseRow:
    """`tool_use` 表の 1 行。結果側の 4 項目は後続の tool_result で埋まる。

    `unit_id` / `attribution_skill` は v2 (#498) 追加。列順は `as_row()` の
    dataclass field 順がそのまま INSERT 列順になるため、既存列より後ろに足す。
    """

    line_no: int
    block_no: int
    tool_use_id: str
    tool: str
    command: str
    target_path: str
    input_excerpt: str
    outcome_base: str = "unknown"
    denial_kind: str = ""
    result_text: str = ""
    paired: int = 0
    unit_id: str = ""
    attribution_skill: str = ""

    def as_row(self) -> tuple:
        return dataclasses.astuple(self)


@dataclasses.dataclass
class _ParsedFile:
    records: list[tuple] = dataclasses.field(default_factory=list)
    tool_uses: list[_ToolUseRow] = dataclasses.field(default_factory=list)
    hook_firings: list[tuple] = dataclasses.field(default_factory=list)
    user_prompts: list[tuple] = dataclasses.field(default_factory=list)
    slash_invocations: list[tuple] = dataclasses.field(default_factory=list)
    user_excerpts: list[tuple] = dataclasses.field(default_factory=list)
    assistant_turns: list[tuple] = dataclasses.field(default_factory=list)
    presented_names: list[tuple] = dataclasses.field(default_factory=list)
    static_payloads: list[tuple] = dataclasses.field(default_factory=list)
    memory_injections: list[tuple] = dataclasses.field(default_factory=list)
    compact_boundaries: list[tuple] = dataclasses.field(default_factory=list)
    broken_lines: int = 0


def _parse_lines(lines: list[str]) -> _ParsedFile:
    """1 file 分の行を spine + projection へ平坦化する。

    tool_use ↔ tool_result のペアリングをここで済ませるのは、`tool_use_id` の
    一意性が lake で保証されないため。SQL の join に委ねると重複 id が
    cross product になり静かに二重計上する。**1 つの tool_result を消費する
    tool_use は高々 1 件**という一対一を、この 1 pass で構造的に保つ。

    同じ id の tool_use が複数現れたら **最後の 1 件**が結果を受け取り、先行分は
    `paired = 0` / `outcome_base = 'unknown'` のまま残る (lake では直近の呼び出しが
    その id の実体で、古い方は解決されないため)。実 lake 2,870 file では同一 file 内の
    重複 id は 0 件で、この分岐は罠 fixture でしか踏まない。
    """
    parsed = _ParsedFile()
    # tool_use_id → parsed.tool_uses の index。同じ id の tool_use が再び現れたら
    # 上書きする (後勝ち)
    pending: dict[str, int] = {}

    for line_no, raw in enumerate(lines, start=1):
        rec = _load_record(raw)
        if rec is None:
            if raw.strip():
                parsed.broken_lines += 1
            continue
        record_type = str(rec.get("type") or "")
        ts = str(rec.get("timestamp") or "")
        parsed.records.append((
            line_no,
            str(rec.get("uuid") or ""),
            record_type,
            str(rec.get("subtype") or ""),
            session_id_of(rec),
            ts,
            parse_ts_epoch(ts),
            str(rec.get("cwd") or ""),
        ))
        if record_type == "assistant":
            _collect_tool_uses(rec, line_no, parsed, pending)
            _collect_assistant_turn(rec, line_no, parsed)
        elif record_type == "user":
            _resolve_tool_results(rec, parsed, pending)
            _collect_user_events(rec, line_no, parsed)
            _collect_user_prompt(rec, line_no, parsed)
        elif record_type == "attachment":
            firing = hook_firing_of(rec)
            if firing is not None:
                parsed.hook_firings.append((
                    line_no, firing.hook_name, firing.hook_event,
                    firing.attachment_type, firing.command, firing.exit_code,
                    firing.duration_ms, 1 if firing.timed_out else 0,
                ))
            _collect_attachment_projections(rec, line_no, parsed)
        elif record_type == "system":
            boundary = compact_boundary_of(rec)
            if boundary is not None:
                parsed.compact_boundaries.append((
                    line_no, boundary["trigger"], boundary["pre_tokens"],
                    boundary["post_tokens"], boundary["cumulative_dropped_tokens"],
                    boundary["duration_ms"],
                ))
    return parsed


def _load_record(raw: str) -> dict | None:
    line = raw.strip()
    if not line:
        return None
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    return rec if isinstance(rec, dict) else None


def _collect_tool_uses(rec: dict, line_no: int, parsed: _ParsedFile,
                       pending: dict[str, int]) -> None:
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    attribution = rec.get("attributionSkill")
    attribution = attribution if isinstance(attribution, str) else ""
    for block_no, block in enumerate(content):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool = str(block.get("name") or "")
        tool_use_id = str(block.get("id") or "")
        block_input = block.get("input")
        block_input = block_input if isinstance(block_input, dict) else {}
        command = block_input.get("command")
        parsed.tool_uses.append(_ToolUseRow(
            line_no=line_no,
            block_no=block_no,
            tool_use_id=tool_use_id,
            tool=tool,
            command=command if isinstance(command, str) else "",
            target_path=_target_path(block_input),
            input_excerpt=truncate(json.dumps(block_input, ensure_ascii=False),
                                   INPUT_EXCERPT_LIMIT),
            unit_id=_unit_id(tool, block_input),
            attribution_skill=attribution,
        ))
        if tool_use_id:
            pending[tool_use_id] = len(parsed.tool_uses) - 1


def _unit_id(tool: str, block_input: dict) -> str:
    """Skill / Agent tool_use の識別引数 (skill 名 / subagent_type)。他 tool は空文字。"""
    key = UNIT_ID_KEYS.get(tool)
    if key is None:
        return ""
    value = block_input.get(key)
    return value.strip() if isinstance(value, str) else ""


def _collect_assistant_turn(rec: dict, line_no: int, parsed: _ParsedFile) -> None:
    """assistant turn の usage を 1 行にする。usage が無い turn は row を作らない。"""
    usage = usage_of(rec)
    if usage is None:
        return
    attribution = rec.get("attributionSkill")
    parsed.assistant_turns.append((
        line_no, attribution if isinstance(attribution, str) else "",
        usage.get("input_tokens", 0), usage.get("output_tokens", 0),
        usage.get("cache_creation_input_tokens", 0),
        usage.get("cache_read_input_tokens", 0),
    ))


def _collect_user_events(rec: dict, line_no: int, parsed: _ParsedFile) -> None:
    """user turn から実呼出 slash command と表示可能 text の抜粋を拾う。"""
    content = (rec.get("message") or {}).get("content")
    slash = parse_slash_invocation(content)
    if slash:
        parsed.slash_invocations.append((line_no, slash))
    text = extract_user_text(content)
    if text:
        parsed.user_excerpts.append((line_no, truncate(text, INPUT_EXCERPT_LIMIT)))


def _presented_names(body: dict, atype: str) -> list[str]:
    names: list[str] = []
    for field in PRESENTED_NAME_FIELDS[atype]:
        for name in body.get(field) or []:
            if isinstance(name, str) and name:
                names.append(name)
    return names


def _collect_attachment_projections(rec: dict, line_no: int,
                                    parsed: _ParsedFile) -> None:
    """静的コンテキスト・提示分母・memory file 注入を attachment record から拾う。

    hook 系 (`hook_firing_of`) とは type 集合が排他なので、呼び元 (`_parse_lines`)
    と分けて呼んでも二重計上しない。
    """
    body = attachment_of(rec)
    if body is None:
        return
    atype = str(body.get("type") or "")
    if atype in PRESENTED_NAME_FIELDS:
        for seq, name in enumerate(_presented_names(body, atype)):
            parsed.presented_names.append((line_no, seq, atype, name))
    if atype in STATIC_PAYLOAD_FIELDS:
        text = attachment_text_of(body, STATIC_PAYLOAD_FIELDS[atype])
        cjk, other = count_chars(text)
        parsed.static_payloads.append((line_no, atype, cjk + other, cjk))
    injection = memory_injection_of(rec)
    if injection is not None:
        cjk, other = count_chars(injection.text)
        parsed.memory_injections.append((
            line_no, injection.path, injection.display_path,
            injection.memory_type, json.dumps(injection.globs),
            cjk + other, cjk,
            (injection.text.count("\n") + 1) if injection.text else 0,
            1 if injection.differs_from_disk else 0,
        ))


def _resolve_tool_results(rec: dict, parsed: _ParsedFile,
                          pending: dict[str, int]) -> None:
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    result_blocks = [b for b in content
                     if isinstance(b, dict) and b.get("type") == "tool_result"]
    if not result_blocks:
        return
    structured = tool_use_result_of(rec, result_blocks)
    denial_kind = rec.get("toolDenialKind")
    denial_kind = denial_kind if isinstance(denial_kind, str) else None
    for block in result_blocks:
        tool_use_id = block.get("tool_use_id")
        if tool_use_id not in pending:
            continue
        block_content = block.get("content")
        row = parsed.tool_uses[pending.pop(tool_use_id)]
        row.outcome_base = classify_base_outcome(
            block.get("is_error"), block_content, structured, denial_kind)
        row.denial_kind = denial_kind or ""
        row.result_text = truncate(flatten_result_text(block_content),
                                   RESULT_TEXT_LIMIT)
        row.paired = 1


def _collect_user_text(content: Any) -> tuple[str, int]:
    """user record の `message.content` から (全文 text, tool_result を含むか)。

    典型は str だが、画像添付時は content が block list になる。list の場合は
    text block だけを改行連結する。**切り詰めない** — prompts mart の truncate は
    presentation 側の関心事 (ADR 0031: store が全文を持つ)。

    前後の空白は strip する — mart 側の `text_chars` / `steering_pattern` は
    strip 後の text を基準にする契約 (旧実装と同じ)。slash 展開判定
    (`is_slash_expansion_record` の `lstrip()`) や `#rule ` 捕捉判定 (行単位で
    空行を無視) は strip の有無で結果が変わらないため、strip 後の値を格納しても
    判定契約は壊れない。
    """
    if isinstance(content, str):
        return content.strip(), 0
    if isinstance(content, list):
        parts: list[str] = []
        has_tool_result = 0
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "tool_result":
                has_tool_result = 1
            elif block_type == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip(), has_tool_result
    return "", 0


def _collect_user_prompt(rec: dict, line_no: int, parsed: _ParsedFile) -> None:
    """user record 1 件を `user_prompt` の 1 行へ正規化する。

    「欠落」と「値が human 以外」を区別するため `prompt_source` / `origin_kind` は
    NULL 許容にする — フィールド自体が無いときだけ NULL、読めた値は空文字を
    含めそのまま持つ (mart 側の判定契約が NULL とその他を書き分けられるように)。
    """
    content = (rec.get("message") or {}).get("content")
    text, has_tool_result = _collect_user_text(content)

    prompt_source = rec.get("promptSource")
    prompt_source = prompt_source if isinstance(prompt_source, str) else None

    origin = rec.get("origin")
    origin_is_dict = 1 if isinstance(origin, dict) else 0
    origin_kind = origin.get("kind") if origin_is_dict else None
    origin_kind = origin_kind if isinstance(origin_kind, str) else None

    parsed.user_prompts.append((
        line_no,
        text,
        has_tool_result,
        1 if rec.get("isCompactSummary") is True else 0,
        1 if rec.get("isMeta") is True else 0,
        1 if rec.get("isSidechain") is True else 0,
        prompt_source,
        origin_is_dict,
        origin_kind,
        str(rec.get("gitBranch") or ""),
        str(rec.get("version") or ""),
    ))


def _target_path(block_input: dict) -> str:
    for key in TARGET_PATH_KEYS:
        value = block_input.get(key)
        if isinstance(value, str):
            return value
    return ""


def parse_ts_epoch(ts: str) -> float | None:
    """ISO timestamp を unix 秒へ。欠損・解釈不能は None (窓判定は mart 側の責務)。"""
    if not ts:
        return None
    try:
        moment = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.timestamp()


def _stamp(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def open_synced(transcripts_dir: Path, cache_dir: str | Path | None = None,
                now: dt.datetime | None = None
                ) -> tuple[sqlite3.Connection, SyncReport]:
    """store を開いて差分 sync を前置し、接続と sync 結果を返す。

    **鮮度は呼び出し側の関心事にしない** — 全 tool がこの入口を通ることで、
    「いつ時点の lake か」を各 tool が判断しなくて済む (ADR 0031)。

    接続と sync は `sync_lock` の中で行う。store が無い状態では複数プロセスが
    同時に full ingest を始め、SQLite の busy_timeout では待ち切れずに
    `database is locked` で落ちるため (writer 同士は WAL でも排他)。後続は先行の
    完了を待ってから差分 sync に入る。schema drift 検出時の drop & rebuild
    (`store.connect`) も同じ lock の内側で直列化される。
    """
    with lock.sync_lock(store_mod.store_path(transcripts_dir, cache_dir)):
        conn = store_mod.connect(transcripts_dir, cache_dir)
        report = sync(conn, Path(transcripts_dir), now=now)
    return conn, report
