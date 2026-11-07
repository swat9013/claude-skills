"""permission entry・hook 設定 × 実績の両軸 mart。

観測窓の tool 実行と hook の fire を view から読み、declared state (settings) と突き合わせて、
読む順の part を `rows` に並べる。決定的ルールは view の macro が評価する。bucket は確定しない。

section: project (cwd × 当該 repo 実績) / global (`~/.claude/settings.json` × 全 repo 実績) / all。

invariant:
1. 不正な `section` と `transcript_kind` は lake を読む前に止める。窓の片側が欠けたら答えを返さない
2. 観測窓は record の時刻だけで判定し、時刻の無い実行は窓内へ倒す
3. project section は repo root 配下の実行だけを数える
4. 照合対象を持たない entry の 0 件は `unmatchable` とし、未使用の証拠にしない
5. `unmatchable` の entry は必ず `match_count == 0` を伴う
6. bypass 系列は別 agent の呼び出しを対にしない
"""

from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import json
import os
from pathlib import Path
from typing import Mapping

from named_queries import _permissions_contract as contract
from named_queries import _permissions_matcher as matcher
from named_queries import _permissions_settings as settings_mod
from named_query import ALL_POPULATION, MAIN_POPULATION, NamedQuery, QueryRun, Stopped

SECTIONS = {"project": ("project",), "global": ("global",), "all": ("project", "global")}
POPULATIONS = {"main": MAIN_POPULATION, "all": ALL_POPULATION}
GLOBAL_SETTINGS = Path("~/.claude/settings.json").expanduser()
CONFIG_DIR = Path("~/.claude").expanduser()

# section の `settings_sources` に出す reason (= その section の分母そのもの)。
SECTION_DENOMINATOR_REASONS = ("read", "absent", "unparsed")

# result_text を読むのは拒否の判定だけ。success と unknown の本文は運ばない (全 event 分だと重い)。
TOOL_EVENTS = """
SELECT tool, command, target_path, target_url, input_excerpt, param_keys, session_id, transcript_kind, path,
       ts, ts_epoch, cwd, outcome_base, denial_kind,
       CASE WHEN outcome_base NOT IN ('success', 'unknown') THEN result_text ELSE '' END AS result_text
FROM lake_tool_outcome
WHERE ts_epoch IS NULL OR ts_epoch BETWEEN $since AND $until
ORDER BY path, line_seq, block_no
"""

HOOK_FIRINGS = """
SELECT hook_name, hook_tool, hook_event, command, exit_code, duration_ms, timed_out, tool_use_id, session_id, ts
FROM lake_hook_firing
WHERE ts_epoch IS NULL OR ts_epoch BETWEEN $since AND $until
ORDER BY path, line_seq
"""


@dataclasses.dataclass(frozen=True)
class Event:
    """窓の tool 実行 1 件。`seq` は lake の走査順で、同数の並びの tie-break に使う。"""

    seq: int
    tool: str
    command: str
    command_head: str
    target_path: str
    target_url: str
    input_excerpt: str
    param_keys: tuple[str, ...]
    session_id: str
    # subagent の実行なら transcript の path、main なら空文字。subagent は親 session の id を持つので
    # 系列はこの列でも区切る。main を空に揃えるのは、resume で複数 file にまたがる系列を割らないため
    subagent_transcript: str
    ts: str
    ts_epoch: float | None
    cwd: str
    outcome: str
    denial_kind: str | None
    denial_reason_label: str | None


def _refined_outcome(base: str, denial_kind: str, result_text: str) -> str:
    """base の語彙を mart の 7 分類へ細分する。base が選んだ枝の中で label を付けるだけ。"""
    if base in ("success", "unknown"):
        return base
    if base == "user-reject":
        return "deny_user-rejected"
    if denial_kind == "permission-rule":
        return "deny_permission-rule"
    if denial_kind in ("automode-blocked", "automode-unavailable"):
        return "deny_automode"
    if matcher.looks_like_permission_denial(result_text):
        return "deny_permission-rule"
    if matcher.looks_like_automode_denial(result_text):
        return "deny_automode"
    return "error"


def _refined_denial_kind(outcome: str, raw_kind: str) -> str | None:
    if outcome == "deny_automode":
        return raw_kind if raw_kind in ("automode-blocked", "automode-unavailable") else "automode-blocked"
    return {"deny_user-rejected": "user-rejected", "deny_permission-rule": "permission-rule"}.get(outcome)


def _events(run: QueryRun) -> list[Event]:
    events = []
    for seq, row in enumerate(run.select(TOOL_EVENTS), start=1):
        outcome = _refined_outcome(row["outcome_base"], row["denial_kind"], row["result_text"])
        events.append(Event(
            seq=seq, tool=row["tool"], command=row["command"],
            # command_head は Bash 専用の集約キー。他 tool にも command を持つものがあり、
            # 区別しないと B 軸の 1 unit が引数ごとに割れる
            command_head=matcher.command_head(row["command"]) if row["tool"] == "Bash" else "",
            target_path=row["target_path"], target_url=row["target_url"], input_excerpt=row["input_excerpt"],
            param_keys=tuple(row["param_keys"]), session_id=row["session_id"],
            subagent_transcript=row["path"] if row["transcript_kind"] == "subagent" else "",
            ts=row["ts"], ts_epoch=row["ts_epoch"], cwd=row["cwd"], outcome=outcome,
            denial_kind=_refined_denial_kind(outcome, row["denial_kind"]),
            denial_reason_label=(matcher.automode_reason_label(row["result_text"])
                                 if outcome == "deny_automode" else None)))
    return events


