"""台帳の書き込み面。1 project = 1 台帳 (`<project dir>/events.jsonl`)。

書き込みは常に **検証 → 追記 → memory の state へ適用** の順で回す。追記の後で適用が失敗
すると、正本に「読み戻せない event」が残って次回の起動ごと落ちるため。

state は `fold` が持つ形をそのまま memory に置く (ADR 0057)。**state file は書かない** —
台帳ディレクトリに残るのは events.jsonl だけで、現況は起動のたびに畳み直す。

**並行制御を持たない**のは、書き手が daemon 1 プロセスに集約されているから (ADR 0056 /
0057)。この class を 2 プロセスから同じディレクトリへ向けるのは契約違反で、それを止めるのは
daemon 側の lock。
"""

from pathlib import Path

from dispatch_v2 import actors, event_log, fold, project, refs, ulid


class LedgerError(RuntimeError):
    """台帳への記帳の前提が成立しない (引数が中立語彙に合わない等)。"""


class Ledger:
    """1 project 分の台帳 (events.jsonl + memory 上の fold)。"""

    def __init__(self, log, state, *, clock, new_id, directory, revision):
        self.log = log
        self.state = state
        # 台帳が持つ event の数。**投影 (`materialized_view`) が「台帳が動いたか」を測る目盛**で、
        # 記帳のたびに 1 進む。state の中身を比べる代わりにこれを見れば、動いていない project の
        # 投影を毎ループ書き直さずに済む
        self.revision = revision
        # 台帳ディレクトリ。宣言 config (`dispatch-project.toml`) が同じ場所に居るので、
        # 読み手が log の path から逆算しないで済むように持たせる
        self.directory = Path(directory)
        self._clock = clock
        self._new_id = new_id

    @classmethod
    def open(cls, directory, *, clock=event_log.now_iso, new_id=ulid.new_ulid):
        """台帳を開く: 千切れた末尾の収束 → event 列の fold。

        **起動のたびに走らせてよい** (収束は冪等)。
        """
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        log = event_log.EventLog(directory / project.EVENTS_FILENAME, clock=clock)
        log.recover()
        events = log.read()
        return cls(
            log,
            fold.fold(events),
            clock=clock,
            new_id=new_id,
            directory=directory,
            revision=len(events),
        )

    # --- 記帳 -----------------------------------------------------------------

    def create_work_order(self, *, issue_ref, note, actor):
        """issue 1 件への着手意図を新しい WorkOrder として記帳する。

        同一 issue の非終端 WorkOrder が既にあれば拒む (制約は fold が持つ)。
        """
        try:
            issue_ref = refs.require_issue_ref(issue_ref)
        except refs.RefError as exc:
            raise LedgerError(str(exc)) from exc
        payload = {"wo_id": self._new_id(), "issue_ref": issue_ref, "note": _clean_note(note)}
        return self._record("wo_created", payload, actor=actor, wo_id=payload["wo_id"])

    def transition(self, *, wo_id, phase, note, actor, evidence=None):
        """phase を遷移させる (合法性は fold が検証する)。

        Args:
            evidence: 機械遷移の根拠 (観測事実そのもの)。**意図の遷移では None** —
                根拠を持つのは観測から確定した遷移だけで、人の判断の根拠は note が持つ
        """
        payload = {
            "wo_id": wo_id,
            "phase": phase,
            "note": _clean_note(note),
            "evidence": evidence,
        }
        return self._record("wo_transitioned", payload, actor=actor, wo_id=wo_id)

    def annotate(self, *, wo_id, note, actor):
        """phase を変えずに note を置き換える (次セッションへの引き継ぎの更新)。"""
        cleaned = _clean_note(note)
        if cleaned is None:
            raise LedgerError("note が空 (引き継ぎを消すだけの annotate は受け付けない)")
        return self._record(
            "wo_annotated", {"wo_id": wo_id, "note": cleaned}, actor=actor, wo_id=wo_id
        )

    def report_outcome(self, *, wo_id, outcome, summary, actor):
        """worker が自分の担当の顛末を自己申告する (phase は動かさない)。

        **phase 遷移と分けてある** — 顛末は worker が知っている事実で、WorkOrder を終端へ
        送るかどうかは orchestrator の判断 (または merge 観測からの機械遷移) だから。
        """
        cleaned = _clean_note(outcome)
        if cleaned is None:
            raise LedgerError("outcome が空 (何をどう終えたかを 1 行で名乗る)")
        payload = {"wo_id": wo_id, "outcome": cleaned, "summary": _clean_note(summary)}
        return self._record("wo_outcome_reported", payload, actor=actor, wo_id=wo_id)

    # --- Session の記帳 -------------------------------------------------------

    def request_session(self, *, wo_id, backend, label, actor):
        """WorkOrder に対して worker セッションの起動を依頼した事実を記帳する。

        **runtime を呼ぶ前に記帳する** — 起動が落ちても「起動を試みた Session」が残り、
        ended(launch_error) まで含めた一生が台帳から読める (設計 シナリオ 2)。
        """
        session_id = self._new_id()
        payload = {
            "session_id": session_id,
            "wo_id": wo_id,
            "backend": backend,
            "label": label,
        }
        self._append("session_requested", payload, actor=actor)
        return dict(self.state["sessions"][session_id])

    def transition_session(self, *, session_id, lifecycle, actor, handle=None, reason=None):
        """Session の lifecycle を進める (合法性と reason の要否は fold が検証する)。"""
        payload = {
            "session_id": session_id,
            "lifecycle": lifecycle,
            "handle": handle,
            "reason": reason,
        }
        self._append("session_lifecycle_changed", payload, actor=actor)
        return dict(self.state["sessions"][session_id])

    def observe_session_activity(self, *, session_id, activity, activity_raw):
        """alive な Session の activity を記帳する (中立値 + runtime の生値)。

        actor を引数に取らないのは、**activity が観測でしか生まれない**ため — 誰かが
        「今 blocked である」と主張して書ける欄にすると、観測と主張が同じ欄に混ざる。
        """
        payload = {
            "session_id": session_id,
            "activity": activity,
            "activity_raw": activity_raw,
        }
        self._append("session_activity_observed", payload, actor=actors.RECONCILER)
        return dict(self.state["sessions"][session_id])

    # --- Worktree と稼働 clone の記帳 -----------------------------------------

    def record_worktree(self, *, wo_id, path, branch, actor):
        """WorkOrder が作業ツリーを所有したことを記帳する。"""
        payload = {"wo_id": wo_id, "path": path, "branch": branch}
        return self._record("worktree_created", payload, actor=actor, wo_id=wo_id)

    def forget_worktree(self, *, wo_id, actor):
        """作業ツリーを回収したことを記帳する (削除の実行は呼び出し側)。"""
        return self._record("worktree_removed", {"wo_id": wo_id}, actor=actor, wo_id=wo_id)

    # --- CL 記録の記帳 (tracker から紐づきを引けない置き場の join) ---------------

    def record_cl(self, *, wo_id, cl_ref, repo, role, actor):
        """WorkOrder に issue → CL の紐づきを記帳する (`cl_record`)。

        同じ CL ref の再記帳は**置換**なので、repo や role の訂正はそのまま撃ち直せる。
        """
        payload = {"wo_id": wo_id, "cl_ref": cl_ref, "repo": repo, "role": role}
        return self._record("cl_recorded", payload, actor=actor, wo_id=wo_id)

    def forget_cl(self, *, wo_id, cl_ref, actor):
        """記帳した紐づきを取り消す (誤った CL ref を機械遷移の根拠から外す唯一の経路)。"""
        payload = {"wo_id": wo_id, "cl_ref": cl_ref}
        return self._record("cl_forgotten", payload, actor=actor, wo_id=wo_id)

    def observe_clone_path(self, *, clone_path):
        """稼働 clone の path を観測として記帳する。同じ path の再観測は追記しない。

        追記を抑えるのは、spawn のたびに同じ事実が正本へ積み上がるのを避けるため
        (正本は人が grep で診断する — ADR 0057)。
        """
        if self.state["clone_path"] == clone_path:
            return self.state["clone_path"]
        self._append("project_clone_observed", {"clone_path": clone_path}, actor=actors.RECONCILER)
        return self.state["clone_path"]

    def deliver_escalation(self, *, rule_id, dedup_key, evidence, wo_id=None, permanent=False):
        """判断を LLM へ委ねる事実を 1 回配送する。配送しなかったときは None。

        **dedup と打ち切りをここ 1 箇所に閉じる** — rule 側は「今 tick で条件が当たった」だけを
        報告し、それが初回なのか再送なのか打ち切り済みなのかを持たない。rule ごとに数える形に
        すると rule の数だけ打ち切りの実装が要り、揺れる。

        分岐は 3 つ:

        - 同じ dedup_key の escalation が無い → 新規に積む (配送 1 回目)
        - あって未 ack かつ上限未満 → 再送を記帳する (配送回数 +1)
        - あって ack 済み / 上限到達 → **何もしない** (ack で終了・3 回で打ち切り)

        `permanent` は**事象の性質の宣言**で、呼び出し側の分岐ではない。再試行では直らない
        不成立 (git repo でない / upstream 未設定 / detached HEAD 等) を真で渡すと、**配送は
        最初の 1 度きり**になる — 直らない条件を再送に乗せると、上限まで同じ inbox 行を
        3 回押し付けたうえで打ち切る (ADR 0047「最初の 1 度だけ escalation し、以後スキップ。
        観測失敗カウンタに数えず」)。**宣言は初回の raise で台帳へ入り、以後の打ち切りは
        record の `permanent` が根拠になる** — 打ち切ったことが state に残らないと、読み手は
        `attempts` が 1 のまま止まっているのを配送中と区別できない (gh#930)。

        dedup_key は呼び出し側が**毎 tick 観測値から再計算する**。tick を跨いで持ち回るのは
        台帳 (= 正本) 側だけなので、daemon の再起動で打ち切りが 0 に戻らない。
        """
        existing = fold.escalation_for_dedup_key(self.state, dedup_key)
        if existing is None:
            escalation_id = self._new_id()
            self._append(
                "escalation_raised",
                {
                    "escalation_id": escalation_id,
                    "rule_id": rule_id,
                    "dedup_key": dedup_key,
                    "evidence": evidence,
                    "wo_id": wo_id,
                    "permanent": permanent,
                },
                actor=actors.RECONCILER,
            )
            return dict(self.state["escalations"][escalation_id])
        # **どちらかが打ち切りを名乗れば止める**。record 側だけを見ると、本欄が入る前に上がって
        # まだ ack されていない escalation が再送に復活する (古い event は偽に畳まれる)。引数側
        # だけを見ると、渡し忘れた tick で同じことが起きる
        if permanent or existing["permanent"]:
            return None
        if existing["acked_at"] is not None:
            return None
        if existing["attempts"] >= fold.MAX_ESCALATION_ATTEMPTS:
            return None
        self._append(
            "escalation_resent",
            {"escalation_id": existing["escalation_id"]},
            actor=actors.RECONCILER,
        )
        return dict(self.state["escalations"][existing["escalation_id"]])

    def ack_escalation(self, *, escalation_id, actor):
        """escalation を受け取ったことを記帳する (再送の打ち切り契機)。"""
        self._append("escalation_acked", {"escalation_id": escalation_id}, actor=actor)
        return dict(self.state["escalations"][escalation_id])

    def record_candidate_observation(self, *, candidates, engaged):
        """観測した候補集合と、そのとき着手中だった issue を記帳する (次 tick の差分の基準)。

        **前回と同じなら書かない** — 毎 tick 同じ列を追記すると、正本が観測の履歴ではなく
        tick の心拍で埋まり、grep で診断する用途が潰れる (ADR 0057)。
        """
        # **正本には観測したとおりを書き、比較だけ順序非依存で行う**。`gh issue list` の並びは
        # 更新順なので、同じ候補集合でも順序は毎 tick 動く — 順序込みで比べると正本が tick の
        # 心拍で埋まる。かといって整列済み集合を書くと、正本が「観測した列」ではなくなる
        current = {"candidates": list(candidates), "engaged": list(engaged)}
        if _as_sets(fold.candidate_observation(self.state)) == _as_sets(current):
            return None
        return self._append("candidates_observed", current, actor="reconciler")

    # --- 読み -----------------------------------------------------------------

    def get(self, wo_id):
        """WorkOrder 1 件 + 同一 issue の系譜 + その WorkOrder の Session 系譜。"""
        view = dict(fold.require_work_order(self.state, wo_id))
        view["lineage"] = fold.lineage_of(self.state, view["issue_ref"])
        view["sessions"] = fold.sessions_of(self.state, wo_id)
        return view

    def get_session(self, session_id):
        """Session 1 件 (lifecycle の事実と activity の観測)。"""
        return dict(fold.require_session(self.state, session_id))

    def list_sessions(self):
        """記帳順の Session 一覧 (終わったものも残る — 再発行の系譜が読めるように)。"""
        return [dict(session) for session in self.state["sessions"].values()]

    def clone_path(self):
        """最後に観測した稼働 clone の path (未観測なら None)。"""
        return self.state["clone_path"]

    def now(self):
        """台帳が記帳に使っている時刻 (ISO8601)。観測の経過時間はこれを基準に測る。

        呼び出し側が実時刻を直接読むと、台帳の ts と別の時計で経過を測ることになり、
        テストが時刻を固定しても観測側だけが実時刻で動く。
        """
        return self._clock()

    def list_work_orders(self, *, phases=None, issue_ref=None):
        """記帳順の WorkOrder 一覧 (系譜は載せない — 1 件の詳細は `get`)。"""
        return [
            dict(work_order)
            for work_order in self.state["work_orders"].values()
            if (phases is None or work_order["phase"] in phases)
            and (issue_ref is None or work_order["issue_ref"] == issue_ref)
        ]

    def read_inbox(self):
        """まだ ack されていない escalation。"""
        return [dict(escalation) for escalation in fold.pending_escalations(self.state)]

    def anomalies(self):
        """正本から捨てた欠け (千切れた末尾行) の記録。"""
        return list(self.state["anomalies"])

    # --- 記帳の共通経路 -------------------------------------------------------

    def _record(self, event_type, payload, *, actor, wo_id):
        self._append(event_type, payload, actor=actor)
        return self.get(wo_id)

    def _append(self, event_type, payload, *, actor):
        event = event_log.make_event(event_type, payload, actor=actor, ts=self._clock())
        fold.require_applicable(self.state, event)
        self.log.append(event)
        fold.apply_event(self.state, event)
        self.revision += 1
        return event


def _as_sets(observation):
    """候補観測を順序非依存の形へ均す (記帳するかの判定にだけ使う)。"""
    if observation is None:
        return None
    return {key: frozenset(value) for key, value in observation.items()}


def _clean_note(note):
    """空白だけの note は「無い」として扱う (空文字が引き継ぎとして残らないように)。"""
    if note is None:
        return None
    stripped = note.strip()
    return stripped or None
