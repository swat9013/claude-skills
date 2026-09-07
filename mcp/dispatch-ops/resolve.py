"""台帳と外部 store の join と drift 検出 (spec §3.5 / §4.4 の `resolve`)。

外部 store (tracker / pane / git) が「現実」、台帳は「意図と記録」。本 module は両者を
突き合わせて食い違い (drift) を機械的に列挙するだけで、**解消の判断は持たない** — 台帳を
現実へ合わせるか現実を直すか (pane 再起動・worktree 回収) は LLM が毎回決める。drift record に
`suggested_action` の類を足さないこと。そこに判断が滲むと server がポリシーを持ってしまう。

**join の鍵は中立 issue ref**。number slug (`i<N>`) の観測は tracker を持たない `issue_number`
までしか返せない (spec §4.4 の #404 / #405 註) ので、「1 repo = 1 tracker」を知っている本 module が
issue 置き場の tracker を渡して `refs.lift_issue_ref` に写させる (**持ち上げ規則そのものは綴りの
正本である refs が持つ** — 綴りを増やしたとき持ち上げが追随しないと join が黙って壊れる)。
key slug (`swatcf-14`、jira) の観測は slug 自体が自己記述
なので写す必要が無く、**番号を持たない ref でも pane / worktree とは突き合わせられる** — 照合は
tracker への到達性と無関係に成立するから (issue #575)。写せないのは番号を持つ別 tracker の entry
だけで、それは**黙って落とさず `unjoinable` として報告する** — `derive_tidy_scope` の `unmappable`
と同じ理由 (見えない取りこぼしは「保護したつもりが消える」に化ける)。

tracker へ**問い合わせる**entry はこれとは別の条件で選ぶ。**issue 置き場と PR 置き場は別々の port**
で観測する (#576) — 1 つに束ねると、issue 置き場が Jira の project で PR 置き場 (GitLab) の MR まで
観測できず、駐機した worktree の MR が merged でも検知されない。

- issue: adapter が届く tracker の entry だけ撃つ (`_observable_entries`)。Jira entry の issue 現況は
  `checked.issue` が false のまま返り、issue 由来の drift は 1 件も出ない。その現況を誰が観測するか
  (Rovo MCP 等) は呼び出し側の領分
- PR: 同 tracker の entry は issue から引き、**別 tracker の entry は台帳が記録した (repo, ref) を種に
  直接引く** (`observe_pr_refs`)。記録が無い entry は観測の種が無いので `errors` に理由を残して未検査
- review thread: 駐機中 (`REVIEW_THREAD_PHASES`) の entry の closes PR にだけ撃つ (ADR 0039)。
  未対応の adapter は store ごと未観測で返る — **空で返すと「レビュー対応済み」と読まれる**

project の実装 repo は複数ありうる (ADR 0036) ので、join の鍵は 2 箇所で cwd の clone から離れる:
**worktree は台帳が記録したパス**で照合し (一覧に無いことを消失と読まない)、**PR は (repo, ref)**
で突き合わせる (別 repo の同番号 PR を自分の成果と読まない)。どちらも server が clone や repo の
集合を持つのではなく、entry に記録された値と呼び出し側が渡した識別子だけを見る。

観測できなかったことを「観測して無かった」と混同しない (`blocked: null` / `dirty: null` と同じ規則):

- store 単位: `items` が None なら未観測。その store 由来の drift は 1 件も出さない
- entry 単位: `checked` が false の側面は判定していない。`observed` の null と読み分ける

**決定的な畳み方は entry の `derived` へ押し下げる** (`_derived`、issue #608)。closes × repo 一致の
突合も `done` の前提述語も、観測列だけで評価でき自然言語の解釈を要さない — その規則を呼び出し側の
散文が毎 tick 組み直していると、join が変わるたびに散文の全箇所を人力で追うことになる。押し下げても
**確定は呼び出し側のまま**で、server は phase を提案しない (ADR 0032 の出力契約: 候補 + 導出過程 +
未判定条件まで)。

外部 store の観測 (`observe_stores`) も本 module が組み立てる。**port は scope から受け取り、どの
backend を立てるかの判断は scope を組み立てた tool 層が持つ** — 本 module は port を選ばない。観測する
entry の選別 (`_observable_entries`) と `checked` 判定が同じ module に居るので、「観測しなかった entry は
未検査として扱う」という噛み合わせを module の外から保証しなくてよい。

観測束は名前のある object (`Observation`) で運ぶ。**「その entry のその store を見たか」(`checked`) を
答えるのはこの object 1 箇所**で、`join` も drift 判定も問うだけにする — 判定が呼び出し側へ散ると、
store を 1 つ足すたびに「未観測を空と読む」経路が新しく増える。
"""

from dataclasses import dataclass, field
from typing import Any

import pane as pane_mod
import refs
import tracker as tracker_mod
import vocabulary
import worktree as worktree_mod

# 観測を受け取る store。tracker は issue と PR で取得経路が別なので 2 つに割ってある。
# review thread は PR の観測を種にする (どの PR へ問い合わせるかを PR 観測から得る) が、
# **撃つ対象も未観測の意味も別**なので store を分ける — 「PR は見たが review thread は
# 見ていない」(未対応 adapter / 非 parked entry) を 1 つの `checked` で潰さないため
STORES = ("issues", "prs", "panes", "worktrees", "review_threads")

# entry 1 件ずつ撃つ store。**`items` の鍵がそのまま「見た entry」の集合**になるので `checked` は
# 鍵の有無で決まる。残り (panes / worktrees) は entry を選ばず一括で撮るので、`checked` は
# 「その entry の索引の鍵を作れたか」(= join できるか) で決まる
PER_ENTRY_STORES = ("issues", "prs", "review_threads")

# entry の現況 (`observed` / `checked`) の欄 → その値を持つ store。**欄名の正本はここ 1 箇所**で、
# 出力契約の綴りと store 名の対応が 2 箇所に写らないようにする
ENTRY_STORES = {
    "issue": "issues",
    "prs": "prs",
    "review_threads": "review_threads",
    "pane": "panes",
    "worktree": "worktrees",
}

