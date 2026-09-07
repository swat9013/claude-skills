"""state = fold(events)。台帳の現況を event 列だけから導出する (ADR 0057)。

**state を別 file に持たない**のが v1 との違い。daemon は起動のたびにここで畳み直し、
以降は追記のたびに同じ関数を 1 event ずつ当てて memory 上の state を進める。

適用できない event は **停止させる** (`FoldError`)。log を手で編集した / 別 version の
daemon が書いた event が混ざったときに、黙って別の状態を作るのが最も危険な失敗
— 「なぜこの状態か」に答えられない状態が正本から生まれてしまう。

不変条件は 2 つ:

- 同一 issue の非終端 WorkOrder は 0..1 (ADR 0055)。再着手は新しい WorkOrder になり、
  系譜は同一 issue_ref の WorkOrder 列として `lineage_of` で導出する
- terminal は不変 (`vocabulary.validate_transition` が拒む)
"""

from dispatch_v2 import cl_record, event_log, session, vocabulary, working_clone, worktree
from dispatch_v2.fold_core import (  # noqa: F401 — 正本は fold_core、参照は fold 越しのまま
    EventRule,
    FoldError,
    NotInLedgerError,
    require_field as _require_field,
    require_session,
    require_work_order,
)

# escalation 1 件を配送してよい回数の上限。**初回の raise を 1 回目に数える** ので、続く再送は
# 2 回まで = 合計 3 回で機械的に打ち切られる (設計 §8「再送 3 回で打ち切り、ack で終了」)。
# 打ち切っても escalation は inbox から消えない — 配送 (nudge) を止めるだけで、pull が正だから
# (ADR 0056)。**上限をここに 1 つだけ置く**のは、rule 側に散ると rule ごとに打ち切りが揺れるため
MAX_ESCALATION_ATTEMPTS = 3


def delivery_is_cut_off(escalation):
    """この escalation の配送がもう起きないか (**読み手向けの述語**)。

    打ち切りの根拠は 2 つある — 性質の宣言 (`permanent`) と再送上限。上限に届いた escalation は
    `permanent` が偽のまま止まるので、`attempts` だけを見る読み手は配送中と区別が付かない
    (gh#930)。**上限と同じ場所に置く**のは、読み手が上限の綴りを写経しないため。

    `deliver_escalation` の分岐 (今この tick で配送してよいか) とは別物なので畳まない — あちらは
    ack 済みや dedup も見るし、**呼び出し側の宣言も込みで判断する**。
    """
    return bool(escalation["permanent"]) or escalation["attempts"] >= MAX_ESCALATION_ATTEMPTS


def empty_state():
    """event が 1 件も無いときの state。

    集約ごとの初期値は集約の module が持ち、ここは合成だけを行う (event rule と同じ形)。

    `candidate_observation` の初期値が `None` なのは「まだ一度も観測していない」を「観測して
    空」から分けるため — 潰すと daemon の初回 tick で候補が全件「新規」に見え、escalation が
    溢れる。
    """
    return {
        "work_orders": {},
        "escalations": {},
        "escalation_by_dedup_key": {},
        "candidate_observation": None,
        "anomalies": [],
        **session.empty_state(),
        **working_clone.empty_state(),
    }


def fold(events):
    """event 列から state を作る。"""
    state = empty_state()
    for event in events:
        apply_event(state, event)
    return state


def apply_event(state, event):
    """event 1 件を state へ適用する (適用不能なら state を変えずに FoldError)。"""
    require_applicable(state, event)
    _rule_for(event).apply(state, event)
    return state


def require_applicable(state, event):
    """event を適用できるかだけを検査する (state は変えない)。

    書き込み経路が「検証 → 追記 → memory へ適用」の順で回せるように、検査を分けてある。
    追記の後で適用が失敗すると、正本に読めない event が残る。
    """
    _rule_for(event).require(state, event)


def _rule_for(event):
    event_type = event.get("type")
    rule = EVENT_RULES.get(event_type)
    if rule is None:
        raise FoldError(
            f"未知の event type: {event_type!r} "
            f"(既知: {', '.join(EVENT_RULES)}。新しい daemon が書いた log の可能性)"
        )
    return rule


# --- 検証 ---------------------------------------------------------------------


