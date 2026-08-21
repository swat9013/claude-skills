"""permissions mart の組み立てと書き出し (**唯一 I/O を持つ層**)。

`scan_permissions` tool の実体。store への query 結果を `00-meta` 〜 `40-hooks` の
分割ファイルへ組み替え、**path だけを返す** (mart 本体は context に載せない)。

原則 (ADR 0011 の 3 層分離): **観測・集計は決定的に、判断は人間に、LLM は文章の
具体化のみ**。決定的ルールの評価は `rules.py` が行うが、本 module も rules も
**bucket (revoke / promote / refine / sandbox / keep) を確定しない** — 出すのは
候補 (`bucket_candidate`) と導出過程 (`rule_fired` / `rule_inputs`) と未判定条件
(`open_predicates`) までで、確定は LLM 段階、最終採否は人間
([ADR 0032](../../../../docs/adr/0032-policy-free-refinement-deterministic-rules.md)
の出力契約)。

section: project (cwd × 当該 repo 実績) / global (`~/.claude/settings.json` ×
全 repo 実績) / all。
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from adapter.transcript import resolve_now
from artifacts import prepare_output_dir
from marts import load_statements
from store import ingest
from store import store as store_mod

from . import contract, rules, settings as settings_mod, udf

QUERY_PATH = Path(__file__).resolve().parent / "query.sql"

DEFAULT_TRANSCRIPTS_DIR = Path("~/.claude/projects").expanduser()
DEFAULT_GLOBAL_SETTINGS = Path("~/.claude/settings.json").expanduser()
DEFAULT_CONFIG_DIR = Path("~/.claude").expanduser()
DEFAULT_OUTPUT_DIR = Path("/tmp/inventory-permissions")
DEFAULT_DAYS = contract.DEFAULT_DAYS

# `refined_event` の SELECT 列順。scoped_event への INSERT 列順と 1:1 で対応する。
SCOPED_EVENT_COLUMNS = (
    "tool", "command", "command_head", "target_path", "input_excerpt",
    "session_id", "ts", "ts_epoch", "cwd", "outcome", "denial_kind",
    "denial_reason_label",
)

# section の `settings_sources` に出す reason (= その section の分母そのもの)。
# `cross_layer_match` (global 突合用) と `out_of_section` / `not_a_settings_layer` は
# section の分母ではないので meta の `settings_denominator` 側だけに残す。
SECTION_DENOMINATOR_REASONS = ("read", "absent", "unparsed")

PERMISSION_ENTRY_COLUMNS = (
    "raw", "category", "source_path", "scope", "tool", "pattern", "confidence",
    "match_kind",
)


@dataclasses.dataclass(frozen=True)
class Request:
    """1 回の観測要求。**引数の解釈をここで終わらせる** (以降は値だけを渡す)。"""

    section: str = "project"
    days: int = DEFAULT_DAYS
    repo_root: Path = dataclasses.field(default_factory=Path.cwd)
    transcripts_dir: Path = DEFAULT_TRANSCRIPTS_DIR
    global_settings: Path = DEFAULT_GLOBAL_SETTINGS
    config_dir: Path = DEFAULT_CONFIG_DIR
    output_dir: Path = DEFAULT_OUTPUT_DIR
    now: str | None = None
    cache_dir: Path | None = None
    sufficient_threshold: int = contract.DEFAULT_SUFFICIENT_THRESHOLD
    bypass_lookahead: int = contract.BYPASS_LOOKAHEAD
    bypass_max_gap_seconds: int = contract.BYPASS_MAX_GAP_SECONDS

    def sections(self) -> tuple[str, ...]:
        return ("project", "global") if self.section == "all" else (self.section,)


# --- store への問い合わせ ----------------------------------------------------

def _scope_roots(section: str, repo_root: Path) -> str:
    """section の cwd スコープ (改行区切りの root 一覧。global は空文字)。

    **解決前と解決後の両表記を渡す** — 片側比較だと symlink 経由で開いた repo の
    project section が黙って 0 件になる。
    """
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


def _load_scoped_events(conn: sqlite3.Connection, statements: dict[str, str],
                        cutoff_epoch: float, scope_roots: str) -> None:
    conn.execute(statements["clear_scoped_event"])
    conn.execute(
        f"INSERT INTO scoped_event ({', '.join(SCOPED_EVENT_COLUMNS)}) "
        + statements["refined_event"],
        {"cutoff_epoch": cutoff_epoch, "scope_roots": scope_roots},
    )


def _load_permission_entries(conn: sqlite3.Connection, statements: dict[str, str],
                             entries: list[settings_mod.PermissionEntry]) -> None:
    conn.execute(statements["clear_permission_entry"])
    conn.executemany(
        f"INSERT INTO permission_entry ({', '.join(PERMISSION_ENTRY_COLUMNS)}) "
        f"VALUES ({', '.join('?' * len(PERMISSION_ENTRY_COLUMNS))})",
        [tuple(getattr(entry, column) for column in PERMISSION_ENTRY_COLUMNS)
         for entry in entries],
    )


# --- 集計の組み立て ----------------------------------------------------------

def build_axis_a(conn: sqlite3.Connection, statements: dict[str, str],
                 entries: list[settings_mod.PermissionEntry],
                 split_epoch: float) -> list[dict]:
    """設定 entry 別の match_count / outcome_breakdown / sample_matched。

    query は (entry, tool, command_head, outcome, window_half) の 1 粒度で返る。
    outcome 内訳と代表 (tool, command_head) はそれを 2 方向へ畳み直したもので、
    **どちらも初出順 (`first_seq`) を tie-break に使う** — 同数のとき「lake で先に
    現れた方が上」になり、順序が実行のたびに揺れない。

    `compound_command_deny_count` は deny 系 outcome のうち**複合コマンド行**由来の
    件数。1 行に allow 対象と deny 対象が混在すると call 全体の deny が行内の全 entry
    へ載るため、この数が entry の高 deny 比率の別解を示す (ADR 0032 の誤計上検査)。

    `outcome_breakdown_early` / `_late` は観測窓を `split_epoch` で二分した内訳
    (#584)。窓内で挙動が変わった entry では窓全体の比率が変更前後の平均になり entry の
    性質を表さないため、**比率と同じ場所に変化の有無を読める材料を置く**。ts 欠損
    event はどちらにも入らない (early + late < match_count がありうる)。
    """
    breakdown: dict[int, dict[str, int]] = collections.defaultdict(dict)
    half_breakdown: dict[str, dict[int, dict[str, int]]] = {
        "early": collections.defaultdict(dict), "late": collections.defaultdict(dict)}
    outcome_first_seq: dict[tuple[int, str], int] = {}
    compound_deny: dict[int, int] = collections.defaultdict(int)
    combos: dict[tuple[int, str, str], dict] = {}
    for row in conn.execute(statements["axis_a_matches"],
                            {"split_epoch": split_epoch}):
        if row["outcome"] is None:
            continue
        entry_no, outcome = row["entry_no"], row["outcome"]
        counts = breakdown[entry_no]
        counts[outcome] = counts.get(outcome, 0) + row["n"]
        if row["window_half"] is not None:
            half_counts = half_breakdown[row["window_half"]][entry_no]
            half_counts[outcome] = half_counts.get(outcome, 0) + row["n"]
        if outcome.startswith("deny_"):
            compound_deny[entry_no] += row["compound_n"] or 0
        key = (entry_no, outcome)
        outcome_first_seq[key] = min(outcome_first_seq.get(key, row["first_seq"]),
                                     row["first_seq"])
        combo_key = (entry_no, row["tool"], row["command_head"])
        combo = combos.setdefault(combo_key, {"count": 0,
                                              "first_seq": row["first_seq"]})
        combo["count"] += row["n"]
        combo["first_seq"] = min(combo["first_seq"], row["first_seq"])

    samples: dict[int, list[dict]] = collections.defaultdict(list)
    for (entry_no, tool, command_head), combo in sorted(
            combos.items(), key=lambda item: (item[0][0], -item[1]["count"],
                                              item[1]["first_seq"])):
        bucket = samples[entry_no]
        if len(bucket) < 3:
            bucket.append({"tool": tool, "command_head": command_head,
                           "count": combo["count"]})

    rows: list[dict] = []
    for entry_no, entry in enumerate(entries, start=1):
        def _ordered(counts: dict[str, int], entry_no: int = entry_no) -> dict:
            """outcome の並びを entry 内で揃える (初出順。半分ごとに揺らさない)。"""
            return dict(sorted(counts.items(),
                               key=lambda item: outcome_first_seq[(entry_no, item[0])]))

        outcomes = _ordered(breakdown.get(entry_no, {}))
        rows.append({
            "entry": entry.raw,
            "category": entry.category,
            "source_path": entry.source_path,
            "scope": entry.scope,
            "match_kind": entry.match_kind,
            "matcher_confidence": entry.confidence,
            "match_count": sum(outcomes.values()),
            "outcome_breakdown": outcomes,
            "outcome_breakdown_early": _ordered(
                half_breakdown["early"].get(entry_no, {})),
            "outcome_breakdown_late": _ordered(
                half_breakdown["late"].get(entry_no, {})),
            "compound_command_deny_count": compound_deny.get(entry_no, 0),
            "sample_matched": samples.get(entry_no, []),
        })
    rows.sort(key=lambda row: (-row["match_count"], row["category"], row["entry"]))
    return rows


def axis_b_matches(conn: sqlite3.Connection,
                   statements: dict[str, str]) -> dict[tuple[str, str], list[dict]]:
    """B 軸 key × **現在 load 済みの** permission_entry の match。

    どの層を突き合わせるかは呼び出し側が `permission_entry` に何を積んだかで決まる
    (section の config / global の config)。
    """
    matches: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for row in conn.execute(statements["axis_b_config_matches"]):
        matches[(row["tool"], row["command_head"])].append(
            {"entry": row["entry_raw"], "category": row["category"],
             "scope": row["scope"]})
    return matches


def build_axis_b(conn: sqlite3.Connection, statements: dict[str, str],
                 matches: dict[tuple[str, str], list[dict]],
                 global_matches: dict[tuple[str, str], list[dict]]) -> list[dict]:
    """tool × command_head × outcome の集計 + 対応 entry。

    `config_matches` は当該 section の config (project section なら project +
    project_local) だけを見る。**それが空でも「どこにも収載されていない」ではない**
    ので、global 層の match を `global_config_matches` に別列で出す (#513: 空の
    `config_matches` を promote の証拠に使うと、global で既に許可されている entry を
    section 層へ重複追加する提案になる)。section `global` では両者が同じ集合を指す。
    """
    grouped: dict[tuple[str, str], dict] = {}
    for row in conn.execute(statements["axis_b"]):
        key = (row["tool"], row["command_head"])
        bucket = grouped.setdefault(key, {"count": 0, "outcomes": {},
                                          "first_seq": row["first_seq"]})
        bucket["count"] += row["n"]
        bucket["outcomes"][row["outcome"]] = row["n"]
        bucket["first_seq"] = min(bucket["first_seq"], row["first_seq"])

    ordered = sorted(grouped.items(), key=lambda item: (-item[1]["count"],
                                                        item[1]["first_seq"]))
    return [
        {
            "tool": tool,
            "command_head": command_head,
            "count": bucket["count"],
            "outcomes": dict(sorted(bucket["outcomes"].items(),
                                    key=lambda kv: -bucket["outcomes"][kv[0]])),
            "config_matches": [match["entry"]
                               for match in matches.get((tool, command_head), [])],
            "global_config_matches": global_matches.get((tool, command_head), []),
        }
        for (tool, command_head), bucket in ordered
    ]


def build_bypass_sequences(conn: sqlite3.Connection, statements: dict[str, str],
                           lookahead: int, max_gap: int) -> list[dict]:
    """deny 直後の同 tool 呼び出し系列。**意図の同一性は判定しない**。"""
    sequences: dict[int, dict] = {}
    for row in conn.execute(statements["bypass_pairs"],
                            {"lookahead": lookahead, "max_gap": max_gap}):
        sequence = sequences.get(row["denied_seq"])
        if sequence is None:
            sequence = {
                "session_id": row["session_id"],
                "denied_at": row["denied_at"],
                "denied_tool": row["denied_tool"],
                "denied_command_head": row["denied_command_head"],
                "denied_input_excerpt": row["denied_input_excerpt"],
                "denied_outcome": row["denied_outcome"],
                "denial_kind": row["denial_kind"],
                "denial_reason_label": row["denial_reason_label"],
                "cwd": row["cwd"],
                "follow_ups": [],
            }
            sequences[row["denied_seq"]] = sequence
        sequence["follow_ups"].append({
            "tool": row["tool"],
            "command_head": row["command_head"],
            "input_excerpt": row["input_excerpt"],
            "outcome": row["outcome"],
            "gap_seconds": row["gap_seconds"],
            "timestamp": row["ts"],
        })
    return list(sequences.values())


def build_guard_reverse(conn: sqlite3.Connection,
                        statements: dict[str, str]) -> list[dict]:
    """guard 系 deny (自動モード分類器) の Reason label 別内訳 + 代表文言。"""
    samples: dict[tuple, list[dict]] = collections.defaultdict(list)
    for row in conn.execute(statements["guard_samples"]):
        bucket = samples[(row["denial_kind"], row["denial_reason_label"])]
        if len(bucket) < 3:
            bucket.append({"session_id": row["session_id"], "timestamp": row["ts"],
                           "tool": row["tool"], "input_excerpt": row["input_excerpt"],
                           "cwd": row["cwd"]})
    rows = [
        {
            "denial_kind": row["denial_kind"],
            "reason_label": row["denial_reason_label"],
            "deny_count": row["deny_count"],
            "samples": samples.get((row["denial_kind"], row["denial_reason_label"]), []),
        }
        for row in conn.execute(statements["guard_reverse"])
    ]
    rows.sort(key=lambda row: (-row["deny_count"], str(row["denial_kind"]),
                               str(row["reason_label"])))
    return rows


# --- hook 設定 × 実績 --------------------------------------------------------

def _percentile(values: list[int], ratio: float) -> int:
    """昇順 values の分位点 (最近傍)。空なら 0。"""
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))
    return ordered[index]


def _firing_stats(firings: list[dict]) -> dict:
    durations = [f["duration_ms"] for f in firings if f["duration_ms"] is not None]
    exit_codes = collections.Counter(
        f["exit_code"] for f in firings if f["exit_code"] is not None)
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


def aggregate_hook_activity(configured: list[dict], firings: list[dict]) -> dict:
    """設定側の分母 × fire 実績。**bucket は付けない** (判定は LLM 段階)。

    紐づけは (event, command basename) を第一キーにする。command を持たない
    attachment は hookName で引き当てる (`matched_by` に手段を残す)。
    """
    by_command: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    by_name: dict[str, list[dict]] = collections.defaultdict(list)
    # 照合キーは command ごとに 1 回だけ求める (実測 4 万 fire / 23 command)
    command_keys: dict[str, str] = {}
    for firing in firings:
        command = firing["command"]
        if command not in command_keys:
            command_keys[command] = udf.hook_command_key(command)
        firing["command_key"] = command_keys[command]
        if firing["command_key"]:
            by_command[(firing["hook_event"], firing["command_key"])].append(firing)
        else:
            by_name[firing["hook_name"]].append(firing)

    # 同一 (event, command_key) を複数の設定 unit が共有すると、同じ firing が両方の
    # row に載る。数を割り振る根拠が transcript に無いので、按分せず**共有である
    # 事実を出す**
    key_owners = collections.Counter(
        (entry["hook_event"], entry["command_key"]) for entry in configured)
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
        stats = _firing_stats(matched)
        if matched_by is None:
            # 無出力で pass する hook は attachment を残さないため、firing 0 件は
            # 「発火なし」と「発火したが観測に残らなかった」を区別しない (#514)。
            # 0 と混同させず、観測不能を null で出す
            stats["fire_count"] = None
        rows.append({
            **{k: entry[k] for k in ("hook_event", "matchers", "hook_names",
                                     "command", "command_key", "source_path",
                                     "source", "scope")},
            "matched_by": matched_by,
            # True なら fire_count は同 key の他 unit と**共有**の値。「この hook は
            # fire していない」は主張できるが「n 回動いた」は主張できない
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
    unlisted_rows = [
        {
            "hook_event": group[0]["hook_event"],
            "hook_name": group[0]["hook_name"],
            "command_key": group[0]["command_key"],
            **_firing_stats(group),
        }
        for group in unlisted.values()
    ]
    unlisted_rows.sort(key=lambda row: (-row["fire_count"], row["hook_name"]))

    return {
        "configured": rows,
        "observed_unlisted": unlisted_rows,
        "totals": {
            "configured_units": len(rows),
            "unobserved_units": sum(1 for r in rows if r["fire_count"] is None),
            "key_collision_units": sum(1 for r in rows if r["key_collision"]),
            "total_firings": len(firings),
            "nonzero_exit_firings": sum(
                1 for f in firings if f["exit_code"] not in (None, 0)),
            "timed_out_firings": sum(1 for f in firings if f["timed_out"]),
        },
        "observability": contract.HOOK_OBSERVABILITY,
    }


# --- derived views -----------------------------------------------------------

def _half_stats(counts: dict[str, int]) -> dict:
    """窓の半分の match_count / hard_deny_count / hard_deny_share (生値)。"""
    match_count = sum(counts.values())
    hard_deny = sum(counts.get(key, 0) for key in contract.HARD_DENY_OUTCOMES)
    return {"match_count": match_count, "hard_deny_count": hard_deny,
            "hard_deny_share": (hard_deny / match_count) if match_count else None}


def _window_split(row: dict) -> dict:
    """観測窓を二分した前半 / 後半の hard deny 比率と変化点フラグ (#584)。

    `shifted` は 3 値。**`null` (判定不能) を `false` (変化なし) に潰さない** —
    どちらかの半分が `WINDOW_SPLIT_MIN_MATCH` に満たなければ比較が成立しないので、
    「一様だった」とは言えない。判定不能でも各半分の件数はそのまま出す (なぜ判定
    できないかが row から読めないと `null` が窓全体の平均と同じ不透明さになる)。
    """
    early = _half_stats(row.get("outcome_breakdown_early") or {})
    late = _half_stats(row.get("outcome_breakdown_late") or {})
    comparable = min(early["match_count"],
                     late["match_count"]) >= contract.WINDOW_SPLIT_MIN_MATCH
    delta = (None if early["hard_deny_share"] is None or late["hard_deny_share"] is None
             else abs(early["hard_deny_share"] - late["hard_deny_share"]))
    return {
        "early": {**early, "hard_deny_share": None if early["hard_deny_share"] is None
                  else round(early["hard_deny_share"], 2)},
        "late": {**late, "hard_deny_share": None if late["hard_deny_share"] is None
                 else round(late["hard_deny_share"], 2)},
        "hard_deny_share_delta": None if delta is None else round(delta, 2),
        "shifted": (delta >= contract.WINDOW_SPLIT_MIN_SHARE_DELTA
                    if comparable else None),
    }


def build_derived_views(axis_a: list[dict], axis_b: list[dict],
                        bypass_sequences: list[dict]) -> dict:
    """LLM 段階が典型的に必要とする view を決定的に前計算する (純関数)。

    mart 本体を LLM 段階で ad-hoc に slice する摩擦を避けるための派生集計。意味論は
    `contract.DERIVED_VIEW_SEMANTICS` を単一ソースとし、本関数の出力 key 集合と
    一致させる。**bucket は確定しない** — 決定的ルールの評価は `rules.py`、bucket の
    確定は LLM 段階 (ADR 0032 の出力契約)。
    """
    zero_match = [
        {"entry": row["entry"], "category": row["category"], "scope": row["scope"],
         "matcher_confidence": row["matcher_confidence"]}
        for row in axis_a if row["match_count"] == 0
    ]

    high_deny: list[dict] = []
    for row in axis_a:
        if row["match_count"] < contract.HIGH_DENY_MIN_MATCH:
            continue
        hard_deny = sum(row["outcome_breakdown"].get(key, 0)
                        for key in contract.HARD_DENY_OUTCOMES)
        share = hard_deny / row["match_count"]
        if share >= contract.HIGH_DENY_MIN_RATIO:
            high_deny.append({
                "entry": row["entry"], "category": row["category"],
                "scope": row["scope"], "match_count": row["match_count"],
                "hard_deny_count": hard_deny, "hard_deny_share": round(share, 2),
                "window_split": _window_split(row),
                "outcome_breakdown": row["outcome_breakdown"],
                "sample_matched": row["sample_matched"],
            })
    high_deny.sort(key=lambda row: (-row["hard_deny_share"], -row["match_count"],
                                    row["entry"]))

    units: list[dict] = []
    omitted = 0
    for row in axis_b:
        if row["config_matches"] or row["count"] < contract.UNLISTED_MIN_COUNT:
            continue
        permission_relevant = (
            row["tool"] == "Bash"
            or row["tool"].startswith("mcp__")
            or row["outcomes"].get("deny_permission-rule", 0) > 0
        )
        if not permission_relevant:
            omitted += 1
            continue
        units.append(row)
    units.sort(key=lambda row: (-row["count"], row["tool"], row["command_head"]))

    grouped: dict[tuple, dict] = {}
    for sequence in bypass_sequences:
        key = (sequence.get("denial_kind"), sequence["denied_tool"],
               sequence.get("denied_command_head", ""))
        group = grouped.setdefault(key, {
            "denial_kind": sequence.get("denial_kind"),
            "denied_tool": sequence["denied_tool"],
            "denied_command_head": sequence.get("denied_command_head", ""),
            "count": 0,
            "fast_success_follow_up_count": 0,
            "latest_denied_at": sequence["denied_at"],
        })
        group["count"] += 1
        group["latest_denied_at"] = max(group["latest_denied_at"],
                                        sequence["denied_at"])
        first = sequence["follow_ups"][0] if sequence["follow_ups"] else None
        if (first is not None and first["outcome"] == "success"
                and first.get("gap_seconds") is not None
                and first["gap_seconds"] <= contract.FOLLOWUP_FAST_GAP_SECONDS):
            group["fast_success_follow_up_count"] += 1
    bypass_grouped = sorted(
        grouped.values(),
        key=lambda group: (-group["count"], str(group["denial_kind"]),
                           group["denied_tool"], group["denied_command_head"]),
    )

    return {
        "axis_a_zero_match": zero_match,
        "axis_a_high_deny_share": high_deny,
        "axis_b_unlisted_frequent": {
            "units": units[:contract.DERIVED_TOP_N],
            "omitted_non_permission_units": omitted,
        },
        "bypass_grouped": bypass_grouped[:contract.DERIVED_TOP_N],
    }


def build_bypass_group_samples(bypass_grouped: list[dict],
                               sequences: list[dict]) -> list[dict]:
    """bypass_grouped の top group ごとに代表系列を機械的に選ぶ (最新から N 件)。"""
    out: list[dict] = []
    for group in bypass_grouped[:contract.BYPASS_SAMPLE_GROUPS]:
        matched = [
            sequence for sequence in sequences
            if sequence.get("denial_kind") == group["denial_kind"]
            and sequence["denied_tool"] == group["denied_tool"]
            and sequence.get("denied_command_head", "") == group["denied_command_head"]
        ]
        out.append({**group, "samples": matched[:contract.BYPASS_SAMPLES_PER_GROUP]})
    return out


# --- section 組み立て --------------------------------------------------------

def _summarize_settings_sources(
        described: list[dict],
        entries: list[settings_mod.PermissionEntry]) -> list[dict]:
    """section の分母 path × 件数。

    **列挙した path から組む** — entry から逆算すると、在るのに 0 件だった層と
    そもそも読まなかった層が同じ「行が無い」に潰れる (#583)。
    """
    grouped: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    for entry in entries:
        grouped[entry.source_path][entry.category] += 1
    rows = [
        {
            "path": row["path"],
            "resolved_path": row["resolved_path"],
            "scope": row["scope"],
            "exists": row["exists"],
            "read": row["read"],
            "reason": row["reason"],
            "allow_count": grouped[row["path"]].get("allow", 0),
            "deny_count": grouped[row["path"]].get("deny", 0),
            "ask_count": grouped[row["path"]].get("ask", 0),
            "sandbox_excluded_commands_count": grouped[row["path"]].get(
                settings_mod.SANDBOX_CATEGORY, 0),
        }
        for row in described
        if row["reason"] in SECTION_DENOMINATOR_REASONS
    ]
    rows.sort(key=lambda row: (row["scope"], row["path"]))
    return rows


def build_section(
        conn: sqlite3.Connection, statements: dict[str, str], request: Request,
        section_name: str, cutoff_epoch: float,
        split_epoch: float) -> tuple[dict, list[settings_mod.PermissionEntry]]:
    """section 1 つ分の集計と、その section の設定 entry (rule 評価の入力)。

    entry を返すのは、rule 評価が **mart 全体の総 event 数** (判定可能性) を要し、
    section の組み立て時点ではまだ確定していないため。
    """
    entries = settings_mod.collect_permission_entries(
        section_name, request.repo_root, request.global_settings)
    _load_scoped_events(conn, statements, cutoff_epoch,
                        _scope_roots(section_name, request.repo_root))

    # global 層の突合を**先に**済ませる (#513)。順序が逆だと permission_entry の
    # 最終状態が global の entry になり、以降の A 軸集計・rule 評価が section の
    # entry を正とする前提が崩れる
    global_matches = None
    if section_name != "global":
        _load_permission_entries(conn, statements,
                                 settings_mod.collect_permission_entries(
                                     "global", request.repo_root,
                                     request.global_settings))
        global_matches = axis_b_matches(conn, statements)
    _load_permission_entries(conn, statements, entries)

    summary = conn.execute(statements["event_summary"]).fetchone()
    axis_a = build_axis_a(conn, statements, entries, split_epoch)
    matches = axis_b_matches(conn, statements)
    axis_b = build_axis_b(conn, statements, matches,
                          matches if global_matches is None else global_matches)
    bypass = build_bypass_sequences(conn, statements, request.bypass_lookahead,
                                    request.bypass_max_gap_seconds)
    return {
        "name": section_name,
        "settings_sources": _summarize_settings_sources(
            settings_mod.describe_settings_paths(
                section_name, request.repo_root, request.global_settings),
            entries),
        "event_count": summary["event_count"],
        "distinct_sessions": summary["distinct_sessions"],
        "outcome_totals": {row["outcome"]: row["n"]
                           for row in conn.execute(statements["outcome_totals"])},
        "axis_a_pattern_matches": axis_a,
        "axis_b_actual_usage": axis_b,
        "bypass_sequences": bypass,
        "guard_reverse_lookup": build_guard_reverse(conn, statements),
        "derived_views": build_derived_views(axis_a, axis_b, bypass),
        "rule_candidates": [],
    }, entries


# --- mart 全体 ---------------------------------------------------------------

def _stamp(moment: dt.datetime) -> str:
    """mart に出す ISO timestamp (UTC は `Z` 表記)。6 tool で表記を揃える。"""
    return moment.isoformat().replace("+00:00", "Z")


def build(request: Request) -> dict:
    """store を sync してから mart を組み立てる (ファイル書き出しは含まない)。"""
    now = resolve_now(request.now)
    cutoff = now - dt.timedelta(days=request.days)
    cutoff_epoch = cutoff.timestamp()
    # 変化点フラグ用の固定二分点 (#584)。section・entry を問わず 1 点で、mart の
    # meta にも出す (どこで切ったかが分からないと前後半の内訳を検証できない)
    midpoint = cutoff + (now - cutoff) / 2
    split_epoch = midpoint.timestamp()

    conn, sync_report = ingest.open_synced(
        request.transcripts_dir, cache_dir=request.cache_dir, now=now)
    try:
        udf.register(conn)
        statements = load_statements(QUERY_PATH)
        conn.execute(statements["create_scoped_event"])
        conn.execute(statements["create_scoped_event_index"])
        conn.execute(statements["create_permission_entry"])
        anomalies = store_mod.anomalies(conn)
        firings = [dict(row) for row in
                   conn.execute(statements["hook_firings"],
                                {"cutoff_epoch": cutoff_epoch})]
        built = {
            name: build_section(conn, statements, request, name, cutoff_epoch,
                                split_epoch)
            for name in request.sections()
        }
    finally:
        conn.close()

    sections = {name: section for name, (section, _entries) in built.items()}
    hook_entries = settings_mod.collect_hook_entries(
        request.repo_root, request.global_settings, request.config_dir)
    total_events = sum(section["event_count"] for section in sections.values())
    sufficient = total_events >= request.sufficient_threshold
    # rule 評価は section 集計の**後**。判定可能性 (総 event 数) が mart 全体の
    # 値であり、section 単位では確定しないため
    for name, (section, entries) in built.items():
        section["rule_candidates"] = rules.evaluate(
            section["axis_a_pattern_matches"], entries, sufficient,
            request.sufficient_threshold, total_events)
    return {
        "meta": {
            "generated_at": _stamp(now),
            "observation_window": {
                "start": _stamp(cutoff),
                "end": _stamp(now),
                "days": request.days,
                # derived view の window_split がここで前半 / 後半を切る (#584)
                "midpoint": _stamp(midpoint),
            },
            "section": request.section,
            "repo_root": str(request.repo_root.resolve()),
            "transcripts_dir": str(request.transcripts_dir),
            "global_settings": str(request.global_settings),
            "settings_denominator": settings_mod.describe_settings_paths(
                request.section, request.repo_root, request.global_settings),
            "total_events": total_events,
            "sufficient_threshold": request.sufficient_threshold,
            "sufficient_for_relative_judgment": sufficient,
            "store": {
                **anomalies,
                "skipped_nested_files": sync_report.skipped_nested_files,
                "synced_files": sync_report.ingested_files,
                "unchanged_files": sync_report.unchanged_files,
                "removed_files": sync_report.removed_files,
            },
            "notes": list(contract.META_NOTES),
        },
        "sections": sections,
        "hook_activity": aggregate_hook_activity(hook_entries, firings),
    }


def split_outputs(mart: dict) -> list[tuple[str, dict]]:
    """mart を LLM 段階が読む順に並べた分割ファイル群へ組み替える。

    読む順・用途・標準フロー可否は `contract.SPLIT_FILES` が単一ソースで、本関数は
    ファイル名 → doc builder の対応のみを持つ。
    """
    sections = mart["sections"]

    def per_section(build_doc) -> dict:
        return {"sections": {name: build_doc(section)
                             for name, section in sections.items()}}

    builders = {
        "00-meta.json": lambda: {
            "meta": mart["meta"],
            "contract": contract.build_contract(
                rules.rule_catalog(mart["meta"]["sufficient_threshold"])),
            **per_section(lambda section: {key: section[key]
                                           for key in contract.SPLIT_SECTION_SUMMARY_KEYS}),
        },
        "10-derived-views.json": lambda: per_section(lambda section: {
            "derived_views": section["derived_views"],
            "guard_reverse_lookup": section["guard_reverse_lookup"],
        }),
        "15-rule-candidates.json": lambda: per_section(lambda section: {
            "rule_candidates": section["rule_candidates"],
        }),
        "20-axis-a.json": lambda: per_section(lambda section: {
            "axis_a_pattern_matches": section["axis_a_pattern_matches"],
        }),
        "30-bypass-samples.json": lambda: per_section(lambda section: {
            "bypass_group_samples": build_bypass_group_samples(
                section["derived_views"]["bypass_grouped"],
                section["bypass_sequences"]),
        }),
        # hook は section (cwd scope) を持たない窓全体の観測なので per_section にしない
        "40-hooks.json": lambda: {"hook_activity": mart["hook_activity"]},
    }
    return [(entry["name"], builders[entry["name"]]())
            for entry in contract.SPLIT_FILES]


def emit(mart: dict, output_dir: Path) -> list[str]:
    """分割ファイルを `run-<timestamp>/` に書き、読む順の path を返す。

    stamp は mart の `generated_at` から起こす — 時刻を引き直すと mart 内の時刻と
    dir 名が秒境界でずれる。
    """
    generated = dt.datetime.fromisoformat(mart["meta"]["generated_at"])
    # 親も締める — run dir だけ 0700 にしても、親を辿れれば dir 名から run が
    # 列挙できる (既存 dir が 0755 で残っている経路がある)
    prepare_output_dir(output_dir)
    run_dir = prepare_output_dir(output_dir / f"run-{generated.strftime('%Y%m%dT%H%M%SZ')}")
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
) -> dict[str, Any]:
    """tool 側の入口。mart は返さず、書いた path と判定可能性の meta だけを返す。"""
    request = Request(
        section=section,
        days=days,
        repo_root=Path(repo_root) if repo_root else Path.cwd(),
        output_dir=Path(output_dir),
        transcripts_dir=Path(transcripts_dir),
        global_settings=Path(global_settings),
        config_dir=Path(config_dir),
        now=now,
    )
    mart = build(request)
    meta = mart["meta"]
    return {
        "paths": emit(mart, request.output_dir),
        "read_order": [entry["name"] for entry in contract.SPLIT_FILES],
        "meta": {
            "section": meta["section"],
            "repo_root": meta["repo_root"],
            "observation_window": meta["observation_window"],
            "total_events": meta["total_events"],
            "sufficient_for_relative_judgment": meta["sufficient_for_relative_judgment"],
            "event_count_by_section": {
                name: section["event_count"]
                for name, section in mart["sections"].items()
            },
            "hook_activity": mart["hook_activity"]["totals"],
            "rule_candidates": {
                name: {
                    "fired": sum(1 for row in section["rule_candidates"]
                                 if row["rule_fired"]),
                    "near_miss_only": sum(1 for row in section["rule_candidates"]
                                          if not row["rule_fired"]),
                }
                for name, section in mart["sections"].items()
            },
            "store": meta["store"],
        },
    }


# --- CLI ---------------------------------------------------------------------
# mart schema を固定するテストと、実 lake に対する検証 (差分説明書) が通す入口。

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", choices=["project", "global", "all"],
                        default="project")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--transcripts-dir", type=Path,
                        default=DEFAULT_TRANSCRIPTS_DIR)
    parser.add_argument("--global-settings", type=Path,
                        default=DEFAULT_GLOBAL_SETTINGS)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="store の置き場。既定 ~/.cache/claude-transcript-ops")
    parser.add_argument("--now", type=str, default=None)
    parser.add_argument("--sufficient-threshold", type=int,
                        default=contract.DEFAULT_SUFFICIENT_THRESHOLD)
    parser.add_argument("--bypass-lookahead", type=int,
                        default=contract.BYPASS_LOOKAHEAD)
    parser.add_argument("--bypass-max-gap-seconds", type=int,
                        default=contract.BYPASS_MAX_GAP_SECONDS)
    parser.add_argument("--stdout-mart", action="store_true",
                        help="ファイルに書かず mart JSON を stdout に出す")
    return parser.parse_args(argv)


def request_from_args(namespace: argparse.Namespace) -> Request:
    return Request(
        section=namespace.section,
        days=namespace.days,
        repo_root=namespace.repo_root,
        transcripts_dir=namespace.transcripts_dir,
        global_settings=namespace.global_settings,
        config_dir=namespace.config_dir,
        output_dir=namespace.output_dir,
        now=namespace.now,
        cache_dir=namespace.cache_dir,
        sufficient_threshold=namespace.sufficient_threshold,
        bypass_lookahead=namespace.bypass_lookahead,
        bypass_max_gap_seconds=namespace.bypass_max_gap_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    namespace = parse_args(argv)
    request = request_from_args(namespace)
    mart = build(request)
    if namespace.stdout_mart:
        json.dump(mart, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    for path in emit(mart, request.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