# phase の部分集合は vocabulary の属性表から導出したものを使う (直書きすると phase 追加時に
# 黙って古いまま通る)。各軸の意味と「値が同じでも意味が別の軸は統合しない」規約は
# `vocabulary.PHASE_ATTRIBUTES` の註が正本。
# agent が動いていないと見なす中立 status (spec §4.3)
STOPPED_AGENT_STATUSES = ("exited", "gone")

# review thread を問い合わせる phase (ADR 0039)。**駐機中の entry だけ**が対象 — `active` は
# worker が作業中、`done` 以降は worktree が回収済みで対応する作業ツリーがもう無い。他 phase
# へ広げると tick 1 回あたりの CLI 起動が entry 数だけ増えて observer の周期が壊れる
# (2 分を超えた tick は自動 background 化される)。phase 名が実在することはテストが押さえる
REVIEW_THREAD_PHASES = ("parked",)

# drift の語彙。kind → (台帳から期待される状態, 観測した状態)。**何をすべきか**は書かない
DRIFT_KINDS = {
    "pane_missing": ("pane が稼働している", "その issue の pane が無い"),
    "pane_id_mismatch": ("台帳の pane_id の pane が稼働している", "同じ label で別 id の pane が稼働している"),
    "agent_stopped": ("pane の agent が稼働している", "agent が終了している"),
    "pane_present": ("pane が居ない", "その issue の pane が稼働している"),
    "worktree_missing": ("台帳の worktree が存在する", "worktree が無い"),
    "worktree_present": ("worktree が回収済み", "worktree が残っている"),
    "worktree_path_mismatch": (
        "台帳に記録したパスの worktree を観測する",
        "同じ番号の worktree が別のパスに在る",
    ),
    "issue_closed": ("issue が open", "issue が closed"),
    "issue_open": ("issue が closed", "issue が open"),
    "pr_status_changed": ("台帳に記録した PR status のまま", "PR status が変わっている"),
    "pr_ref_ambiguous": ("台帳の PR ref が 1 件の PR を指す", "同じ ref の PR が複数 repo にある"),
    "pr_repo_mismatch": (
        "台帳に記録した repo にその PR が居る",
        "同じ ref の PR が別 repo にしか居ない",
    ),
    "pr_merged": ("closes PR が未 merge", "closes PR が merged"),
    "worktree_untracked": ("台帳に対応する dispatch がある", "台帳に無い worktree が残っている"),
    "pane_untracked": ("台帳に対応する dispatch がある", "台帳に無い pane が稼働している"),
}


class ResolveError(ValueError):
    """観測束 / drift の組み立てが規約に合わない (未知の store / 未知の drift kind)。"""


# --- 観測束 --------------------------------------------------------------------------


@dataclass
class StoreObservation:
    """外部 store 1 つの観測。

    `items` が None なら未観測で、**「見て 0 件」とは別物**。`error` が None ならそもそも観測しに
    行っていない (意図的な未観測)。`errors` は entry 単位で失敗した分 (ref → 理由) で、その entry
    だけが未検査になる。
    """

    items: Any = None
    error: str | None = None
    errors: dict = field(default_factory=dict)

    @property
    def was_observed(self):
        return self.items is not None

    def report(self):
        """出力の `stores` block に載せる 1 store 分。"""
        return {"observed": self.was_observed, "error": self.error, "errors": self.errors}


def observed(items, errors=None):
    """観測できた store。`errors` は entry 単位で失敗した分 (ref → 理由)。"""
    return StoreObservation(items=items, errors=dict(errors or {}))


def unobserved(error=None):
    """観測しなかった / できなかった store。`error` が None なら意図的に観測していない。"""
    return StoreObservation(error=None if error is None else str(error))


class Observation:
    """外部 store 5 つの観測束 (`join` の入力)。

    渡さなかった store は未観測で埋まる。`STORES` に無い名前は組み立て時に落とす — 観測側と
    join 側が別々の store 名で育つと、片方が書いた観測をもう片方が黙って読み飛ばす。

    `worktree_root` は台帳が相対で記録したパスを解く基準 (観測した clone の root)。store の中では
    なく束が持つ — store の中に入れると出力の `stores` block へ漏れる。
    """

    def __init__(self, *, worktree_root=None, **stores):
        unknown = sorted(set(stores) - set(STORES))
        if unknown:
            raise ResolveError(f"未知の store: {unknown} (既知: {', '.join(STORES)})")
        self._stores = {
            name: stores[name] if name in stores else unobserved() for name in STORES
        }
        self.worktree_root = worktree_root

    def store(self, name):
        """store 1 つの観測 (組み立て途中の追記もこれ経由で行う)。"""
        return self._stores[name]

    def items(self, name):
        """store の観測本体。未観測なら None。"""
        return self._stores[name].items

    def for_entry(self, name, issue_ref):
        """entry 1 件ずつ撃った store の観測値。未観測 / 撃たなかった entry は None。"""
        return (self._stores[name].items or {}).get(issue_ref)

    def checked(self, name, issue_ref, *, joinable):
        """その entry のその store を**見たか**。`observed` の null を読み分ける唯一の鍵。

        規則は 1 つ — 「store を観測していて、その entry で失敗しておらず、その entry が観測の
        対象だった」。対象だったかの判定だけが store の撃ち方で分かれる (`PER_ENTRY_STORES` は
        `items` の鍵、一括観測は索引の鍵を作れたか = `joinable`)。

        `errors` を先に見るのは一括観測側のため (記録パスの probe に失敗した worktree は、
        一覧を観測していても未検査)。entry ごとに撃つ store では `items` と `errors` が排他なので
        結果は変わらない — **その排他性が崩れるとこの規則が黙って壊れる**ので、観測側
        (`_observe_*`) は同じ ref を両方へ書かないこと。
        """
        store = self._stores[name]
        if not store.was_observed or issue_ref in store.errors:
            return False
        if name in PER_ENTRY_STORES:
            return issue_ref in store.items
        return joinable

    def report(self):
        """出力の `stores` block。"""
        return {name: store.report() for name, store in self._stores.items()}


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
    return [entry for entry in entries if entry.issue_ref == ref]


