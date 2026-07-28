#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""inventory-skill-mcp の観測 script (data mart 生成)。

transcript (~/.claude/projects/<cwd-hash>/<sid>.jsonl) を data lake として、直近
N 日 (--days、default 30) の tool_use を抽出し、単位別 (skill / agent / mcp_tool
+ mcp_server / plugin の roll-up) 分布 + 使用文脈抜粋 + 分母列挙を含む mart JSON
を出力する。

script の責務は決定的な集計まで — **bucket 判定 (delete-candidate /
review-candidate / keep / insufficient-data 等) は行わない**。bucket 分類は SKILL.md
手順 2 の LLM 段階で mart を読んでから行う (循環依存の回避)。抜粋も bucket に
関係なく全 unit 一律で max 3 件 (session 重複排除・新しい順) 出す。

出力: --output-dir に mart-<timestamp>.json を書き、path を stdout に print する。
Markdown レポートは LLM 段階の成果物として並置される想定で、本 script は生成しない。

汎用スキル制約: 依存は Claude Code 標準ファイルのみ (installed_plugins.json /
plugin manifest / ~/.claude/skills/ / 現 repo .claude/skills/ / ~/.claude.json /
~/.mcp.json)。swat-skills 固有資産 (tool-signatures.jsonl 等) には触らない。
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _transcript_lib import (  # noqa: E402
    USER_REJECT_PATTERNS,
    _iter_jsonl,
    resolve_now,
    truncate,
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

# <command-name>/xxx</command-name> — slash command 呼び出しの user turn 検出用。
# content が str のときに match する (list content は skill/tool loop の中で扱う)
COMMAND_NAME_RE = re.compile(r"<command-name>/([^<]+)</command-name>")

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

# USER_REJECT_PATTERNS は _transcript_lib へ移設 (inventory-permissions と共有)。


# --- データ ------------------------------------------------------------------

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
# resolve_now / truncate は _transcript_lib へ移設 (inventory-permissions と共有)。

def parse_mcp_name(name: str) -> tuple[str, str] | None:
    m = MCP_NAME_RE.match(name)
    if not m:
        return None
    return m.group(1), m.group(2)


def parse_slash_command_name(content: Any) -> str | None:
    """user turn の message.content から `<command-name>/xxx</command-name>` の
    `xxx` 部分 (leading slash 抜き) を取り出す。

    - content が str のみ対象 (list content は tool_result 用)
    - 実 slash 呼出しは必ず `<command-args>` tag も伴う。それが無いものは
      assistant Bash 出力等に literal 引用された regex 例の false positive として
      弾く (実 transcript で観測された false positive パターン)
    - 該当なしなら None
    """
    if not isinstance(content, str):
        return None
    if "<command-args>" not in content:
        return None
    m = COMMAND_NAME_RE.search(content)
    if not m:
        return None
    return m.group(1).strip()


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


def classify_outcome(is_error: Any, content: Any) -> str:
    """tool_result の is_error + content から outcome を推定。

    - is_error==False → "success"
    - is_error==True かつ content に user-reject 文言 → "user-reject"
    - is_error==True → "error"
    - それ以外 → "unknown"
    """
    if is_error is False:
        return "success"
    if is_error is True:
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    t = c.get("text")
                    if isinstance(t, str):
                        parts.append(t)
            text = " ".join(parts)
        low = text.lower()
        for pat in USER_REJECT_PATTERNS:
            if pat in low:
                return "user-reject"
        return "error"
    return "unknown"


# --- transcript walk ---------------------------------------------------------
# _iter_jsonl / walk_transcripts は _transcript_lib へ移設 (inventory-permissions と共有)。

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


def extract_invocations(
    jsonl_path: Path,
    cutoff: dt.datetime,
    project_dir: str,
    resolver: dict[str, Any] | None = None,
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
                if rtype == "user":
                    msg = rec.get("message") or {}
                    content = msg.get("content")
                    text = _extract_user_text(content)
                    if text:
                        last_user_prompt = text
                    # slash command による skill load
                    slash = parse_slash_command_name(content)
                    if slash:
                        ts_str = rec.get("timestamp") or ""
                        sid = rec.get("sessionId") or rec.get("session_id") or ""
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
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") != "tool_result":
                                continue
                            tid = block.get("tool_use_id")
                            if tid in pending:
                                idx = pending.pop(tid)
                                invocations[idx].outcome = classify_outcome(
                                    block.get("is_error"), block.get("content")
                                )
                    continue
                if rtype != "assistant":
                    continue
                ts_str = rec.get("timestamp") or ""
                if not _within_window(ts_str):
                    continue
                msg = rec.get("message") or {}
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                sid = rec.get("sessionId") or rec.get("session_id") or ""
                attribution = rec.get("attributionSkill")
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

    - skill invocation を 1 度以上持つ session だけを載せる (coverage の
      分母候補は「skill load が発生し得た session」ではなく「発生した session」で
      なく LLM が「発生し得たか」の解釈材料にする — 拡張 2 の原型)
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


def build_mart(
    args: argparse.Namespace,
    invocations: list[Invocation],
    cutoff: dt.datetime,
    now: dt.datetime,
    denominators: dict[str, list[dict]],
    session_attrs: dict[str, dict],
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
    for project_dir, jsonl in walk_transcripts(args.transcripts_dir, cutoff):
        invs, sattrs = extract_invocations(jsonl, cutoff, project_dir, resolver)
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
    return build_mart(args, invocations, cutoff, now, denominators, session_attrs)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mart = collect(args)
    if args.stdout_mart:
        json.dump(mart, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    now_dt = resolve_now(args.now)
    stamp = now_dt.strftime("%Y%m%dT%H%M%SZ")
    out = args.output_dir / f"mart-{stamp}.json"
    out.write_text(
        json.dumps(mart, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(str(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
