#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""inventory-permissions の観測 script (data mart 生成)。

transcript (`~/.claude/projects/**/*.jsonl`) を data lake として直近 N 日
(--days、default 30) を **stateless 全走査** し、以下 2 軸 × 2 section を含む
mart JSON を出力する。

- A 軸 (設定 pattern 軸): 各 permissions.allow / deny / ask / sandbox
  excludedCommands entry のマッチ実行数 (matcher_confidence: exact / approx)
- B 軸 (実績軸): tool × command_head × outcome 頻度
- bypass 系列: 同 session の deny 直後 N 件の同種 tool 呼び出し (refine 証拠)
- guard hook 逆引き: hook-deny 相当の toolDenialKind + 文言別内訳
- derived_views: LLM 段階が典型的に必要とする前計算 view (zero match /
  high deny share / 未収載頻出 unit / bypass 集約)。mart 直読みの摩擦を避ける。
  詳細は build_derived_views の docstring
- section: project (cwd × 当該 repo 実績) / global (`~/.claude/settings.json` ×
  全 repo 実績)。省略時 project、`--section all` で両方。

原則 (map #209): **観測・集計は決定的に、判断は人間に、LLM は文章の具体化のみ**。
script は bucket (revoke / promote / refine / sandbox / keep) を割り当てない。
bucket 判定は SKILL.md 手順の LLM 段階で mart を読んでから行う (循環依存の回避)。

出力: --output-dir に run-<timestamp>/ を作り、LLM 段階が読む順の分割ファイル
(00-meta / 10-derived-views / 20-axis-a / 30-bypass-samples / 90-mart) を書いて
path を読む順に stdout へ print する (詳細は split_outputs の docstring)。
Markdown レポートは LLM 段階の成果物で、本 script は生成しない。

汎用スキル制約: 依存は Claude Code 標準ファイルのみ
(`~/.claude/projects/` / `~/.claude/settings.json` / `<repo>/.claude/settings.json` /
`<repo>/.claude/settings.local.json`)。swat-skills 固有 hook 資産
(tool-signatures.jsonl 等) には触らない。
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _transcript_lib import (  # noqa: E402
    USER_REJECT_PATTERNS,
    _iter_jsonl,
    resolve_now,
    truncate,
    walk_transcripts,
)

# --- 定数 --------------------------------------------------------------------

# 00-meta の contract に emit する schema 版 (単調増加 int、読み手側の将来分岐用)
SCHEMA_VERSION = 1

DEFAULT_DAYS = 30
DEFAULT_TRANSCRIPTS_DIR = Path("~/.claude/projects").expanduser()
DEFAULT_GLOBAL_SETTINGS = Path("~/.claude/settings.json").expanduser()
DEFAULT_OUTPUT_DIR = Path("/tmp/inventory-permissions")

DEFAULT_SUFFICIENT_THRESHOLD = 30
BYPASS_LOOKAHEAD = 5
BYPASS_MAX_GAP_SECONDS = 300

# derived_views の集計パラメータ (sort / filter の閾値であり bucket 判定ではない)
DERIVED_TOP_N = 30
HIGH_DENY_MIN_MATCH = 20
HIGH_DENY_MIN_RATIO = 0.3
UNLISTED_MIN_COUNT = 5
FOLLOWUP_FAST_GAP_SECONDS = 10
HARD_DENY_OUTCOMES = ("deny_permission-rule", "deny_automode")

# 分割出力 (読む順) のパラメータ
SPLIT_SECTION_SUMMARY_KEYS = ("settings_sources", "event_count",
                              "distinct_sessions", "outcome_totals")
BYPASS_SAMPLE_GROUPS = 10
BYPASS_SAMPLES_PER_GROUP = 2

# 分割ファイルの読む順・用途・標準フロー可否の単一ソース。split_outputs と 00-meta の
# contract.files が本定数を iterate する (二重管理を廃止)。purpose は data 内容の記述
# のみ — bucket 語彙 (revoke / promote / ...) は含めない (循環依存の回避)。
SPLIT_FILES = (
    {"name": "00-meta.json", "order": 0, "standard_flow": True,
     "purpose": "meta + section 概況 (判定可能性の分岐に必要な最小情報) + 本 contract"},
    {"name": "10-derived-views.json", "order": 10, "standard_flow": True,
     "purpose": "derived_views + guard_reverse_lookup"},
    {"name": "20-axis-a.json", "order": 20, "standard_flow": True,
     "purpose": "全設定 entry の両軸集計 (全 entry を含む母集団)"},
    {"name": "30-bypass-samples.json", "order": 30, "standard_flow": True,
     "purpose": "top bypass group の代表系列"},
    {"name": "90-mart.json", "order": 90, "standard_flow": False,
     "purpose": "mart 全量 (bypass_sequences / axis_b_actual_usage 含む、想定外の追加検査用)"},
)

# Bash command_head の抽出上限 (先頭 2 token を primary key に、fallback で先頭 1)
COMMAND_HEAD_MAX_TOKENS = 2
CONTENT_EXCERPT_LIMIT = 200

# `Tool(pattern)` 形式を解体
PERMISSION_ENTRY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*(?:\((.*)\))?\s*$")

# tool_result.content 内の deny 定型文言 (permission-rule)
PERMISSION_DENIAL_TOOL_RE = re.compile(
    r"Permission to use\s+(?P<tool>[A-Za-z_][A-Za-z0-9_-]*)"
    r"(?:\s+with\s+command\s+(?P<cmd>.+?))?\s+has been denied\.",
    re.DOTALL,
)

# 自動モード分類器 deny の Reason 先頭ラベル `[Xxx Yyy]`
AUTOMODE_REASON_LABEL_RE = re.compile(r"Reason:\s*\[([^\]]+)\]")

# USER_REJECT_PATTERNS は _transcript_lib へ移設 (inventory-skill-mcp と共有)。

# NOTE: `~/.claude/projects/` の dir 名は cwd を `/` と `.` の両方を `-` に置換した
# **lossy** エンコード (`/Users/a-b/x.y` → `-Users-a-b-x-y`)。逆写像は unique に
# 決まらないため、cwd スコープ判定は event レベル (record.cwd) のみで行う。


# --- データ ------------------------------------------------------------------

@dataclasses.dataclass
class ToolEvent:
    tool: str
    command_head: str  # Bash なら先頭 2 token、他は "" (入力 file path を command_head にはしない)
    input_repr: str  # 抜粋 (200 char 切り詰め、原型復元用ではない)
    session_id: str
    timestamp: str  # ISO
    cwd: str
    project_dir: str
    outcome: str  # success / deny_permission-rule / deny_user-rejected / deny_automode / deny_hook / error / unknown
    denial_kind: str | None  # tool_result 側の toolDenialKind (permission-rule / user-rejected / automode-blocked / automode-unavailable / hook / None)
    denial_reason_label: str | None  # automode の [Label] / hook の識別断片
    raw_input: dict[str, Any]  # matcher 用の生 input (Bash.command 等)


@dataclasses.dataclass
class PermissionEntry:
    raw: str
    category: str  # allow / deny / ask / sandbox_excluded_commands
    source_path: str  # settings.json path
    scope: str  # global / project / project_local
    tool: str  # 先頭の Tool 名 (`Bash(...)` の Bash)
    pattern: str | None  # 括弧内 (無ければ None)
    confidence: str  # exact / approx
    match_kind: str  # exact_tool / exact_command / prefix / glob / none


# --- CLI ---------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--section", choices=["project", "global", "all"], default="project",
                   help="集計対象。default project (cwd × 当該 repo 実績)")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS,
                   help="観測窓 (日)。default 30")
    p.add_argument("--transcripts-dir", type=Path, default=DEFAULT_TRANSCRIPTS_DIR,
                   help="transcript lake。default ~/.claude/projects")
    p.add_argument("--global-settings", type=Path, default=DEFAULT_GLOBAL_SETTINGS,
                   help="global settings。default ~/.claude/settings.json")
    p.add_argument("--repo-root", type=Path, default=Path.cwd(),
                   help="現 project root。省略時 cwd")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help="mart 出力先。default /tmp/inventory-permissions")
    p.add_argument("--now", type=str, default=None,
                   help="ISO timestamp。窓の起点を固定 (テスト・再現用)")
    p.add_argument("--sufficient-threshold", type=int,
                   default=DEFAULT_SUFFICIENT_THRESHOLD,
                   help="相対判定可能とみなす総 event 閾値。default 30")
    p.add_argument("--bypass-lookahead", type=int, default=BYPASS_LOOKAHEAD,
                   help="bypass 系列の後続 event 上限。default 5")
    p.add_argument("--bypass-max-gap-seconds", type=int, default=BYPASS_MAX_GAP_SECONDS,
                   help="bypass 判定の最大 gap 秒。default 300")
    p.add_argument("--stdout-mart", action="store_true",
                   help="ファイルに書かず mart JSON を stdout に出す (テスト用)")
    return p.parse_args(argv)


