"""CL 記録の集約 — WorkOrder が台帳に持つ issue → CL の紐づき (ADR 0040 決定 1 / ADR 0059)。

**tracker から紐づきを引けない置き場のための join。** gh / glab 置き場では closing reference
(`Closes #N`) を adapter が読めるので本 module は使われない。Jira 置き場では読めない — Jira は
GitLab / GitHub の CL を列挙できないので、**worker の自己申告を種に人 (orchestrator) が
記録した紐づきが唯一の源**になる。

**「まだ記録が無い」を terminal の根拠にしない**のが本 module の中核。記録 0 件を
「紐づき 0 件」として扱うと、CL を記録する前に issue が閉じた WorkOrder が `abandoned` で
terminal に落ちる (terminal は不変)。ただし止めるのは**確定だけ**で、観測そのものは
`[]` で返す (`absence_is_evidence = False`) — `None` (未観測) に倒すと CL を読む rule が
軒並み降り、`session_died_empty` (worker が成果を残さず死んだ) すら立たなくなる。
代償として、**CL を 1 件も記録しなかった WorkOrder は機械遷移で終わらない** — 非終端のまま
`wo_list` に残り、人が terminal を選ぶ。沈黙して誤分類するより、残って見えるほうを取る。

記録の**訂正経路を持つ** (`cl_forgotten`)。誤った CL ref を記録すると、その CL の status が
機械遷移の根拠になり、無関係な CL の merge で WorkOrder が `completed` へ落ちる。台帳は
append-only なので、訂正の口が無いと誤記録は WorkOrder が終わるまで消せない。
"""

from dispatch_v2 import ports, refs, vocabulary
from dispatch_v2.fold_core import EventRule, FoldError, require_field, require_work_order


# --- 検証 ---------------------------------------------------------------------


def _require_recordable(state, event):
    payload = event["payload"]
    require_work_order(state, require_field(payload, "wo_id"))
    _require_cl_ref(require_field(payload, "cl_ref"))
    require_field(payload, "repo")
    _require_role(require_field(payload, "role"))


def _require_forgettable(state, event):
    payload = event["payload"]
    work_order = require_work_order(state, require_field(payload, "wo_id"))
    cl_ref = _require_cl_ref(require_field(payload, "cl_ref"))
    if cl_ref not in {recorded["cl_ref"] for recorded in work_order.get("cls", [])}:
        raise FoldError(f"WorkOrder {work_order['wo_id']} は {cl_ref} を記録していない")


def _require_cl_ref(cl_ref):
    """CL ref が中立形式であることを検証して返す。

    **記帳の境界で落とす** (`principle-validate-at-boundaries`)。素通しすると、綴りの壊れた ref
    が観測層まで旅して adapter の中で落ち、観測失敗 (store 到達不能) として数えられる — 台帳の
    誤記録が「GitLab が落ちている」と報告される。
    """
    try:
        return refs.require_cl_ref(cl_ref)
    except refs.RefError as exc:
        raise FoldError(str(exc)) from exc


def _require_role(role):
    """紐づき方が中立語彙 (`closes` / `mention`) であることを検証して返す。"""
    try:
        return ports.require_role(role)
    except ports.ObservationError as exc:
        raise FoldError(str(exc)) from exc


# --- 適用 ---------------------------------------------------------------------


def _apply_recorded(state, event):
    """同じ CL ref の記録は**置換**する (追記しない)。

    同じ CL を repo や role を直して記録し直す経路が自然に効く
    (`principle-idempotent-operations`) — 追記にすると、同じ CL が 2 度畳まれて
    `aggregate_cl_status` が同じ status を 2 回数え、訂正のたびに列が伸びる。
    """
    payload = event["payload"]
    work_order = state["work_orders"][payload["wo_id"]]
    recorded = {
        "cl_ref": payload["cl_ref"],
        "repo": payload["repo"],
        "role": payload["role"],
        "recorded_by": event["actor"],
        "recorded_at": event["ts"],
    }
    kept = [entry for entry in work_order.get("cls", []) if entry["cl_ref"] != payload["cl_ref"]]
    work_order["cls"] = kept + [recorded]
    work_order["updated_at"] = event["ts"]


def _apply_forgotten(state, event):
    payload = event["payload"]
    work_order = state["work_orders"][payload["wo_id"]]
    work_order["cls"] = [
        entry for entry in work_order["cls"] if entry["cl_ref"] != payload["cl_ref"]
    ]
    work_order["updated_at"] = event["ts"]


EVENT_RULES = {
    "cl_recorded": EventRule(_require_recordable, _apply_recorded),
    "cl_forgotten": EventRule(_require_forgettable, _apply_forgotten),
}


# --- 紐づきの供給 (CLLink port) -------------------------------------------------


class LedgerCLLinks(ports.CLLinkPort):
    """台帳の CL 記録を紐づきとして供給する (`CLLinkPort` の台帳実装)。

    **tracker を一切触らない。** issue 置き場が紐づきを引けない project で、reconciler が
    tracker adapter の代わりに挿す。
    """

    #: 記録 0 件は「その issue に CL は無い」の証拠にならない — 誰もまだ記録していないだけ
    #: かもしれない。`None` を返す代わりにこの宣言で terminal の確定だけを止める (ADR 0059)。
    absence_is_evidence = False

    def __init__(self, book):
        self._book = book

    def fetch_cl_links(self, issue_ref, *, repo):
        """その issue の非終端 WorkOrder が記録している紐づき。記録が無ければ `[]`。

        **`repo` は使わない** — 記録は CL ごとに自分の repo を持っており、issue 置き場の
        識別子で絞ると別 repo の正当な closes が消える (`CLLinkPort` の契約どおり)。
        引数に残すのは port の署名だから。
        """
        return recorded_cl_links(self._book.state, issue_ref)


def recorded_cl_links(state, issue_ref):
    """issue の非終端 WorkOrder が記録している紐づき (`ports.cl_link` の列)。無ければ `[]`。

    **`None` を返さない。** 記録 0 件は観測としては 0 件で、その 0 件を terminal の根拠に
    してよいかは `LedgerCLLinks.absence_is_evidence` が答える (module docstring)。

    非終端の WorkOrder が無い issue も `[]`。終端の WorkOrder が持つ記録を紐づきとして返すと、
    再着手で切り直した WorkOrder が前回の CL を根拠に終わる。
    """
    work_order = _open_work_order_for(state, issue_ref)
    if work_order is None:
        return []
    return [
        ports.cl_link(cl_ref=entry["cl_ref"], repo=entry["repo"], role=entry["role"])
        for entry in work_order.get("cls") or []
    ]


def _open_work_order_for(state, issue_ref):
    """その issue の非終端 WorkOrder。**`fold.open_work_order_for` と同じ導出の局所実装**。

    `fold` は本 module の `EVENT_RULES` を合成する側なので、こちらから import すると循環する
    (`fold_core` を分けてあるのと同じ理由)。
    """
    for work_order in state["work_orders"].values():
        if (
            work_order["issue_ref"] == issue_ref
            and work_order["phase"] not in vocabulary.TERMINAL_PHASES
        ):
            return work_order
    return None
