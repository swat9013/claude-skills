"""Worktree 集約 — WorkOrder が所有する作業ツリー (ADR 0055)。

**worktree は WorkOrder の持ち物で Session の持ち物ではない。** Session が死んでも残り、
同じ WorkOrder の次の Session が同じツリーを引き継ぐ (v1 で「pane は死んだが worktree は
生きている」が drift として扱われていた組合せが、ここでは正常な状態になる)。

回収 (削除) は**不可逆なので機械に渡さない** (設計 Q8 / ADR 0048)。本 module が持つのは
**回収資格の機械判定まで**で、削除の実行は orchestrator が対象を名指しした 1 回の指示で走る。
判定は観測 (`TreeSighting`) を引数で受ける純関数なので、git を触らずに検証できる。
"""

from collections import namedtuple

from dispatch_v2 import session, vocabulary
from dispatch_v2.fold_core import EventRule, FoldError, require_field, require_work_order

# git 側から観測した作業ツリーの状態。**判定に要る事実だけ**を持つ (git の出力そのものは
# 呼び出し側に留める)
TreeSighting = namedtuple("TreeSighting", "path present registered dirty locked")

# 回収を阻む事由。**判定結果は真偽値ではなく事由の列**で返す — 「なぜ回収できないか」を
# orchestrator が読んで次の手 (worker へ commit を促す / Session を閉じる) を選ぶため
BLOCKER_MEANINGS = {
    "workorder_open": "WorkOrder がまだ非終端 (terminal を見てから回収する)",
    "session_live": "非終端の Session が居る (先に session_close する)",
    "tree_dirty": "未コミットの変更がある (worker に commit / push させる)",
    "tree_locked": "git が worktree を lock している",
    "tree_unregistered": (
        "ディレクトリは在るが git の登録に無い (未コミットの変更が入っているか判定できない — "
        "ADR 0048 の残骸として報告するだけにし、回収は repo 側の手順で行う)"
    ),
}


# --- 検証 ---------------------------------------------------------------------


def _require_recordable(state, event):
    payload = event["payload"]
    work_order = require_work_order(state, require_field(payload, "wo_id"))
    require_field(payload, "path")
    recorded = work_order.get("worktree")
    if recorded is not None and recorded["path"] != payload["path"]:
        # 上書きを許すと、前の path のツリーが台帳から見えないまま残る (= 自前で orphan を作る)
        raise FoldError(
            f"WorkOrder {work_order['wo_id']} は既に {recorded['path']} を所有している "
            "(別の worktree を持たせる前に回収する)"
        )


def _require_forgettable(state, event):
    payload = event["payload"]
    work_order = require_work_order(state, require_field(payload, "wo_id"))
    if work_order.get("worktree") is None:
        raise FoldError(f"WorkOrder {work_order['wo_id']} は worktree を所有していない")


# --- 適用 ---------------------------------------------------------------------


def _apply_recorded(state, event):
    payload = event["payload"]
    work_order = state["work_orders"][payload["wo_id"]]
    work_order["worktree"] = {"path": payload["path"], "branch": payload.get("branch")}
    work_order["updated_at"] = event["ts"]


def _apply_forgotten(state, event):
    payload = event["payload"]
    work_order = state["work_orders"][payload["wo_id"]]
    work_order["worktree"] = None
    work_order["updated_at"] = event["ts"]


EVENT_RULES = {
    "worktree_created": EventRule(_require_recordable, _apply_recorded),
    "worktree_removed": EventRule(_require_forgettable, _apply_forgotten),
}


# --- 回収資格の判定 (純関数) ---------------------------------------------------


def reclaim_verdict(state, wo_id, sighting):
    """WorkOrder 1 件の worktree が回収資格を満たすかを判定する。

    資格は「terminal な WorkOrder ∧ 非終端 Session が居ない ∧ ツリーが clean ∧ lock 無し」。
    **ツリーが既に無い場合も資格あり**とするのは、台帳の記録だけが残った状態へ再実行で
    収束させるため (`principle-idempotent-operations`)。
    """
    work_order = require_work_order(state, wo_id)
    recorded = require_owned_worktree(state, wo_id)
    blockers = []
    if work_order["phase"] not in vocabulary.TERMINAL_PHASES:
        blockers.append("workorder_open")
    if session.live_session_for(state, wo_id) is not None:
        blockers.append("session_live")
    if sighting.present and not sighting.registered:
        # **dirty を判定できないので回収しない**。git が知らないディレクトリで status を
        # 撃つと親 clone の答えを借りてしまい、未コミットの成果ごと台帳から手放しうる
        blockers.append("tree_unregistered")
    if sighting.present and sighting.dirty:
        blockers.append("tree_dirty")
    if sighting.present and sighting.locked:
        blockers.append("tree_locked")
    return {
        "wo_id": wo_id,
        "path": recorded["path"],
        "branch": recorded["branch"],
        "eligible": not blockers,
        "blockers": blockers,
        "present": sighting.present,
    }


# --- 導出 (state から読む) -----------------------------------------------------


def require_owned_worktree(state, wo_id):
    """WorkOrder が所有している worktree を引く。所有していなければ FoldError。

    **所有の検査はここ 1 箇所**。呼び出し側が「無ければ空 path」のような代替値を作ると、
    その値が git を触る層まで旅する。
    """
    recorded = require_work_order(state, wo_id).get("worktree")
    if recorded is None:
        raise FoldError(f"WorkOrder {wo_id} は worktree を所有していない")
    return recorded


def owned_worktrees(state):
    """台帳が所有を記録している worktree (wo_id / path / branch) を記帳順に返す。"""
    return [
        {"wo_id": work_order["wo_id"], **work_order["worktree"]}
        for work_order in state["work_orders"].values()
        if work_order.get("worktree") is not None
    ]


def owned_paths(state):
    """台帳が知っている worktree path の集合 (台帳外 worktree の検出に使う)。"""
    return {entry["path"] for entry in owned_worktrees(state)}
