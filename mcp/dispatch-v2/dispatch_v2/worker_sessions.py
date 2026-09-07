"""worker セッションの一生と、その足元の作業ツリーを組み立てる層。

台帳 (`ledger`) / runtime (`session_runtime`) / git (`git_worktrees`) の 3 つを結ぶのが本 module の
仕事で、**どれも自分では持たない** (引数で受ける)。HTTP / MCP の境界にこの組み立てを置かない
のは、境界は検証と変換だけの薄い配線に留めるため。

起動の順序が要点:

```
worktree を用意 (無ければ作り、在れば前の Session から引き継ぐ)
  └ Session を requested として記帳      ← runtime を呼ぶ前
      └ runtime へ launch
          ├ 失敗 → ended(launch_error)。WorkOrder は assigned のまま再発行できる
          └ 成功 → starting(handle)。alive への昇格は観測 (tick) が行う
```

**記帳を launch より先に置く**のは、起動が落ちた Session も台帳に残すため — v1 は起動失敗を
WorkOrder の終端 (`spawn_failed`) にしてしまい、再発行の系譜が切れていた。
"""

import sys
from datetime import datetime

from dispatch_v2 import (
    actors,
    git_worktrees,
    refs,
    session,
    session_runtime,
    session_vocabulary,
    vocabulary,
    worktree,
)

# `starting` に入ってから agent が現れるのを待つ上限 (秒)。**adapter ではなく観測側に置く** —
# daemon は serve と同じ 1 スレッドで回るので、adapter 内で poll すると待っている間 daemon 全体
# が止まる。上限を持たせるのは、起動直後にクラッシュした Session が `starting` に固着しないため
STARTING_GRACE_SEC = 60


class SpawnFailed(RuntimeError):
    """worker セッションを起動できなかった (Session は ended(launch_error) として残る)。"""


class SessionNotReady(RuntimeError):
    """まだ runtime に実行単位を持たない Session へ操作しようとした。

    **起動の失敗 (`SpawnFailed`) と分けてある** — こちらは呼び出し側の順序の問題で、
    runtime の不調ではない。同じ系統に混ぜると「herdr が落ちている」と「呼ぶ順序が違う」が
    呼び出し側から区別できなくなる。
    """


def spawn(book, *, runtime, run_git, wo_id, prompt, model, actor, clone_path, anchor):
    """WorkOrder に対して worker セッションを 1 つ起こす。

    返り値は `{"work_order", "session", "worktree"}`。起動に失敗したら `SpawnFailed` を上げるが、
    **台帳には起動を試みた Session が ended(launch_error) として残る**。

    `clone_path` は呼び出し側が解決した稼働 clone。**WorkOrder を引いた後に記帳する** —
    存在しない WorkOrder への spawn で clone の観測だけが正本に残らないように。

    `anchor` も呼び出し側の観測値 (`session_runtime.RuntimeAnchor`。観測していなければ
    `None`) で、そのまま runtime へ渡す — 本 module は割り元の採否を持たない (runtime 固有の
    判断なので adapter 側)。
    """
    work_order = book.get(wo_id)
    if work_order["phase"] in vocabulary.TERMINAL_PHASES:
        raise SpawnFailed(
            f"WorkOrder {wo_id} は {work_order['phase']} (終端) なので worker を起こさない"
        )
    book.observe_clone_path(clone_path=clone_path)
    tree = _ensure_worktree(book, run_git=run_git, work_order=work_order, actor=actor)
    label = refs.slug_of(work_order["issue_ref"])
    # **command の組み立ては記帳より前**。記帳と launch の間で落ちると Session が requested の
    # まま残り、観測もできず close もできず、その WorkOrder は二度と spawn できなくなる
    command = session_runtime.build_agent_command(prompt, name=label, model=model)
    requested = book.request_session(
        wo_id=wo_id, backend=runtime.backend, label=label, actor=actor
    )
    try:
        handle = runtime.launch(command=command, cwd=tree["path"], label=label, anchor=anchor)
    except session_runtime.LaunchFailed as exc:
        _end_as_launch_error(book, requested, actor=actor, handle=exc.handle)
        raise SpawnFailed(_launch_failure_text(requested, work_order, exc)) from exc
    except Exception as exc:  # noqa: BLE001 — requested のまま固着させない
        # 想定外の失敗でも Session は終わらせる。**再 spawn の道を残すのが目的**で、
        # 失敗そのものは SpawnFailed の message ごと呼び出し側へ上がる
        _end_as_launch_error(book, requested, actor=actor, handle=None)
        raise SpawnFailed(_launch_failure_text(requested, work_order, exc)) from exc
    started = book.transition_session(
        session_id=requested["session_id"], lifecycle="starting", handle=handle, actor=actor
    )
    return {"work_order": book.get(wo_id), "session": started, "worktree": tree}


def send(book, *, runtime, session_id, text):
    """走っている worker へテキストを届ける (台帳は動かない)。"""
    session_view = book.get_session(session_id)
    _require_handle(session_view)
    runtime.send(session_view["handle"], text)
    return session_view


