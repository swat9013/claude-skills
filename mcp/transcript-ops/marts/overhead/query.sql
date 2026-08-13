-- overhead mart の観測契約 (ADR 0031 の query 層)。
--
-- **transcript の生 key 名を書かない。** 参照してよいのは store の列と、
-- `udf.py` が登録した純関数だけ (`scripts/gate/verify-query-format-isolation.py`
-- が機械検査する)。静的コンテキストの種別ラベル (mart 語彙) と repo scope による
-- session 絞り込みは presentation 層の観測契約 (query 層は on-disk type 値・
-- git 解決を持たない)。
--
-- 文は `-- name: <識別子>` で区切り、`marts.load_statements` が名前で引く。

-- 静的コンテキスト 4 種 (memory file を除く) の per-record token 概算を
-- (type, session) 単位で合算する。**per-record に ceil してから合算**する
-- (合算してから ceil すると 1 session に複数 record がある場合に旧実装と
-- 数値がずれる)。
-- name: static_source_costs
SELECT sp.attachment_type AS attachment_type, r.session_id AS session_id,
       sum(estimate_tokens_from_counts(sp.cjk_chars, sp.chars - sp.cjk_chars))
           AS tokens
FROM static_payload sp
JOIN record r ON r.file_id = sp.file_id AND r.line_no = sp.line_no
WHERE r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch
GROUP BY sp.attachment_type, r.session_id

-- memory file 注入 1 件 = 1 行。`memory_key` (path 正規化 UDF) と `tokens`
-- (token 概算 UDF) を列として渡し、畳み込み (worktree 折り畳み・repo scope による
-- session 絞り込み) は presentation 層が行う。**`display_path` / `memory_type` /
-- `globs` は畳み込みの「最初に見た値」を採る**規約 (presentation 層) なので、
-- walk 順 (project_dir, file, line) を保証する ORDER BY が要る — 順序を保証しないと
-- SQLite の物理走査順に依存し、実行のたびに代表値が変わりうる。
-- name: memory_injection_rows
SELECT memory_key(mi.path) AS memory_key, mi.path AS path,
       mi.display_path AS display_path, mi.memory_type AS memory_type,
       mi.globs AS globs, mi.chars AS chars, mi.lines AS lines,
       mi.differs_from_disk AS differs_from_disk,
       estimate_tokens_from_counts(mi.cjk_chars, mi.chars - mi.cjk_chars) AS tokens,
       r.session_id AS session_id, r.ts AS ts
FROM memory_injection mi
JOIN record r ON r.file_id = mi.file_id AND r.line_no = mi.line_no
JOIN file f ON f.file_id = mi.file_id
WHERE r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch
ORDER BY f.project_dir, f.path, r.line_no

-- compaction boundary。`cumulative_dropped_tokens` は session 内で累積するので、
-- session ごとの畳み込み (最大値を採る) は presentation 層が行う。
-- name: compact_boundaries
SELECT r.session_id AS session_id, r.cwd AS cwd, cb.trigger AS trigger,
       cb.pre_tokens AS pre_tokens,
       cb.cumulative_dropped_tokens AS cumulative_dropped_tokens
FROM compact_boundary cb
JOIN record r ON r.file_id = cb.file_id AND r.line_no = cb.line_no
WHERE r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch

-- session の cwd (repo scope 解決の入力)。同一 session 内で複数 cwd が観測された
-- 場合の tie-break (最初に観測された値を採る) は presentation 層が行う。
-- name: session_cwd_events
SELECT f.project_dir AS project_dir, f.path AS file_path, r.line_no AS line_no,
       r.session_id AS session_id, r.cwd AS cwd
FROM record r
JOIN file f ON f.file_id = r.file_id
WHERE (r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch)
  AND r.session_id != '' AND r.cwd != ''
ORDER BY f.project_dir, f.path, r.line_no