def joinability(entry, tracker):
    """台帳 entry → (join の鍵になる中立 ref, 突き合わせられない理由)。突合できるなら理由は None。

    鍵が中立 ref なので、**番号を持つかどうかは突合の可否と関係しない**。判断するのは
    「観測側の綴りをこの entry の ref へ写せるか」の 1 点:

    - server の tracker と同じ entry: number slug (`i<N>`) の観測を「1 repo = 1 tracker」で
      写せる
    - slug が自己記述な entry (jira): 観測側が ref をそのまま名乗るので写す必要が無い
    - それ以外 (番号を持つ別 tracker): 写せない。番号だけで照合すると同じ番号の `i<N>`
      worktree / pane を取り違える
    """
    issue_ref = entry.issue_ref
    tracker_of_entry = entry.tracker
    if tracker_of_entry == tracker:
        return issue_ref, None
    if issue_ref is not None and refs.slug_is_self_describing(issue_ref):
        return issue_ref, None
    return None, (
        f"tracker が {tracker_of_entry!r} で server の {tracker!r} と違い、"
        "observation の slug から ref を復元できない"
    )


def observable_at_tracker(entry, tracker):
    """その adapter で現況を引ける entry か (join できるかとは別の問い)。

    join は pane / worktree との突合まで含むので tracker を跨いで成立するが、issue / PR の
    現況は adapter が届く tracker にしか無い。両者を同じ述語で判断すると、**届かない tracker
    へ CLI を撃つ**か、**撃てるのに join ごと落とす**かのどちらかになる。

    `tracker` が None (その置き場の adapter が無い) なら誰も観測できない。issue 置き場が
    未実装 tracker (Jira) の project では port 自体が None なので、entry 側の tracker と
    「一致する」形で撃ちに行かせない。
    """
    return tracker is not None and entry.tracker == tracker


# --- 外部 store の観測 ----------------------------------------------------------------


def observe_stores(entries, scope, *, include_prs, repo=None, pr_repo=None):
    """scope の port を観測して `join` に渡す観測束を組み立てる (store は 5 つ — PR 置き場の
    port が PR と review thread の 2 store を埋める)。

    port は scope から引く — どの backend / tracker を立てるかは scope を組み立てた tool 層の
    判断で、本 module は選ばない。repo 識別子の実効値 (**明示引数 > 宣言 > 未指定**) も scope に
    解かせる。呼び出し側で解いてから渡すと、同じ解決規則が tool 層と本 module に二重化する。

    **issue 置き場と PR 置き場の port を別に引く** (#576)。1 つに束ねると、issue 置き場が
    未実装 tracker (Jira) の project で PR 置き場 (GitLab) の MR まで観測できなくなり、駐機
    した worktree の MR が merged でも検知されない。tracker 名は port と別に引く — adapter が
    無い置き場でも tracker 名は判っており、number slug の持ち上げに要るため。

    PR 置き場の repo は宣言でも決まらなければ issue 置き場へ倒す (置き場が 1 つだった頃の挙動)。
    """
    issue_tracker = scope.issue_tracker_name()
    pane_port = scope.get_pane()
    worktree_port = scope.get_worktrees()
    pr_port = scope.get_pr_adapter_optional()
    issue_scope = scope.issue_repo(repo)
    pr_scope = scope.pr_repo(pr_repo)
    if pr_scope is None:
        pr_scope = issue_scope

    panes = _observe_store(lambda: pane_port.observe_panes()["panes"], pane_mod.PaneError)
    worktrees = _observe_store(
        lambda: worktree_port.observe()["worktrees"], worktree_mod.WorktreeError
    )
    _add_recorded_worktrees(worktrees, entries, worktree_port, issue_tracker)
    issues = _observe_issues(scope.get_adapter_optional(), entries, issue_scope)
    prs = _observe_prs(pr_port, entries, pr_scope) if include_prs else unobserved()
    review_threads = (
        _observe_review_threads(pr_port, entries, prs, pr_scope)
        if include_prs
        else unobserved("PR を観測していないので review thread の観測の種が無い")
    )
    return Observation(
        issues=issues,
        prs=prs,
        panes=panes,
        worktrees=worktrees,
        review_threads=review_threads,
        # 台帳が相対で記録したパスを解く基準。無いと記録と観測を突き合わせられない (未検査扱い)
        worktree_root=worktree_port.root,
    )


def _add_recorded_worktrees(store, entries, worktree_port, tracker):
    """`observe` の一覧に出なかった entry を、台帳が記録したパスで観測し直す。

    `observe` が返すのは port の root にぶら下がる worktree だけなので、別 clone に切られた
    ツリーは一覧に無い。**それを「消えた」と読むと、生きている作業ツリーを持つ entry に
    `worktree_missing` が出る** (ADR 0036 の壊れ点 4)。どの clone を回るかは server が知らない
    ままでよい — 見に行く先は entry に記録されたパスだけで、clone の集合は列挙しない。

    観測できなかった entry は `errors` に落とすだけで、それを未検査と読むのは `Observation.checked`
    (規則を持つのはあちら 1 箇所)。一覧が丸ごと未観測 (`items` が None) のときは何も足さない —
    その状態は全 entry が既に未検査で、一部だけ検査済みにすると読み分けが崩れる。

    一覧に同じ ref が既に在る entry は probe しない (ref 1 つに観測 1 件を保つ)。その一覧の
    ツリーが記録と別のパスなら `join` が `worktree_path_mismatch` を出すので、別 clone の
    ツリーを自分のものと読む取り違えは黙って通らない。

    一覧が読むのは number slug (`i<N>`) だけなので、key slug の作業ツリー (jira) は原理的に
    一覧へ出ない。**その entry の worktree 観測はこの probe だけが担う** — 記録パスさえあれば
    番号は要らないので、番号を持たない ref でも消失 / 残置は検出できる。
    """
    if not store.was_observed:
        return
    seen = {refs.lift_issue_ref(tracker, item) for item in store.items}
    for entry in entries:
        ref, reason = joinability(entry, tracker)
        recorded = entry.recorded_worktree
        if reason is not None or not recorded or ref in seen:
            continue
        try:
            found = worktree_port.probe(recorded, ref)
        except worktree_mod.WorktreeError as exc:
            store.errors[entry.issue_ref] = str(exc)
            continue
        if found is not None:
            store.items.append(found)
            seen.add(ref)


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
    """tracker へ現況を問い合わせる entry (adapter が届いて、かつ非終端)。

    終端 (cleaned / released / spawn_failed) は履歴であって現況の突合先が無く、1 件あたり
    CLI が起動する観測を履歴のために撃つ理由が無い。ここで落とした entry は観測束に載らず、
    `join` が `checked` を false にする — 選別と `checked` 判定を同じ module に置くことで、
    両者の噛み合わせが暗黙の約束でなく本 module 内の契約になる。

    篩うのは `observable_at_tracker` であって `joinability` ではない。別 tracker の entry
    (jira を含む) は pane / worktree では join できても issue の現況は adapter の外なので、
    **問い合わせずに未検査で返す** — 撃てば別 repo の同番号 issue を読むか、単に失敗する。
    """
    return [entry for entry in _live_entries(entries) if observable_at_tracker(entry, tracker)]


