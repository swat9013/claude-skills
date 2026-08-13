-- transcript store の DDL (ADR 0031)。
--
-- store は lake の純関数であり authored data を 1 bit も含まない。よって
-- **migration を書かない** — schema を変えたら store.STORE_VERSION を上げ、
-- 新しい file 名 (store-v<N>.sqlite3) に drop & rebuild する。ALTER も down
-- migration も持たない。
--
-- 構成は spine (record) + projection (tool_use / hook_firing)。spine は lake の
-- 全 record type を 1 行ずつ受ける (未知 type も落とさない = 不変条件 I4)。
-- projection は「その形で読みたい mart がある record」を平坦化した派生表で、
-- 追加は spine を変えずに行える。

CREATE TABLE IF NOT EXISTS store_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ingest 済み file と fingerprint。(mtime_ns, size_bytes) は **read の前に stat し
-- 読込完了後にのみ記録する** (store.ingest 側の順序保証)。読込中に伸びた file は
-- fingerprint が不一致になり、次回の sync で必ず再取得される。
CREATE TABLE IF NOT EXISTS file (
    file_id      INTEGER PRIMARY KEY,
    path         TEXT NOT NULL UNIQUE,
    project_dir  TEXT NOT NULL,
    mtime_ns     INTEGER NOT NULL,
    size_bytes   INTEGER NOT NULL,
    record_count INTEGER NOT NULL,
    -- JSON として解釈できなかった行数。0 でない値が出ること自体が観測の劣化シグナル
    -- なので、黙って skip せず file 単位で数える (silent skip の可視化)
    broken_lines INTEGER NOT NULL,
    ingested_at  TEXT NOT NULL
);

