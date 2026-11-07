-- lake の on-disk 形式を知る唯一の層。ここと schema-snapshot.json の外に生の key 名を書かない。
--
-- 読み込む前に script が次を用意する。
--   table lake_entry (path, rel, readable, size_bytes): lake の file の一覧 (rel は lake からの相対 path)
--   variable population: 読む transcript_kind の一覧
--   variable named_session: 問いが transcript を名指すとき、lake からの相対 path から .jsonl を外した値。無ければ NULL
--   variable lake_root_length: lake の絶対 path の長さ (path から rel を切り出す)
--   variable record_structure: json_transform に渡す列の宣言 (schema snapshot から作る)
--
-- repo の解決は git を呼ぶので view に書けない。問いが lake_cwd_repo (cwd, repo, unresolved_cause) を
-- 読むときに script が表を作る。この表で消えた cwd の repo は NULL で、消えた cwd を祖先で解決する問いは
-- この表の代わりに同じ解決を祖先へ当てる。

-- token の概算の係数の正本。CJK は 1 字 1 token、その他は 4 字 1 token。tokenizer ではないので桁の比較にだけ使う。
CREATE MACRO lake_cjk_chars(body) AS
    length(coalesce(body, ''))
    - length(regexp_replace(coalesce(body, ''), '[\x{3000}-\x{9fff}\x{f900}-\x{faff}\x{ff00}-\x{ffef}]', '', 'g'));
CREATE MACRO lake_estimate_tokens(cjk_chars, other_chars) AS
    ceil(cjk_chars / 1.0 + other_chars / 4.0)::BIGINT;

-- JSON の値が整数のときだけ値を返す。欠けた値を 0 に丸めない (未観測と 0 を混ぜない)。
CREATE MACRO lake_json_int(doc, json_path) AS
    CASE WHEN json_type(doc, json_path) IN ('BIGINT', 'UBIGINT')
         THEN json_extract_string(doc, json_path)::BIGINT END;
CREATE MACRO lake_json_is_true(doc, json_path) AS
    coalesce(json_type(doc, json_path) = 'BOOLEAN' AND json_extract_string(doc, json_path) = 'true', false);
CREATE MACRO lake_json_strings(doc) AS
    coalesce(list_filter(json_transform(doc, '["VARCHAR"]'), lambda item: item IS NOT NULL AND item <> ''), []);

-- 行の時刻 (unix 秒)。欠損・解釈不能は NULL。窓の条件で NULL をどちらへ倒すかは問いの側が決める。
CREATE MACRO lake_ts_epoch(ts) AS epoch(try_cast(ts AS TIMESTAMP));
CREATE MACRO lake_line_epoch(line) AS
    CASE WHEN json_valid(line) THEN lake_ts_epoch(json_extract_string(line, '$.timestamp')) END;

-- JSON の関数は壊れた行で例外を投げ、AND は短絡しないので、CASE で守る。
CREATE MACRO lake_is_record(line) AS
    CASE WHEN json_valid(line) THEN json_type(line) = 'OBJECT' ELSE false END;

-- 行から key を 1 つ引く。多くの行を読む view は lake_record を経ずにこれで引く。lake_record は record 全体を解釈し、
-- 巨大な行の chunk で memory の上限を超える。fixture は小さいので、この上限はテストでは見えない。
CREATE MACRO lake_line_text(line, json_path) AS
    CASE WHEN lake_is_record(line) THEN json_extract_string(line, json_path) END;
CREATE MACRO lake_line_session(line) AS
    coalesce(nullif(lake_line_text(line, '$.sessionId'), ''), nullif(lake_line_text(line, '$.session_id'), ''), '');

-- 置き場の判定 (設計規則 (8)): 何が subagent の transcript かはここの 1 本だけが決める。
-- <project>/<session>/ の下は subagents/ の直下と subagents/workflows/<run>/ の直下の agent-<id>.jsonl だけが
-- subagent の transcript で、workflow の進行ログは transcript でない。それ以外は読まずに件数に出す。
CREATE MACRO lake_file_kind_of(rel) AS
    CASE
        WHEN regexp_full_match(rel, '[^/]+/[^/]+\.jsonl') THEN 'main'
        WHEN regexp_full_match(rel, '[^/]+/[^/]+/subagents/agent-[^/]+\.jsonl') THEN 'subagent'
        WHEN regexp_full_match(rel, '[^/]+/[^/]+/subagents/workflows/[^/]+/agent-[^/]+\.jsonl') THEN 'subagent'
        WHEN regexp_full_match(rel, '[^/]+/[^/]+/subagents/workflows/[^/]+/journal\.jsonl') THEN 'workflow_journal'
        ELSE 'unrecognized'
    END;

-- transcript file が属する project の dir 名。
CREATE MACRO lake_project_dir(path) AS
    regexp_extract(substr(path, getvariable('lake_root_length') + 2), '^([^/]+)/', 1);

-- 問いが transcript を名指すと、読む範囲はその file と session dir の下だけになる。
CREATE MACRO lake_in_named_session(rel) AS
    getvariable('named_session') IS NULL
    OR rel = getvariable('named_session') || '.jsonl'
    OR starts_with(rel || '/', getvariable('named_session') || '/');

-- 読めない dir が母集団に関わるか。main の transcript は project dir の直下にだけ置かれる。
CREATE MACRO lake_dir_in_population(rel) AS
    lake_in_named_session(rel) AND (list_contains(getvariable('population'), 'subagent') OR NOT contains(rel, '/'));

CREATE MACRO lake_agent_id_of(rel) AS regexp_extract(rel, 'agent-([^/]+)\.jsonl$', 1);
CREATE MACRO lake_workflow_run_of(rel) AS regexp_extract(rel, '/workflows/([^/]+)/', 1);

CREATE VIEW lake_file AS
SELECT path, rel, readable, size_bytes, file_kind,
       CASE WHEN file_kind IN ('main', 'subagent') THEN file_kind END AS transcript_kind,
       CASE WHEN file_kind = 'subagent' THEN lake_agent_id_of(rel) END AS agent_id,
       CASE WHEN file_kind IN ('subagent', 'workflow_journal') THEN lake_workflow_run_of(rel) END AS workflow_run_id,
       regexp_extract(rel, '^([^/]+/[^/]+)/', 1) AS session_dir
