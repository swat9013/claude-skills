"""skill / agent / MCP tool の invocation 実績の mart。

lake の呼出しと install 済みの分母を突き合わせ、提示の分母・token 経済・決定的ルールの候補までを 1 行に載せる。
bucket は確定しない — 出すのは候補と導出過程と未判定の条件までで、確定は呼び出し元が持つ。

invariant:
1. 数えるのは観測窓の中の record だけ (時刻の無い record は窓の中に数える)
2. subagent の transcript の呼出しは数えない (母集団は main)
3. 無関係な Read と、分母に無い組み込みの slash は skill の呼出しに数えない
4. `select:Bash` のような誤 invoke の prefix を plugin の分母に生やさない
5. 提示と呼出しは MCP server の表記が違っても join する
6. rule は候補までで bucket を確定しない
7. 編集だけの session も sessions に載せ、2 つの ts 欄は同じ表記で出す
"""

from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import json
import re
from collections.abc import Callable, Collection
from pathlib import Path

from named_queries import _invocation_denominators as denominators
from named_query import RECOVERY, NamedQuery, QueryRun, Stopped

# 総 invocation がこれ未満なら、個別の 0 件を「使われていない」と読める母数が無い
SUFFICIENT_THRESHOLD = 30
EXCERPT_MAX_PER_UNIT = 3
EXCERPT_TEXT_LIMIT = 200

MCP_NAME_RE = re.compile(r"^mcp__(.+?)__(.+)$")
SKILL_LOAD_CHANNELS = ("skill_tool", "command", "read")
CODE_EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
PLAN_MODE_TOOLS = frozenset({"EnterPlanMode"})

# 分母の unit 型は units の unit 型と揃える。揃えないと分子と分母を join できない。
PRESENTED_UNIT_TYPE = {"skill_listing": "skill", "mcp_instructions_delta": "mcp_server",
                       "agent_listing_delta": "agent", "deferred_tools_delta": "deferred_tool"}
# plugin を名指しする提示は無いので、plugin の提示は配下の skill の提示から導く
PRESENTED_UNIT_TYPES = ("skill", "mcp_server", "agent", "deferred_tool", "plugin")
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

IN_WINDOW = "(ts_epoch IS NULL OR ts_epoch BETWEEN $since AND $until)"
TOOL_USES = f"""
SELECT lake_project_dir(path) AS project_dir, path, line_seq, block_no, tool, unit_id, target_path, input_excerpt,
       outcome_base AS outcome, attribution_skill, session_id, ts, ts_epoch
FROM lake_tool_outcome
WHERE {IN_WINDOW} AND (tool IN ('Skill', 'Agent', 'Read') OR starts_with(tool, 'mcp__'))
"""
SLASHES = f"""
SELECT lake_project_dir(path) AS project_dir, path, line_seq, command_name, session_id, ts, ts_epoch
FROM lake_slash_invocation WHERE {IN_WINDOW}
"""
USER_TEXTS = f"SELECT path, line_seq, text_blocks FROM lake_user_prompt WHERE {IN_WINDOW} AND len(text_blocks) > 0"
TOOL_SESSIONS = f"""
SELECT tool, session_id, min(ts_epoch) AS first_ts_epoch
FROM lake_tool_use WHERE {IN_WINDOW} AND tool <> '' GROUP BY tool, session_id
"""
LISTING_SESSIONS = f"""
SELECT DISTINCT session_id FROM lake_attachment
WHERE {IN_WINDOW} AND attachment_type = 'skill_listing' AND session_id <> ''
"""
PRESENTED = f"""
SELECT attachment_type, unit_name, session_id, ts FROM lake_presented_name WHERE {IN_WINDOW} AND session_id <> ''
"""
USAGE = f"""
SELECT attribution_skill, {', '.join(USAGE_KEYS)} FROM lake_assistant_usage WHERE {IN_WINDOW}
"""

