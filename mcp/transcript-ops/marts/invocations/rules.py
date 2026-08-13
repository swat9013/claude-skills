"""invocations mart の決定的ルール ([ADR 0032](../../../../docs/adr/0032-policy-free-refinement-deterministic-rules.md))。

移行判定基準: **mart の列だけで評価でき、自然言語の解釈を要さない述語だけ**が
ここに来る。**server は bucket を確定しない** — 出すのは候補 (`bucket_candidate`)
と導出過程 (`rule_fired` / `rule_inputs`) と未判定条件 (`open_predicates`) までで、
確定は LLM 段階、最終採否は人間。出力契約の器は `marts.rulespec`。

## 禁止事項 — 意味判断の allowlist 化

**「思想系 skill か」「rare-by-design か」を skill 名の一覧として本 module に
落とさないこと。** どの skill を「1 session 1 load 型」として coverage 補正の
対象にするかは内容判断で、一覧に固定した瞬間に新設 skill を取りこぼす。同様に
「rename / 削除が観測窓をまたいだか」も git 履歴を要する判断であり、
`open_predicates` に留める。

本 module は純関数のみ (I/O・clock・store アクセスを持たない)。
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Callable

from marts.rulespec import Condition, Evaluation, RuleSpec, catalog, verdict

# unit 型 → 候補ラベル。**確定した bucket ではなく候補** (`-pending` を外すのは
# LLM 段階の判断 + 人間の採否)。
BUCKET_CANDIDATES = {
    "skill": "delete-candidate-pending",
    "mcp_server": "disconnect-candidate-pending",
    "plugin": "uninstall-candidate-pending",
}

_CONDITIONS = (
    Condition("relative_judgment_available",
              "総 invocation 数が sufficient_threshold 以上 "
              "(個別 0 件を「使われていない」と読める母数がある)"),
    # 母集団を絞る条件。呼ばれている unit は候補の裏返しなので落選理由に出さない
    Condition("zero_invocation", "観測窓内の count が 0",
              report_near_miss=False),
    Condition("presented_at_least_once",
              "その session に実際に提示されていた (sessions_presented >= 1)。"
              "提示されていない unit の 0 件は環境差 (plugin 無効化等) であって"
              "不使用ではない"),
    Condition("denominator_source_config",
              "分母 source が config (session-observed 補完でなく "
              "ローカル config から確実に列挙されたもの)"),
)

# plugin 単位にだけ足す条件。**skill / mcp_server には効かせない** — 個々の skill の
# 削除は plugin ごと外す操作ではないため、同 plugin の他 unit が使われていることは
# その skill を残す理由にならない。逆に plugin の uninstall は配下を丸ごと巻き込む
# ので、配下 skill と同梱 MCP tool の**両方**が 0 件のときだけ候補になる。
_NO_SIBLING_USAGE = Condition(
    "no_sibling_usage",
    "配下 skill と同梱 MCP tool の invocation がいずれも 0 件 "
    "(1 つでも使われていれば uninstall は依存を巻き込む)")

_OPEN_PREDICATES = (
    Condition("rename_or_removal_in_window",
              "観測窓が unit の rename / 削除日をまたいでいないか "
              "(またいでいれば旧名の 0 件は正常であり参照元の修正も要らない)"),
    Condition("denominator_completeness",
              "ローカル config に出ない提供元 (claude.ai connectors 等) を"
              "分母へ補完したか"),
)

# plugin 同梱の unit は単体では外せないことがある (第三者 plugin の skill は
# `/plugin` 操作でしか触れない)。**機械では決められない**ので open predicate。
_REMOVABLE_INDEPENDENTLY = Condition(
    "removable_independently",
    "その unit を単体で外せるか (第三者 plugin 同梱の skill は plugin 操作でしか"
    "触れず、削除が他 unit を巻き込む)")


def _spec(unit_kind: str, sufficient_threshold: int) -> RuleSpec:
    plugin_scoped = unit_kind == "plugin"
    return RuleSpec(
        rule_id=f"unused_{unit_kind}",
        unit_kind=unit_kind,
        scope=f"denominators に列挙された {unit_kind} 全件",
        bucket_candidate=BUCKET_CANDIDATES[unit_kind],
        conditions=_CONDITIONS + ((_NO_SIBLING_USAGE,) if plugin_scoped else ()),
        open_predicates=_OPEN_PREDICATES + (
            () if plugin_scoped else (_REMOVABLE_INDEPENDENTLY,)),
        thresholds={"sufficient_threshold": sufficient_threshold},
    )


def rules(sufficient_threshold: int) -> tuple[RuleSpec, ...]:
    return tuple(_spec(kind, sufficient_threshold) for kind in BUCKET_CANDIDATES)


def rule_catalog(sufficient_threshold: int) -> list[dict]:
    """mart の contract に emit する rule カタログ (SKILL.md はこれを参照する)。"""
    return catalog(rules(sufficient_threshold))


def plugin_of_skill(skill_id: str, known_plugin_ids: Collection[str]) -> str | None:
    """skill id の namespace が登録済み plugin ならその plugin 名。

    prefix を分母の plugin 一覧と照合するのは、`:` を含むだけの誤 invoke
    (`select:Bash` / `plugin:bash`) から存在しない plugin が分母に生えるのを防ぐため
    (#508 f)。personal / project skill は namespace を持たないのでここで None になる。
    """
    prefix, sep, _ = skill_id.partition(":")
    return prefix if sep and prefix in known_plugin_ids else None


def mcp_tool_belongs_to(tool_id: str, plugin_id: str) -> bool:
    """MCP tool 名が指定 plugin 同梱 server のものか。

    plugin 同梱 server の tool は `mcp__plugin_<plugin>_<server>__<tool>` という
    命名になる。**命名規約への依存**であり、規約から外れる server は巻き込み検査に
    載らない (その分は `open_predicates` の denominator_completeness が受ける)。
    """
    return tool_id.startswith(f"mcp__plugin_{plugin_id}_")


def evaluate(units: dict[str, list[dict]], denominators: dict[str, list[dict]],
             presented: dict, sufficient: bool, sufficient_threshold: int,
             total_invocations: int,
             mcp_server_key: Callable[[str], str] = lambda name: name) -> list[dict]:
    """分母に列挙された unit ごとの候補 / near-miss を出す (**bucket は確定しない**)。

    母集団は `units` ではなく `denominators` — `units` は invocation を 1 件以上
    持つ unit しか含まないため、count 0 の unit がそもそも現れない。

    `mcp_server_key` は MCP server 名の表記差 (config / 提示 / 呼出で `:` `.` 空白 が
    揺れる) を吸収する join キー関数。**揃えないと毎日呼ばれている server が
    「提示されたが 0 呼出」に見える**ので、呼び先 (presentation 層) が渡す。

    plugin の提示数も他の unit 型と同じく `presented` から引く。plugin 自体は提示
    attachment を持たないが、配下 skill の提示からの導出は presentation 層が済ませて
    ある — 導出をここでも書くと同じ規則が 2 箇所に分かれる。
    """
    specs = {kind: _spec(kind, sufficient_threshold) for kind in BUCKET_CANDIDATES}

    def key_of(kind: str, unit_id: str) -> str:
        return mcp_server_key(unit_id) if kind == "mcp_server" else unit_id

    counts = {kind: {key_of(kind, unit["id"]): unit["count"] for unit in rows}
              for kind, rows in units.items()}
    presented_units = presented.get("units") or {}
    presented_sessions = {
        kind: {key_of(kind, row["id"]): row["sessions_presented"] for row in rows}
        for kind, rows in presented_units.items()
    }

    known_plugin_ids = frozenset(
        row["id"] for row in denominators.get("plugins", []))

    def plugin_sibling_invocations(plugin_id: str) -> int:
        """plugin 配下 unit の invocation 合計 (skill + 同梱 MCP tool)。"""
        total = 0
        for unit in units.get("skill", []):
            if plugin_of_skill(unit["id"], known_plugin_ids) == plugin_id:
                total += unit["count"]
        for unit in units.get("mcp_tool", []):
            if mcp_tool_belongs_to(unit["id"], plugin_id):
                total += unit["count"]
        return total

    out: list[dict] = []
    for kind, denominator_key in (("skill", "skills"),
                                  ("mcp_server", "mcp_servers"),
                                  ("plugin", "plugins")):
        for row in denominators.get(denominator_key, []):
            unit_id = row["id"]
            lookup = key_of(kind, unit_id)
            count = counts.get(kind, {}).get(lookup, 0)
            checks = {
                "relative_judgment_available": sufficient,
                "zero_invocation": count == 0,
                "denominator_source_config": row.get("source") == "config",
            }
            inputs: dict = {"count": count,
                            "denominator_source": row.get("source"),
                            "total_invocations": total_invocations}
            if kind == "plugin":
                siblings = plugin_sibling_invocations(unit_id)
                checks["no_sibling_usage"] = siblings == 0
                inputs["sibling_invocations"] = siblings
            sessions_presented = presented_sessions.get(kind, {}).get(lookup, 0)
            checks["presented_at_least_once"] = sessions_presented > 0
            inputs["sessions_presented"] = sessions_presented

            row_verdict = verdict(unit_id, [Evaluation(
                spec=specs[kind], checks=checks, inputs=inputs)])
            if row_verdict is not None:
                out.append({**row_verdict, "unit_kind": kind})
    return out
