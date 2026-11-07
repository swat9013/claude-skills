"""permissions の問いの data contract (成果物の読み方の単一ソース)。

成果物の `contract` key に載る。SKILL.md は本 contract を参照し、schema と読み方を再記述しない。
ここに在る閾値は sort / filter のパラメータで、bucket を確定しない。決定的ルールの評価は view が行い、
rule カタログも本 contract が emit する。bucket の確定は呼び出し元の責務。
"""

from __future__ import annotations

# 読み手側の将来分岐用に単調増加させる。v12 で分割 file を成果物の `rows` の part へ畳んだ。
SCHEMA_VERSION = 12

DEFAULT_SUFFICIENT_THRESHOLD = 30
BYPASS_LOOKAHEAD = 5
BYPASS_MAX_GAP_SECONDS = 300

# derived_views の集計パラメータ。
DERIVED_TOP_N = 30
HIGH_DENY_MIN_MATCH = 20
HIGH_DENY_MIN_RATIO = 0.3
# `axis_b_unlisted_frequent` の収載床。**数える対象は unit の総件数ではなく
# `uncovered_event_count`** (= 当該 section の config のどの entry にも当たらなかった
# 実行の数)。値そのものは policy なので観測は動かさない — 何件の未収載実行が
# あれば収載を検討すべきかの確定は読み手に残す。
UNLISTED_MIN_COUNT = 5
FOLLOWUP_FAST_GAP_SECONDS = 10
HARD_DENY_OUTCOMES = ("deny_permission-rule", "deny_automode")

# 観測窓の変化点フラグ。窓を **midpoint で二分**し、前半 / 後半の hard deny
# 比率を比べる。**変化点の位置は求めない** — フラグの役目は「窓全体の比率を entry の
# 性質として読むな」の合図までで、位置と原因の特定は読み手に残す。
#
# 閾値の根拠 (恣意的な値を置かないため既存の決定に紐づける):
# - MIN_SHARE_DELTA は HIGH_DENY_MIN_RATIO と同値。差がこの幅に達すると前半と後半が
#   `axis_a_high_deny_share` の収載条件 (>= HIGH_DENY_MIN_RATIO) をまたぎうる —
#   **この view 自身の分類が半分ごとに変わる**大きさを「有意」の定義に採る
# - MIN_MATCH は view の収載床 HIGH_DENY_MIN_MATCH の半分。n = 10 で 0.3 の差は
#   3 件以上の差を要するので、1 件の増減ではフラグが立たない
WINDOW_SPLIT_MIN_MATCH = 10
WINDOW_SPLIT_MIN_SHARE_DELTA = HIGH_DENY_MIN_RATIO

# part の読むパラメータ。
BYPASS_SAMPLE_GROUPS = 10
BYPASS_SAMPLES_PER_GROUP = 2

# 成果物の `rows` の並び = 読む順。purpose は data 内容の記述だけで、確定した bucket の語彙を含めない。
PARTS = (
    {"part": "meta", "purpose": "観測窓・判定可能性・分母 + section 概況 (判定可能性の分岐に必要な最小情報)"},
    {"part": "derived_views", "purpose": "derived_views + guard_reverse_lookup"},
    {"part": "rule_candidates",
     "purpose": "決定的ルールの評価結果 (bucket_candidate / rule_fired / rule_inputs / open_predicates / "
                "near_misses)。**判断するのは open_predicates だけ**で、bucket の確定と最終採否は下流に残る"},
    {"part": "axis_a", "purpose": "全設定 entry の両軸集計 (全 entry を含む母集団)"},
    {"part": "bypass_samples", "purpose": "top bypass group の代表系列"},
    {"part": "hooks",
     "purpose": "hook の設定側分母 × fire 実績 (fire_count null = 未観測 / 遅い / timeout の観測。"
                "observability に観測限界を同梱)"},
)

