"""permissions mart の決定的ルール ([ADR 0032](../../../../docs/adr/0032-policy-free-refinement-deterministic-rules.md))。

移行判定基準: **mart の列だけで評価でき、自然言語の解釈を要さない述語だけ**が
ここに来る。要するものは `open_predicates` として名前だけを出し、判断は LLM へ、
最終採否は人間へ残す。**server は bucket を確定しない** — 出力契約の器は
`marts.rulespec` を参照。

## 禁止事項 — 意味判断の allowlist 化

**「副作用能力あり」「rare-by-design」を危険コマンド一覧・除外 entry 一覧として
本 module に落とさないこと。** 陳腐化した allowlist は無いより悪い (存在するだけで
検査済みに見え、実際には新しい tool / 改名を取りこぼす)。この 2 述語は本 module の
将来の変更者が最も移したくなる箇所であり、移した瞬間に ADR 0032 が却下した
「bucket 確定まで server が行う」案になる。両者は `open_predicates` に留める。

本 module は純関数のみ (I/O・clock・store アクセスを持たない)。
"""

from __future__ import annotations

from marts.rulespec import Condition, Evaluation, RuleSpec, catalog, verdict

from . import contract, settings as settings_mod

# revoke 候補の対象カテゴリ。deny entry の未使用は「守れている」ことの証拠でもあり、
# 削除候補の意味が反転するため対象にしない。
REVOCABLE_CATEGORIES = ("allow", "ask")


def _revoke_candidate(sufficient_threshold: int) -> RuleSpec:
    """revoke 候補の rule。閾値は request で動くため spec も request 時に組む。"""
    return RuleSpec(
        rule_id="revoke_candidate",
        unit_kind="permission_entry",
        scope=f"category が {' / '.join(REVOCABLE_CATEGORIES)} の設定 entry",
        bucket_candidate="revoke-pending",
        conditions=(
            Condition("relative_judgment_available",
                      "観測窓の総 event 数が sufficient_threshold 以上 "
                      "(個別 0 件を「使われていない」と読める母数がある)"),
            # 母集団を絞る条件。外れた entry (= 使われている entry) は候補の裏返し
            # なので落選理由として出さない
            Condition("zero_match", "観測窓内の match_count が 0",
                      report_near_miss=False),
            Condition("matcher_exact",
                      "matcher_confidence が exact。approx (glob の fnmatch 近似) は "
                      "~ 展開と ** 意味論を本体 matcher どおりに再現しないため、"
                      "発火中の entry でも 0 が出る"),
            Condition("no_sandbox_pair",
                      "同一 (tool, pattern) の sandbox.excludedCommands entry が無い "
                      "(あれば連動削除の判断が要る)"),
        ),
        open_predicates=(
            Condition("side_effect_capability",
                      "副作用能力 (remote 書き込み / process 起動 / 破壊的操作) を持つか。"
                      "read-only で無害な未使用 entry は削除しても attack surface が"
                      "減らず、permission ask の反復コストだけが増える"),
            Condition("exposure_opportunity",
                      "観測窓内に当該 capability を使う機会が実在したか "
                      "(窓内追加 / rare-by-design なら露出不足であって不使用ではない)"),
            Condition("alias_still_in_use",
                      "同じ capability が別名 (改名後の tool / MCP server) で"
                      "現役でないか。現役なら revoke ではなく pattern の書き換え"),
            Condition("invocation_form_pair",
                      "同一 script の複数呼び出し形が 1 unit を成していないか "
                      "(片側だけの削除は pair 規約を壊す)"),
        ),
        thresholds={"sufficient_threshold": sufficient_threshold},
    )