def _scope_roots(section: str, repo_root: Path) -> str:
    """section の cwd スコープ。片側の表記だけだと symlink 経由の repo が黙って 0 件になる。"""
    if section != "project":
        return ""
    roots = [str(repo_root)]
    try:
        resolved = str(repo_root.resolve())
    except OSError:
        resolved = ""
    if resolved and resolved not in roots:
        roots.append(resolved)
    return "\n".join(roots)


def _entry_matches(entries: list[settings_mod.PermissionEntry], events: list[Event]) -> list[list[Event]]:
    """entry ごとの当たった実行を走査順で返す。tool 名の被覆で母集団を絞ってから pattern を照合する。

    A 軸と B 軸が同じ結果を読むので、両軸の値は矛盾しない。
    """
    by_tool: dict[str, list[Event]] = collections.defaultdict(list)
    for event in events:
        by_tool[event.tool].append(event)
    matched = []
    for entry in entries:
        hits = [event
                for tool, group in by_tool.items() if matcher.entry_tool_covers(entry.category, entry.tool, tool)
                for event in group
                if matcher.entry_matches(entry.match_kind, entry.pattern, event.tool, event.command,
                                         event.target_path, event.target_url)]
        matched.append(sorted(hits, key=lambda event: event.seq))
    return matched


def _availability(events: list[Event]) -> dict[str, dict]:
    """tool 別に、matcher が読む各列が非空だった実行数。URL は取り出せた hostname を数える。"""
    counts: dict[str, dict] = {}
    for event in events:
        row = counts.setdefault(event.tool, {"event_count": 0, "command": 0, "target_path": 0, "target_url": 0})
        row["event_count"] += 1
        row["command"] += bool(event.command)
        row["target_path"] += bool(event.target_path)
        row["target_url"] += bool(matcher.url_host(event.target_url))
    return counts


def _param_names(events: list[Event]) -> dict[str, set[str]]:
    """tool 別に、窓内の呼び出しが 1 度でも渡した input param 名。"""
    names: dict[str, set[str]] = collections.defaultdict(set)
    for event in events:
        names[event.tool].update(event.param_keys)
    return names


def unmatchable_reason(entry: settings_mod.PermissionEntry, availability: dict[str, dict],
                       param_names: dict[str, set[str]], match_count: int) -> str | None:
    """照合が成立していない理由。成立しているなら None。

    非 None なら `matcher_confidence` は宣言値より `unmatchable` が勝つ。`unmatchable` は
    match_count 0 を「未使用」と読ませないための札で、理由は 2 つある。

    - `input_column_empty`: 窓内にその tool の実行が在るのに、matcher が読む列がどの実行でも空だった。
      一部の実行にだけ列が無い場合は札を付けず、差は `unobserved_input_count` に出す
    - `param_rule`: `Tool(<param>:<value>)` 形で、その param 名を渡した実行が窓内に在る。param の値は
      view に無いので照合できない。1 度も渡されていない param 名なら 0 件は真の観測

    `match_count` を見るのは、param 名と同名の command を持つ prefix rule を取り違えないため。
    1 件でも当たれば command prefix として機能している。
    本体が rule ごと skip する括弧付き `mcp__…` は照合不能ではなく不在なので札を付けない。
    """
    if matcher.is_skipped_mcp_rule(entry.match_kind, entry.tool):
        return None
    if match_count == 0 and entry.tool in param_names:
        param = matcher.param_rule_name(entry.category, entry.tool, entry.match_kind, entry.pattern)
        if param and param in param_names[entry.tool]:
            return "param_rule"
    column = matcher.matcher_input_column(entry.match_kind, entry.tool)
    counts = availability.get(entry.tool)
    if column is None or counts is None:
        return None
    return "input_column_empty" if counts[column] == 0 else None


def matcher_confidence(entry: settings_mod.PermissionEntry, reason: str | None) -> str:
    """優先順は `unmatchable` > skip される rule の `exact` > 宣言値。

    skip される rule は pattern を照合しないので、pattern 側の wildcard に付く `approx` は意味を持たない。
    """
    if reason:
        return "unmatchable"
    if matcher.is_skipped_mcp_rule(entry.match_kind, entry.tool):
        return "exact"
    return entry.confidence