# derived view の view 名 → 1 行意味論 (contract の views に emit)。
# build_derived_views の出力の key 集合と一致させる (整合性テストで固定)。
DERIVED_VIEW_SEMANTICS = {
    "axis_a_zero_match": (
        "観測窓内で match_count == 0 の設定 entry。**0 の意味は matcher_confidence で"
        "変わる** — unmatchable の行は「使われていない」ではなく「照合が成立して"
        "いない」で、どの理由で成立していないかは `unmatchable_reason` "
        "(input_column_empty / param_rule) が持つ (notes を参照)。"),
    "axis_a_high_deny_share": (
        f"match_count >= {HIGH_DENY_MIN_MATCH} かつ hard deny "
        f"(permission-rule + automode) 比率 >= {HIGH_DENY_MIN_RATIO} の entry。"
        "user-rejected は Claude Code の #29499 の false positive 影響下のため hard deny に数えない。"
        "各 row の `window_split` は観測窓を midpoint (meta.observation_window.midpoint) で"
        "二分した前半 / 後半の内訳で、`shifted: true` なら **窓全体の hard_deny_share を "
        "entry の性質として読んではいけない** (変更前後を混ぜた平均で、どちらの期間も"
        "表していない) — 前半 / 後半を別々に読む。`shifted: false` は「窓内で一様」、"
        f"`null` は判定不能 (前半 / 後半のどちらかが match_count < {WINDOW_SPLIT_MIN_MATCH}) "
        "で、0 件と未判定を潰さない。判定は "
        f"|前半 share - 後半 share| >= {WINDOW_SPLIT_MIN_SHARE_DELTA} の 1 条件のみ。"
        "**変化点の位置も原因も求めない** (窓端の変化は delta が薄まりフラグが立たない"
        "ことがある。フラグが立たないことは一様さの証明ではない)。ts 欠損 event は"
        "どちらの半分にも入らないため early + late < match_count がありうる。"
    ),
    "axis_b_unlisted_frequent": (
        f"`uncovered_event_count >= {UNLISTED_MIN_COUNT}` の permission 関連 unit "
        "(Bash / mcp__* / deny_permission-rule 実績あり)。permission entry でゲート"
        "されない built-in tool は units から除外し omitted_non_permission_units に件数計上。"
        "**収載床も並べ替えも `uncovered_event_count`** (総件数ではない) — 5,000 件中 "
        "4,999 件が収載済みの unit を「未収載」として上位に出さないため。"
        "未収載の判定は **section の config だけ**を見る (project section なら project + "
        "project_local + worktree なら親 clone の project_local_main_clone。global 層は "
        "`~/.claude/settings.json` 1 本で、`<config dir>/settings.local.json` は "
        "Claude Code が読まない層なので分母に入れない)。"
        "`uncovered_event_count > 0` は「どこにも収載されていない」ではないので、"
        "global 層の被覆は units から除かず `global_config_matches` "
        "(entry / category / scope / matched_event_count) と "
        "`global_uncovered_event_count` に別列で出す — section global では "
        "config_matches / uncovered_event_count と同値。"
        "**突合は unit 内の全 event に対して行う** (代表 1 件の近似ではない)。"
        "**この view に載る unit は定義上すべて未被覆 >= 収載床**なので、"
        "`config_matches` に match がある unit は例外なく**部分被覆** (既存 entry が "
        "unit の一部しか覆っていない) — `uncovered_event_count` の非 0 は判別条件に"
        "使えない。**section project でのみ** `global_uncovered_event_count` が判別に"
        "効く (0 なら global 層では全件が覆われている)。**section global では両者が"
        "同値なので判別材料が無い** — 収載床未満まで含めて被覆を見るなら ad-hoc の問いで "
        "当該 tool の実行を直接引く。"
        "**未被覆が床未満の部分被覆 unit はこの view に出ない** (全量 "
        "`axis_b_actual_usage` は成果物に載せないので、床未満を見るなら "
        "ad-hoc の問いで当該 tool の実行を直接引く)。"
        "`matched_event_count` の和が unit の count を超えることはある — "
        "1 実行が複数 entry にマッチしうるため、和を被覆件数として読まない。"
        "被覆件数が要るなら差 (count - uncovered_event_count) を見る。"
    ),
    "bypass_grouped": (
        "(denial_kind, denied_tool, denied_command_head) 別の系列数と、first follow_up が "
        f"success かつ gap <= {FOLLOWUP_FAST_GAP_SECONDS} 秒の件数 (代替経路の存在示唆)。"
    ),
}