FROM (SELECT *, lake_file_kind_of(rel) AS file_kind FROM lake_entry
      WHERE regexp_full_match(rel, '[^/]+/.+\.jsonl') AND lake_in_named_session(rel));

SET VARIABLE population_paths = (
    SELECT coalesce(list(path ORDER BY path), [])
    FROM lake_file
    WHERE readable AND list_contains(getvariable('population'), transcript_kind)
);
SET VARIABLE subagent_meta_paths = (
    SELECT coalesce(list(path ORDER BY path), [])
    FROM lake_entry
    WHERE readable AND suffix(rel, '.meta.json') AND lake_in_named_session(rel)
      AND lake_file_kind_of(regexp_replace(rel, '\.meta\.json$', '.jsonl')) = 'subagent'
);

-- read_csv は file の一覧が空だと bind で落ちるので、理由の分かる失敗で先に止める。
SELECT CASE WHEN len(getvariable('population_paths')) = 0 THEN error('lake_empty') END;

-- 行は text として順に読む。parallel=false の row_number() OVER () が file の中で行の順に連番を振る
-- (PARTITION BY は順序を保たないので使わない)。line_seq は file の中で増えるが 1 から始まらない。
-- 1 から数える行番号が要る問いは lake_line_position を引く。
-- ignore_errors は UTF-8 として読めない行と delim の文字を含む行を黙って捨てるので、
-- lake_file_read がバイト数の差で undecodable として数える。
CREATE MACRO lake_read_lines(paths) AS TABLE
SELECT filename AS path, line
FROM read_csv(paths,
              columns = {'line': 'VARCHAR'}, delim = chr(1), quote = '', escape = '',
              header = false, auto_detect = false, filename = true, parallel = false,
              max_line_size = 200000000, ignore_errors = true);

CREATE VIEW lake_line AS
SELECT path, row_number() OVER () AS line_seq, line
FROM lake_read_lines(getvariable('population_paths'));

CREATE VIEW lake_line_position AS
SELECT path, line_seq, line_seq - min(line_seq) OVER (PARTITION BY path) + 1 AS line_no
FROM (SELECT path, line_seq FROM lake_line);

-- 壊れた行は DuckDB が全列 NULL の行として残すので、text のまま判定する。空行は壊れた行に数えない。
CREATE VIEW lake_parsed AS
SELECT path, line_seq, line AS raw_line,
       line IS NULL OR trim(line) = '' AS blank,
       lake_is_record(line) AS is_record,
       CASE WHEN lake_is_record(line) THEN json_transform(line, getvariable('record_structure')) END AS doc,
       strlen(coalesce(line, '')) + 1 AS line_bytes
FROM lake_line;

-- record の正規化。session id は 2 つの表記があるので、どちらを見るかを問いの側に選ばせない。
-- 種類は lake_file と join せずに path から引く。join すると optimizer が行の側を hash に積み、memory の上限を超える。
CREATE VIEW lake_record AS
SELECT path, lake_file_kind_of(substr(path, getvariable('lake_root_length') + 2)) AS transcript_kind, line_seq, raw_line,
       coalesce(doc.uuid, '') AS record_uuid,
       coalesce(doc."type", '') AS record_type,
       coalesce(doc.subtype, '') AS subtype,
       lake_line_session(raw_line) AS session_id,
       coalesce(doc."timestamp", '') AS ts,
       lake_ts_epoch(doc."timestamp") AS ts_epoch,
       coalesce(doc.cwd, '') AS cwd,
       coalesce(doc.gitBranch, '') AS git_branch,
       coalesce(doc."version", '') AS cli_version,
       coalesce(doc.isMeta, false) AS is_meta,
       coalesce(doc.isSidechain, false) AS is_sidechain,
       coalesce(doc.isCompactSummary, false) AS is_compact_summary,
       doc.promptSource AS prompt_source,
       json_type(doc.origin) = 'OBJECT' AS origin_is_object,
       json_extract_string(doc.origin, '$.kind') AS origin_kind,
       coalesce(doc.attributionSkill, '') AS attribution_skill,
       doc.toolDenialKind AS denial_kind,
       doc.toolUseResult AS structured_result,
       doc.message AS message_doc,
       doc.message -> '$.content' AS message_content,
       doc.attachment AS attachment_doc,
       doc.compactMetadata AS compact_doc
FROM lake_parsed
WHERE is_record;

-- session の cwd の判定材料。全行を読むので lake_record を経ない。contains は key の解釈の前に行を絞る前置き。
CREATE VIEW lake_session_cwd AS
SELECT line_seq, session_id, cwd, ts_epoch
FROM (
    SELECT line_seq, lake_line_session(line) AS session_id, coalesce(lake_line_text(line, '$.cwd'), '') AS cwd,
           lake_line_epoch(line) AS ts_epoch
    FROM lake_line
    WHERE contains(line, '"cwd"')
)
WHERE session_id <> '' AND cwd <> '';

-- 観測の劣化の件数 (設計規則 (10))。読んだ file・record・壊れた行は population の母集団で数える。
-- 置き場の判定に当たらない file は main にも subagent にも属さないので母集団を問わず数え、
-- workflow の進行ログは劣化ではないので別の件数に出す。
-- session の出来事の record だけが時刻を持つ。設定の記録などの種類は時刻を持たないのが正常なので、欠損に数えない。
CREATE VIEW lake_file_read AS
SELECT path,
       count(*) FILTER (WHERE is_record) AS records,
       count(*) FILTER (WHERE NOT is_record AND NOT blank) AS broken_lines,
       count(*) FILTER (WHERE doc."type" IN ('user', 'assistant', 'attachment', 'system')
                          AND lake_ts_epoch(doc."timestamp") IS NULL) AS ts_missing_records,
       sum(line_bytes) AS bytes_read
FROM lake_parsed
GROUP BY path;