# --- 共通ユーティリティ ------------------------------------------------------
# resolve_now / truncate は _transcript_lib へ移設 (inventory-skill-mcp と共有)。

def extract_command_head(command: str, max_tokens: int = COMMAND_HEAD_MAX_TOKENS) -> str:
    """Bash command から先頭 token を取り出す。

    `git diff origin/main` → `git diff` (max_tokens=2) / `ls -la` → `ls`。
    先頭が env var 代入 (VAR=x cmd ...) や `sudo` prefix でも簡素な取扱いに留める
    (matcher 側で共通 head を突合すれば十分)。
    """
    if not command:
        return ""
    # 改行・連結演算子で切って先頭コマンドのみ
    for sep in ("&&", "||", ";", "|"):
        if sep in command:
            command = command.split(sep, 1)[0]
    command = command.strip()
    tokens = command.split()
    if not tokens:
        return ""
    # 代入 (VAR=xxx cmd) prefix 除去
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens.pop(0)
    if not tokens:
        return ""
    head = " ".join(tokens[:max_tokens])
    # `subcommand` が redirection 混じりなら 1 token に degrade
    if any(c in head for c in ("<", ">", "$", "`")):
        return tokens[0]
    return head


def classify_outcome(
    is_error: Any,
    content: Any,
    tool_denial_kind: str | None,
) -> tuple[str, str | None, str | None]:
    """tool_result から outcome (7 分類) を返す。

    Returns (outcome, denial_kind, denial_reason_label)。
    - is_error == False または未定義 → ("success", None, None) — Anthropic API 仕様上
      `is_error` は optional で、無い場合は false 相当 (Read / Edit / TaskUpdate 等
      多くの tool の成功 result は is_error field を持たない)
    - is_error == True → toolDenialKind を優先、無ければ content 文言で fallback

    outcome 語彙:
      success / deny_permission-rule / deny_user-rejected / deny_automode /
      deny_hook / error / unknown
    """
    if is_error is False or is_error is None:
        return "success", None, None

    text = _flatten_text(content)

    if tool_denial_kind == "permission-rule":
        return "deny_permission-rule", "permission-rule", None
    if tool_denial_kind == "user-rejected":
        return "deny_user-rejected", "user-rejected", None
    if tool_denial_kind in ("automode-blocked", "automode-unavailable"):
        label = None
        m = AUTOMODE_REASON_LABEL_RE.search(text)
        if m:
            label = m.group(1).strip()
        return "deny_automode", tool_denial_kind, label

    # toolDenialKind が無い場合は content 文言で fallback (permission-rule /
    # user-rejected / automode の 3 種のみ)。hook-deny の text fallback は
    # git の pre-commit エラー等 (`hook failed with exit code 1`) を高頻度で
    # 誤検知するため採らない — hook 由来は明示的 toolDenialKind に限定する。
    if PERMISSION_DENIAL_TOOL_RE.search(text):
        return "deny_permission-rule", "permission-rule", None
    low = text.lower()
    for pat in USER_REJECT_PATTERNS:
        if pat in low:
            return "deny_user-rejected", "user-rejected", None
    if "denied by the claude code auto mode" in low:
        label = None
        m = AUTOMODE_REASON_LABEL_RE.search(text)
        if m:
            label = m.group(1).strip()
        return "deny_automode", "automode-blocked", label

    return "error", None, None