def _live_entries(entries):
    """現況の突合先がある entry (非終端)。

    終端 (cleaned / released / spawn_failed) は履歴であって現況の突合先が無く、1 件あたり
    CLI が起動する観測を履歴のために撃つ理由が無い。
    """
    return [entry for entry in entries if not entry.is_terminal]


def _observe_issues(adapter, entries, repo=None):
    """台帳 entry の issue 現況を issue 置き場へ問い合わせる。

    個別の失敗は entry 単位で記録して他の entry の観測を続ける — 1 件の CLI 失敗で現況
    全体を落とさない。

    `repo` は issue 置き場の識別子で、未指定なら CLI の cwd 推論のまま。置き場が cwd と別
    repo のときに渡さないと、**別 repo の同番号 issue の state を読んで `issue_closed` を
    でっち上げる** (その drift は phase を `done` へ送る根拠に使われる)。

    port が None (issue 置き場の adapter が未実装 = Jira) なら 1 件も撃たず store ごと未観測で
    返す。`observed({})` にすると「全 entry を見て 1 件も現況が無かった」と読めてしまう。
    """
    if adapter is None:
        return unobserved("issue 置き場の adapter が無い (未実装 tracker)")
    issues, errors = {}, {}
    for entry in _observable_entries(entries, adapter.tracker):
        issue_ref = entry.issue_ref
        try:
            issues[issue_ref] = adapter.observe_issue(issue_ref, repo=repo)
        except tracker_mod.TrackerError as exc:
            errors[issue_ref] = str(exc)
    return observed(issues, errors)


def _observe_prs(adapter, entries, repo=None):
    """台帳 entry に紐づく PR を **PR 置き場**へ問い合わせる。

    観測の種は entry の tracker で 2 通りに分かれる (#576):

    - issue 置き場と PR 置き場が同じ tracker: issue から PR を引く (従来経路)。closes /
      mention の判定も closing reference の観測なので、台帳に PR を記録していなくても効く
    - 別 tracker (issue = Jira / PR = GitLab): issue → PR の経路が無いので、**台帳が記録した
      `{ref, role, repo}` を種に PR を直接引く**。記録が無い entry は観測の種が無く、
      「見ていない」として `errors` に理由を残す (`checked.prs` は false)

    port が None (PR 置き場の adapter が無い) なら store ごと未観測で返す。
    """
    if adapter is None:
        return unobserved("PR 置き場の adapter が無い (未実装 tracker)")
    prs, errors = {}, {}
    for entry in _live_entries(entries):
        issue_ref = entry.issue_ref
        try:
            if observable_at_tracker(entry, adapter.tracker):
                prs[issue_ref] = adapter.observe_prs(issue_ref, repo=repo)
                continue
            records = entry.recorded_prs
            if not records:
                errors[issue_ref] = (
                    f"issue 置き場 ({entry.tracker}) が PR 置き場 ({adapter.tracker}) と別で、"
                    "台帳にも PR 記録が無い (観測の種が無い)"
                )
                continue
            prs[issue_ref] = adapter.observe_pr_refs(records, issue_ref=issue_ref, repo=repo)
        except tracker_mod.TrackerError as exc:
            errors[issue_ref] = str(exc)
    return observed(prs, errors)


def _observe_review_threads(adapter, entries, prs_store, repo=None):
    """駐機中の entry に紐づく closes PR の review thread を観測する (ADR 0039)。

    撃つ対象を `REVIEW_THREAD_PHASES` (= `parked`) に限る。ADR 0039 がレビュー対応の対象を
    駐機中の entry に限った以上、他 phase へ問い合わせても使い道が無く、tick の所要時間だけが
    entry 数に比例して伸びる。

    観測の種は **PR 観測の結果**で、`role == "closes"` の PR だけを見る。mention された PR の
    指摘は自分の成果への指摘ではない (集約 `status` を closes 限定にしているのと同じ理由)。

    未対応の adapter は store ごと未観測で返す。**空 (`observed({})`) にしない** —
    「全 entry を見て未解決 thread が 1 件も無かった」と読まれ、指摘の付いた PR が駐機した
    まま滞留する。理由は port の 1 箇所 (`require_review_threads`) から採る。
    """
    if adapter is None:
        return unobserved("PR 置き場の adapter が無い (未実装 tracker)")
    try:
        adapter.require_review_threads()
    except tracker_mod.TrackerError as exc:
        return unobserved(exc)
    if not prs_store.was_observed:
        return unobserved("PR を観測していないので review thread の観測の種が無い")
    threads, errors = {}, {}
    for entry in entries:
        if entry.phase not in REVIEW_THREAD_PHASES:
            continue
        issue_ref = entry.issue_ref
        observed_prs = prs_store.items.get(issue_ref)
        if observed_prs is None:
            errors[issue_ref] = (
                "PR を観測できていない entry なので review thread の観測の種が無い"
            )
            continue
        records = [
            pr for pr in observed_prs.get("prs") or [] if pr.get("role") == tracker_mod.ROLE_CLOSES
        ]
        try:
            threads[issue_ref] = adapter.observe_review_threads(records, repo=repo)
        except tracker_mod.TrackerError as exc:
            errors[issue_ref] = str(exc)
    return observed(threads, errors)


# --- 突合の入口 -----------------------------------------------------------------------