-- 読んだ行が 1 つも残らなかった file も数えるので、file の一覧を起点にする。
CREATE VIEW lake_degradation AS
WITH read_files AS (
    SELECT f.path, f.size_bytes, coalesce(r.records, 0) AS records, coalesce(r.broken_lines, 0) AS broken_lines,
           coalesce(r.ts_missing_records, 0) AS ts_missing_records, coalesce(r.bytes_read, 0) AS bytes_read
    FROM lake_file f
    LEFT JOIN lake_file_read r ON r.path = f.path
    WHERE f.readable AND list_contains(getvariable('population'), f.transcript_kind)
)
SELECT
    (SELECT count(*) FROM read_files) AS files_read,
    (SELECT coalesce(sum(records), 0) FROM read_files) AS records,
    (SELECT coalesce(sum(broken_lines), 0) FROM read_files) AS broken_lines,
    (SELECT coalesce(sum(ts_missing_records), 0) FROM read_files) AS ts_missing_records,
    (SELECT count(*) FROM read_files WHERE size_bytes - bytes_read > 1) AS undecodable_files,
    (SELECT count(*) FROM lake_file
     WHERE NOT readable AND list_contains(getvariable('population'), transcript_kind)) AS unreadable_files,
    (SELECT count(*) FROM lake_file WHERE file_kind = 'unrecognized') AS unrecognized_nested_files,
    (SELECT count(*) FROM lake_file WHERE file_kind = 'workflow_journal') AS workflow_journal_files;

-- content block を 1 行に開く。JSON の解釈は chunk ごとに領域を積むので、生の text で tool の行に絞ってから解釈する
-- (全行を解釈すると所要が数倍になり、巨大な行が続く chunk で memory の上限を超える)。
-- tool_result の block が 2 つ以上ある record の構造化された結果は、
-- どの block の真値か決められないので block に帰属させない。
CREATE VIEW lake_block AS
SELECT * EXCLUDE (blocks),
       unnest(blocks) AS block,
       generate_subscripts(blocks, 1) - 1 AS block_no,
       len(list_filter(blocks, lambda item: json_extract_string(item, '$.type') = 'tool_result')) AS result_blocks
FROM (
    SELECT path, transcript_kind, line_seq, raw_line, record_type, record_uuid, is_sidechain, session_id, ts, ts_epoch, cwd,
           attribution_skill, denial_kind, structured_result,
           json_transform(message_content, '["JSON"]') AS blocks
    FROM lake_record
    WHERE (contains(raw_line, '"tool_use"') OR contains(raw_line, '"tool_result"'))
      AND record_type IN ('assistant', 'user') AND json_type(message_content) = 'ARRAY'
);

CREATE MACRO lake_first_string(doc, path_a, path_b, path_c) AS
    coalesce(CASE WHEN json_type(doc, path_a) = 'VARCHAR' THEN json_extract_string(doc, path_a) END,
             CASE WHEN json_type(doc, path_b) = 'VARCHAR' THEN json_extract_string(doc, path_b) END,
             CASE WHEN json_type(doc, path_c) = 'VARCHAR' THEN json_extract_string(doc, path_c) END,
             '');

-- Skill の呼出しが必ず持つ識別の path。問いが識別を読めなかったときに、この path を食い違いとして返す。
CREATE MACRO lake_skill_identity_path() AS 'message.content[].input.skill';

CREATE VIEW lake_tool_use AS
SELECT path, transcript_kind, line_seq, block_no, raw_line, record_uuid, is_sidechain, session_id, ts, ts_epoch, cwd,
       attribution_skill,
       coalesce(json_extract_string(block, '$.id'), '') AS tool_use_id,
       coalesce(json_extract_string(block, '$.name'), '') AS tool,
       lake_first_string(tool_input, '$.command', '$.command', '$.command') AS command,
       lake_first_string(tool_input, '$.file_path', '$.path', '$.notebook_path') AS target_path,
       lake_first_string(tool_input, '$.url', '$.url', '$.url') AS target_url,
       left(trim(coalesce(tool_input::VARCHAR, '')), 200) AS input_excerpt,
       -- key の名前だけを持つ。値を持たないので prompt の本文を含まない
       coalesce(list_sort(json_keys(tool_input)), []) AS input_keys,
       -- 上の列へ持ち上げた照合の対象の key を除いたもの。permission の param rule はこの key しか名指せない
       list_filter(coalesce(list_sort(json_keys(tool_input)), []),
                   lambda key: key NOT IN ('command', 'file_path', 'path', 'notebook_path', 'url')) AS param_keys,
       CASE json_extract_string(block, '$.name')
           WHEN 'Skill' THEN trim(lake_first_string(tool_input, '$.skill', '$.skill', '$.skill'))
           WHEN 'Agent' THEN trim(lake_first_string(tool_input, '$.subagent_type', '$.subagent_type', '$.subagent_type'))
           ELSE ''
       END AS unit_id,
       coalesce(json_extract_string(tool_input, '$.args'), '') AS skill_args,
       CASE WHEN json_extract_string(block, '$.name') IN ('Task', 'Agent') THEN 'subagent'
            WHEN json_extract_string(block, '$.name') = 'Workflow' THEN 'workflow'
       END AS launch_kind
FROM (SELECT *, CASE WHEN json_type(block, '$.input') = 'OBJECT' THEN block -> '$.input' END AS tool_input FROM lake_block)
WHERE record_type = 'assistant' AND json_extract_string(block, '$.type') = 'tool_use';

-- tool_result の content (文字列か block の list) を 1 本の text に潰す。
CREATE MACRO lake_flatten_result_text(content) AS
    CASE json_type(content)
        WHEN 'VARCHAR' THEN json_extract_string(content, '$')
        WHEN 'ARRAY' THEN array_to_string(
            list_filter(
                list_transform(json_transform(content, '["JSON"]'),
                               lambda item: CASE WHEN json_type(item, '$.text') = 'VARCHAR'
                                                 THEN json_extract_string(item, '$.text') END),
                lambda item: item IS NOT NULL),
            ' ')
        ELSE ''
    END;

