"""決定的ルールの**形**だけを持つ共有 module ([ADR 0032](../../../docs/adr/0032-policy-free-refinement-deterministic-rules.md))。

ここに在るのは出力契約の器 (RuleSpec / Condition / `verdict`) だけで、**述語も閾値も
domain 知識も持たない**。ADR 0032 は汎用 rule engine を明示的に却下している —
決定的ルールが実在するのは 5〜6 domain 中 3 系統だけで、一様性が成立しないため。
engine 化すると dict-of-any の型を強い、domain 知識が engine へ漏れる。**本 module に
述語や閾値を引き上げないこと。**

## 出力契約 (循環依存の防波堤)

server は **bucket を確定しない**。`verdict()` が組む 1 unit 分の dict:

| key | 意味 |
|---|---|
| `unit` | 判定対象の識別子 (entry / unit id / 正規形) |
| `bucket_candidate` | 発火した rule が提案する候補ラベル。**確定ではない** |
| `rule_fired` | 全条件を満たした rule の id (人間が導出過程を検査する) |
| `rule_inputs` | 条件評価に使った値 (候補のラベルではなく数字を見せる) |
| `open_predicates` | 機械が決められなかった条件。**LLM はここだけを判断する** |
| `near_misses` | 条件を 1 つだけ満たさなかった rule と落選理由 (閾値の当否の唯一の証拠) |

`open_predicates` が意味を持つのは「意味判断を要する述語を機械が確定させる経路が
構造的に存在しない」ことで、rule が発火しても bucket は開いたままになる。
"""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping

# near-miss とみなす「あと 1 条件」の距離。1 より大きくすると落選理由が絞れず、
# near_misses が母集団の写しになって閾値検査の証拠にならない。
NEAR_MISS_DISTANCE = 1


@dataclasses.dataclass(frozen=True)
class Condition:
    """rule を構成する 1 条件。`name` が `failed_condition` としてそのまま出る。

    `report_near_miss` は「この条件だけが外れた unit を落選理由付きで出すか」。
    **母集団を絞る条件 (「使われていない」「deny 比率が高い」等) では False にする** —
    そこを外した unit は候補の裏返し (= 母集団そのもの) であり、全件を near_misses に
    載せると閾値検査の証拠が母集団の写しに埋もれる。True にする値打ちがあるのは、
    外れた理由が**閾値・近似・連動といった見直しうる前提**に帰属する条件だけ。
    """

    name: str
    predicate: str
    report_near_miss: bool = True


@dataclasses.dataclass(frozen=True)
class RuleSpec:
    """1 rule の定義。**形 (id・条件名・入力名) は凍結し、閾値の値は凍結しない。**

    値まで凍結すると閾値調整のたびに schema break になる (ADR 0032 Consequences)。
    テストが assert してよいのは rule_id / conditions の name / inputs / open_predicates
    までで、`thresholds` の値は対象外。
    """

    rule_id: str
    unit_kind: str
    scope: str
    bucket_candidate: str
    conditions: tuple[Condition, ...]
    open_predicates: tuple[Condition, ...] = ()
    thresholds: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def as_catalog_entry(self) -> dict:
        """mart meta / contract に emit する rule カタログの 1 行。

        SKILL.md はこれを参照して**再記述しない** — 述語文の二重管理を廃すのが
        カタログを emit する目的そのもの。
        """
        return {
            "rule_id": self.rule_id,
            "unit_kind": self.unit_kind,
            "scope": self.scope,
            "bucket_candidate": self.bucket_candidate,
            "conditions": [{"name": c.name, "predicate": c.predicate,
                            "report_near_miss": c.report_near_miss}
                           for c in self.conditions],
            "open_predicates": [{"name": c.name, "predicate": c.predicate}
                                for c in self.open_predicates],
            "thresholds": dict(self.thresholds),
        }


def catalog(specs: tuple[RuleSpec, ...]) -> list[dict]:
    return [spec.as_catalog_entry() for spec in specs]


@dataclasses.dataclass(frozen=True)
class Evaluation:
    """1 unit × 1 rule の評価結果。`checks` の key は spec の条件名と一致させる。"""

    spec: RuleSpec
    checks: Mapping[str, bool]
    inputs: Mapping[str, Any]


def verdict(unit: str, evaluations: list[Evaluation]) -> dict | None:
    """1 unit 分の出力契約を組む。発火も near-miss も無ければ None (行を作らない)。

    `bucket_candidate` は最初に発火した rule のもの。複数 rule が同じ unit で
    発火する構成は今のところ無く、増やすなら候補ラベルの優先順を先に決めること
    (server が 2 つの候補を並べると、どちらが提案なのか読み手に伝わらない)。
    """
    fired: list[RuleSpec] = []
    near_misses: list[dict] = []
    inputs: dict[str, Any] = {}
    for evaluation in evaluations:
        spec = evaluation.spec
        missing = [condition for condition in spec.conditions
                   if not evaluation.checks.get(condition.name, False)]
        inputs.update(evaluation.inputs)
        if not missing:
            fired.append(spec)
        elif len(missing) <= NEAR_MISS_DISTANCE and missing[0].report_near_miss:
            near_misses.append({
                "rule_id": spec.rule_id,
                "failed_condition": missing[0].name,
                "inputs": dict(evaluation.inputs),
            })
    if not fired and not near_misses:
        return None
    open_predicates = [condition.name
                       for spec in fired for condition in spec.open_predicates]
    return {
        "unit": unit,
        "bucket_candidate": fired[0].bucket_candidate if fired else None,
        "rule_fired": [spec.rule_id for spec in fired],
        "rule_inputs": inputs,
        "open_predicates": open_predicates,
        "near_misses": near_misses,
    }