def resolve(entries, scope, *, scope_ref=None, include_prs=True, repo=None, pr_repo=None):
    """台帳 entry と外部 store を突き合わせた現況 + drift (tool `resolve` の実体)。

    Args:
        entries: 台帳の全 entry (`scope_ref` の適用は本関数が行う)
        scope: server process の scope。置き場の tracker 名・観測に使う 4 つの port・repo
            識別子の解決をここから引く
        scope_ref: 中立 issue ref。渡すとその issue だけを突き合わせる
        include_prs: 紐づく PR も観測する
        repo: issue 置き場の repo 識別子 (未指定なら宣言 → CLI の cwd 推論)
        pr_repo: PR 置き場の repo 識別子 (未指定なら宣言 → `repo` へ倒す)

    port を 5 つに割らず scope 1 つで受けるのは、**「resolve が scope の何を要るか」を本 module に
    持たせる**ため。呼び出し側が port を選んで並べ替える形だと、同じ対応表が tool 層とテストの
    両方へ写り、port が 1 つ増えるたびに両方を直すことになる。どの backend を立てるかの判断は
    scope を組み立てた tool 層のまま。

    絞り込み → 観測 → join を 1 本にまとめてあるのは、観測した集合と join する集合が
    ずれると drift が誤って出るため (同じ `select_entries` の結果を両方へ渡す)。
    """
    selected = select_entries(entries, scope_ref)
    observation = observe_stores(
        selected, scope, include_prs=include_prs, repo=repo, pr_repo=pr_repo
    )
    return join(scope.issue_tracker_name(), selected, observation, scope_ref=scope_ref)


# --- join ---------------------------------------------------------------------------


def join(tracker, entries, observation, scope_ref=None):
    """台帳 entry と外部 store の観測を突き合わせ、現況と drift を返す。

    Args:
        tracker: **issue 置き場**の tracker。number slug → 中立 ref の持ち上げに使う。番号体系を
            持たない tracker (`jira`) では持ち上げられない観測が出るので、
            `unmappable_observations` に残す
        entries: `select_entries` で絞った台帳 entry の列
        observation: 外部 store の観測束 (`Observation`)
        scope_ref: scope 指定時の中立 issue ref。台帳に無い pane / worktree の検出をその
            issue へ限る (絞った entry 列を台帳の全体と見なして他を全部 orphan にしない)
    """
    scope_key = _scope_key(scope_ref)
    panes = observation.items("panes")
    worktrees = observation.items("worktrees")

    panes_by_ref = _index_by_ref(panes, lambda item: _pane_ref(item, tracker))
    # worktree は pane と違って除く policy が無いので持ち上げをそのまま索引の鍵にする
    # (一覧は number slug しか読まないので `issue_number` だけを持ち、記録パスの probe は
    # 問い合わせた ref を名乗る — 番号を持たない ref はその経路だけで載る)
    worktrees_by_ref = _index_by_ref(worktrees, lambda item: refs.lift_issue_ref(tracker, item))

    current, drift, unjoinable, joined_refs = [], [], [], set()
    for entry in entries:
        ref, reason = joinability(entry, tracker)
        if reason is not None:
            unjoinable.append(
                {"issue_ref": entry.issue_ref, "phase": entry.phase, "reason": reason}
            )
        else:
            joined_refs.add(ref)
        view = _entry_view(
            entry,
            observation,
            reason=reason,
            pane=panes_by_ref.get(ref),
            worktree=worktrees_by_ref.get(ref),
        )
        current.append(view)
        drift.extend(_entry_drift(entry, view, observation.worktree_root))

    drift.extend(_untracked_drift(panes_by_ref, worktrees_by_ref, joined_refs, scope_key))

    return {
        "tracker": tracker,
        "scope": scope_key,
        "stores": observation.report(),
        "count": len(current),
        "current": current,
        "drift_count": len(drift),
        "drift": drift,
        "unjoinable": unjoinable,
        "unmappable_observations": _unmappable_observations(tracker, panes, worktrees),
    }


# --- entry の現況 --------------------------------------------------------------------


def _entry_view(entry, observation, *, reason, pane, worktree):
    """台帳 entry 1 件 → 台帳側 (`ledger`) と観測側 (`observed`) を並べた現況。

    `observed` の null は「無い」か「見ていない」かが単体では読めないので、必ず
    `checked` と対で読む (checked が false のときは判定していない)。**その `checked` は
    observation が答える** — ここが判定を持つと、store が増えるたびに規則の写しが増える。

    pane / worktree の観測値だけ引数で受ける。ref 索引は `join` が 1 回だけ作るもので、entry
    ごとに作り直すと観測件数 × entry 件数の照合になる。

    `derived` は同じ入力 (`ledger` + `observed` + `checked`) から機械的に畳んだ派生列
    (`_derived`)。畳み方を呼び出し側で毎回書き直させないために server 側へ置いてある。

    `ledger` block は `EntryView.raw` の該当欄をそのまま写した**出力整形**で、判断には使わない
    (判断に要る値は `entry` の述語から採る)。応答の外形が台帳の綴りに追従するのはここ 1 箇所。
    """
    issue_ref = entry.issue_ref
    raw = entry.raw
    view = {
        "issue_ref": issue_ref,
        "phase": entry.phase,
        "ledger": {
            column: raw.get(column)
            for column in ("issue", "agent", "prs", "outcome", "note", "updated_at")
        },
        "observed": {
            "issue": observation.for_entry("issues", issue_ref),
            "prs": observation.for_entry("prs", issue_ref),
            "review_threads": observation.for_entry("review_threads", issue_ref),
            "pane": pane,
            "worktree": worktree,
        },
        "checked": {
            slot: observation.checked(store, issue_ref, joinable=reason is None)
            for slot, store in ENTRY_STORES.items()
        },
        "unjoinable_reason": reason,
    }
    view["derived"] = _derived(entry, view)
    return view


# --- 派生列 (決定的な畳み方の押し下げ) -------------------------------------------------


# `mechanical_done` の rule。値は条件名の並びで、**述語の実装は `_done_conditions` が持つ**。
# 表を出力にも評価にも使うのは drift の語彙表 (`DRIFT_KINDS`) と同じ理由 — 表と実装が
# 別々に育つと、呼び出し側が読む条件と server が評価する条件が黙って食い違う。
DONE_RULES = {
    # 自分の worker の成果が merge された (repo 一致は取り違え防止の要 — ADR 0036)
    "closes_merged_in_entry_repo": ("pane_absent", "closes_merged_in_entry_repo"),
    # issue が閉じた (PR 経由でも手動でも、閉じていれば作業の現況としては同じ)
    "issue_closed": ("pane_absent", "issue_closed"),
}