def _flatten_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if isinstance(c, dict):
                t = c.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return " ".join(parts)
    return ""


# --- transcript walk ---------------------------------------------------------
# _iter_jsonl / walk_transcripts は _transcript_lib へ移設 (inventory-skill-mcp と共有)。

def extract_events(
    jsonl_path: Path,
    cutoff: dt.datetime,
    project_dir: str,
) -> list[ToolEvent]:
    """1 transcript file から tool_use event を抽出し、outcome を後続
    tool_result で補完する。

    生 tool_use 記録に紐づく tool_result が同 file 内に無い場合は outcome=unknown
    のまま残す。cutoff より古い assistant record は skip。
    """
    events: list[ToolEvent] = []
    pending: dict[str, int] = {}

    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as fp:
            for rec in _iter_jsonl(fp):
                rtype = rec.get("type")
                if rtype == "user":
                    msg = rec.get("message") or {}
                    content = msg.get("content")
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") != "tool_result":
                                continue
                            tid = block.get("tool_use_id")
                            if tid in pending:
                                idx = pending.pop(tid)
                                tdk = rec.get("toolDenialKind")
                                outcome, dk, label = classify_outcome(
                                    block.get("is_error"),
                                    block.get("content"),
                                    tdk if isinstance(tdk, str) else None,
                                )
                                events[idx].outcome = outcome
                                events[idx].denial_kind = dk
                                events[idx].denial_reason_label = label
                    continue
                if rtype != "assistant":
                    continue
                ts_str = rec.get("timestamp") or ""
                try:
                    rec_ts = dt.datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00")
                    ).astimezone(dt.timezone.utc)
                except (ValueError, AttributeError):
                    rec_ts = None
                if rec_ts is not None and rec_ts < cutoff:
                    continue
                msg = rec.get("message") or {}
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                sid = rec.get("sessionId") or rec.get("session_id") or ""
                cwd = rec.get("cwd") or ""
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_use":
                        continue
                    name = block.get("name", "") or ""
                    tid = block.get("id", "") or ""
                    blk_input = block.get("input") or {}
                    if not isinstance(blk_input, dict):
                        blk_input = {}
                    command_head = ""
                    if name == "Bash":
                        cmd = blk_input.get("command", "")
                        if isinstance(cmd, str):
                            command_head = extract_command_head(cmd)
                    ev = ToolEvent(
                        tool=name,
                        command_head=command_head,
                        input_repr=truncate(
                            json.dumps(blk_input, ensure_ascii=False),
                            CONTENT_EXCERPT_LIMIT,
                        ),
                        session_id=str(sid),
                        timestamp=ts_str,
                        cwd=str(cwd),
                        project_dir=project_dir,
                        outcome="unknown",
                        denial_kind=None,
                        denial_reason_label=None,
                        raw_input=blk_input,
                    )
                    events.append(ev)
                    if tid:
                        pending[tid] = len(events) - 1
    except OSError:
        return []
    return events


