-- sessions slice の観測契約 (ADR 0031 の query 層 / ADR 0074)。
--
-- **transcript の生 key 名を書かない。** 参照してよいのは store の列と、
-- `udf.py` が登録した純関数だけ (`scripts/gate/verify-query-format-isolation.py`
-- が機械検査する)。
--
-- 観測窓は持たない — 名指しされた session を丸ごと見るのが本 slice の用途で、
-- 期間で切ると session の途中が欠ける。
--
-- **session 内の順序**: file は最初の record の ts 順、file 内は行順。ts だけで並べると
-- 同時刻の record の前後が崩れ、行順だけでは session が複数 file に跨ったときの
-- file 間の前後が決まらない。この順序の定義は `requested_file` 1 箇所に置き、
-- 後続の文はそこから引く。
--
-- 文は `-- name: <識別子>` で区切り、`marts.load_statements` が名前で引く。
-- `requested_file` は後続の文が参照する接続ローカルの temp 表で、作成と参照の順序は
-- `present.query_requested_sessions` 1 箇所が持つ。

-- 対象 session が載っている transcript file と、その file での session の開始 ts。
-- 対象 session は `:session_ids` (JSON array の文字列) で渡す。
-- name: requested_file
CREATE TEMP TABLE requested_file AS
SELECT r.file_id AS file_id, r.session_id AS session_id,
       min(r.ts_epoch) AS start_epoch, f.path AS path
FROM record r
JOIN file f ON f.file_id = r.file_id
WHERE r.session_id IN (SELECT value FROM json_each(:session_ids))
GROUP BY r.file_id, r.session_id

-- session ごとの transcript file。1 行も無い session id は「未観測」で、
-- presentation 層が欠落一覧へ回す。
-- name: session_files
SELECT session_id, path
FROM requested_file
ORDER BY session_id, start_epoch, path

-- tool_use の時系列。**同じ record (record_uuid) が複数 file に再掲されたら最初の
-- 1 件だけを採る** — resume 等で過去の record が新しい file に写ると、同じ呼び出しが
-- 2 度 timeline に載り deny も二重に数えられる。record_uuid が空の record は再掲を判定
-- できないので file と行で個別に扱う。`outcome` は permissions mart の refined 語彙
-- (success / unknown / error / deny_user-rejected / deny_permission-rule /
-- deny_automode) に `deny_hook` を足したもの。permissions mart は permission entry を
-- 評価するので hook の deny を error に畳むが、本 slice は「止められたか」を見るので
-- hook も deny 側に出す (CASE を mart ごとに持つのは ADR 0013 の観測契約の線)。
-- name: tool_uses
WITH sequenced AS (
    SELECT
        r.session_id     AS session_id,
        r.ts             AS ts,
        rf.start_epoch   AS start_epoch,
        rf.path          AS file_path,
        tu.line_no       AS line_no,
        tu.block_no      AS block_no,
        CASE WHEN r.record_uuid = '' THEN tu.file_id || ':' || tu.line_no
             ELSE r.record_uuid END AS record_key,
        tu.tool          AS tool,
        tu.command       AS command,
        tu.target_path   AS target_path,
        tu.target_url    AS target_url,
        tu.unit_id       AS unit_id,
        tu.input_excerpt AS input_excerpt,
        tu.result_text   AS result_text,
        CASE
            WHEN tu.outcome_base IN ('success', 'unknown') THEN tu.outcome_base
            WHEN tu.outcome_base = 'user-reject' THEN 'deny_user-rejected'
            WHEN tu.denial_kind = 'permission-rule' THEN 'deny_permission-rule'
            WHEN tu.denial_kind IN ('automode-blocked', 'automode-unavailable')
                THEN 'deny_automode'
            WHEN tu.denial_kind = 'hook' THEN 'deny_hook'
            WHEN looks_like_permission_denial(tu.result_text)
                THEN 'deny_permission-rule'
            WHEN looks_like_automode_denial(tu.result_text) THEN 'deny_automode'
            ELSE 'error'
        END AS outcome
    FROM tool_use tu
    JOIN record r ON r.file_id = tu.file_id AND r.line_no = tu.line_no
    JOIN requested_file rf ON rf.file_id = tu.file_id AND rf.session_id = r.session_id
),
deduplicated AS (
    SELECT *, row_number() OVER (
        PARTITION BY session_id, record_key, block_no
        ORDER BY start_epoch, file_path, line_no
    ) AS occurrence
    FROM sequenced
)
SELECT session_id, ts, tool, command, target_path, target_url, unit_id,
       input_excerpt, result_text, outcome
FROM deduplicated
WHERE occurrence = 1
ORDER BY session_id, start_epoch, file_path, line_no, block_no

-- session ごとの最終 text: text を持つ最後の assistant record の text block 全部
-- (block 順)。1 message に見出しと本文が別 block で並ぶことがあるので、最後の
-- 1 block だけでは報告が欠ける。連結は presentation 層が行う。
-- name: final_text_blocks
WITH ranked_record AS (
    SELECT
        r.session_id AS session_id,
        at.file_id   AS file_id,
        at.line_no   AS line_no,
        row_number() OVER (
            PARTITION BY r.session_id
            ORDER BY rf.start_epoch DESC, rf.path DESC, at.line_no DESC
        ) AS position_from_end
    FROM (SELECT DISTINCT file_id, line_no FROM assistant_text) at
    JOIN record r ON r.file_id = at.file_id AND r.line_no = at.line_no
    JOIN requested_file rf ON rf.file_id = at.file_id AND rf.session_id = r.session_id
)
SELECT rr.session_id AS session_id, r.ts AS ts, at.text AS text
FROM ranked_record rr
JOIN record r ON r.file_id = rr.file_id AND r.line_no = rr.line_no
JOIN assistant_text at ON at.file_id = rr.file_id AND at.line_no = rr.line_no
WHERE rr.position_from_end = 1
ORDER BY rr.session_id, at.block_no
