"""permissions mart の機械可読 contract (mart schema 知識の単一ソース)。

`00-meta.json` に埋め込んで LLM 段階へ渡す。**SKILL.md も tool docstring も本
contract を参照し、schema と読み方を再エンコードしない** (二重管理の廃止。
docstring は全利用セッションの context に常駐するため、注記の置き場はここ)。

ここに在る閾値は sort / filter のパラメータで、**bucket を確定しない**。決定的
ルールの評価結果 (候補 / 導出過程 / 未判定条件) は `rules.py` が出し、その rule
カタログも本 contract が emit する ([ADR 0032](../../../../docs/adr/0032-policy-free-refinement-deterministic-rules.md))。
bucket (revoke / promote / refine / sandbox / keep) の確定は本 server の責務外 —
観測・集計は決定的に、判断は人間に (ADR 0011 の 3 層分離)。
"""

from __future__ import annotations

# 読み手側の将来分岐用に単調増加させる。v2 で mtime gate 撤廃 + store 由来の
# 観測メタ (`store`) を meta に追加。v3 で rule 層 (`15-rule-candidates.json` +
# contract.rules) を追加し `90-mart.json` を retire した (ADR 0031 / 0032)。
# v4 で B 軸 unit に `global_config_matches` を追加した (#513: promote の証拠源が
# 「section の config に無い」から「どの層の config にも allow が無い」へ変わる)。
# v5 で `matched_by: null` の hook unit の `fire_count` を 0 でなく null (観測不能) に
# し、totals の `never_fired_units` を `unobserved_units` へ改名した (#514: 無出力で
# pass する hook は attachment を残さず、0 は「発火なし」を主張できない)。
# v6 で分母の観測可能性を meta に足した (#583: `settings_denominator` = 読んだ path /
# 存在するのに読まなかった path を reason 付きで列挙、symlink は解決前後の両表記)。
# section の `settings_sources` も entry からの逆算をやめ、**列挙した path から**
# 組むようにした (0 件の層と読まなかった層が同じ「行が無い」に潰れていた)。
# 併せて project 層に worktree の親 clone 側 `settings.local.json`
# (`scope: project_local_main_clone`) を足した (worktree セッションでは実際に読まれる)。
# v7 で観測窓の変化点フラグを足した (#584: A 軸 entry に前半 / 後半別の
# `outcome_breakdown_early` / `outcome_breakdown_late`、`axis_a_high_deny_share` の
# 各 row に `window_split`、meta.observation_window に `midpoint`)。窓内で挙動が
# 変わった entry の `hard_deny_share` は変更前後を混ぜた平均で、entry の性質を表さない。
# v8 で照合不能を可視化した (#873: `matcher_confidence` に `unmatchable` を追加し、
# `WebFetch(domain:…)` を URL の hostname で突合するようにした)。それまで URL は
# store の照合列 (command / target_path) のどちらにも入らず、現役の allow entry が
# match_count 0 = revoke 候補として提示されていた。併せて A 軸 entry に
# `unobserved_input_count` (照合対象の列を持たなかったその tool の実行数) を足し、
# 同じ数を revoke_candidate の rule_inputs にも載せた。
# v9 で `Tool(param:value)` 形の param rule を照合不能として扱えるようにした
# (#888: store の `tool_use` に `input_keys` = input の top-level key **名だけ**を
# 足し、A 軸 entry と revoke_candidate の rule_inputs に `unmatchable_reason` を
# 追加)。それまで param rule は照合列が埋まったまま match 0 になり、`exact` の
# まま revoke 候補に出ていた。
# v10 で B 軸の照合を代表 event 1 件から **unit 内の全 event** へ変えた
# (#889: `config_matches` / `global_config_matches` の各要素に `matched_event_count`
# が付き、unit に `uncovered_event_count` / `global_uncovered_event_count` が加わった。
# `axis_b_unlisted_frequent` の収載床と並べ替えも総件数から未被覆件数へ移した)。
# それまで非 Bash tool は全実行が 1 unit に潰れたまま代表 1 件で収載を判定しており、
# 全 event を照合する A 軸と矛盾した像が出ていた。
# v11 で entry の tool 名の**被覆**を完全一致から公式 doc の MCP 規則へ広げた
# (#916: `mcp__foo` は foo server の全 tool を、`mcp__foo__*` は tool 名位置の
# wildcard として同じ集合を指す)。key は増減しないが `match_count` /
# `config_matches` / `uncovered_event_count` と、そこから出る `axis_a_zero_match` /
# `axis_b_unlisted_frequent` / `rule_candidates` の意味が変わる — それまで
# server 単位 / wildcard の entry は**収載済みなのに match 0 = revoke 候補**として
# 出ていた。併せて括弧付きの `mcp__…` entry (本体が rule ごと skip する形) の 0 を
# 明示的な不在として扱い、その entry の `matcher_confidence` は `unmatchable` から
# 宣言値 (`exact` / `approx`) へ、`unmatchable_reason` は `input_column_empty` から
# `null` へ、`unobserved_input_count` は N から 0 へ変わる (この 3 つが揃って初めて
# 「skip される rule は revoke 候補に出る」が成立する)。`unmatchable_reason` の値集合は
# 変えていない — 非 MCP の tool 名 glob (`B*`) は未実装のままで、片側だけ札で補償すると
# A 軸と B 軸が矛盾するため (下の notes を参照)。
SCHEMA_VERSION = 11