# --- 設定 entry 列挙 ---------------------------------------------------------

def parse_permission_entry(raw: str, category: str, source_path: str, scope: str) -> PermissionEntry:
    """`Bash(git diff:*)` 等を分解し matcher の確度ラベルを付ける。

    match_kind:
      - `exact_tool`: `ToolName` のみ (全 invocation マッチ)
      - `exact_command`: 括弧内が exact 文字列 (Bash なら command 完全一致)
      - `prefix`: 括弧内が `xxx:*` (Bash: command 先頭一致)
      - `glob`: `**` や `?` を含む glob pattern (Read/Edit の file path 系)
      - `none`: 解釈不能

    confidence:
      - `exact`: exact_tool / exact_command / prefix (well-formed)
      - `approx`: glob / regex / 特殊記号入り (**内部 matcher と厳密に一致しない可能性**)
    """
    m = PERMISSION_ENTRY_RE.match(raw)
    tool = raw.strip()
    pattern: str | None = None
    match_kind = "exact_tool"
    confidence = "exact"
    if m:
        tool = m.group(1)
        pattern = m.group(2)
    if pattern is None:
        return PermissionEntry(raw=raw, category=category, source_path=source_path,
                                scope=scope, tool=tool, pattern=None,
                                confidence="exact", match_kind="exact_tool")
    # 括弧内あり
    if pattern.endswith(":*"):
        match_kind = "prefix"
        confidence = "exact"
    elif "*" in pattern or "?" in pattern or "**" in pattern:
        match_kind = "glob"
        confidence = "approx"
    else:
        match_kind = "exact_command"
        confidence = "exact"
    return PermissionEntry(raw=raw, category=category, source_path=source_path,
                            scope=scope, tool=tool, pattern=pattern,
                            confidence=confidence, match_kind=match_kind)


def read_permission_entries(settings_path: Path, scope: str) -> list[PermissionEntry]:
    """settings.json を読み permissions.{allow,deny,ask} + sandbox.excludedCommands
    を PermissionEntry の list として返す。file が無ければ空 list。
    """
    if not settings_path.is_file():
        return []
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    perms = data.get("permissions") if isinstance(data, dict) else None
    if not isinstance(perms, dict):
        return []
    result: list[PermissionEntry] = []
    for cat in ("allow", "deny", "ask"):
        for raw in (perms.get(cat) or []):
            if isinstance(raw, str):
                result.append(parse_permission_entry(raw, cat, str(settings_path), scope))
    sandbox = perms.get("sandbox")
    if isinstance(sandbox, dict):
        for raw in (sandbox.get("excludedCommands") or []):
            if isinstance(raw, str):
                result.append(parse_permission_entry(
                    raw, "sandbox_excluded_commands", str(settings_path), scope,
                ))
    return result


def enumerate_settings_sources(section: str, repo_root: Path,
                                global_settings: Path) -> list[dict]:
    """section 別に読む settings.json path のリストを返す。project section では
    `<repo>/.claude/settings.json(.local).json` を対象、global section では
    `~/.claude/settings.json` を対象、all は両方。
    """
    sources: list[dict] = []
    if section in ("project", "all"):
        for name, scope in (("settings.json", "project"),
                             ("settings.local.json", "project_local")):
            p = repo_root / ".claude" / name
            sources.append({"path": p, "scope": scope})
    if section in ("global", "all"):
        sources.append({"path": global_settings, "scope": "global"})
    return sources


# --- matcher -----------------------------------------------------------------

def entry_matches_event(entry: PermissionEntry, event: ToolEvent) -> bool:
    """conservative matcher。誤検知よりは取りこぼしを許す。

    - tool 名が一致しなければ即 False
    - `exact_tool` (括弧なし): 常に True (Bash などツール全体を許可/禁止する形)
    - `exact_command` (Bash): command が pattern と完全一致
    - `prefix` (Bash): command が `pattern[:-2]` で始まる
    - `glob` (Read/Edit/Write 等の path): input 内の候補 (file_path / path) を
      glob で照合。合致すれば True
    """
    if entry.tool != event.tool:
        return False
    if entry.pattern is None or entry.match_kind == "exact_tool":
        return True
    if event.tool == "Bash":
        cmd = event.raw_input.get("command", "") if isinstance(event.raw_input, dict) else ""
        if not isinstance(cmd, str):
            return False
        if entry.match_kind == "exact_command":
            return cmd.strip() == entry.pattern.strip()
        if entry.match_kind == "prefix":
            prefix = entry.pattern[:-2]  # ":*" 除去
            return cmd.startswith(prefix)
        if entry.match_kind == "glob":
            return fnmatch.fnmatch(cmd, entry.pattern)
        return False
    # Bash 以外 (Read/Edit/Write 等) は file path を候補にして glob 照合
    target_candidates: list[str] = []
    if isinstance(event.raw_input, dict):
        for key in ("file_path", "path", "notebook_path"):
            v = event.raw_input.get(key)
            if isinstance(v, str):
                target_candidates.append(v)
    if not target_candidates:
        return entry.match_kind == "prefix"  # 具体候補が無ければ tool 名一致で拾う保守側
    for t in target_candidates:
        if entry.match_kind in ("glob", "prefix", "exact_command"):
            # prefix/exact_command でも path 系 tool は glob 相当で扱う
            if fnmatch.fnmatch(t, entry.pattern):
                return True
    return False


