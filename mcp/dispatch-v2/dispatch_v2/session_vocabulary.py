"""Session の lifecycle (事実) と activity (観測) の語彙の正本。

設計正本 `docs/design/dispatch-v2/system.md` §4 / ADR 0055。**WorkOrder の phase (意図) とは別軸**
なので `vocabulary.py` と表を分けてある — 混ぜると「Session が死んだから WorkOrder も終わり」
という v1 の drift がそのまま戻る。

- lifecycle は**システムの事実**: requested → starting → alive → ended(reason)。起動に失敗した
  Session も ended として台帳に残り、WorkOrder は assigned のまま再発行できる
- activity は **alive の間の観測**: running / idle / blocked。**runtime の生値を併記して潰さない**
  (v1 で blocked が running に潰れていた — 設計 決定 17)
- **runtime 固有の生値 (herdr の `agent_status` 等) の写像表はここに持たない。** 生値 → activity
  の対応は runtime ごとに違うので adapter 側が持ち、本 module は中立 3 値の正本だけを持つ
  (runtime 語彙を台帳の語彙へ染ませない = 設計 決定 5 / 13)

整合検査を引数で受ける形にしてあるのは `vocabulary.py` と同じ理由 — 検査そのものを壊れた表で
検証できるようにするため。
"""

import string

# --- lifecycle (システムの事実) ------------------------------------------------

LIFECYCLES = (
    "requested",  # 起動を依頼した (runtime にはまだ何も無い)
    "starting",  # runtime が handle を作った (agent はまだ検出できていない)
    "alive",  # agent が動いている
    "ended",  # 終わった (理由は ended.reason が持つ)
)

INITIAL_LIFECYCLE = "requested"

# 終端。**Session の終端は資源回収の前提**なので逆行させない (WorkOrder の terminal と同じ理屈)。
# 同じ WorkOrder で作業を続けるなら新しい Session を切り、系譜は WorkOrder の sessions 列に出る
TERMINAL_LIFECYCLES = frozenset({"ended"})

LIFECYCLE_TRANSITIONS = {
    "requested": ("starting", "ended"),
    "starting": ("alive", "ended"),
    "alive": ("ended",),
    "ended": (),
}

LIFECYCLE_FLOW_TEXT = "requested → starting → alive → ended(reason) (launch_error は requested から直接 ended)"

# --- ended.reason (終わり方の 4 値) --------------------------------------------

ENDED_REASONS = (
    "exited",  # handle は在るが agent が居ない (worker のセッションが終了した)
    "gone",  # handle ごと消えた (pane が閉じられた / runtime が落とした)
    "closed_by_orchestrator",  # orchestrator の指示で閉じた
    "launch_error",  # 起動そのものが失敗した (WorkOrder は assigned のまま再発行できる)
)

# **どの lifecycle からどの reason で終われるか**。`exited` と `gone` は「handle は在るが agent が
# 居ない」「handle ごと消えた」という**別の観測**で、一方しか観測できない port を書くと 2 値が
# 1 値に潰れる。`launch_error` を alive から許さないのは、一度生きた Session の死を起動失敗と
# 呼ぶと系譜 (再発行の根拠) が読めなくなるため
ENDED_REASONS_BY_LIFECYCLE = {
    "requested": ("launch_error",),
    "starting": ("launch_error", "exited", "gone", "closed_by_orchestrator"),
    "alive": ("exited", "gone", "closed_by_orchestrator"),
}

ENDED_REASON_MEANINGS = {
    "exited": "runtime handle は在るが agent が居ない (worker のセッションが終了した)",
    "gone": "runtime handle ごと消えた (pane が閉じられた / runtime が落とした)",
    "closed_by_orchestrator": (
        "dispatch 側の指示で閉じた (session_close か、有効化された opt-in rule。"
        "どちらかは event の actor で読む)"
    ),
    "launch_error": "起動そのものが失敗した (WorkOrder は assigned のまま再発行できる)",
}

# --- activity (alive の間の観測) ------------------------------------------------

ACTIVITIES = (
    "running",  # 作業が進んでいる
    "idle",  # 手が空いている (駐機の契機)
    "blocked",  # 進めずに待っている (permission / 質問待ち)
)

