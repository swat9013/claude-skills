"""WorkOrder の phase 状態機械の正本 (意図のみ 5 値)。

設計正本 `docs/design/dispatch-v2/system.md`「4. 状態機械」/ ADR 0055。**phase は意図しか持たない** —
v1 の `active` は Session 生死から、`cleaned` は Worktree 有無から導出する値なので、
状態として持たない。意図と観測事実を同じ軸に混ぜたことが v1 の drift の出所だった。

整合検査は `require_vocabulary_consistency` が表を引数で受け取る形にしてあり、module 末尾で
自分の表を渡して import 時に走らせる。**引数で受ける形にしたのは検査そのものを負の表で
検証できるようにするため** — module の global を直接読む検査は「壊れた表」を作れない。
"""

import string

# --- phase 語彙 --------------------------------------------------------------

PHASES = (
    "assigned",  # 着手を割り当てた (worker が動いているかは Session 側の事実)
    "parked",  # 駐機。判断待ちで手を離したが、まだ返していない
    "completed",  # closes CL の merge を経て決着した (終端)
    "released",  # 候補プールへ返した = まだ着手可 (終端)
    "abandoned",  # merge を伴わずに閉じた / 取り下げた (終端)
)

INITIAL_PHASE = "assigned"

# 終端 phase。**逆行遷移は設けない** — 「terminal を見たら資源回収してよい」という機械 rule の
# 前提を守るため (ADR 0055)。誤分類の訂正は annotate + 新 WorkOrder で表す
TERMINAL_PHASES = frozenset({"completed", "released", "abandoned"})

# 合法遷移表。遷移するかどうかの判断は LLM、合法性の検証だけが機械側 (policy-free を踏襲)。
# `assigned ⇄ parked` は循環し、非終端のどちらからも 3 つの終端へ落ちる
TRANSITIONS = {
    "assigned": ("parked", "completed", "released", "abandoned"),
    "parked": ("assigned", "completed", "released", "abandoned"),
    "completed": (),
    "released": (),
    "abandoned": (),
}

# LLM 向け interface に載せる 1 行説明。**terminal の使い分けはここでしか伝わらない** —
# `released` を close 済み issue に使うと候補プールへ戻り、二重 dispatch になる
PHASE_MEANINGS = {
    "assigned": "着手を割り当てた (worker の生死は Session 側の事実で、phase は動かない)",
    "parked": "駐機。判断待ちで手を離したが候補プールへは返していない",
    "completed": "closes CL の merge を経て決着した",
    "released": "候補プールへ返した (まだ着手可。close 済み issue には使わない)",
    "abandoned": "merge を伴わずに閉じた / 取り下げた",
}

# 説明散文の主系列。TRANSITIONS から機械的には導けない (どれを代表経路と見せるかは読み手
# 向けの選択) ので literal で置き、全 phase が現れることを import 時に検査する
PHASE_FLOW_TEXT = "assigned ⇄ parked → completed | released | abandoned"


class VocabularyError(ValueError):
    """語彙違反 (未知の phase)。"""


class TransitionError(ValueError):
    """phase 遷移が合法でない。"""


def require_phase(phase):
    """phase が語彙内であることを検証して返す。"""
    if phase not in PHASES:
        raise VocabularyError(f"未知の phase: {phase!r} (候補: {', '.join(PHASES)})")
    return phase


def validate_transition(current, to):
    """current → to が合法なら to を返し、そうでなければ TransitionError。"""
    require_phase(current)
    require_phase(to)
    allowed = TRANSITIONS[current]
    if to in allowed:
        return to
    if current in TERMINAL_PHASES:
        raise TransitionError(
            f"{current} は終端 phase なので {to} へ遷移できない "
            "(同じ issue へ再着手するなら新しい WorkOrder を切る。系譜は issue_ref で辿れる)"
        )
    if current == to:
        raise TransitionError(
            f"{current} から同じ phase への遷移は無効 "
            "(phase を変えずに note を更新するなら wo_annotate)"
        )
    raise TransitionError(f"{current} → {to} は不正な遷移 (合法: {', '.join(allowed) or 'なし'})")


# --- docstring 用の生成断片 ---------------------------------------------------
# LLM 向け interface (tool docstring / server INSTRUCTIONS) のうち **phase の列挙と遷移表
# だけ**を生成する。前後の説明散文は人力のまま — 文面の品質は template では担保できない


def _join(items):
    return " / ".join(items)