def _require_creatable(state, event):
    payload = event["payload"]
    wo_id = _require_field(payload, "wo_id")
    issue_ref = _require_field(payload, "issue_ref")
    if wo_id in state["work_orders"]:
        raise FoldError(f"WorkOrder {wo_id} は既に存在する (woId が重複している)")
    open_work_order = open_work_order_for(state, issue_ref)
    if open_work_order is not None:
        raise FoldError(
            f"{issue_ref} には非終端の WorkOrder {open_work_order['wo_id']} "
            f"({open_work_order['phase']}) が既にある — 同一 issue の非終端 WorkOrder は 1 つまで"
        )


def _require_transitionable(state, event):
    payload = event["payload"]
    work_order = require_work_order(state, _require_field(payload, "wo_id"))
    try:
        vocabulary.validate_transition(work_order["phase"], _require_field(payload, "phase"))
    except (vocabulary.TransitionError, vocabulary.VocabularyError) as exc:
        raise FoldError(str(exc)) from exc


def _require_annotatable(state, event):
    require_work_order(state, _require_field(event["payload"], "wo_id"))


def _require_escalation_raisable(state, event):
    payload = event["payload"]
    escalation_id = _require_field(payload, "escalation_id")
    if escalation_id in state["escalations"]:
        raise FoldError(f"escalation {escalation_id} は既に存在する")
    _require_field(payload, "rule_id")
    dedup_key = _require_field(payload, "dedup_key")
    existing = state["escalation_by_dedup_key"].get(dedup_key)
    if existing is not None:
        # **同じ dedup_key で 2 件目を積ませない**。dedup は「同じ事象を 2 度上げない」ための
        # 仕組みなので、2 件目を許すと inbox に同一事象が並び、ack と再送の打ち切りがどちらの
        # 件に効いたのか答えられなくなる。条件が続いているなら escalation_resent
        raise FoldError(
            f"dedup_key {dedup_key!r} の escalation {existing} が既にある "
            "(条件が続いているなら escalation_resent、受け取ったなら escalation_acked)"
        )


def _require_escalation_resendable(state, event):
    escalation_id = _require_field(event["payload"], "escalation_id")
    escalation = state["escalations"].get(escalation_id)
    if escalation is None:
        raise NotInLedgerError(f"escalation {escalation_id} が台帳に無い")
    if escalation["acked_at"] is not None:
        raise FoldError(
            f"escalation {escalation_id} は ack 済み (ack が再送の終了契機。再送は積まない)"
        )
    if escalation["attempts"] >= MAX_ESCALATION_ATTEMPTS:
        raise FoldError(
            f"escalation {escalation_id} は配送 {escalation['attempts']} 回で打ち切り済み "
            f"(上限 {MAX_ESCALATION_ATTEMPTS} 回)"
        )


def _require_candidates_observable(state, event):
    payload = event["payload"]
    for field in ("candidates", "engaged"):
        value = payload.get(field)
        if not isinstance(value, list):
            raise FoldError(f"{field} が list でない ({type(value).__name__})")


def _require_escalation_ackable(state, event):
    escalation_id = _require_field(event["payload"], "escalation_id")
    escalation = state["escalations"].get(escalation_id)
    if escalation is None:
        raise NotInLedgerError(f"escalation {escalation_id} が台帳に無い")
    if escalation["acked_at"] is not None:
        # 上書きを許すと「最初にいつ受け取ったか」が state から答えられなくなる
        raise FoldError(
            f"escalation {escalation_id} は {escalation['acked_at']} に ack 済み (再 ack は無効)"
        )


def _require_anomaly(state, event):
    """記録専用の event。適用の前提を持たない。"""


# --- 適用 ---------------------------------------------------------------------


def _apply_created(state, event):
    payload = event["payload"]
    state["work_orders"][payload["wo_id"]] = {
        "wo_id": payload["wo_id"],
        "issue_ref": payload["issue_ref"],
        "phase": vocabulary.INITIAL_PHASE,
        "note": payload.get("note"),
        # WorkOrder が所有する資源と、worker から返ってきた自己申告。**欄として最初から
        # 置く** — 後から生える欄にすると、読み手が「無い」と「まだ観測していない」を
        # 区別できない
        "worktree": None,
        # 台帳へ記録した issue → CL の紐づき。tracker から紐づきを引けない置き場 (Jira) では
        # ここが唯一の join になる (`cl_record`)
        "cls": [],
        "outcome": None,
        # 機械遷移が根拠を載せる欄。意図の記帳 (人 / orchestrator) では埋まらないので None
        "evidence": None,
        "created_at": event["ts"],
        "updated_at": event["ts"],
    }


