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
SCHEMA_VERSION = 7

DEFAULT_DAYS = 30
DEFAULT_SUFFICIENT_THRESHOLD = 30
BYPASS_LOOKAHEAD = 5
BYPASS_MAX_GAP_SECONDS = 300

# derived_views の集計パラメータ。
DERIVED_TOP_N = 30
HIGH_DENY_MIN_MATCH = 20
HIGH_DENY_MIN_RATIO = 0.3
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
    "axis_a_zero_match": "観測窓内で match_count == 0 の設定 entry。",
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
        f"config 未収載かつ count >= {UNLISTED_MIN_COUNT} の permission 関連 unit "
        "(Bash / mcp__* / deny_permission-rule 実績あり)。permission entry でゲート"
        "されない built-in tool は units から除外し omitted_non_permission_units に件数計上。"
        "未収載の判定は **section の config だけ**を見る (project section なら project + "
        "project_local + worktree なら親 clone の project_local_main_clone。global 層は "
        "`~/.claude/settings.json` 1 本で、`<config dir>/settings.local.json` は "
        "Claude Code が読まない層なので分母に入れない)。"
        "`config_matches: []` は「どこにも収載されていない」ではないので、"
        "global 層の match は units から除かず `global_config_matches` "
        "(entry / category / scope) に別列で出す — section global では config_matches と"
        "同一集合。両列とも突合は各 unit の代表 event 1 件に対する近似。"
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
    "guard_reverse_lookup は transcript の toolDenialKind + Reason label ベース (hooks.json の静的列挙はしない)。",
    "bucket 判定は本 script では行わない (責務境界: 判定は SKILL.md 手順の LLM 段階)。",
    "hook_activity は section (cwd scope) で絞らない — 「30 日どこでも fire していない」"
    "が「fire していない hook」の主張になるため、分母を窓全体に取る。",
    "観測窓は record の timestamp だけで切る (v2 で file mtime による事前除外を撤廃)。"
    "timestamp を持たない record は保守的に窓内へ倒し、件数は store.ts_missing_events に出す。",
    "meta.settings_denominator が分母 path の全量。read: false の行が「存在するのに"
    "読まなかった層」で、reason (out_of_section / absent / unparsed / cross_layer_match / "
    "not_a_settings_layer) が理由を示す。**`config_matches: []` を読む前にここを見る** — "
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