DEFAULT_DAYS = 30
DEFAULT_SUFFICIENT_THRESHOLD = 30
BYPASS_LOOKAHEAD = 5
BYPASS_MAX_GAP_SECONDS = 300

# derived_views の集計パラメータ。
DERIVED_TOP_N = 30
HIGH_DENY_MIN_MATCH = 20
HIGH_DENY_MIN_RATIO = 0.3
# `axis_b_unlisted_frequent` の収載床。**数える対象は unit の総件数ではなく
# `uncovered_event_count`** (= 当該 section の config のどの entry にも当たらなかった
# 実行の数。#889)。値そのものは policy なので server は動かさない — 何件の未収載実行が
# あれば収載を検討すべきかの確定は読み手に残す (ADR 0032)。
UNLISTED_MIN_COUNT = 5
FOLLOWUP_FAST_GAP_SECONDS = 10
HARD_DENY_OUTCOMES = ("deny_permission-rule", "deny_automode")

# 観測窓の変化点フラグ (#584)。窓を **midpoint で二分**し、前半 / 後半の hard deny
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

# 分割出力のパラメータ。
SPLIT_SECTION_SUMMARY_KEYS = ("settings_sources", "event_count",
                              "distinct_sessions", "outcome_totals")
BYPASS_SAMPLE_GROUPS = 10
BYPASS_SAMPLES_PER_GROUP = 2

# 分割ファイルの読む順・用途・標準フロー可否の単一ソース。present.split_outputs と
# 00-meta の contract.files が本定数を iterate する (二重管理を廃止)。purpose は
# data 内容の記述のみ — **確定した bucket 語彙は含めない** (server は候補と導出過程
# までを出し、bucket は確定しない。ADR 0032 の出力契約)。
#
# `90-mart.json` (数 MB 級の全量) は retire した (ADR 0031)。想定外の追加検査は
# read-only の `query` tool が担う。
SPLIT_FILES = (
    {"name": "00-meta.json", "order": 0, "standard_flow": True,
     "purpose": "meta + section 概況 (判定可能性の分岐に必要な最小情報) + 本 contract "
                "(files / views / rules / notes)"},
    {"name": "10-derived-views.json", "order": 10, "standard_flow": True,
     "purpose": "derived_views + guard_reverse_lookup"},
    {"name": "15-rule-candidates.json", "order": 15, "standard_flow": True,
     "purpose": "決定的ルールの評価結果 (bucket_candidate / rule_fired / rule_inputs / "
                "open_predicates / near_misses)。**LLM が判断するのは open_predicates "
                "だけ**で、bucket の確定と最終採否は下流に残る"},
    {"name": "20-axis-a.json", "order": 20, "standard_flow": True,
     "purpose": "全設定 entry の両軸集計 (全 entry を含む母集団)"},
    {"name": "30-bypass-samples.json", "order": 30, "standard_flow": True,
     "purpose": "top bypass group の代表系列"},
    {"name": "40-hooks.json", "order": 40, "standard_flow": True,
     "purpose": "hook の設定側分母 × fire 実績 (fire_count null = 未観測 / 遅い / "
                "timeout の観測。observability に観測限界を同梱)"},
)