-- 読めなかった file。**存在しない file と読めない file を同じ「0 件」に潰さない**。
CREATE TABLE IF NOT EXISTS unreadable_file (
    path        TEXT PRIMARY KEY,
    project_dir TEXT NOT NULL,
    reason      TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

-- spine。lake の 1 行 = 1 record = 本表の 1 行。
--
-- ts_epoch は窓判定の**機構**だけを提供する (unix 秒。ts 欠損・解釈不能は NULL)。
-- NULL をどちらへ倒すかは各 mart の WHERE 句が書く (ADR 0013 の線)。
CREATE TABLE IF NOT EXISTS record (
    file_id     INTEGER NOT NULL,
    line_no     INTEGER NOT NULL,
    record_uuid TEXT NOT NULL DEFAULT '',
    record_type TEXT NOT NULL,
    subtype     TEXT NOT NULL DEFAULT '',
    session_id  TEXT NOT NULL DEFAULT '',
    ts          TEXT NOT NULL DEFAULT '',
    ts_epoch    REAL,
    cwd         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (file_id, line_no)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS record_type_ts_idx ON record (record_type, ts_epoch);
CREATE INDEX IF NOT EXISTS record_uuid_idx ON record (record_uuid);

-- projection: assistant record の tool_use block を 1 行に平坦化し、対応する
-- tool_result の判定結果を**同じ行に畳んである**。
--
-- ペアリングを ingest でやり切るのは、`(file, tool_use_id)` の一意性が lake で
-- 保証されないため。SQL の join 述語で結ぶと重複 id が cross product になり
-- 静かに二重計上する (ADR 0031 が「pairing の file スコープが join 述語に変わる」
-- として挙げた劣化)。ingest なら「1 つの tool_result を消費する tool_use は
-- 高々 1 件」という一対一を構造で保てる (同じ id が重複したら最後の tool_use が
-- 受け取り、先行分は paired = 0 で残る)。
--
-- outcome_base は adapter の base 語彙 (success / error / user-reject / unknown)。
-- mart 固有の細分 (permissions の 7 分類) は query 層の責務なので持ち込まない。
-- 判定を 1 箇所に固定するのは、2 scanner が別々に判定していた間に `is_error` 欠落時の
-- 解釈が逆になり、同一 record について 2 mart が矛盾した答えを返したため (#476)。
--
-- tool 入力は `command` / `target_path` / `input_excerpt` の 3 列へ正規化する。
-- 生の input JSON は保存しない — Write / Edit の input は file 全文を含み、
-- 全 mart が要求しない量になる (input 本文を捨てる選択: ADR 0031)。集約キー
-- (permissions の command_head 等) は本表からの導出なので query 層の UDF が作る。
CREATE TABLE IF NOT EXISTS tool_use (
    file_id       INTEGER NOT NULL,
    line_no       INTEGER NOT NULL,
    block_no      INTEGER NOT NULL,
    tool_use_id   TEXT NOT NULL DEFAULT '',
    tool          TEXT NOT NULL DEFAULT '',
    -- Bash の command 全文。matcher が exact / prefix 照合に使う (他 tool は空文字)
    command       TEXT NOT NULL DEFAULT '',
    -- file path 系 tool の照合対象 (file_path / path / notebook_path の最初の 1 つ)
    target_path   TEXT NOT NULL DEFAULT '',
    input_excerpt TEXT NOT NULL DEFAULT '',
    outcome_base  TEXT NOT NULL,
    denial_kind   TEXT NOT NULL DEFAULT '',
    result_text   TEXT NOT NULL DEFAULT '',
    paired        INTEGER NOT NULL DEFAULT 0,
    -- v2 (#498): Skill / Agent tool_use の識別引数 (skill 名 / subagent_type)。
    -- 他 tool は空文字。matcher と同じ理由 (input JSON の生 key 名を query 層に
    -- 漏らさない) で、抜き出しを ingest 側の列に固定する
    unit_id            TEXT NOT NULL DEFAULT '',
    -- v2 (#498): 同一 assistant record 内の全 tool_use block が共有する帰属 skill
    -- (`attributionSkill`)。turn 単位の値なので block ごとに重複して持つ
    attribution_skill  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (file_id, line_no, block_no)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS tool_use_tool_idx ON tool_use (tool);

-- projection: hook 系 attachment の fire 実績 (#478 P4 — 統治対象の 3 本目に観測が
-- 無かった穴)。exit_code / duration_ms は成功系 attachment にしか載らないため
-- NULL 可 — **NULL を 0 と読まない** (未観測と成功の混同)。
CREATE TABLE IF NOT EXISTS hook_firing (
    file_id         INTEGER NOT NULL,
    line_no         INTEGER NOT NULL,
    hook_name       TEXT NOT NULL DEFAULT '',
    hook_event      TEXT NOT NULL DEFAULT '',
    attachment_type TEXT NOT NULL DEFAULT '',
    command         TEXT NOT NULL DEFAULT '',
    exit_code       INTEGER,
    duration_ms     INTEGER,
    timed_out       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_id, line_no)
) WITHOUT ROWID;

-- v2 (#498, invocations/overhead mart の query 化 / #497, scan_prompts /
-- select_candidates の query 化) から以下追加。既存 table は変更しない
-- (両 issue が同時進行のため append-only に保つ)。

-- projection: 実呼出の slash command (`/xxx`)。判定 (`parse_slash_invocation`) は
-- on-disk 形式知識 (展開 record の tag 構造) なので ingest がここで済ませ、
-- query 層は command 名だけを引く。
CREATE TABLE IF NOT EXISTS slash_invocation (
    file_id      INTEGER NOT NULL,
    line_no      INTEGER NOT NULL,
    command_name TEXT NOT NULL,
    PRIMARY KEY (file_id, line_no)
) WITHOUT ROWID;

-- projection: user turn の表示可能 text の抜粋 (`extract_user_text`)。tool_use の
-- `input_excerpt` と同じ考え方 (`INPUT_EXCERPT_LIMIT` 200 字) で、invocation の
-- 直前文脈を復元するための breadcrumb。原文全体は scan_prompts (#497) の領分。
CREATE TABLE IF NOT EXISTS user_turn (
    file_id      INTEGER NOT NULL,
    line_no      INTEGER NOT NULL,
    text_excerpt TEXT NOT NULL,
    PRIMARY KEY (file_id, line_no)
) WITHOUT ROWID;

-- projection: assistant turn 単位の token 経済 (`usage_of`) + 帰属 skill。usage が
-- 無い turn (稀) は row を作らない — `assistant_turns` の分母を usage 観測数と
-- 一致させるため。
CREATE TABLE IF NOT EXISTS assistant_turn (
    file_id                     INTEGER NOT NULL,
    line_no                     INTEGER NOT NULL,
    attribution_skill           TEXT NOT NULL DEFAULT '',
    input_tokens                INTEGER NOT NULL DEFAULT 0,
    output_tokens               INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_id, line_no)
) WITHOUT ROWID;

-- projection: 「session に提示された分母」attachment (skill_listing /
-- mcp_instructions_delta / agent_listing_delta / deferred_tools_delta) が列挙する
-- 名前。1 record が複数名を持ちうるので `seq` で行を分ける (`PRESENTED_NAME_FIELDS`
-- が単位型ごとの field 名を持つ形式知識で、ingest がここで平坦化する)。
CREATE TABLE IF NOT EXISTS presented_name (
    file_id         INTEGER NOT NULL,
    line_no         INTEGER NOT NULL,
    seq             INTEGER NOT NULL,
    attachment_type TEXT NOT NULL,
    name            TEXT NOT NULL,
    PRIMARY KEY (file_id, line_no, seq)
) WITHOUT ROWID;

-- projection: 静的コンテキスト 4 種 (skill_listing / mcp_instructions_delta /
-- deferred_tools_delta / agent_listing_delta) の本文サイズ。`chars` / `cjk_chars` を
-- 持つのは token 概算を query 層の UDF (`estimate_tokens_from_counts`) の責務に
-- 保つため (ADR 0031 の UDF 純関数規律)。memory file (5 つ目の静的 source) は
-- `memory_injection` に別枠を持つ (path / globs 等の追加属性があるため)。
-- **type ごとに 1 record = 1 row** (名前が 0 件の record も row を作る) —
-- overhead mart の `sessions_with_skill_listing` 判定はこの row の存在で行う。
CREATE TABLE IF NOT EXISTS static_payload (
    file_id         INTEGER NOT NULL,
    line_no         INTEGER NOT NULL,
    attachment_type TEXT NOT NULL,
    chars           INTEGER NOT NULL DEFAULT 0,
    cjk_chars       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_id, line_no)
) WITHOUT ROWID;

-- projection: memory file (`nested_memory` attachment) の 1 注入。`path` は観測
-- された絶対 path そのもの (worktree 断片の畳み込み・repo 相対化は mart 側の
-- 集計単位の話なので ingest へ持ち込まない)。`globs` は JSON array のまま保存し、
-- 消費側 (presentation 層) が decode する (query 層は opaque な列として扱う)。
CREATE TABLE IF NOT EXISTS memory_injection (
    file_id           INTEGER NOT NULL,
    line_no           INTEGER NOT NULL,
    path              TEXT NOT NULL DEFAULT '',
    display_path      TEXT NOT NULL DEFAULT '',
    memory_type       TEXT NOT NULL DEFAULT '',
    globs             TEXT NOT NULL DEFAULT '[]',
    chars             INTEGER NOT NULL DEFAULT 0,
    cjk_chars         INTEGER NOT NULL DEFAULT 0,
    lines             INTEGER NOT NULL DEFAULT 0,
    differs_from_disk INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_id, line_no)
) WITHOUT ROWID;

-- projection: `system.compact_boundary` の compactMetadata。session_id / ts / cwd は
-- record 表から引くので持たない。
CREATE TABLE IF NOT EXISTS compact_boundary (
    file_id                   INTEGER NOT NULL,
    line_no                   INTEGER NOT NULL,
    trigger                   TEXT NOT NULL DEFAULT '',
    pre_tokens                INTEGER NOT NULL DEFAULT 0,
    post_tokens               INTEGER NOT NULL DEFAULT 0,
    cumulative_dropped_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms               INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_id, line_no)
) WITHOUT ROWID;

-- projection: user record の手入力 prompt 判定材料 (ADR 0031 Phase 2 / #497)。
--
-- **原文を切り詰めずに全文で持つ** (`text`)。手入力 prompt の判定・分類は
-- prompts mart の query 層が行うため、on-disk の形 (content が str か block list か、
-- isMeta / isSidechain / promptSource / origin.kind をどう読むか) だけをここで
-- 正規化する。文字列の意味解釈 (slash 展開判定 / #rule 捕捉 / 発話型分類) は
-- 全て `text` (flatten 済みの全文) だけを入力に取る純関数なので UDF に残し、
-- 別列は持たない — 形の解釈 (このテーブル) と意味の解釈 (UDF) を層で分ける。
--
-- `prompt_source` / `origin_kind` は「欠落」と「値が human 以外」を区別するため
-- NULL 許容にする。両方とも「フィールド自体が無い」場合だけ NULL、値が読めた
-- ときは (空文字を含め) その値をそのまま持つ。
CREATE TABLE IF NOT EXISTS user_prompt (
    file_id            INTEGER NOT NULL,
    line_no            INTEGER NOT NULL,
    text               TEXT NOT NULL DEFAULT '',
    has_tool_result     INTEGER NOT NULL DEFAULT 0,
    is_compact_summary  INTEGER NOT NULL DEFAULT 0,
    is_meta             INTEGER NOT NULL DEFAULT 0,
    is_sidechain        INTEGER NOT NULL DEFAULT 0,
    prompt_source       TEXT,
    origin_is_dict       INTEGER NOT NULL DEFAULT 0,
    origin_kind          TEXT,
    git_branch          TEXT NOT NULL DEFAULT '',
    cli_version         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (file_id, line_no)
) WITHOUT ROWID;
