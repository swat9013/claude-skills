"""Session 集約 — worker の 1 実行 (ADR 0055)。

**identity はシステム発番の ULID が正で、runtime handle (herdr の pane_id 等) は属性**
(設計 決定 13)。台帳の鍵に runtime 語彙を使うと、runtime を差し替えた瞬間に過去の記録が
読めなくなる。handle は「その Session がどの runtime のどこに居たか」を指す観測値。

軸が 2 つあるのは v1 で blocked が running に潰れていたから (設計 決定 17):

- **lifecycle = システムの事実**。起動を依頼した / 起動した / 生きている / 終わった
- **activity = alive の間の観測**。中立 3 値 + runtime の生値を併記

不変条件は 1 つ: **同一 WorkOrder の非終端 Session は 0..1**。WorkOrder の「同一 issue の
非終端 WO は 0..1」と同じ理屈で、これが無いと 1 つの作業指示に対して worker が二重に立つ。
"""

from dispatch_v2 import session_vocabulary as session_words
from dispatch_v2.fold_core import (
    EventRule,
    FoldError,
    require_field,
    require_session,
    require_work_order,
)


def empty_state():
    """Session 集約の初期 state (`fold.empty_state` が合成する)。"""
    return {"sessions": {}}


# --- 検証 ---------------------------------------------------------------------


def _require_requestable(state, event):
    payload = event["payload"]
    session_id = require_field(payload, "session_id")
    wo_id = require_field(payload, "wo_id")
    require_field(payload, "backend")
    if session_id in state["sessions"]:
        raise FoldError(f"Session {session_id} は既に存在する (sessionId が重複している)")
    require_work_order(state, wo_id)
    live = live_session_for(state, wo_id)
    if live is not None:
        raise FoldError(
            f"WorkOrder {wo_id} には非終端の Session {live['session_id']} "
            f"({live['lifecycle']}) が既にある — 再 spawn の前に前の Session を終わらせる"
        )


def _require_lifecycle_changeable(state, event):
    payload = event["payload"]
    session = require_session(state, require_field(payload, "session_id"))
    target = require_field(payload, "lifecycle")
    try:
        session_words.validate_lifecycle(session["lifecycle"], target)
        if target == "ended":
            session_words.require_ended_reason(
                session["lifecycle"], require_field(payload, "reason")
            )
    except (session_words.LifecycleError, session_words.SessionVocabularyError) as exc:
        raise FoldError(str(exc)) from exc
    if target == "starting":
        # handle が付かない starting は「runtime に何が生まれたか」を答えられない記録になる。
        # 後から観測で埋める経路を作ると、その間の Session は close も観測もできない
        require_field(payload, "handle")


def _require_activity_observable(state, event):
    payload = event["payload"]
    session = require_session(state, require_field(payload, "session_id"))
    if session["lifecycle"] != "alive":
        raise FoldError(
            f"Session {session['session_id']} は {session['lifecycle']} なので activity を持たない "
            "(activity は alive の間の観測)"
        )
    require_field(payload, "activity_raw")
    activity = payload.get("activity")
    if activity is not None:
        try:
            session_words.require_activity(activity)
        except session_words.SessionVocabularyError as exc:
            raise FoldError(str(exc)) from exc


# --- 適用 ---------------------------------------------------------------------


def _apply_requested(state, event):
    payload = event["payload"]
    state["sessions"][payload["session_id"]] = {
        "session_id": payload["session_id"],
        "wo_id": payload["wo_id"],
        "backend": payload["backend"],
        "label": payload.get("label"),
        "handle": None,
        "lifecycle": session_words.INITIAL_LIFECYCLE,
        "ended_reason": None,
        "activity": None,
        "activity_raw": None,
        "created_at": event["ts"],
        "updated_at": event["ts"],
    }


def _apply_lifecycle_changed(state, event):
    payload = event["payload"]
    session = state["sessions"][payload["session_id"]]
    session["lifecycle"] = payload["lifecycle"]
    if payload.get("handle") is not None:
        session["handle"] = payload["handle"]
    if payload["lifecycle"] == "ended":
        session["ended_reason"] = payload["reason"]
        # activity は alive の間の観測なので、終わった Session には現在値が無い。
        # 死の瞬間の生値が要るときは event log を読む (state に古い観測を残さない)
        session["activity"] = None
        session["activity_raw"] = None
    session["updated_at"] = event["ts"]


def _apply_activity_observed(state, event):
    payload = event["payload"]
    session = state["sessions"][payload["session_id"]]
    session["activity"] = payload.get("activity")
    session["activity_raw"] = payload["activity_raw"]
    session["updated_at"] = event["ts"]


EVENT_RULES = {
    "session_requested": EventRule(_require_requestable, _apply_requested),
    "session_lifecycle_changed": EventRule(_require_lifecycle_changeable, _apply_lifecycle_changed),
    "session_activity_observed": EventRule(_require_activity_observable, _apply_activity_observed),
}


# --- 導出 (state から読む) -----------------------------------------------------


def live_session_for(state, wo_id):
    """その WorkOrder の非終端 Session (0..1)。無ければ None。"""
    for session in state["sessions"].values():
        if session["wo_id"] == wo_id and session["lifecycle"] not in session_words.TERMINAL_LIFECYCLES:
            return session
    return None


def latest_session_for(state, wo_id):
    """その WorkOrder の**最後に記帳された** Session (終わったものも含む)。無ければ None。

    `live_session_for` と分けてあるのは、**終わった Session そのものが escalation の条件だから**
    (rule #5 session_died_empty)。非終端だけを見る関数で代用すると、死んだ worker が
    「Session が無い」に潰れ、再 spawn の判断が誰にも上がらない。

    最後の 1 件で足りるのは「同一 WorkOrder の非終端 Session は 0..1」の不変条件があるため —
    前の Session が終わっていなければ次は起こせないので、系譜の末尾が今の状況を代表する。
    """
    latest = None
    for session in state["sessions"].values():
        if session["wo_id"] == wo_id:
            latest = session
    return latest


def live_sessions(state):
    """非終端の Session を記帳順に返す (観測 tick が回る対象)。"""
    return [
        session
        for session in state["sessions"].values()
        if session["lifecycle"] not in session_words.TERMINAL_LIFECYCLES
    ]


def handles_in_ledger(state):
    """台帳が知っている runtime handle の集合 (台帳外 session の検出に使う)。"""
    return {session["handle"] for session in state["sessions"].values() if session["handle"]}