RULE_UNIT_TABLE = "invocation_rule_unit"
BUCKET_CANDIDATES = {"skill": "delete-candidate-pending", "mcp_server": "disconnect-candidate-pending",
                     "plugin": "uninstall-candidate-pending"}
DENOMINATOR_KEYS = {"skill": "skills", "mcp_server": "mcp_servers", "plugin": "plugins"}


@dataclasses.dataclass(frozen=True)
class Invocation:
    unit_type: str  # skill / agent / mcp_tool
    unit_id: str
    session_id: str
    timestamp: str
    ts_epoch: float | None  # 順序の判定に使う。表示は timestamp
    project_dir: str
    user_prompt: str
    tool_input: str
    outcome: str
    attribution_skill: str | None
    channel: str = "skill_tool"  # skill の unit だけ意味を持つ


def _parse_mcp_name(name: str) -> tuple[str, str] | None:
    match = MCP_NAME_RE.match(name)
    return (match.group(1), match.group(2)) if match else None


def _normalize_mcp_server(name: str) -> str:
    """MCP server の表記の差を吸収した join の key。

    提示 (`claude.ai Slack`) と呼出し (`claude_ai_Slack`) で表記が違い、plugin 同梱の server も区切りが違う。
    揃えないと毎日呼ばれている server が「提示されたが 0 呼出」に見える。`-` は両側で保たれる。
    """
    return name.replace(":", "_").replace(".", "_").replace(" ", "_")


def _plugin_of_skill(skill_id: str, plugin_ids: Collection[str]) -> str | None:
    """skill id の namespace が登録済みの plugin なら、その名前。"""
    prefix, separator, _ = skill_id.partition(":")
    return prefix if separator and prefix in plugin_ids else None


def _mcp_tool_belongs_to(tool_id: str, plugin_id: str) -> bool:
    """同梱の server の tool か。命名の規約に頼るので、外れる server は未判定の条件が受ける。"""
    return tool_id.startswith(f"mcp__plugin_{plugin_id}_")


def _earliest(*epochs: float | None) -> float | None:
    known = [epoch for epoch in epochs if epoch is not None]
    return min(known) if known else None


def _epoch_to_iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    # 小数部の桁を揃える。省くと文字列の大小が時刻の前後と逆になる組が出る
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _skill_resolver(skills: list[dict]) -> tuple[set[str], Callable[[str], str | None]]:
    """分母の skill id と、Read した path → skill id の関数。

    symlink の配置は 2 つの表記で読まれるので両方を持つ。plugin の skill は、窓の中で更新された版や別の置き場の
    path でも読まれるので、plugin の名前を含む path の下の同じ相対 path を同じ skill と読む。
    """
    md_paths: dict[str, str] = {}
    plugin_tails: list[tuple[str, str, str]] = []
    for skill in skills:
        md_paths[skill["skill_md"]] = md_paths[str(Path(skill["skill_md"]).resolve())] = skill["id"]
        # manifest は install 先の外を指せるので、その skill は完全一致だけで読む
        if skill["plugin"] and Path(skill["skill_md"]).is_relative_to(skill["install_path"]):
            tail = "/" + str(Path(skill["skill_md"]).relative_to(skill["install_path"]))
            plugin_tails.append((tail, f"/{skill['plugin']}/", skill["id"]))

    def skill_of_read(path: str) -> str | None:
        if path in md_paths or not path.endswith("/SKILL.md"):
            return md_paths.get(path)
        return next((skill_id for tail, plugin_dir, skill_id in plugin_tails
                     if path.endswith(tail) and plugin_dir in path), None)

    return {skill["id"] for skill in skills}, skill_of_read


def _slash_skill_id(command: str, known_ids: set[str]) -> str | None:
    """namespace を持つ名前は plugin の skill と確定する。無い名前は分母にあるときだけで、組み込みの slash を落とす。"""
    return command if ":" in command or command in known_ids else None


def _user_excerpt(text_blocks: list[str]) -> str:
    """人が読める本文の抜粋。tag で始まる block (slash の展開を含む) は読まない。"""
    return " ".join(block for block in text_blocks if not block.startswith("<")).strip()[:EXCERPT_TEXT_LIMIT]