def close(book, *, runtime, session_id, actor):
    """worker セッションを閉じる。runtime に既に無ければ `gone` として記帳する。

    **既に終わっている Session でも、runtime に実行単位が残っていれば閉じる**。台帳の
    lifecycle は動かさず (終端は不変)、資源だけを解放して同じ view を返す — 再実行しても
    同じ終状態へ収束させるため。「片付けは成功したのに失敗を報告する」経路を作らない。
    """
    session_view = book.get_session(session_id)
    _require_handle(session_view)
    sighted = runtime.sight(session_view["handle"])
    if session_view["lifecycle"] in session_vocabulary.TERMINAL_LIFECYCLES:
        if sighted is not None:
            runtime.close(session_view["handle"])
        return session_view
    if sighted is None:
        return book.transition_session(
            session_id=session_id, lifecycle="ended", reason="gone", actor=actor
        )
    runtime.close(session_view["handle"])
    return book.transition_session(
        session_id=session_id, lifecycle="ended", reason="closed_by_orchestrator", actor=actor
    )


def observe(book, *, runtime):
    """非終端 Session を 1 巡観測し、変化だけを記帳する。

    返すのは記帳した変化の列 (何も変わらなければ空)。**毎 tick 同じ観測を記帳しない** —
    正本は人が読む plain text なので、変化していない事実で埋めない。
    """
    now = book.now()
    changes = []
    for live in session.live_sessions(book.state):
        if live["handle"] is None:
            continue  # requested のまま = runtime にまだ何も無い (観測対象が無い)
        change = _observe_one(book, runtime=runtime, live=live, now=now)
        if change is not None:
            changes.append(change)
    return changes


def reclaim_report(book, *, run_git, runtime):
    """回収資格の機械判定と、台帳外の資源の報告 (**削除はしない**)。

    設計 Q8 の線: 判定は機械、削除は orchestrator の名指し 1 回。worktree と session の
    両方を 1 つの報告に組み立てるので、runtime も受け取る (呼び出し側で欄を埋め直させない)。
    """
    clone_path = book.clone_path()
    if clone_path is None:
        # **欄の形は通常経路と同じにする** — 早期 return で欄が欠けると、読み手は「無い」と
        # 「観測していない」を区別できない
        return {
            "clone_path": None,
            "candidates": [],
            **git_worktrees.empty_scan(),
            **_session_findings(book, runtime=runtime),
        }
    registered_entries = git_worktrees.registered(run_git, clone_path=clone_path)
    candidates = [
        worktree.reclaim_verdict(
            book.state,
            owned["wo_id"],
            git_worktrees.sight(run_git, clone_path=clone_path, path=owned["path"]),
        )
        for owned in worktree.owned_worktrees(book.state)
    ]
    scanned = git_worktrees.scan_orphans(
        clone_path,
        registered_entries=registered_entries,
        owned_paths=worktree.owned_paths(book.state),
    )
    return {
        "clone_path": clone_path,
        "candidates": candidates,
        **scanned,
        **_session_findings(book, runtime=runtime),
    }


def _session_findings(book, *, runtime):
    return {
        "orphan_sessions": orphan_sessions(book, runtime=runtime),
        "stranded_sessions": stranded_sessions(book, runtime=runtime),
    }


def orphan_sessions(book, *, runtime):
    """runtime に居るが台帳のどの Session でもない handle を**報告する** (閉じない)。

    label で dispatch 由来かを絞らないのは、**絞る規則そのものが誤検知の種**だから
    (別 project の pane を label だけで自分の物と読むのが v1 の実測での事故)。判断は
    orchestrator が読んで行う。
    """
    known = session.handles_in_ledger(book.state)
    return [
        {"handle": sighting.handle, "label": sighting.label}
        for sighting in runtime.sight_all()
        if sighting.handle not in known
    ]


def stranded_sessions(book, *, runtime):
    """終わった Session なのに runtime に実行単位が残っているものを**報告する** (閉じない)。

    `exited` (agent は居ないが実行単位は在る) と、起動途中で落ちた `launch_error` がここに出る。
    **台帳外ではないので `orphan_sessions` には出ない** — 誰も報告しないと、資源だけが静かに
    溜まる。閉じるのは orchestrator の `session_close` (終端 Session でも実行単位は閉じる)。
    """
    live_handles = {sighting.handle for sighting in runtime.sight_all()}
    return [
        {
            "session_id": one["session_id"],
            "wo_id": one["wo_id"],
            "handle": one["handle"],
            "ended_reason": one["ended_reason"],
        }
        for one in book.list_sessions()
        if one["lifecycle"] in session_vocabulary.TERMINAL_LIFECYCLES
        and one["handle"] in live_handles
    ]


