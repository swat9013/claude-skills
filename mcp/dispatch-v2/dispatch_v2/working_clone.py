"""稼働 clone (dispatch が動いている checkout) の観測。

**宣言 config ではなく event log に置く** — 人が宣言する運用方針ではなく、cwd を知っている
MCP server が解決して daemon へ渡す観測事実だから (ADR 0057 の「観測事実の変化も同じ log へ
書く」)。daemon はマシンに 1 プロセスで複数 project を抱えるので、ADR 0047 が observer を
deploy の実行者に選んだ根拠 (「導出なしに正しい場所に既に居る」) は v2 では成立しない。

worktree 集約とは**変わる理由が違う** (あちらは WorkOrder が所有するツリーの規則、こちらは
project がどの checkout の上で動いているかの観測) ので module を分けてある。
"""

from dispatch_v2.fold_core import EventRule, require_field


def empty_state():
    """稼働 clone の初期 state (`fold.empty_state` が合成する)。"""
    return {"clone_path": None}


def _require_observable(_state, event):
    require_field(event["payload"], "clone_path")


def _apply_observed(state, event):
    # **path が変わることは異常ではない** (clone を移した / 別 clone から dispatch した)。
    # 観測事実なので最後の観測が現況で、変遷そのものは event log が持つ
    state["clone_path"] = event["payload"]["clone_path"]


EVENT_RULES = {
    "project_clone_observed": EventRule(_require_observable, _apply_observed),
}