DOC_FRAGMENTS = {
    "phase_flow": PHASE_FLOW_TEXT,
    "phase_list": _join(PHASES),
    "terminal_phases": _join(phase for phase in PHASES if phase in TERMINAL_PHASES),
    "transitions": _join(
        f"{source}→({'|'.join(TRANSITIONS[source])})" for source in PHASES if TRANSITIONS[source]
    ),
    "phase_meanings": _join(f"{phase}: {PHASE_MEANINGS[phase]}" for phase in PHASES),
}


def render_doc(text):
    """`${name}` 置換で phase 語彙を差し込む (docstring / INSTRUCTIONS 用)。

    `str.format` を使わないのは docstring に `{"note": null}` のような literal brace が
    居るから。未知の `${name}` は `KeyError` で落とす (`safe_substitute` にしない) —
    黙って生の template 文字列が LLM へ届くのを避ける。
    """
    return string.Template(text).substitute(DOC_FRAGMENTS)


def with_rendered_doc(func):
    """`__doc__` の `${name}` を解決する decorator。`@server.tool()` より内側に置く。"""
    func.__doc__ = render_doc(func.__doc__)
    return func


# --- 語彙表の整合検査 (import 時 fail-closed) ---------------------------------


def require_vocabulary_consistency(*, phases, initial_phase, terminal_phases, transitions, meanings, flow_text):
    """phase 語彙の網羅性・整合を検査する。不整合は ValueError で即死。

    phase を 1 つ足したときに、遷移表・終端宣言・説明・散文のどれかを書き忘れたまま通るのを
    止めるのが目的。**「非終端なのに遷移先が空」を落とす**のが要点で、これが無いと終端の
    宣言を書き忘れた phase が黙って終端として振る舞う (= 資源回収の前提が崩れる)。
    """
    missing = set(phases) - set(transitions)
    extra = set(transitions) - set(phases)
    if missing or extra:
        raise ValueError(
            f"TRANSITIONS: phase 語彙と不一致 (missing={sorted(missing)}, extra={sorted(extra)})"
        )
    for source, targets in transitions.items():
        unknown = set(targets) - set(phases)
        if unknown:
            raise ValueError(f"TRANSITIONS[{source!r}]: 未知の遷移先 {sorted(unknown)}")
        if source in terminal_phases and targets:
            raise ValueError(f"TERMINAL_PHASES: {source!r} は終端なのに遷移先を持つ")
        if source not in terminal_phases and not targets:
            raise ValueError(
                f"TRANSITIONS[{source!r}]: 非終端なのに遷移先が空 "
                "(終端なら TERMINAL_PHASES へ宣言する)"
            )
    unknown_terminal = set(terminal_phases) - set(phases)
    if unknown_terminal:
        raise ValueError(f"TERMINAL_PHASES: 未知の phase {sorted(unknown_terminal)}")
    if initial_phase not in phases:
        raise ValueError(f"INITIAL_PHASE: 未知の phase {initial_phase!r}")
    if initial_phase in terminal_phases:
        raise ValueError(f"INITIAL_PHASE: {initial_phase!r} が終端 (記帳した瞬間に終わる)")
    undescribed = set(phases) - set(meanings)
    undeclared = set(meanings) - set(phases)
    if undescribed or undeclared:
        raise ValueError(
            f"PHASE_MEANINGS: phase 語彙と不一致 "
            f"(未記載={sorted(undescribed)}, 未知={sorted(undeclared)})"
        )
    absent_from_prose = [phase for phase in phases if phase not in flow_text]
    if absent_from_prose:
        raise ValueError(f"PHASE_FLOW_TEXT: 散文が触れていない phase {absent_from_prose}")
    _require_every_phase_is_reachable(phases, initial_phase, transitions)


def _require_every_phase_is_reachable(phases, initial_phase, transitions):
    """initial から到達できない phase を落とす (語彙にあるのに使えない phase を作らない)。"""
    reached = {initial_phase}
    frontier = [initial_phase]
    while frontier:
        for target in transitions[frontier.pop()]:
            if target not in reached:
                reached.add(target)
                frontier.append(target)
    unreachable = set(phases) - reached
    if unreachable:
        raise ValueError(
            f"TRANSITIONS: {initial_phase!r} から到達できない phase {sorted(unreachable)}"
        )


require_vocabulary_consistency(
    phases=PHASES,
    initial_phase=INITIAL_PHASE,
    terminal_phases=TERMINAL_PHASES,
    transitions=TRANSITIONS,
    meanings=PHASE_MEANINGS,
    flow_text=PHASE_FLOW_TEXT,
)