COMPOUND_LINE_DENY = RuleSpec(
    rule_id="compound_line_deny_miscount",
    unit_kind="permission_entry",
    scope="hard deny 比率が高い設定 entry (derived view の axis_a_high_deny_share と同条件)",
    bucket_candidate="refine-pending",
    conditions=(
        # 母集団を絞る条件 (deny 比率が低い entry は「疑う対象」ではない)
        Condition("high_deny_share",
                  f"match_count >= {contract.HIGH_DENY_MIN_MATCH} かつ hard deny "
                  f"(permission-rule + automode) 比率 >= {contract.HIGH_DENY_MIN_RATIO}",
                  report_near_miss=False),
        Condition("compound_line_deny_present",
                  "その deny のうち複合コマンド行 (shell 連結演算子を含む入力) 由来が"
                  "1 件以上ある"),
    ),
    open_predicates=(
        Condition("deny_attributable_to_entry",
                  "その deny が entry 自身の pattern 由来か、同一行の別コマンド由来か。"
                  "後者なら refine 対象は entry ではなく複合行の組み立て方で、"
                  "entry 変更は不要"),
    ),
    thresholds={
        "high_deny_min_match": contract.HIGH_DENY_MIN_MATCH,
        "high_deny_min_ratio": contract.HIGH_DENY_MIN_RATIO,
    },
)


def rules(sufficient_threshold: int) -> tuple[RuleSpec, ...]:
    return (_revoke_candidate(sufficient_threshold), COMPOUND_LINE_DENY)


def rule_catalog(sufficient_threshold: int) -> list[dict]:
    """mart の contract に emit する rule カタログ (SKILL.md はこれを参照する)。"""
    return catalog(rules(sufficient_threshold))


def sandbox_pair_keys(
        entries: list[settings_mod.PermissionEntry]) -> set[tuple[str, str]]:
    """`sandbox.excludedCommands` 由来 entry の (tool, pattern) 集合。

    settings 側は `gh:*` のような素の command pattern で書かれ、mart 側では
    `Bash(<raw>)` として parse 済み (`settings.read_permission_entries`)。よって
    allow entry `Bash(gh:*)` と同じ (tool, pattern) で突き合わせられる。
    """
    return {(entry.tool, entry.pattern) for entry in entries
            if entry.category == settings_mod.SANDBOX_CATEGORY}


def evaluate(axis_a: list[dict], entries: list[settings_mod.PermissionEntry],
             sufficient: bool, sufficient_threshold: int,
             total_events: int) -> list[dict]:
    """設定 entry ごとの候補 / near-miss を出す (**bucket は確定しない**)。

    near-miss が出るのは「あと 1 条件」で外れた entry だけ。閾値が誤っているときの
    証拠はここにしか出ないので、母集団全体を near-miss にはしない。
    """
    revoke_spec = _revoke_candidate(sufficient_threshold)
    pairs = sandbox_pair_keys(entries)
    parsed = {(entry.raw, entry.category): (entry.tool, entry.pattern)
              for entry in entries}
    out: list[dict] = []
    for row in axis_a:
        evaluations: list[Evaluation] = []
        match_count = row["match_count"]
        hard_deny = sum(row["outcome_breakdown"].get(key, 0)
                        for key in contract.HARD_DENY_OUTCOMES)
        deny_share = (hard_deny / match_count) if match_count else 0.0
        compound_deny = row.get("compound_command_deny_count", 0)

        if row["category"] in REVOCABLE_CATEGORIES:
            key = parsed.get((row["entry"], row["category"]), ("", ""))
            evaluations.append(Evaluation(
                spec=revoke_spec,
                checks={
                    "relative_judgment_available": sufficient,
                    "zero_match": match_count == 0,
                    "matcher_exact": row["matcher_confidence"] == "exact",
                    "no_sandbox_pair": key not in pairs,
                },
                inputs={
                    "match_count": match_count,
                    "matcher_confidence": row["matcher_confidence"],
                    "category": row["category"],
                    "total_events": total_events,
                    "sandbox_pair": key in pairs,
                },
            ))

        evaluations.append(Evaluation(
            spec=COMPOUND_LINE_DENY,
            checks={
                "high_deny_share": (
                    match_count >= contract.HIGH_DENY_MIN_MATCH
                    and deny_share >= contract.HIGH_DENY_MIN_RATIO),
                "compound_line_deny_present": compound_deny > 0,
            },
            inputs={
                "hard_deny_count": hard_deny,
                "hard_deny_share": round(deny_share, 2),
                "compound_command_deny_count": compound_deny,
            },
        ))

        row_verdict = verdict(row["entry"], evaluations)
        if row_verdict is not None:
            out.append({**row_verdict, "category": row["category"],
                        "scope": row["scope"], "source_path": row["source_path"]})
    return out