# hook 観測の限界を mart に同梱する。**`nonzero_exit_count: 0` を「失敗していない」と
# 読ませないための注記**で、matcher_confidence と同じ役割 (観測の確度を数値の隣に置く)。
HOOK_OBSERVABILITY = {
    "exit_code_source": "hook_success の記録のみ (他 3 種は終了コードを持たない)",
    "duration_source": "hook_success / hook_cancelled の記録の所要",
    "failure_confidence": "approx",
    "notes": [
        "nonzero_exit_count は「観測窓内に非 0 終了が記録されなかった」であって"
        "「hook が失敗していない」ではない。実測で hook_success の終了コードは全件 0",
        "timeout による打ち切りは hook_cancelled の記録にしか出ない",
        "Stop hook の要約の記録は command と所要だけで、hook の名前も終了コードも持たないため、"
        "Stop hook の帰属には使えない",
        "無出力で pass する hook は attachment を残さないため観測に残らない。"
        "attribution が付かない unit (matched_by: null) の fire_count は null (観測不能) — "
        "「発火条件を満たす操作が窓内に無かった」と「発火したが観測に残らなかった」を"
        "transcript からは区別できない (fire_count 0 は configured には現れない)",
        "fire_count null (未観測) の判定が成立するのは configured (設定側の分母) に載る "
        "unit だけ。observed_unlisted は fire した attachment からしか作られない",
        "key_collision: true の unit は fire_count を同 key の他 unit と共有する。"
        "「fire していない」は主張できるが「n 回動いた」は主張できない",
        "command を持たない attachment (hook_additional_context / hook_system_message) は"
        "hook の名前でしか引けず、matcher が `*` の設定には帰属しない (observed_unlisted に残る)",
        "pre_tool_use_per_call は 1 回の tool 呼び出しごとに PreToolUse hook の "
        "所要の max を取った分布 (並列に走るので和ではなく max)。PostToolUse は含まない"
        " — 無出力の PostToolUse hook は attachment を残さないので、1 回分の所要を transcript から"
        "求められない (hook 段 = Pre + Post の和は lab で測る)。non_global は user settings に"
        "だけ登録された hook と設定に紐づかない firing を除いた max。"
        "母集団は meta.transcript_kind が選んだ transcript (下の META_NOTES の "
        "transcript_kind の注記)",
    ],
}