# --- 集計 --------------------------------------------------------------------

def aggregate_axis_a(entries: list[PermissionEntry],
                     events: list[ToolEvent]) -> list[dict]:
    """設定 entry 別に match_count / outcome_breakdown / sample_matched を組む。"""
    out: list[dict] = []
    for e in entries:
        matched = [ev for ev in events if entry_matches_event(e, ev)]
        outcome_counter = collections.Counter(ev.outcome for ev in matched)
        # sample_matched: tool × command_head 別 count top 3
        combo_counter: collections.Counter = collections.Counter()
        for ev in matched:
            combo_counter[(ev.tool, ev.command_head)] += 1
        samples = [
            {"tool": tool, "command_head": head, "count": count}
            for (tool, head), count in combo_counter.most_common(3)
        ]
        out.append({
            "entry": e.raw,
            "category": e.category,
            "source_path": e.source_path,
            "scope": e.scope,
            "match_kind": e.match_kind,
            "matcher_confidence": e.confidence,
            "match_count": len(matched),
            "outcome_breakdown": dict(outcome_counter),
            "sample_matched": samples,
        })
    # match_count desc、次に category、次に entry の determinsitic sort
    out.sort(key=lambda x: (-x["match_count"], x["category"], x["entry"]))
    return out


def aggregate_axis_b(events: list[ToolEvent],
                     entries: list[PermissionEntry]) -> list[dict]:
    """tool × command_head × outcome の集計。config_matches に対応 entry の raw を挙げる。"""
    key_counter: collections.Counter = collections.Counter()
    outcome_map: dict[tuple[str, str], collections.Counter] = {}
    for ev in events:
        key = (ev.tool, ev.command_head)
        key_counter[key] += 1
        outcome_map.setdefault(key, collections.Counter())[ev.outcome] += 1
    # 突き合わせ用: entry ごとに match するかを representative event で試す
    out: list[dict] = []
    for (tool, head), count in key_counter.most_common():
        matches: list[str] = []
        # 代表 event 1 件を採り entry 照合 (raw_input 復元は先頭 event を使う)
        rep = next((ev for ev in events if ev.tool == tool and ev.command_head == head), None)
        if rep is not None:
            for e in entries:
                if entry_matches_event(e, rep):
                    matches.append(e.raw)
        out.append({
            "tool": tool,
            "command_head": head,
            "count": count,
            "outcomes": dict(outcome_map[(tool, head)]),
            "config_matches": matches,
        })
    return out


def extract_bypass_sequences(
    events: list[ToolEvent],
    lookahead: int,
    max_gap: int,
) -> list[dict]:
    """同 session の deny 直後 N 件の同 tool 呼び出しを bypass 系列として抽出。

    - session_id で group 化
    - 各 event を時刻順に並べ、deny を trigger にし
    - 後続 lookahead 件までの同 tool 呼び出しを follow_up として収集
    - trigger と follow_up の gap が max_gap 秒を超えたら follow_up 打ち切り

    「意図の同一性」判定はしない — 系列をそのまま人間に提示する。
    """
    by_session: dict[str, list[ToolEvent]] = collections.defaultdict(list)
    for ev in events:
        by_session[ev.session_id].append(ev)
    for lst in by_session.values():
        lst.sort(key=lambda ev: ev.timestamp)

    sequences: list[dict] = []
    for sid, evs in by_session.items():
        for i, ev in enumerate(evs):
            if not ev.outcome.startswith("deny_"):
                continue
            trigger_ts = _parse_ts(ev.timestamp)
            follow_ups = []
            for j in range(i + 1, min(len(evs), i + 1 + lookahead)):
                cand = evs[j]
                if cand.tool != ev.tool:
                    continue
                cand_ts = _parse_ts(cand.timestamp)
                if trigger_ts and cand_ts:
                    gap = (cand_ts - trigger_ts).total_seconds()
                    if gap < 0 or gap > max_gap:
                        continue
                    gap_sec = int(gap)
                else:
                    gap_sec = None
                follow_ups.append({
                    "tool": cand.tool,
                    "command_head": cand.command_head,
                    "input_excerpt": cand.input_repr,
                    "outcome": cand.outcome,
                    "gap_seconds": gap_sec,
                    "timestamp": cand.timestamp,
                })
                if len(follow_ups) >= lookahead:
                    break
            if not follow_ups:
                continue
            sequences.append({
                "session_id": sid,
                "denied_at": ev.timestamp,
                "denied_tool": ev.tool,
                "denied_command_head": ev.command_head,
                "denied_input_excerpt": ev.input_repr,
                "denied_outcome": ev.outcome,
                "denial_kind": ev.denial_kind,
                "denial_reason_label": ev.denial_reason_label,
                "cwd": ev.cwd,
                "follow_ups": follow_ups,
            })
    # timestamp desc の順 (recent first)
    sequences.sort(key=lambda s: s["denied_at"], reverse=True)
    return sequences


