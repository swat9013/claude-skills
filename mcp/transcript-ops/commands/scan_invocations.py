"""`scan_invocations` tool の実装 (inventory-skill-mcp 向け data mart 生成)。

transcript (~/.claude/projects/<cwd-hash>/<sid>.jsonl) を data lake として、直近
N 日 (--days、default 30) の tool_use を抽出し、単位別 (skill / agent / mcp_tool
+ mcp_server / plugin の roll-up) 分布 + 使用文脈抜粋 + 分母列挙を含む mart JSON
を出力する。

本 tool の責務は決定的な集計まで — **bucket 判定 (delete-candidate /
review-candidate / keep / insufficient-data 等) は行わない**。bucket 分類は SKILL.md
手順 2 の LLM 段階で mart を読んでから行う (循環依存の回避)。抜粋も bucket に
関係なく全 unit 一律で max 3 件 (session 重複排除・新しい順) 出す。

出力: output_dir に mart-<timestamp>.json を書き、**path だけを返す** (mart 本体は
context に載せない)。Markdown レポートは LLM 段階の成果物として並置される想定で、
本 tool は生成しない。

汎用スキル制約: 依存は Claude Code 標準ファイルのみ (installed_plugins.json /
plugin manifest / ~/.claude/skills/ / 現 repo .claude/skills/ / ~/.claude.json /
~/.mcp.json)。swat-skills 固有資産 (tool-signatures.jsonl 等) には触らない。

`parse_args` / `main(argv)` を残してあるのは、mart schema を固定するテストが CLI
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
    PRESENTED_ATTACHMENT_TYPES,
    PRESENTED_NAME_FIELDS,
    _iter_jsonl,
    attachment_of,
    classify_base_outcome,
    parse_slash_invocation,
    resolve_now,
    session_id_of,
    tool_use_result_of,
    truncate,
    usage_of,
    walk_transcripts,
)

# --- 定数 --------------------------------------------------------------------

DEFAULT_DAYS = 30
DEFAULT_TRANSCRIPTS_DIR = Path("~/.claude/projects").expanduser()
DEFAULT_CONFIG_DIR = Path("~/.claude").expanduser()
DEFAULT_CLAUDE_JSON = Path("~/.claude.json").expanduser()
DEFAULT_OUTPUT_DIR = Path("/tmp/inventory-skill-mcp")

# 相対判定の閾値。総 invocation 数がこれ未満なら「相対判定不能」を mart header に立てる
DEFAULT_SUFFICIENT_THRESHOLD = 30

EXCERPT_MAX_PER_UNIT = 3
EXCERPT_TEXT_LIMIT = 200

# mcp__<server>__<tool> の分解 (server / tool 名内の `_` を許容)
MCP_NAME_RE = re.compile(r"^mcp__(.+?)__(.+)$")

# slash 呼出しの判定 (parse_slash_invocation) は adapter.transcript にある
# (「slash 呼出しが record にどう現れるか」は形式知識。find_invocations と共有する)。

# skill load の channel 語彙。mart output の JSON key として直接露出する
# (LLM が読む). 定数化で production の複数箇所で表記ゆれが起きないよう保証する。
CHANNEL_SKILL_TOOL = "skill_tool"
CHANNEL_COMMAND = "command"
CHANNEL_READ = "read"
SKILL_LOAD_CHANNELS = (CHANNEL_SKILL_TOOL, CHANNEL_COMMAND, CHANNEL_READ)

# session attribute 判定用の tool 名集合。
# CODE_EDIT_TOOLS は coverage 分母 (「コード編集 session」) の判定源。
# PLAN_MODE_TOOLS は design session の判定源 (拡張 3 の材料。有無だけ mart に出す)。
CODE_EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
PLAN_MODE_TOOLS = frozenset({"EnterPlanMode"})

# attachment type → 提示分母の unit 型。**分母の unit 型は units の unit 型と
# 揃える** (skill / mcp_server / agent) — 揃えないと分子と分母を join できない。
# `deferred_tool` だけは units 側に対応が無く、`mcp_tool` の一部が deferred として
# 提示される関係なので独立させる。
#
# どの field に名前が載るかは形式の事実なので adapter の `PRESENTED_NAME_FIELDS` が
# 持つ。ここに写すのは「その名前を何の分母として読むか」だけ。
PRESENTED_UNIT_TYPE: dict[str, str] = {
    "skill_listing": "skill",
    "mcp_instructions_delta": "mcp_server",
    "agent_listing_delta": "agent",
    "deferred_tools_delta": "deferred_tool",
}

# adapter が読める type と本 tool が分母に写せる type がずれると、gate を通った
# attachment を黙って捨てる (提示分母だけが欠ける) 経路ができる。実行時の握り潰しに
# 倒さず import 時に落とす。
if set(PRESENTED_UNIT_TYPE) != set(PRESENTED_ATTACHMENT_TYPES):
    raise RuntimeError(
        "PRESENTED_UNIT_TYPE と adapter の PRESENTED_ATTACHMENT_TYPES が不一致: "
        f"{sorted(set(PRESENTED_UNIT_TYPE) ^ set(PRESENTED_ATTACHMENT_TYPES))}"
    )
PRESENTED_UNIT_TYPES = ("skill", "mcp_server", "agent", "deferred_tool")

# USER_REJECT_PATTERNS は adapter.transcript にある (scan_permissions と共有)。


# --- データ ------------------------------------------------------------------

@dataclasses.dataclass
class ContextObservation:
    """session に**提示された分母**と token 経済の accumulator (#478 P2 / P5)。

    invocation (分子) と同じ walk で集める。分子だけでは「使われていない skill」が
    「そもそも提示されていない skill」と区別できず、逆に「呼ばれているが重い」は
    invocation 数からは出ない。

    presented は **(unit_type, id) → 提示された session 集合**。attachment は
    delta 形式 (`addedNames` / `removedNames`) で来るので、除去は追跡しない —
    「一度でも提示された session」が分母の定義。
    """

    presented: dict[tuple[str, str], set[str]] = dataclasses.field(
        default_factory=lambda: collections.defaultdict(set))
    tool_use_sessions: dict[str, set[str]] = dataclasses.field(
        default_factory=lambda: collections.defaultdict(set))
    presented_span: dict[tuple[str, str], list[str]] = dataclasses.field(
        default_factory=dict)
    listing_sessions: set[str] = dataclasses.field(default_factory=set)
    usage_by_skill: dict[str, dict[str, int]] = dataclasses.field(
        default_factory=dict)
    usage_totals: dict[str, int] = dataclasses.field(default_factory=dict)
    assistant_turns: int = 0
    attributed_turns: int = 0

    def note_presented(self, unit_type: str, unit_id: str,
                       session_id: str, timestamp: str) -> None:
        # session id を持たない record は分母に数えない (空文字を 1 session として
        # 数えると、`sessions_presented` が実 session 数より 1 多く出る)
        if not session_id:
            return
        key = (unit_type, unit_id)
        self.presented[key].add(session_id)
        span = self.presented_span.get(key)
        if span is None:
            self.presented_span[key] = [timestamp, timestamp]
        else:
            span[0] = min(span[0], timestamp) if span[0] else timestamp
            span[1] = max(span[1], timestamp) if span[1] else timestamp

    def note_tool_use(self, tool_name: str, session_id: str) -> None:
        """全 tool_use の tool 名 × session。deferred tool の分子はここからしか出ない
        (`units` は skill / agent / mcp のみを数えるので `WebFetch` 等が落ちる)。"""
        if tool_name:
            self.tool_use_sessions[tool_name].add(session_id)

    def note_usage(self, skill: str | None, usage: dict[str, int]) -> None:
        self.assistant_turns += 1
        for key, value in usage.items():
            self.usage_totals[key] = self.usage_totals.get(key, 0) + value
        if not skill:
            return
        self.attributed_turns += 1
        bucket = self.usage_by_skill.setdefault(
            skill, {"assistant_turns": 0, **{k: 0 for k in usage}})
        bucket["assistant_turns"] += 1
        for key, value in usage.items():
            bucket[key] = bucket.get(key, 0) + value


@dataclasses.dataclass
class Invocation:
    unit_type: str  # "skill" | "agent" | "mcp_tool"
    unit_id: str
    session_id: str
    timestamp: str
    project_dir: str
    user_prompt: str
    tool_input: str
    outcome: str  # "success" | "error" | "user-reject" | "unknown"
    attribution_skill: str | None
    channel: str = CHANNEL_SKILL_TOOL  # skill unit のみ意味を持つ (agent/mcp は "skill_tool" 固定)


# --- CLI ---------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=DEFAULT_DAYS,
                   help="観測窓 (日)。default 30")
    p.add_argument("--transcripts-dir", type=Path, default=DEFAULT_TRANSCRIPTS_DIR,
                   help="transcript の data lake。default ~/.claude/projects")
    p.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR,
                   help="Claude Code config dir。default ~/.claude")
    p.add_argument("--claude-json", type=Path, default=DEFAULT_CLAUDE_JSON,
                   help="~/.claude.json path (MCP server 分母源)")
    p.add_argument("--repo-root", type=Path, default=Path.cwd(),
                   help="現 project root。.claude/skills や .mcp.json の起点。"
                        "省略時は cwd")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help="mart 出力先。default /tmp/inventory-skill-mcp")
    p.add_argument("--now", type=str, default=None,
                   help="ISO timestamp。窓の起点を固定 (テスト・再現用)")
    p.add_argument("--sufficient-threshold", type=int,
                   default=DEFAULT_SUFFICIENT_THRESHOLD,
                   help="相対判定可能とみなす総 invocation 閾値。default 30")
    p.add_argument("--stdout-mart", action="store_true",
                   help="ファイルに書かず mart JSON を stdout に出す (テスト用)")
    return p.parse_args(argv)


# --- 共通ユーティリティ ------------------------------------------------------
# resolve_now / truncate は adapter.transcript にある (scan_permissions と共有)。

def parse_mcp_name(name: str) -> tuple[str, str] | None:
    m = MCP_NAME_RE.match(name)
    if not m:
        return None
    return m.group(1), m.group(2)


def resolve_slash_to_skill_id(
    slash_name: str,
    known_skill_ids: set[str],
) -> str | None:
    """slash command 名 (leading slash 抜き) を skill id にマップする。

    - `plugin:skill` 形式 (`:` 含む) は plugin skill 呼出しと確定。skill id は
      そのまま (denominators.skills に無ければ session-observed 補完される)
    - namespace 無しは enumerated skill と一致するときだけ命中する。
      built-in slash (`/model` `/compact` `/clear` `/loop` …) は分母に無いので
      自動で drop される
    """
    if ":" in slash_name:
        return slash_name
    if slash_name in known_skill_ids:
        return slash_name
    return None


def build_skill_resolver(enumerated_skills: list[dict]) -> dict[str, Any]:
    """extract 段階で使う skill id 解決テーブルを組む。

    - `known_skill_ids`: 分母 (`enumerate_skills` 結果) の id set。
      unnamespaced slash と Read tool_use の両方の filter に使う
    - `skill_md_paths`: SKILL.md 絶対 path → skill id。Read tool_use の
      `file_path` string 一致で load 判定する
    """
    known_ids: set[str] = set()
    md_paths: dict[str, str] = {}
    for s in enumerated_skills:
        sid = s.get("id")
        if isinstance(sid, str):
            known_ids.add(sid)
        install_path = s.get("install_path")
        if isinstance(install_path, str) and install_path and isinstance(sid, str):
            md_paths[str(Path(install_path) / "SKILL.md")] = sid
    return {"known_skill_ids": known_ids, "skill_md_paths": md_paths}


def classify_outcome(
    is_error: Any,
    content: Any,
    tool_use_result: Any = None,
    tool_denial_kind: str | None = None,
) -> str:
    """tool 実行の outcome ("success" | "error" | "user-reject" | "unknown")。

    判定は `adapter.transcript.classify_base_outcome` に一本化してある (#476) —
    scan_permissions 側の分類器と同じ record に同じ答えを出すため、ここに
    ロジックのコピーを置かない。本 mart の語彙は base 語彙と同一なのでそのまま返す。
    """
    return classify_base_outcome(is_error, content, tool_use_result, tool_denial_kind)


# --- transcript walk ---------------------------------------------------------
# _iter_jsonl / walk_transcripts は adapter.transcript にある (scan_permissions と共有)。

def _extract_user_text(content: Any) -> str:
    """user turn の message.content から表示可能な text を取り出す。

    tool_result / system tag 混入 turn は空文字を返す (プロンプト扱いしない)。
    """
    if isinstance(content, str):
        if content.startswith("<"):
            return ""
        return content
    if isinstance(content, list):
        parts = []
        has_tool_result = False
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_result":
                has_tool_result = True
                continue
            if btype == "text":
                txt = block.get("text", "")
                if isinstance(txt, str) and not txt.startswith("<"):
                    parts.append(txt)
        if has_tool_result and not parts:
            return ""
        return " ".join(parts)
    return ""


def _note_presented_attachment(rec: dict, context: ContextObservation) -> None:
    """attachment 1 件から「この session に提示された分母」を拾う。

    - `skill_listing`: 全量 (`names`)。session 冒頭に 1 度出る
    - `deferred_tools_delta` / `mcp_instructions_delta` / `agent_listing_delta`:
      差分の `added*`。除去 (`removedNames`) は追わない — 分母の定義が
      「一度でも提示された」だから
    """
    body = attachment_of(rec)
    if body is None or body.get("type") not in PRESENTED_ATTACHMENT_TYPES:
        return
    session_id = session_id_of(rec)
    timestamp = str(rec.get("timestamp") or "")
    atype = body.get("type")
    if atype == "skill_listing" and session_id:
        context.listing_sessions.add(session_id)
    unit_type = PRESENTED_UNIT_TYPE[str(atype)]
    for field in PRESENTED_NAME_FIELDS[str(atype)]:
        for name in body.get(field) or []:
            if isinstance(name, str) and name:
                context.note_presented(unit_type, name, session_id, timestamp)


def extract_invocations(
    jsonl_path: Path,
    cutoff: dt.datetime,
    project_dir: str,
    resolver: dict[str, Any] | None = None,
    context: ContextObservation | None = None,
) -> tuple[list[Invocation], dict[str, dict]]:
    """1 transcript file から Skill / Agent / mcp__* の tool_use を抽出する。

    - Skill tool_use は channel=skill_tool、`<command-name>/xxx</command-name>` 含む
      user turn は channel=command、SKILL.md path への Read tool_use は channel=read
      として skill unit を計上する
    - agent / mcp_tool は tool_use のみ (channel は無関係)
    - outcome は同 file 内の後続 tool_result (matching tool_use_id) から補完
    - user_prompt は最直前 user turn の text 先頭 EXCERPT_TEXT_LIMIT 文字
    - cutoff より古い record は個別に skip (mtime だけでは filter しきれない
      長寿命 session のため)
    - 併せて session attribute (has_code_edit / has_plan_mode) を集める
      (coverage 分母の材料)
    """
    known_skill_ids: set[str] = (resolver or {}).get("known_skill_ids", set())
    skill_md_paths: dict[str, str] = (resolver or {}).get("skill_md_paths", {})

    invocations: list[Invocation] = []
    pending: dict[str, int] = {}
    session_attrs: dict[str, dict] = {}
    last_user_prompt = ""

    def _within_window(ts_str: str) -> bool:
        try:
            rec_ts = dt.datetime.fromisoformat(
                ts_str.replace("Z", "+00:00")
            ).astimezone(dt.timezone.utc)
        except (ValueError, AttributeError):
            return True  # timestamp 欠損は保守的に窓内扱い (既存挙動と揃える)
        return rec_ts >= cutoff

    def _mark(sid: str, key: str) -> None:
        session_attrs.setdefault(sid, {"has_code_edit": False,
                                       "has_plan_mode": False})[key] = True

    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as fp:
            for rec in _iter_jsonl(fp):
                rtype = rec.get("type")
                if context is not None and rtype == "attachment":
                    if _within_window(str(rec.get("timestamp") or "")):
                        _note_presented_attachment(rec, context)
                    continue
                if rtype == "user":
                    msg = rec.get("message") or {}
                    content = msg.get("content")
                    text = _extract_user_text(content)
                    if text:
                        last_user_prompt = text
                    # slash command による skill load。判定は adapter に一本化して
                    # あり、引用マーカー / wrapper 内包はここへ来ない
                    slash = parse_slash_invocation(content)
                    if slash:
                        ts_str = rec.get("timestamp") or ""
                        sid = session_id_of(rec)
                        if _within_window(ts_str):
                            skill_id = resolve_slash_to_skill_id(slash, known_skill_ids)
                            if skill_id is not None:
                                invocations.append(Invocation(
                                    unit_type="skill",
                                    unit_id=skill_id,
                                    session_id=str(sid),
                                    timestamp=ts_str,
                                    project_dir=project_dir,
                                    user_prompt=truncate(f"/{slash}", EXCERPT_TEXT_LIMIT),
                                    tool_input="",
                                    outcome="success",
                                    attribution_skill=None,
                                    channel=CHANNEL_COMMAND,
                                ))
                    if isinstance(content, list):
                        result_blocks = [
                            b for b in content
                            if isinstance(b, dict) and b.get("type") == "tool_result"
                        ]
                        tur = tool_use_result_of(rec, result_blocks)
                        tdk = rec.get("toolDenialKind")
                        for block in result_blocks:
                            tid = block.get("tool_use_id")
                            if tid in pending:
                                idx = pending.pop(tid)
                                invocations[idx].outcome = classify_outcome(
                                    block.get("is_error"),
                                    block.get("content"),
                                    tur,
                                    tdk if isinstance(tdk, str) else None,
                                )
                    continue
                if rtype != "assistant":
                    continue
                ts_str = rec.get("timestamp") or ""
                if not _within_window(ts_str):
                    continue
                msg = rec.get("message") or {}
                content = msg.get("content")
                sid = session_id_of(rec)
                attribution = rec.get("attributionSkill")
                if context is not None:
                    usage = usage_of(rec)
                    if usage is not None:
                        # tool_use を持たない turn (思考・応答のみ) も費用は発生する。
                        # 「呼ばれているが重い skill」は turn 単位でしか見えない
                        context.note_usage(
                            attribution if isinstance(attribution, str) else None,
                            usage,
                        )
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_use":
                        continue
                    name = block.get("name", "") or ""
                    if context is not None:
                        context.note_tool_use(name, str(sid))
                    tid = block.get("id", "") or ""
                    blk_input = block.get("input") or {}
                    if not isinstance(blk_input, dict):
                        blk_input = {}

                    # session attribute (invocation の可否と独立に集める)
                    if name in CODE_EDIT_TOOLS:
                        _mark(str(sid), "has_code_edit")
                    if name in PLAN_MODE_TOOLS:
                        _mark(str(sid), "has_plan_mode")

                    unit_type: str | None = None
                    unit_id: str | None = None
                    channel: str = CHANNEL_SKILL_TOOL
                    if name == "Skill":
                        skill_name = blk_input.get("skill", "")
                        if isinstance(skill_name, str) and skill_name.strip():
                            unit_type = "skill"
                            unit_id = skill_name.strip()
                    elif name == "Agent":
                        atype = blk_input.get("subagent_type", "")
                        if isinstance(atype, str) and atype.strip():
                            unit_type = "agent"
                            unit_id = atype.strip()
                    elif name.startswith("mcp__"):
                        unit_type = "mcp_tool"
                        unit_id = name
                    elif name == "Read":
                        fp_val = blk_input.get("file_path", "")
                        if isinstance(fp_val, str) and fp_val in skill_md_paths:
                            unit_type = "skill"
                            unit_id = skill_md_paths[fp_val]
                            channel = CHANNEL_READ
                    if unit_type is None or unit_id is None:
                        continue
                    inv = Invocation(
                        unit_type=unit_type,
                        unit_id=unit_id,
                        session_id=str(sid),
                        timestamp=ts_str,
                        project_dir=project_dir,
                        user_prompt=truncate(last_user_prompt, EXCERPT_TEXT_LIMIT),
                        tool_input=truncate(
                            json.dumps(blk_input, ensure_ascii=False),
                            EXCERPT_TEXT_LIMIT,
                        ),
                        outcome="unknown",
                        attribution_skill=attribution if isinstance(attribution, str) else None,
                        channel=channel,
                    )
                    invocations.append(inv)
                    if tid:
                        pending[tid] = len(invocations) - 1
    except OSError:
        return [], {}
    return invocations, session_attrs


# --- 分母列挙 ----------------------------------------------------------------

def enumerate_installed_plugins(config_dir: Path) -> dict[str, dict]:
    """installed_plugins.json から plugin 一覧 (name → metadata) を返す。

    plugin key は "<name>@<marketplace>" 形式なので `@` 前を name として扱う。
    複数 install がある場合は先頭 entry を採る (実運用では 1 件想定)。
    """
    ipath = config_dir / "plugins" / "installed_plugins.json"
    if not ipath.is_file():
        return {}
    try:
        data = json.loads(ipath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    plugins: dict[str, dict] = {}
    for pkey, entries in (data.get("plugins") or {}).items():
        if not isinstance(entries, list) or not entries:
            continue
        entry = entries[0] if isinstance(entries[0], dict) else {}
        name = pkey.split("@")[0] if isinstance(pkey, str) else str(pkey)
        plugins[name] = {
            "id": name,
            "key": pkey,
            "install_path": entry.get("installPath"),
            "version": entry.get("version"),
        }
    return plugins


def _scan_plugin_skills(install_path: Path) -> set[str]:
    """plugin dir から skill 名の集合を返す。plugin.json.skills[] を優先し、
    無ければ skills/**/SKILL.md を走査する。
    """
    names: set[str] = set()
    plugin_json = install_path / ".claude-plugin" / "plugin.json"
    if plugin_json.is_file():
        try:
            pdata = json.loads(plugin_json.read_text(encoding="utf-8"))
            for entry in pdata.get("skills") or []:
                if not isinstance(entry, str):
                    continue
                rel = entry.removeprefix("./").rstrip("/")
                nm = Path(rel).name
                if nm:
                    names.add(nm)
        except (OSError, json.JSONDecodeError):
            pass
    skills_root = install_path / "skills"
    if skills_root.is_dir():
        for skill_md in skills_root.rglob("SKILL.md"):
            names.add(skill_md.parent.name)
    return names


def enumerate_skills(
    config_dir: Path,
    repo_root: Path,
) -> list[dict]:
    """全 source から skill id 一覧を返す。id の付け方:

    - plugin skill: "<plugin>:<skill>" (Claude Code の Skill invocation 表現に一致)
    - personal (~/.claude/skills/*): "<skill>"
    - current repo (.claude/skills/*): "project:<skill>"
    """
    skills: list[dict] = []
    for plugin_id, meta in enumerate_installed_plugins(config_dir).items():
        ipath = meta.get("install_path")
        if not ipath:
            continue
        pdir = Path(str(ipath))
        for skill_name in _scan_plugin_skills(pdir):
            skills.append({
                "id": f"{plugin_id}:{skill_name}",
                "source": "config",
                "plugin": plugin_id,
                "install_path": str(pdir),
            })
    personal = config_dir / "skills"
    if personal.is_dir():
        for skill_md in sorted(personal.glob("*/SKILL.md")):
            skills.append({
                "id": skill_md.parent.name,
                "source": "config",
                "plugin": None,
                "install_path": str(skill_md.parent),
            })
    project_skills = repo_root / ".claude" / "skills"
    if project_skills.is_dir():
        for skill_md in sorted(project_skills.glob("*/SKILL.md")):
            skills.append({
                "id": f"project:{skill_md.parent.name}",
                "source": "config",
                "plugin": None,
                "install_path": str(skill_md.parent),
            })
    # dedupe (id 一致は先勝ち)
    seen = set()
    deduped = []
    for s in skills:
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        deduped.append(s)
    return deduped


def enumerate_mcp_servers(claude_json: Path, repo_root: Path) -> list[dict]:
    """~/.claude.json の mcpServers + project entry + <repo>/.mcp.json から一覧。

    dedupe key は (id, scope)。claude.ai connectors はローカル config には出現
    しないため、LLM 段階で session-observed として補完される (mart 側で自動 tag)。
    """
    servers: list[dict] = []
    if claude_json.is_file():
        try:
            data = json.loads(claude_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for name in (data.get("mcpServers") or {}).keys():
            servers.append({"id": name, "source": "config", "scope": "global"})
        projects = data.get("projects") or {}
        # project key は絶対 path (~/.claude.json の格納形式)
        for key in (str(repo_root), str(repo_root.resolve())):
            entry = projects.get(key)
            if not isinstance(entry, dict):
                continue
            for name in (entry.get("mcpServers") or {}).keys():
                servers.append({"id": name, "source": "config", "scope": "project-entry"})
            for name in entry.get("enabledMcpjsonServers") or []:
                servers.append({"id": name, "source": "config", "scope": "project-enabled"})
    mcp_json = repo_root / ".mcp.json"
    if mcp_json.is_file():
        try:
            mdata = json.loads(mcp_json.read_text(encoding="utf-8"))
            for name in (mdata.get("mcpServers") or {}).keys():
                servers.append({"id": name, "source": "config", "scope": "project-mcp-json"})
        except (OSError, json.JSONDecodeError):
            pass
    seen = set()
    deduped = []
    for s in servers:
        key = (s["id"], s["scope"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    return deduped


# --- 集計 --------------------------------------------------------------------

def _percentile_of(agg: list[dict], count: int) -> float:
    if not agg:
        return 0.0
    smaller = sum(1 for other in agg if other["count"] < count)
    return smaller / len(agg)


def _sample_excerpts(invs: list[Invocation]) -> list[dict]:
    """session 重複排除 (1 session 1 件、新しい順) で max EXCERPT_MAX_PER_UNIT。"""
    seen_sessions: set[str] = set()
    excerpts: list[dict] = []
    for inv in sorted(invs, key=lambda x: x.timestamp, reverse=True):
        if inv.session_id in seen_sessions:
            continue
        seen_sessions.add(inv.session_id)
        excerpts.append({
            "session_id": inv.session_id,
            "timestamp": inv.timestamp,
            "project_dir": inv.project_dir,
            "user_prompt": inv.user_prompt,
            "tool_input": inv.tool_input,
            "outcome": inv.outcome,
            "attribution_skill": inv.attribution_skill,
        })
        if len(excerpts) >= EXCERPT_MAX_PER_UNIT:
            break
    return excerpts


def _channels_breakdown(invs: list[Invocation]) -> dict[str, int]:
    """skill unit の invocations を channel 別に集計する。
    未観測 channel も 0 で埋めて schema を安定化する (LLM が missing key を扱わなくて済む)。
    """
    counts = {ch: 0 for ch in SKILL_LOAD_CHANNELS}
    for inv in invs:
        if inv.channel in counts:
            counts[inv.channel] += 1
    return counts


def aggregate_units(invocations: list[Invocation]) -> dict[str, list[dict]]:
    """unit_type ごとに count / share / rank / percentile / excerpts を計算し、
    mcp_server と plugin の roll-up も行う。skill unit は channels 内訳も含む。
    """
    grouped: dict[tuple[str, str], list[Invocation]] = collections.defaultdict(list)
    outcomes_grouped: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    for inv in invocations:
        key = (inv.unit_type, inv.unit_id)
        grouped[key].append(inv)
        outcomes_grouped[key][inv.outcome] += 1

    total = len(invocations)
    result: dict[str, list[dict]] = {
        "skill": [], "agent": [], "mcp_tool": [], "mcp_server": [], "plugin": [],
    }
    for utype in ("skill", "agent", "mcp_tool"):
        agg = []
        for (t, uid), invs in grouped.items():
            if t != utype:
                continue
            entry = {
                "id": uid,
                "count": len(invs),
                "share": (len(invs) / total) if total else 0.0,
                "outcomes": dict(outcomes_grouped[(t, uid)]),
                "excerpts": _sample_excerpts(invs),
            }
            if utype == "skill":
                entry["channels"] = _channels_breakdown(invs)
            agg.append(entry)
        agg.sort(key=lambda x: (-x["count"], x["id"]))
        for i, u in enumerate(agg):
            u["rank"] = i + 1
            u["percentile"] = _percentile_of(agg, u["count"])
        result[utype] = agg

    # mcp_server roll-up
    servers: dict[str, dict] = {}
    for tool in result["mcp_tool"]:
        parsed = parse_mcp_name(tool["id"])
        if not parsed:
            continue
        server_name, tool_name = parsed
        s = servers.setdefault(server_name, {
            "id": server_name, "count": 0, "tools": [],
            "outcomes": collections.Counter(),
        })
        s["count"] += tool["count"]
        s["tools"].append({"tool": tool_name, "count": tool["count"]})
        s["outcomes"].update(tool["outcomes"])
    servers_list = []
    for s in servers.values():
        servers_list.append({
            "id": s["id"],
            "count": s["count"],
            "share": s["count"] / total if total else 0.0,
            "tools": sorted(s["tools"], key=lambda x: (-x["count"], x["tool"])),
            "outcomes": dict(s["outcomes"]),
        })
    servers_list.sort(key=lambda x: (-x["count"], x["id"]))
    for i, s in enumerate(servers_list):
        s["rank"] = i + 1
        s["percentile"] = _percentile_of(servers_list, s["count"])
    result["mcp_server"] = servers_list

    # plugin roll-up: skill id "plugin:xxx" を plugin ごとに合算
    plugin_agg: dict[str, dict] = {}
    for skill in result["skill"]:
        sid = skill["id"]
        if ":" not in sid:
            continue
        pname = sid.split(":", 1)[0]
        if pname == "project":  # current repo project skills は plugin ではない
            continue
        p = plugin_agg.setdefault(pname, {
            "id": pname, "count": 0, "skill_ids": [],
        })
        p["count"] += skill["count"]
        p["skill_ids"].append(sid)
    plugins_list = []
    for p in plugin_agg.values():
        plugins_list.append({
            "id": p["id"],
            "count": p["count"],
            "share": p["count"] / total if total else 0.0,
            "skill_ids": sorted(p["skill_ids"]),
        })
    plugins_list.sort(key=lambda x: (-x["count"], x["id"]))
    for i, p in enumerate(plugins_list):
        p["rank"] = i + 1
        p["percentile"] = _percentile_of(plugins_list, p["count"])
    result["plugin"] = plugins_list
    return result


# --- mart 生成 ---------------------------------------------------------------

def build_sessions_section(
    invocations: list[Invocation],
    session_attrs: dict[str, dict],
) -> list[dict]:
    """session 単位で loaded_skills / has_code_edit / has_plan_mode を出す。

    - skill invocation を 1 度以上持つ session だけを載せる。載るのは「skill load が
      **発生した** session」であって「発生し得た session」ではない — coverage の
      分母をどちらに取るかは、has_code_edit / has_plan_mode を材料に LLM 段階が決める
    - 出力は session_id 昇順で決定的にする
    """
    per_session: dict[str, set[str]] = collections.defaultdict(set)
    for inv in invocations:
        if inv.unit_type != "skill":
            continue
        per_session[inv.session_id].add(inv.unit_id)
    out: list[dict] = []
    for sid in sorted(per_session):
        attrs = session_attrs.get(sid) or {}
        out.append({
            "session_id": sid,
            "loaded_skills": sorted(per_session[sid]),
            "has_code_edit": bool(attrs.get("has_code_edit", False)),
            "has_plan_mode": bool(attrs.get("has_plan_mode", False)),
        })
    return out


def normalize_mcp_server(name: str) -> str:
    """MCP server 名の表記差を吸収した join キー。

    提示側 (`mcp_instructions_delta`) は `plugin:swat-skills:dispatch-ops` /
    `claude.ai Slack`、呼出側 (`mcp__<server>__<tool>`) は
    `plugin_swat-skills_dispatch-ops` / `claude_ai_Slack` と表記が違う。**揃えないと
    毎日呼ばれている server が「提示されたが 0 呼出」に見える** (実測で判明)。
    `-` は両側で保たれるので潰さない。
    """
    return name.replace(":", "_").replace(".", "_").replace(" ", "_")


def build_presented_section(context: ContextObservation,
                            invocations: list[Invocation]) -> dict:
    """「session で実際に提示された分母」× 呼出しの有無。

    install 済み一覧 (`denominators`) との違いは**その session に本当に載っていたか**。
    install されていても listing に出ない (session 側の絞り込み・plugin 無効化) 分は
    分母から落ちるため、「提示されているのに一度も呼ばれない」という主張がここで
    はじめて成立する。

    `invoked_sessions` は**呼出しがあった session 数**で、提示 session 数との比が
    そのまま使用率になる (LLM 段階で bucket に落とす材料。tool は判定しない)。
    """
    invoked: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for inv in invocations:
        invoked[(inv.unit_type, inv.unit_id)].add(inv.session_id)
        if inv.unit_type == "mcp_tool":
            parsed = parse_mcp_name(inv.unit_id)
            if parsed:
                invoked[("mcp_server", normalize_mcp_server(parsed[0]))].add(
                    inv.session_id)
    # deferred tool の分子は tool 名そのもの (units に対応 unit 型が無い)
    for tool_name, sessions in context.tool_use_sessions.items():
        invoked[("deferred_tool", tool_name)] |= sessions

    units: dict[str, list[dict]] = {t: [] for t in PRESENTED_UNIT_TYPES}
    for (unit_type, unit_id), sessions in context.presented.items():
        if unit_type not in units:
            continue
        span = context.presented_span.get((unit_type, unit_id), ["", ""])
        join_id = (normalize_mcp_server(unit_id)
                   if unit_type == "mcp_server" else unit_id)
        units[unit_type].append({
            "id": unit_id,
            "sessions_presented": len(sessions),
            "sessions_invoked": len(invoked.get((unit_type, join_id), set())),
            "first_presented_at": span[0],
            "last_presented_at": span[1],
        })
    for entries in units.values():
        entries.sort(key=lambda e: (-e["sessions_presented"], e["id"]))
    return {
        "sessions_with_skill_listing": len(context.listing_sessions),
        "units": units,
    }


def build_usage_section(context: ContextObservation) -> dict:
    """turn 単位の token 経済と、`attributionSkill` 由来の skill 別内訳。

    `iterations[]` は足さない (adapter 側で除外済み)。skill 別は**その skill に
    帰属した assistant turn の合計**であって、skill が「消費させた」総量ではない
    (skill 適用後の turn は帰属が外れる) — 過小側の下限として読む。
    """
    by_skill = [
        {"id": skill_id, **values}
        for skill_id, values in sorted(context.usage_by_skill.items())
    ]
    by_skill.sort(key=lambda e: (-e.get("output_tokens", 0), e["id"]))
    return {
        "totals": dict(sorted(context.usage_totals.items())),
        "assistant_turns": context.assistant_turns,
        "attributed_turns": context.attributed_turns,
        "by_skill": by_skill,
        "attribution_note": (
            "by_skill は attributionSkill が付いた turn だけの合計 (下限)。"
            "帰属の付かない turn は totals にのみ入る"
        ),
    }


def build_mart(
    args: argparse.Namespace,
    invocations: list[Invocation],
    cutoff: dt.datetime,
    now: dt.datetime,
    denominators: dict[str, list[dict]],
    session_attrs: dict[str, dict],
    context: ContextObservation | None = None,
) -> dict:
    total = len(invocations)
    sessions = {inv.session_id for inv in invocations}
    projects = {inv.project_dir for inv in invocations}
    units = aggregate_units(invocations)
    sessions_section = build_sessions_section(invocations, session_attrs)

    # 観測されたが分母に無い id を session-observed として補完 tag する。
    # ここでの補完は「観測分の下限保証」でしかない — claude.ai connectors 等の
    # 実際に installed だがローカル config に出ない分は LLM 段階で追加補完する。
    known = {
        "skills": {s["id"] for s in denominators["skills"]},
        "mcp_servers": {s["id"] for s in denominators["mcp_servers"]},
        "plugins": {s["id"] for s in denominators["plugins"]},
    }
    for uid in sorted({u["id"] for u in units["skill"]} - known["skills"]):
        denominators["skills"].append({
            "id": uid, "source": "session-observed",
            "plugin": None, "install_path": None,
        })
    for uid in sorted({u["id"] for u in units["mcp_server"]} - known["mcp_servers"]):
        denominators["mcp_servers"].append({
            "id": uid, "source": "session-observed", "scope": None,
        })
    for uid in sorted({u["id"] for u in units["plugin"]} - known["plugins"]):
        denominators["plugins"].append({
            "id": uid, "source": "session-observed", "install_path": None,
        })

    return {
        "meta": {
            "generated_at": now.isoformat().replace("+00:00", "Z"),
            "window_start": cutoff.isoformat().replace("+00:00", "Z"),
            "window_end": now.isoformat().replace("+00:00", "Z"),
            "days": args.days,
            "total_invocations": total,
            "distinct_sessions": len(sessions),
            "distinct_projects": len(projects),
            "config_dir": str(args.config_dir),
            "repo_root": str(args.repo_root),
            "transcripts_dir": str(args.transcripts_dir),
        },
        "distribution": {
            "total": total,
            "threshold": args.sufficient_threshold,
            "sufficient_for_relative_judgment": total >= args.sufficient_threshold,
        },
        "denominators": denominators,
        "units": units,
        "sessions": sessions_section,
        "presented": build_presented_section(context or ContextObservation(),
                                             invocations),
        "usage": build_usage_section(context or ContextObservation()),
    }


# --- main --------------------------------------------------------------------

def collect(args: argparse.Namespace) -> dict:
    """テスト用の純関数 entrypoint。ファイル I/O を伴わない mart 構築まで。"""
    now = resolve_now(args.now)
    cutoff = now - dt.timedelta(days=args.days)
    plugins_meta = enumerate_installed_plugins(args.config_dir)
    enumerated_skills = enumerate_skills(args.config_dir, args.repo_root)
    # extract より先に分母を確定させ、resolver を組む (slash / Read channel の判定源)
    resolver = build_skill_resolver(enumerated_skills)
    invocations: list[Invocation] = []
    session_attrs: dict[str, dict] = {}
    context = ContextObservation()
    for project_dir, jsonl in walk_transcripts(args.transcripts_dir, cutoff):
        invs, sattrs = extract_invocations(jsonl, cutoff, project_dir, resolver,
                                           context=context)
        invocations.extend(invs)
        for sid, attrs in sattrs.items():
            merged = session_attrs.setdefault(
                sid, {"has_code_edit": False, "has_plan_mode": False}
            )
            for key, val in attrs.items():
                if val:
                    merged[key] = True
    denominators = {
        "skills": enumerated_skills,
        "mcp_servers": enumerate_mcp_servers(args.claude_json, args.repo_root),
        "plugins": [
            {
                "id": pid,
                "source": "config",
                "install_path": meta.get("install_path"),
            }
            for pid, meta in plugins_meta.items()
        ],
    }
    return build_mart(args, invocations, cutoff, now, denominators, session_attrs,
                      context)


def emit(mart: dict, args: argparse.Namespace) -> str:
    """mart-<timestamp>.json を書いて path を返す。

    stamp は mart の `generated_at` から起こす — `resolve_now` を引き直すと
    mart 内の時刻とファイル名が秒境界でずれる。
    """
    args.output_dir.mkdir(parents=True, exist_ok=True)
    generated = dt.datetime.fromisoformat(mart["meta"]["generated_at"])
    out = args.output_dir / f"mart-{generated.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(
        json.dumps(mart, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(out)


def run(
    days: int = DEFAULT_DAYS,
    repo_root: str | None = None,
    output_dir: str = str(DEFAULT_OUTPUT_DIR),
    transcripts_dir: str = str(DEFAULT_TRANSCRIPTS_DIR),
    config_dir: str = str(DEFAULT_CONFIG_DIR),
    claude_json: str = str(DEFAULT_CLAUDE_JSON),
    now: str | None = None,
) -> dict:
    """tool 側の入口。mart は返さず、書いた path と判定可能性の meta だけを返す。"""
    args = parse_args([])
    args.days = days
    args.repo_root = Path(repo_root) if repo_root else Path.cwd()
    args.output_dir = Path(output_dir)
    args.transcripts_dir = Path(transcripts_dir)
    args.config_dir = Path(config_dir)
    args.claude_json = Path(claude_json)
    args.now = now

    mart = collect(args)
    meta = mart["meta"]
    return {
        "path": emit(mart, args),
        "meta": {
            "window_start": meta["window_start"],
            "window_end": meta["window_end"],
            "days": meta["days"],
            "repo_root": meta["repo_root"],
            "total_invocations": meta["total_invocations"],
            "distinct_sessions": meta["distinct_sessions"],
            "sufficient_for_relative_judgment":
                mart["distribution"]["sufficient_for_relative_judgment"],
            "unit_counts": {k: len(v) for k, v in mart["units"].items()},
            "presented_counts": {
                k: len(v) for k, v in mart["presented"]["units"].items()
            },
            "sessions_with_skill_listing":
                mart["presented"]["sessions_with_skill_listing"],
            "attributed_turns": mart["usage"]["attributed_turns"],
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mart = collect(args)
    if args.stdout_mart:
        json.dump(mart, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    print(emit(mart, args))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
