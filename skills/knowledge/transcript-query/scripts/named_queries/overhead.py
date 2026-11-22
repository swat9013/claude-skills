"""session に載った静的コンテキストの量を、実績 (注入された session 数と compaction で捨てた token) と並べる問い。

静的コンテキストは memory file・skill の一覧・MCP の指示・tool の一覧で、消費者の棚卸しの重み付けに使う。
成果物は 3 つの節を持つ 1 行で返す。節ごとに粒度が違い、行に割ると列が揃わない。

invariant:
1. cwd の repo を解決できなければ全 repo へ倒さずに止まる。他の repo の session を混ぜると分母が崩れる。
   repo の main checkout を git から引けないときも止まる。root 無しで続けると、main checkout の下の file が黙って絶対 path になる
2. repo の絞り込みで落ちた session の数を、別の repo と判った数と repo を解決できなかった cause 別の数に分けて出す。
   混ぜると、消えた worktree の session の注入が落ちたことを読み手が別の repo の session と区別できない
3. 節の値はどれも repo に絞った session だけから作る
4. `cumulative_dropped_tokens` は session の中で累積するので、session ごとに最大値を採り、足さない
"""

from __future__ import annotations

import collections
import os
from pathlib import Path
from typing import Mapping

from named_query import NamedQuery, QueryRun, Stopped, checkout_root

STATIC_SOURCES = ("skill_listing", "mcp_instructions", "deferred_tools", "agent_listing")
MEMORY_SOURCE = "memory_file"

# 時刻の無い record は窓から落とさない
IN_WINDOW = "(ts_epoch IS NULL OR ts_epoch BETWEEN $since AND $until)"
EST_TOKENS = "lake_estimate_tokens(cjk_chars, chars - cjk_chars)"

# 走査は lake 全体を読むので、view ごとに 1 度だけ読み、1 つの文に 1 つの走査だけを置く。
# 同じ文に並べると同時に走り、memory の上限を超える。view の行を GROUP BY へ直接流しても上限を超えるので、
# MATERIALIZED で窓の中の行に絞ってから集計する

# lake_cwd_repo は record の cwd だけを解決するので、record として読めない行にしか無い cwd は表に無い
CWD_OUTSIDE_RECORDS = "cwd_outside_records"

# session の repo は窓の中で最初に現れた cwd で決まる。line_seq は読む順に増え、file は path の順に読む
SESSION_REPOS = f"""
WITH cwds AS MATERIALIZED (
    SELECT session_id, cwd, line_seq
    FROM lake_session_cwd
    WHERE {IN_WINDOW}
)
SELECT c.session_id, r.repo,
       CASE WHEN r.cwd IS NULL THEN '{CWD_OUTSIDE_RECORDS}' ELSE r.unresolved_cause END AS unresolved_cause
FROM (SELECT session_id, arg_min(cwd, line_seq) AS cwd FROM cwds GROUP BY session_id) c
LEFT JOIN lake_cwd_repo r ON r.cwd = c.cwd
"""

# 窓の中に cwd を持つ行が無い session の cause。resolve_repo の cause と並べて数える
CWD_UNOBSERVED = "cwd_unobserved"

# record ごとに概算してから足す。足してから概算すると端数の切り上げの回数が変わる
STATIC_COSTS = f"""
WITH payloads AS MATERIALIZED (
    SELECT session_id, source, {EST_TOKENS} AS tokens
    FROM lake_static_payload
    WHERE {IN_WINDOW}
)
SELECT session_id, source, sum(tokens) AS tokens
FROM payloads
GROUP BY session_id, source
"""

