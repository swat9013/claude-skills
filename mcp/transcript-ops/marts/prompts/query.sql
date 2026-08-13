-- prompts mart の観測契約 (ADR 0031 の query 層。scan_prompts / select_candidates 共有)。
--
-- **transcript の生 key 名を書かない。** 参照してよいのは store の列と、
-- `udf.py` が登録した純関数だけ (`scripts/gate/verify-query-format-isolation.py`
-- が機械検査する)。形式の解釈が要るなら ingest 側の責務。
--
-- 文は `-- name: <識別子>` で区切り、`marts.load_statements` が名前で引く。

-- 観測窓で絞った user record の作業表。**呼び出しごとに作り直す**
-- (scan_prompts / select_candidates で `days` が異なりうる)。seq は挿入順 =
-- lake の走査順で、同時刻の tie-break に使う。
-- name: create_scoped_prompt
CREATE TEMP TABLE IF NOT EXISTS scoped_prompt (
    seq              INTEGER PRIMARY KEY,
    project_dir      TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    record_uuid      TEXT NOT NULL,
    ts               TEXT NOT NULL,
    ts_epoch         REAL,
    cwd              TEXT NOT NULL,
    git_branch       TEXT NOT NULL,
    prompt_source    TEXT,
    cli_version      TEXT NOT NULL,
    text             TEXT NOT NULL,
    text_chars       INTEGER NOT NULL,
    steering_pattern TEXT NOT NULL,
    reason           TEXT NOT NULL
)

-- name: create_scoped_prompt_index
CREATE INDEX IF NOT EXISTS scoped_prompt_reason_idx ON scoped_prompt (reason)

-- name: clear_scoped_prompt
DELETE FROM scoped_prompt

-- user record 1 件を採用 / 除外理由へ確定させる。**この CASE の分岐順が仕様**
-- (record ごとに 1 理由へ確定させるため): tool 実行結果を運ぶか → slash command の
-- 展開 record か → `#rule ` 捕捉対象か → context 圧縮の要約注入か → skill 本文注入
-- (`is_meta`) か → subagent 側の prompt (`is_sidechain`) か → `prompt_source` 欠落か
-- → `prompt_source` が非 human か → `origin_kind` が非 human か → 空文字か →
-- 残りは採用。totality (scanned == accepted + 各除外の和) はこの CASE が全域
-- (ELSE を持つ) であることから構造的に成立する。
--
-- 窓は `ts_epoch` で切る。`NULL` (ts 欠損) は保守的に窓内へ倒す — 件数は
-- store の観測劣化シグナルに出るので、黙って増えることはない。
--
-- reason の 1 番目の枝だけ SQL 内部 label (`content_is_tool_result`) を使う —
-- 公開 reason 語彙 (`contract.EXCLUSION_REASONS` の先頭) はそれ自体が on-disk の
-- record type 名と同綴りで gate の禁止語に触れるため、`present.py` の
-- `REASON_SQL_ALIASES` で変換する (SQL は関係の形だけを持ち、公開語彙への写像は
-- presentation 層に置く)。
-- name: refined_prompt
SELECT
    f.project_dir AS project_dir,
    r.session_id AS session_id,
    r.record_uuid AS record_uuid,
    r.ts AS ts,
    r.ts_epoch AS ts_epoch,
    r.cwd AS cwd,
    up.git_branch AS git_branch,
    up.prompt_source AS prompt_source,
    up.cli_version AS cli_version,
    up.text AS text,
    char_length(up.text) AS text_chars,
    classify_steering_pattern(up.text) AS steering_pattern,
    CASE
        WHEN up.has_tool_result THEN 'content_is_tool_result'
        WHEN is_slash_expansion(up.text) THEN 'slash_command_expansion'
        WHEN is_rule_capture(up.text) THEN 'rule_capture'
        WHEN up.is_compact_summary THEN 'compact_summary'
        WHEN up.is_meta THEN 'meta_injected'
        WHEN up.is_sidechain THEN 'sidechain'
        WHEN up.prompt_source IS NULL THEN 'no_prompt_source'
        WHEN up.prompt_source NOT IN ('typed', 'queued') THEN 'non_human_prompt_source'
        WHEN up.origin_is_dict
             AND (up.origin_kind IS NULL OR up.origin_kind <> 'human')
            THEN 'non_human_origin'
        WHEN is_blank(up.text) THEN 'empty_text'
        ELSE 'accepted'
    END AS reason
FROM user_prompt up
JOIN record r ON r.file_id = up.file_id AND r.line_no = up.line_no
JOIN file f ON f.file_id = up.file_id
WHERE r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch
-- 走査順 = (project dir 名, file 名, 行)。seq がこの順を写す
ORDER BY f.project_dir, f.path, r.line_no

-- 採用 / 除外理由ごとの件数。合計が scanned_user_records に一致する (totality)。
-- name: verdict_totals
SELECT reason, count(*) AS n
FROM scoped_prompt
GROUP BY reason

-- 採用された手入力 prompt。読み順は ts → session_id → record_uuid の全順序。
-- **repo は解決しない** (git 呼び出しを伴う I/O のため present 層が cwd 単位で行う)。
-- name: accepted_prompt
SELECT seq, project_dir, session_id, record_uuid, ts, ts_epoch, cwd, git_branch,
       prompt_source, cli_version, text, text_chars, steering_pattern
FROM scoped_prompt
WHERE reason = 'accepted'
ORDER BY ts, session_id, record_uuid

-- 定型判定 (select_candidates)。**母数は min_chars を通した全 repo の採用 prompt**
-- (repo 絞り込み前) — corpus 全体で検出し、除外の帰属だけ repo scope 側 (present 層)
-- で数える。正規形が `:boilerplate_min_group` 件以上ある行だけを返す。
-- name: boilerplate_membership
WITH candidate AS (
    SELECT seq, normalize_text(text) AS form
    FROM scoped_prompt
    WHERE reason = 'accepted' AND text_chars >= :min_chars
),
grouped AS (
    SELECT form, count(*) AS n
    FROM candidate
    GROUP BY form
    HAVING count(*) >= :boilerplate_min_group
)
SELECT candidate.seq AS seq, candidate.form AS form, grouped.n AS group_size
FROM candidate
JOIN grouped USING (form)
