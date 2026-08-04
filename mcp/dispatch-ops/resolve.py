"""台帳と外部 store の join と drift 検出 (spec §3.5 / §4.4 の `resolve`)。

外部 store (tracker / pane / git) が「現実」、台帳は「意図と記録」。本 module は両者を
突き合わせて食い違い (drift) を機械的に列挙するだけで、**解消の判断は持たない** — 台帳を
現実へ合わせるか現実を直すか (pane 再起動・worktree 回収) は LLM が毎回決める。drift record に
`suggested_action` の類を足さないこと。そこに判断が滲むと server がポリシーを持ってしまう。

join の鍵は `i<N>` → 中立 ref の持ち上げ。`observe_panes` / `observe_worktrees` は tracker を
持たない `issue_number` までしか返せない (spec §4.4 の #404 / #405 註) ので、「1 repo = 1
tracker」を知っている本 module が `refs.format_issue_ref` で ref へ写す。台帳 entry のうち
server の tracker と食い違うもの・番号を持たないもの (jira) は写せないので、**黙って落とさず
`unjoinable` として報告する** — `derive_tidy_scope` の `unmappable` と同じ理由 (見えない取り
こぼしは「保護したつもりが消える」に化ける)。

観測できなかったことを「観測して無かった」と混同しない (`blocked: null` / `dirty: null` と同じ規則):

- store 単位: `items` が None なら未観測。その store 由来の drift は 1 件も出さない
- entry 単位: `checked` が false の側面は判定していない。`observed` の null と読み分ける

外部 store の観測 (`observe_stores`) も本 module が組み立てる。**port は引数で受け取り、本 module は
port を生成しない** — どの backend を立てるかは tool 層の責務。観測する entry の選別 (`_observable_entries`)
と `join` の `checked` 判定が同じ module に居るので、「観測しなかった entry は未検査として扱う」という
噛み合わせを module の外から保証しなくてよい。
"""

import pane as pane_mod
import refs
import tracker as tracker_mod
import vocabulary
import worktree as worktree_mod

# 観測を受け取る store。tracker は issue と PR で取得経路が別なので 2 つに割ってある
STORES = ("issues", "prs", "panes", "worktrees")

# phase の部分集合は vocabulary の属性表から導出したものを使う (直書きすると phase 追加時に
# 黙って古いまま通る)。各軸の意味と「値が同じでも意味が別の軸は統合しない」規約は
# `vocabulary.PHASE_ATTRIBUTES` の註が正本。
# agent が動いていないと見なす中立 status (spec §4.3)
STOPPED_AGENT_STATUSES = ("exited", "gone")

# drift の語彙。kind → (台帳から期待される状態, 観測した状態)。**何をすべきか**は書かない
DRIFT_KINDS = {
    "pane_missing": ("pane が稼働している", "その issue の pane が無い"),
    "pane_id_mismatch": ("台帳の pane_id の pane が稼働している", "同じ label で別 id の pane が稼働している"),
    "agent_stopped": ("pane の agent が稼働している", "agent が終了している"),
    "pane_present": ("pane が居ない", "その issue の pane が稼働している"),
    "worktree_missing": ("台帳の worktree が存在する", "worktree が無い"),
    "worktree_present": ("worktree が回収済み", "worktree が残っている"),
    "issue_closed": ("issue が open", "issue が closed"),
    "issue_open": ("issue が closed", "issue が open"),
    "pr_status_changed": ("台帳に記録した PR status のまま", "PR status が変わっている"),
    "pr_merged": ("closes PR が未 merge", "closes PR が merged"),
    "worktree_untracked": ("台帳に対応する dispatch がある", "台帳に無い worktree が残っている"),
    "pane_untracked": ("台帳に対応する dispatch がある", "台帳に無い pane が稼働している"),
}


class ResolveError(ValueError):
    """join の入力が組み立て規約に合わない (未知の store / 未知の drift kind)。"""


# --- 観測の受け渡し ------------------------------------------------------------------


def observed(items, errors=None):
    """観測できた store。`errors` は entry 単位で失敗した分 (ref → 理由)。"""
    return {"items": items, "error": None, "errors": dict(errors or {})}