-- tool 実行の outcome の判定の 1 本。語彙は success / error / user-reject / unknown。
-- is_error が真なら構造化された結果より先に見る (そのとき構造化された結果は生の error 文字列で判定を持たない)。
-- 判定材料が無いときは success に丸めず unknown にする。
CREATE MACRO lake_base_outcome(is_error, result_text, structured_result, denial_kind) AS
    CASE
        WHEN is_error IS TRUE THEN
            CASE
                WHEN denial_kind = 'user-rejected' THEN 'user-reject'
                WHEN denial_kind IN ('permission-rule', 'automode-blocked', 'automode-unavailable', 'hook') THEN 'error'
                WHEN regexp_matches(result_text,
                                    '(?s)Permission to use\s+[A-Za-z_][A-Za-z0-9_-]*(\s+with\s+command\s+.+?)?\s+has been denied\.')
                    THEN 'error'
                WHEN contains(lower(result_text), 'the user doesn''t want')
                     OR contains(lower(result_text), 'user rejected the')
                     OR contains(lower(result_text), 'request interrupted by user')
                     OR contains(lower(result_text), 'tool call was rejected')
                    THEN 'user-reject'
                ELSE 'error'
            END
        WHEN structured_result IS NOT NULL AND json_type(structured_result) <> 'NULL' THEN
            CASE
                WHEN lake_json_is_true(structured_result, '$.interrupted') THEN 'user-reject'
                WHEN lake_json_is_true(structured_result, '$.is_error') THEN 'error'
                WHEN json_type(structured_result, '$.success') = 'BOOLEAN'
                     AND json_extract_string(structured_result, '$.success') = 'false' THEN 'error'
                ELSE 'success'
            END
        WHEN is_error IS FALSE THEN 'success'
        ELSE 'unknown'
    END;

CREATE VIEW lake_tool_result AS
SELECT path, line_seq, block_no,
       coalesce(json_extract_string(block, '$.tool_use_id'), '') AS tool_use_id,
       lake_base_outcome(
           CASE WHEN json_type(block, '$.is_error') = 'BOOLEAN' THEN json_extract_string(block, '$.is_error')::BOOLEAN END,
           lake_flatten_result_text(block -> '$.content'),
           CASE WHEN result_blocks = 1 THEN structured_result END,
           denial_kind) AS outcome_base,
       coalesce(denial_kind, '') AS denial_kind,
       left(trim(lake_flatten_result_text(block -> '$.content')), 4000) AS result_text,
       CASE WHEN result_blocks = 1 THEN structured_result END AS structured_result
FROM lake_block
WHERE record_type = 'user' AND json_extract_string(block, '$.type') = 'tool_result';

-- tool_use と tool_result の対応。id の一意性が lake で保証されないので、1 つの結果を受け取る
-- tool_use を高々 1 件に保つ: 同じ id の tool_use が続いたら、結果はそれより前で直近の tool_use が受け取る。
CREATE VIEW lake_tool_outcome AS
WITH uses AS (
    SELECT *, lead(line_seq) OVER (PARTITION BY path, tool_use_id ORDER BY line_seq, block_no) AS next_seq
    FROM lake_tool_use
)
SELECT u.* EXCLUDE (next_seq),
       coalesce(r.outcome_base, 'unknown') AS outcome_base,
       coalesce(r.denial_kind, '') AS denial_kind,
       coalesce(r.result_text, '') AS result_text,
       r.tool_use_id IS NOT NULL AS paired
FROM uses u
LEFT JOIN lake_tool_result r
       ON r.path = u.path AND r.tool_use_id = u.tool_use_id AND u.tool_use_id <> ''
      AND r.line_seq > u.line_seq AND (u.next_seq IS NULL OR r.line_seq < u.next_seq)
QUALIFY row_number() OVER (PARTITION BY u.path, u.line_seq, u.block_no ORDER BY r.line_seq, r.block_no) = 1;

-- 1 つの応答は content block ごとに複数の record に分かれ、各 record が同じ message id の usage を
-- 重ねて持つ (前の record は書きかけの値)。id ごとに書き終えた値、つまり output が最大の record だけを数える。
-- session の再開で応答の途中までが別の file へ写るので、file をまたいで id を 1 回だけ数える。
-- 同じ turn の再掲である iterations は足さない。
-- first_line_seq はその応答の file の中で最初の record で、応答を工程へ帰属させる位置になる。
CREATE VIEW lake_file_usage AS
SELECT *, CASE WHEN message_id IS NULL THEN line_seq
               ELSE min(line_seq) OVER (PARTITION BY path, message_id) END AS first_line_seq
FROM (
    SELECT path, transcript_kind, line_seq, session_id, ts, ts_epoch, cwd, attribution_skill,
           json_extract_string(message_doc, '$.id') AS message_id,
           coalesce(lake_json_int(message_doc, '$.usage.input_tokens'), 0) AS input_tokens,
           coalesce(lake_json_int(message_doc, '$.usage.output_tokens'), 0) AS output_tokens,
           coalesce(lake_json_int(message_doc, '$.usage.cache_creation_input_tokens'), 0) AS cache_creation_input_tokens,
           coalesce(lake_json_int(message_doc, '$.usage.cache_read_input_tokens'), 0) AS cache_read_input_tokens
    FROM lake_record
    WHERE contains(raw_line, '"usage"') AND record_type = 'assistant' AND json_type(message_doc, '$.usage') = 'OBJECT'
)
QUALIFY message_id IS NULL
     OR row_number() OVER (PARTITION BY path, message_id ORDER BY output_tokens DESC, line_seq DESC) = 1;

CREATE VIEW lake_assistant_usage AS
SELECT * FROM lake_file_usage
QUALIFY message_id IS NULL
     OR row_number() OVER (PARTITION BY message_id ORDER BY output_tokens DESC, path DESC, line_seq DESC) = 1;

-- slash 呼出しの判定の 1 本: 行頭で一致する・wrapper でない・先頭に slash がある。
-- 引数の無い呼出しには引数の tag が付かないので、引数の tag は条件にしない。
CREATE MACRO lake_is_slash_expansion(body) AS
    regexp_matches(coalesce(body, ''), '^\s*<command-(name|message)>');
CREATE MACRO lake_is_wrapper(body) AS contains(coalesce(body, ''), '<transcript-data>');
CREATE MACRO lake_slash_command(body) AS
    CASE WHEN lake_is_slash_expansion(body) AND NOT lake_is_wrapper(body)
         THEN nullif(trim(regexp_extract(body, '<command-name>/([^<]+)</command-name>', 1)), '') END;

CREATE VIEW lake_slash_invocation AS
SELECT path, transcript_kind, line_seq, raw_line, record_uuid, is_sidechain, session_id, ts, ts_epoch, cwd,
       command_name, command_args