def _apply_transitioned(state, event):
    payload = event["payload"]
    work_order = state["work_orders"][payload["wo_id"]]
    work_order["phase"] = payload["phase"]
    if payload.get("note") is not None:
        work_order["note"] = payload["note"]
    # **evidence は state へ写す** — terminal は不変で逆行遷移も無いので、機械が確定させた
    # 遷移の根拠を後から人が監査する材料はこれしかない (ADR 0055 の「誤分類は annotate +
    # debrief で補足」が成立する前提)。正本にだけ残して view に出さないと `wo_get` から読めない
    if payload.get("evidence") is not None:
        work_order["evidence"] = payload["evidence"]
    work_order["updated_at"] = event["ts"]


def _apply_annotated(state, event):
    payload = event["payload"]
    work_order = state["work_orders"][payload["wo_id"]]
    work_order["note"] = payload.get("note")
    work_order["updated_at"] = event["ts"]


def _require_outcome_reportable(state, event):
    payload = event["payload"]
    require_work_order(state, _require_field(payload, "wo_id"))
    _require_field(payload, "outcome")


def _apply_outcome_reported(state, event):
    payload = event["payload"]
    work_order = state["work_orders"][payload["wo_id"]]
    # **置換 (追記ではない)**。過去の申告は event log が持ち、state は「今の申告」を答える。
    # 誰が名乗ったかを残すのは、worker の自己申告と orchestrator の代筆を読み分けるため
    work_order["outcome"] = {
        "outcome": payload["outcome"],
        "summary": payload.get("summary"),
        "reported_by": event["actor"],
        "reported_at": event["ts"],
    }
    work_order["updated_at"] = event["ts"]


def _apply_escalation_raised(state, event):
    payload = event["payload"]
    state["escalations"][payload["escalation_id"]] = {
        "escalation_id": payload["escalation_id"],
        "rule_id": payload["rule_id"],
        "dedup_key": payload["dedup_key"],
        "evidence": payload.get("evidence"),
        "wo_id": payload.get("wo_id"),
        # 再試行では直らない事象か。**真なら配送はこの 1 回で終わり**、`attempts` は上限へ
        # 達しないまま止まる — state に残さないと、読み手が「まだ配送中」と「1 度きりで
        # 打ち切り済み」を区別できず経過時間で代理判定するしかなくなる (gh#930)。
        # 既に disk に在る event は本欄を持たないので既定は偽 (欠けを畳めないと daemon が止まる)
        "permanent": payload.get("permanent", False),
        # 配送回数。**正本の event 列から畳む**ので daemon を再起動しても 0 に戻らない —
        # memory の counter に置くと ADR 0056 が「v1 で打ち切りが永久に効かない」と名指した
        # 問題がそのまま再発する。初回の raise が 1 回目の配送
        "attempts": 1,
        "raised_at": event["ts"],
        "last_sent_at": event["ts"],
        "acked_at": None,
    }
    state["escalation_by_dedup_key"][payload["dedup_key"]] = payload["escalation_id"]


def _apply_escalation_resent(state, event):
    escalation = state["escalations"][event["payload"]["escalation_id"]]
    escalation["attempts"] += 1
    escalation["last_sent_at"] = event["ts"]


def _apply_escalation_acked(state, event):
    escalation = state["escalations"][event["payload"]["escalation_id"]]
    escalation["acked_at"] = event["ts"]


def _apply_candidates_observed(state, event):
    """候補集合の観測を state へ写す。**差分の基準はここにしか無い**。

    集合を event として残すのは、daemon の再起動で基準が消えると次の tick で候補が全件
    「新規」に見えるため (ADR 0057 の「観測事実の変化も全部イベント」)。

    観測した候補と、そのとき着手中だった issue を**別の欄で持つ**。畳んで 1 つの集合として
    残すと、着手中だった issue が「通知済み」と区別できなくなり、WorkOrder を `released` で
    候補へ返したときに二度と通知されない。
    """
    payload = event["payload"]
    state["candidate_observation"] = {
        "candidates": list(payload["candidates"]),
        "engaged": list(payload["engaged"]),
    }


def _apply_anomaly(state, event):
    state["anomalies"].append(event)


