-- permissions mart の観測契約 (ADR 0031 の query 層)。
--
-- **transcript の生 key 名を書かない。** 参照してよいのは store の列と、
-- `udf.py` が登録した純関数だけ (`scripts/gate/verify-query-format-isolation.py`
-- が機械検査する)。形式の解釈が要るなら ingest 側の責務。
--
-- 文は `-- name: <識別子>` で区切り、`marts.load_statements` が名前で引く。

-- 観測窓 × section scope で絞った実行の作業表。**section ごとに作り直す**
-- (project = cwd 配下 / global = 全 repo)。seq は挿入順 = lake の走査順で、
-- 同時刻の tie-break と「代表 event は先頭の 1 件」の定義に使う。
-- name: create_scoped_event
CREATE TEMP TABLE IF NOT EXISTS scoped_event (
    seq                  INTEGER PRIMARY KEY,
    tool                 TEXT NOT NULL,
    command              TEXT NOT NULL,
    command_head         TEXT NOT NULL,
    target_path          TEXT NOT NULL,
    input_excerpt        TEXT NOT NULL,
    session_id           TEXT NOT NULL,
    ts                   TEXT NOT NULL,
    ts_epoch             REAL,
    cwd                  TEXT NOT NULL,
    outcome              TEXT NOT NULL,
    denial_kind          TEXT,
    denial_reason_label  TEXT
)

-- 設定側の分母。store には入れず (窓を持たない「現在の状態」なので) 実行のたびに
-- settings.json から積み直す。entry_no は列挙順 = 出力の tie-break 順。
-- A 軸は設定 entry ごとに実行を舐める。tool 名一致は matcher の第 1 条件なので、
-- index で母集団を先に絞る (無いと entry 数 × 実行数の行走査になる)。
-- name: create_scoped_event_index
CREATE INDEX IF NOT EXISTS scoped_event_tool_idx ON scoped_event (tool)

-- name: create_permission_entry
CREATE TEMP TABLE IF NOT EXISTS permission_entry (
    entry_no    INTEGER PRIMARY KEY,
    raw         TEXT NOT NULL,
    category    TEXT NOT NULL,
    source_path TEXT NOT NULL,
    scope       TEXT NOT NULL,
    tool        TEXT NOT NULL,
    pattern     TEXT NOT NULL,
    confidence  TEXT NOT NULL,
    match_kind  TEXT NOT NULL
)

-- name: clear_scoped_event
DELETE FROM scoped_event

-- name: clear_permission_entry
DELETE FROM permission_entry