def unobserved(error=None):
    """観測しなかった / できなかった store。`error` が None なら意図的に観測していない。"""
    return {"items": None, "error": None if error is None else str(error), "errors": {}}


def empty_observation():
    """全 store 未観測の観測束 (呼び出し側が観測できたものだけ差し替える)。"""
    return {name: unobserved() for name in STORES}


# --- 台帳 entry の選別 ---------------------------------------------------------------


def select_entries(entries, scope_ref=None):
    """scope (`issue_ref` 指定) を台帳 entry へ適用する。

    scope を指定した呼び出しで台帳全件を観測すると、1 件見たいだけで tracker CLI が
    entry 数だけ起動する。絞り込みの規則をここ 1 箇所に置き、観測 (main) と join が
    同じ集合を見るようにする。
    """
    if scope_ref is None:
        return list(entries)
    ref = refs.parse_issue_ref(scope_ref)["ref"]
    return [entry for entry in entries if entry.get("issue_ref") == ref]


def joinability(entry, tracker):
    """台帳 entry → (issue 番号, 写せない理由)。写せるなら理由は None。

    別 tracker の entry を番号だけで照合すると、同じ番号の `i<N>` worktree / pane を
    取り違える。番号を持たない ref (jira) は `i<N>` 規約に写せない。
    """
    issue = entry.get("issue") or {}
    if issue.get("tracker") != tracker:
        return None, f"tracker が {issue.get('tracker')!r} で server の {tracker!r} と違う"
    number = issue.get("number")
    if number is None:
        return None, "番号を持たない ref なので i<N> 規約に写せない"
    return number, None


# --- 外部 store の観測 ----------------------------------------------------------------


def observe_stores(entries, *, tracker_port, pane_port, worktree_port, include_prs):
    """3 つの port を観測して `join` に渡す観測束を組み立てる。

    port は引数で受ける — 生成 (どの backend / tracker を立てるか) は tool 層の責務で、
    本 module は渡された port を使うだけ。
    """
    observation = empty_observation()
    observation["panes"] = _observe_store(
        lambda: pane_port.observe_panes()["panes"], pane_mod.PaneError
    )
    observation["worktrees"] = _observe_store(
        lambda: worktree_port.observe()["worktrees"], worktree_mod.WorktreeError
    )
    observation["issues"], observation["prs"] = _observe_tracker(
        tracker_port, entries, include_prs
    )
    return observation


def _observe_store(fetch, expected):
    """外部 store を 1 つ観測する。落ちても未観測として返す (観測束全体は落とさない)。

    pane backend が居ないセッション (herdr 外) で `observe_panes` が落ちるだけで resolve が
    丸ごと失敗すると、台帳と worktree の突合まで道連れになる。未観測の store 由来の drift は
    `join` が抑止するので、「見ていない」を「無かった」と読ませる危険は無い。
    """
    try:
        return observed(fetch())
    except expected as exc:
        return unobserved(exc)


def _observable_entries(entries, tracker):
    """tracker へ現況を問い合わせる entry (join できて、かつ非終端)。

    終端 (cleaned / released / spawn_failed) は履歴であって現況の突合先が無く、1 件あたり
    CLI が起動する観測を履歴のために撃つ理由が無い。ここで落とした entry は観測束に載らず、
    `join` が `checked` を false にする — 選別と `checked` 判定を同じ module に置くことで、
    両者の噛み合わせが暗黙の約束でなく本 module 内の契約になる。
    """
    return [
        entry
        for entry in entries
        if joinability(entry, tracker)[1] is None
        and entry["phase"] not in vocabulary.TERMINAL_PHASES
    ]


def _observe_tracker(adapter, entries, include_prs):
    """台帳 entry を tracker へ問い合わせる (issue 現況 + 紐づく PR)。

    個別の失敗は entry 単位で記録して他の entry の観測を続ける — 1 件の CLI 失敗で現況
    全体を落とさない。
    """
    issues, prs = {}, {}
    issue_errors, pr_errors = {}, {}
    for entry in _observable_entries(entries, adapter.tracker):
        issue_ref = entry["issue_ref"]
        try:
            issues[issue_ref] = adapter.observe_issue(issue_ref)
        except tracker_mod.TrackerError as exc:
            issue_errors[issue_ref] = str(exc)
        if not include_prs:
            continue
        try:
            prs[issue_ref] = adapter.observe_prs(issue_ref)
        except tracker_mod.TrackerError as exc:
            pr_errors[issue_ref] = str(exc)
    return (
        observed(issues, issue_errors),
        observed(prs, pr_errors) if include_prs else unobserved(),
    )