# memory_key は注入を数える単位。`.claude/worktrees/<名前>` の段を畳み、cwd の repo の main checkout の下はそこからの
# path、user の `~/.claude/` の下は `~/.claude/` からの path にする。main checkout の外では、注入した session の cwd の直下の
# `.claude/` の file (repo の外の worktree・別の clone の rule) だけを `.claude/` からの path にして main checkout の行と畳み、
# それ以外は絶対 path にする。cwd に依らず `.claude/` の末尾で畳むと、無関係な dir の `.claude/` の file まで repo の行に入る。
# 末尾だけで畳むと、user の `~/.claude/rules/a.md` と repo の同じ相対 path の file や、別の dir の同じ名前の file が 1 行になる。
# root は git の今の worktree の一覧から取らない — worktree を消すと同じ lake と窓の答えが変わる。
# costs は repo で絞らない。`sessions_out_of_scope` と `sessions_unresolved` は全 session から数えるので、絞ると注入だけを持つ session が消える
MEMORY_INJECTIONS = f"""
WITH injections AS MATERIALIZED (
    SELECT *, {EST_TOKENS} AS tokens,
           regexp_replace(memory_path, '/\\.claude/worktrees/[^/]+', '', 'g') AS folded_path,
           regexp_replace(coalesce(cwd, ''), '/\\.claude/worktrees/[^/]+', '', 'g') AS folded_cwd
    FROM lake_memory_injection
    WHERE {IN_WINDOW}
), memory_files AS (
    SELECT memory_key AS path,
           first(display_path ORDER BY line_seq) AS display_path,
           first(memory_type ORDER BY line_seq) AS memory_type,
           first(path_globs ORDER BY line_seq) AS path_globs,
           count(DISTINCT session_id) AS sessions_injected,
           max(tokens) AS est_tokens,
           max(chars) AS chars,
           max(body_lines) AS lines,
           count(DISTINCT memory_path) AS absolute_paths_folded,
           min(memory_path) AS sample_path,
           count(DISTINCT session_id) FILTER (WHERE differs_from_disk) AS differs_from_disk_sessions,
           min(nullif(ts, '')) AS first_injected_at,
           max(nullif(ts, '')) AS last_injected_at
    FROM (SELECT *, CASE WHEN starts_with(folded_path, $root || '/') THEN substr(folded_path, length($root) + 2)
                         WHEN starts_with(folded_path, $home || '/.claude/') THEN '~/' || substr(folded_path, length($home) + 2)
                         WHEN folded_cwd <> '' AND starts_with(folded_path, folded_cwd || '/.claude/')
                              THEN substr(folded_path, length(folded_cwd) + 2)
                         ELSE folded_path
                    END AS memory_key
          FROM injections
          WHERE memory_path <> '' AND list_contains($repo_sessions::VARCHAR[], session_id))
    GROUP BY memory_key
), costs AS (
    SELECT session_id, '{MEMORY_SOURCE}' AS source, sum(tokens) AS tokens
    FROM injections
    GROUP BY session_id
)
SELECT (SELECT coalesce(list(c), []) FROM costs c) AS costs,
       (SELECT coalesce(list(m ORDER BY m.sessions_injected DESC, m.path), []) FROM memory_files m) AS memory_files
"""

COMPACTIONS = f"""
WITH compact_boundaries AS MATERIALIZED (
    SELECT session_id, compact_trigger, cumulative_dropped_tokens, pre_tokens
    FROM lake_compact_boundary
    WHERE {IN_WINDOW}
)
SELECT session_id, count(*) AS boundaries,
       list(coalesce(nullif(compact_trigger, ''), 'unknown')) AS triggers,
       coalesce(max(cumulative_dropped_tokens), 0) AS cumulative_dropped_tokens,
       coalesce(max(pre_tokens), 0) AS max_pre_tokens
FROM compact_boundaries
GROUP BY session_id
ORDER BY cumulative_dropped_tokens DESC, session_id
"""


def _percentile(ordered: list[int], ratio: float) -> int:
    return ordered[round(ratio * (len(ordered) - 1))] if ordered else 0


def _stats(values: list[int]) -> dict:
    ordered = sorted(values)
    return {"observed": len(ordered), "p50": _percentile(ordered, 0.5), "p95": _percentile(ordered, 0.95),
            "max": ordered[-1] if ordered else 0, "total": sum(ordered)}


def _static_context(costs: list[dict]) -> dict:
    per_session: dict[str, dict[str, int]] = collections.defaultdict(dict)
    for cost in costs:
        per_session[cost["session_id"]][cost["source"]] = cost["tokens"]
    sources = [*STATIC_SOURCES, MEMORY_SOURCE]
    per_source = {name: [cost[name] for cost in per_session.values() if cost.get(name)] for name in sources}
    return {
        "sessions_observed": len(per_session),
        "per_session_est_tokens": _stats([sum(cost.values()) for cost in per_session.values()]),
        "sources": [{"source": name, "sessions": len(per_source[name]), "est_tokens": _stats(per_source[name])}
                    for name in sources],
    }


def _compaction(rows: list[dict]) -> dict:
    triggers = collections.Counter(trigger for row in rows for trigger in row["triggers"])
    return {
        "sessions_with_boundary": len(rows),
        "total_boundaries": sum(row["boundaries"] for row in rows),
        "by_trigger": dict(sorted(triggers.items())),
        "dropped_tokens": _stats([row["cumulative_dropped_tokens"] for row in rows]),
        "sessions": [{key: value for key, value in row.items() if key != "triggers"} for row in rows],
    }