def _parse_ts(ts: str) -> dt.datetime | None:
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except (ValueError, AttributeError):
        return None


def aggregate_guard_reverse(events: list[ToolEvent]) -> list[dict]:
    """guard 系 deny (denial_kind in {automode-blocked, automode-unavailable})
    を Reason label 別に集計 + 代表文言を保持。

    hook-deny (PreToolUse permissionDecision: deny) は Claude Code が明示的な
    toolDenialKind を emit した場合のみ含める (現状の transcript 実測では観測なし)。
    hooks.json の静的列挙はしない — transcript の denial 実績のみから逆引き。
    """
    guard_kinds = ("automode-blocked", "automode-unavailable")
    buckets: dict[tuple[str, str | None], list[ToolEvent]] = collections.defaultdict(list)
    for ev in events:
        if ev.denial_kind in guard_kinds:
            buckets[(ev.denial_kind, ev.denial_reason_label)].append(ev)
    out: list[dict] = []
    for (kind, label), evs in buckets.items():
        samples = []
        for ev in sorted(evs, key=lambda e: e.timestamp, reverse=True)[:3]:
            samples.append({
                "session_id": ev.session_id,
                "timestamp": ev.timestamp,
                "tool": ev.tool,
                "input_excerpt": ev.input_repr,
                "cwd": ev.cwd,
            })
        out.append({
            "denial_kind": kind,
            "reason_label": label,
            "deny_count": len(evs),
            "samples": samples,
        })
    out.sort(key=lambda x: (-x["deny_count"], str(x.get("denial_kind")),
                             str(x.get("reason_label"))))
    return out


# derived view の view 名 → 1 行意味論 (00-meta の contract.views に emit)。
# build_derived_views 出力の key 集合と一致させる (整合性テストで固定)。data 特徴のみ
# で記述し bucket 語彙 (revoke / promote / ...) は含めない (循環依存の回避)。
DERIVED_VIEW_SEMANTICS = {
    "axis_a_zero_match": "観測窓内で match_count == 0 の設定 entry。",
    "axis_a_high_deny_share": (
        f"match_count >= {HIGH_DENY_MIN_MATCH} かつ hard deny "
        f"(permission-rule + automode) 比率 >= {HIGH_DENY_MIN_RATIO} の entry。"
        "user-rejected は #29499 の false positive 影響下のため hard deny に数えない。"
    ),
    "axis_b_unlisted_frequent": (
        f"config 未収載かつ count >= {UNLISTED_MIN_COUNT} の permission 関連 unit "
        "(Bash / mcp__* / deny_permission-rule 実績あり)。permission entry でゲート"
        "されない built-in tool は units から除外し omitted_non_permission_units に件数計上。"
    ),
    "bypass_grouped": (
        "(denial_kind, denied_tool, denied_command_head) 別の系列数と、first follow_up が "
        f"success かつ gap <= {FOLLOWUP_FAST_GAP_SECONDS} 秒の件数 (代替経路の存在示唆)。"
    ),
}