ACTIVITY_MEANINGS = {
    "running": "作業が進んでいる",
    "idle": "手が空いている (駐機の契機)",
    "blocked": "進めずに待っている (permission 待ち / 質問待ち)",
}

# 生値を中立 3 値へ落とせなかったときの activity。**running へ寄せない** — 未知の生値が
# 黙って「動いている」に化けると、blocked を潰さないという保証が形だけになる。
# 生値そのものは常に併記されるので、読み手は未分類のまま原因に辿り着ける
UNCLASSIFIED_ACTIVITY = None


class SessionVocabularyError(ValueError):
    """語彙違反 (未知の lifecycle / ended.reason / activity)。"""


class LifecycleError(ValueError):
    """lifecycle 遷移が合法でない / その lifecycle では起こりえない終わり方。"""


def require_lifecycle(lifecycle):
    """lifecycle が語彙内であることを検証して返す。"""
    if lifecycle not in LIFECYCLES:
        raise SessionVocabularyError(
            f"未知の lifecycle: {lifecycle!r} (候補: {', '.join(LIFECYCLES)})"
        )
    return lifecycle


def require_activity(activity):
    """activity が語彙内であることを検証して返す。"""
    if activity not in ACTIVITIES:
        raise SessionVocabularyError(
            f"未知の activity: {activity!r} (候補: {', '.join(ACTIVITIES)})"
        )
    return activity


def validate_lifecycle(current, to):
    """current → to が合法なら to を返し、そうでなければ LifecycleError。"""
    require_lifecycle(current)
    require_lifecycle(to)
    allowed = LIFECYCLE_TRANSITIONS[current]
    if to in allowed:
        return to
    if current in TERMINAL_LIFECYCLES:
        raise LifecycleError(
            f"{current} は終端の lifecycle なので {to} へ遷移できない "
            "(同じ WorkOrder で続けるなら新しい Session を切る)"
        )
    raise LifecycleError(
        f"{current} → {to} は不正な遷移 (合法: {', '.join(allowed) or 'なし'})"
    )


def require_ended_reason(current, reason):
    """current から ended へ落ちるときの reason を検証して返す。"""
    require_lifecycle(current)
    if reason not in ENDED_REASONS:
        raise SessionVocabularyError(
            f"未知の ended.reason: {reason!r} (候補: {', '.join(ENDED_REASONS)})"
        )
    allowed = ENDED_REASONS_BY_LIFECYCLE.get(current, ())
    if reason not in allowed:
        raise LifecycleError(
            f"{current} の Session が {reason} で終わることはない "
            f"(合法: {', '.join(allowed) or 'なし'})"
        )
    return reason


# --- 語彙表の整合検査 (import 時 fail-closed) ---------------------------------


def require_vocabulary_consistency(
    *,
    lifecycles,
    initial_lifecycle,
    terminal_lifecycles,
    transitions,
    ended_reasons,
    reasons_by_lifecycle,
    activities,
):
    """lifecycle / ended.reason / activity の表の網羅性・整合を検査する。不整合は即死。

    **どの lifecycle からも到達できない ended.reason を落とす**のが要点 — reason を 1 つ足して
    生産者 (観測経路) を書き忘れると、語彙にはあるが誰も記帳しない値が残り、「4 値が観測から
    記帳される」という保証が表の上だけになる。
    """
    _require_lifecycle_graph(lifecycles, initial_lifecycle, terminal_lifecycles, transitions)
    unknown_sources = set(reasons_by_lifecycle) - set(lifecycles)
    if unknown_sources:
        raise ValueError(f"ENDED_REASONS_BY_LIFECYCLE: 未知の lifecycle {sorted(unknown_sources)}")
    for source, reasons in reasons_by_lifecycle.items():
        unknown = set(reasons) - set(ended_reasons)
        if unknown:
            raise ValueError(
                f"ENDED_REASONS_BY_LIFECYCLE[{source!r}]: 未知の ended.reason {sorted(unknown)}"
            )
        if "ended" not in transitions.get(source, ()):
            raise ValueError(
                f"ENDED_REASONS_BY_LIFECYCLE[{source!r}]: ended へ遷移できない lifecycle に "
                "reason が宣言されている"
            )
    produced = {reason for reasons in reasons_by_lifecycle.values() for reason in reasons}
    unreachable = set(ended_reasons) - produced
    if unreachable:
        raise ValueError(
            f"ENDED_REASONS: どの lifecycle からも起こらない reason {sorted(unreachable)} "
            "(観測の出所を ENDED_REASONS_BY_LIFECYCLE へ宣言する)"
        )
    if not activities:
        raise ValueError("ACTIVITIES: 空の語彙")


