"""`scan_overhead` tool の実装 (configured vs actual の overhead 観測)。

ADR 0031 の store 移行 (#498)。transcript の直読みではなく、store への query
(`query.sql`) から静的コンテキストの実測コスト・memory file 注入実績・compaction
実害を組み立てる。mart の出力契約は移行前の `commands/scan_overhead.py` と同一に
保つ (`tests/test_mart_schema_freeze.py` が固定する)。

session の開始時点で**無条件に載る静的コンテキスト** (memory file / skill listing /
MCP instructions / deferred tool 一覧) を実測し、それが実際に何をもたらしたか
(注入された session 数 / compaction で捨てられた token) と並べて出す。

消費者は inventory-claude-md (ツール 3)。

- **memory file の注入実績**: `.claude/rules/<topic>.md` のような path-scoped file が
  観測窓で何 session に実際に注入されたか。`paths:` で絞ったつもりの規範が 0 session
  なら「書いてあるが誰も読んでいない」が実測で言える
- **compaction 実害**: 静的コンテキストが膨らんだ session がどれだけ token を
  捨てたか (`cumulativeDroppedTokens`)

**実績が決定的なのは file 粒度まで**で、行粒度ではない。行単位の静的コストは
repo static 側 (`scan-claude-md.py` の `token_cost`) が出し、両者の突合は LLM 段階が
行う — transcript には「どの行が効いたか」の record が存在しないため。

token 数はすべて **概算** (query 層の UDF `estimate_tokens_from_counts`)。tokenizer を
持ち込まないので桁の比較にしか使えず、bucket 判定の単独根拠にはしない。

出力: output_dir に overhead-<timestamp>.json を書き、**path だけを返す**。

`parse_args` / `main(argv)` を残してあるのは、mart schema を固定するテストが CLI
形の entrypoint を通して観測契約を検査しているため。tool 側の入口は `run()`。
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

from adapter.transcript import (
    MEMORY_ATTACHMENT_TYPE,
    STATIC_PAYLOAD_FIELDS,
    TOKEN_ESTIMATOR,
    resolve_now,
    resolve_repo_at,
)
from artifacts import prepare_output_dir
from marts import load_statements
from store import ingest

from . import udf

QUERY_PATH = Path(__file__).resolve().parent / "query.sql"

# --- 定数 --------------------------------------------------------------------

DEFAULT_DAYS = 30
DEFAULT_TRANSCRIPTS_DIR = Path("~/.claude/projects").expanduser()
DEFAULT_OUTPUT_DIR = Path("/tmp/inventory-claude-md")

# 静的コンテキストの source 名 → attachment type。**1 source = 1 行の観測**に
# しておくと、「何が重いか」が session ごとに並ぶ。どの field に本文が載るかは形式の
# 事実なので adapter の `STATIC_PAYLOAD_FIELDS` が持ち、ここには source 語彙だけ置く。
STATIC_SOURCE_TYPES: tuple[tuple[str, str], ...] = (
    ("skill_listing", "skill_listing"),
    ("mcp_instructions", "mcp_instructions_delta"),
    ("deferred_tools", "deferred_tools_delta"),
    ("agent_listing", "agent_listing_delta"),
    ("memory_file", MEMORY_ATTACHMENT_TYPE),
)
STATIC_SOURCE_NAMES = tuple(name for name, _ in STATIC_SOURCE_TYPES)

# memory file の source 名 (静的コストに加えて注入実績も出す唯一の source)。
MEMORY_SOURCE = "memory_file"

# on-disk attachment type → mart の source 名。memory file は入れ子構造が違い
# 専用経路 (`memory_injection` 表) を通るのでここから外す。
SOURCE_BY_ATTACHMENT_TYPE = {atype: name for name, atype in STATIC_SOURCE_TYPES
                             if atype != MEMORY_ATTACHMENT_TYPE}
if set(SOURCE_BY_ATTACHMENT_TYPE) - set(STATIC_PAYLOAD_FIELDS):
    raise RuntimeError(
        "本文 field を持たない attachment type を静的コストに数えようとしている: "
        f"{sorted(set(SOURCE_BY_ATTACHMENT_TYPE) - set(STATIC_PAYLOAD_FIELDS))}"
    )


# --- CLI ---------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=DEFAULT_DAYS,
                   help="観測窓 (日)。default 30")
    p.add_argument("--transcripts-dir", type=Path, default=DEFAULT_TRANSCRIPTS_DIR,
                   help="transcript lake。default ~/.claude/projects")
    p.add_argument("--repo-root", type=Path, default=Path.cwd(),
                   help="repo 絞り込みの起点。省略時 cwd")
    p.add_argument("--all-repos", action="store_true",
                   help="repo 絞り込みを外して全 project 横断で見る")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help="mart 出力先。default /tmp/inventory-claude-md")
    p.add_argument("--now", type=str, default=None,
                   help="ISO timestamp。窓の起点を固定 (テスト・再現用)")
    p.add_argument("--stdout-mart", action="store_true",
                   help="ファイルに書かず mart JSON を stdout に出す (テスト用)")
    return p.parse_args(argv)


# --- 観測の accumulator -------------------------------------------------------

class OverheadObservation:
    """query 結果を畳み込む accumulator。session 単位と memory file 単位の 2 系統を持つ。"""

    def __init__(self) -> None:
        self.session_cost: dict[str, dict[str, int]] = collections.defaultdict(
            lambda: {name: 0 for name in STATIC_SOURCE_NAMES})
        self.session_cwd: dict[str, str] = {}
        self.memory_files: dict[str, dict] = {}
        self.compaction: dict[str, dict] = {}

    def note_static(self, session_id: str, source: str, tokens: int) -> None:
        self.session_cost[session_id][source] += tokens

    def note_memory_file(self, *, path: str, display_path: str, memory_type: str,
                         globs: list[str], tokens: int, chars: int, lines: int,
                         differs_from_disk: bool, session_id: str, timestamp: str,
                         memory_key_value: str) -> None:
        """memory file 1 回の注入を畳み込む (静的コストの計上も本 method が行う)。

        `tokens` / `chars` / `lines` / `memory_key_value` は query 層 (UDF 込み) が
        計算済みの値 — 呼び元が生 text を持ち込む必要はない。
        """
        self.note_static(session_id, MEMORY_SOURCE, tokens)
        if not path:
            return
        entry = self.memory_files.get(memory_key_value)
        if entry is None:
            entry = {
                "path": memory_key_value, "display_path": display_path,
                "memory_type": memory_type, "globs": globs,
                "sessions": set(), "observed_paths": set(),
                "est_tokens": 0, "chars": 0, "lines": 0,
                "differs_from_disk_sessions": set(),
                "first_injected_at": timestamp, "last_injected_at": timestamp,
            }
            self.memory_files[memory_key_value] = entry
        entry["sessions"].add(session_id)
        entry["observed_paths"].add(path)
        # 同一 file が session ごとに別の長さで現れる (編集途中の注入) ので最大値を採る
        entry["est_tokens"] = max(entry["est_tokens"], tokens)
        entry["chars"] = max(entry["chars"], chars)
        entry["lines"] = max(entry["lines"], lines)
        if differs_from_disk:
            entry["differs_from_disk_sessions"].add(session_id)
        if timestamp:
            entry["first_injected_at"] = min(
                entry["first_injected_at"] or timestamp, timestamp)
            entry["last_injected_at"] = max(entry["last_injected_at"], timestamp)

    def note_compaction(self, boundary: dict) -> None:
        session_id = boundary["session_id"]
        entry = self.compaction.setdefault(session_id, {
            "session_id": session_id,
            "boundaries": 0,
            "triggers": collections.Counter(),
            "cumulative_dropped_tokens": 0,
            "max_pre_tokens": 0,
            "cwd": boundary["cwd"],
        })
        entry["boundaries"] += 1
        entry["triggers"][boundary["trigger"] or "unknown"] += 1
        # cumulative なので**最大値を採る** (boundary をまたいで足すと多重計上)
        entry["cumulative_dropped_tokens"] = max(
            entry["cumulative_dropped_tokens"], boundary["cumulative_dropped_tokens"])
        entry["max_pre_tokens"] = max(entry["max_pre_tokens"], boundary["pre_tokens"])


# --- store への問い合わせ ------------------------------------------------------

def collect_from_store(conn: sqlite3.Connection, statements: dict[str, str],
                       cutoff_epoch: float) -> OverheadObservation:
    """query.sql の結果を `OverheadObservation` へ畳み込む。"""
    observation = OverheadObservation()
    params = {"cutoff_epoch": cutoff_epoch}

    for row in conn.execute(statements["static_source_costs"], params):
        source = SOURCE_BY_ATTACHMENT_TYPE.get(row["attachment_type"])
        if source is None:
            continue
        observation.note_static(str(row["session_id"]), source, row["tokens"])

    for row in conn.execute(statements["memory_injection_rows"], params):
        globs = json.loads(row["globs"]) if row["globs"] else []
        observation.note_memory_file(
            path=row["path"], display_path=row["display_path"],
            memory_type=row["memory_type"], globs=globs, tokens=row["tokens"],
            chars=row["chars"], lines=row["lines"],
            differs_from_disk=bool(row["differs_from_disk"]),
            session_id=str(row["session_id"]), timestamp=row["ts"],
            memory_key_value=row["memory_key"],
        )

    for row in conn.execute(statements["compact_boundaries"], params):
        observation.note_compaction(dict(row))

    for row in conn.execute(statements["session_cwd_events"], params):
        observation.session_cwd.setdefault(str(row["session_id"]), row["cwd"])

    return observation


# --- 集計 --------------------------------------------------------------------

def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))
    return ordered[idx]


def _stats(values: list[int]) -> dict:
    return {
        "observed": len(values),
        "p50": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
        "max": max(values) if values else 0,
        "total": sum(values),
    }


def build_static_section(observation: OverheadObservation,
                         sessions: set[str]) -> dict:
    """source 別の静的コスト (session あたり)。**configured 側の実測**。"""
    per_source: dict[str, list[int]] = {name: [] for name in STATIC_SOURCE_NAMES}
    totals: list[int] = []
    for session_id in sessions:
        cost = observation.session_cost.get(session_id)
        if not cost:
            continue
        for name in STATIC_SOURCE_NAMES:
            if cost[name]:
                per_source[name].append(cost[name])
        totals.append(sum(cost.values()))
    return {
        "sessions_observed": len(totals),
        "per_session_est_tokens": _stats(totals),
        "sources": [
            {"source": name, "sessions": len(per_source[name]),
             "est_tokens": _stats(per_source[name])}
            for name in STATIC_SOURCE_NAMES
        ],
    }


def build_memory_files_section(observation: OverheadObservation,
                               sessions: set[str]) -> list[dict]:
    """memory file 別の注入実績。**「書いてあるが注入されていない」の証拠源**。

    載るのは**観測した注入だけ**で、repo の file 一覧は知らない (それは repo static
    の領分)。したがって「窓内で 1 度も注入されなかった file」は `sessions_injected: 0`
    ではなく**行の不在**として現れる — 消費側は静的観測の file 列挙と差分を取る。
    """
    rows: list[dict] = []
    for entry in observation.memory_files.values():
        injected = entry["sessions"] & sessions
        if not injected:
            continue
        rows.append({
            "path": entry["path"],
            "display_path": entry["display_path"],
            "memory_type": entry["memory_type"],
            "globs": entry["globs"],
            "sessions_injected": len(injected),
            "est_tokens": entry["est_tokens"],
            "chars": entry["chars"],
            "lines": entry["lines"],
            "absolute_paths_folded": len(entry["observed_paths"]),
            "sample_path": min(entry["observed_paths"]),
            "differs_from_disk_sessions": len(
                entry["differs_from_disk_sessions"] & sessions),
            "first_injected_at": entry["first_injected_at"],
            "last_injected_at": entry["last_injected_at"],
        })
    rows.sort(key=lambda r: (-r["sessions_injected"], r["path"]))
    return rows


def build_compaction_section(observation: OverheadObservation,
                             sessions: set[str]) -> dict:
    """compaction 実害。`cumulativeDroppedTokens` は session ごとに最大値を採る。"""
    rows = [entry for sid, entry in observation.compaction.items() if sid in sessions]
    dropped = [r["cumulative_dropped_tokens"] for r in rows]
    triggers: collections.Counter = collections.Counter()
    for row in rows:
        triggers.update(row["triggers"])
    return {
        "sessions_with_boundary": len(rows),
        "total_boundaries": sum(r["boundaries"] for r in rows),
        "by_trigger": dict(sorted(triggers.items())),
        "dropped_tokens": _stats(dropped),
        "sessions": sorted(
            [
                {
                    "session_id": r["session_id"],
                    "boundaries": r["boundaries"],
                    "cumulative_dropped_tokens": r["cumulative_dropped_tokens"],
                    "max_pre_tokens": r["max_pre_tokens"],
                }
                for r in rows
            ],
            key=lambda r: (-r["cumulative_dropped_tokens"], r["session_id"]),
        ),
    }


# --- mart 生成 ---------------------------------------------------------------

def select_sessions(observation: OverheadObservation, repo: str | None) -> set[str]:
    """観測対象 session。`repo` 指定時は cwd の repo が一致するものだけ。

    repo 解決は cwd が実在するときだけ行う (消えた worktree の祖先遡りは
    `scan_prompts` 固有の観測契約なので持ち込まない)。解決できない session は
    repo 絞り込み時には落ちるので、件数を meta に出して silent にしない。
    """
    all_sessions = set(observation.session_cost) | set(observation.compaction)
    if repo is None:
        return all_sessions
    selected: set[str] = set()
    resolved: dict[str, str | None] = {}
    for session_id in all_sessions:
        cwd = observation.session_cwd.get(session_id, "")
        if cwd not in resolved:
            path = Path(cwd) if cwd else None
            resolved[cwd] = (resolve_repo_at(path)
                             if path is not None and path.is_dir() else None)
        if resolved[cwd] == repo:
            selected.add(session_id)
    return selected


def build_mart(args: argparse.Namespace, observation: OverheadObservation,
               repo: str | None, cutoff: dt.datetime, now: dt.datetime) -> dict:
    all_sessions = set(observation.session_cost) | set(observation.compaction)
    sessions = select_sessions(observation, repo)
    return {
        "meta": {
            "generated_at": now.isoformat().replace("+00:00", "Z"),
            "window_start": cutoff.isoformat().replace("+00:00", "Z"),
            "window_end": now.isoformat().replace("+00:00", "Z"),
            "days": args.days,
            "transcripts_dir": str(args.transcripts_dir),
            "repo": repo,
            "repo_scope": "all" if repo is None else "repo",
            "sessions_observed": len(sessions),
            "sessions_out_of_scope": len(all_sessions) - len(sessions),
            "token_estimator": TOKEN_ESTIMATOR,
            "notes": [
                "token 数はすべて概算。桁の比較にだけ使い、bucket 判定の単独根拠にしない",
                "memory_files[] の sessions_injected は .claude/rules/ や CLAUDE.md が"
                "**実際に注入された session 数**で、paths: で絞った規範が届いているかの実測",
                "compaction.dropped_tokens の cumulative_dropped_tokens は session ごとの"
                "最大値。boundary をまたいで足さない",
                "注入実績が決定的なのは file 粒度まで。行粒度の実績は transcript に無く、"
                "行単位の静的コストは scan-claude-md.py の token_cost が出す",
                "memory file は repo 相対 path で数える (worktree・複数 checkout に"
                "散った同一 file を 1 行に畳む)。畳んだ絶対 path 数は"
                "absolute_paths_folded に出る",
                "all_repos では repo 相対 path が同じ別 repo の file も 1 行に畳まれる。"
                "repo 単位で読むなら repo_scope: repo で採り直す",
            ],
        },
        "static_context": build_static_section(observation, sessions),
        "memory_files": build_memory_files_section(observation, sessions),
        "compaction": build_compaction_section(observation, sessions),
    }


# --- main --------------------------------------------------------------------

class RepoScopeUnresolved(RuntimeError):
    """repo_root から repo を解決できず、明示 (all_repos) が要る状態。

    全 repo へ倒す fallback を持たない — 他 project の session を黙って混ぜると
    「この repo の CLAUDE.md は 30 日で 3 session にしか注入されていない」のような
    主張が別 repo の分母で崩れる (`select_candidates` と同じ fail-closed)。
    """


def collect(args: argparse.Namespace) -> dict:
    """テスト用の純関数 entrypoint (I/O は store の差分 sync のみ)。

    **scope の解決を store 問い合わせより先に行う。** repo 解決は transcript を
    1 行も読まずに決まるので、後に置くと解決不能時に store 全量を sync してから
    失敗する。`select_candidates` の fail-closed と位置を揃える。
    """
    now = resolve_now(args.now)
    cutoff = now - dt.timedelta(days=args.days)
    cutoff_epoch = cutoff.timestamp()

    repo = None
    if not args.all_repos:
        repo = resolve_repo_at(args.repo_root)
        if repo is None:
            raise RepoScopeUnresolved(
                f"repo を解決できない: {args.repo_root} / "
                "他 repo の session を黙って混ぜないため、all_repos を明示する"
            )

    conn, _sync_report = ingest.open_synced(args.transcripts_dir, now=now)
    try:
        udf.register(conn)
        statements = load_statements(QUERY_PATH)
        observation = collect_from_store(conn, statements, cutoff_epoch)
    finally:
        conn.close()

    return build_mart(args, observation, repo, cutoff, now)


def emit(mart: dict, args: argparse.Namespace) -> str:
    """overhead-<timestamp>.json を書いて path を返す。"""
    output_dir = prepare_output_dir(args.output_dir)
    generated = dt.datetime.fromisoformat(mart["meta"]["generated_at"])
    out = output_dir / f"overhead-{generated.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(
        json.dumps(mart, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(out)


def run(
    days: int = DEFAULT_DAYS,
    repo_root: str | None = None,
    all_repos: bool = False,
    output_dir: str = str(DEFAULT_OUTPUT_DIR),
    transcripts_dir: str = str(DEFAULT_TRANSCRIPTS_DIR),
    now: str | None = None,
) -> dict:
    """tool 側の入口。mart は返さず、書いた path と観測範囲の meta だけを返す。"""
    args = parse_args([])
    args.days = days
    args.repo_root = Path(repo_root) if repo_root else Path.cwd()
    args.all_repos = all_repos
    args.output_dir = Path(output_dir)
    args.transcripts_dir = Path(transcripts_dir)
    args.now = now

    mart = collect(args)
    meta = mart["meta"]
    return {
        "path": emit(mart, args),
        "meta": {
            "window_start": meta["window_start"],
            "window_end": meta["window_end"],
            "days": meta["days"],
            "repo": meta["repo"],
            "repo_scope": meta["repo_scope"],
            "sessions_observed": meta["sessions_observed"],
            "sessions_out_of_scope": meta["sessions_out_of_scope"],
            "memory_file_count": len(mart["memory_files"]),
            "sessions_with_compaction": mart["compaction"]["sessions_with_boundary"],
            "token_estimator": meta["token_estimator"],
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