def _derived(entry, view):
    """entry 1 件の派生列。**確定はしない** — 出すのは候補と導出過程と未判定条件まで。

    ここに置くのは「観測列だけで評価でき、自然言語の解釈を要さない」畳み方だけ
    ([ADR 0032](../../docs/adr/0032-policy-free-refinement-deterministic-rules.md) の移行判定
    基準)。同じ規則を skill の散文が毎 tick 組み直していると、cross-tracker で join が変わる
    たびに散文の全箇所を人力で追う羽目になる (issue #608)。

    - `closes_same_repo` / `closes_other_repo`: 観測 PR の union を `role == "closes"` と
      **entry の実装 repo との一致**で割った列。生データ `prs[]` は従来どおり `observed` に
      在るので、「現実のまま返す」原則は崩さない
    - `mechanical_done`: `done` を記帳してよいかの機械的前提 (`DONE_RULES`) の評価。
      **記帳するかは呼び出し側** — server は phase を提案しない
    - `unresolved_review_threads`: 駐機中の entry の closes PR に付いた未解決 review thread
      (ADR 0039)。集約 `status` の梯子には足さない — 1 語へ潰す設計なので、段を足すと
      conflict / merged / review が互いを隠す

    **未検査は `[]` / `False` ではなく null** (`blocked: null` / `dirty: null` と同じ規則)。
    `checked.prs` が false の entry に `closes_same_repo: []` を返すと「自分の repo に closes
    PR は無い」と読め、成果のある issue を `released` + unclaim へ送る経路に乗る。
    """
    same_repo, other_repo = _closes_split(entry, view)
    return {
        "closes_same_repo": same_repo,
        "closes_other_repo": other_repo,
        "mechanical_done": _mechanical_done(entry, view, same_repo),
        "unresolved_review_threads": _unresolved_review_threads(view),
    }


def _unresolved_review_threads(view):
    """観測した review thread → 未解決のものだけの列。判定できないなら null。

    `[]` は「観測して未解決が 0 件」= レビュー対応済みという強い主張なので、**その主張が
    立たない状態はすべて null へ倒す**:

    - `checked.review_threads` が false (撃っていない / adapter が未対応 / 非 parked)
    - 観測した PR のどれかが `truncated` (1 回で取り切れなかった。取れた範囲が全部 resolved
      でも、残りに未解決が居るかは判っていない)
    - 観測できなかった PR 記録がある (`unmappable_prs`)。撃てなかった PR の指摘は
      「無い」ではなく「見ていない」

    載せるのは `ref` / `repo` / `thread_id` の 3 つだけ。指摘の本文は `resolve` の応答へ
    積まず、対応する worker が PR を読む (`_pr_projection` と同じ理由)。
    """
    if not view["checked"]["review_threads"]:
        return None
    observation = view["observed"]["review_threads"] or {}
    if observation.get("unmappable_prs"):
        return None
    found = []
    for pr in observation.get("prs") or []:
        if pr.get("truncated"):
            return None
        found.extend(
            {"ref": pr.get("ref"), "repo": pr.get("repo"), "thread_id": thread.get("id")}
            for thread in pr.get("threads") or []
            if not thread.get("resolved")
        )
    return found


def _closes_split(entry, view):
    """観測 PR → (entry の repo と一致する closes, 一致しない closes)。判定不能なら (None, None)。

    closes は **repo で絞らずに観測される** (cross-repo の closing reference を落とさない
    ため — ADR 0036)。したがって集約 `status` には fork や第三者 repo の PR も効いており、
    repo を確かめずに merged を採ると**他人の PR の merge で駐機中の成果が回収される**。

    entry に `repo` が無い (台帳導入前 / 記録漏れ) ときは**どちらの列にも振らない**。
    「issue 置き場と同じ repo の closes を自分のものと見なす」のは呼び出し側の既定判断で、
    機械的述語ではない (`mechanical_done` は `entry_repo_unrecorded` を開いたまま返す)。
    """
    if not view["checked"]["prs"]:
        return None, None
    entry_repo = entry.implementation_repo
    if entry_repo is None:
        return None, None
    closes = [
        pr for pr in (view["observed"]["prs"] or {}).get("prs") or [] if pr.get("role") == "closes"
    ]
    same = [_pr_projection(pr) for pr in closes if pr.get("repo") == entry_repo]
    other = [_pr_projection(pr) for pr in closes if pr.get("repo") != entry_repo]
    return same, other


def _pr_projection(pr):
    """派生列に載せる PR の射影。**判定に要る 3 つだけ**で、全体は `observed` の union に在る。

    観測をそのまま複製すると同じ応答に PR が 2 度載る。`resolve` の応答は台帳の全 entry 分が
    積み上がる (spec §4.4 の 25,000 token 上限を名指しされている) ので、派生列は判定に使う
    列だけを持つ。title / url が要る報告は `observed.prs.prs` を `ref` で引き直す。
    """
    return {"ref": pr.get("ref"), "repo": pr.get("repo"), "status": pr.get("status")}


def _mechanical_done(entry, view, closes_same_repo):
    """`done` の機械的前提の評価。返すのは候補 (`satisfied`) と導出過程と未判定条件。

    `satisfied` は 3 値:

    - `True`: `DONE_RULES` のどれかが全条件を満たした (`rule_fired` にその id)
    - `False`: どの rule も**確定して**満たさない (pane が生きている等)
    - `None`: 観測が足りず決められない。塞いだ条件が `open_predicates` に名前で出る

    `False` と `None` を潰さないのがこの関数の要点。未検査を「満たさない」と読むと、
    観測できなかった tick が「done ではない」の根拠として積み上がる。
    """
    values, blockers = _done_conditions(view, closes_same_repo)
    fired, open_predicates = [], []
    for rule_id, conditions in DONE_RULES.items():
        verdict = _all_true(values[name] for name in conditions)
        if verdict is True:
            fired.append(rule_id)
        elif verdict is None:
            open_predicates.extend(
                blockers[name] for name in conditions if values[name] is None
            )
    satisfied = True if fired else (None if open_predicates else False)
    return {
        "satisfied": satisfied,
        "rule_fired": fired,
        "open_predicates": _dedupe(open_predicates),
        "evidence": _done_evidence(entry, view, closes_same_repo, values),
    }