# derived view の view 名 → 1 行意味論 (00-meta の contract.views に emit)。
# present.build_derived_views 出力の key 集合と一致させる (整合性テストで固定)。
DERIVED_VIEW_SEMANTICS = {
    "axis_a_zero_match": (
        "観測窓内で match_count == 0 の設定 entry。**0 の意味は matcher_confidence で"
        "変わる** — unmatchable の行は「使われていない」ではなく「照合が成立して"
        "いない」で、どの理由で成立していないかは `unmatchable_reason` "
        "(input_column_empty / param_rule) が持つ (notes を参照)。"),
    "axis_a_high_deny_share": (
        f"match_count >= {HIGH_DENY_MIN_MATCH} かつ hard deny "
        f"(permission-rule + automode) 比率 >= {HIGH_DENY_MIN_RATIO} の entry。"
        "user-rejected は #29499 の false positive 影響下のため hard deny に数えない。"
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
        "**突合は unit 内の全 event に対して行う** (代表 1 件の近似ではない。#889)。"
        "**この view に載る unit は定義上すべて未被覆 >= 収載床**なので、"
        "`config_matches` に match がある unit は例外なく**部分被覆** (既存 entry が "
        "unit の一部しか覆っていない) — `uncovered_event_count` の非 0 は判別条件に"
        "使えない。**section project でのみ** `global_uncovered_event_count` が判別に"
        "効く (0 なら global 層では全件が覆われている)。**section global では両者が"
        "同値なので判別材料が無い** — 収載床未満まで含めて被覆を見るなら query tool で "
        "B 軸を直接引く。"
        "**未被覆が床未満の部分被覆 unit はこの view に出ない** (全量 "
        "`axis_b_actual_usage` は分割ファイルに書き出さないので、床未満を見るなら "
        "query tool で B 軸を直接引く)。"
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
    "exit_code_source": "attachment.hook_success のみ (他 3 種は exitCode を持たない)",
    "duration_source": "attachment.hook_success / hook_cancelled の durationMs",
    "failure_confidence": "approx",
    "notes": [
        "nonzero_exit_count は「観測窓内に非 0 終了が記録されなかった」であって"
        "「hook が失敗していない」ではない。実測で hook_success の exitCode は全件 0",
        "timeout による打ち切りは hook_cancelled (timedOut) にしか出ない",
        "system.stop_hook_summary の hookInfos は {command, durationMs} だけで"
        "hookName も exitCode も持たないため、Stop hook の帰属には使えない",
        "無出力で pass する hook は attachment を残さないため観測に残らない。"
        "attribution が付かない unit (matched_by: null) の fire_count は null (観測不能) — "
        "「発火条件を満たす操作が窓内に無かった」と「発火したが観測に残らなかった」を"
        "transcript からは区別できない (fire_count 0 は configured には現れない)",
        "fire_count null (未観測) の判定が成立するのは configured (設定側の分母) に載る "
        "unit だけ。observed_unlisted は fire した attachment からしか作られない",
        "key_collision: true の unit は fire_count を同 key の他 unit と共有する。"
        "「fire していない」は主張できるが「n 回動いた」は主張できない",
        "command を持たない attachment (hook_additional_context / hook_system_message) は"
        "hookName でしか引けず、matcher が `*` の設定には帰属しない (observed_unlisted に残る)",
    ],
}

# mart meta に添える読み方の注記。**tool docstring に書かない** — docstring は全
# 利用セッションの context に常駐するコストを払うが、注記が要るのは mart を読む
# 段階だけなので、mart 自身に同梱する (ADR 0031)。
META_NOTES = (
    "sufficient_for_relative_judgment が false なら相対判定 (未使用の entry を"
    "「使われていない」と読むこと) は成立しない — 観測不足であって不使用の証拠ではない。",
    "outcome の deny_user-rejected は #29499 の false positive バグ影響下 (best-effort な近似)。",
    "matcher の glob pattern は fnmatch による近似 (Claude Code 本体の matcher と揺れる余地あり)。",
    "**matcher_confidence が unmatchable の entry は match_count 0 を「未使用」と"
    "読んではいけない** — 突合が原理的に成立していない状態で、典型は窓内にその tool の"
    "実行が在るのに matcher が読む列 (Bash は command / `WebFetch(domain:…)` は URL の "
    "hostname / それ以外は file path) がどの実行でも空だった場合 "
    "(`unmatchable_reason: input_column_empty`)。"
    "revoke 候補は matcher_exact を条件に持つのでこの entry では発火しない。"
    "15-rule-candidates.json の near_misses に failed_condition: matcher_exact として"
    "落選理由つきで出るのは、**外れた条件がそれ 1 つだけのとき**に限る "
    "(near-miss は「あと 1 条件」の行しか作らない) — "
    "sufficient_for_relative_judgment が false の窓では relative_judgment_available も"
    "同時に外れ、その entry は rule_candidates に 1 行も出ない。**窓が足りないときは "
    "20-axis-a.json の matcher_confidence を直接見る**。"
    "判断するには transcript 側に照合対象を持たせる (store の列を"
    "足す) か、その tool の入力を query tool で直接見る。"
    "**tool の実行自体が窓内に 0 件の entry は unmatchable にならない** — "
    "「使わなかった」は真の観測なので、宣言どおりの exact / approx が残る。",
    "A 軸 entry の `unobserved_input_count` は、matcher が読む列を持たなかったその tool の"
    "実行数。**0 でなければ match_count は確認できた範囲の下限**で、0 件でも「その pattern を"
    "使っていない」とは言い切れない。実測ではこの差の実体は `__unparsedToolInput` "
    "(入力を解釈できず error になった呼び出し = tool が実行されていない) なので、"
    "**revoke_candidate の条件にはしていない** — 何件までなら 0 を不使用の証拠と見なすかは"
    "閾値の policy であり、server は数を出すところまでを担う。rule_inputs にも同じ数が載る。"
    "引数が任意の tool (`Grep` の `path` 等) では正当な省略もこの数に入るので、"
    "revoke 候補を採る前にこの数を見る。"
    "**本体が rule ごと skip する括弧付き `mcp__…` entry では常に 0** "
    "([#916](https://github.com/swat9013/swat-skills/issues/916))。skip される rule に"
    "「下限」は無い (照合が成立していないのではなく rule が存在しない) ので、"
    "match_count 0 は確定値。同じ理由で `matcher_confidence` も宣言値の `approx` ではなく "
    "`exact` になる — 括弧の中身 (`(x)` か `(*)` か) で revoke 候補に出たり出なかったり"
    "しないため。",
    "unmatchable の理由は A 軸 entry と revoke_candidate の rule_inputs の "
    "`unmatchable_reason` が持つ。`input_column_empty` は照合対象の列が空 (上記)、"
    "`param_rule` は `Tool(param:value)` 形 (`Bash(run_in_background:true)` 等) で "
    "**その param 名を渡した実行が窓内に在るのに、本 server が param の値を持たないため"
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
    "公式 doc に根拠が無い本 server の保守的な仮定**で、doc は名前が衝突したときの"
    "優先順位に触れていない。"
    "**param rule の match_count は依然として実績ではない** — 値を突合していないので、"
    "使用実績が要るなら **transcript (lake) の当該 tool_use を直接読む**。"
    "**store からは引けない**: 値を持つ列が無く、`input_excerpt` は 200 字上限で "
    "Bash は `command` が先に直列化されるため、長い command の呼び出しでは param が"
    "切り落とされる (query tool でこれを数えると、実在する呼び出しを 0 件と読む)。",
    "**非 Bash tool の B 軸 unit は引数を鍵に持たない** (unit key は "
    "(tool, command_head) で command_head は Bash 以外では空文字なので、"
    "`Read` や `WebFetch` は引数の別を問わず 1 unit に潰れる)。**それでも収載の"
    "判定は unit 内の全 event で行う** (#889) ので、`Read(**/*.env)` のような引数依存の "
    "entry でも代表 event の当たり外れで像が反転することはない。読み方は 3 つ: "
    "(1) `config_matches[].matched_event_count` がその entry の被覆件数、"
    "(2) `uncovered_event_count` がどの entry にも当たらなかった件数、"
    "(3) 両方が非 0 なら**部分被覆** — unit の一部だけが収載されている。"
    "**これは B 軸の全量 (`axis_b_actual_usage`) を読むときの判別で、"
    "`axis_b_unlisted_frequent` の中では成り立たない** (あの view の収載条件が"
    "未被覆 >= 床なので常に真になる)。view の中での読み方と和の扱いは "
    "`axis_b_unlisted_frequent` の view 意味論が正本 (contract.views)。"
    "**A 軸 (20-axis-a.json) と同じ全 event を照合するので、両軸の値は矛盾しない** — "
    "A 軸の match_count > 0 なら、その entry は B 軸のいずれかの unit で非 0 の "
    "matched_event_count を持つ。ただし**この一致を出力ファイルだけで確かめることは"
    "できない**: B 軸の全量 (`axis_b_actual_usage`) は分割ファイルに書き出さず、"
    "10-derived-views.json に出るのは未被覆が床を超えた unit だけなので、全件収載済みの "
    "unit は出力に現れない。突き合わせるなら query tool で B 軸を直接引く。"
    "**unit 粒度の限界は残る**: どの引数の実行が未収載なのかは B 軸からは分からない "
    "(unit key に引数が無い)。**A 軸の sample_matched では代替できない** — あれは entry に"
    "match した実行だけの (tool, command_head, count) で、非 Bash tool では "
    "command_head が空文字なので引数を 1 文字も持たない。未被覆側の実体が要るなら "
    "query tool で当該 tool の実行を直接見る。"
    "**未被覆には照合不能な実行も混ざる**: matcher が読む列 (Bash は command / "
    "domain は URL の hostname / それ以外は file path) を持たない実行はどの entry にも"
    "当たらないので、必ず未被覆に数えられる。引数が任意の tool (`Grep` の `path` 等) "
    "では正当な省略もここに入る。**A 軸の `matcher_confidence` / "
    "`unmatchable_reason` / `unobserved_input_count` にあたる札を B 軸の unit は"
    "持たず、A 軸で代替もできない** — A 軸の行は config entry を列挙して作るので、"
    "その section の config にその tool の entry が 1 件も無ければ数そのものが出力に"
    "現れない (`config_matches: []` の unit = まさに promote 候補がこれに当たる)。"
    "entry が在る場合も値は entry ごと・match_kind ごとなので、unit 1 つに対して"
    "どの数を引くかが決まらない。**純粋な未収載件数が要るなら query tool で当該 tool の"
    "実行を直接見る** (照合不能件数の列を B 軸 unit に足すのは #889 の範囲外とした)。",
    "**entry の tool 名は完全一致で突合しない** (#916)。公式 doc の MCP 節が定めるとおり、"
    "`mcp__foo` は foo server の全 tool を、`mcp__foo__*` は tool 名位置の wildcard として"
    "同じ集合を被覆する。**A 軸と B 軸は同じ被覆表を通る**ので、server 単位 entry の "
    "match_count と、その server の tool の `config_matches` は同じ関係から出る。"
    "server 単位 entry は `__` 境界を要求する (`mcp__foo` は `mcp__foobar__baz` を"
    "被覆しない)。**非 MCP の tool 名 glob (deny の `*` / `B*`) は未実装**で完全一致の"
    "まま — 当てない側が保守的なので、その 0 は「実装が当てていない」であって"
    "「使っていない」ではない。**allow の錨の無い wildcard** (`mcp__*` 等) は公式 doc が"
    "「skip して warning を出す」と定めるので被覆しない — その 0 は真の観測。",
    "guard_reverse_lookup は transcript の toolDenialKind + Reason label ベース (hooks.json の静的列挙はしない)。",
    "bucket 判定は本 script では行わない (責務境界: 判定は SKILL.md 手順の LLM 段階)。",
    "hook_activity は section (cwd scope) で絞らない — 「30 日どこでも fire していない」"
    "が「fire していない hook」の主張になるため、分母を窓全体に取る。",
    "観測窓は record の timestamp だけで切る (v2 で file mtime による事前除外を撤廃)。"
    "timestamp を持たない record は保守的に窓内へ倒し、件数は store.ts_missing_events に出す。",
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
    "20-axis-a.json の outcome_breakdown_early / _late を直接見る。",
    "store.broken_lines / store.unreadable_files / store.skipped_nested_files が 0 でなければ"
    "観測が劣化している (件数だけ出す。判断は読み手)。",
)


def build_contract(rule_catalog: list[dict] | None = None) -> dict:
    """`00-meta.json` に埋め込む contract。

    分割ファイルの読む順・用途 (SPLIT_FILES)・derived view の意味論
    (DERIVED_VIEW_SEMANTICS)・rule カタログ・読み方の注記 (META_NOTES) を
    **tool 発の契約**として LLM 段階へ渡す。SKILL.md はこれを参照して再記述しない。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "files": [dict(entry) for entry in SPLIT_FILES],
        "views": dict(DERIVED_VIEW_SEMANTICS),
        "rules": list(rule_catalog or []),
        "notes": list(META_NOTES),
    }
