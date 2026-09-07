"""機械が自発的に実行してよい**外部への作用**の catalog (設計 §7 / 決定 8)。

reconciler の自律動作は原則「記帳 + escalation 発行」だけで、そこに載らない作用は
orchestrator の指示 (MCP 呼び出し) 経由で走る。**その原則の例外はこの表に載っているものが
すべて**で、暗黙の例外を作らない。

- 記帳と escalation は作用ではないので catalog に載らない (載せると「何が例外か」が薄まる)
- **worktree の削除は不可逆なので catalog に無い** — 機械には渡さない (設計 Q8)
- built-in は `deploy_ff_only` の 1 つだけ (ADR 0047 の後継)。既定 on・宣言で off
- opt-in は既定 off で、project の宣言で個別に on にする

網羅性は import 時に検査する: **catalog に載っているのに実装が無い / 実装があるのに catalog に
載っていない**、のどちらも起動時に落ちる。これが「有効な機械作用がすべて catalog に列挙される」
という保証の実体で、規約ではなく構造で真になる。
"""

import sys
from collections import namedtuple

from dispatch_v2 import actors, deploy, session, vocabulary, worker_sessions

# rule_id: 宣言 config の key であり escalation の rule id でもある識別子
# default_enabled: 宣言が無いときの既定 (built-in だけが真)
# reversible: その作用が取り消せるか (**偽の作用は catalog に載せない**という線の記録)
# summary: 何を外部へ及ぼすか (LLM / 人が読む 1 行)
MachineRule = namedtuple("MachineRule", "rule_id default_enabled reversible summary")

DEPLOY_RULE = "deploy_ff_only"
CLOSE_IDLE_ON_TERMINAL_RULE = "close_idle_session_on_terminal_workorder"

CATALOG = (
    MachineRule(
        rule_id=DEPLOY_RULE,
        default_enabled=True,
        reversible=True,
        summary="稼働 clone を `git pull --ff-only` で前進させる (冪等・前進のみ)",
    ),
    MachineRule(
        rule_id=CLOSE_IDLE_ON_TERMINAL_RULE,
        default_enabled=False,
        reversible=True,
        summary="terminal な WorkOrder に残っている idle な Session を閉じる (再 spawn で戻せる)",
    ),
)


class RuleCatalogError(RuntimeError):
    """catalog と実装が食い違っている (import 時 fail-closed)。"""


def rule_ids():
    """catalog に載っている rule id (宣言 config の検証に渡す)。"""
    return tuple(rule.rule_id for rule in CATALOG)


def rule_summaries():
    """rule id → 1 行説明 (LLM 向け interface と escalation の evidence に載せる)。"""
    return {rule.rule_id: rule.summary for rule in CATALOG}


def apply_effects(book, enabled, *, runtime, run_git):
    """有効な機械作用を 1 巡させ、結果 (rule_id → 結果) を返す。

    **catalog に無い作用はここから走らない** — 走らせるのは `EFFECTS` に登録されたものだけで、
    その表は catalog との一致を import 時に検査してある。

    **1 つの作用の失敗を他へ波及させない** — 失敗はその rule の結果として返す。まとめて
    落とすと、opt-in rule の失敗が deploy の劣化報告ごと消して「沈黙してよいのは成功だけ」が
    破れる (ADR 0047)。
    """
    return {
        rule.rule_id: _apply_one(rule.rule_id, book, runtime=runtime, run_git=run_git)
        for rule in CATALOG
        if rule.rule_id in enabled
    }


def _apply_one(rule_id, book, *, runtime, run_git):
    try:
        return EFFECTS[rule_id](book, runtime=runtime, run_git=run_git)
    except Exception as exc:  # noqa: BLE001 — 作用ごとに失敗を閉じ込めるのがこの関数の仕事
        print(f"[dispatch-v2] 機械作用 {rule_id} が失敗: {exc}", file=sys.stderr)
        return {"failed": str(exc)}


# --- 作用の実装 ---------------------------------------------------------------


def _deploy(book, *, runtime, run_git):  # noqa: ARG001 — 表の signature を揃える
    """稼働 clone を pull する。clone を 1 度も観測していない project では走らない。"""
    clone_path = book.clone_path()
    if clone_path is None:
        return deploy.unknown_clone()
    return deploy.run_deploy(run_git, clone_path)


def _close_idle_sessions_on_terminal_work_orders(book, *, runtime, run_git):  # noqa: ARG001
    """terminal な WorkOrder に残っている **idle な** Session を閉じる (opt-in)。

    **可逆**である根拠: 閉じた Session は同じ WorkOrder へ再 spawn できる。ただし WorkOrder が
    terminal なのでそこへ再 spawn する理由は普通は無く、実質は資源の解放だけになる。

    `idle` を条件に入れるのは、**動いている worker を機械が黙って落とさない**ため (設計 §7 の
    例も「merged 済み CL の *idle* Session close」)。走行中の Session に手を出すのは
    escalation (rule #2 merged_but_alive) の担当で、作用ではない。
    """
    closed = []
    for live in session.live_sessions(book.state):
        work_order = book.get(live["wo_id"])
        if work_order["phase"] not in vocabulary.TERMINAL_PHASES or live["handle"] is None:
            continue
        if live["activity"] != "idle":
            continue
        worker_sessions.close(
            book, runtime=runtime, session_id=live["session_id"], actor=actors.RECONCILER
        )
        closed.append(live["session_id"])
    return {"closed_sessions": closed}


EFFECTS = {
    DEPLOY_RULE: _deploy,
    CLOSE_IDLE_ON_TERMINAL_RULE: _close_idle_sessions_on_terminal_work_orders,
}


def require_catalog_consistency(catalog, effects):
    """catalog と実装表の一致を検査する。ずれていれば ValueError で即死。

    **片側だけの entry を落とす**のが要点 — 実装だけあって catalog に無い作用は「暗黙の例外」
    そのもので、catalog を読んでも見つからないまま外部へ作用する。
    """
    declared = {rule.rule_id for rule in catalog}
    implemented = set(effects)
    unimplemented = sorted(declared - implemented)
    if unimplemented:
        raise RuleCatalogError(f"catalog に実装が無い作用 {unimplemented}")
    unlisted = sorted(implemented - declared)
    if unlisted:
        raise RuleCatalogError(
            f"catalog に載っていない作用 {unlisted} (暗黙の例外を作らない — CATALOG へ宣言する)"
        )
    irreversible = sorted(rule.rule_id for rule in catalog if not rule.reversible)
    if irreversible:
        raise RuleCatalogError(
            f"不可逆な作用は機械に渡さない {irreversible} (設計 Q8 — orchestrator の指示経由にする)"
        )
    if len(declared) != len(catalog):
        raise RuleCatalogError("CATALOG に rule_id の重複がある")
    automated = declared & set(NEVER_AUTOMATED)
    if automated:
        raise RuleCatalogError(
            f"機械に渡さないと決めた作用が catalog に載っている {sorted(automated)}"
        )


# 機械に渡さないと決めた作用の**記録**。実効的な不変条件は上の `reversible` 検査が持ち
# (不可逆な作用は catalog に載らない)、この表は「なぜこの 2 つが載っていないか」を名前で
# 残すためにある。同じ綴りで登録した場合だけ import 時に落ちる
NEVER_AUTOMATED = {
    "worktree_removal": "不可逆。回収資格の判定までが機械で、削除は orchestrator の名指し 1 回",
    "tracker_write": "issue の close / label / comment は orchestrator の指示経由 (設計 §7)",
}

require_catalog_consistency(CATALOG, EFFECTS)