def build_derived_views(axis_a: list[dict], axis_b: list[dict],
                        bypass_sequences: list[dict]) -> dict:
    """LLM 段階が典型的に必要とする view を決定的に前計算する。

    mart 本体 (数 MB 級) を LLM 段階で ad-hoc に slice する摩擦 (直読みの
    コンテキスト消費 / 環境によっては inline python 実行が hook で禁止) を
    避けるための派生集計。各 view の意味論は DERIVED_VIEW_SEMANTICS を単一ソースとし
    (00-meta の contract.views に emit)、本関数の出力 key 集合と一致させる。
    bucket (revoke / promote / ...) は割り当てない — 循環依存の回避。
    """
    zero_match = [
        {
            "entry": r["entry"],
            "category": r["category"],
            "scope": r["scope"],
            "matcher_confidence": r["matcher_confidence"],
        }
        for r in axis_a if r["match_count"] == 0
    ]

    high_deny: list[dict] = []
    for r in axis_a:
        if r["match_count"] < HIGH_DENY_MIN_MATCH:
            continue
        hard_deny = sum(r["outcome_breakdown"].get(k, 0) for k in HARD_DENY_OUTCOMES)
        share = hard_deny / r["match_count"]
        if share >= HIGH_DENY_MIN_RATIO:
            high_deny.append({
                "entry": r["entry"],
                "category": r["category"],
                "scope": r["scope"],
                "match_count": r["match_count"],
                "hard_deny_count": hard_deny,
                "hard_deny_share": round(share, 2),
                "outcome_breakdown": r["outcome_breakdown"],
                "sample_matched": r["sample_matched"],
            })
    high_deny.sort(key=lambda x: (-x["hard_deny_share"], -x["match_count"], x["entry"]))

    units: list[dict] = []
    omitted = 0
    for r in axis_b:
        if r["config_matches"] or r["count"] < UNLISTED_MIN_COUNT:
            continue
        permission_relevant = (
            r["tool"] == "Bash"
            or r["tool"].startswith("mcp__")
            or r["outcomes"].get("deny_permission-rule", 0) > 0
        )
        if not permission_relevant:
            omitted += 1
            continue
        units.append(r)
    units.sort(key=lambda x: (-x["count"], x["tool"], x["command_head"]))

    grouped: dict[tuple, dict] = {}
    for s in bypass_sequences:
        key = (s.get("denial_kind"), s["denied_tool"], s.get("denied_command_head", ""))
        g = grouped.setdefault(key, {
            "denial_kind": s.get("denial_kind"),
            "denied_tool": s["denied_tool"],
            "denied_command_head": s.get("denied_command_head", ""),
            "count": 0,
            "fast_success_follow_up_count": 0,
            "latest_denied_at": s["denied_at"],
        })
        g["count"] += 1
        g["latest_denied_at"] = max(g["latest_denied_at"], s["denied_at"])
        first = s["follow_ups"][0] if s["follow_ups"] else None
        if (first is not None and first["outcome"] == "success"
                and first.get("gap_seconds") is not None
                and first["gap_seconds"] <= FOLLOWUP_FAST_GAP_SECONDS):
            g["fast_success_follow_up_count"] += 1
    bypass_grouped = sorted(
        grouped.values(),
        key=lambda x: (-x["count"], str(x["denial_kind"]), x["denied_tool"],
                       x["denied_command_head"]),
    )

    return {
        "axis_a_zero_match": zero_match,
        "axis_a_high_deny_share": high_deny,
        "axis_b_unlisted_frequent": {
            "units": units[:DERIVED_TOP_N],
            "omitted_non_permission_units": omitted,
        },
        "bypass_grouped": bypass_grouped[:DERIVED_TOP_N],
    }


def build_bypass_group_samples(bypass_grouped: list[dict],
                               sequences: list[dict]) -> list[dict]:
    """bypass_grouped の top group ごとに代表系列を機械的に選ぶ。

    sequences は extract_bypass_sequences が denied_at 降順で返すため、
    先頭から BYPASS_SAMPLES_PER_GROUP 件 = 最新の代表系列になる。
    """
    out: list[dict] = []
    for g in bypass_grouped[:BYPASS_SAMPLE_GROUPS]:
        matches = [
            s for s in sequences
            if s.get("denial_kind") == g["denial_kind"]
            and s["denied_tool"] == g["denied_tool"]
            and s.get("denied_command_head", "") == g["denied_command_head"]
        ]
        out.append({**g, "samples": matches[:BYPASS_SAMPLES_PER_GROUP]})
    return out


