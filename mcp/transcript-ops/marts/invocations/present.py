"""`scan_invocations` tool の実装 (inventory-skill-mcp 向け data mart 生成)。

ADR 0031 の store 移行 (#498)。transcript の直読みではなく、store への query
(`query.sql`) から skill / agent / mcp_tool の invocation 実績を組み立てる。
mart の出力契約 (top-level key / meta key / unit 型 / channel 語彙) は移行前の
`commands/scan_invocations.py` と同一に保つ (`tests/test_mart_schema_freeze.py` が
固定する)。

本 tool は決定的ルール (`rules.py`) を評価するが、**bucket を確定しない** — 出すのは
候補 (`bucket_candidate`) と導出過程 (`rule_fired` / `rule_inputs`) と未判定条件
(`open_predicates`) までで、bucket (delete-candidate / review-candidate / keep /
insufficient-data) の確定は SKILL.md 手順の LLM 段階、最終採否は人間
([ADR 0032](../../../../docs/adr/0032-policy-free-refinement-deterministic-rules.md)
の出力契約)。抜粋も候補と関係なく全 unit 一律で max 3 件 (session 重複排除・新しい順)
出す。

出力: output_dir に mart-<timestamp>.json を書き、**path だけを返す** (mart 本体は
context に載せない)。

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
import sqlite3
import sys
from collections.abc import Collection
from pathlib import Path
from typing import Any

from adapter.transcript import (
    PRESENTED_ATTACHMENT_TYPES,
    resolve_now,
    truncate,
)
from artifacts import prepare_output_dir
from marts import load_statements
from store import ingest

from . import contract, denominators, rules

QUERY_PATH = Path(__file__).resolve().parent / "query.sql"

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
PRESENTED_UNIT_TYPE: dict[str, str] = {
    "skill_listing": "skill",
    "mcp_instructions_delta": "mcp_server",
    "agent_listing_delta": "agent",
    "deferred_tools_delta": "deferred_tool",
}

if set(PRESENTED_UNIT_TYPE) != set(PRESENTED_ATTACHMENT_TYPES):
    raise RuntimeError(
        "PRESENTED_UNIT_TYPE と adapter の PRESENTED_ATTACHMENT_TYPES が不一致: "
        f"{sorted(set(PRESENTED_UNIT_TYPE) ^ set(PRESENTED_ATTACHMENT_TYPES))}"
    )
# `plugin` は上表に無い — plugin を名指しする attachment は存在せず、提示は配下
# skill の `skill_listing` から導出する (`_plugin_presentation`)。提示分母の unit 型
# としては第一級なので、この tuple にだけ入れる。
PRESENTED_UNIT_TYPES = ("skill", "mcp_server", "agent", "deferred_tool", "plugin")


# --- データ ------------------------------------------------------------------

@dataclasses.dataclass
class ContextObservation:
    """session に**提示された分母**と token 経済の accumulator (#478 P2 / P5)。

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
    channel: str = CHANNEL_SKILL_TOOL  # skill unit のみ意味を持つ


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
      `target_path` string 一致で load 判定する。symlink 配置の plugin は同じ
      SKILL.md が `~/.claude/skills/<plugin>/...` と実体 path の 2 表記で読まれる
      ので、両方を登録する
    """
    known_ids: set[str] = set()
    md_paths: dict[str, str] = {}
    for s in enumerated_skills:
        sid = s.get("id")
        if isinstance(sid, str):
            known_ids.add(sid)
        install_path = s.get("install_path")
        if isinstance(install_path, str) and install_path and isinstance(sid, str):
            skill_md = Path(install_path) / "SKILL.md"
            md_paths[str(skill_md)] = sid
            md_paths[str(skill_md.resolve())] = sid
    return {"known_skill_ids": known_ids, "skill_md_paths": md_paths}


# --- store への問い合わせ ------------------------------------------------------

def collect_from_store(
    conn: sqlite3.Connection,
    statements: dict[str, str],
    cutoff_epoch: float,
    resolver: dict[str, Any],
) -> tuple[list[Invocation], dict[str, dict], ContextObservation]:
    """query.sql の結果を旧 `extract_invocations` と同じ Invocation 列へ組み立てる。

    tool_use / slash / user_text の 3 系列を (project_dir, file_path, line_no,
    block_no) でソートマージし、逐次巡回で「直前の user turn text」を復元する —
    outcome は ingest 側で確定済みなので tool_result のペアリングはもう要らない。
    """
    known_skill_ids: set[str] = resolver["known_skill_ids"]
    skill_md_paths: dict[str, str] = resolver["skill_md_paths"]
    params = {"cutoff_epoch": cutoff_epoch}

    events: list[tuple] = []
    for row in conn.execute(statements["user_text_events"], params):
        events.append((row["project_dir"], row["file_path"], row["line_no"], 0,
                       "user", row))
    for row in conn.execute(statements["slash_events"], params):
        events.append((row["project_dir"], row["file_path"], row["line_no"], 0,
                       "slash", row))
    for row in conn.execute(statements["candidate_tool_use"], params):
        events.append((row["project_dir"], row["file_path"], row["line_no"],
                       row["block_no"], "tool_use", row))
    events.sort(key=lambda e: (e[0], e[1], e[2], e[3]))

    invocations: list[Invocation] = []
    last_user_prompt = ""
    current_file: tuple[str, str] | None = None
    for project_dir, file_path, _line_no, _block_no, kind, row in events:
        if (project_dir, file_path) != current_file:
            current_file = (project_dir, file_path)
            last_user_prompt = ""
        if kind == "user":
            last_user_prompt = row["text_excerpt"]
            continue
        if kind == "slash":
            skill_id = resolve_slash_to_skill_id(row["command_name"], known_skill_ids)
            if skill_id is None:
                continue
            invocations.append(Invocation(
                unit_type="skill", unit_id=skill_id,
                session_id=str(row["session_id"]), timestamp=row["ts"],
                project_dir=project_dir,
                user_prompt=truncate(f"/{row['command_name']}", EXCERPT_TEXT_LIMIT),
                tool_input="", outcome="success", attribution_skill=None,
                channel=CHANNEL_COMMAND,
            ))
            continue
        # tool_use
        tool = row["tool"]
        channel = CHANNEL_SKILL_TOOL
        if tool == "Skill":
            unit_type, unit_id = "skill", row["unit_id"]
        elif tool == "Agent":
            unit_type, unit_id = "agent", row["unit_id"]
        elif tool.startswith("mcp__"):
            unit_type, unit_id = "mcp_tool", tool
        elif tool == "Read":
            skill_id = skill_md_paths.get(row["target_path"])
            if skill_id is None:
                continue
            unit_type, unit_id, channel = "skill", skill_id, CHANNEL_READ
        else:
            continue
        if not unit_id:
            continue
        invocations.append(Invocation(
            unit_type=unit_type, unit_id=unit_id,
            session_id=str(row["session_id"]), timestamp=row["ts"],
            project_dir=project_dir,
            # user_prompt / tool_input は ingest 側で既に INPUT_EXCERPT_LIMIT (200
            # = EXCERPT_TEXT_LIMIT) へ truncate 済み。ここで truncate() を再適用すると
            # `s.strip()` が 200 字スライスの末尾空白まで剥がし、旧実装 (1 回だけ
            # truncate) と 1 字ずれる (実 lake の差分説明書で検出)
            user_prompt=last_user_prompt,
            tool_input=row["input_excerpt"],
            outcome=row["outcome"],
            attribution_skill=row["attribution_skill"] or None,
            channel=channel,
        ))

    context = ContextObservation()
    session_attrs: dict[str, dict] = {}
    for row in conn.execute(statements["all_tool_use_sessions"], params):
        tool, sid = row["tool"], str(row["session_id"])
        context.note_tool_use(tool, sid)
        if tool in CODE_EDIT_TOOLS:
            session_attrs.setdefault(
                sid, {"has_code_edit": False, "has_plan_mode": False})[
                "has_code_edit"] = True
        if tool in PLAN_MODE_TOOLS:
            session_attrs.setdefault(
                sid, {"has_code_edit": False, "has_plan_mode": False})[
                "has_plan_mode"] = True

    context.listing_sessions = {
        str(row["session_id"])
        for row in conn.execute(statements["static_payload_sessions"], params)
        if row["attachment_type"] == "skill_listing"
    }
    for row in conn.execute(statements["presented_events"], params):
        unit_type = PRESENTED_UNIT_TYPE.get(row["attachment_type"])
        if unit_type is None:
            continue
        context.note_presented(unit_type, row["name"], str(row["session_id"]),
                               row["ts"])

    for row in conn.execute(statements["assistant_usage"], params):
        attribution = row["attribution_skill"] or None
        context.note_usage(attribution, {
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cache_creation_input_tokens": row["cache_creation_input_tokens"],
            "cache_read_input_tokens": row["cache_read_input_tokens"],
        })

    return invocations, session_attrs, context


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


def aggregate_units(invocations: list[Invocation],
                    known_plugin_ids: Collection[str]) -> dict[str, list[dict]]:
    """unit_type ごとに count / share / rank / percentile / excerpts を計算し、
    mcp_server と plugin の roll-up も行う。skill unit は channels 内訳も含む。

    `known_plugin_ids` は plugin roll-up で namespace を照合する分母の plugin 一覧。
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

    # plugin roll-up: skill id "<plugin>:<skill>" を plugin ごとに合算
    plugin_agg: dict[str, dict] = {}
    for skill in result["skill"]:
        sid = skill["id"]
        pname = rules.plugin_of_skill(sid, known_plugin_ids)
        if pname is None:
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


def _plugin_presentation(
    context: ContextObservation,
    known_plugin_ids: Collection[str],
) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    """配下 skill の提示から plugin 単位の提示 session と提示時刻を組む。

    plugin を名指しする提示 attachment は無いので、`skill_listing` に載った配下 skill を
    その plugin の提示とみなす。session は **union** を採る — skill ごとに提示される
    session は違うので、代表 1 件の max では plugin が提示された session を取りこぼす。
    """
    sessions: dict[str, set[str]] = collections.defaultdict(set)
    stamps: dict[str, list[str]] = collections.defaultdict(list)
    for (unit_type, unit_id), presented_sessions in context.presented.items():
        if unit_type != "skill":
            continue
        plugin_id = rules.plugin_of_skill(unit_id, known_plugin_ids)
        if plugin_id is None:
            continue
        sessions[plugin_id] |= presented_sessions
        stamps[plugin_id].extend(
            s for s in context.presented_span.get((unit_type, unit_id), []) if s)
    return sessions, stamps


def build_presented_section(context: ContextObservation,
                            invocations: list[Invocation],
                            known_plugin_ids: Collection[str]) -> dict:
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
        if inv.unit_type == "skill":
            plugin_id = rules.plugin_of_skill(inv.unit_id, known_plugin_ids)
            if plugin_id:
                invoked[("plugin", plugin_id)].add(inv.session_id)
        elif inv.unit_type == "mcp_tool":
            parsed = parse_mcp_name(inv.unit_id)
            if parsed:
                invoked[("mcp_server", normalize_mcp_server(parsed[0]))].add(
                    inv.session_id)
            # 同梱 MCP tool の呼出も plugin が使われた証拠 (uninstall は両方を巻き込む)
            for plugin_id in known_plugin_ids:
                if rules.mcp_tool_belongs_to(inv.unit_id, plugin_id):
                    invoked[("plugin", plugin_id)].add(inv.session_id)
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
    plugin_sessions, plugin_stamps = _plugin_presentation(context, known_plugin_ids)
    for plugin_id, sessions in plugin_sessions.items():
        stamps = plugin_stamps.get(plugin_id) or [""]
        units["plugin"].append({
            "id": plugin_id,
            "sessions_presented": len(sessions),
            "sessions_invoked": len(invoked.get(("plugin", plugin_id), set())),
            "first_presented_at": min(stamps),
            "last_presented_at": max(stamps),
        })
    for entries in units.values():
        entries.sort(key=lambda e: (-e["sessions_presented"], e["id"]))
    return {
        "sessions_with_skill_listing": len(context.listing_sessions),
        "units": units,
    }


def build_usage_section(context: ContextObservation) -> dict:
    """turn 単位の token 経済と、`attributionSkill` 由来の skill 別内訳。

    `iterations[]` は足さない (ingest 側で除外済み)。skill 別は**その skill に
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
    denoms: dict[str, list[dict]],
    session_attrs: dict[str, dict],
    context: ContextObservation | None = None,
) -> dict:
    total = len(invocations)
    sessions = {inv.session_id for inv in invocations}
    projects = {inv.project_dir for inv in invocations}
    known_plugin_ids = frozenset(p["id"] for p in denoms["plugins"])
    units = aggregate_units(invocations, known_plugin_ids)
    sessions_section = build_sessions_section(invocations, session_attrs)

    # 観測されたが分母に無い id を session-observed として補完 tag する。
    # ここでの補完は「観測分の下限保証」でしかない — claude.ai connectors 等の
    # 実際に installed だがローカル config に出ない分は LLM 段階で追加補完する。
    known_skills = {s["id"] for s in denoms["skills"]}
    # MCP server だけは表記が config / 提示 / 呼出で揺れるので、突合も join キーで行う。
    # 生比較すると config 済みの server が session-observed として二重に生える
    known_mcp_keys = {normalize_mcp_server(s["id"]) for s in denoms["mcp_servers"]}
    for uid in sorted({u["id"] for u in units["skill"]} - known_skills):
        denoms["skills"].append({
            "id": uid, "source": "session-observed",
            "plugin": None, "install_path": None,
        })
    for uid in sorted({u["id"] for u in units["mcp_server"]}):
        if normalize_mcp_server(uid) in known_mcp_keys:
            continue
        denoms["mcp_servers"].append({
            "id": uid, "source": "session-observed", "scope": None,
        })
    for uid in sorted({u["id"] for u in units["plugin"]} - known_plugin_ids):
        denoms["plugins"].append({
            "id": uid, "source": "session-observed", "install_path": None,
        })

    sufficient = total >= args.sufficient_threshold
    presented = build_presented_section(context or ContextObservation(), invocations,
                                        known_plugin_ids)
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
        "contract": contract.build_contract(
            rules.rule_catalog(args.sufficient_threshold)),
        "distribution": {
            "total": total,
            "threshold": args.sufficient_threshold,
            "sufficient_for_relative_judgment": sufficient,
        },
        "denominators": denoms,
        "units": units,
        "sessions": sessions_section,
        "presented": presented,
        "usage": build_usage_section(context or ContextObservation()),
        "rule_candidates": rules.evaluate(
            units, denoms, presented, sufficient, args.sufficient_threshold, total,
            mcp_server_key=normalize_mcp_server),
    }


# --- main --------------------------------------------------------------------

def collect(args: argparse.Namespace) -> dict:
    """テスト用の純関数 entrypoint (I/O は store の差分 sync のみ)。"""
    now = resolve_now(args.now)
    cutoff = now - dt.timedelta(days=args.days)
    cutoff_epoch = cutoff.timestamp()

    plugins_meta = denominators.enumerate_installed_plugins(args.config_dir)
    enumerated_skills = denominators.enumerate_skills(args.config_dir, args.repo_root)
    resolver = build_skill_resolver(enumerated_skills)

    conn, _sync_report = ingest.open_synced(args.transcripts_dir, now=now)
    try:
        statements = load_statements(QUERY_PATH)
        invocations, session_attrs, context = collect_from_store(
            conn, statements, cutoff_epoch, resolver)
    finally:
        conn.close()

    denoms = {
        "skills": enumerated_skills,
        "mcp_servers": denominators.enumerate_mcp_servers(
            args.claude_json, args.repo_root, plugins_meta.keys()),
        "plugins": [
            {
                "id": pid,
                "source": "config",
                "install_path": meta.get("install_path"),
            }
            for pid, meta in plugins_meta.items()
        ],
    }
    return build_mart(args, invocations, cutoff, now, denoms, session_attrs, context)


def emit(mart: dict, args: argparse.Namespace) -> str:
    """mart-<timestamp>.json を書いて path を返す。

    stamp は mart の `generated_at` から起こす — `resolve_now` を引き直すと
    mart 内の時刻とファイル名が秒境界でずれる。
    """
    output_dir = prepare_output_dir(args.output_dir)
    generated = dt.datetime.fromisoformat(mart["meta"]["generated_at"])
    out = output_dir / f"mart-{generated.strftime('%Y%m%dT%H%M%SZ')}.json"
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
            "rule_candidates": _rule_counts(mart["rule_candidates"]),
        },
    }


def _rule_counts(rule_candidates: list[dict]) -> dict[str, int]:
    """候補と near-miss の件数だけを返す (**中身は mart を Read する**)。"""
    return {
        "fired": sum(1 for row in rule_candidates if row["rule_fired"]),
        "near_miss_only": sum(1 for row in rule_candidates if not row["rule_fired"]),
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