FROM (
    SELECT *, lake_slash_command(json_extract_string(message_content, '$')) AS command_name,
           regexp_extract(json_extract_string(message_content, '$'), '(?s)<command-args>(.*?)</command-args>', 1) AS command_args
    FROM lake_record
    WHERE record_type = 'user' AND json_type(message_content) = 'VARCHAR'
)
WHERE command_name IS NOT NULL;

-- plugin の接頭辞を外した skill の名前。
CREATE MACRO lake_bare_skill(name) AS trim(regexp_extract(name, '[^:]*$'));

-- skill の実際の呼出し。record の構造で判定するので、本文に引用された記述は入らない。
-- 全期間を読むので、名前を含む行に絞ってから解釈する (全行を解釈すると memory の上限を超える)。
-- 2 つの経路を UNION で 1 本にしない。optimizer が共通の行の読み取りを積んで memory の上限を超える。
CREATE MACRO lake_skill_tool_call(bare) AS TABLE
SELECT path, line_seq, record_uuid, is_sidechain, ts, 'skill_tool' AS channel, unit_id AS called_name, skill_args AS args
FROM lake_tool_use
WHERE contains(raw_line, bare) AND tool = 'Skill' AND lake_bare_skill(unit_id) = bare;

CREATE MACRO lake_skill_slash_call(bare) AS TABLE
SELECT path, line_seq, record_uuid, is_sidechain, ts, 'command' AS channel, command_name AS called_name, command_args AS args
FROM lake_slash_invocation
WHERE contains(raw_line, bare) AND lake_bare_skill(command_name) = bare;

-- 名指しした file の最後の session id と cwd。slice の file 単位の anchor になる。
CREATE MACRO lake_file_anchor(paths) AS TABLE
SELECT path, arg_max(session_id, line_seq) FILTER (session_id <> '') AS session_id,
       arg_max(cwd, line_seq) FILTER (cwd <> '') AS cwd
FROM (
    SELECT path, row_number() OVER () AS line_seq,
           CASE WHEN lake_is_record(line) THEN coalesce(nullif(json_extract_string(line, '$.sessionId'), ''),
                                                        nullif(json_extract_string(line, '$.session_id'), ''), '') END AS session_id,
           CASE WHEN lake_is_record(line) THEN coalesce(json_extract_string(line, '$.cwd'), '') END AS cwd,
           CASE WHEN lake_is_record(line) THEN json_extract_string(line, '$.type') END AS record_type
    FROM lake_read_lines(paths)
)
WHERE record_type IN ('user', 'assistant')
GROUP BY path;

-- 呼出しの記述 (マーカー) を持つのに呼出しではない record を、理由を 1 つ付けて並べる。
-- assistant の record は引用と本物の呼出しが同居しうるので、呼出しの macro とは独立に数える。
CREATE MACRO lake_skill_marker_pattern(bare) AS
    '<command-name>/(?:[A-Za-z0-9_-]+:)?' || regexp_escape(bare) || '</command-name>'
    || '|"skill"\s*:\s*"(?:[A-Za-z0-9_-]+:)?' || regexp_escape(bare) || '"';
-- 生の行ではマーカーの引用符が JSON の文字列の中で escape されている。
CREATE MACRO lake_skill_marker_in_raw_line(line, bare) AS
    contains(line, bare) AND regexp_matches(line, replace(lake_skill_marker_pattern(bare), '"', '\\*"'));

-- 名前を含む行には巨大な行が多く、解釈すると memory の上限を超えるので、text のままマーカーの行へ絞りきってから解釈する。
CREATE MACRO lake_skill_marker_exclusion(bare) AS TABLE
WITH hits AS MATERIALIZED (
    SELECT path, line_seq, line
    FROM lake_line
    WHERE lake_skill_marker_in_raw_line(line, bare) AND lake_is_record(line)
), parsed AS (
    SELECT path, line_seq,
           json_extract_string(line, '$.type') AS record_type,
           json_extract_string(line, '$.subtype') AS subtype,
           coalesce(json_extract_string(line, '$.content'), '') AS record_content,
           line -> '$.message.content' AS message_content
    FROM hits
)
SELECT path, line_seq, reason
FROM (
    SELECT path, line_seq,
           CASE
               WHEN record_type = 'queue-operation'
                    AND regexp_matches(record_content, lake_skill_marker_pattern(bare)) THEN 'queue_operation'
               WHEN record_type = 'system' AND subtype = 'local_command'
                    AND regexp_matches(record_content, lake_skill_marker_pattern(bare)) THEN 'local_command_echo'
               WHEN record_type = 'assistant' AND json_type(message_content) = 'ARRAY'
                    AND regexp_matches(lake_flatten_result_text(message_content), lake_skill_marker_pattern(bare)) THEN 'quoted_marker'
               WHEN record_type = 'user' AND json_type(message_content) = 'VARCHAR'
                    AND regexp_matches(json_extract_string(message_content, '$'), lake_skill_marker_pattern(bare)) THEN
                   CASE WHEN lake_is_wrapper(json_extract_string(message_content, '$')) THEN 'wrapper_embedded'
                        WHEN lake_is_slash_expansion(json_extract_string(message_content, '$')) THEN NULL
                        ELSE 'quoted_marker' END
           END AS reason
    FROM parsed
)
WHERE reason IS NOT NULL;

-- user record の手入力 prompt の判定材料。形の解釈だけをここで持ち、発話の分類は問いの側に置く。
-- prompt_source と origin_kind の NULL は key が無いことを表す。
CREATE VIEW lake_user_prompt AS
SELECT path, split_part(substr(path, getvariable('lake_root_length') + 2), '/', 1) AS project_dir,
       transcript_kind, line_seq, record_uuid, session_id, ts, ts_epoch, cwd, git_branch, cli_version,
       is_meta, is_sidechain, is_compact_summary, prompt_source, origin_is_object, origin_kind,
       trim(CASE json_type(message_content)
                WHEN 'VARCHAR' THEN json_extract_string(message_content, '$')
                WHEN 'ARRAY' THEN array_to_string(list_filter(block_texts, lambda item: item IS NOT NULL), chr(10))
                ELSE ''
            END, chr(32) || chr(9) || chr(10) || chr(13)) AS body,
       CASE json_type(message_content)
           WHEN 'VARCHAR' THEN [json_extract_string(message_content, '$')]
           WHEN 'ARRAY' THEN list_filter(block_texts, lambda item: item IS NOT NULL)
           ELSE []
       END AS text_blocks,
       coalesce(json_type(message_content) = 'ARRAY'
                AND list_contains(block_types, 'tool_result'), false) AS has_tool_result