def reclaim(book, *, run_git, wo_id, actor):
    """名指しされた WorkOrder の作業ツリーを 1 件だけ回収する。

    **資格を満たさなければ拒む** — 走査して該当を全部消す経路を持たないのが設計 Q8 の線。
    """
    clone_path = book.clone_path()
    if clone_path is None:
        raise git_worktrees.WorktreeError("稼働 clone をまだ観測していない (回収先が分からない)")
    recorded = worktree.require_owned_worktree(book.state, wo_id)
    owned = worktree.reclaim_verdict(
        book.state,
        wo_id,
        git_worktrees.sight(run_git, clone_path=clone_path, path=recorded["path"]),
    )
    if not owned["eligible"]:
        raise git_worktrees.WorktreeError(
            f"WorkOrder {wo_id} の worktree は回収資格を満たさない: "
            + ", ".join(worktree.BLOCKER_MEANINGS[blocker] for blocker in owned["blockers"])
        )
    removed = git_worktrees.remove(run_git, clone_path=clone_path, path=owned["path"])
    book.forget_worktree(wo_id=wo_id, actor=actor)
    return {"wo_id": wo_id, "path": owned["path"], "removed_from_git": removed}


# --- 組み立ての内訳 -----------------------------------------------------------


def _ensure_worktree(book, *, run_git, work_order, actor):
    clone_path = book.clone_path()
    if clone_path is None:
        raise SpawnFailed(
            "稼働 clone をまだ観測していない (session_spawn に repo_root を渡す)"
        )
    recorded = work_order["worktree"]
    tree = git_worktrees.ensure(
        run_git,
        clone_path=clone_path,
        slug=refs.slug_of(work_order["issue_ref"]),
        recorded=recorded,
        foreign_paths=worktree.owned_paths(book.state) - _own_paths(recorded),
    )
    if recorded is None:
        book.record_worktree(
            wo_id=work_order["wo_id"], path=tree["path"], branch=tree["branch"], actor=actor
        )
    return tree


def _end_as_launch_error(book, requested, *, actor, handle):
    """起動に失敗した Session を終わらせる。runtime に残った handle は先に記帳する。

    handle を捨てると、生きている実行単位が台帳のどこからも指されない残骸になる。
    """
    if handle is not None:
        book.transition_session(
            session_id=requested["session_id"], lifecycle="starting", handle=handle, actor=actor
        )
    book.transition_session(
        session_id=requested["session_id"], lifecycle="ended", reason="launch_error", actor=actor
    )


def _launch_failure_text(requested, work_order, exc):
    return (
        f"Session {requested['session_id']} の起動に失敗 (WorkOrder は "
        f"{work_order['phase']} のまま。同じ worktree へ再 spawn できる): {exc}"
    )


def _own_paths(recorded):
    return {recorded["path"]} if recorded else set()


def _observe_one(book, *, runtime, live, now):
    sighting = runtime.sight(live["handle"])
    reason = _ended_reason(live, sighting, now=now)
    if reason is not None:
        return book.transition_session(
            session_id=live["session_id"],
            lifecycle="ended",
            reason=reason,
            actor=actors.RECONCILER,
        )
    if sighting is None or not sighting.agent_present:
        return None  # 起動待ちの猶予の中。まだ何も記帳しない
    if live["lifecycle"] == "starting":
        book.transition_session(
            session_id=live["session_id"], lifecycle="alive", actor=actors.RECONCILER
        )
    activity = runtime.classify_activity(sighting.activity_raw)
    current = book.get_session(live["session_id"])
    if (current["activity"], current["activity_raw"]) == (activity, sighting.activity_raw):
        return None
    return book.observe_session_activity(
        session_id=live["session_id"], activity=activity, activity_raw=sighting.activity_raw
    )


def _ended_reason(live, sighting, *, now):
    """観測から終わり方を読む。`starting` の猶予の中なら None (まだ終わっていない)。

    **agent が現れるまでには数秒〜数十秒かかる** (v1 は adapter 側で 5 回 × 2 秒 poll していた)。
    v2 の daemon は単一スレッドで serve も兼ねるので adapter を待たせず、観測側で猶予を持つ:
    `starting` に入ってから `STARTING_GRACE_SEC` を過ぎても agent が現れなければ `exited`。
    猶予を無条件に外すと、起動直後の worker が毎回その場で終了扱いになる。
    """
    if sighting is None:
        return "gone"  # 実行単位ごと消えたのは猶予の対象ではない
    if sighting.agent_present:
        return None
    if live["lifecycle"] == "starting" and _elapsed_sec(live["updated_at"], now) < STARTING_GRACE_SEC:
        return None
    return session_runtime.ended_reason_for(sighting)


def _elapsed_sec(since, now):
    """台帳の ISO8601 (UTC・秒精度) 2 点の差。

    読めない値は猶予を与えない側へ倒すが、**黙って倒さない** — 時刻が読めないまま
    「起動直後の worker が exited になる」のは、この猶予が防ごうとしている症状そのもの。
    """
    try:
        return (datetime.fromisoformat(now) - datetime.fromisoformat(since)).total_seconds()
    except (TypeError, ValueError) as exc:
        print(
            f"[dispatch-v2] 台帳の時刻を読めないので猶予を与えずに判定する "
            f"({since!r} → {now!r}: {exc})",
            file=sys.stderr,
        )
        return STARTING_GRACE_SEC


def _require_handle(session_view):
    if session_view["handle"] is None:
        raise SessionNotReady(
            f"Session {session_view['session_id']} はまだ runtime に何も持っていない "
            f"({session_view['lifecycle']})"
        )