-- 実行 1 件を mart の 7 分類へ細分する。base 語彙 (store) が選んだ枝の中で
-- **label を付けるだけ**で、成否そのものの分岐を再導出しない (#476)。
--
-- 窓は `ts_epoch` で切る。`NULL` (ts 欠損) は保守的に窓内へ倒す — 件数は meta に
-- 出すので、黙って増えることはない。
-- name: refined_event
WITH refined AS (
    SELECT
        f.project_dir   AS project_dir,
        f.path          AS file_path,
        tu.line_no      AS line_no,
        tu.block_no     AS block_no,
        tu.tool         AS tool,
        tu.command      AS command,
        tu.target_path  AS target_path,
        tu.input_excerpt AS input_excerpt,
        tu.result_text  AS result_text,
        tu.denial_kind  AS raw_denial_kind,
        r.session_id    AS session_id,
        r.ts            AS ts,
        r.ts_epoch      AS ts_epoch,
        r.cwd           AS cwd,
        CASE
            WHEN tu.outcome_base IN ('success', 'unknown') THEN tu.outcome_base
            WHEN tu.outcome_base = 'user-reject' THEN 'deny_user-rejected'
            WHEN tu.denial_kind = 'permission-rule' THEN 'deny_permission-rule'
            WHEN tu.denial_kind IN ('automode-blocked', 'automode-unavailable')
                THEN 'deny_automode'
            WHEN looks_like_permission_denial(tu.result_text)
                THEN 'deny_permission-rule'
            WHEN looks_like_automode_denial(tu.result_text) THEN 'deny_automode'
            ELSE 'error'
        END AS outcome
    FROM tool_use tu
    JOIN record r ON r.file_id = tu.file_id AND r.line_no = tu.line_no
    JOIN file f ON f.file_id = tu.file_id
    WHERE r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch
)
SELECT
    tool, command,
    -- command_head は **Bash 専用の集約キー**。他 tool にも `command` を持つものが
    -- あり (Monitor 等)、区別しないと B 軸の 1 unit が引数ごとに割れる
    CASE WHEN tool = 'Bash' THEN command_head(command) ELSE '' END AS command_head,
    target_path,
    input_excerpt, session_id, ts, ts_epoch, cwd, outcome,
    CASE outcome
        WHEN 'deny_user-rejected' THEN 'user-rejected'
        WHEN 'deny_permission-rule' THEN 'permission-rule'
        WHEN 'deny_automode' THEN
            CASE WHEN raw_denial_kind IN ('automode-blocked', 'automode-unavailable')
                 THEN raw_denial_kind ELSE 'automode-blocked' END
    END AS denial_kind,
    CASE WHEN outcome = 'deny_automode'
         THEN automode_reason_label(result_text) END AS denial_reason_label
FROM refined
WHERE :scope_roots = '' OR cwd_in_scope(cwd, :scope_roots)
-- 走査順 = (project dir 名, file 名, 行, block)。seq がこの順を写すので、同数 tie の
-- 並び (代表 sample / B 軸 key) が lake の並びで決まる
ORDER BY project_dir, file_path, line_no, block_no

-- name: event_summary
SELECT count(*) AS event_count, count(DISTINCT session_id) AS distinct_sessions
FROM scoped_event

-- name: outcome_totals
SELECT outcome, count(*) AS n
FROM scoped_event
GROUP BY outcome
ORDER BY min(seq)

-- 設定 entry × 実行の照合 (A 軸)。tool 名一致は join 条件が担い、pattern の
-- 解釈だけを UDF に委ねる。match_count 0 の entry も残すため LEFT JOIN。
--
-- outcome 内訳と代表 (tool, command_head) を **1 本の GROUP BY で同時に**出す。
-- 分けると matcher の呼び出しが 2 倍になり、実測で A 軸だけが全体の 4 割を占める
-- (137 entry × 17,707 Bash 実行 = 240 万回 × 2)。集約の畳み直しは present 側で行う。
-- **複合行の計数も同じ GROUP BY に載せる** (ADR 0032 の誤計上検査。別 query に
-- 分けると matcher の再走査が要る)。`is_compound_command` は join 済みの行に
-- しか当たらないので matcher の呼び出し数は変わらない。
-- name: axis_a_matches
SELECT e.entry_no       AS entry_no,
       ev.tool          AS tool,
       ev.command_head  AS command_head,
       ev.outcome       AS outcome,
       count(ev.seq)    AS n,
       sum(CASE WHEN is_compound_command(ev.command) THEN 1 ELSE 0 END)
                        AS compound_n,
       min(ev.seq)      AS first_seq
FROM permission_entry e
LEFT JOIN scoped_event ev
       ON ev.tool = e.tool
      AND entry_matches(e.match_kind, e.pattern, ev.tool, ev.command,
                        ev.target_path)
GROUP BY e.entry_no, ev.tool, ev.command_head, ev.outcome
ORDER BY e.entry_no, first_seq

-- 実績 (B 軸)。tool × command_head × outcome。
-- name: axis_b
SELECT tool, command_head, outcome, count(*) AS n, min(seq) AS first_seq
FROM scoped_event
GROUP BY tool, command_head, outcome

-- B 軸の各 key を代表 event (先頭 1 件) で設定 entry に突き合わせる。
-- **どの層の entry が載っているかは呼び出し側が決める** (permission_entry に何を
-- load したかで決まる)。section の config と global の config を別々に突き合わせる
-- ため、category / scope も返す。
-- name: axis_b_config_matches
WITH representative AS (
    SELECT tool, command_head, min(seq) AS seq
    FROM scoped_event
    GROUP BY tool, command_head
)
SELECT rep.tool AS tool, rep.command_head AS command_head, e.raw AS entry_raw,
       e.category AS category, e.scope AS scope
FROM representative k
JOIN scoped_event rep ON rep.seq = k.seq
JOIN permission_entry e
     ON e.tool = rep.tool
    AND entry_matches(e.match_kind, e.pattern, rep.tool, rep.command,
                      rep.target_path)
ORDER BY rep.tool, rep.command_head, e.entry_no

-- deny 直後の同 tool 呼び出し (bypass 系列)。session 内の位置で lookahead を測り、
-- 経過秒で打ち切る。**「意図の同一性」は判定しない** — 系列をそのまま人間に出す。
-- name: bypass_pairs
WITH ordered AS (
    SELECT *, row_number() OVER (PARTITION BY session_id ORDER BY ts, seq) AS pos
    FROM scoped_event
)
SELECT
    d.seq                 AS denied_seq,
    d.session_id          AS session_id,
    d.ts                  AS denied_at,
    d.tool                AS denied_tool,
    d.command_head        AS denied_command_head,
    d.input_excerpt       AS denied_input_excerpt,
    d.outcome             AS denied_outcome,
    d.denial_kind         AS denial_kind,
    d.denial_reason_label AS denial_reason_label,
    d.cwd                 AS cwd,
    follow.tool           AS tool,
    follow.command_head   AS command_head,
    follow.input_excerpt  AS input_excerpt,
    follow.outcome        AS outcome,
    follow.ts             AS ts,
    CASE WHEN d.ts_epoch IS NOT NULL AND follow.ts_epoch IS NOT NULL
         THEN CAST(follow.ts_epoch - d.ts_epoch AS INTEGER) END AS gap_seconds
FROM ordered d
JOIN ordered follow
     ON follow.session_id = d.session_id
    AND follow.pos > d.pos
    AND follow.pos <= d.pos + :lookahead
    AND follow.tool = d.tool
WHERE substr(d.outcome, 1, 5) = 'deny_'
  AND (d.ts_epoch IS NULL OR follow.ts_epoch IS NULL
       OR (follow.ts_epoch - d.ts_epoch >= 0
           AND follow.ts_epoch - d.ts_epoch <= :max_gap))
ORDER BY d.ts DESC, d.seq, follow.pos

-- guard 系 deny (自動モード分類器) の Reason label 別内訳。
-- name: guard_reverse
SELECT denial_kind, denial_reason_label, count(*) AS deny_count
FROM scoped_event
WHERE denial_kind IN ('automode-blocked', 'automode-unavailable')
GROUP BY denial_kind, denial_reason_label

-- name: guard_samples
SELECT denial_kind, denial_reason_label, session_id, ts, tool, input_excerpt, cwd
FROM scoped_event
WHERE denial_kind IN ('automode-blocked', 'automode-unavailable')
ORDER BY denial_kind, denial_reason_label, ts DESC, seq

-- hook の fire 実績。**section (cwd scope) で絞らない** — 「30 日どこでも fire
-- していない」が「fire していない hook」の主張になるため、分母を窓全体に取る。
--
-- 照合キー (command の basename) はここでは求めない。実測 4 万 fire に対し実際の
-- command は 23 種しかなく、**行ごとに求めると全体の過半を占める** (per-row UDF も
-- distinct 表との TEXT join も同じ 5.9 秒)。present 側で command ごとに 1 回だけ
-- 求める。
-- name: hook_firings
SELECT hf.hook_name AS hook_name, hf.hook_event AS hook_event,
       hf.command AS command,
       hf.exit_code AS exit_code, hf.duration_ms AS duration_ms,
       hf.timed_out AS timed_out,
       r.session_id AS session_id, r.ts AS ts
FROM hook_firing hf
JOIN record r ON r.file_id = hf.file_id AND r.line_no = hf.line_no
JOIN file f ON f.file_id = hf.file_id
WHERE r.ts_epoch IS NULL OR r.ts_epoch >= :cutoff_epoch
ORDER BY f.project_dir, f.path, hf.line_no

-- 観測の劣化シグナル (`store_anomalies`) は `store.anomalies()` へ引き上げた
-- (#497: prompts mart が 2 つ目の消費者になったため)。SQL をコピーしない。