def build_contract() -> dict:
    """00-meta.json に埋め込む機械可読 contract (mart schema 知識の単一ソース)。

    分割ファイルの読む順・用途 (SPLIT_FILES) と derived view の意味論
    (DERIVED_VIEW_SEMANTICS) を script 発の contract として LLM 段階へ渡す。SKILL.md は
    本 contract を参照し schema を再エンコードしない。schema_version は読み手側の将来分岐用。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "files": [dict(f) for f in SPLIT_FILES],
        "views": dict(DERIVED_VIEW_SEMANTICS),
    }


def split_outputs(mart: dict) -> list[tuple[str, dict]]:
    """mart を LLM 段階が読む順に並べた分割ファイル群へ組み替える。

    読む順・用途・標準フロー可否は SPLIT_FILES を単一ソースとし、本関数はファイル名 →
    doc builder の対応のみを持つ (00-meta には build_contract の結果を同梱する)。各
    ファイルの意味は 00-meta.json の contract (build_contract) に emit されるため
    docstring では再列挙しない。
    """
    sections = mart["sections"]

    def per_section(build) -> dict:
        return {"sections": {name: build(s) for name, s in sections.items()}}

    builders = {
        "00-meta.json": lambda: {
            "meta": mart["meta"],
            "contract": build_contract(),
            **per_section(lambda s: {k: s[k] for k in SPLIT_SECTION_SUMMARY_KEYS}),
        },
        "10-derived-views.json": lambda: per_section(lambda s: {
            "derived_views": s["derived_views"],
            "guard_reverse_lookup": s["guard_reverse_lookup"],
        }),
        "20-axis-a.json": lambda: per_section(lambda s: {
            "axis_a_pattern_matches": s["axis_a_pattern_matches"],
        }),
        "30-bypass-samples.json": lambda: per_section(lambda s: {
            "bypass_group_samples": build_bypass_group_samples(
                s["derived_views"]["bypass_grouped"], s["bypass_sequences"]),
        }),
        "90-mart.json": lambda: mart,
    }
    return [(f["name"], builders[f["name"]]()) for f in SPLIT_FILES]


# --- section 組み立て --------------------------------------------------------

def build_section(
    section_name: str,
    entries: list[PermissionEntry],
    events: list[ToolEvent],
    bypass_lookahead: int,
    bypass_max_gap: int,
) -> dict:
    axis_a = aggregate_axis_a(entries, events)
    axis_b = aggregate_axis_b(events, entries)
    bypass = extract_bypass_sequences(events, bypass_lookahead, bypass_max_gap)
    return {
        "name": section_name,
        "settings_sources": _summarize_settings_sources(entries),
        "event_count": len(events),
        "distinct_sessions": len({ev.session_id for ev in events}),
        "outcome_totals": dict(collections.Counter(ev.outcome for ev in events)),
        "axis_a_pattern_matches": axis_a,
        "axis_b_actual_usage": axis_b,
        "bypass_sequences": bypass,
        "guard_reverse_lookup": aggregate_guard_reverse(events),
        "derived_views": build_derived_views(axis_a, axis_b, bypass),
    }


def _summarize_settings_sources(entries: list[PermissionEntry]) -> list[dict]:
    grouped: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    for e in entries:
        grouped[(e.source_path, e.scope)][e.category] += 1
    out: list[dict] = []
    for (path, scope), c in grouped.items():
        out.append({
            "path": path,
            "scope": scope,
            "allow_count": c.get("allow", 0),
            "deny_count": c.get("deny", 0),
            "ask_count": c.get("ask", 0),
            "sandbox_excluded_commands_count": c.get("sandbox_excluded_commands", 0),
        })
    out.sort(key=lambda x: (x["scope"], x["path"]))
    return out


# --- entrypoint --------------------------------------------------------------

def _filter_events_for_project(events: list[ToolEvent], repo_root: str) -> list[ToolEvent]:
    """cwd == repo_root (子孫含む) の event のみ残す。project section 用。"""
    root = repo_root.rstrip("/")
    return [ev for ev in events if ev.cwd == root or ev.cwd.startswith(root + "/")]


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    now = resolve_now(ns.now)
    cutoff = now - dt.timedelta(days=ns.days)

    all_events: list[ToolEvent] = []
    for project_dir_name, jsonl in walk_transcripts(ns.transcripts_dir, cutoff):
        all_events.extend(extract_events(jsonl, cutoff, project_dir_name))

    sections_out: dict[str, dict] = {}

    if ns.section in ("project", "all"):
        project_entries: list[PermissionEntry] = []
        for src in enumerate_settings_sources("project", ns.repo_root, ns.global_settings):
            project_entries.extend(read_permission_entries(src["path"], src["scope"]))
        project_events = _filter_events_for_project(all_events, str(ns.repo_root.resolve()))
        sections_out["project"] = build_section(
            "project", project_entries, project_events,
            ns.bypass_lookahead, ns.bypass_max_gap_seconds,
        )

    if ns.section in ("global", "all"):
        global_entries: list[PermissionEntry] = []
        for src in enumerate_settings_sources("global", ns.repo_root, ns.global_settings):
            global_entries.extend(read_permission_entries(src["path"], src["scope"]))
        sections_out["global"] = build_section(
            "global", global_entries, all_events,
            ns.bypass_lookahead, ns.bypass_max_gap_seconds,
        )

    total_events = sum(s["event_count"] for s in sections_out.values())
    mart = {
        "meta": {
            "generated_at": now.isoformat(),
            "observation_window": {
                "start": cutoff.isoformat(),
                "end": now.isoformat(),
                "days": ns.days,
            },
            "section": ns.section,
            "repo_root": str(ns.repo_root.resolve()),
            "transcripts_dir": str(ns.transcripts_dir),
            "global_settings": str(ns.global_settings),
            "total_events": total_events,
            "sufficient_threshold": ns.sufficient_threshold,
            "sufficient_for_relative_judgment": total_events >= ns.sufficient_threshold,
            "notes": [
                "outcome の deny_user-rejected は #29499 の false positive バグ影響下 (best-effort な近似)。",
                "matcher の glob pattern は fnmatch による近似 (Claude Code 本体の matcher と揺れる余地あり)。",
                "guard_reverse_lookup は transcript の toolDenialKind + Reason label ベース (hooks.json の静的列挙はしない)。",
                "bucket 判定は本 script では行わない (責務境界: 判定は SKILL.md 手順の LLM 段階)。",
            ],
        },
        "sections": sections_out,
    }

    if ns.stdout_mart:
        json.dump(mart, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    ts = now.strftime("%Y%m%dT%H%M%SZ")
    run_dir = ns.output_dir / f"run-{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, doc in split_outputs(mart):
        path = run_dir / name
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        print(str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