# --- 突合の入口 -----------------------------------------------------------------------


def resolve(entries, *, tracker_port, pane_port, worktree_port, scope_ref=None, include_prs=True):
    """台帳 entry と外部 store を突き合わせた現況 + drift (tool `resolve` の実体)。

    Args:
        entries: 台帳の全 entry (`scope_ref` の適用は本関数が行う)
        tracker_port / pane_port / worktree_port: 観測に使う port
        scope_ref: 中立 issue ref。渡すとその issue だけを突き合わせる
        include_prs: 紐づく PR も観測する

    絞り込み → 観測 → join を 1 本にまとめてあるのは、観測した集合と join する集合が
    ずれると drift が誤って出るため (同じ `select_entries` の結果を両方へ渡す)。
    """
    selected = select_entries(entries, scope_ref)
    observation = observe_stores(
        selected,
        tracker_port=tracker_port,
        pane_port=pane_port,
        worktree_port=worktree_port,
        include_prs=include_prs,
    )
    return join(tracker_port.tracker, selected, observation, scope_ref=scope_ref)


# --- join ---------------------------------------------------------------------------


def join(tracker, entries, observation, scope_ref=None):
    """台帳 entry と外部 store の観測を突き合わせ、現況と drift を返す。

    Args:
        tracker: server の tracker (`gh` / `glab`)。`i<N>` → 中立 ref の持ち上げに使う
        entries: `select_entries` で絞った台帳 entry の列
        observation: store 名 → `observed()` / `unobserved()` の束
        scope_ref: scope 指定時の中立 issue ref。台帳に無い pane / worktree の検出をその
            issue へ限る (絞った entry 列を台帳の全体と見なして他を全部 orphan にしない)
    """
    unknown = set(observation) - set(STORES)
    if unknown:
        raise ResolveError(f"未知の store: {sorted(unknown)} (既知: {', '.join(STORES)})")

    scope, scope_number = _scope(tracker, scope_ref)

    panes = observation["panes"]["items"]
    worktrees = observation["worktrees"]["items"]
    issues = observation["issues"]["items"]
    prs = observation["prs"]["items"]

    panes_by_number = _index_by_number(panes, _pane_number)
    worktrees_by_number = _index_by_number(worktrees, lambda item: item.get("issue_number"))

    current, drift, unjoinable, joined_numbers = [], [], [], set()
    for entry in entries:
        issue_ref = entry.get("issue_ref")
        number, reason = joinability(entry, tracker)
        if reason is not None:
            unjoinable.append(
                {"issue_ref": issue_ref, "phase": entry.get("phase"), "reason": reason}
            )
        else:
            joined_numbers.add(number)
        view = _entry_view(
            entry,
            reason=reason,
            issue=(issues or {}).get(issue_ref),
            issue_checked=issues is not None and issue_ref in issues,
            prs=(prs or {}).get(issue_ref),
            prs_checked=prs is not None and issue_ref in prs,
            pane=panes_by_number.get(number) if panes is not None else None,
            pane_checked=panes is not None and reason is None,
            worktree=worktrees_by_number.get(number) if worktrees is not None else None,
            worktree_checked=worktrees is not None and reason is None,
        )
        current.append(view)
        drift.extend(_entry_drift(view))

    drift.extend(
        _untracked_drift(
            tracker, panes_by_number, worktrees_by_number, joined_numbers, scope, scope_number
        )
    )

    return {
        "tracker": tracker,
        "scope": scope,
        "stores": {
            name: {
                "observed": observation[name]["items"] is not None,
                "error": observation[name]["error"],
                "errors": observation[name]["errors"],
            }
            for name in STORES
        },
        "count": len(current),
        "current": current,
        "drift_count": len(drift),
        "drift": drift,
        "unjoinable": unjoinable,
    }


# --- entry の現況 --------------------------------------------------------------------


def _entry_view(
    entry,
    *,
    reason,
    issue,
    issue_checked,
    prs,
    prs_checked,
    pane,
    pane_checked,
    worktree,
    worktree_checked,
):
    """台帳 entry 1 件 → 台帳側 (`ledger`) と観測側 (`observed`) を並べた現況。

    `observed` の null は「無い」か「見ていない」かが単体では読めないので、必ず
    `checked` と対で読む (checked が false のときは判定していない)。
    """
    return {
        "issue_ref": entry.get("issue_ref"),
        "phase": entry.get("phase"),
        "ledger": {
            "issue": entry.get("issue"),
            "agent": entry.get("agent"),
            "prs": entry.get("prs"),
            "outcome": entry.get("outcome"),
            "note": entry.get("note"),
            "updated_at": entry.get("updated_at"),
        },
        "observed": {"issue": issue, "prs": prs, "pane": pane, "worktree": worktree},
        "checked": {
            "issue": issue_checked,
            "prs": prs_checked,
            "pane": pane_checked,
            "worktree": worktree_checked,
        },
        "unjoinable_reason": reason,
    }


# --- drift 検出 ----------------------------------------------------------------------


def _entry_drift(view):
    """現況 1 件 → drift record の列。観測していない側面からは 1 件も出さない。"""
    issue_ref = view["issue_ref"]
    phase = view["phase"]
    checked = view["checked"]
    agent = view["ledger"]["agent"] or {}
    found = []

    if checked["pane"]:
        found.extend(_pane_drift(issue_ref, phase, agent, view["observed"]["pane"]))
    if checked["worktree"]:
        found.extend(
            _worktree_drift(issue_ref, phase, agent, view["observed"]["worktree"])
        )
    if checked["issue"]:
        found.extend(_issue_drift(issue_ref, phase, view["observed"]["issue"]))
    if checked["prs"]:
        found.extend(
            _pr_drift(issue_ref, phase, view["ledger"]["prs"] or [], view["observed"]["prs"])
        )
    return found


def _pane_drift(issue_ref, phase, agent, pane):
    recorded_id = agent.get("pane_id")
    if phase in vocabulary.PANE_EXPECTED_PHASES:
        if pane is None:
            return [_drift("pane_missing", issue_ref, phase, {"ledger_pane_id": recorded_id})]
        found = []
        if recorded_id is not None and pane.get("pane_id") != recorded_id:
            found.append(
                _drift(
                    "pane_id_mismatch",
                    issue_ref,
                    phase,
                    {"ledger_pane_id": recorded_id, "pane_id": pane.get("pane_id")},
                )
            )
        if pane.get("agent_status") in STOPPED_AGENT_STATUSES:
            found.append(
                _drift(
                    "agent_stopped",
                    issue_ref,
                    phase,
                    {
                        "pane_id": pane.get("pane_id"),
                        "agent_status": pane.get("agent_status"),
                        "agent_status_raw": pane.get("agent_status_raw"),
                    },
                )
            )
        return found
    if pane is not None:
        return [_drift("pane_present", issue_ref, phase, {"pane_id": pane.get("pane_id")})]
    return []


def _worktree_drift(issue_ref, phase, agent, worktree):
    recorded = agent.get("worktree")
    if (
        phase in vocabulary.WORKTREE_EXPECTED_PHASES
        and recorded is not None
        and worktree is None
    ):
        return [_drift("worktree_missing", issue_ref, phase, {"ledger_worktree": recorded})]
    if phase in vocabulary.TERMINAL_PHASES and worktree is not None:
        return [
            _drift(
                "worktree_present",
                issue_ref,
                phase,
                {"path": worktree.get("path"), "dirty": worktree.get("dirty")},
            )
        ]
    return []


def _issue_drift(issue_ref, phase, issue):
    state = (issue or {}).get("state")
    if phase in vocabulary.ISSUE_OPEN_PHASES and state == "closed":
        return [_drift("issue_closed", issue_ref, phase, {"state": state})]
    if phase in vocabulary.ISSUE_CLOSED_PHASES and state == "open":
        return [_drift("issue_open", issue_ref, phase, {"state": state})]
    return []