def unobserved_input_count(entry: settings_mod.PermissionEntry, availability: dict[str, dict]) -> int:
    """matcher が読む列を持たなかった、その tool の実行数。0 でなければ match_count は下限。"""
    if matcher.is_skipped_mcp_rule(entry.match_kind, entry.tool):
        return 0
    column = matcher.matcher_input_column(entry.match_kind, entry.tool)
    counts = availability.get(entry.tool)
    if column is None or counts is None:
        return 0
    return counts["event_count"] - counts[column]


def _sample_matched(hits: list[Event]) -> list[dict]:
    combos: dict[tuple[str, str], dict] = {}
    for event in hits:
        combo = combos.setdefault((event.tool, event.command_head), {"count": 0, "first_seq": event.seq})
        combo["count"] += 1
    ordered = sorted(combos.items(), key=lambda item: (-item[1]["count"], item[1]["first_seq"]))
    return [{"tool": tool, "command_head": head, "count": combo["count"]} for (tool, head), combo in ordered[:3]]


def build_axis_a(entries: list[settings_mod.PermissionEntry], matched: list[list[Event]], events: list[Event],
                 split_epoch: float) -> list[dict]:
    """設定 entry 別の match_count / outcome_breakdown / sample_matched。

    outcome の並びは初出順で、半分ごとの内訳も同じ並びにする。時刻の無い実行はどちらの半分にも入らない
    (early + late < match_count がありうる)。`compound_command_deny_count` は deny のうち複合コマンド行
    由来の件数で、entry の高 deny 比率の別解を示す。
    """
    availability = _availability(events)
    param_names = _param_names(events)
    rows = []
    for entry, hits in zip(entries, matched):
        outcomes = collections.Counter(event.outcome for event in hits)
        halves = {"early": collections.Counter(), "late": collections.Counter()}
        for event in hits:
            if event.ts_epoch is not None:
                halves["early" if event.ts_epoch < split_epoch else "late"][event.outcome] += 1
        reason = unmatchable_reason(entry, availability, param_names, len(hits))
        rows.append({
            "entry": entry.raw,
            "category": entry.category,
            "source_path": entry.source_path,
            "scope": entry.scope,
            "match_kind": entry.match_kind,
            "matcher_confidence": matcher_confidence(entry, reason),
            "unmatchable_reason": reason,
            "unobserved_input_count": unobserved_input_count(entry, availability),
            "match_count": len(hits),
            "outcome_breakdown": dict(outcomes),
            "outcome_breakdown_early": {key: halves["early"][key] for key in outcomes if halves["early"][key]},
            "outcome_breakdown_late": {key: halves["late"][key] for key in outcomes if halves["late"][key]},
            "compound_command_deny_count": sum(
                1 for event in hits
                if event.outcome in contract.HARD_DENY_OUTCOMES and matcher.is_compound_command(event.command)),
            "sample_matched": _sample_matched(hits),
        })
    rows.sort(key=lambda row: (-row["match_count"], row["category"], row["entry"]))
    return rows


@dataclasses.dataclass(frozen=True)
class AxisBCoverage:
    """B 軸 unit を 1 つの config 層で照合した結果。

    `uncovered` は `matches` から導けない — 1 実行が複数 entry に当たるので、entry 別の件数の和は過大になる。
    """

    matches: dict[tuple[str, str], list[dict]]
    uncovered: dict[tuple[str, str], int]


def axis_b_coverage(entries: list[settings_mod.PermissionEntry], matched: list[list[Event]],
                    events: list[Event]) -> AxisBCoverage:
    matches: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    covered: set[int] = set()
    for entry, hits in zip(entries, matched):
        units = collections.Counter((event.tool, event.command_head) for event in hits)
        covered.update(event.seq for event in hits)
        for key, count in units.items():
            matches[key].append({"entry": entry.raw, "category": entry.category, "scope": entry.scope,
                                 "matched_event_count": count})
    uncovered = collections.Counter((event.tool, event.command_head) for event in events if event.seq not in covered)
    return AxisBCoverage(matches=matches, uncovered=uncovered)


def build_axis_b(events: list[Event], coverage: AxisBCoverage, global_coverage: AxisBCoverage) -> list[dict]:
    """tool × command_head の集計 + 対応 entry。

    `config_matches` は section の config だけを見るので、空でも「どこにも収載されていない」ではない。
    global 層の被覆は別列に出す (section global では同じ値を指す)。収載の有無は unit 内の全実行で決まる。
    """
    grouped: dict[tuple[str, str], dict] = {}
    for event in events:
        bucket = grouped.setdefault((event.tool, event.command_head),
                                    {"outcomes": collections.Counter(), "first_seq": event.seq})
        bucket["outcomes"][event.outcome] += 1
    ordered = sorted(grouped.items(), key=lambda item: (-sum(item[1]["outcomes"].values()), item[1]["first_seq"]))
    return [
        {
            "tool": tool,
            "command_head": command_head,
            "count": sum(bucket["outcomes"].values()),
            "outcomes": dict(sorted(bucket["outcomes"].items(), key=lambda item: (-item[1], item[0]))),
            "config_matches": coverage.matches.get((tool, command_head), []),
            "uncovered_event_count": coverage.uncovered.get((tool, command_head), 0),
            "global_config_matches": global_coverage.matches.get((tool, command_head), []),
            "global_uncovered_event_count": global_coverage.uncovered.get((tool, command_head), 0),
        }
        for (tool, command_head), bucket in ordered
    ]


