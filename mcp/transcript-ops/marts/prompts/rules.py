"""prompts slice の決定的ルール ([ADR 0032](../../../../docs/adr/0032-policy-free-refinement-deterministic-rules.md))。

**本 mart で決定的ルールを持つのは engineering-values (全 repo 横断) の採用 gate
1 本だけ**。`inventory-project-values` (cwd repo 単独) は SKILL.md 自身が
「決定的シグナルが存在しない」と明文化しており、ADR 0032 の対象外 — よって
`repo_scope != "all"` の slice では rule を**評価しない** (scope 外)。

対象は `boilerplate_forms` (正規化後にバイト一致する群) だけ。**同一規範の束ね**は
LLM の役割で、束ねた候補の repo 数は機械が数えられない (束ねが確定していないため) —
だから gate が機械化できるのは repo 数が既に決定的に出ている定型一覧だけになる。

## 禁止事項 — 意味判断の allowlist 化

**「起動文・依頼テンプレートの除外」を文面パターンの一覧として本 module に
落とさないこと。** 定型判定はハードコードした文面パターンも類似度計算も持たない
設計 (正規化後のバイト一致だけ) であり、除外の文面リストを持ち込むと同じ腐り方を
する。規範として読めるかどうかは `open_predicates` に留める。

本 module は純関数のみ (I/O・clock・store アクセスを持たない)。
"""

from __future__ import annotations

from marts.rulespec import Condition, Evaluation, RuleSpec, catalog, verdict

# 採用 gate の閾値。**姉妹 skill 間の閾値一致がここで構造保証になる** — 以前は
# `inventory-engineering-values` の SKILL.md 本文に散文で書かれており、tool 側の
# 定数と手で同期する二重管理だった (ADR 0032 Context)。
MIN_REPO_COUNT = 2

# rule を評価する slice の repo scope。
APPLICABLE_REPO_SCOPE = "all"

CROSS_REPO_RECURRENCE = RuleSpec(
    rule_id="cross_repo_recurrence",
    unit_kind="boilerplate_form",
    scope=f"repo_scope が {APPLICABLE_REPO_SCOPE} の slice の boilerplate_forms 全件",
    bucket_candidate="cross-repo-norm-candidate",
    conditions=(
        Condition("multi_repo",
                  f"正規化後の distinct repo 数が {MIN_REPO_COUNT} 以上 "
                  "(repo をまたぐ再出現 = 汎用性の証拠)"),
    ),
    open_predicates=(
        Condition("norm_readable",
                  "逐語反復された文から規範が読み取れるか。起動文・依頼テンプレート"
                  "(issue 実行の起動文等) は反復されるが規範ではない"),
        Condition("paste_boundary",
                  "貼り付け境界より前の人間記述だけで規範が復元できるか "
                  "(貼り戻された assistant 応答を規範として帰属させない)"),
        Condition("container_classification",
                  "器が coding-principles / engineering-judgment のどちらか、"
                  "あるいは決まらないか"),
    ),
    thresholds={"min_repo_count": MIN_REPO_COUNT},
)

RULES = (CROSS_REPO_RECURRENCE,)


def rule_catalog() -> list[dict]:
    """slice の contract に emit する rule カタログ (SKILL.md はこれを参照する)。"""
    return catalog(RULES)


def evaluate(boilerplate_forms: list[dict], repo_scope: str) -> list[dict]:
    """定型正規形ごとの候補 / near-miss を出す (**bucket は確定しない**)。

    `repo_scope` が `all` でなければ空を返す — cwd repo 単独の slice では
    repo 数が構造的に 1 以下で、gate が「常に不採用」を言うだけになるため。
    """
    if repo_scope != APPLICABLE_REPO_SCOPE:
        return []
    out: list[dict] = []
    for form in boilerplate_forms:
        repo_count = form["repo_count"]
        row_verdict = verdict(form["normalized"], [Evaluation(
            spec=CROSS_REPO_RECURRENCE,
            checks={"multi_repo": repo_count >= MIN_REPO_COUNT},
            inputs={
                "repo_count": repo_count,
                "repos": list(form.get("repos") or []),
                "occurrences": form["count"],
            },
        )])
        if row_verdict is not None:
            out.append(row_verdict)
    return out
