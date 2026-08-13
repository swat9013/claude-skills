-- invocations mart の観測契約 (ADR 0031 の query 層)。
--
-- **transcript の生 key 名を書かない。** 参照してよいのは store の列だけ
-- (`scripts/gate/verify-query-format-isolation.py` が機械検査する)。
--
-- unit (skill / agent / mcp_tool) への振り分け・分母との突合・提示分母の単位型
-- 写像は presentation 層 (`present.py`) の観測契約。本 file は「窓で絞った行を
-- 順序どおりに返す」relational algebra だけを持つ。
--
-- 文は `-- name: <識別子>` で区切り、`marts.load_statements` が名前で引く。

-- Skill / Agent / mcp__* / Read の tool_use。unit 判定 (Read は config 依存の
-- skill_md_paths 突合が要る) は presentation 層が行う。
-- name: candidate_tool_use
SELECT f.project_dir AS project_dir, f.path AS file_path, r.line_no AS line_no,
       tu.block_no AS block_no, tu.tool AS tool, tu.unit_id AS unit_id,
       tu.target_path AS target_path, tu.input_excerpt AS input_excerpt,
       tu.outcome_base AS outcome, tu.attribution_skill AS attribution_skill,
       r.session_id AS session_id, r.ts AS ts
FROM tool_use tu
JOIN record r ON r.file_id = tu.file_id AND r.line_no = tu.line_no
JOIN file f ON f.file_id = tu.file_id
WHERE (r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch)
  AND (tu.tool IN ('Skill', 'Agent', 'Read') OR tu.tool LIKE 'mcp\_\_%' ESCAPE '\')
ORDER BY f.project_dir, f.path, r.line_no, tu.block_no

-- 実呼出しの slash command (`parse_slash_invocation` が ingest 側で確定済み)。
-- name: slash_events
SELECT f.project_dir AS project_dir, f.path AS file_path, r.line_no AS line_no,
       si.command_name AS command_name, r.session_id AS session_id, r.ts AS ts
FROM slash_invocation si
JOIN record r ON r.file_id = si.file_id AND r.line_no = si.line_no
JOIN file f ON f.file_id = si.file_id
WHERE r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch
ORDER BY f.project_dir, f.path, r.line_no

-- 人間可読の user turn 抜粋 (invocation 直前文脈の breadcrumb)。
-- name: user_text_events
SELECT f.project_dir AS project_dir, f.path AS file_path, r.line_no AS line_no,
       ut.text_excerpt AS text_excerpt
FROM user_turn ut
JOIN record r ON r.file_id = ut.file_id AND r.line_no = ut.line_no
JOIN file f ON f.file_id = ut.file_id
WHERE r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch
ORDER BY f.project_dir, f.path, r.line_no

-- 窓内で観測された全 tool_use の (tool, session) の distinct 組。deferred tool の
-- 分子 (`units` に対応 unit 型が無い built-in tool) と session attribute
-- (has_code_edit / has_plan_mode) の両方がこの 1 本から作れる。
-- name: all_tool_use_sessions
SELECT DISTINCT tu.tool AS tool, r.session_id AS session_id
FROM tool_use tu
JOIN record r ON r.file_id = tu.file_id AND r.line_no = tu.line_no
WHERE (r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch) AND tu.tool != ''

-- 「session に提示された分母」の 1 名前 = 1 行。type 別の意味 (どの type が
-- どの unit 型か) は presentation 層の観測契約 (`PRESENTED_UNIT_TYPE`)。
-- name: presented_events
SELECT pn.attachment_type AS attachment_type, pn.name AS name,
       r.session_id AS session_id, r.ts AS ts
FROM presented_name pn
JOIN record r ON r.file_id = pn.file_id AND r.line_no = pn.line_no
WHERE r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch
ORDER BY pn.line_no, pn.seq

-- 静的コンテキスト種別ごとに観測された session (名前 0 件の record でも
-- `static_payload` に row が立つ)。type ごとの絞り込みは presentation 層が行う
-- (query 層は on-disk type 値をリテラルで持たない)。
-- name: static_payload_sessions
SELECT DISTINCT sp.attachment_type AS attachment_type, r.session_id AS session_id
FROM static_payload sp
JOIN record r ON r.file_id = sp.file_id AND r.line_no = sp.line_no
WHERE (r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch) AND r.session_id != ''

-- assistant turn の token 経済 (帰属 skill 別の内訳は presentation 層が畳む)。
-- name: assistant_usage
SELECT at.attribution_skill AS attribution_skill,
       at.input_tokens AS input_tokens, at.output_tokens AS output_tokens,
       at.cache_creation_input_tokens AS cache_creation_input_tokens,
       at.cache_read_input_tokens AS cache_read_input_tokens
FROM assistant_turn at
JOIN record r ON r.file_id = at.file_id AND r.line_no = at.line_no
WHERE r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch
