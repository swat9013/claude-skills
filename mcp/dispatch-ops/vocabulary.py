"""dispatch-ops MCP server の中立語彙と phase 状態機械の正本。

spec §3.3 (phase) と §4.3 (中立語彙) に対応する。tracker 固有の語彙 (GitHub の
`MERGEABLE` 等) はここに入れない — adapter が本 module の値へ写像する責務を持つ
(spec §2「adapter の責務は語彙の写像のみ」)。

方針は既存の stage_vocabulary / outcome_vocabulary と同型: 語彙の正本を単一 module に
置き、整合検査を import 時に走らせて、どの実行経路でも最初の import で落とす。
"""

import string

# --- phase (spec §3.3) -------------------------------------------------------

PHASES = (
    "claimed",  # assignee 設定済み、pane 未起動
    "active",  # pane 稼働中 (worktree あり)
    "parked",  # pane を降ろし worktree と claim を保持 (駐機)
    "done",  # issue closed / PR merged を観測
    "cleaned",  # worktree / branch 回収済み (終端)
    "released",  # unclaim して候補プールへ返却 (終端)
    "spawn_failed",  # 起動失敗 (終端。再試行は新規 entry)
)

INITIAL_PHASE = "claimed"

# 終端 phase。ここから先へは遷移しない — 同じ issue を再度 dispatch するときは
# 新規 entry を起こす (spec §3.3 の spawn_failed 註)。
TERMINAL_PHASES = frozenset({"cleaned", "released", "spawn_failed"})

# 合法遷移表。**遷移するかどうかの判断は LLM、合法性の検証だけが server** (spec §3.3)。
# 表に無い遷移を拒む目的は「段階の飛び越し」を止めることであって、運用上ありうる
# 観測を否定することではない。各行の根拠:
#
# - claimed → active / spawn_failed / released: spec §3.3 の図そのまま
# - claimed → done: pane を起動する前に別経路で issue が closed になる場合がある。
#   released (候補プールへ返却) は「まだ着手可」の意味なので closed の表現に使えない
# - active → parked / done / released: spec §3.3 の図 + SKILL.md 手順 7.5 行 2-3
#   (pane 消失かつ PR 成果なし → unclaim) が released の経路
# - parked → active: 駐機 worktree への再入 (SKILL.md 手順 7.6 `reenter`)。再入した
#   session は通常の追跡 pane として扱われ、再び駐機しうる (park ⇄ active は循環する)
# - done → cleaned のみ: 「worktree を回収した」は「終わったと観測した」の後にしか
#   来ない。spec §3.3 が名指しで拒む claimed → cleaned の飛び越しはこの 1 行で閉じる
TRANSITIONS = {
    "claimed": ("active", "done", "released", "spawn_failed"),
    "active": ("parked", "done", "released"),
    "parked": ("active", "done", "released"),
    "done": ("cleaned",),
    "cleaned": (),
    "released": (),
    "spawn_failed": (),
}

# 説明散文で使う主系列。docstring / INSTRUCTIONS の「claimed → active → …」行の正本で、
# TRANSITIONS から機械的には導けない (合法遷移の図には分岐も戻りもあり、どれを代表経路と
# 見せるかは読み手向けの選択)。連鎖が合法であることと、主系列に載らない phase が全て終端で
# あることは import 時に検査する。
MAIN_FLOW = ("claimed", "active", "parked", "done", "cleaned")

# --- phase 属性表 (phase 分類の正本) ------------------------------------------

# phase の部分集合 (「pane が居るはずの phase」「worktree を回収してよい phase」等) は
# **すべてこの表から導出する**。module ごとにタプルを直書きすると、phase を 1 つ足したとき
# 既存の部分集合が黙って古いまま通る — `worktree_protected` の漏れは worktree 誤回収
# (未 commit 作業の消失) に直結する。表は 1 phase = 1 行で全軸が必須 key なので、新 phase は
# 全軸の分類を書かない限り import 時に落ちる (fail-closed)。
#
# 各軸の意味。**値が同じでも意味が別の軸は統合しない** — 片方の意味で編集したときにもう
# 片方が黙って変わるのを避ける (`in_flight` と `issue_open` が現にそうなっている):
#
# - `pane_expected`: pane が稼働しているはず。`claimed` は pane 未起動、`parked` は pane を
#   降ろした状態なので、どちらも「pane が居ない」が正常
# - `worktree_expected`: worktree を保持しているはず (spec §3.3 の `active` / `parked`)
# - `issue_open`: issue が open のはず。着手中に closed になっていたら台帳が現実に追いつい
#   ていない
# - `issue_closed`: issue が closed のはず。`done` = issue closed / PR merged を観測した状態。
#   `issue_open` の否定ではない — `released` / `spawn_failed` は issue の状態を主張しない
# - `in_flight`: 作業が進行中とみなす。closes PR の merged を「まだ done へ送っていない」と
#   読む対象。値が `issue_open` と一致するのは今のところ偶然で、意味が別 (issue の状態 /
#   作業の状態)
# - `worktree_protected`: `worktree_tidy` / `worktree_sweep` が回収しない。pane が動いている
#   (`active`) か、worktree と claim を保持したまま降りている (`parked`) 間は触らない
# - `worktree_reclaim`: `worktree_tidy` が回収対象に載せる。squash merge した branch は
#   `branch --merged` に現れないので、この経路が取りこぼしを補う
PHASE_AXES = (
    "pane_expected",
    "worktree_expected",
    "issue_open",
    "issue_closed",
    "in_flight",
    "worktree_protected",
    "worktree_reclaim",
)

# 同時に真になってはいけない軸の組。表の行を書き間違えたときに import 時で落とす
EXCLUSIVE_AXIS_PAIRS = (
    ("issue_open", "issue_closed"),
    ("worktree_protected", "worktree_reclaim"),
)

PHASE_ATTRIBUTES = {
    "claimed": dict(
        pane_expected=False,
        worktree_expected=False,
        issue_open=True,
        issue_closed=False,
        in_flight=True,
        worktree_protected=False,
        worktree_reclaim=False,
    ),
    "active": dict(
        pane_expected=True,
        worktree_expected=True,
        issue_open=True,
        issue_closed=False,
        in_flight=True,
        worktree_protected=True,
        worktree_reclaim=False,
    ),
    "parked": dict(
        pane_expected=False,
        worktree_expected=True,
        issue_open=True,
        issue_closed=False,
        in_flight=True,
        worktree_protected=True,
        worktree_reclaim=False,
    ),
    "done": dict(
        pane_expected=False,
        worktree_expected=False,
        issue_open=False,
        issue_closed=True,
        in_flight=False,
        worktree_protected=False,
        worktree_reclaim=True,
    ),
    "cleaned": dict(
        pane_expected=False,
        worktree_expected=False,
        issue_open=False,
        issue_closed=True,
        in_flight=False,
        worktree_protected=False,
        worktree_reclaim=False,
    ),
    "released": dict(
        pane_expected=False,
        worktree_expected=False,
        issue_open=False,
        issue_closed=False,
        in_flight=False,
        worktree_protected=False,
        worktree_reclaim=False,
    ),
    "spawn_failed": dict(
        pane_expected=False,
        worktree_expected=False,
        issue_open=False,
        issue_closed=False,
        in_flight=False,
        worktree_protected=False,
        worktree_reclaim=False,
    ),
}


def phases_where(axis):
    """`axis` が真の phase を PHASES の宣言順で返す。

    順序を `PHASES` 側から取るのは、表の行を並べ替えても導出タプルが動かないようにするため。
    """
    if axis not in PHASE_AXES:
        raise VocabularyError(f"未知の phase 軸: {axis!r} (候補: {', '.join(PHASE_AXES)})")
    return tuple(phase for phase in PHASES if PHASE_ATTRIBUTES[phase][axis])

# --- 中立語彙 (spec §4.3) ----------------------------------------------------

# issue state。未知値は adapter 側で error にする (推測に倒さない)
ISSUE_STATES = ("open", "closed")

# PR status。現行 dispatch_tracker.derive_pr_status の actionable-first 梯子を
# 中立名で踏襲する (#403 で adapter が写像する)
PR_STATUSES = ("open", "conflict", "checking", "merged", "closed", "none")

# closing reference (`Closes #N`) が張られた PR か、本文言及だけか
PR_ROLES = ("closes", "mention")

# agent status。pane 側 (#404) が写像する
AGENT_STATUSES = ("running", "idle", "exited", "gone")

# state.json の agent block が持ちうる key (spec §3.2)。未知 key を拒むのは typo を
# 早期に落とすためで、値の意味には踏み込まない (policy-free)
AGENT_FIELDS = (
    "pane_backend",
    "pane_id",
    "pane_label",
    "worktree",
    "branch",
    "model",
    "effort",
)

# state.json の prs[] entry が持つ key
PR_FIELDS = ("ref", "role", "last_seen_status")


class VocabularyError(ValueError):
    """語彙違反 (未知の phase / status / role / field)。"""


class TransitionError(ValueError):
    """phase 遷移が合法でない。"""


# --- 属性表から導出する phase 部分集合 ----------------------------------------

# 各軸の意味は PHASE_ATTRIBUTES の註を見る。ここで名前を付けるのは呼び出し側で
# `phases_where("...")` の文字列が散らないようにするためで、正本は表のほう。
PANE_EXPECTED_PHASES = phases_where("pane_expected")
WORKTREE_EXPECTED_PHASES = phases_where("worktree_expected")
ISSUE_OPEN_PHASES = phases_where("issue_open")
ISSUE_CLOSED_PHASES = phases_where("issue_closed")
IN_FLIGHT_PHASES = phases_where("in_flight")
PROTECTED_PHASES = phases_where("worktree_protected")
RECLAIM_PHASES = phases_where("worktree_reclaim")


# --- docstring 用の生成断片 ---------------------------------------------------

# LLM 向け interface (tool docstring / INSTRUCTIONS) のうち **phase の列挙と遷移表だけ**を
# ここから生成する。前後の説明散文は人力のまま — 文面の品質は template では担保できない。


def _join(phases):
    return " / ".join(phases)


DOC_FRAGMENTS = {
    # 「claimed → active → parked → done → cleaned (+ released / spawn_failed)」
    "phase_flow": " → ".join(MAIN_FLOW)
    + f" (+ {_join([p for p in PHASES if p not in MAIN_FLOW])})",
    # 「claimed / active / parked / done / cleaned / released / spawn_failed」
    "phase_list": _join(PHASES),
    # 「cleaned / released / spawn_failed」
    "terminal_phases": _join([p for p in PHASES if p in TERMINAL_PHASES]),
    # 「claimed→(active|done|released|spawn_failed) / active→(parked|done|released) / …」
    "transitions": _join(
        f"{source}→({'|'.join(TRANSITIONS[source])})"
        for source in PHASES
        if TRANSITIONS[source]
    ),
    "protected_phases": _join(PROTECTED_PHASES),
    "reclaim_phases": _join(RECLAIM_PHASES),
    # markdown の inline code span 付き。`${protected_phases}` と外から括ると slash ごと
    # 1 span になってしまうので、phase 単位で括った変種を別 key として持つ
    "protected_phases_code": _join(f"`{phase}`" for phase in PROTECTED_PHASES),
    "reclaim_phases_code": _join(f"`{phase}`" for phase in RECLAIM_PHASES),
}


def render_doc(text):
    """`${name}` 置換で phase 語彙を差し込む (docstring / INSTRUCTIONS 用)。

    `str.format` を使わないのは docstring に `{"pane_id": null}` のような literal brace が
    居るから。未知の `${name}` は `KeyError` で落とす (`safe_substitute` にしない) —
    黙って生の template 文字列が LLM へ届くのを避ける。
    """
    return string.Template(text).substitute(DOC_FRAGMENTS)


def with_rendered_doc(func):
    """`__doc__` の `${name}` を解決する decorator。`@server.tool()` より内側に置く。"""
    func.__doc__ = render_doc(func.__doc__)
    return func


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
    if to not in allowed:
        if current in TERMINAL_PHASES:
            raise TransitionError(
                f"{current} は終端 phase なので {to} へ遷移できない "
                "(同じ issue を再 dispatch するなら新規 entry を起こす)"
            )
        if current == to:
            raise TransitionError(f"{current} から同じ phase への遷移は無効")
        raise TransitionError(
            f"{current} → {to} は不正な遷移 (合法: {', '.join(allowed) or 'なし'})"
        )
    return to


def _require_import_time_consistency():
    """語彙表の整合を import 時に検証する (不整合は ValueError で即死)。"""
    missing = set(PHASES) - set(TRANSITIONS)
    extra = set(TRANSITIONS) - set(PHASES)
    if missing or extra:
        raise ValueError(
            "vocabulary.TRANSITIONS: phase 語彙が PHASES と不一致 "
            f"(missing={sorted(missing)}, extra={sorted(extra)})"
        )
    for source, targets in TRANSITIONS.items():
        unknown = set(targets) - set(PHASES)
        if unknown:
            raise ValueError(
                f"vocabulary.TRANSITIONS[{source!r}]: 未知の遷移先 {sorted(unknown)}"
            )
    for phase in TERMINAL_PHASES:
        if phase not in PHASES:
            raise ValueError(f"vocabulary.TERMINAL_PHASES: 未知の phase {phase!r}")
        if TRANSITIONS[phase]:
            raise ValueError(
                f"vocabulary.TERMINAL_PHASES: {phase!r} は終端なのに遷移先を持つ"
            )
    if INITIAL_PHASE not in PHASES:
        raise ValueError(f"vocabulary.INITIAL_PHASE: 未知の phase {INITIAL_PHASE!r}")
    _require_attribute_table_covers_every_phase()
    _require_main_flow_is_a_legal_chain()


def _require_attribute_table_covers_every_phase():
    """属性表の網羅性 (行 × 軸) を検証する。phase 追加を fail-closed にする本体。"""
    missing = set(PHASES) - set(PHASE_ATTRIBUTES)
    extra = set(PHASE_ATTRIBUTES) - set(PHASES)
    if missing or extra:
        raise ValueError(
            "vocabulary.PHASE_ATTRIBUTES: phase 語彙が PHASES と不一致 "
            f"(missing={sorted(missing)}, extra={sorted(extra)})"
        )
    for phase, row in PHASE_ATTRIBUTES.items():
        unset = set(PHASE_AXES) - set(row)
        unknown = set(row) - set(PHASE_AXES)
        if unset or unknown:
            raise ValueError(
                f"vocabulary.PHASE_ATTRIBUTES[{phase!r}]: 軸が不一致 "
                f"(未分類={sorted(unset)}, 未知={sorted(unknown)})"
            )
        for axis in PHASE_AXES:
            if not isinstance(row[axis], bool):
                raise ValueError(
                    f"vocabulary.PHASE_ATTRIBUTES[{phase!r}][{axis!r}]: bool でない "
                    f"({row[axis]!r})"
                )
        for left, right in EXCLUSIVE_AXIS_PAIRS:
            if row[left] and row[right]:
                raise ValueError(
                    f"vocabulary.PHASE_ATTRIBUTES[{phase!r}]: "
                    f"{left} と {right} は同時に真にできない"
                )


def _require_main_flow_is_a_legal_chain():
    """主系列が合法遷移の連鎖であり、系列外の phase が全て終端であることを検証する。"""
    for source, target in zip(MAIN_FLOW, MAIN_FLOW[1:]):
        if source not in PHASES or target not in PHASES:
            raise ValueError(f"vocabulary.MAIN_FLOW: 未知の phase {source!r} → {target!r}")
        if target not in TRANSITIONS[source]:
            raise ValueError(
                f"vocabulary.MAIN_FLOW: {source} → {target} は合法遷移でない"
            )
    offshoots = set(PHASES) - set(MAIN_FLOW) - TERMINAL_PHASES
    if offshoots:
        raise ValueError(
            f"vocabulary.MAIN_FLOW: 主系列にも終端にも属さない phase {sorted(offshoots)} "
            "(説明散文がその phase に触れないまま通ってしまう)"
        )


_require_import_time_consistency()
