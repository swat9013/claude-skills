-- permissions mart の観測契約 (ADR 0031 の query 層)。
--
-- **transcript の生 key 名を書かない。** 参照してよいのは store の列と、
-- `udf.py` が登録した純関数だけ (`scripts/gate/verify-query-format-isolation.py`
-- が機械検査する)。形式の解釈が要るなら ingest 側の責務。
--
-- 文は `-- name: <識別子>` で区切り、`marts.load_statements` が名前で引く。

-- 観測窓 × section scope で絞った実行の作業表。**section ごとに作り直す**
-- (project = cwd 配下 / global = 全 repo)。seq は挿入順 = lake の走査順で、
-- 同時刻の tie-break (同数で並ぶ行の順序) の定義に使う。**照合の母集団を選ぶのには
-- 使わない** — 先頭 1 件を代表にした突合は #889 で撤去した。
-- name: create_scoped_event
CREATE TEMP TABLE IF NOT EXISTS scoped_event (
    seq                  INTEGER PRIMARY KEY,
    tool                 TEXT NOT NULL,
    command              TEXT NOT NULL,
    command_head         TEXT NOT NULL,
    target_path          TEXT NOT NULL,
    target_url           TEXT NOT NULL,
    input_excerpt        TEXT NOT NULL,
    input_keys           TEXT NOT NULL,
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
-- A 軸は設定 entry ごとに実行を舐める。tool 名の被覆は matcher の第 1 条件なので、
-- index で母集団を先に絞る (無いと entry 数 × 実行数の行走査になる)。被覆が
-- 完全一致より広くなっても等値 join のまま残せるよう、entry 側は `covered_tool` が
-- 実行の tool 名へ展開済みで降りてくる (#916)。
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

-- **entry の tool 名 × 実行の tool 名の被覆表** (#916)。両軸の join はここを通る。
--
-- entry の tool 名は実行の tool 名と等しいとは限らない — 公式 doc の MCP 節が
-- `mcp__foo` (server の全 tool) と `mcp__foo__*` (tool 名位置の wildcard) を定めており、
-- 完全一致で突合すると**収載済みの entry が「未収載」として出る**。被覆そのものは
-- `udf.entry_tool_covers` が決め、SQL には (entry_no, tool) の対応**だけ**が降りてくる
-- (「SQL は関係代数だけ、意味は UDF」の配置規則)。
--
-- 述語を join に置かず表に落とすのは cost のため: tool 名が等値でなくなると
-- `scoped_event_tool_idx` が効かず総当たりになる (実測の桁は present 側の
-- `covered_tool_rows` docstring が持つ — 数字を写すと次の計測で片方だけ古くなる)。
--
-- **match_kind / pattern を複製して持つ**のは B 軸の join 順序のため。`entry_matches` は
-- 「この表を導入する join」に同居していなければならず、後段の join へ回すと当たらない
-- entry の数だけ NULL 行が出て `uncovered_event_count` が候補 entry 数倍に膨らむ。
-- name: create_covered_tool
CREATE TEMP TABLE IF NOT EXISTS covered_tool (
    entry_no    INTEGER NOT NULL,
    tool        TEXT NOT NULL,
    match_kind  TEXT NOT NULL,
    pattern     TEXT NOT NULL,
    PRIMARY KEY (entry_no, tool)
)

-- B 軸の被覆計数 (`axis_b_coverage`) は実行の tool 名から候補 entry を引くので、
-- A 軸と同じ理由で index を張る。無いと event ごとに対応表の全走査になり、増えた
-- コストが index 不在由来なのか照合本来のコストなのか切り分けられない。
-- name: create_covered_tool_index
CREATE INDEX IF NOT EXISTS covered_tool_tool_idx ON covered_tool (tool)

-- name: clear_scoped_event
DELETE FROM scoped_event

-- name: clear_permission_entry
DELETE FROM permission_entry

-- name: clear_covered_tool
DELETE FROM covered_tool

-- 対応表を組む母集団。**窓 × section で絞った後の実行に現れた tool 名だけ**を返す
-- (被覆は観測された tool にしか意味を持たない)。
-- name: observed_tool
SELECT DISTINCT tool FROM scoped_event

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
        tu.target_url   AS target_url,
        tu.input_excerpt AS input_excerpt,
        tu.input_keys   AS input_keys,
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
    target_path, target_url,
    input_excerpt, input_keys, session_id, ts, ts_epoch, cwd, outcome,
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
-- 並び (A 軸の代表 sample / B 軸 unit の並び) が lake の並びで決まる
ORDER BY project_dir, file_path, line_no, block_no

-- name: event_summary
SELECT count(*) AS event_count, count(DISTINCT session_id) AS distinct_sessions
FROM scoped_event

-- name: outcome_totals
SELECT outcome, count(*) AS n
FROM scoped_event
GROUP BY outcome
ORDER BY min(seq)

-- 設定 entry × 実行の照合 (A 軸)。tool 名の被覆は `covered_tool` が担い、pattern の
-- 解釈だけを UDF に委ねる。match_count 0 の entry も残すため LEFT JOIN。
-- **B 軸 (`axis_b_coverage`) と同じ被覆表・同じ matcher を通す** — 片方だけ広げると
-- 「A 軸では現役、B 軸では未収載」という矛盾した像が戻る (#889 が解消したばかり)。
--
-- outcome 内訳と代表 (tool, command_head) を **1 本の GROUP BY で同時に**出す。
-- 分けると matcher の呼び出しが 2 倍になり、実測で A 軸だけが全体の 4 割を占める
-- (137 entry × 17,707 Bash 実行 = 240 万回 × 2)。集約の畳み直しは present 側で行う。
-- **複合行の計数も同じ GROUP BY に載せる** (ADR 0032 の誤計上検査。別 query に
-- 分けると matcher の再走査が要る)。`is_compound_command` は join 済みの行に
-- しか当たらないので matcher の呼び出し数は変わらない。
--
-- **窓の前後半 (`window_half`) も同じ GROUP BY に載せる** (#584 の変化点フラグ)。
-- `:split_epoch` との定数比較なので matcher の呼び出し数も走査回数も変わらない
-- (entry ごとの中央値で割るなら window 関数 = 照合済み行の partition sort が要り、
-- 全体の 4 割を占めるこの query を重くする。窓の固定二分点で足りる)。
-- ts 欠損 event は `NULL` に落とし、どちらの半分にも入れない。
-- name: axis_a_matches
SELECT e.entry_no       AS entry_no,
       ev.tool          AS tool,
       ev.command_head  AS command_head,
       ev.outcome       AS outcome,
       CASE WHEN ev.ts_epoch IS NULL THEN NULL
            WHEN ev.ts_epoch < :split_epoch THEN 'early'
            ELSE 'late' END AS window_half,
       count(ev.seq)    AS n,
       sum(CASE WHEN is_compound_command(ev.command) THEN 1 ELSE 0 END)
                        AS compound_n,
       min(ev.seq)      AS first_seq
FROM permission_entry e
LEFT JOIN covered_tool c ON c.entry_no = e.entry_no
LEFT JOIN scoped_event ev
       ON ev.tool = c.tool
      AND entry_matches(c.match_kind, c.pattern, ev.tool, ev.command,
                        ev.target_path, ev.target_url)
GROUP BY e.entry_no, ev.tool, ev.command_head, ev.outcome, window_half
ORDER BY e.entry_no, first_seq

-- tool 別に「その tool の event 数」と「matcher が読む列が非空だった event 数」を
-- 数える (#873)。**match_count が完全な実績なのか、照合できなかった実行を含む
-- 下限なのかを分ける**唯一の材料。どの列を読むかは entry の match_kind で決まるので、
-- ここでは列ごとの件数を並べるだけにし、entry への割り当ては present 側が行う
-- (tool 名の allowlist を持たないため — 陳腐化した一覧は無いより悪い)。
--
-- **`event_count` との差が要る**: 列を持つ実行と持たない実行が混在する tool
-- (`Grep` は `path` が任意) では、照合できなかった実行があるのに「0 件 = 未使用」と
-- 読める行が出る。0 件かどうかではなく**全件を照合できたか**で確度を決める。
--
-- matcher を呼ばない単純な集約なので、A 軸の照合コストは増えない。
-- **列の別名は `matcher_input_column` の戻り値そのもの**にする。`<列>_n` のような
-- 派生名にすると present 側が文字列結合で引くことになり、写像が別の実在する列名へ
-- ずれたとき KeyError にならず静かに別の集計値を読む。
-- name: matcher_input_availability
SELECT tool,
       count(*)                                                  AS event_count,
       sum(CASE WHEN command <> '' THEN 1 ELSE 0 END)            AS command,
       sum(CASE WHEN target_path <> '' THEN 1 ELSE 0 END)        AS target_path,
       -- **生の target_url ではなく取り出せた hostname を数える** — 解釈できない
       -- URL しか無い entry は照合が成立しないので、非空の生値で数えると
       -- unmatchable を取り逃がして exact の 0 件 = revoke 候補になる
       sum(CASE WHEN url_host(target_url) <> '' THEN 1 ELSE 0 END) AS target_url
FROM scoped_event
GROUP BY tool

-- param rule (`Tool(<param>:<value>)`) の照合対象は列ではなく **その param を
-- 渡した呼び出しの有無**。key 名の組ごとに畳んで返し、名前への分解は present 側が
-- 行う (SQL に生 key 名を書かない規律のため、key は値としてだけ通す)。返る行数は
-- **窓内に実在した組合せの種類数**で、tool の入力 schema が許す組合せ全体 (key 名 n
-- 個なら最大 2^n) ではない — 実測では 1 tool あたり数種類に収まる。
-- name: observed_input_keys
SELECT DISTINCT tool, input_keys
FROM scoped_event

-- 実績 (B 軸)。tool × command_head × outcome。
-- name: axis_b
SELECT tool, command_head, outcome, count(*) AS n, min(seq) AS first_seq
FROM scoped_event
GROUP BY tool, command_head, outcome

-- B 軸の各 unit を **unit 内の全 event** で設定 entry に突き合わせる (#889)。
-- **どの層の entry が載っているかは呼び出し側が決める** (permission_entry に何を
-- load したかで決まる)。section の config と global の config を別々に突き合わせる
-- ため、category / scope も返す。
--
-- **代表 event 1 件との照合をやめた理由**: unit key は (tool, command_head) で
-- command_head は Bash 以外では空文字なので、非 Bash tool は全実行が 1 unit に潰れる。
-- 代表 1 件で決めると `Read(**/*.env)` のような引数依存の entry では収載の有無が
-- 代表の当たり外れで反転し、全 event を照合する A 軸と矛盾した像が出る
-- (実測: A 軸 `Edit(**/.ai/**)` が 54 件マッチしている窓で、B 軸の `Edit` unit は
-- `config_matches: []` = 未収載だった)。
--
-- **LEFT JOIN の NULL 群が未被覆件数**。どの entry にも当たらなかった event は
-- entry_no NULL の行を 1 本ずつ作るので、その group の件数が「この unit のうち
-- どの entry にも収載されていない実行の数」になる。**entry ごとの件数を足しても
-- 求まらない** (1 event が複数 entry にマッチしうるので和は過大になる) ので、
-- 未被覆には別の集計が要る — それをこの 1 文に同居させて走査を 1 度に抑える。
--
-- **section 層の被覆件数だけなら `axis_a_matches` から畳み直せる** (present 側の
-- `combos` が (entry_no, tool, command_head) 別の件数を既に持つ)。それでも本文を
-- 置くのは、**global 層には借りる先が無い**ため — `build_axis_a` は section の entry に
-- しか走らないので、#513 の層をまたぐ突合には結局この走査が要る。片方だけ combos 由来に
-- すると同じ出力を 2 通りの経路で組むことになるので、両層とも本文で揃える。
-- **走査は増える。** warm な `build(section=all)` は #889 当時 13.0〜13.4s、#916 後
-- 15.6〜16.4s (レンジが重ならない +2.5s 前後)。被覆表を通す分の対価として受け入れた —
-- 完全一致に戻せばこの 2.5s は消えるが、収載済みの MCP entry を「未収載」として
-- 出し続けることになる。等値 join を維持しているので増分は線形で、`EXPLAIN QUERY PLAN`
-- でも両軸の全 join が index を使う (述語を join に置く案なら二次に落ちていた)。
--
-- **`entry_matches` は `covered_tool` を導入する join に置く** (#916)。後段の
-- `permission_entry` 側へ回すと、当たらなかった候補 entry の数だけ NULL 行が出て
-- 未被覆件数が候補数倍に膨らむ (`e.entry_no` は entry_no の等値なので多重度を生まない)。
-- name: axis_b_coverage
SELECT ev.tool          AS tool,
       ev.command_head  AS command_head,
       e.entry_no       AS entry_no,
       e.raw            AS entry_raw,
       e.category       AS category,
       e.scope          AS scope,
       count(*)         AS n
FROM scoped_event ev
LEFT JOIN covered_tool c
       ON c.tool = ev.tool
      AND entry_matches(c.match_kind, c.pattern, ev.tool, ev.command,
                        ev.target_path, ev.target_url)
LEFT JOIN permission_entry e ON e.entry_no = c.entry_no
GROUP BY ev.tool, ev.command_head, e.entry_no
ORDER BY ev.tool, ev.command_head, e.entry_no

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