def _require_lifecycle_graph(lifecycles, initial_lifecycle, terminal_lifecycles, transitions):
    missing = set(lifecycles) - set(transitions)
    extra = set(transitions) - set(lifecycles)
    if missing or extra:
        raise ValueError(
            f"LIFECYCLE_TRANSITIONS: 語彙と不一致 (missing={sorted(missing)}, extra={sorted(extra)})"
        )
    for source, targets in transitions.items():
        unknown = set(targets) - set(lifecycles)
        if unknown:
            raise ValueError(f"LIFECYCLE_TRANSITIONS[{source!r}]: 未知の遷移先 {sorted(unknown)}")
        if source in terminal_lifecycles and targets:
            raise ValueError(f"TERMINAL_LIFECYCLES: {source!r} は終端なのに遷移先を持つ")
        if source not in terminal_lifecycles and not targets:
            raise ValueError(
                f"LIFECYCLE_TRANSITIONS[{source!r}]: 非終端なのに遷移先が空 "
                "(終端なら TERMINAL_LIFECYCLES へ宣言する)"
            )
    if initial_lifecycle not in lifecycles:
        raise ValueError(f"INITIAL_LIFECYCLE: 未知の lifecycle {initial_lifecycle!r}")
    reached = {initial_lifecycle}
    frontier = [initial_lifecycle]
    while frontier:
        for target in transitions[frontier.pop()]:
            if target not in reached:
                reached.add(target)
                frontier.append(target)
    unreachable = set(lifecycles) - reached
    if unreachable:
        raise ValueError(
            f"LIFECYCLE_TRANSITIONS: {initial_lifecycle!r} から到達できない lifecycle "
            f"{sorted(unreachable)}"
        )


# --- docstring 用の生成断片 ---------------------------------------------------
# LLM 向け interface (tool docstring) の**語彙の列挙だけ**を生成する。散文で書き写すと
# 語彙を足したときに tool の説明だけが古いまま残る (`vocabulary.py` と同じ理由)

def _as_list(names, meanings):
    """1 値 1 行の箇条書き。**1 行に連ねない** — tool description は人も LLM も読む面で、
    4 値の説明を `/` で連結すると 1 行が数百文字になって読めなくなる。"""
    return "".join(f"\n      - {name}: {meanings[name]}" for name in names)


DOC_FRAGMENTS = {
    "lifecycle_flow": LIFECYCLE_FLOW_TEXT,
    "ended_reasons": _as_list(ENDED_REASONS, ENDED_REASON_MEANINGS),
    "activities": _as_list(ACTIVITIES, ACTIVITY_MEANINGS),
}


def with_rendered_doc(func):
    """`__doc__` の `${name}` を Session 語彙で解決する decorator (`@server.tool()` の内側)。

    WorkOrder 側 (`vocabulary.with_rendered_doc`) と表を混ぜないのは、**2 つの語彙が別の理由で
    変わる**から (`principle-localize-change-impact`)。未知の `${name}` は `KeyError` で落として、
    生の template が LLM へ届くのを避ける。
    """
    func.__doc__ = string.Template(func.__doc__).substitute(DOC_FRAGMENTS)
    return func


require_vocabulary_consistency(
    lifecycles=LIFECYCLES,
    initial_lifecycle=INITIAL_LIFECYCLE,
    terminal_lifecycles=TERMINAL_LIFECYCLES,
    transitions=LIFECYCLE_TRANSITIONS,
    ended_reasons=ENDED_REASONS,
    reasons_by_lifecycle=ENDED_REASONS_BY_LIFECYCLE,
    activities=ACTIVITIES,
)