def _pr_drift(issue_ref, phase, recorded_prs, observation):
    """台帳の `last_seen_status` と観測の食い違い + 着手中の closes PR merged。

    merged を別 kind で出すのは、駐機中に PR が merged になったことが再入時にいちばん
    効く観測だから (台帳に PR を記録していなくても検出できる経路を残す)。
    """
    observed_prs = (observation or {}).get("prs") or []
    by_ref = {pr.get("ref"): pr for pr in observed_prs}
    found = []
    for recorded in recorded_prs:
        last_seen = recorded.get("last_seen_status")
        if last_seen is None:
            continue
        current = (by_ref.get(recorded.get("ref")) or {}).get("status")
        if current != last_seen:
            found.append(
                _drift(
                    "pr_status_changed",
                    issue_ref,
                    phase,
                    {
                        "pr_ref": recorded.get("ref"),
                        "last_seen_status": last_seen,
                        "status": current,
                    },
                )
            )
    if phase in vocabulary.IN_FLIGHT_PHASES:
        merged = [
            pr["ref"]
            for pr in observed_prs
            if pr.get("role") == "closes" and pr.get("status") == "merged"
        ]
        if merged:
            found.append(_drift("pr_merged", issue_ref, phase, {"pr_refs": merged}))
    return found


def _scope(tracker, scope_ref):
    """scope 指定 → (中立 ref, 突合に使う issue 番号)。

    別 tracker / 番号を持たない ref を scope に渡されたときは番号が無い。番号が無い scope で
    外部側 drift を出すと、その scope とは無関係の worktree / pane を報告することになるので、
    番号側だけ None に落として「1 件も出さない」に倒す。
    """
    if scope_ref is None:
        return None, None
    parsed = refs.parse_issue_ref(scope_ref)
    number = parsed["number"] if parsed["tracker"] == tracker else None
    return parsed["ref"], number


def _untracked_drift(
    tracker, panes_by_number, worktrees_by_number, joined_numbers, scope, scope_number
):
    """台帳に対応 entry が無い pane / worktree (spec §3.5 が名指しする外部側の drift)。

    scope 指定時はその issue だけを見る — 絞り込んだ entry 列を台帳の全体と誤認すると、
    追跡中の他の worktree まで「台帳に無い」と報告してしまう。
    """
    found = []
    for number, worktree in sorted(worktrees_by_number.items()):
        if number in joined_numbers or (scope is not None and number != scope_number):
            continue
        found.append(
            _drift(
                "worktree_untracked",
                refs.format_issue_ref(tracker, number=number),
                None,
                {"path": worktree.get("path"), "dirty": worktree.get("dirty")},
            )
        )
    for number, pane in sorted(panes_by_number.items()):
        if number in joined_numbers or (scope is not None and number != scope_number):
            continue
        found.append(
            _drift(
                "pane_untracked",
                refs.format_issue_ref(tracker, number=number),
                None,
                {"pane_id": pane.get("pane_id"), "agent_status": pane.get("agent_status")},
            )
        )
    return found


def _drift(kind, issue_ref, phase, evidence):
    """drift record。`expected` / `observed` は語彙表から引く (書き下ろすと表と drift する)。"""
    if kind not in DRIFT_KINDS:
        raise ResolveError(f"未知の drift kind: {kind!r}")
    expected, observed_text = DRIFT_KINDS[kind]
    return {
        "kind": kind,
        "issue_ref": issue_ref,
        "phase": phase,
        "expected": expected,
        "observed": observed_text,
        "evidence": evidence,
    }


# --- 索引 ---------------------------------------------------------------------------


def _index_by_number(items, number_of):
    """issue 番号 → 要素。番号を持たない要素は落とす (追跡対象でない pane / worktree)。"""
    index = {}
    for item in items or []:
        number = number_of(item)
        if number is not None:
            index.setdefault(number, item)
    return index


def _pane_number(pane):
    """追跡対象 (`i<N>` label) かつ自 pane でない pane の issue 番号。

    自 pane を除くのは `pane_watch` と同じ理由 — dispatcher 自身に残骸 label が付いて
    いると、自分を「その issue の作業 pane」と誤認する。
    """
    if pane.get("is_self"):
        return None
    return pane.get("issue_number")