def build_bypass_sequences(events: list[Event]) -> list[dict]:
    """deny 直後の同 tool 呼び出し系列。意図の同一性は判定しない。

    session の中の位置で lookahead を測り、経過秒で打ち切る。系列は (session, subagent の transcript)
    ごとに切る — session だけで切ると、別の agent の呼び出しが deny の直後として対になる。
    """
    streams: dict[tuple[str, str], list[Event]] = collections.defaultdict(list)
    for event in events:
        streams[(event.session_id, event.subagent_transcript)].append(event)
    sequences: list[tuple[Event, dict]] = []
    for stream in streams.values():
        stream.sort(key=lambda event: (event.ts, event.seq))
        for position, denied in enumerate(stream):
            if not denied.outcome.startswith("deny_"):
                continue
            follow_ups = []
            for follow in stream[position + 1:position + 1 + contract.BYPASS_LOOKAHEAD]:
                gap = (None if denied.ts_epoch is None or follow.ts_epoch is None
                       else follow.ts_epoch - denied.ts_epoch)
                if follow.tool != denied.tool or (gap is not None and not 0 <= gap <= contract.BYPASS_MAX_GAP_SECONDS):
                    continue
                follow_ups.append({"tool": follow.tool, "command_head": follow.command_head,
                                   "input_excerpt": follow.input_excerpt, "outcome": follow.outcome,
                                   "gap_seconds": None if gap is None else int(gap), "timestamp": follow.ts})
            if follow_ups:
                sequences.append((denied, {
                    "session_id": denied.session_id, "denied_at": denied.ts, "denied_tool": denied.tool,
                    "denied_command_head": denied.command_head, "denied_input_excerpt": denied.input_excerpt,
                    "denied_outcome": denied.outcome, "denial_kind": denied.denial_kind,
                    "denial_reason_label": denied.denial_reason_label, "cwd": denied.cwd, "follow_ups": follow_ups}))
    sequences.sort(key=lambda item: item[0].seq)
    sequences.sort(key=lambda item: item[0].ts, reverse=True)
    return [sequence for _denied, sequence in sequences]


def build_guard_reverse(events: list[Event]) -> list[dict]:
    """guard 系 deny (自動モード分類器) の Reason label 別内訳 + 代表文言。"""
    groups: dict[tuple, list[Event]] = collections.defaultdict(list)
    for event in events:
        if event.denial_kind in ("automode-blocked", "automode-unavailable"):
            groups[(event.denial_kind, event.denial_reason_label)].append(event)
    rows = []
    for (kind, label), group in groups.items():
        latest = sorted(group, key=lambda event: event.ts, reverse=True)[:3]
        rows.append({"denial_kind": kind, "reason_label": label, "deny_count": len(group),
                     "samples": [{"session_id": event.session_id, "timestamp": event.ts, "tool": event.tool,
                                  "input_excerpt": event.input_excerpt, "cwd": event.cwd} for event in latest]})
    rows.sort(key=lambda row: (-row["deny_count"], str(row["denial_kind"]), str(row["reason_label"])))
    return rows


def _percentile(values: list[int], ratio: float) -> int:
    """values の分位点 (最近傍)。空なら 0。"""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))]


def _firing_stats(firings: list[dict]) -> dict:
    durations = [f["duration_ms"] for f in firings if f["duration_ms"] is not None]
    exit_codes = collections.Counter(f["exit_code"] for f in firings if f["exit_code"] is not None)
    timestamps = sorted(f["ts"] for f in firings if f["ts"])
    return {
        "fire_count": len(firings),
        "distinct_sessions": len({f["session_id"] for f in firings if f["session_id"]}),
        "exit_codes": {str(code): count for code, count in sorted(exit_codes.items())},
        "nonzero_exit_count": sum(c for code, c in exit_codes.items() if code != 0),
        "timed_out_count": sum(1 for f in firings if f["timed_out"]),
        "duration_ms": {
            "observed": len(durations),
            "p50": _percentile(durations, 0.5),
            "p95": _percentile(durations, 0.95),
            "max": max(durations) if durations else 0,
            "total": sum(durations),
        },
        "last_fired_at": timestamps[-1] if timestamps else "",
    }


def _per_call_stats(per_call: list[int]) -> dict:
    return {"calls": len(per_call), "p50": _percentile(per_call, 0.5), "p90": _percentile(per_call, 0.9),
            "max": max(per_call) if per_call else 0}