def _stop_on_lost_identity(run: QueryRun, tool_uses: list[dict]) -> None:
    """識別の path は使わない環境では窓に現れないので照合が止めない。識別を失った呼出しを 0 件へ読み替えない。"""
    skill_uses = [row for row in tool_uses if row["tool"] == "Skill"]
    if skill_uses and not any(row["unit_id"] for row in skill_uses):
        (row,) = run.select("SELECT lake_skill_identity_path() AS path")
        raise Stopped("schema_mismatch", RECOVERY, missing_paths=[row["path"]])


def _collect_invocations(run: QueryRun, known_ids: set[str],
                         skill_of_read: Callable[[str], str | None]) -> list[Invocation]:
    """3 つの系列を file の行の順に並べ、直前の user turn の抜粋を添えて呼出しの列にする。"""
    tool_uses = run.select(TOOL_USES)
    _stop_on_lost_identity(run, tool_uses)
    events = [(row["path"], row["line_seq"], 0, "user", row) for row in run.select(USER_TEXTS)]
    events += [(row["path"], row["line_seq"], 0, "slash", row) for row in run.select(SLASHES)]
    events += [(row["path"], row["line_seq"], row["block_no"], "tool_use", row) for row in tool_uses]
    events.sort(key=lambda event: event[:3])

    invocations: list[Invocation] = []
    last_prompt = ""
    current_file = None
    for path, _, _, kind, row in events:
        if path != current_file:
            current_file, last_prompt = path, ""
        if kind == "user":
            last_prompt = _user_excerpt(row["text_blocks"]) or last_prompt
        elif kind == "slash":
            skill_id = _slash_skill_id(row["command_name"], known_ids)
            if skill_id is not None:
                invocations.append(Invocation(
                    "skill", skill_id, row["session_id"], row["ts"], row["ts_epoch"], row["project_dir"],
                    f"/{row['command_name']}"[:EXCERPT_TEXT_LIMIT], "", "success", None, "command"))
        else:
            unit = _tool_unit(row, skill_of_read)
            if unit is not None:
                invocations.append(Invocation(
                    unit[0], unit[1], row["session_id"], row["ts"], row["ts_epoch"], row["project_dir"], last_prompt,
                    row["input_excerpt"], row["outcome"], row["attribution_skill"] or None, unit[2]))
    return invocations


def _tool_unit(row: dict, skill_of_read: Callable[[str], str | None]) -> tuple[str, str, str] | None:
    """tool_use を unit へ振り分ける。無関係な Read と識別の無い呼出しは None。"""
    tool = row["tool"]
    if tool == "Skill":
        unit = ("skill", row["unit_id"], "skill_tool")
    elif tool == "Agent":
        unit = ("agent", row["unit_id"], "skill_tool")
    elif tool == "Read":
        unit = ("skill", skill_of_read(row["target_path"]), "read")
    else:
        unit = ("mcp_tool", tool, "skill_tool")
    return unit if unit[1] else None


def _session_attributes(tool_sessions: list[dict]) -> dict[str, dict]:
    attributes: dict[str, dict] = collections.defaultdict(
        lambda: {"has_code_edit": False, "has_plan_mode": False, "first_code_edit_epoch": None})
    for row in tool_sessions:
        if row["tool"] in CODE_EDIT_TOOLS:
            session = attributes[row["session_id"]]
            session["has_code_edit"] = True
            session["first_code_edit_epoch"] = _earliest(session["first_code_edit_epoch"], row["first_ts_epoch"])
        if row["tool"] in PLAN_MODE_TOOLS:
            attributes[row["session_id"]]["has_plan_mode"] = True
    return attributes


