"""`scan_permissions` tool の実装 (inventory-permissions 向け data mart 生成)。

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
本 tool は bucket (revoke / promote / refine / sandbox / keep) を割り当てない。
bucket 判定は SKILL.md 手順の LLM 段階で mart を読んでから行う (循環依存の回避)。

出力: output_dir に run-<timestamp>/ を作り、LLM 段階が読む順の分割ファイル
(00-meta / 10-derived-views / 20-axis-a / 30-bypass-samples / 90-mart) を書いて
**path だけを返す** (mart 本体は context に載せない)。詳細は split_outputs の
docstring。Markdown レポートは LLM 段階の成果物で、本 tool は生成しない。

汎用スキル制約: 依存は Claude Code 標準ファイルのみ
(`~/.claude/projects/` / `~/.claude/settings.json` / `<repo>/.claude/settings.json` /
`<repo>/.claude/settings.local.json`)。swat-skills 固有 hook 資産
(tool-signatures.jsonl 等) には触らない。

`parse_args` / `main(argv)` を残してあるのは、mart schema を固定するテストが CLI
形の entrypoint を通して観測契約を検査しているため。tool 側の入口は `run()`。
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

from adapter.transcript import (
    PERMISSION_DENIAL_TOOL_RE,
    HookFiring,
    _iter_jsonl,
    classify_base_outcome,
    flatten_result_text,
    hook_firing_of,
    resolve_now,
    session_id_of,
    tool_use_result_of,
    truncate,
    walk_transcripts,
)

# --- 定数 --------------------------------------------------------------------

# 00-meta の contract に emit する schema 版 (単調増加 int、読み手側の将来分岐用)
SCHEMA_VERSION = 1

DEFAULT_DAYS = 30
DEFAULT_TRANSCRIPTS_DIR = Path("~/.claude/projects").expanduser()
DEFAULT_GLOBAL_SETTINGS = Path("~/.claude/settings.json").expanduser()
DEFAULT_CONFIG_DIR = Path("~/.claude").expanduser()
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
    {"name": "40-hooks.json", "order": 40, "standard_flow": True,
     "purpose": "hook の設定側分母 × fire 実績 (fire 0 / 遅い / timeout の観測。"
                "observability に観測限界を同梱)"},
    {"name": "90-mart.json", "order": 90, "standard_flow": False,
     "purpose": "mart 全量 (bypass_sequences / axis_b_actual_usage 含む、想定外の追加検査用)"},
)

# hook 観測の限界を mart に同梱する。**`nonzero_exit_count: 0` を「失敗していない」と
# 読ませないための注記**で、matcher_confidence / sufficient_for_relative_judgment と
# 同じ役割 (観測の確度を数値の隣に置く)。実測 (直近 60 transcript / hook_success
# 1,788 件) では exitCode は全件 0 で、失敗は別 type (`hook_cancelled`) にしか出ない。
HOOK_OBSERVABILITY = {
    "exit_code_source": "attachment.hook_success のみ (他 3 種は exitCode を持たない)",
    "duration_source": "attachment.hook_success / hook_cancelled の durationMs",
    "failure_confidence": "approx",
    "notes": [
        "nonzero_exit_count は「観測窓内に非 0 終了が記録されなかった」であって"
        "「hook が失敗していない」ではない。実測で hook_success の exitCode は全件 0",
        "timeout による打ち切りは hook_cancelled (timedOut) にしか出ない",
        "system.stop_hook_summary の hookInfos は {command, durationMs} だけで"
        "hookName も exitCode も持たないため、Stop hook の帰属には使えない",
        "fire_count 0 の判定が成立するのは configured (設定側の分母) に載る unit だけ。"
        "observed_unlisted は分母に無いので 0 件になり得ない",
        "key_collision: true の unit は fire_count を同 key の他 unit と共有する。"
        "「fire していない」は主張できるが「n 回動いた」は主張できない",
        "command を持たない attachment (hook_additional_context / hook_system_message) は"
        "hookName でしか引けず、matcher が `*` の設定には帰属しない (observed_unlisted に残る)",
    ],
}

# Bash command_head の抽出上限 (先頭 2 token を primary key に、fallback で先頭 1)
COMMAND_HEAD_MAX_TOKENS = 2
CONTENT_EXCERPT_LIMIT = 200

# `Tool(pattern)` 形式を解体
PERMISSION_ENTRY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*(?:\((.*)\))?\s*$")

# 自動モード分類器 deny の Reason 先頭ラベル `[Xxx Yyy]`
AUTOMODE_REASON_LABEL_RE = re.compile(r"Reason:\s*\[([^\]]+)\]")

# hook command 中の script file token (照合キーの抽出源)。
HOOK_SCRIPT_TOKEN_RE = re.compile(
    r"[\w./~${}@-]*[\w-]+\.(?:py|sh|bash|zsh|js|cjs|mjs|ts|rb|pl)\b"
)

# USER_REJECT_PATTERNS / PERMISSION_DENIAL_TOOL_RE は adapter.transcript に置く
# (scan_invocations と共有し、outcome 判定の優先順を 1 箇所で固定するため)。

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
    p.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR,
                   help="Claude Code config dir (plugin hooks の分母源)。"
                        "default ~/.claude")
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
# resolve_now / truncate は adapter.transcript にある (scan_invocations と共有)。

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
    tool_use_result: Any = None,
) -> tuple[str, str | None, str | None]:
    """tool_result から outcome (7 分類) を返す。

    Returns (outcome, denial_kind, denial_reason_label)。

    成否そのもの (success / error / user-reject / unknown) の判定は
    `adapter.transcript.classify_base_outcome` に委譲する (#476) — scan_invocations
    側の分類器と同じ record に同じ答えを出すため、判定の分岐をここで再導出しない。
    本関数が足すのは **base が選んだ枝の中での deny 種別の label 付け**だけ。

    - base success / unknown → そのまま (deny 種別は無い)
    - base user-reject → deny_user-rejected
    - base error → toolDenialKind → content 文言の順で deny_permission-rule /
      deny_automode へ細分し、当たらなければ error

    `is_error` 欠落時は `toolUseResult` (構造化真値) で判定する。両方無い record は
    unknown — 従来ここは success に丸めていたが、判定不能を成功に数えていた。

    outcome 語彙:
      success / deny_permission-rule / deny_user-rejected / deny_automode /
      deny_hook / error / unknown
    """
    base = classify_base_outcome(
        is_error, content, tool_use_result, tool_denial_kind
    )
    if base in ("success", "unknown"):
        return base, None, None
    if base == "user-reject":
        return "deny_user-rejected", "user-rejected", None

    text = flatten_result_text(content)

    if tool_denial_kind == "permission-rule":
        return "deny_permission-rule", "permission-rule", None
    if tool_denial_kind in ("automode-blocked", "automode-unavailable"):
        return "deny_automode", tool_denial_kind, _automode_reason_label(text)

    # toolDenialKind が無い場合は content 文言で fallback (permission-rule /
    # automode の 2 種のみ。user-rejected は base 側で判定済み)。hook-deny の
    # text fallback は git の pre-commit エラー等 (`hook failed with exit code 1`)
    # を高頻度で誤検知するため採らない — hook 由来は明示的 toolDenialKind に限定する。
    if PERMISSION_DENIAL_TOOL_RE.search(text):
        return "deny_permission-rule", "permission-rule", None
    if "denied by the claude code auto mode" in text.lower():
        return "deny_automode", "automode-blocked", _automode_reason_label(text)

    return "error", None, None


def _automode_reason_label(text: str) -> str | None:
    m = AUTOMODE_REASON_LABEL_RE.search(text)
    return m.group(1).strip() if m else None


# --- transcript walk ---------------------------------------------------------
# _iter_jsonl / walk_transcripts は adapter.transcript にある (scan_invocations と共有)。

def extract_events(
    jsonl_path: Path,
    cutoff: dt.datetime,
    project_dir: str,
    hook_sink: list[HookFiring] | None = None,
) -> list[ToolEvent]:
    """1 transcript file から tool_use event を抽出し、outcome を後続
    tool_result で補完する。

    生 tool_use 記録に紐づく tool_result が同 file 内に無い場合は outcome=unknown
    のまま残す。cutoff より古い assistant record は skip。

    `hook_sink` を渡すと hook の fire 実績 (`attachment.hook_*`) も同じ walk で
    追記する。**別関数に切って 2 周させない** — lake は実測 1.2 GB あり、hook 観測の
    ためだけに全走査をもう 1 周するコストが観測価値に見合わないため (#478)。
    """
    events: list[ToolEvent] = []
    pending: dict[str, int] = {}

    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as fp:
            for rec in _iter_jsonl(fp):
                rtype = rec.get("type")
                if hook_sink is not None and rtype == "attachment":
                    firing = hook_firing_of(rec)
                    if firing is not None and _hook_within_window(firing, cutoff):
                        hook_sink.append(firing)
                    continue
                if rtype == "user":
                    msg = rec.get("message") or {}
                    content = msg.get("content")
                    if isinstance(content, list):
                        result_blocks = [
                            b for b in content
                            if isinstance(b, dict) and b.get("type") == "tool_result"
                        ]
                        tur = tool_use_result_of(rec, result_blocks)
                        for block in result_blocks:
                            tid = block.get("tool_use_id")
                            if tid in pending:
                                idx = pending.pop(tid)
                                tdk = rec.get("toolDenialKind")
                                outcome, dk, label = classify_outcome(
                                    block.get("is_error"),
                                    block.get("content"),
                                    tdk if isinstance(tdk, str) else None,
                                    tur,
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
                sid = session_id_of(rec)
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


def _hook_within_window(firing: HookFiring, cutoff: dt.datetime) -> bool:
    """hook firing が観測窓内か。timestamp 欠損は保守的に窓内扱い (event 側と同じ)。"""
    ts = _parse_ts(firing.timestamp)
    return ts is None or ts >= cutoff


# --- hook の設定側分母 -------------------------------------------------------
# 「fire していない hook」は**設定側の分母**が要る。transcript には fire した hook
# しか現れないので、observed だけでは「0 回」を主張できない (#478 P4)。分母は
# settings の `hooks` と plugin の `hooks.json` の 2 系統から列挙する。


def _hook_command_key(command: str) -> str:
    """hook command の照合キー (最初に現れる script file の basename)。

    設定側は `"${CLAUDE_PLUGIN_ROOT}"/hooks/harness/guard-git.sh` のように変数を
    含み、観測側は展開済み絶対 path で現れる。**matcher の文字列一致では紐づかない**
    (設定の matcher が regex で、観測側は match した実 tool 名になるため) ので、
    照合は command 中の script 名で行う。

    「**最後の** script 名」を採る。実 hook には `export PATH=...; <runner>.js
    <entry>.js` のような長い shell 一行が実在し、先頭側は共通の runner なので
    先頭を採ると別 hook が同じ key に潰れて fire を二重計上する。末尾側は実際に
    起動される script に寄る (実測で claude-mem の 4 hook が正しく分かれる)。
    素の token 分割で末尾を採ると `:true}` のような shell 断片を掴むため、
    **script 拡張子を持つ token だけ**を候補にする。
    """
    tokens = HOOK_SCRIPT_TOKEN_RE.findall(command)
    if tokens:
        return tokens[-1].rsplit("/", 1)[-1]
    for token in command.replace('"', " ").replace("'", " ").split():
        if "=" in token or token.startswith("-"):
            continue
        return token.rsplit("/", 1)[-1]
    return ""


def hook_name_for(event: str, matcher: str) -> str:
    """設定の (event, matcher) から観測側の `hookName` 表記を組む。

    matcher が空 / `*` の hook は観測側でも event 名だけで現れる (`Stop` 等)。
    """
    if not matcher or matcher == "*":
        return event
    return f"{event}:{matcher}"


def read_hook_entries(settings_path: Path, scope: str, source_label: str) -> list[dict]:
    """settings / hooks.json の `hooks` から hook 設定を列挙する。

    出力の 1 単位は **(event, command) の組**で、同一 command に複数 matcher が
    紐づく場合は matchers に畳む — 単位を raw entry のままにすると、同じ script を
    複数 matcher で登録した hook (guard-shell.sh の bash / sh 等) に同じ fire を
    重複計上してしまうため。
    """
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    grouped: dict[tuple[str, str], dict] = {}
    for event, matcher_groups in (data.get("hooks") or {}).items():
        if not isinstance(matcher_groups, list):
            continue
        for group in matcher_groups:
            if not isinstance(group, dict):
                continue
            matcher = str(group.get("matcher") or "")
            for hook in group.get("hooks") or []:
                if not isinstance(hook, dict):
                    continue
                command = str(hook.get("command") or "")
                if not command:
                    continue
                key = (str(event), command)
                entry = grouped.setdefault(key, {
                    "hook_event": str(event),
                    "matchers": [],
                    "hook_names": [],
                    "command": truncate(command, CONTENT_EXCERPT_LIMIT),
                    "command_key": _hook_command_key(command),
                    "source_path": str(settings_path),
                    "source": source_label,
                    "scope": scope,
                })
                if matcher not in entry["matchers"]:
                    entry["matchers"].append(matcher)
                    entry["hook_names"].append(hook_name_for(str(event), matcher))
    return list(grouped.values())


def enumerate_hook_sources(repo_root: Path, global_settings: Path,
                           config_dir: Path) -> list[dict]:
    """hook 設定の source 一覧 (settings 3 種 + install 済み plugin の hooks.json)。

    plugin 側まで見るのは、ツール 1 の統治対象が「permission / sandbox / **guard
    hook**」で、その guard hook の実体が plugin 同梱 (`hooks/hooks.json`) だから。
    settings だけを分母にすると、統治対象の本体が丸ごと「未設定」に見える。
    """
    sources: list[dict] = [
        {"path": global_settings, "scope": "global", "source": "settings"},
        {"path": repo_root / ".claude" / "settings.json",
         "scope": "project", "source": "settings"},
        {"path": repo_root / ".claude" / "settings.local.json",
         "scope": "project-local", "source": "settings"},
    ]
    # skills ディレクトリプラグイン (symlink 配置) は installed_plugins.json に
    # 載らない。載らない側が本 repo の配布形なので、両方の install 形を列挙する。
    skills_root = config_dir / "skills"
    if skills_root.is_dir():
        for entry in sorted(skills_root.iterdir()):
            sources.append({
                "path": entry / "hooks" / "hooks.json",
                "scope": f"plugin:{entry.name}",
                "source": "plugin",
            })
    plugins_json = config_dir / "plugins" / "installed_plugins.json"
    try:
        data = json.loads(plugins_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    for pkey, entries in (data.get("plugins") or {}).items():
        if not isinstance(entries, list) or not entries:
            continue
        entry = entries[0] if isinstance(entries[0], dict) else {}
        install_path = entry.get("installPath")
        if not install_path:
            continue
        sources.append({
            "path": Path(str(install_path)) / "hooks" / "hooks.json",
            "scope": f"plugin:{str(pkey).split('@')[0]}",
            "source": "plugin",
        })
    # 同一実体を 2 経路で拾うと (symlink 配置 + installed_plugins) 同じ hook が
    # 2 unit に割れ、never_fired_units が水増しされる。実 path で重複を落とす。
    deduped: list[dict] = []
    seen: set[str] = set()
    for src in sources:
        if not src["path"].is_file():
            continue
        try:
            key = str(src["path"].resolve())
        except OSError:
            key = str(src["path"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(src)
    return deduped


def _percentile(values: list[int], ratio: float) -> int:
    """昇順 values の分位点 (最近傍)。空なら 0。"""
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))
    return ordered[idx]


def _firing_stats(firings: list[HookFiring]) -> dict:
    durations = [f.duration_ms for f in firings if f.duration_ms is not None]
    exit_codes: collections.Counter = collections.Counter(
        f.exit_code for f in firings if f.exit_code is not None
    )
    timestamps = sorted(f.timestamp for f in firings if f.timestamp)
    return {
        "fire_count": len(firings),
        "distinct_sessions": len({f.session_id for f in firings if f.session_id}),
        "exit_codes": {str(code): count for code, count in sorted(exit_codes.items())},
        "nonzero_exit_count": sum(c for code, c in exit_codes.items() if code != 0),
        "timed_out_count": sum(1 for f in firings if f.timed_out),
        "duration_ms": {
            "observed": len(durations),
            "p50": _percentile(durations, 0.5),
            "p95": _percentile(durations, 0.95),
            "max": max(durations) if durations else 0,
            "total": sum(durations),
        },
        "last_fired_at": timestamps[-1] if timestamps else "",
    }


def aggregate_hook_activity(configured: list[dict],
                            firings: list[HookFiring]) -> dict:
    """設定側の分母 × fire 実績。**bucket は付けない** (判定は LLM 段階)。

    紐づけは (event, command basename) を第一キーにする。command を持たない
    attachment (`hook_additional_context` / `hook_system_message`) は hookName で
    引き当てる (`matched_by` に手段を残す)。
    """
    by_command: dict[tuple[str, str], list[HookFiring]] = collections.defaultdict(list)
    by_name: dict[str, list[HookFiring]] = collections.defaultdict(list)
    for firing in firings:
        key = _hook_command_key(firing.command)
        if key:
            by_command[(firing.hook_event, key)].append(firing)
        else:
            by_name[firing.hook_name].append(firing)

    # 同一 (event, command_key) を複数の設定 unit が共有すると、同じ firing が
    # 両方の row に載る (実測: 単一 runner に別 entry を渡す形の plugin hook)。
    # 数を割り振る根拠が transcript に無いので、按分せず**共有である事実を出す**。
    key_owners: collections.Counter = collections.Counter(
        (e["hook_event"], e["command_key"]) for e in configured
    )
    used_command_keys: set[tuple[str, str]] = set()
    used_names: set[str] = set()
    rows: list[dict] = []
    for entry in configured:
        key = (entry["hook_event"], entry["command_key"])
        matched = list(by_command.get(key, []))
        matched_by = "command" if matched else None
        if matched:
            used_command_keys.add(key)
        for name in entry["hook_names"]:
            if name in by_name:
                matched.extend(by_name[name])
                used_names.add(name)
                matched_by = matched_by or "hook_name"
        rows.append({
            **{k: entry[k] for k in ("hook_event", "matchers", "hook_names",
                                     "command", "command_key", "source_path",
                                     "source", "scope")},
            "matched_by": matched_by,
            # True なら fire_count は同 key の他 unit と**共有**の値 (unit 単独の
            # 実績ではない)。「この hook は fire していない」の主張はできるが
            # 「この hook が n 回動いた」は主張できない
            "key_collision": key_owners[key] > 1,
            **_firing_stats(matched),
        })
    rows.sort(key=lambda r: (r["fire_count"], r["hook_event"], r["command_key"]))

    unlisted: dict[str, list[HookFiring]] = collections.defaultdict(list)
    for (event, key), group in by_command.items():
        if (event, key) not in used_command_keys:
            unlisted[f"{event}::{key}"].extend(group)
    for name, group in by_name.items():
        if name not in used_names:
            unlisted[f"{name}::"].extend(group)
    unlisted_rows = [
        {
            "hook_event": group[0].hook_event,
            "hook_name": group[0].hook_name,
            "command_key": _hook_command_key(group[0].command),
            **_firing_stats(group),
        }
        for group in unlisted.values()
    ]
    unlisted_rows.sort(key=lambda r: (-r["fire_count"], r["hook_name"]))

    return {
        "configured": rows,
        "observed_unlisted": unlisted_rows,
        "totals": {
            "configured_units": len(rows),
            "never_fired_units": sum(1 for r in rows if r["fire_count"] == 0),
            "key_collision_units": sum(1 for r in rows if r["key_collision"]),
            "total_firings": len(firings),
            "nonzero_exit_firings": sum(1 for f in firings
                                        if f.exit_code not in (None, 0)),
            "timed_out_firings": sum(1 for f in firings if f.timed_out),
        },
        "observability": HOOK_OBSERVABILITY,
    }


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


def _sandbox_excluded_commands(data: dict) -> list[str]:
    """`sandbox.excludedCommands` を top-level / `permissions.sandbox` の両方から集める。

    Claude Code の settings.json では sandbox 設定を top-level `sandbox` に書く形と
    `permissions.sandbox` に書く形の双方が観測される (本 repo の
    `settings/settings.local.json` は top-level)。片方しか読まないと excludedCommands が
    黙って 0 件になり、SKILL.md 手順 2 の「allow entry と対になる excludedCommands の
    連動削除確認」が無検出のまま素通りする。よって両方を読み、重複は初出順で除く。
    """
    seen: set[str] = set()
    out: list[str] = []
    perms = data.get("permissions")
    containers = [data.get("sandbox"),
                  perms.get("sandbox") if isinstance(perms, dict) else None]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for raw in (container.get("excludedCommands") or []):
            if isinstance(raw, str) and raw not in seen:
                seen.add(raw)
                out.append(raw)
    return out


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
    if not isinstance(data, dict):
        return []
    result: list[PermissionEntry] = []
    perms = data.get("permissions")
    if isinstance(perms, dict):
        for cat in ("allow", "deny", "ask"):
            for raw in (perms.get(cat) or []):
                if isinstance(raw, str):
                    result.append(parse_permission_entry(raw, cat, str(settings_path), scope))
    for raw in _sandbox_excluded_commands(data):
        # excludedCommands は `Tool(...)` 形ではなく素の Bash command pattern (`gh:*` /
        # `git push:*`)。`Bash(<raw>)` として解釈しないと tool 名一致で弾かれ、常に
        # match_count 0 になり zero-match view を汚す
        entry = parse_permission_entry(
            f"Bash({raw})", "sandbox_excluded_commands", str(settings_path), scope,
        )
        result.append(dataclasses.replace(entry, raw=raw))
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

# prefix マッチで「token が続いていない」と見なす境界文字。空白のほか shell の区切り
# (`;` `&` `|` `<` `>` `)`) を含める。素の str.startswith だと `git push --force:*` が
# `git push --force-with-lease ...` に、`comm:*` が `command rm ...` にマッチしてしまう
# (実測: 2026-07-28 の棚卸しで --force-with-lease 4 件が deny entry の match に混入)。
PREFIX_BOUNDARY_CHARS = frozenset(";&|<>)")


def _prefix_matches_command(prefix: str, command: str) -> bool:
    """`Bash(xxx:*)` の xxx が command の先頭 token 列として現れるか。

    Claude Code 本体の matcher は token 境界を見ており、prefix が単語の途中で切れる
    ケースはマッチしない。素の startswith 近似はそこで過剰マッチするため、prefix 直後が
    行末・空白・shell 区切りであることを追加条件にする。
    """
    if not prefix:
        return True
    if not command.startswith(prefix):
        return False
    rest = command[len(prefix):]
    if not rest:
        return True
    nxt = rest[0]
    return nxt.isspace() or nxt in PREFIX_BOUNDARY_CHARS


def entry_matches_event(entry: PermissionEntry, event: ToolEvent) -> bool:
    """conservative matcher。誤検知よりは取りこぼしを許す。

    - tool 名が一致しなければ即 False
    - `exact_tool` (括弧なし): 常に True (Bash などツール全体を許可/禁止する形)
    - `exact_command` (Bash): command が pattern と完全一致
    - `prefix` (Bash): command が `pattern[:-2]` で始まり、かつその直後が token 境界
      (行末 / 空白 / shell 区切り) — `_prefix_matches_command` 参照
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
            return _prefix_matches_command(entry.pattern[:-2], cmd)  # ":*" 除去
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
    """設定 entry 別に match_count / outcome_breakdown / sample_matched を組む。

    entry ごとに全 event を舐めると entries × events になる (実測 357 × 46,863 =
    1,673 万回の matcher 呼出)。tool 名一致は `entry_matches_event` の第 1 条件なので、
    先に tool で index して母集団を絞る。**照合結果は変わらない** — index は
    matcher の第 1 条件をそのまま前倒ししただけで、event の順序も保つ。
    """
    events_by_tool: dict[str, list[ToolEvent]] = collections.defaultdict(list)
    for ev in events:
        events_by_tool[ev.tool].append(ev)

    out: list[dict] = []
    for e in entries:
        matched = [ev for ev in events_by_tool.get(e.tool, ())
                   if entry_matches_event(e, ev)]
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
    """tool × command_head × outcome の集計。config_matches に対応 entry の raw を挙げる。

    代表 event (`raw_input` の復元源) は集計と**同じ 1 pass**で確保する。key ごとに
    events を線形探索すると keys × events になる (実測 2,875 × 46,863 = 1.35 億)。
    採るのは先頭 event で、探索していた頃と同じ 1 件。
    """
    key_counter: collections.Counter = collections.Counter()
    outcome_map: dict[tuple[str, str], collections.Counter] = {}
    representative: dict[tuple[str, str], ToolEvent] = {}
    for ev in events:
        key = (ev.tool, ev.command_head)
        key_counter[key] += 1
        outcome_map.setdefault(key, collections.Counter())[ev.outcome] += 1
        representative.setdefault(key, ev)
    out: list[dict] = []
    for (tool, head), count in key_counter.most_common():
        matches: list[str] = []
        rep = representative.get((tool, head))
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
        # hook は section (cwd scope) を持たない窓全体の観測なので per_section にしない
        "40-hooks.json": lambda: {"hook_activity": mart["hook_activity"]},
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

def _stamp(moment: dt.datetime) -> str:
    """mart に出す ISO timestamp (UTC は `Z` 表記)。"""
    return moment.isoformat().replace("+00:00", "Z")


def _filter_events_for_project(events: list[ToolEvent], repo_root: str) -> list[ToolEvent]:
    """cwd == repo_root (子孫含む) の event のみ残す。project section 用。"""
    root = repo_root.rstrip("/")
    return [ev for ev in events if ev.cwd == root or ev.cwd.startswith(root + "/")]


def build(ns: argparse.Namespace) -> dict:
    """mart 構築まで (ファイル I/O を伴わない)。"""
    now = resolve_now(ns.now)
    cutoff = now - dt.timedelta(days=ns.days)

    all_events: list[ToolEvent] = []
    hook_firings: list[HookFiring] = []
    for project_dir_name, jsonl in walk_transcripts(ns.transcripts_dir, cutoff):
        all_events.extend(
            extract_events(jsonl, cutoff, project_dir_name, hook_sink=hook_firings))

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

    hook_entries: list[dict] = []
    for src in enumerate_hook_sources(ns.repo_root, ns.global_settings, ns.config_dir):
        hook_entries.extend(
            read_hook_entries(src["path"], src["scope"], src["source"]))

    total_events = sum(s["event_count"] for s in sections_out.values())
    mart = {
        "meta": {
            # timestamp は 6 tool 共通で `Z` 表記に揃える (mart をまたいで
            # 突合する消費側が表記差を吸収しなくて済むように)
            "generated_at": _stamp(now),
            "observation_window": {
                "start": _stamp(cutoff),
                "end": _stamp(now),
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
                "hook_activity は section (cwd scope) で絞らない — 「30 日どこでも fire していない」"
                "が「fire していない hook」の主張になるため、分母を窓全体に取る。",
            ],
        },
        "sections": sections_out,
        "hook_activity": aggregate_hook_activity(hook_entries, hook_firings),
    }
    return mart


def emit(mart: dict, ns: argparse.Namespace) -> list[str]:
    """分割ファイルを run-<timestamp>/ に書き、読む順の path を返す。

    stamp は mart の `generated_at` から起こす — `resolve_now` を引き直すと
    mart 内の時刻と dir 名が秒境界でずれる。
    """
    generated = dt.datetime.fromisoformat(mart["meta"]["generated_at"])
    ts = generated.strftime("%Y%m%dT%H%M%SZ")
    run_dir = ns.output_dir / f"run-{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for name, doc in split_outputs(mart):
        path = run_dir / name
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        paths.append(str(path))
    return paths


def run(
    section: str = "project",
    days: int = DEFAULT_DAYS,
    repo_root: str | None = None,
    output_dir: str = str(DEFAULT_OUTPUT_DIR),
    transcripts_dir: str = str(DEFAULT_TRANSCRIPTS_DIR),
    global_settings: str = str(DEFAULT_GLOBAL_SETTINGS),
    config_dir: str = str(DEFAULT_CONFIG_DIR),
    now: str | None = None,
) -> dict:
    """tool 側の入口。mart は返さず、書いた path と判定可能性の meta だけを返す。"""
    ns = parse_args([])
    ns.section = section
    ns.days = days
    ns.repo_root = Path(repo_root) if repo_root else Path.cwd()
    ns.output_dir = Path(output_dir)
    ns.transcripts_dir = Path(transcripts_dir)
    ns.global_settings = Path(global_settings)
    ns.config_dir = Path(config_dir)
    ns.now = now

    mart = build(ns)
    meta = mart["meta"]
    return {
        "paths": emit(mart, ns),
        "read_order": [f["name"] for f in SPLIT_FILES],
        "meta": {
            "section": meta["section"],
            "repo_root": meta["repo_root"],
            "observation_window": meta["observation_window"],
            "total_events": meta["total_events"],
            "sufficient_for_relative_judgment": meta["sufficient_for_relative_judgment"],
            "event_count_by_section": {
                name: s["event_count"] for name, s in mart["sections"].items()
            },
            "hook_activity": mart["hook_activity"]["totals"],
        },
    }


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    mart = build(ns)
    if ns.stdout_mart:
        json.dump(mart, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    for path in emit(mart, ns):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