def _pre_tool_use_per_call(configured: list[dict], firings: list[dict], command_keys: dict[str, str]) -> list[dict]:
    """tool 呼び出し 1 回あたりの PreToolUse hook 群の所要を tool ごとに分位点で返す。

    同じ tool 呼び出しの hook は並列に走るので、1 回分の所要は所要の max。`non_global` は user settings に
    だけ登録された hook と、設定に紐づかない firing を除いた max。
    """
    scopes: dict[str, set[str]] = collections.defaultdict(set)
    for entry in configured:
        if entry["hook_event"] == "PreToolUse":
            scopes[entry["command_key"]].add(entry["scope"])
    per_call_all: dict[tuple[str, str], int] = {}
    per_call_repo: dict[tuple[str, str], int] = {}
    for firing in firings:
        duration = firing["duration_ms"]
        if firing["hook_event"] != "PreToolUse" or duration is None or not firing["tool_use_id"]:
            continue
        call = (firing["hook_tool"], firing["tool_use_id"])
        per_call_all[call] = max(per_call_all.get(call, 0), duration)
        if scopes.get(command_keys[firing["command"]], set()) - {"global"}:
            per_call_repo[call] = max(per_call_repo.get(call, 0), duration)
    grouped: dict[str, dict[str, list[int]]] = collections.defaultdict(lambda: {"all": [], "non_global": []})
    for (tool, _), ms in per_call_all.items():
        grouped[tool]["all"].append(ms)
    for (tool, _), ms in per_call_repo.items():
        grouped[tool]["non_global"].append(ms)
    rows = [{"tool": tool, "all": _per_call_stats(v["all"]), "non_global": _per_call_stats(v["non_global"])}
            for tool, v in grouped.items()]
    rows.sort(key=lambda row: (-row["all"]["calls"], row["tool"]))
    return rows


def aggregate_hook_activity(configured: list[dict], firings: list[dict]) -> dict:
    """設定側の分母 × fire 実績。bucket は付けない。

    紐づけは (event, command basename) を第一キーにし、command を持たない fire は hook の名前で引き当てる
    (`matched_by` に手段を残す)。
    """
    by_command: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    by_name: dict[str, list[dict]] = collections.defaultdict(list)
    command_keys: dict[str, str] = {}
    for firing in firings:
        command = firing["command"]
        if command not in command_keys:
            command_keys[command] = matcher.hook_command_key(command)
        firing["command_key"] = command_keys[command]
        if firing["command_key"]:
            by_command[(firing["hook_event"], firing["command_key"])].append(firing)
        else:
            by_name[firing["hook_name"]].append(firing)

    # 同じ key を複数の設定 unit が共有すると、同じ firing が両方の row に載る。
    # 数を割り振る根拠が transcript に無いので、按分せず共有である事実を出す
    key_owners = collections.Counter((entry["hook_event"], entry["command_key"]) for entry in configured)
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
            # event 名だけの表記は matcher が `*` の設定で、どの hook の fire かを名前から引けない
            if name in by_name and name != entry["hook_event"]:
                matched.extend(by_name[name])
                used_names.add(name)
                matched_by = matched_by or "hook_name"
        stats = _firing_stats(matched)
        if matched_by is None:
            # 無出力で pass する hook は記録を残さないので、0 件は「発火なし」と「観測に残らなかった」を
            # 区別しない。0 と混同させず、観測不能を null で出す
            stats["fire_count"] = None
        rows.append({
            **{k: entry[k] for k in ("hook_event", "matchers", "hook_names", "command", "command_key",
                                     "source_path", "source", "scope")},
            "matched_by": matched_by,
            "key_collision": key_owners[key] > 1,
            **stats,
        })
    rows.sort(key=lambda row: (-1 if row["fire_count"] is None else row["fire_count"],
                               row["hook_event"], row["command_key"]))

    unlisted: dict[str, list[dict]] = collections.defaultdict(list)
    for (event, key), group in by_command.items():
        if (event, key) not in used_command_keys:
            unlisted[f"{event}::{key}"].extend(group)
    for name, group in by_name.items():
        if name not in used_names:
            unlisted[f"{name}::"].extend(group)
    unlisted_rows = [{"hook_event": group[0]["hook_event"], "hook_name": group[0]["hook_name"],
                      "command_key": group[0]["command_key"], **_firing_stats(group)}
                     for group in unlisted.values()]
    unlisted_rows.sort(key=lambda row: (-row["fire_count"], row["hook_name"]))

    return {
        "configured": rows,
        "observed_unlisted": unlisted_rows,
        "pre_tool_use_per_call": _pre_tool_use_per_call(configured, firings, command_keys),
        "totals": {
            "configured_units": len(rows),
            "unobserved_units": sum(1 for r in rows if r["fire_count"] is None),
            "key_collision_units": sum(1 for r in rows if r["key_collision"]),
            "total_firings": len(firings),
            "nonzero_exit_firings": sum(1 for f in firings if f["exit_code"] not in (None, 0)),
            "timed_out_firings": sum(1 for f in firings if f["timed_out"]),
        },
        "observability": contract.HOOK_OBSERVABILITY,
    }


def _hard_deny(counts: Mapping[str, int]) -> int:
    return sum(counts.get(key, 0) for key in contract.HARD_DENY_OUTCOMES)