# 読み方の注記。注記が要るのは mart を読む段階だけなので、skill の本文ではなく mart に同梱する。
META_NOTES = (
    "sufficient_for_relative_judgment が false なら相対判定 (未使用の entry を"
    "「使われていない」と読むこと) は成立しない — 観測不足であって不使用の証拠ではない。",
    "outcome の deny_user-rejected は Claude Code の #29499 の false positive バグ影響下 (best-effort な近似)。",
    "matcher の glob pattern は fnmatch による近似 (Claude Code 本体の matcher と揺れる余地あり)。",
    "**matcher_confidence が unmatchable の entry は match_count 0 を「未使用」と"
    "読んではいけない** — 突合が原理的に成立していない状態で、典型は窓内にその tool の"
    "実行が在るのに matcher が読む列 (Bash は command / `WebFetch(domain:…)` は URL の "
    "hostname / それ以外は file path) がどの実行でも空だった場合 "
    "(`unmatchable_reason: input_column_empty`)。"
    "revoke 候補は matcher_exact を条件に持つのでこの entry では発火しない。"
    "part rule_candidates の near_misses に failed_condition: matcher_exact として"
    "落選理由つきで出るのは、**外れた条件がそれ 1 つだけのとき**に限る "
    "(near-miss は「あと 1 条件」の行しか作らない) — "
    "sufficient_for_relative_judgment が false の窓では relative_judgment_available も"
    "同時に外れ、その entry は rule_candidates に 1 行も出ない。**窓が足りないときは "
    "part axis_a の matcher_confidence を直接見る**。"
    "判断するには view に照合対象の列を足すか、その tool の入力を ad-hoc の問いで直接見る。"
    "**tool の実行自体が窓内に 0 件の entry は unmatchable にならない** — "
    "「使わなかった」は真の観測なので、宣言どおりの exact / approx が残る。",
    "A 軸 entry の `unobserved_input_count` は、matcher が読む列を持たなかったその tool の"
    "実行数。**0 でなければ match_count は確認できた範囲の下限**で、0 件でも「その pattern を"
    "使っていない」とは言い切れない。実測ではこの差の実体は "
    "入力を解釈できず error になった呼び出し (tool が実行されていない) なので、"
    "**revoke_candidate の条件にはしていない** — 何件までなら 0 を不使用の証拠と見なすかは"
    "閾値の policy であり、観測は数を出すところまでを担う。rule_inputs にも同じ数が載る。"
    "引数が任意の tool (`Grep` の `path` 等) では正当な省略もこの数に入るので、"
    "revoke 候補を採る前にこの数を見る。"
    "**本体が rule ごと skip する括弧付き `mcp__…` entry では常に 0**。"
    "skip される rule に"
    "「下限」は無い (照合が成立していないのではなく rule が存在しない) ので、"
    "match_count 0 は確定値。同じ理由で `matcher_confidence` も宣言値の `approx` ではなく "
    "`exact` になる — 括弧の中身 (`(x)` か `(*)` か) で revoke 候補に出たり出なかったり"
    "しないため。",
    "unmatchable の理由は A 軸 entry と revoke_candidate の rule_inputs の "
    "`unmatchable_reason` が持つ。`input_column_empty` は照合対象の列が空 (上記)、"
    "`param_rule` は `Tool(param:value)` 形 (`Bash(run_in_background:true)` 等) で "
    "**その param 名を渡した実行が窓内に在るのに、view が param の値を持たないため"
    "照合できない**状態。判別は observed の input param 名で行い、tool ごとの param 名"
    "一覧は持たない (陳腐化する一覧を作らないため)。"
    "**本体が rule ごと無視する形は判別対象外**で、その 0 は真の観測 (revoke 候補に"
    "出るのが正しい): allow entry の param rule (公式 doc: param rule は deny / ask のみ)、"
    "primary content field を名指した entry (`Bash(command:*)` / `Read(file_path:*)` — "
    "公式 doc が「本体は rule ごと無視して起動時に warning を出す」と定める)、"
    "括弧付きの `mcp__…` entry (同じく本体が settings 読み込み時に skip する)。"
    "**残る限界は 2 つ**: (1) その param 名が窓内で 1 度も渡されていないと判別材料が"
    "無い — ただしその場合は公式 doc の「渡されなかった param は match しない」に"
    "より 0 件が真の観測なので、exact のまま revoke 候補に出るのが正しい。"
    "(2) deny / ask に書いた `Bash(timeout:*)` のように param 名と"
    "同名の command を持つ prefix rule は、窓内に 1 件もマッチしなければ param rule 側へ"
    "倒れる (1 件でもマッチしていれば command prefix として扱う)。**この tie-break は"
    "公式 doc に根拠が無い観測の保守的な仮定**で、doc は名前が衝突したときの"
    "優先順位に触れていない。"
    "**param rule の match_count は依然として実績ではない** — 値を突合していないので、"
    "使用実績が要るなら **transcript (lake) の当該 tool_use を直接読む**。"
    "**`input_excerpt` の列では数えない**: 200 字上限で Bash は `command` が先に直列化されるため、"
    "長い command の呼び出しでは param が切り落とされ、実在する呼び出しを 0 件と読む。"
    "ad-hoc の問いで lake の生の行を読む。",
    "**非 Bash tool の B 軸 unit は引数を鍵に持たない** (unit key は "
    "(tool, command_head) で command_head は Bash 以外では空文字なので、"
    "`Read` や `WebFetch` は引数の別を問わず 1 unit に潰れる)。**それでも収載の"
    "判定は unit 内の全 event で行う** ので、`Read(**/*.env)` のような引数依存の "
    "entry でも代表 event の当たり外れで像が反転することはない。読み方は 3 つ: "
    "(1) `config_matches[].matched_event_count` がその entry の被覆件数、"
    "(2) `uncovered_event_count` がどの entry にも当たらなかった件数、"
    "(3) 両方が非 0 なら**部分被覆** — unit の一部だけが収載されている。"
    "**これは B 軸の全量 (`axis_b_actual_usage`) を読むときの判別で、"
    "`axis_b_unlisted_frequent` の中では成り立たない** (あの view の収載条件が"
    "未被覆 >= 床なので常に真になる)。view の中での読み方と和の扱いは "
    "`axis_b_unlisted_frequent` の view 意味論が正本 (contract.views)。"
    "**A 軸 (part axis_a) と同じ全 event を照合するので、両軸の値は矛盾しない** — "
    "A 軸の match_count > 0 なら、その entry は B 軸のいずれかの unit で非 0 の "
    "matched_event_count を持つ。ただし**この一致を出力ファイルだけで確かめることは"
    "できない**: B 軸の全量 (`axis_b_actual_usage`) は成果物に載せず、"
    "part derived_views に出るのは未被覆が床を超えた unit だけなので、全件収載済みの "
    "unit は出力に現れない。突き合わせるなら ad-hoc の問いで当該 tool の実行を直接引く。"
    "**unit 粒度の限界は残る**: どの引数の実行が未収載なのかは B 軸からは分からない "
    "(unit key に引数が無い)。**A 軸の sample_matched では代替できない** — あれは entry に"
    "match した実行だけの (tool, command_head, count) で、非 Bash tool では "
    "command_head が空文字なので引数を 1 文字も持たない。未被覆側の実体が要るなら "
    "ad-hoc の問いで当該 tool の実行を直接見る。"
    "**未被覆には照合不能な実行も混ざる**: matcher が読む列 (Bash は command / "
    "domain は URL の hostname / それ以外は file path) を持たない実行はどの entry にも"
    "当たらないので、必ず未被覆に数えられる。引数が任意の tool (`Grep` の `path` 等) "
    "では正当な省略もここに入る。**A 軸の `matcher_confidence` / "
    "`unmatchable_reason` / `unobserved_input_count` にあたる札を B 軸の unit は"
    "持たず、A 軸で代替もできない** — A 軸の行は config entry を列挙して作るので、"
    "その section の config にその tool の entry が 1 件も無ければ数そのものが出力に"
    "現れない (`config_matches: []` の unit = まさに promote 候補がこれに当たる)。"
    "entry が在る場合も値は entry ごと・match_kind ごとなので、unit 1 つに対して"
    "どの数を引くかが決まらない。**純粋な未収載件数が要るなら ad-hoc の問いで当該 tool の"
    "実行を直接見る**。",
    "**entry の tool 名は完全一致で突合しない**。公式 doc の MCP 節が定めるとおり、"
    "`mcp__foo` は foo server の全 tool を、`mcp__foo__*` は tool 名位置の wildcard として"
    "同じ集合を被覆する。**A 軸と B 軸は同じ被覆表を通る**ので、server 単位 entry の "
    "match_count と、その server の tool の `config_matches` は同じ関係から出る。"
    "server 単位 entry は `__` 境界を要求する (`mcp__foo` は `mcp__foobar__baz` を"
    "被覆しない)。**非 MCP の tool 名 glob (deny の `*` / `B*`) は未実装**で完全一致の"
    "まま — 当てない側が保守的なので、その 0 は「実装が当てていない」であって"
    "「使っていない」ではない。**allow の錨の無い wildcard** (`mcp__*` 等) は公式 doc が"
    "「skip して warning を出す」と定めるので被覆しない — その 0 は真の観測。",
    "guard_reverse_lookup は transcript の deny の種類 + Reason label ベース (hooks.json の静的列挙はしない)。",
    "bucket 判定は観測では行わない (責務境界: 判定は呼び出し元の手順の LLM 段階)。",
    "part hooks は section (cwd scope) で絞らない — 「30 日どこでも fire していない」"
    "が「fire していない hook」の主張になるため、分母を窓全体に取る。",
    "観測窓は record の timestamp だけで切る。"
    "timestamp を持たない record は保守的に窓内へ倒す。件数は返り値の `degradation` の "
    "`ts_missing_records` が持つ。",
    "meta.settings_denominator が分母 path の全量。read: false の行が「存在するのに"
    "読まなかった層」で、reason (out_of_section / absent / unparsed / cross_layer_match / "
    "not_a_settings_layer) が理由を示す。**`uncovered_event_count` を読む前にここを見る** — "
    "層が抜けていれば未収載の判定自体が成立しない。path (解決前) と resolved_path (symlink "
    "解決後) は別列で、両者が違えば symlink 経由。",
    "project 層は repo_root が git worktree のとき 3 本 (worktree の settings.json / "
    "settings.local.json + 親 clone の settings.local.json = scope "
    "project_local_main_clone)。project の settings.json は cwd 起点、local は "
    "canonical git root 起点で解決されるため、worktree では親 clone 側の local も"
    "読まれる。entry の scope / source_path でどちらの層から来たかを見分ける。",
    "`<config dir>/settings.local.json` は Claude Code の settings 層ではない "
    "(v2.1.234 実測: user scope の file 名は常に settings.json で、.local.json を"
    "作るのは project の local scope だけ)。置かれていても分母には入らず、"
    "not_a_settings_layer として settings_denominator に出る — そこに書いた entry は"
    "効いていないので、promote 候補の反証には使えない。"
    "**例外**: cwd が config dir の親 ($HOME) のときだけ同 path が local scope の"
    "解決先になり実際に効く。その環境では project_local として分母に入り、"
    "not_a_settings_layer の行は出ない。",
    "hard_deny_share (derived view / rule_candidates の inputs) は**観測窓全体の平均**。"
    "窓内で挙動が変わった entry では変更前後を混ぜた値になり entry の性質を表さないので、"
    "比率を根拠にする前に axis_a_high_deny_share の window_split を見る "
    "(shifted true = 窓全体の比率で診断しない / null = 判定不能で一様の証明ではない)。"
    "**rule_candidates の rule_inputs は view の収載条件に届かない entry にも "
    "hard_deny_share を載せる** (near-miss 行を含む) ので、view に該当行が無ければ "
    "part axis_a の outcome_breakdown_early / _late を直接見る。",
    "観測の劣化は成果物ではなく返り値の `degradation` が持つ。`broken_lines` / `undecodable_files` / "
    "`unreadable_files` / `unreadable_dirs` は観測の欠け、`unrecognized_nested_files` は置き場の判定に"
    "当たらず読まなかった file の数で、transcript ならその分の観測が欠けている。`ts_missing_records` は"
    "時刻を持たない record の数で、この mart はその実行を保守的に窓内へ倒すので、窓の外の実行が混ざりうる。"
    "窓内へ倒した実行は前後半の内訳 early / late のどちらにも入らない。件数は tool 実行に限らず record の"
    "数で、窓内へ倒した実行の数とは一致しない (0 なら倒した実行は無い)。劣化は mart と同じ母集団 "
    "(meta.transcript_kind) で数える — 既定 main の mart では subagent の transcript の劣化を含まない。"
    "record の重複は取り除かない (再開で同じ record が別の file へ写る) ので、件数を水増ししうる。",
    "母集団 (実行・hook の fire とも) は meta.transcript_kind が選ぶ。**既定 main は "
    "main session の transcript だけを数え、subagent の tool 呼び出しを含まない** — "
    "subagent の中でだけ使われた entry は match_count 0 に見えるので、revoke 判定の前に "
    "transcript_kind: all で取り直して確かめられる。all で数えた subagent の実行は "
    "親 session の id で数える (subagent ごとの session は立たない)。",
)


