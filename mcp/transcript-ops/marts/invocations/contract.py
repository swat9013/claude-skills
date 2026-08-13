"""invocations mart の機械可読 contract (mart schema 知識の単一ソース)。

mart の `contract` key に埋め込んで LLM 段階へ渡す。**SKILL.md も tool docstring も
本 contract を参照し、読み方を再エンコードしない** — docstring は全利用セッションの
context に常駐するコストを払うが、注記が要るのは mart を読む段階だけなので、置き場は
mart 自身 ([ADR 0031](../../../../docs/adr/0031-transcript-store-elt.md))。

閾値は判定可能性の分岐と抜粋件数のパラメータで、**bucket を確定しない**。決定的
ルールの評価は `rules.py` (ADR 0032)。
"""

from __future__ import annotations

# 読み手側の将来分岐用に単調増加させる。v1 で rule 層 + contract を追加 (ADR 0032)。
SCHEMA_VERSION = 1

# mart meta に添える読み方の注記。
META_NOTES = (
    "skill unit の count は 3 channel (Skill tool_use / slash command / SKILL.md への "
    "Read) の合算で、内訳は units.skill[].channels に出る。",
    "分母に無い id は source: session-observed で補完されるが、これは**観測分の下限"
    "保証**であって install 済み一覧ではない (claude.ai connectors 等はローカル "
    "config に出ない)。",
    "presented.units の sessions_presented > 0 かつ sessions_invoked == 0 が"
    "「載っているのに使われていない」の証拠。sessions_presented == 0 は"
    "「そもそも提示されていない」で、不使用の証拠にはならない。",
    "usage.by_skill は attributionSkill が付いた turn だけの合計 (下限)。"
    "「この skill が消費させた総量」とは読まない。",
    "sufficient_for_relative_judgment が false なら相対判定は成立しない — "
    "観測不足であって不使用の証拠ではない。",
)


def build_contract(rule_catalog: list[dict]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "rules": list(rule_catalog),
        "notes": list(META_NOTES),
    }
