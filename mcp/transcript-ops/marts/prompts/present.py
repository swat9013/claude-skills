"""prompts mart の組み立てと書き出し (**唯一 I/O を持つ層**)。

`scan_prompts` / `select_candidates` 2 tool の実体。store への query 結果を mart /
slice へ組み替え、**path だけを返す** (本体は context に載せない)。

本 module の責務は決定的な抽出・絞り込みまで — **「どれがフィードバックか」
「どれが価値観か」の判定は一切行わない**。bucket を知らない生データだけを出し、
判断は棚卸し実行時の人間、文章の具体化のみ LLM が担う (3 段階モデル。運用正本は
plugin repo の docs/steering.md §1)。ADR 0032 の決定的ルール層は本 domain を
対象外にしている (SKILL.md が「決定的シグナルが存在しない」ことを明文化済み)。

repo 解決 (git 呼び出し) は I/O なので UDF に置けない。**post-query で cwd 単位に
1 回だけ行う** (query 層は cwd の値をそのまま返すだけ)。
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
from typing import Any, Callable

from adapter.transcript import (
    resolve_now,
    resolve_repo_at,
    resolve_repo_at_with_cause,
    truncate,
)
from artifacts import prepare_output_dir
from marts import load_statements
from store import ingest
from store import store as store_mod

from . import contract, rules, udf
from .udf import normalize_repo_identifier

QUERY_PATH = Path(__file__).resolve().parent / "query.sql"

DEFAULT_TRANSCRIPTS_DIR = Path("~/.claude/projects").expanduser()
DEFAULT_OUTPUT_DIR = Path("/tmp/inventory-values")
DEFAULT_DAYS = contract.DEFAULT_DAYS
DEFAULT_TEXT_LIMIT = contract.DEFAULT_TEXT_LIMIT
DEFAULT_MIN_CHARS = contract.DEFAULT_MIN_CHARS

# select_candidates と同義。除外理由 / 発話型 / 帯定義は contract が正本。
EXCLUSION_REASONS = contract.EXCLUSION_REASONS
STEERING_PATTERNS = contract.STEERING_PATTERNS
BANDS = contract.BANDS
BOILERPLATE_MIN_GROUP = contract.BOILERPLATE_MIN_GROUP
CANDIDATE_FIELDS = contract.CANDIDATE_FIELDS
PRIORITY_PATTERN = contract.PRIORITY_PATTERN
REPO_SOURCE_CWD = contract.REPO_SOURCE_CWD
REPO_SOURCE_ANCESTOR = contract.REPO_SOURCE_ANCESTOR
REPO_SOURCES = contract.REPO_SOURCES
REPO_SCOPE_EXPLICIT = contract.REPO_SCOPE_EXPLICIT
REPO_SCOPE_CWD = contract.REPO_SCOPE_CWD
REPO_SCOPE_ALL = contract.REPO_SCOPE_ALL

STORE_META_KEYS = (
    "ingested_files", "records", "broken_lines", "unreadable_files",
    "duplicate_record_uuid_groups", "ts_missing_events",
)


def _store_meta(conn: sqlite3.Connection, sync_report: ingest.SyncReport) -> dict:
    """観測劣化シグナル。store 側の共有関数 + 差分 sync 件数を合わせて出す。

    scan_prompts / select_candidates の両方が同じ形で出す (permissions mart と
    同じ関数を呼ぶ — #497 でこの mart が 2 つ目の消費者になったため store 側へ
    引き上げた)。
    """
    anomalies = store_mod.anomalies(conn)
    return {
        **anomalies,
        "skipped_nested_files": sync_report.skipped_nested_files,
        "synced_files": sync_report.ingested_files,
        "unchanged_files": sync_report.unchanged_files,
        "removed_files": sync_report.removed_files,
    }


# --- repo 解決 (I/O のため query 層に置けない) --------------------------------

def resolve_repo(cwd: str,
                 resolver: Callable[[Path], str | None] = resolve_repo_at,
                 ) -> tuple[str | None, str | None]:
    """cwd から repo 識別子を解決し、(repo, 解決元) を返す。

    1 ディレクトリの解決は `adapter.transcript.resolve_repo_at` (origin remote URL →
    git-common-dir の親) に委ねる。scan_prompts / select_candidates の repo 既定
    解決も同じ関数を呼ぶため、mart の `repo` 値と絞り込みキーの表現一致が構造的に
    保証される。

    issue ごとに worktree を作って merge 後に削除する運用では、観測時点で cwd が
    既に存在しない record が大量に出る。そのままでは repo 単位の絞り込みが欠落
    するため、**cwd が消えている場合に限り実在する最近祖先まで遡って**解決する
    (`REPO_SOURCE_ANCESTOR`)。
    """
    if not cwd:
        return None, None
    cwd_path = Path(cwd)
    if cwd_path.is_dir():
        repo = resolver(cwd_path)
        return (repo, REPO_SOURCE_CWD) if repo else (None, None)
    for ancestor in cwd_path.parents:
        if not ancestor.is_dir():
            continue
        repo = resolver(ancestor)
        return (repo, REPO_SOURCE_ANCESTOR) if repo else (None, None)
    return None, None


def _resolve_repos_by_cwd(
        rows: list[dict],
        resolver: Callable[[str], tuple[str | None, str | None]] = resolve_repo,
) -> dict[str, tuple[str | None, str | None]]:
    """distinct cwd ごとに 1 回だけ resolve する (git 呼び出しの重複を避ける)。"""
    resolved: dict[str, tuple[str | None, str | None]] = {}
    for row in rows:
        cwd = row["cwd"]
        if cwd not in resolved:
            resolved[cwd] = resolver(cwd)
    return resolved


class RepoScopeUnresolved(RuntimeError):
    """cwd から repo を解決できず、明示 (repo / all_repos) が要る状態。

    全 repo へ倒す fallback を持たない — 他 project の prompt を黙って slice に
    載せないことが repo 既定解決の目的そのものだから。
    """


def resolve_repo_scope(repo: str | None, all_repos: bool) -> str:
    if repo is not None:
        return REPO_SCOPE_EXPLICIT
    if all_repos:
        return REPO_SCOPE_ALL
    return REPO_SCOPE_CWD


def _resolve_default_repo_filter(repo_root: Path) -> str:
    """repo 未指定時に repo_root (既定 cwd) の repo 識別子を解決する。

    解決できないときに全 repo へ倒さない — 他 project の prompt を黙って slice に
    載せないことが repo 既定化の目的そのものなので、明示を要求して fail する。
    理由 (git 不在 / 非 repo) をエラー文言に含める (#496 からの繰り越しバグ:
    `resolve_repo_at` は両者を区別せず None に潰していた)。
    """
    repo, cause = resolve_repo_at_with_cause(repo_root)
    if repo is not None:
        return repo
    reason = {
        "git_unavailable": "git を起動できない (未インストール / timeout)",
        "not_a_repo": "git は動いたが repo と認識できない",
    }.get(cause, "解決できない")
    raise RepoScopeUnresolved(
        f"cwd から repo を解決できない: {repo_root} ({reason})。"
        "他 repo の prompt を黙って載せないため、repo か all_repos を明示する"
    )


# --- store への問い合わせ ----------------------------------------------------

SCOPED_PROMPT_COLUMNS = (
    "project_dir", "session_id", "record_uuid", "ts", "ts_epoch", "cwd", "git_branch",
    "prompt_source", "cli_version", "text", "text_chars", "steering_pattern",
    "reason",
)


def _load_scoped_prompt(conn: sqlite3.Connection, statements: dict[str, str],
                        cutoff_epoch: float) -> None:
    conn.execute(statements["clear_scoped_prompt"])
    conn.execute(
        f"INSERT INTO scoped_prompt ({', '.join(SCOPED_PROMPT_COLUMNS)}) "
        + statements["refined_prompt"],
        {"cutoff_epoch": cutoff_epoch},
    )


# query.sql の SQL 内部 label → 公開 reason 語彙 (`contract.EXCLUSION_REASONS`)。
# `tool_result` は on-disk の record type 名と同綴りで format isolation gate の
# 禁止語に触れるため、SQL 側は `content_is_tool_result` を返し presentation 層で
# 変換する (query.sql の `refined_prompt` docstring 参照)。
REASON_SQL_ALIASES = {"content_is_tool_result": "tool_result"}


def _verdict_counts(conn: sqlite3.Connection,
                    statements: dict[str, str]) -> collections.Counter:
    counts: collections.Counter = collections.Counter()
    for row in conn.execute(statements["verdict_totals"]):
        reason = REASON_SQL_ALIASES.get(row["reason"], row["reason"])
        counts[reason] = row["n"]
    return counts


def _accepted_rows(conn: sqlite3.Connection, statements: dict[str, str]) -> list[dict]:
    return [dict(row) for row in conn.execute(statements["accepted_prompt"])]


def _boilerplate_membership(conn: sqlite3.Connection, statements: dict[str, str],
                            min_chars: int) -> dict[int, tuple[str, int]]:
    """定型判定 (query 層の CTE)。seq → (正規形, 群の総件数)。"""
    rows = conn.execute(
        statements["boilerplate_membership"],
        {"min_chars": min_chars, "boilerplate_min_group": BOILERPLATE_MIN_GROUP},
    )
    return {row["seq"]: (row["form"], row["group_size"]) for row in rows}


def _open(transcripts_dir: Path, cache_dir: Path | None, now: dt.datetime,
         cutoff_epoch: float) -> tuple[sqlite3.Connection, dict[str, str], ingest.SyncReport]:
    conn, sync_report = ingest.open_synced(transcripts_dir, cache_dir=cache_dir, now=now)
    udf.register(conn)
    statements = load_statements(QUERY_PATH)
    conn.execute(statements["create_scoped_prompt"])
    conn.execute(statements["create_scoped_prompt_index"])
    _load_scoped_prompt(conn, statements, cutoff_epoch)
    return conn, statements, sync_report


# --- scan_prompts (mart) ------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class ScanRequest:
    days: int = DEFAULT_DAYS
    transcripts_dir: Path = DEFAULT_TRANSCRIPTS_DIR
    output_dir: Path = DEFAULT_OUTPUT_DIR
    text_limit: int = DEFAULT_TEXT_LIMIT
    now: str | None = None
    cache_dir: Path | None = None


def _stamp(moment: dt.datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def build_repos_section(prompts: list[dict]) -> list[dict]:
    """repo 単位の索引。後段が repo で絞り込むときの入口になる。

    repo 未解決分は id `null` の 1 entry にまとめる (件数を隠さない)。
    """
    grouped: dict[str | None, list[dict]] = collections.defaultdict(list)
    for prompt in prompts:
        grouped[prompt["repo"]].append(prompt)
    section: list[dict] = []
    for repo, items in grouped.items():
        timestamps = sorted(item["timestamp"] for item in items)
        section.append({
            "repo": repo,
            "prompt_count": len(items),
            "session_count": len({item["session_id"] for item in items}),
            "first_timestamp": timestamps[0] if timestamps else "",
            "last_timestamp": timestamps[-1] if timestamps else "",
        })
    section.sort(key=lambda entry: (-entry["prompt_count"], str(entry["repo"])))
    return section


def build_mart(request: ScanRequest,
              repo_resolver: Callable[[str], tuple[str | None, str | None]] = resolve_repo,
              ) -> dict:
    """store を sync してから mart を組み立てる (ファイル書き出しは含まない)。"""
    now = resolve_now(request.now)
    cutoff = now - dt.timedelta(days=request.days)

    conn, statements, sync_report = _open(
        request.transcripts_dir, request.cache_dir, now, cutoff.timestamp())
    try:
        verdicts = _verdict_counts(conn, statements)
        accepted = _accepted_rows(conn, statements)
        store_meta = _store_meta(conn, sync_report)
    finally:
        conn.close()

    resolved_by_cwd = _resolve_repos_by_cwd(accepted, repo_resolver)

    prompts: list[dict] = []
    for row in accepted:
        repo, repo_source = resolved_by_cwd[row["cwd"]]
        text = row["text"]
        prompts.append({
            "session_id": row["session_id"],
            "uuid": row["record_uuid"],
            "timestamp": row["ts"],
            "cwd": row["cwd"],
            "repo": repo,
            "repo_source": repo_source,
            "project_dir": row["project_dir"],
            "git_branch": row["git_branch"],
            "prompt_source": row["prompt_source"],
            "cli_version": row["cli_version"],
            "text": truncate(text, request.text_limit),
            "text_chars": row["text_chars"],
            "truncated": row["text_chars"] > request.text_limit,
            "steering_pattern": row["steering_pattern"],
        })

    excluded = {reason: verdicts.get(reason, 0) for reason in EXCLUSION_REASONS}
    scanned = sum(verdicts.values())
    by_repo_source = collections.Counter(
        p["repo_source"] for p in prompts if p["repo_source"])
    resolved = sum(by_repo_source.values())

    return {
        "meta": {
            "generated_at": _stamp(now),
            "window_start": _stamp(cutoff),
            "window_end": _stamp(now),
            "days": request.days,
            "transcripts_dir": str(request.transcripts_dir),
            "text_limit": request.text_limit,
            "total_prompts": len(prompts),
            "distinct_sessions": len({p["session_id"] for p in prompts}),
            "distinct_repos": len({p["repo"] for p in prompts if p["repo"]}),
            "scanned_user_records": scanned,
            "excluded": excluded,
            "steering_patterns": {
                pattern: sum(1 for p in prompts if p["steering_pattern"] == pattern)
                for pattern in STEERING_PATTERNS
            },
            "repo_resolution": {
                "resolved": resolved,
                "unresolved": len(prompts) - resolved,
                "by_source": {
                    source: by_repo_source.get(source, 0) for source in REPO_SOURCES
                },
            },
            "store": store_meta,
        },
        "contract": contract.build_mart_contract(),
        "repos": build_repos_section(prompts),
        "prompts": prompts,
    }


def emit_mart(mart: dict, output_dir: Path) -> str:
    """mart-<timestamp>.json を 0700 で書いて path を返す。

    stamp は mart の `generated_at` から起こす — `resolve_now` を引き直すと
    mart 内の時刻とファイル名が秒境界でずれる。
    """
    resolved = prepare_output_dir(output_dir)
    generated = dt.datetime.fromisoformat(mart["meta"]["generated_at"])
    out = resolved / f"mart-{generated.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(
        json.dumps(mart, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(out)


def run_scan_prompts(
    days: int = DEFAULT_DAYS,
    output_dir: str = str(DEFAULT_OUTPUT_DIR),
    transcripts_dir: str = str(DEFAULT_TRANSCRIPTS_DIR),
    text_limit: int = DEFAULT_TEXT_LIMIT,
    now: str | None = None,
) -> dict:
    """`scan_prompts` tool の入口。mart は返さず、書いた path と meta だけを返す。"""
    request = ScanRequest(
        days=days, output_dir=Path(output_dir),
        transcripts_dir=Path(transcripts_dir), text_limit=text_limit, now=now,
    )
    mart = build_mart(request)
    meta = mart["meta"]
    return {
        "path": emit_mart(mart, request.output_dir),
        "meta": {
            "window_start": meta["window_start"],
            "window_end": meta["window_end"],
            "days": meta["days"],
            "total_prompts": meta["total_prompts"],
            "distinct_sessions": meta["distinct_sessions"],
            "distinct_repos": meta["distinct_repos"],
            "scanned_user_records": meta["scanned_user_records"],
            "excluded": meta["excluded"],
            "steering_patterns": meta["steering_patterns"],
            "repo_resolution": meta["repo_resolution"],
            "store": meta["store"],
        },
    }


# --- select_candidates (slice) ------------------------------------------------

@dataclasses.dataclass(frozen=True)
class SelectRequest:
    mart: Path | None = None
    min_chars: int = DEFAULT_MIN_CHARS
    repo: str | None = None
    all_repos: bool = False
    limit: int | None = None
    output_dir: Path = DEFAULT_OUTPUT_DIR
    repo_root: Path = dataclasses.field(default_factory=Path.cwd)
    transcripts_dir: Path = DEFAULT_TRANSCRIPTS_DIR
    days: int = DEFAULT_DAYS
    now: str | None = None
    cache_dir: Path | None = None

    def repo_scope(self) -> str:
        return resolve_repo_scope(self.repo, self.all_repos)


def _mart_provenance(mart_path: Path | None) -> dict | None:
    """`mart` (provenance) の meta を読む。渡されていなければ None。

    読めない mart path は明示的な誤りとして呼び元に伝える (存在しないと知らずに
    黙って own window へ倒すと「provenance を指定したつもりが無視された」事故になる)。
    """
    if mart_path is None:
        return None
    data = json.loads(mart_path.read_text(encoding="utf-8"))
    return data.get("meta") or {}


def _resolve_window(request: SelectRequest,
                    mart_meta: dict | None) -> tuple[dt.datetime, int, dt.datetime]:
    """観測窓を決める。`mart` があればその window を、無ければ `days` から作る。

    stamp drift 対策: `now` はここで 1 度だけ解決し、以降 (meta 生成 / 出力
    ファイル名) はこの値だけを使う。
    """
    now = resolve_now(request.now)
    if mart_meta and mart_meta.get("window_start"):
        cutoff = dt.datetime.fromisoformat(
            str(mart_meta["window_start"]).replace("Z", "+00:00")
        ).astimezone(dt.timezone.utc)
        days = int(mart_meta.get("days") or request.days)
        return cutoff, days, now
    return now - dt.timedelta(days=request.days), request.days, now


def band_of(chars: int) -> str | None:
    for label, lower, upper in BANDS:
        if chars >= lower and (upper is None or chars <= upper):
            return label
    return None


def build_band_histogram(rows: list[dict], min_chars: int) -> list[dict]:
    """帯別の件数。**絞り込み前** (repo 絞り込み前) の母数に対して集計する。"""
    counter: collections.Counter = collections.Counter()
    for row in rows:
        label = band_of(int(row["text_chars"] or 0))
        if label:
            counter[label] += 1
    return [
        {"band": label, "count": counter.get(label, 0), "in_scope": lower >= min_chars}
        for label, lower, _ in BANDS
    ]


def _distinct_repos(items: list[dict]) -> list[str]:
    """正規化後の distinct repo (昇順)。**解決できなかった repo は数えない**。

    `repo_count >= 2` は engineering-values の採用 gate (ADR 0032) の入力なので、
    None (repo 未解決) を 1 repo として数えると gate が誤って通る。表記違いの
    水増しは `normalize_repo_identifier` が潰す。
    """
    return sorted({normalized for item in items
                   if (normalized := normalize_repo_identifier(item.get("repo")))})


def build_boilerplate_forms(rows: list[dict],
                            membership: dict[int, tuple[str, int]],
                            excluded_counts: collections.Counter) -> list[dict]:
    """定型と判定した正規形の一覧 (件数付き)。**人間が拾い戻すための第 2 の候補源**。"""
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    group_size: dict[str, int] = {}
    for row in rows:
        entry = membership.get(row["seq"])
        if entry is None:
            continue
        form, size = entry
        grouped[form].append(row)
        group_size[form] = size

    forms = [
        {
            "normalized": truncate(form, contract.FORM_PREVIEW_CHARS),
            "count": group_size[form],
            "excluded_from_slice": excluded_counts.get(form, 0),
            "repos": _distinct_repos(items),
            "repo_count": len(_distinct_repos(items)),
            "sample": {
                "session_id": items[0]["session_id"],
                "timestamp": items[0]["ts"],
                "repo": items[0].get("repo"),
                "text": truncate(str(items[0]["text"] or ""), contract.FORM_PREVIEW_CHARS),
            },
        }
        for form, items in grouped.items()
    ]
    forms.sort(key=lambda entry: (-entry["count"], entry["normalized"]))
    return forms


def build_steering_pattern_section(candidates: list[dict]) -> dict:
    """発話型の内訳と、訂正を先に読むための優先帯索引。"""
    histogram: collections.Counter = collections.Counter(
        str(c.get("steering_pattern") or "") for c in candidates)
    priority = [c["rank"] for c in candidates
               if c.get("steering_pattern") == PRIORITY_PATTERN]
    rest = [c["rank"] for c in candidates
           if c.get("steering_pattern") != PRIORITY_PATTERN]
    return {
        "priority_pattern": PRIORITY_PATTERN,
        "histogram": dict(sorted(histogram.items())),
        "priority_order": priority + rest,
        "definition": (
            f"{PRIORITY_PATTERN} を先頭帯に、帯内は rank 昇順 "
            "(rank = text_chars 降順の読み順。優先帯は rank を変えない)"
        ),
    }


def build_repo_index(candidates: list[dict]) -> list[dict]:
    grouped: dict[Any, list[dict]] = collections.defaultdict(list)
    for candidate in candidates:
        grouped[candidate.get("repo")].append(candidate)
    index = [
        {
            "repo": repo,
            "candidate_count": len(items),
            "session_count": len({item.get("session_id") for item in items}),
        }
        for repo, items in grouped.items()
    ]
    index.sort(key=lambda entry: (-entry["candidate_count"], str(entry["repo"])))
    return index


def sort_key(row: dict) -> tuple:
    """読み順の全順序キー。第 1 キーは `text_chars` 降順。"""
    return (
        -int(row.get("text_chars") or 0),
        str(row.get("ts") or ""),
        str(row.get("session_id") or ""),
        str(row.get("record_uuid") or ""),
    )


def build_slice(
    request: SelectRequest,
    repo_resolver: Callable[[str], tuple[str | None, str | None]] = resolve_repo,
    default_repo_resolver: Callable[[Path], str] = _resolve_default_repo_filter,
) -> dict:
    """store を直接 query して slice を組む (ファイル出力を伴わない)。"""
    mart_meta = _mart_provenance(request.mart)
    cutoff, days, now = _resolve_window(request, mart_meta)

    repo_scope = request.repo_scope()
    repo_filter = request.repo
    if repo_scope == REPO_SCOPE_CWD:
        repo_filter = default_repo_resolver(request.repo_root)

    conn, statements, sync_report = _open(
        request.transcripts_dir, request.cache_dir, now, cutoff.timestamp())
    try:
        accepted = _accepted_rows(conn, statements)
        membership = _boilerplate_membership(conn, statements, request.min_chars)
        store_meta = _store_meta(conn, sync_report)
    finally:
        conn.close()

    resolved_by_cwd = _resolve_repos_by_cwd(accepted, repo_resolver)
    for row in accepted:
        repo, _source = resolved_by_cwd[row["cwd"]]
        row["repo"] = repo

    below_min = 0
    other_repo = 0
    boilerplate = 0
    boilerplate_excluded: collections.Counter = collections.Counter()
    matched: list[dict] = []
    for row in accepted:
        if int(row["text_chars"] or 0) < request.min_chars:
            below_min += 1
            continue
        if repo_filter is not None and row["repo"] != repo_filter:
            other_repo += 1
            continue
        entry = membership.get(row["seq"])
        if entry is not None:
            boilerplate += 1
            boilerplate_excluded[entry[0]] += 1
            continue
        matched.append(row)

    matched.sort(key=sort_key)
    emitted_rows = matched if request.limit is None else matched[:max(request.limit, 0)]

    candidates: list[dict] = []
    for rank, row in enumerate(emitted_rows, start=1):
        candidate = {"rank": rank, "session_id": row["session_id"],
                    "uuid": row["record_uuid"], "timestamp": row["ts"],
                    "repo": row["repo"], "git_branch": row["git_branch"],
                    "text_chars": row["text_chars"], "truncated": False,
                    "steering_pattern": row["steering_pattern"], "text": row["text"]}
        candidates.append({field: candidate[field] for field in ("rank", *CANDIDATE_FIELDS)})

    generated_at = _stamp(now)
    boilerplate_forms = build_boilerplate_forms(accepted, membership,
                                                boilerplate_excluded)
    return {
        "meta": {
            "generated_at": generated_at,
            "mart_path": str(request.mart) if request.mart else None,
            "mart_generated_at": mart_meta.get("generated_at") if mart_meta else None,
            "mart_window_start": mart_meta.get("window_start") if mart_meta else None,
            "mart_window_end": mart_meta.get("window_end") if mart_meta else None,
            "window_start": _stamp(cutoff),
            "window_end": generated_at,
            "days": days,
            "transcripts_dir": str(request.transcripts_dir),
            "min_chars": request.min_chars,
            "repo_filter": repo_filter,
            "repo_scope": repo_scope,
            "limit": request.limit,
            "total_prompts": len(accepted),
            "selected": len(matched),
            "emitted": len(candidates),
            "truncated_by_limit": len(matched) - len(candidates),
            "excluded": {
                "below_min_chars": below_min,
                "other_repo": other_repo,
                "boilerplate": boilerplate,
            },
            "band_histogram": build_band_histogram(accepted, request.min_chars),
            "boilerplate": {
                "min_group": BOILERPLATE_MIN_GROUP,
                "detection_scope": "min_chars を通した全 repo の accepted prompt",
                "detected_forms": len({v[0] for v in membership.values()}),
                "detected_prompts": len(membership),
            },
            "read_order": (
                "text_chars 降順 → timestamp → session_id → uuid (全順序・再現可能)"
            ),
            "store": store_meta,
        },
        "contract": contract.build_slice_contract(rules.rule_catalog()),
        "boilerplate_forms": boilerplate_forms,
        "rule_candidates": rules.evaluate(boilerplate_forms, repo_scope),
        "steering_patterns": build_steering_pattern_section(candidates),
        "repos": build_repo_index(candidates),
        "candidates": candidates,
    }


def emit_slice(slice_json: dict, output_dir: Path) -> str:
    """candidates-<timestamp>.json を 0700 で書いて path を返す。

    stamp は slice の `generated_at` から起こす (stamp drift の解消: `resolve_now`
    を 2 度呼ばない)。
    """
    resolved = prepare_output_dir(output_dir)
    generated = dt.datetime.fromisoformat(slice_json["meta"]["generated_at"])
    out = resolved / f"candidates-{generated.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(
        json.dumps(slice_json, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(out)


def run_select_candidates(
    mart: str | None = None,
    min_chars: int = DEFAULT_MIN_CHARS,
    repo: str | None = None,
    all_repos: bool = False,
    limit: int | None = None,
    output_dir: str = str(DEFAULT_OUTPUT_DIR),
    repo_root: str | None = None,
    days: int = DEFAULT_DAYS,
    transcripts_dir: str = str(DEFAULT_TRANSCRIPTS_DIR),
    now: str | None = None,
) -> dict:
    """`select_candidates` tool の入口。slice は返さず、書いた path と meta だけを返す。

    `mart` は provenance に降格した — 渡されれば window (`days` 相当) をその meta
    から読み、渡されなければ `days` から窓を作る (既定 30)。どちらの経路でも
    候補 text は store から**切り詰めずに**取得する。
    """
    request = SelectRequest(
        mart=Path(mart) if mart else None,
        min_chars=min_chars, repo=repo, all_repos=all_repos, limit=limit,
        output_dir=Path(output_dir),
        repo_root=Path(repo_root) if repo_root else Path.cwd(),
        transcripts_dir=Path(transcripts_dir), days=days, now=now,
    )
    slice_json = build_slice(request)
    return {
        "path": emit_slice(slice_json, request.output_dir),
        "meta": slice_json["meta"],
        "repo_index": slice_json["repos"],
        "steering_patterns": slice_json["steering_patterns"]["histogram"],
        "rule_candidates": {
            "fired": sum(1 for row in slice_json["rule_candidates"]
                         if row["rule_fired"]),
            "near_miss_only": sum(1 for row in slice_json["rule_candidates"]
                                  if not row["rule_fired"]),
        },
    }


# --- CLI -----------------------------------------------------------------
# mart / slice schema を固定するテストと、実 lake に対する検証 (差分説明書) が
# 通す入口。subcommand は `scan` (mart) / `select` (slice)。

def parse_scan_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="scan_prompts の mart を組む")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS)
    p.add_argument("--transcripts-dir", type=Path, default=DEFAULT_TRANSCRIPTS_DIR)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--text-limit", type=int, default=DEFAULT_TEXT_LIMIT)
    p.add_argument("--cache-dir", type=Path, default=None)
    p.add_argument("--now", type=str, default=None)
    p.add_argument("--stdout-mart", action="store_true")
    return p.parse_args(argv)


def parse_select_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="select_candidates の slice を組む")
    p.add_argument("--mart", type=Path, default=None,
                   help="provenance。渡すとその window から候補を絞る")
    p.add_argument("--min-chars", type=int, default=DEFAULT_MIN_CHARS)
    p.add_argument("--repo", type=str, default=None)
    p.add_argument("--all-repos", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--repo-root", type=Path, default=Path.cwd())
    p.add_argument("--transcripts-dir", type=Path, default=DEFAULT_TRANSCRIPTS_DIR)
    p.add_argument("--days", type=int, default=DEFAULT_DAYS)
    p.add_argument("--cache-dir", type=Path, default=None)
    p.add_argument("--now", type=str, default=None)
    p.add_argument("--stdout-slice", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("scan", "select"):
        print("usage: present.py {scan,select} ...", file=sys.stderr)
        return 2
    mode, rest = argv[0], argv[1:]
    if mode == "scan":
        args = parse_scan_args(rest)
        request = ScanRequest(
            days=args.days, transcripts_dir=args.transcripts_dir,
            output_dir=args.output_dir, text_limit=args.text_limit,
            now=args.now, cache_dir=args.cache_dir,
        )
        mart = build_mart(request)
        if args.stdout_mart:
            json.dump(mart, sys.stdout, ensure_ascii=False, indent=2)
            print()
            return 0
        print(emit_mart(mart, request.output_dir))
        return 0

    args = parse_select_args(rest)
    request = SelectRequest(
        mart=args.mart, min_chars=args.min_chars, repo=args.repo,
        all_repos=args.all_repos, limit=args.limit, output_dir=args.output_dir,
        repo_root=args.repo_root, transcripts_dir=args.transcripts_dir,
        days=args.days, now=args.now, cache_dir=args.cache_dir,
    )
    try:
        slice_json = build_slice(request)
    except RepoScopeUnresolved as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1
    if args.stdout_slice:
        json.dump(slice_json, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    print(emit_slice(slice_json, request.output_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
