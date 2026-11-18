"""名指しした repo 内の文書ごとに、観測窓の中でその文書を Read した session の数を数える問い。

索引行が指す文書が読まれているかを、消費者の棚卸しの evidence にする。
文書は repo root からの相対 path で受け、どの文書も 0 の行を返す (行の不在で 0 を表さない)。

invariant:
1. cwd の repo を解決できなければ全 repo へ倒さずに止まる。他の repo の session を混ぜると分母が崩れる
2. session は cwd の repo の main session だけを数える。subagent の record は親の session id を持つので、母集団を既定の main から広げない
3. Read の path が、その session の最初の cwd (worktree の段を畳む) の下の文書の path と一致するものだけを数える。
   別の repo・cwd の外・cwd の下の別の位置にある同じ末尾の file は数えない
4. 文書の path が 1 つも渡されなければ止まる。空の一覧で走ると、全行 0 にもならず 0 行の成果物が返る
"""

from __future__ import annotations

import os

from named_query import NamedQuery, QueryRun, Stopped

# 時刻の無い record は窓から落とさない。overhead とは意図して共有しない (観測窓と repo の解決は問いごとの schema に属する。ADR 0013)
IN_WINDOW = "(ts_epoch IS NULL OR ts_epoch BETWEEN $since AND $until)"

# session の repo は窓の中で最初に現れた cwd で決まる。line_seq は読む順に増え、file は path の順に読む
REPO_SESSIONS = f"""
WITH cwds AS MATERIALIZED (
    SELECT session_id, cwd, line_seq
    FROM lake_session_cwd
    WHERE {IN_WINDOW}
)
SELECT c.session_id, c.cwd
FROM (SELECT session_id, arg_min(cwd, line_seq) AS cwd FROM cwds GROUP BY session_id) c
JOIN lake_cwd_repo r ON r.cwd = c.cwd
WHERE r.repo = $repo
"""

FOLD_WORKTREE = "regexp_replace({}, '/\\.claude/worktrees/[^/]+', '', 'g')"

READS = f"""
WITH roots AS (
    SELECT unnest($sessions::VARCHAR[]) AS session_id, unnest($roots::VARCHAR[]) AS root
), reads AS MATERIALIZED (
    SELECT u.session_id, u.ts, {FOLD_WORKTREE.format('u.target_path')} AS target,
           {FOLD_WORKTREE.format('roots.root')} AS root
    FROM lake_tool_use u
    JOIN roots ON roots.session_id = u.session_id
    WHERE u.tool = 'Read' AND u.target_path <> '' AND {IN_WINDOW}
)
SELECT t.path,
       count(DISTINCT r.session_id) AS sessions_read,
       max(nullif(r.ts, '')) AS last_read_at
FROM (SELECT unnest($targets::VARCHAR[]) AS path) t
LEFT JOIN reads r ON r.target = r.root || '/' || t.path
GROUP BY t.path
ORDER BY t.path
"""


def _targets(text: str) -> list[str]:
    targets = sorted({part.strip().strip("/") for part in text.split(",") if part.strip()})
    if not targets:
        raise Stopped("bad_argument", "paths には repo root からの相対 path をカンマ区切りで 1 つ以上渡してください")
    return targets


def _build(run: QueryRun) -> list[dict]:
    targets = _targets(run.params["paths"])
    cwd = os.getcwd()
    resolved = run.resolve_repo(cwd)
    if resolved["repo"] is None:
        raise Stopped("bad_argument", f"cwd の {cwd} から repo を解決できません ({resolved['cause']})。"
                                      "git が動く環境で、repo の中から呼び直してください")
    repo = resolved["repo"]
    first_cwds = {row["session_id"]: row["cwd"] for row in run.select(REPO_SESSIONS, repo=repo)}
    sessions = sorted(first_cwds)
    run.meta["repo"] = repo
    run.meta["sessions_observed"] = len(sessions)
    return run.select(READS, sessions=sessions, roots=[first_cwds[s] for s in sessions], targets=targets)


CONTRACT = {
    "columns": {
        "path": "渡した文書の path (repo root からの相対)",
        "sessions_read": "観測窓の中でこの文書を main session が Read した session の数",
        "last_read_at": "窓の中で最後に Read した record の timestamp。読まれていなければ null",
    },
    "meta": {
        "repo": "cwd の repo。session はこの repo のものだけを数える",
        "sessions_observed": "repo に絞った session の数 (sessions_read の分母)。session の repo は窓の中で最初に現れた cwd で決まる",
    },
    "notes": [
        "数えるのは main session の Read tool の呼出しだけで、subagent の Read・Bash の cat や grep による読み・hook や skill の注入で載った本文は数えない",
        "数えない経路は 2 つ。(a) 最初の cwd が repo root でない session (repo の subdirectory で始めた session)。"
        "(b) cwd の外の path で読んだ Read (symlink 経由で repo の文書を読んだ Read など)。"
        "どちらも読まれていても sessions_read に入らないので、sessions_read は少なめの値で、0 は読まれていないことの証拠にならない",
        "worktree (`.claude/worktrees/<名前>`) の段は畳んで照合する",
        "session の repo は窓の中で最初に現れた cwd で決まり、cwd が消えた (削除した worktree など) session は落ちる",
        "sessions_read が 0 の行は、その文書が窓の中で Read されなかったことだけを示し、効いていないのか場面が起きていないのかは区別しない",
    ],
}

QUERY = NamedQuery(contract=CONTRACT, params={"paths": None}, build=_build)
