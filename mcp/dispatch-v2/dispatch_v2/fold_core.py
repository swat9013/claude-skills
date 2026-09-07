"""fold の共通部品 (例外型と payload の取り出し)。

`fold.py` から**機械的に切り出しただけ**で、振る舞いは持たない。切り出したのは、集約ごとの
event rule (WorkOrder / Session / Worktree) を別 module に置いても循環 import にならない
ようにするため — rule 側は `fold_core` だけを見て、合成は `fold.py` が行う。

例外型の正本はここ 1 箇所。`fold.FoldError` として再輸出されるので、既存の呼び出し側
(HTTP 層 / 台帳) は `fold` 越しに見たままでよい。
"""

from collections import namedtuple

# event type → (検証, 適用)。**1 つの表に組で置く** — 検証表と適用表を分けると、片方だけ
# 足した状態を止めるための同期検査が要る。組にすれば entry を足す行為そのものが両方の登録に
# なり、網羅性が構造で真になる
EventRule = namedtuple("EventRule", "require apply")


class FoldError(RuntimeError):
    """event を現在の state へ適用できない (log が壊れている / 不変条件に反する)。

    **適用不能はこの 1 系統に束ねる** — 呼び出し側 (HTTP 層 / MCP 薄 client) が
    vocabulary の例外型まで知ると、fold の内部構造が境界の外へ漏れる。
    """


class NotInLedgerError(FoldError):
    """指し先 (WorkOrder / Session / escalation) が台帳に無い (HTTP 404 に写る唯一の区別)。"""


def require_field(payload, field):
    """payload の必須欄を取り出す。無ければ FoldError。"""
    value = payload.get(field)
    if value is None:
        raise FoldError(f"event payload に {field} が無い")
    return value


def require_work_order(state, wo_id):
    """WorkOrder を引く。無ければ NotInLedgerError (存在検査の唯一の置き場)。"""
    work_order = state["work_orders"].get(wo_id)
    if work_order is None:
        raise NotInLedgerError(f"WorkOrder {wo_id} が台帳に無い")
    return work_order


def require_session(state, session_id):
    """Session を引く。無ければ NotInLedgerError。"""
    session = state["sessions"].get(session_id)
    if session is None:
        raise NotInLedgerError(f"Session {session_id} が台帳に無い")
    return session