def _sessions_section(invocations: list[Invocation], attributes: dict[str, dict]) -> list[dict]:
    """skill の呼出しを持つ session と、coverage の分母になる session (コード編集・plan mode) を載せる。

    編集して skill を呼ばなかった session は coverage の未遵守の側で、落とすと分母が分子に一致する。
    """
    loaded: dict[str, set[str]] = collections.defaultdict(set)
    first_invoke: dict[str, float | None] = {}
    for invocation in invocations:
        if invocation.unit_type == "skill":
            loaded[invocation.session_id].add(invocation.unit_id)
            first_invoke[invocation.session_id] = _earliest(first_invoke.get(invocation.session_id), invocation.ts_epoch)
    sessions = []
    for session_id in sorted(set(loaded) | set(attributes)):
        session = attributes.get(session_id, {})
        sessions.append({
            "session_id": session_id,
            "loaded_skills": sorted(loaded.get(session_id, ())),
            "has_code_edit": session.get("has_code_edit", False),
            "has_plan_mode": session.get("has_plan_mode", False),
            "first_code_edit_ts": _epoch_to_iso(session.get("first_code_edit_epoch")),
            "first_skill_invoke_ts": _epoch_to_iso(first_invoke.get(session_id)),
        })
    return sessions


def _sample_excerpts(invocations: list[Invocation]) -> list[dict]:
    """1 session 1 件を新しい順に。"""
    seen: set[str] = set()
    excerpts: list[dict] = []
    for invocation in sorted(invocations, key=lambda item: item.timestamp, reverse=True):
        if invocation.session_id in seen:
            continue
        seen.add(invocation.session_id)
        excerpts.append({"session_id": invocation.session_id, "timestamp": invocation.timestamp,
                         "project_dir": invocation.project_dir, "user_prompt": invocation.user_prompt,
                         "tool_input": invocation.tool_input, "outcome": invocation.outcome,
                         "attribution_skill": invocation.attribution_skill})
        if len(excerpts) >= EXCERPT_MAX_PER_UNIT:
            break
    return excerpts


def _ranked(entries: list[dict]) -> list[dict]:
    """count の降順に並べ、rank と percentile を足す。"""
    entries.sort(key=lambda entry: (-entry["count"], entry["id"]))
    for rank, entry in enumerate(entries, start=1):
        entry["rank"] = rank
        entry["percentile"] = sum(1 for other in entries if other["count"] < entry["count"]) / len(entries)
    return entries


def _aggregate_units(invocations: list[Invocation], plugin_ids: Collection[str]) -> dict[str, list[dict]]:
    grouped: dict[tuple[str, str], list[Invocation]] = collections.defaultdict(list)
    for invocation in invocations:
        grouped[(invocation.unit_type, invocation.unit_id)].append(invocation)
    total = len(invocations)

    def share(count: int) -> float:
        return count / total if total else 0.0

    units: dict[str, list[dict]] = {}
    for unit_type in ("skill", "agent", "mcp_tool"):
        entries = []
        for (kind, unit_id), members in grouped.items():
            if kind != unit_type:
                continue
            entry = {"id": unit_id, "count": len(members), "share": share(len(members)),
                     "outcomes": dict(collections.Counter(member.outcome for member in members)),
                     "excerpts": _sample_excerpts(members)}
            if unit_type == "skill":
                channels = collections.Counter(member.channel for member in members)
                entry["channels"] = {channel: channels[channel] for channel in SKILL_LOAD_CHANNELS}
            entries.append(entry)
        units[unit_type] = _ranked(entries)

    servers: dict[str, dict] = {}
    for tool in units["mcp_tool"]:
        parsed = _parse_mcp_name(tool["id"])
        if parsed:
            server = servers.setdefault(parsed[0], {"id": parsed[0], "count": 0, "tools": [],
                                                    "outcomes": collections.Counter()})
            server["count"] += tool["count"]
            server["tools"].append({"tool": parsed[1], "count": tool["count"]})
            server["outcomes"].update(tool["outcomes"])
    units["mcp_server"] = _ranked([
        {"id": server["id"], "count": server["count"], "share": share(server["count"]),
         "tools": sorted(server["tools"], key=lambda item: (-item["count"], item["tool"])),
         "outcomes": dict(server["outcomes"])}
        for server in servers.values()])

    plugins: dict[str, dict] = {}
    for skill in units["skill"]:
        plugin_id = _plugin_of_skill(skill["id"], plugin_ids)
        if plugin_id is not None:
            plugin = plugins.setdefault(plugin_id, {"id": plugin_id, "count": 0, "skill_ids": []})
            plugin["count"] += skill["count"]
            plugin["skill_ids"].append(skill["id"])
    units["plugin"] = _ranked([{"id": plugin["id"], "count": plugin["count"], "share": share(plugin["count"]),
                                "skill_ids": sorted(plugin["skill_ids"])} for plugin in plugins.values()])
    return units