def _half_stats(counts: dict[str, int]) -> dict:
    match_count = sum(counts.values())
    hard_deny = _hard_deny(counts)
    return {"match_count": match_count, "hard_deny_count": hard_deny,
            "hard_deny_share": (hard_deny / match_count) if match_count else None}


def _window_split(row: dict) -> dict:
    """観測窓を二分した前半 / 後半の hard deny 比率と変化点フラグ。

    `shifted` は 3 値で、null (判定不能) を false (変化なし) に潰さない。
    """
    early = _half_stats(row["outcome_breakdown_early"])
    late = _half_stats(row["outcome_breakdown_late"])
    comparable = min(early["match_count"], late["match_count"]) >= contract.WINDOW_SPLIT_MIN_MATCH
    delta = (None if early["hard_deny_share"] is None or late["hard_deny_share"] is None
             else abs(early["hard_deny_share"] - late["hard_deny_share"]))
    return {
        "early": {**early, "hard_deny_share": None if early["hard_deny_share"] is None
                  else round(early["hard_deny_share"], 2)},
        "late": {**late, "hard_deny_share": None if late["hard_deny_share"] is None
                 else round(late["hard_deny_share"], 2)},
        "hard_deny_share_delta": None if delta is None else round(delta, 2),
        # 0.7 - 0.4 は float では 0.3 を割るので、誤差を落としてから閾値と比べる
        "shifted": round(delta, 9) >= contract.WINDOW_SPLIT_MIN_SHARE_DELTA if comparable else None,
    }


def build_derived_views(axis_a: list[dict], axis_b: list[dict], bypass_sequences: list[dict]) -> dict:
    """読み手が典型的に必要とする view を決定的に前計算する。意味論は `contract.DERIVED_VIEW_SEMANTICS`。"""
    zero_match = [{"entry": row["entry"], "category": row["category"], "scope": row["scope"],
                   "matcher_confidence": row["matcher_confidence"], "unmatchable_reason": row["unmatchable_reason"]}
                  for row in axis_a if row["match_count"] == 0]

    high_deny: list[dict] = []
    for row in axis_a:
        if row["match_count"] < contract.HIGH_DENY_MIN_MATCH:
            continue
        hard_deny = _hard_deny(row["outcome_breakdown"])
        share = hard_deny / row["match_count"]
        if share >= contract.HIGH_DENY_MIN_RATIO:
            high_deny.append({
                "entry": row["entry"], "category": row["category"], "scope": row["scope"],
                "match_count": row["match_count"], "hard_deny_count": hard_deny, "hard_deny_share": round(share, 2),
                "window_split": _window_split(row), "outcome_breakdown": row["outcome_breakdown"],
                "sample_matched": row["sample_matched"],
            })
    high_deny.sort(key=lambda row: (-row["hard_deny_share"], -row["match_count"], row["entry"]))

    units: list[dict] = []
    omitted = 0
    for row in axis_b:
        # 収載床は unit 全体の件数ではなく未被覆件数に当てる。閾値の値は policy なので動かさない
        if row["uncovered_event_count"] < contract.UNLISTED_MIN_COUNT:
            continue
        if not (row["tool"] == "Bash" or row["tool"].startswith("mcp__")
                or row["outcomes"].get("deny_permission-rule", 0) > 0):
            omitted += 1
            continue
        units.append(row)
    # 総件数で並べると、ほぼ全件が収載済みの巨大 unit が全件未収載の小さな unit を押し出す
    units.sort(key=lambda row: (-row["uncovered_event_count"], -row["count"], row["tool"], row["command_head"]))

    grouped: dict[tuple, dict] = {}
    for sequence in bypass_sequences:
        key = (sequence["denial_kind"], sequence["denied_tool"], sequence["denied_command_head"])
        group = grouped.setdefault(key, {
            "denial_kind": sequence["denial_kind"], "denied_tool": sequence["denied_tool"],
            "denied_command_head": sequence["denied_command_head"], "count": 0,
            "fast_success_follow_up_count": 0, "latest_denied_at": sequence["denied_at"]})
        group["count"] += 1
        group["latest_denied_at"] = max(group["latest_denied_at"], sequence["denied_at"])
        first = sequence["follow_ups"][0]
        if (first["outcome"] == "success" and first["gap_seconds"] is not None
                and first["gap_seconds"] <= contract.FOLLOWUP_FAST_GAP_SECONDS):
            group["fast_success_follow_up_count"] += 1
    bypass_grouped = sorted(grouped.values(), key=lambda group: (-group["count"], str(group["denial_kind"]),
                                                                 group["denied_tool"], group["denied_command_head"]))
    return {
        "axis_a_zero_match": zero_match,
        "axis_a_high_deny_share": high_deny,
        "axis_b_unlisted_frequent": {"units": units[:contract.DERIVED_TOP_N],
                                     "omitted_non_permission_units": omitted},
        "bypass_grouped": bypass_grouped[:contract.DERIVED_TOP_N],
    }