def _done_conditions(view, closes_same_repo):
    """`DONE_RULES` の条件名 → (3 値の評価, 評価できないときに開く述語の名前)。

    `pane_absent` は 3 態を 1 つに畳む — pane 自体が無い / `exited` / `gone`。生死だけを
    `pane is None` で見ると、終了した pane が「居る」と読まれて回収経路が止まる。
    """
    checked = view["checked"]
    pane = view["observed"]["pane"]
    values = {
        "pane_absent": (
            None
            if not checked["pane"]
            else pane is None or pane.get("agent_status") in STOPPED_AGENT_STATUSES
        ),
        "closes_merged_in_entry_repo": (
            None
            if closes_same_repo is None
            else any(pr.get("status") == "merged" for pr in closes_same_repo)
        ),
        "issue_closed": (
            None
            if not checked["issue"]
            else (view["observed"]["issue"] or {}).get("state") == "closed"
        ),
    }
    blockers = {
        "pane_absent": "pane_unchecked",
        "closes_merged_in_entry_repo": (
            "prs_unchecked"
            if not checked["prs"]
            else "entry_repo_unrecorded"  # 観測は在るが帰属が機械では決まらない
        ),
        "issue_closed": "issue_state_unchecked",
    }
    return values, blockers


def _done_evidence(entry, view, closes_same_repo, values):
    """`satisfied` をその値にした観測。呼び出し側が判断を再現できる分だけ載せる。"""
    pane = view["observed"]["pane"]
    return {
        "entry_repo": entry.implementation_repo,
        "pane_absent": values["pane_absent"],
        "pane": (
            None
            if pane is None
            else {"pane_id": pane.get("pane_id"), "agent_status": pane.get("agent_status")}
        ),
        "merged_closes_prs": (
            None
            if closes_same_repo is None
            else [pr for pr in closes_same_repo if pr.get("status") == "merged"]
        ),
        "issue_state": (view["observed"]["issue"] or {}).get("state"),
    }


def _all_true(values):
    """3 値の連言。1 つでも False なら False、未確定が残るなら None。"""
    found = list(values)
    if any(value is False for value in found):
        return False
    return None if any(value is None for value in found) else True


def _dedupe(names):
    """出現順を保った重複除去 (同じ述語が 2 rule から開くことがある)。"""
    seen, found = set(), []
    for name in names:
        if name not in seen:
            seen.add(name)
            found.append(name)
    return found


# --- drift 検出 ----------------------------------------------------------------------


def _entry_drift(entry, view, worktree_root=None):
    """現況 1 件 → drift record の列。観測していない側面からは 1 件も出さない。

    台帳側の記録は `entry` の述語から採り、観測側は `view` から採る。**下位の判定関数へは
    dict ではなく記録済みの値そのものを渡す** — `agent` を丸ごと配ると、drift 判定のたびに
    entry の綴りを開く側が増える。

    `worktree_root` は台帳が相対で記録したパスを解く基準 (観測した clone の root)。渡されない
    呼び出しでは相対記録との突合を「判定できない」として飛ばす。
    """
    issue_ref = entry.issue_ref
    phase = entry.phase
    checked = view["checked"]
    found = []

    if checked["pane"]:
        found.extend(
            _pane_drift(issue_ref, phase, entry.recorded_pane_id, view["observed"]["pane"])
        )
    if checked["worktree"]:
        found.extend(
            _worktree_drift(
                issue_ref,
                phase,
                entry.recorded_worktree,
                view["observed"]["worktree"],
                worktree_root,
            )
        )
    if checked["issue"]:
        found.extend(_issue_drift(issue_ref, phase, view["observed"]["issue"]))
    if checked["prs"]:
        found.extend(
            _pr_drift(issue_ref, phase, entry.recorded_prs, view["observed"]["prs"])
        )
    return found


def _pane_drift(issue_ref, phase, recorded_id, pane):
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