FROM (
    SELECT *,
           list_transform(blocks, lambda item: json_extract_string(item, '$.type')) AS block_types,
           list_transform(blocks, lambda item:
               CASE WHEN json_extract_string(item, '$.type') = 'text' AND json_type(item, '$.text') = 'VARCHAR'
                    THEN json_extract_string(item, '$.text') END) AS block_texts
    FROM (
        SELECT *, CASE WHEN json_type(message_content) = 'ARRAY'
                       THEN json_transform(message_content, '["JSON"]') END AS blocks
        FROM lake_record
        WHERE record_type = 'user'
    )
);

-- attachment の正規化。どの field に何が載るかは形式の事実なのでここで持つ。
-- contains は record の解釈の前に行を絞る前置きで、外すと巨大な行の chunk で memory の上限を超える。
CREATE VIEW lake_attachment AS
SELECT path, lake_file_kind_of(substr(path, getvariable('lake_root_length') + 2)) AS transcript_kind,
       line_seq, session_id, ts, ts_epoch, cwd,
       coalesce(json_extract_string(attachment_doc, '$.type'), '') AS attachment_type,
       attachment_doc
FROM (
    SELECT path, line_seq, lake_line_session(line) AS session_id,
           coalesce(lake_line_text(line, '$.timestamp'), '') AS ts, lake_line_epoch(line) AS ts_epoch,
           coalesce(lake_line_text(line, '$.cwd'), '') AS cwd,
           CASE WHEN lake_is_record(line) THEN line -> '$.attachment' END AS attachment_doc
    FROM lake_line
    WHERE contains(line, '"attachment"') AND lake_line_text(line, '$.type') = 'attachment'
)
WHERE json_type(attachment_doc) = 'OBJECT';

-- exit_code と duration_ms は成功の attachment にしか載らないので、NULL を 0 と読まない。
CREATE VIEW lake_hook_firing AS
SELECT path, transcript_kind, line_seq, session_id, ts, ts_epoch, cwd, attachment_type,
       coalesce(json_extract_string(attachment_doc, '$.hookName'), '') AS hook_name,
       -- hook の名前は `<event>:<tool>` で、tool を持たない hook は event だけ
       regexp_extract(coalesce(json_extract_string(attachment_doc, '$.hookName'), ''), '^[^:]*:(.*)$', 1) AS hook_tool,
       coalesce(json_extract_string(attachment_doc, '$.hookEvent'), '') AS hook_event,
       coalesce(json_extract_string(attachment_doc, '$.command'), '') AS command,
       lake_json_int(attachment_doc, '$.exitCode') AS exit_code,
       lake_json_int(attachment_doc, '$.durationMs') AS duration_ms,
       lake_json_is_true(attachment_doc, '$.timedOut') AS timed_out,
       coalesce(json_extract_string(attachment_doc, '$.toolUseID'), '') AS tool_use_id
FROM lake_attachment
WHERE attachment_type IN ('hook_success', 'hook_cancelled', 'hook_additional_context', 'hook_system_message');

-- session に提示された名前。除去は追わない — 分母の定義が「一度でも提示された」だから。
CREATE VIEW lake_presented_name AS
SELECT path, transcript_kind, line_seq, session_id, ts, ts_epoch, cwd, attachment_type, unnest(names) AS unit_name
FROM (
    SELECT *,
           CASE attachment_type
               WHEN 'skill_listing' THEN lake_json_strings(attachment_doc -> '$.names')
               WHEN 'mcp_instructions_delta' THEN lake_json_strings(attachment_doc -> '$.addedNames')
               WHEN 'agent_listing_delta' THEN lake_json_strings(attachment_doc -> '$.addedTypes')
               WHEN 'deferred_tools_delta' THEN list_concat(lake_json_strings(attachment_doc -> '$.addedNames'),
                                                            lake_json_strings(attachment_doc -> '$.readdedNames'))
           END AS names
    FROM lake_attachment
    WHERE attachment_type IN ('skill_listing', 'mcp_instructions_delta', 'agent_listing_delta', 'deferred_tools_delta')
);

-- attachment の本文 (文字列か文字列の list) を 1 本の text に潰す。
CREATE MACRO lake_attachment_text(field) AS
    CASE json_type(field)
        WHEN 'VARCHAR' THEN json_extract_string(field, '$')
        WHEN 'ARRAY' THEN array_to_string(lake_json_strings(field), chr(10))
        ELSE ''
    END;

CREATE VIEW lake_static_payload AS
SELECT path, transcript_kind, line_seq, session_id, ts, ts_epoch, cwd, attachment_type,
       CASE attachment_type
           WHEN 'skill_listing' THEN 'skill_listing'
           WHEN 'mcp_instructions_delta' THEN 'mcp_instructions'
           WHEN 'deferred_tools_delta' THEN 'deferred_tools'
           WHEN 'agent_listing_delta' THEN 'agent_listing'
       END AS source,
       length(body) AS chars, lake_cjk_chars(body) AS cjk_chars
FROM (
    SELECT *,
           CASE attachment_type
               WHEN 'skill_listing' THEN lake_attachment_text(attachment_doc -> '$.content')
               WHEN 'mcp_instructions_delta' THEN lake_attachment_text(attachment_doc -> '$.addedBlocks')
               ELSE lake_attachment_text(attachment_doc -> '$.addedLines')
           END AS body
    FROM lake_attachment
    WHERE attachment_type IN ('skill_listing', 'mcp_instructions_delta', 'agent_listing_delta', 'deferred_tools_delta')
);