def _complement_denominators(denoms: dict[str, list[dict]], units: dict[str, list[dict]]) -> None:
    """観測されたが分母に無い id を session-observed で補う。観測分の下限の保証で、install 済みの一覧ではない。"""
    known_skills = {skill["id"] for skill in denoms["skills"]}
    known_servers = {_normalize_mcp_server(server["id"]) for server in denoms["mcp_servers"]}
    for unit_id in sorted({unit["id"] for unit in units["skill"]} - known_skills):
        denoms["skills"].append({"id": unit_id, "source": "session-observed", "plugin": None, "install_path": None,
                                 "skill_md": None})
    for unit_id in sorted(unit["id"] for unit in units["mcp_server"]):
        if _normalize_mcp_server(unit_id) not in known_servers:
            denoms["mcp_servers"].append({"id": unit_id, "source": "session-observed", "scope": None})


def _presented_section(run: QueryRun, invocations: list[Invocation], tool_sessions: list[dict],
                       plugin_ids: Collection[str]) -> dict:
    """session に実際に提示された分母 × 呼出しの有無。除去は追わない — 分母の定義が「一度でも提示された」だから。"""
    presented: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    stamps: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for row in run.select(PRESENTED):
        unit_type = PRESENTED_UNIT_TYPE[row["attachment_type"]]
        presented[(unit_type, row["unit_name"])].add(row["session_id"])
        stamps[(unit_type, row["unit_name"])].append(row["ts"])
        plugin_id = _plugin_of_skill(row["unit_name"], plugin_ids) if unit_type == "skill" else None
        if plugin_id is not None:
            presented[("plugin", plugin_id)].add(row["session_id"])
            stamps[("plugin", plugin_id)].append(row["ts"])

    invoked: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for invocation in invocations:
        invoked[(invocation.unit_type, invocation.unit_id)].add(invocation.session_id)
        if invocation.unit_type == "skill":
            plugin_id = _plugin_of_skill(invocation.unit_id, plugin_ids)
            if plugin_id:
                invoked[("plugin", plugin_id)].add(invocation.session_id)
        elif invocation.unit_type == "mcp_tool":
            parsed = _parse_mcp_name(invocation.unit_id)
            if parsed:
                invoked[("mcp_server", _normalize_mcp_server(parsed[0]))].add(invocation.session_id)
            # 同梱の MCP tool の呼出しも plugin が使われた証拠になる (uninstall は両方を巻き込む)
            for plugin_id in plugin_ids:
                if _mcp_tool_belongs_to(invocation.unit_id, plugin_id):
                    invoked[("plugin", plugin_id)].add(invocation.session_id)
    for row in tool_sessions:
        invoked[("deferred_tool", row["tool"])].add(row["session_id"])

    units: dict[str, list[dict]] = {unit_type: [] for unit_type in PRESENTED_UNIT_TYPES}
    for (unit_type, unit_id), sessions in presented.items():
        join_id = _normalize_mcp_server(unit_id) if unit_type == "mcp_server" else unit_id
        seen = [stamp for stamp in stamps[(unit_type, unit_id)] if stamp]
        units[unit_type].append({"id": unit_id, "sessions_presented": len(sessions),
                                 "sessions_invoked": len(invoked.get((unit_type, join_id), ())),
                                 "first_presented_at": min(seen, default=""),
                                 "last_presented_at": max(seen, default="")})
    for entries in units.values():
        entries.sort(key=lambda entry: (-entry["sessions_presented"], entry["id"]))
    return {"sessions_with_skill_listing": len(run.select(LISTING_SESSIONS)), "units": units}