def _worktree_drift(issue_ref, phase, recorded, worktree, root=None):
    """worktree の食い違い。**番号が同じでもパスが違えば別のツリー** (ADR 0036)。

    観測した `i<N>` が記録と別のパスなら、それは別 clone の同番号ツリーか、記録が作業ツリー
    以外 (clone root 等) を指している。どちらも「在る」と読むと消失も残置も検出できなくなり、
    回収は記録と一致するツリーにしか効かないので、entry は動かないまま木が溜まる。
    """
    if (
        phase in vocabulary.WORKTREE_EXPECTED_PHASES
        and recorded is not None
        and worktree is None
    ):
        return [_drift("worktree_missing", issue_ref, phase, {"ledger_worktree": recorded})]
    if (
        worktree is not None
        and worktree_mod.same_worktree(recorded, worktree.get("path"), root) is False
    ):
        return [
            _drift(
                "worktree_path_mismatch",
                issue_ref,
                phase,
                {"ledger_worktree": recorded, "path": worktree.get("path")},
            )
        ]
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

    **PR は (repo, ref) で突き合わせる**。closes を repo で絞らなくなった (ADR 0036) ため
    観測には別 repo の同番号 PR が並びうるので、ref だけを鍵にすると他 repo の PR の status
    を自分の成果として読む。どの repo の PR を根拠に採るかは呼び出し側の判断なので、
    evidence にも repo を載せる。

    観測が `unmappable_prs` に落とした記録は判定から外す (#576)。PR 置き場で引けなかった記録を
    「観測したが見つからなかった」と読むと `status: null` の `pr_status_changed` が出て、**PR が
    消えたように見える** — 未観測は drift ではない。
    """
    observed_prs = (observation or {}).get("prs") or []
    unmappable = {
        (item.get("ref"), item.get("repo"))
        for item in (observation or {}).get("unmappable_prs") or []
    }
    found = []
    for recorded in recorded_prs:
        last_seen = recorded.get("last_seen_status")
        if last_seen is None or (recorded.get("ref"), recorded.get("repo")) in unmappable:
            continue
        matched, unmatchable = _match_recorded_pr(recorded, observed_prs)
        if unmatchable is not None:
            found.append(
                _drift(
                    unmatchable,
                    issue_ref,
                    phase,
                    {
                        "pr_ref": recorded.get("ref"),
                        "ledger_repo": recorded.get("repo"),
                        "repos": [
                            pr.get("repo")
                            for pr in observed_prs
                            if pr.get("ref") == recorded.get("ref")
                        ],
                    },
                )
            )
            continue
        current = (matched or {}).get("status")
        if current != last_seen:
            found.append(
                _drift(
                    "pr_status_changed",
                    issue_ref,
                    phase,
                    {
                        "pr_ref": recorded.get("ref"),
                        "repo": recorded.get("repo") or (matched or {}).get("repo"),
                        "last_seen_status": last_seen,
                        "status": current,
                    },
                )
            )
    if phase in vocabulary.IN_FLIGHT_PHASES:
        merged = [
            {"ref": pr["ref"], "repo": pr.get("repo")}
            for pr in observed_prs
            if pr.get("role") == "closes" and pr.get("status") == "merged"
        ]
        if merged:
            found.append(_drift("pr_merged", issue_ref, phase, {"prs": merged}))
    return found


def _match_recorded_pr(recorded, observed_prs):
    """台帳の PR record → (突き合わせた観測 PR, 突き合わせられない理由の drift kind)。

    記録が `repo` を持つなら (repo, ref) の完全一致で引く。持たない記録 (cross-repo 観測を
    入れる前の entry) は ref だけで引き、**候補が複数あるときは採らない** — 取り違えた
    status は「merged になった」も「なっていない」も同じ確からしさで誤るので、当てずっぽうの
    1 件目より「一意に決まらない」を報告するほうが安い。

    ref の候補は在るのに repo が一致しないときは、**status が変わったのではなく repo の綴りが
    違う** (記録は自由記述で、server は形を決めない)。`pr_status_changed` の status: null に
    潰すと「PR が消えた」と読めてしまうので、別の kind で返す。
    """
    ref, repo = recorded.get("ref"), recorded.get("repo")
    candidates = [pr for pr in observed_prs if pr.get("ref") == ref]
    if repo is not None:
        matched = next((pr for pr in candidates if pr.get("repo") == repo), None)
        if matched is None and candidates:
            return None, "pr_repo_mismatch"
        return matched, None
    if len(candidates) > 1:
        return None, "pr_ref_ambiguous"
    return (candidates[0] if candidates else None), None


def _scope_key(scope_ref):
    """scope 指定 → 突合に使う中立 ref (未指定なら None)。

    索引の鍵が ref なので、scope も ref のまま比べられる。別 tracker の ref を渡された場合は
    どの観測とも一致しないので、外部側 drift は自然に 1 件も出ない (番号空間を跨いだ取り違えを
    別途避ける必要が無い)。
    """
    return None if scope_ref is None else refs.parse_issue_ref(scope_ref)["ref"]


def _untracked_drift(panes_by_ref, worktrees_by_ref, joined_refs, scope_key):
    """台帳に対応 entry が無い pane / worktree (spec §3.5 が名指しする外部側の drift)。

    scope 指定時はその issue だけを見る — 絞り込んだ entry 列を台帳の全体と誤認すると、
    追跡中の他の worktree まで「台帳に無い」と報告してしまう。

    報告する `issue_ref` は索引の鍵そのもの。番号を持つ tracker の観測は既に持ち上げ済みで、
    key slug の観測は自分の ref を名乗っているので、**両者が同じ番号を共有していても取り違え
    ない** (`i14` は `gh#14`、`swatcf-14` は `jira:SWATCF-14` として別々に出る)。
    """
    found = []
    for issue_ref, worktree in sorted(worktrees_by_ref.items()):
        if issue_ref in joined_refs or (scope_key is not None and issue_ref != scope_key):
            continue
        found.append(
            _drift(
                "worktree_untracked",
                issue_ref,
                None,
                {"path": worktree.get("path"), "dirty": worktree.get("dirty")},
            )
        )
    for issue_ref, pane in sorted(panes_by_ref.items()):
        if issue_ref in joined_refs or (scope_key is not None and issue_ref != scope_key):
            continue
        found.append(
            _drift(
                "pane_untracked",
                issue_ref,
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


def _unmappable_observations(tracker, panes, worktrees):
    """番号を名乗る観測のうち、issue 置き場の tracker では中立 ref へ写せないもの。

    issue 置き場が番号体系を持たない tracker (Jira) だと `i<N>` の worktree / pane は索引に
    入らない。**黙って落とすと「台帳に無い worktree」としてすら現れず、残置に気づけない** ので、
    `unjoinable` / `derive_tidy_scope` の `unmappable` と同じ規則で観測結果に残す
    (`worktree.py` の `unmappable`)。drift ではない — 食い違いではなく、鍵を作れなかった観測。
    """
    found = []
    for item in worktrees or []:
        number = item.get("issue_number")
        if (
            item.get("issue_ref") is None
            and number is not None
            and refs.lift_issue_ref(tracker, item) is None
        ):
            found.append(
                {"store": "worktrees", "issue_number": number, "path": item.get("path")}
            )
    for item in panes or []:
        number = item.get("issue_number")
        if (
            not item.get("is_self")
            and item.get("issue_ref") is None
            and number is not None
            and refs.lift_issue_ref(tracker, item) is None
        ):
            found.append(
                {"store": "panes", "issue_number": number, "pane_id": item.get("pane_id")}
            )
    return found


def _index_by_ref(items, ref_of):
    """中立 issue ref → 要素。ref へ写せない要素は落とす (追跡対象でない pane / worktree)。"""
    index = {}
    for item in items or []:
        ref = ref_of(item)
        if ref is not None:
            index.setdefault(ref, item)
    return index


def _pane_ref(pane, tracker):
    """自 pane でない追跡 pane の中立 issue ref。

    持ち上げそのものは `refs.lift_issue_ref` が担い、本 module が足すのは自 pane を除く
    policy だけ。自 pane を除くのは `pane_watch` と同じ理由 — dispatcher 自身に残骸 label が
    付いていると、自分を「その issue の作業 pane」と誤認する。
    """
    return None if pane.get("is_self") else refs.lift_issue_ref(tracker, pane)