def _build(run: QueryRun) -> list[dict]:
    cwd = os.getcwd()
    resolved = run.resolve_repo(cwd)
    if resolved["repo"] is None:
        raise Stopped("bad_argument", f"cwd の {cwd} から repo を解決できません ({resolved['cause']})。"
                                      "git が動く環境で、repo の中から呼び直してください")
    checkout = checkout_root(cwd)
    if checkout["cause"]:
        raise Stopped("bad_argument", f"cwd の {cwd} の repo の main checkout を git から引けません ({checkout['cause']})。"
                                      "git が動く環境で、repo の中から呼び直してください")
    session_repos = {row["session_id"]: row for row in run.select(SESSION_REPOS)}
    repo_sessions = {session for session, row in session_repos.items() if row["repo"] == resolved["repo"]}
    (memory,) = run.select(MEMORY_INJECTIONS, repo_sessions=sorted(repo_sessions), root=checkout["root"],
                           home=str(Path.home()))
    costs = [*run.select(STATIC_COSTS), *memory["costs"]]
    compactions = run.select(COMPACTIONS)
    observed = {row["session_id"] for row in [*costs, *compactions]}
    other_repo = 0
    unresolved: collections.Counter[str] = collections.Counter()
    for session in observed - repo_sessions:
        row = session_repos.get(session)
        if row and row["repo"]:
            other_repo += 1
        else:
            unresolved[row["unresolved_cause"] if row else CWD_UNOBSERVED] += 1
    return [{
        "repo": resolved["repo"],
        "sessions_observed": len(observed & repo_sessions),
        "sessions_out_of_scope": other_repo,
        "sessions_unresolved": dict(sorted(unresolved.items())),
        "static_context": _static_context([row for row in costs if row["session_id"] in repo_sessions]),
        "memory_files": memory["memory_files"],
        "compaction": _compaction([row for row in compactions if row["session_id"] in repo_sessions]),
    }]


CONTRACT: Mapping[str, object] = {
    "columns": {
        "repo": "cwd の repo。session はこの repo のものだけを数える",
        "sessions_observed": "repo に絞った main session の数。session の repo は窓の中で最初に現れた cwd で決まり、"
                             "repo を解決できなかった session は落ちて sessions_unresolved に入る",
        "sessions_out_of_scope": "repo に解決でき、別の repo と判って落ちた session の数",
        "sessions_unresolved": "repo を解決できずに落ちた session の数の cause 別の内訳 ({cause: 数})。cause は session の最初の cwd を "
                               "repo の解決に掛けたときの理由 (cwd_missing は cwd が実在しない。消えた worktree など) と、"
                               "この問いが足す cwd_unobserved (窓の中に cwd を持つ行が無い)・cwd_outside_records "
                               "(最初の cwd が record として読めない行にしか無い)。"
                               "解決できなかった session が無ければ空",
        "static_context": "sessions_observed (静的コンテキストを持つ session の数)・per_session_est_tokens・sources[] (source・sessions・est_tokens)",
        "memory_files": "file ごとの注入実績。path・display_path・memory_type・path_globs・sessions_injected・est_tokens・chars・lines・"
                        "absolute_paths_folded・sample_path・differs_from_disk_sessions・first_injected_at・last_injected_at",
        "compaction": "sessions_with_boundary・total_boundaries・by_trigger・dropped_tokens・sessions[] (session_id・boundaries・"
                      "cumulative_dropped_tokens・max_pre_tokens)",
    },
    "token_estimator": "approx: cjk 1 字/token + その他 4 字/token (tokenizer 非使用)",
    "notes": [
        "token 数はすべて概算。桁の比較にだけ使い、bucket 判定の単独根拠にしない",
        "session は main だけを数える。subagent への注入は観測の外",
        "est_tokens と dropped_tokens の統計 (observed・p50・p95・max・total) は session あたりの値の統計で、observed はその値を持つ session の数。"
        "boundary に捨てた token の値が無い session は dropped_tokens では 0 として数える",
        "sources の値は窓の中で載った量の累計で、session の途中の注入と再注入を含む。memory_file は memory_files[] の注入の累計で、"
        "session の開始時に載る root の CLAUDE.md は入らない",
        "memory_files[] は観測した注入だけを載せる。窓の中で 1 度も注入されなかった file は 0 の行ではなく行の不在として現れるので、"
        "静的な file の一覧と差分を取る",
        "sessions_unresolved が空でないとき、その session の注入は memory_files[] にも static_context にも入らない。"
        "その session がこの repo のものかは決まらないので、memory_files[] の行の不在を注入 0 の証拠にしない",
        "memory_files[] の sessions_injected は実際に注入された session の数で、path_globs で絞った規範が届いているかの実測。"
        "est_tokens・chars・lines は注入ごとの値の最大",
        "memory_files[] の path は `.claude/worktrees/<名前>` の段を畳んだ値で、cwd の repo の main checkout の下は root からの path、"
        "user の `~/.claude/` の下は `~/.claude/` からの path、main checkout の外では注入した session の cwd の直下の `.claude/` の下 (repo の外の worktree・別の clone) だけが "
        "`.claude/` からの path で、それ以外 (repo の外の worktree の root の CLAUDE.md を含む) は絶対 path。"
        "bare の repo の worktree・submodule は main checkout を持たず、cwd の直下の `.claude/` の下の外は絶対 path。畳んだ絶対 path の数が absolute_paths_folded に出る",
        "注入実績が決定的なのは file 粒度まで。行粒度の実績は transcript に無い",
        "compaction の cumulative_dropped_tokens は session ごとの最大値で、boundary をまたいで足さない",
    ],
}

QUERY = NamedQuery(contract=CONTRACT, build=_build)