def _usage_section(run: QueryRun) -> dict:
    totals = dict.fromkeys(USAGE_KEYS, 0)
    by_skill: dict[str, dict] = {}
    turns = attributed = 0
    for row in run.select(USAGE):
        turns += 1
        for key in USAGE_KEYS:
            totals[key] += row[key]
        skill = row["attribution_skill"]
        if skill:
            attributed += 1
            bucket = by_skill.setdefault(skill, {"id": skill, "assistant_turns": 0, **dict.fromkeys(USAGE_KEYS, 0)})
            bucket["assistant_turns"] += 1
            for key in USAGE_KEYS:
                bucket[key] += row[key]
    return {"totals": dict(sorted(totals.items())) if turns else {}, "assistant_turns": turns,
            "attributed_turns": attributed,
            "by_skill": sorted(by_skill.values(), key=lambda entry: (-entry["output_tokens"], entry["id"]))}


def _rule_units(denoms: dict[str, list[dict]], units: dict[str, list[dict]], presented: dict, sufficient: bool,
                total: int) -> list[dict]:
    """決定的ルールの入力の表。母集団は分母で、invocation の無い unit も載る。"""
    def key_of(kind: str, unit_id: str) -> str:
        return _normalize_mcp_server(unit_id) if kind == "mcp_server" else unit_id

    plugin_ids = frozenset(plugin["id"] for plugin in denoms["plugins"])

    def sibling_invocations(plugin_id: str) -> int:
        return (sum(unit["count"] for unit in units["skill"] if _plugin_of_skill(unit["id"], plugin_ids) == plugin_id)
                + sum(unit["count"] for unit in units["mcp_tool"] if _mcp_tool_belongs_to(unit["id"], plugin_id)))

    rows = []
    for kind, denominator_key in DENOMINATOR_KEYS.items():
        counts = {key_of(kind, unit["id"]): unit["count"] for unit in units[kind]}
        sessions = {key_of(kind, unit["id"]): unit["sessions_presented"] for unit in presented["units"][kind]}
        for entry in denoms[denominator_key]:
            lookup = key_of(kind, entry["id"])
            rows.append({"unit": entry["id"], "unit_kind": kind, "count": counts.get(lookup, 0),
                         "denominator_source": entry["source"], "total_invocations": total,
                         "sessions_presented": sessions.get(lookup, 0), "sufficient": sufficient,
                         "sibling_invocations": sibling_invocations(entry["id"]) if kind == "plugin" else 0})
    return rows


def _rule_candidates(run: QueryRun, rule_units: list[dict]) -> list[dict]:
    """決定的ルールの view に unit の表を渡す。判定の述語は view だけが持つ (設計規則 (5))。"""
    run.con.execute(
        f"""CREATE TEMP TABLE {RULE_UNIT_TABLE} AS
        SELECT unnest(?::VARCHAR[]) AS unit, unnest(?::VARCHAR[]) AS unit_kind, unnest(?::BIGINT[]) AS "count",
               unnest(?::VARCHAR[]) AS denominator_source, unnest(?::BIGINT[]) AS total_invocations,
               unnest(?::BIGINT[]) AS sessions_presented, unnest(?::BOOLEAN[]) AS sufficient,
               unnest(?::BIGINT[]) AS sibling_invocations""",
        [[row[column] for row in rule_units]
         for column in ("unit", "unit_kind", "count", "denominator_source", "total_invocations", "sessions_presented",
                        "sufficient", "sibling_invocations")])
    order = {(row["unit_kind"], row["unit"]): position for position, row in enumerate(rule_units)}
    candidates = []
    for verdict in run.select(f"SELECT * FROM lake_rule_invocations('{RULE_UNIT_TABLE}')"):
        rule_id = (verdict["rule_fired"] or [miss["rule_id"] for miss in verdict["near_misses"]])[0]
        candidates.append({
            **verdict,
            "rule_inputs": json.loads(verdict["rule_inputs"]),
            "near_misses": [{**miss, "inputs": json.loads(miss["inputs"])} for miss in verdict["near_misses"]],
            "unit_kind": rule_id.removeprefix("unused_"),
        })
    candidates.sort(key=lambda candidate: order[(candidate["unit_kind"], candidate["unit"])])
    return candidates