def build_bypass_group_samples(bypass_grouped: list[dict], sequences: list[dict]) -> list[dict]:
    """bypass_grouped の top group ごとに代表系列を機械的に選ぶ (最新から N 件)。"""
    out = []
    for group in bypass_grouped[:contract.BYPASS_SAMPLE_GROUPS]:
        matched = [sequence for sequence in sequences
                   if sequence["denial_kind"] == group["denial_kind"]
                   and sequence["denied_tool"] == group["denied_tool"]
                   and sequence["denied_command_head"] == group["denied_command_head"]]
        out.append({**group, "samples": matched[:contract.BYPASS_SAMPLES_PER_GROUP]})
    return out


def _summarize_settings_sources(described: list[dict], entries: list[settings_mod.PermissionEntry]) -> list[dict]:
    """section の分母 path × 件数。列挙した path から組む — entry から逆算すると、在るのに 0 件だった層と
    読まなかった層が同じ「行が無い」に潰れる。
    """
    grouped: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for entry in entries:
        grouped[entry.source_path][entry.category] += 1
    rows = [{
        "path": row["path"], "resolved_path": row["resolved_path"], "scope": row["scope"], "exists": row["exists"],
        "read": row["read"], "reason": row["reason"],
        "allow_count": grouped[row["path"]].get("allow", 0),
        "deny_count": grouped[row["path"]].get("deny", 0),
        "ask_count": grouped[row["path"]].get("ask", 0),
        "sandbox_excluded_commands_count": grouped[row["path"]].get(settings_mod.SANDBOX_CATEGORY, 0),
    } for row in described if row["reason"] in SECTION_DENOMINATOR_REASONS]
    rows.sort(key=lambda row: (row["scope"], row["path"]))
    return rows


def build_section(name: str, events: list[Event], repo_root: Path, split_epoch: float) -> tuple[dict, list]:
    """section 1 つ分の集計と、その section の設定 entry (rule 評価の入力)。

    rule 評価は mart 全体の総実行数 (判定可能性) を要するので、ここでは行わない。
    """
    roots = _scope_roots(name, repo_root)
    scoped = [event for event in events if not roots or matcher.cwd_in_scope(event.cwd, roots)]
    entries = settings_mod.collect_permission_entries(name, repo_root, GLOBAL_SETTINGS)
    matched = _entry_matches(entries, scoped)
    coverage = axis_b_coverage(entries, matched, scoped)
    if name == "global":
        global_coverage = coverage
    else:
        global_entries = settings_mod.collect_permission_entries("global", repo_root, GLOBAL_SETTINGS)
        global_coverage = axis_b_coverage(global_entries, _entry_matches(global_entries, scoped), scoped)
    axis_a = build_axis_a(entries, matched, scoped, split_epoch)
    axis_b = build_axis_b(scoped, coverage, global_coverage)
    bypass = build_bypass_sequences(scoped)
    return {
        "settings_sources": _summarize_settings_sources(
            settings_mod.describe_settings_paths(name, repo_root, GLOBAL_SETTINGS), entries),
        "event_count": len(scoped),
        "distinct_sessions": len({event.session_id for event in scoped}),
        "outcome_totals": dict(collections.Counter(event.outcome for event in scoped)),
        "axis_a_pattern_matches": axis_a,
        "axis_b_actual_usage": axis_b,
        "guard_reverse_lookup": build_guard_reverse(scoped),
        "derived_views": build_derived_views(axis_a, axis_b, bypass),
        "bypass_sequences": bypass,
    }, entries


def rule_candidates(run: QueryRun, section: dict, entries: list[settings_mod.PermissionEntry],
                    sufficient: bool, total_events: int) -> list[dict]:
    """設定 entry を view の macro へ渡し、候補と near miss を受ける。bucket は確定しない。

    同じ entry が複数の層に書かれうるので、unit には行の番号を渡し、結果を行へ戻す。
    """
    axis_a = section["axis_a_pattern_matches"]
    sandbox_pairs = {(entry.tool, entry.pattern) for entry in entries if entry.category == settings_mod.SANDBOX_CATEGORY}
    parsed = {(entry.raw, entry.category): (entry.tool, entry.pattern) for entry in entries}
    run.con.execute(
        "CREATE OR REPLACE TEMP TABLE permission_units AS SELECT "
        "unnest(?::VARCHAR[]) AS unit, unnest(?::VARCHAR[]) AS category, unnest(?::BIGINT[]) AS match_count, "
        "unnest(?::VARCHAR[]) AS matcher_confidence, unnest(?::VARCHAR[]) AS unmatchable_reason, "
        "unnest(?::BIGINT[]) AS unobserved_input_count, unnest(?::BOOLEAN[]) AS sandbox_pair, "
        "unnest(?::BIGINT[]) AS hard_deny_count, unnest(?::BIGINT[]) AS compound_command_deny_count, "
        "? ::BIGINT AS total_events, ?::BOOLEAN AS sufficient",
        [[str(number) for number in range(len(axis_a))], [row["category"] for row in axis_a],
         [row["match_count"] for row in axis_a], [row["matcher_confidence"] for row in axis_a],
         [row["unmatchable_reason"] for row in axis_a], [row["unobserved_input_count"] for row in axis_a],
         [parsed.get((row["entry"], row["category"]), ("", "")) in sandbox_pairs for row in axis_a],
         [_hard_deny(row["outcome_breakdown"]) for row in axis_a],
         [row["compound_command_deny_count"] for row in axis_a], total_events, sufficient])
    verdicts = sorted(run.select("SELECT * FROM lake_rule_permissions('permission_units', $min_match, $min_ratio)",
                                 min_match=contract.HIGH_DENY_MIN_MATCH, min_ratio=contract.HIGH_DENY_MIN_RATIO),
                      key=lambda row: int(row["unit"]))
    out = []
    for verdict in verdicts:
        row = axis_a[int(verdict["unit"])]
        out.append({
            **verdict,
            "unit": row["entry"],
            "rule_inputs": json.loads(verdict["rule_inputs"]),
            "near_misses": [{**miss, "inputs": json.loads(miss["inputs"])} for miss in verdict["near_misses"]],
            "category": row["category"], "scope": row["scope"], "source_path": row["source_path"],
        })
    return out