_WORK_ORDER_RULES = {
    "wo_created": EventRule(_require_creatable, _apply_created),
    "wo_transitioned": EventRule(_require_transitionable, _apply_transitioned),
    "wo_annotated": EventRule(_require_annotatable, _apply_annotated),
    "wo_outcome_reported": EventRule(_require_outcome_reportable, _apply_outcome_reported),
    "escalation_raised": EventRule(_require_escalation_raisable, _apply_escalation_raised),
    "escalation_resent": EventRule(_require_escalation_resendable, _apply_escalation_resent),
    "escalation_acked": EventRule(_require_escalation_ackable, _apply_escalation_acked),
    "candidates_observed": EventRule(_require_candidates_observable, _apply_candidates_observed),
    event_log.TORN_TAIL_EVENT: EventRule(_require_anomaly, _apply_anomaly),
}

# 集約ごとの表を**合成する**。集約を 1 つ足すのが 1 行になり、追加を検証表と適用表の
# 両方へ書き分ける必要も無い (組で持つ理由は上と同じ)
EVENT_RULES = {
    **_WORK_ORDER_RULES,
    **cl_record.EVENT_RULES,
    **session.EVENT_RULES,
    **worktree.EVENT_RULES,
    **working_clone.EVENT_RULES,
}


# --- 導出 (state から読む) -----------------------------------------------------


def open_work_order_for(state, issue_ref):
    """その issue の非終端 WorkOrder (0..1)。無ければ None。"""
    for work_order in state["work_orders"].values():
        if work_order["issue_ref"] == issue_ref and work_order["phase"] not in vocabulary.TERMINAL_PHASES:
            return work_order
    return None


def lineage_of(state, issue_ref):
    """同一 issue の WorkOrder を記帳順に並べた woId の列 (再着手の系譜)。

    **系譜を結ぶ欄を WorkOrder に持たせない** — 順序は event log の並びが正本で、
    `work_orders` の挿入順がそれを保つ。欄を足すと正本と別の系譜が生まれる。
    """
    return [
        work_order["wo_id"]
        for work_order in state["work_orders"].values()
        if work_order["issue_ref"] == issue_ref
    ]


def escalation_for_dedup_key(state, dedup_key):
    """その dedup_key の escalation (ack 済みも含む)。無ければ None。

    **ack 済みも返す**のが要点。ack は「受け取った」の記帳であり (ADR 0056「ack で終了」)、
    同じ観測値から同じ key が再計算されても上げ直さない — 上げ直すと ack が意味を失う。
    観測値が変われば key も変わるので、状況が動いたときだけ新しい escalation になる。
    """
    escalation_id = state["escalation_by_dedup_key"].get(dedup_key)
    return state["escalations"][escalation_id] if escalation_id is not None else None


def candidate_observation(state):
    """最後の候補観測 (`candidates` と、そのとき着手中だった `engaged`)。

    **一度も観測していなければ None** — 「観測して空」と分ける。潰すと初回 tick で候補が
    全件「新規」に見え、導入直後に inbox が候補プールの写しになる。
    """
    observed = state["candidate_observation"]
    return {key: list(value) for key, value in observed.items()} if observed is not None else None


def announced_candidates(state):
    """前回の tick で「通知済み」と見なせる候補 ref の集合。差分の基準。

    観測した候補から着手中の issue を除いたもの。**着手中を基準に残さない**のが要点で、
    残すと `released` で候補プールへ返した issue が `previous` に居続け、二度と
    `candidate_appeared` が立たない (候補が沈む)。
    """
    observed = state["candidate_observation"]
    if observed is None:
        return None
    return [ref for ref in observed["candidates"] if ref not in set(observed["engaged"])]


def delivered_escalation_count(state):
    """これまでに配送した escalation の延べ回数 (初回 + 再送)。

    **空 nudge を撃つかの判定にだけ使う** — tick の前後で比べて増えていれば、その tick で
    何かが inbox に載ったということ。正本から数えるので、配送が複数の job に散っていても
    数え漏らさない (collector を job へ配って回る必要が無い)。
    """
    return sum(escalation["attempts"] for escalation in state["escalations"].values())


def pending_escalations(state):
    """まだ ack されていない escalation を発生順で返す。"""
    return [
        escalation
        for escalation in state["escalations"].values()
        if escalation["acked_at"] is None
    ]


def sessions_of(state, wo_id):
    """その WorkOrder の Session を記帳順に並べた sessionId の列 (再発行の系譜)。

    **系譜を結ぶ欄を WorkOrder に持たせない** — 順序は event log の並びが正本で、
    `sessions` の挿入順がそれを保つ (WorkOrder の `lineage_of` と同じ理屈)。
    """
    return [
        session_view["session_id"]
        for session_view in state["sessions"].values()
        if session_view["wo_id"] == wo_id
    ]