def _build(run: QueryRun) -> list[dict]:
    repo_root = Path(run.params["repo_root"]).absolute()
    config_dir = Path.home() / ".claude"
    plugins = denominators.enumerate_installed_plugins(config_dir)
    skills = denominators.enumerate_skills(config_dir, repo_root)
    denoms = {
        "skills": skills,
        "mcp_servers": denominators.enumerate_mcp_servers(Path.home() / ".claude.json", repo_root, plugins.keys()),
        "plugins": [{"id": plugin_id, "source": "config", "install_path": install_path}
                    for plugin_id, install_path in plugins.items()],
    }
    plugin_ids = frozenset(plugins)

    invocations = _collect_invocations(run, *_skill_resolver(skills))
    tool_sessions = run.select(TOOL_SESSIONS)
    total = len(invocations)
    units = _aggregate_units(invocations, plugin_ids)
    _complement_denominators(denoms, units)
    presented = _presented_section(run, invocations, tool_sessions, plugin_ids)
    sufficient = total >= SUFFICIENT_THRESHOLD
    return [{
        "meta": {
            "window_start": _epoch_to_iso(run.window.since),
            "window_end": _epoch_to_iso(run.window.until),
            "total_invocations": total,
            "distinct_sessions": len({invocation.session_id for invocation in invocations}),
            "distinct_projects": len({invocation.project_dir for invocation in invocations}),
            "config_dir": str(config_dir),
            "repo_root": str(repo_root),
        },
        "distribution": {"total": total, "threshold": SUFFICIENT_THRESHOLD, "sufficient_for_relative_judgment": sufficient},
        "denominators": denoms,
        "units": units,
        "sessions": _sessions_section(invocations, _session_attributes(tool_sessions)),
        "presented": presented,
        "usage": _usage_section(run),
        "rule_candidates": _rule_candidates(run, _rule_units(denoms, units, presented, sufficient, total)),
    }]


_CONDITIONS = [
    {"name": "relative_judgment_available", "report_near_miss": True,
     "predicate": "総 invocation 数が sufficient_threshold 以上 (個別 0 件を「使われていない」と読める母数がある)"},
    {"name": "zero_invocation", "report_near_miss": False, "predicate": "観測窓内の count が 0"},
    {"name": "presented_at_least_once", "report_near_miss": True,
     "predicate": "その session に実際に提示されていた (sessions_presented >= 1)。"
                  "提示されていない unit の 0 件は環境差 (plugin 無効化等) であって不使用ではない"},
    {"name": "denominator_source_config", "report_near_miss": True,
     "predicate": "分母 source が config (session-observed の補完でなく、ローカルの設定から確実に列挙されたもの)"},
]
_NO_SIBLING_USAGE = {"name": "no_sibling_usage", "report_near_miss": True,
                     "predicate": "配下 skill と同梱 MCP tool の invocation がいずれも 0 件 "
                                  "(1 つでも使われていれば uninstall は依存を巻き込む)"}
_OPEN_PREDICATES = [
    {"name": "rename_or_removal_in_window",
     "predicate": "観測窓が unit の rename / 削除日をまたいでいないか (またいでいれば旧名の 0 件は正常で、参照元の修正も要らない)"},
    {"name": "denominator_completeness", "predicate": "ローカルの設定に出ない提供元 (claude.ai connectors 等) を分母へ補完したか"},
]
_REMOVABLE_INDEPENDENTLY = {"name": "removable_independently",
                            "predicate": "その unit を単体で外せるか (第三者 plugin 同梱の skill は plugin の操作でしか"
                                         "触れず、削除が他の unit を巻き込む)"}