def _stamp(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _population(params: Mapping[str, str]) -> tuple[str, ...]:
    """lake を開く前に条件を確かめる。"""
    if params["section"] not in SECTIONS:
        raise Stopped("bad_argument", f"section は {sorted(SECTIONS)} のどれかで渡してください")
    if params["transcript_kind"] not in POPULATIONS:
        raise Stopped("bad_argument", f"transcript_kind は {sorted(POPULATIONS)} のどれかで渡してください")
    return POPULATIONS[params["transcript_kind"]]


def build(run: QueryRun) -> list[dict]:
    if run.window.since is None or run.window.until is None:
        raise Stopped("bad_argument", "permissions は --since と --until の両方が要ります (窓を前後半に割るため)")
    section_param = run.params["section"]
    # cwd は絶対 path なので、相対の値のまま比べると project section が黙って 0 件になる
    repo_root = Path(os.path.abspath(os.path.expanduser(run.params["repo_root"] or os.getcwd())))
    midpoint = (run.window.since + run.window.until) / 2
    events = _events(run)
    firings = run.select(HOOK_FIRINGS)
    built = {name: build_section(name, events, repo_root, midpoint) for name in SECTIONS[section_param]}
    sections = {name: section for name, (section, _entries) in built.items()}
    # section all の project は global の部分集合なので、足すと同じ実行を 2 度数える
    total_events = (sections["global"] if "global" in sections else sections["project"])["event_count"]
    sufficient = total_events >= contract.DEFAULT_SUFFICIENT_THRESHOLD
    # 個別 0 件を読む母数はその section の実行で、他の repo の実行は project の entry の分母にならない
    candidates = {name: rule_candidates(run, section, entries,
                                        section["event_count"] >= contract.DEFAULT_SUFFICIENT_THRESHOLD,
                                        section["event_count"])
                  for name, (section, entries) in built.items()}

    def per_section(pick) -> dict:
        return {name: pick(name, section) for name, section in sections.items()}

    hooks = settings_mod.collect_hook_entries(repo_root, GLOBAL_SETTINGS, CONFIG_DIR)
    bodies = {
        "meta": {
            "observation_window": {"start": _stamp(run.window.since), "end": _stamp(run.window.until),
                                   # window_split がここで前半 / 後半を切る
                                   "midpoint": _stamp(midpoint)},
            "section": section_param,
            "transcript_kind": run.params["transcript_kind"],
            "repo_root": str(repo_root.resolve()),
            "global_settings": str(GLOBAL_SETTINGS),
            "settings_denominator": settings_mod.describe_settings_paths(section_param, repo_root, GLOBAL_SETTINGS),
            "total_events": total_events,
            "sufficient_threshold": contract.DEFAULT_SUFFICIENT_THRESHOLD,
            "sufficient_for_relative_judgment": sufficient,
            "sections": per_section(lambda _name, section: {
                key: section[key] for key in ("settings_sources", "event_count", "distinct_sessions", "outcome_totals")}),
        },
        "derived_views": per_section(lambda _name, section: {
            "derived_views": section["derived_views"], "guard_reverse_lookup": section["guard_reverse_lookup"]}),
        "rule_candidates": per_section(lambda name, _section: candidates[name]),
        "axis_a": per_section(lambda _name, section: section["axis_a_pattern_matches"]),
        "bypass_samples": per_section(lambda _name, section: build_bypass_group_samples(
            section["derived_views"]["bypass_grouped"], section["bypass_sequences"])),
        # hook は section (cwd scope) を持たない窓全体の観測
        "hooks": aggregate_hook_activity(hooks, firings),
    }
    return [{"part": part["part"], "body": bodies[part["part"]]} for part in contract.PARTS]


QUERY = NamedQuery(contract=contract.CONTRACT,
                   params={"section": "project", "transcript_kind": "main", "repo_root": ""},
                   population=_population, build=build)