RULE_CATALOG = [{'rule_id': 'revoke_candidate',
  'unit_kind': 'permission_entry',
  'scope': 'category が allow / ask の設定 entry',
  'bucket_candidate': 'revoke-pending',
  'conditions': [{'name': 'relative_judgment_available',
                  'predicate': '当該 section の event 数が sufficient_threshold 以上 (個別 0 件を「使われていない」と読める母数がある)',
                  'report_near_miss': True},
                 {'name': 'zero_match', 'predicate': '観測窓内の match_count が 0', 'report_near_miss': False},
                 {'name': 'matcher_exact',
                  'predicate': 'matcher_confidence が exact。approx (glob の fnmatch 近似) は ~ 展開と ** 意味論を本体 '
                               'matcher どおりに再現しないため、発火中の entry でも 0 が出る。unmatchable は照合対象そのものが観測に無く、0 '
                               'が不使用の証拠になりえない (理由は rule_inputs の unmatchable_reason)',
                  'report_near_miss': True},
                 {'name': 'no_sandbox_pair',
                  'predicate': '同一 (tool, pattern) の sandbox.excludedCommands entry が無い (あれば連動削除の判断が要る)',
                  'report_near_miss': True}],
  'open_predicates': [{'name': 'side_effect_capability',
                       'predicate': '副作用能力 (remote 書き込み / process 起動 / 破壊的操作) を持つか。read-only で無害な未使用 entry '
                                    'は削除しても attack surface が減らず、permission ask の反復コストだけが増える'},
                      {'name': 'exposure_opportunity',
                       'predicate': '観測窓内に当該 capability を使う機会が実在したか (窓内追加 / rare-by-design '
                                    'なら露出不足であって不使用ではない)'},
                      {'name': 'alias_still_in_use',
                       'predicate': '同じ capability が別名 (改名後の tool / MCP server) で現役でないか。現役なら revoke ではなく '
                                    'pattern の書き換え'},
                      {'name': 'invocation_form_pair',
                       'predicate': '同一 script の複数呼び出し形が 1 unit を成していないか (片側だけの削除は pair 規約を壊す)'}],
  'thresholds': {'sufficient_threshold': DEFAULT_SUFFICIENT_THRESHOLD}},
 {'rule_id': 'compound_line_deny_miscount',
  'unit_kind': 'permission_entry',
  'scope': 'hard deny 比率が高い設定 entry (derived view の axis_a_high_deny_share と同条件)',
  'bucket_candidate': 'refine-pending',
  'conditions': [{'name': 'high_deny_share',
                  'predicate': f'match_count >= {HIGH_DENY_MIN_MATCH} かつ hard deny (permission-rule + automode) 比率 >= '
                               f'{HIGH_DENY_MIN_RATIO}',
                  'report_near_miss': False},
                 {'name': 'compound_line_deny_present',
                  'predicate': 'その deny のうち複合コマンド行 (shell 連結演算子を含む入力) 由来が1 件以上ある',
                  'report_near_miss': True}],
  'open_predicates': [{'name': 'deny_attributable_to_entry',
                       'predicate': 'その deny が entry 自身の pattern 由来か、同一行の別コマンド由来か。後者なら refine 対象は entry '
                                    'ではなく複合行の組み立て方で、entry 変更は不要'}],
  'thresholds': {'high_deny_min_match': HIGH_DENY_MIN_MATCH, 'high_deny_min_ratio': HIGH_DENY_MIN_RATIO}}]


CONTRACT = {
    "schema_version": SCHEMA_VERSION,
    "parts": [dict(part) for part in PARTS],
    "views": dict(DERIVED_VIEW_SEMANTICS),
    "rules": RULE_CATALOG,
    "notes": list(META_NOTES),
}