-- memory file の注入。本文・種類・globs は内側の object に入れ子で載り、path は内外の両方に現れうる。
CREATE VIEW lake_memory_injection AS
SELECT path AS transcript_path, transcript_kind, line_seq, session_id, ts, ts_epoch, cwd,
       coalesce(nullif(json_extract_string(attachment_doc, '$.path'), ''),
                nullif(json_extract_string(inner_doc, '$.path'), ''), '') AS memory_path,
       coalesce(json_extract_string(attachment_doc, '$.displayPath'), '') AS display_path,
       coalesce(json_extract_string(inner_doc, '$.type'), '') AS memory_type,
       lake_json_strings(inner_doc -> '$.globs') AS path_globs,
       length(body) AS chars,
       lake_cjk_chars(body) AS cjk_chars,
       CASE WHEN body = '' THEN 0 ELSE len(string_split(body, chr(10))) END AS body_lines,
       lake_json_is_true(inner_doc, '$.contentDiffersFromDisk') AS differs_from_disk
FROM (
    SELECT *, lake_attachment_text(inner_doc -> '$.content') AS body
    FROM (
        SELECT *, CASE WHEN json_type(attachment_doc, '$.content') = 'OBJECT'
                       THEN attachment_doc -> '$.content' END AS inner_doc
        FROM lake_attachment
        WHERE attachment_type = 'nested_memory'
    )
);

-- cumulative_dropped_tokens は session の中で累積する値なので、問いの側は session ごとに最後の 1 件を採る。
-- contains は subtype の条件の言い直しではなく、record の解釈の前に行を絞る前置き。
CREATE VIEW lake_compact_boundary AS
SELECT path, transcript_kind, line_seq, session_id, ts, ts_epoch, cwd,
       coalesce(json_extract_string(compact_doc, '$.trigger'), '') AS compact_trigger,
       lake_json_int(compact_doc, '$.preTokens') AS pre_tokens,
       lake_json_int(compact_doc, '$.postTokens') AS post_tokens,
       lake_json_int(compact_doc, '$.cumulativeDroppedTokens') AS cumulative_dropped_tokens,
       lake_json_int(compact_doc, '$.durationMs') AS duration_ms
FROM lake_record
WHERE contains(raw_line, '"compact_boundary"') AND record_type = 'system' AND subtype = 'compact_boundary';

-- subagent の起動元との対応。直下の体は隣の meta の id が起動した tool_use を指し、workflow の体は
-- 主 transcript の起動の結果が持つ run の id が dir 名と一致することで起動元に結び付く。
CREATE VIEW lake_workflow_run AS
SELECT path, line_seq, tool_use_id, json_extract_string(structured_result, '$.runId') AS workflow_run_id
FROM lake_tool_result
WHERE json_type(structured_result, '$.runId') = 'VARCHAR'
  AND json_extract_string(structured_result, '$.runId') <> '' AND tool_use_id <> '';

CREATE VIEW lake_subagent_meta AS
SELECT regexp_replace(filename, '\.meta\.json$', '.jsonl') AS path,
       try(decode(content)) AS meta_text
FROM read_blob(getvariable('subagent_meta_paths'));

-- 体は transcript か meta のどちらかが残っていれば 1 体に数える。meta だけ残った体は has_transcript が偽になる。
CREATE VIEW lake_subagent_origin AS
WITH bodies AS (
    SELECT path, session_dir, agent_id, workflow_run_id, readable AS has_transcript
    FROM lake_file
    WHERE file_kind = 'subagent'
    UNION ALL
    SELECT path, regexp_extract(rel, '^([^/]+/[^/]+)/', 1), lake_agent_id_of(rel), lake_workflow_run_of(rel), false
    FROM (SELECT regexp_replace(path, '\.meta\.json$', '.jsonl') AS path,
                 regexp_replace(rel, '\.meta\.json$', '.jsonl') AS rel
          FROM lake_entry
          WHERE suffix(rel, '.meta.json') AND lake_in_named_session(rel))
    WHERE lake_file_kind_of(rel) = 'subagent' AND path NOT IN (SELECT path FROM lake_file)
)
SELECT f.path, f.session_dir, f.agent_id, nullif(f.workflow_run_id, '') AS workflow_run_id, f.has_transcript,
       coalesce(m.meta_ok, false) AS meta_readable,
       nullif(json_extract_string(m.meta_text, '$.parentAgentId'), '') AS parent_agent_id,
       coalesce(nullif(json_extract_string(m.meta_text, '$.toolUseId'), ''), w.tool_use_id) AS launch_tool_use_id
FROM bodies f
LEFT JOIN (SELECT path, meta_text,
                  coalesce(CASE WHEN json_valid(meta_text) THEN json_type(meta_text) = 'OBJECT' END, false) AS meta_ok
           FROM lake_subagent_meta) m
       ON m.path = f.path AND m.meta_ok
LEFT JOIN lake_workflow_run w
       ON w.workflow_run_id = f.workflow_run_id AND f.workflow_run_id <> ''
      AND regexp_replace(w.path, '\.jsonl$', '') = regexp_replace(f.path, '/subagents/.*$', '');

-- 決定的ルールの器。mart の unit 1 行を受け取り、候補・導出過程・未判定の条件までを出す純関数で、
-- bucket を確定しない。条件の入力が NULL なら満たしていないと読む (判定できない unit を発火させない)。
CREATE MACRO lake_rule_evaluation(rule_id, bucket_candidate, checks, rule_inputs, open_predicates) AS
    {'rule_id': rule_id, 'bucket_candidate': bucket_candidate,
     'failed': list_filter(checks, lambda c: NOT coalesce(c.passed, false)),
     'rule_inputs': rule_inputs, 'open_predicates': open_predicates};

-- 1 unit の評価をまとめる。発火も near miss も無い unit は NULL を返す。
-- near_misses は「あと 1 条件」だけで、落ちた条件が母集団を絞る条件なら載せない (載せると母集団の写しになる)。
CREATE MACRO lake_rule_verdict(unit, evaluations) AS
    (WITH applied AS (SELECT list_filter(evaluations, lambda e: e IS NOT NULL) AS items),
          split AS (SELECT items,
                           list_filter(items, lambda e: len(e.failed) = 0) AS fired,
                           list_filter(items, lambda e: len(e.failed) = 1 AND e.failed[1].report_near_miss) AS near
                    FROM applied)
     SELECT CASE WHEN len(fired) + len(near) > 0 THEN
         {'unit': unit,
          'bucket_candidate': fired[1].bucket_candidate,
          'rule_fired': list_transform(fired, lambda e: e.rule_id),
          -- json_merge_patch は null の key を消すので、入力名を凍結できない
          'rule_inputs': to_json(list_reduce(list_transform(items, lambda e: CAST(e.rule_inputs AS MAP(VARCHAR, JSON))),
                                             lambda merged, next: map_concat(merged, next))),
          'open_predicates': flatten(list_transform(fired, lambda e: e.open_predicates)),
          'near_misses': list_transform(near, lambda e: {'rule_id': e.rule_id,
                                                         'failed_condition': e.failed[1].name,
                                                         'inputs': e.rule_inputs})}
     END FROM split);