def _rule_catalog() -> list[dict]:
    return [{
        "rule_id": f"unused_{kind}", "unit_kind": kind, "scope": f"denominators に列挙された {kind} 全件",
        "bucket_candidate": candidate,
        "conditions": _CONDITIONS + ([_NO_SIBLING_USAGE] if kind == "plugin" else []),
        "open_predicates": _OPEN_PREDICATES + ([] if kind == "plugin" else [_REMOVABLE_INDEPENDENTLY]),
        "thresholds": {"sufficient_threshold": SUFFICIENT_THRESHOLD},
    } for kind, candidate in BUCKET_CANDIDATES.items()]


CONTRACT = {
    "schema_version": 2,
    "rules": _rule_catalog(),
    "notes": [
        "rows は 1 行で、mart の節 (meta / distribution / denominators / units / sessions / presented / usage / "
        "rule_candidates) を列に持つ。",
        "skill unit の count は 3 channel (Skill の tool_use / slash command / SKILL.md への Read) の合算で、"
        "内訳は units.skill[].channels に出る。plugin の skill の Read は、plugin の名前を含む path の下の同じ相対 path "
        "(更新前の版・開発中の checkout) も数える。"
        "session の再開で別の file へ写った呼出しは file ごとに数える。",
        "分母に無い id は source: session-observed で補完されるが、これは観測分の下限の保証であって install 済みの一覧"
        "ではない (claude.ai connectors 等はローカルの設定に出ない)。",
        "presented.units の sessions_presented > 0 かつ sessions_invoked == 0 が「載っているのに使われていない」の証拠。"
        "sessions_presented == 0 は「そもそも提示されていない」で、不使用の証拠にはならない。母集団は main なので、"
        "subagent の中でだけ使われる unit も 0 件に見える。",
        "usage は 1 つの応答を 1 回だけ数える。usage.by_skill は skill に帰属した turn だけの合計 (下限) で、"
        "帰属の付かない turn は totals にだけ入る。「この skill が消費させた総量」とは読まない。",
        "sufficient_for_relative_judgment が false なら相対判定は成立しない — 観測不足であって不使用の証拠ではない。",
        "sessions の母集団は「skill invocation を持つ session ∪ コード編集を持つ session ∪ plan mode の session」。"
        "skill を 1 つも呼ばなかった session も載るので、has_code_edit / has_plan_mode を coverage の分母に取れる "
        "(loaded_skills が空の行が未遵守の側)。どれも無い session は載らない。",
        "first_code_edit_ts / first_skill_invoke_ts は順序の判定用の 2 欄で、UTC ISO-8601 に正規化済み (そのまま大小"
        "比較してよい)。first_skill_invoke_ts が first_code_edit_ts より前 = 「編集に着手する前に skill を呼んだ」。",
        "first_skill_invoke_ts は session 内の最初の skill invoke であって特定の skill のものではない。loaded_skills が "
        "2 件以上ある session では、対象の skill 自身が編集より前だったかは本 mart からは確定しない。",
        "coverage の分母は sessions の行を数える。meta.distinct_sessions は invocation 由来のままで sessions より小さく、"
        "そこから取ると未遵守の session が消える。",
        "sessions の ts 欄は正規化済みで、units[].excerpts[].timestamp (生の時刻の転記) とは表記が違う。2 系統を混ぜて"
        "大小比較しない。",
        "時刻の付かない record (欠損・解釈不能) は初回時刻の算出から外れる。該当分しか無ければ ts 欄は null で、最古にも"
        "最新にも倒していない — null を「編集前」とも「編集後」とも読まない。",
    ],
}

QUERY = NamedQuery(contract=CONTRACT, params={"repo_root": "."}, build=_build)