-- permissions の rule。入力は mart の entry の表の名前。
-- 意味の判断を allowlist にしない: 「副作用を起こせるか」を危険なコマンドの一覧として rule へ落とさない。
-- 古くなった一覧は新しい tool と改名を取りこぼすので、open_predicates に留める。
CREATE MACRO lake_rule_permissions(units, high_deny_min_match, high_deny_min_ratio) AS TABLE
SELECT unnest(verdict)
FROM (
    SELECT lake_rule_verdict(unit, [
        CASE WHEN category IN ('allow', 'ask') THEN lake_rule_evaluation(
            'revoke_candidate', 'revoke-pending',
            [{'name': 'relative_judgment_available', 'passed': sufficient, 'report_near_miss': true},
             {'name': 'zero_match', 'passed': match_count = 0, 'report_near_miss': false},
             {'name': 'matcher_exact', 'passed': matcher_confidence = 'exact', 'report_near_miss': true},
             {'name': 'no_sandbox_pair', 'passed': NOT sandbox_pair, 'report_near_miss': true}],
            to_json({'match_count': match_count, 'matcher_confidence': matcher_confidence,
                     'unmatchable_reason': unmatchable_reason, 'unobserved_input_count': unobserved_input_count,
                     'category': category, 'total_events': total_events, 'sandbox_pair': sandbox_pair}),
            ['side_effect_capability', 'exposure_opportunity', 'alias_still_in_use', 'invocation_form_pair'])
        END,
        lake_rule_evaluation(
            'compound_line_deny_miscount', 'refine-pending',
            [{'name': 'high_deny_share',
              'passed': match_count >= high_deny_min_match AND hard_deny_share >= high_deny_min_ratio,
              'report_near_miss': false},
             {'name': 'compound_line_deny_present', 'passed': compound_command_deny_count > 0, 'report_near_miss': true}],
            to_json({'hard_deny_count': hard_deny_count, 'hard_deny_share': round(hard_deny_share, 2),
                     'compound_command_deny_count': compound_command_deny_count}),
            ['deny_attributable_to_entry'])]) AS verdict
    FROM (SELECT *, CASE WHEN match_count > 0 THEN hard_deny_count / match_count ELSE 0.0 END AS hard_deny_share
          FROM query_table(units))
)
WHERE verdict IS NOT NULL;

-- invocations の rule。入力は mart の unit の表の名前 (unit_kind は skill / mcp_server / plugin)。
-- 意味の判断を allowlist にしない: 「思想系の skill か」を skill 名の一覧として rule へ落とさない。
-- 一覧に固定すると新設の skill を取りこぼすので、open_predicates に留める。
-- plugin の外しかたは配下を巻き込むので、no_sibling_usage は plugin にだけ足す。
CREATE MACRO lake_rule_invocations(units) AS TABLE
SELECT unnest(verdict)
FROM (
    SELECT lake_rule_verdict(unit, [lake_rule_evaluation(
        'unused_' || unit_kind,
        CASE unit_kind WHEN 'skill' THEN 'delete-candidate-pending'
                       WHEN 'mcp_server' THEN 'disconnect-candidate-pending'
                       WHEN 'plugin' THEN 'uninstall-candidate-pending' END,
        list_concat(
            [{'name': 'relative_judgment_available', 'passed': sufficient, 'report_near_miss': true},
             {'name': 'zero_invocation', 'passed': "count" = 0, 'report_near_miss': false},
             {'name': 'presented_at_least_once', 'passed': sessions_presented > 0, 'report_near_miss': true},
             {'name': 'denominator_source_config', 'passed': denominator_source = 'config', 'report_near_miss': true}],
            CASE WHEN unit_kind = 'plugin'
                 THEN [{'name': 'no_sibling_usage', 'passed': sibling_invocations = 0, 'report_near_miss': true}]
                 ELSE [] END),
        CASE WHEN unit_kind = 'plugin'
             THEN to_json({'count': "count", 'denominator_source': denominator_source,
                           'total_invocations': total_invocations, 'sessions_presented': sessions_presented,
                           'sibling_invocations': sibling_invocations})
             ELSE to_json({'count': "count", 'denominator_source': denominator_source,
                           'total_invocations': total_invocations, 'sessions_presented': sessions_presented})
        END,
        list_concat(['rename_or_removal_in_window', 'denominator_completeness'],
                    CASE WHEN unit_kind = 'plugin' THEN []::VARCHAR[] ELSE ['removable_independently'] END))]) AS verdict
    FROM query_table(units)
)
WHERE verdict IS NOT NULL;

-- engineering-values の rule。入力は定型の正規形の表の名前で、全 repo を対象にした slice だけを評価する。
-- 意味の判断を allowlist にしない: 「起動文か」を文面のパターンの一覧として rule へ落とさない。
-- 定型の判定は正規化した後のバイト一致だけで、規範として読めるかは open_predicates に留める。
CREATE MACRO lake_rule_cross_repo(forms, repo_scope, min_repo_count) AS TABLE
SELECT unnest(verdict)
FROM (
    SELECT lake_rule_verdict(unit, [lake_rule_evaluation(
        'cross_repo_recurrence', 'cross-repo-norm-candidate',
        [{'name': 'multi_repo', 'passed': repo_count >= min_repo_count, 'report_near_miss': true}],
        to_json({'repo_count': repo_count, 'repos': repos, 'occurrences': occurrences}),
        ['norm_readable', 'paste_boundary', 'container_classification'])]) AS verdict
    FROM query_table(forms)
    WHERE repo_scope = 'all'
)
WHERE verdict IS NOT NULL;
