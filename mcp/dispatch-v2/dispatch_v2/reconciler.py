"""polling tick — 観測 → 記帳 → escalation。reconciler の判断を持たない中核 (ADR 0056)。

**機械が自律で行ってよいのは記帳と escalation の発行だけ** (設計 §7)。tracker への label 付与も
pane の close も worktree の削除もここからは起こさない。

**rule の条件は本 module に無い** — 11 本すべてが `escalation_rules` の宣言的な catalog にあり、
本 module がするのは「観測して subject を組み、catalog を当て、返ってきた Finding を配送する」
だけ。観測の置き場と rule の scope が 1 対 1 に対応する:

| scope | 何を観測して組むか | rule |
|---|---|---|
| `work_order` | 非終端 WorkOrder ごとの issue / closes CL / Session | #1〜#7 |
| `candidate_pool` | 候補集合と前回との差分 | #8 |
| `store` | 同一 store の連続観測失敗 | #9 |
| `resources` | 台帳外の worktree | #10 |
| `effect` | 機械作用 (deploy) の結果 | #11 |

rule ではないものが 1 つある: **完了の機械遷移** (Session 不在 ∧ issue closed → closes CL
merged なら completed / 無ければ abandoned)。escalation にすると issue が閉じるたび LLM が
起きるので、判断ではなく記帳として扱う (設計 決定 22)。

**tick の入口は本 module 1 つ**にしてある — daemon にスレッドを 2 本生やさないため。

## job は周期が違う (設計 §9 の polling 既定)

```
sessions  10 秒 … SessionRuntime の観測 (lifecycle と activity)
stores    60 秒 … tracker / ChangeHost の観測 (候補集合と完了の機械遷移)
effects   60 秒 … 有効な機械作用 (deploy 等)
resources 300 秒 … worktree の台帳外検出
```

**server は最短周期で毎回知らせるだけ**で、どの job が今 due かは project ごとの `Schedule` が
決める (`PacedTick` が pacing、`Schedule` が job の割り振り)。周期を server 側に置くと、周期の
違う観測を足すたびに server を変えることになる。

**置き場の宣言 (`dispatch-project.toml`) が要るのは `stores` だけ**。宣言が無い project でも
台帳だけで回る job (sessions / effects / resources) は動く — 宣言の欠落で機械作用まで止めると、
「置き場をまだ宣言していない project では deploy も走らない」という無関係な巻き添えが出る。

## 単一スレッドを保つ

tick は `service_actions()` (serve_forever が同一スレッドで毎ループ呼ぶ) から駆動する。別
スレッドで回すと event log の書き手が 2 つになり、`http_app` が「単一スレッドなので追記の
直列性が構造で真になる」と置いている不変条件が崩れる (`principle-isolate-shared-writes`)。

**代償は tick の間 HTTP が止まること**。各 adapter の `SUBPROCESS_TIMEOUT_SEC` が抑えるのは
CLI 1 回ぶんで、rule #1〜#7 は非終端 WorkOrder ごとに issue + 紐づき + closes CL 1 件ごとの
観測を要求するので、**WorkOrder が増えるほど tick が伸びる**。

止まるのは HTTP = **escalation を pull する経路そのもの**なので、伸びを放置すると「nudge が
届かなくても inbox pull で読める」という配送の前提を、こちらの追加で壊すことになる。そこで
`WORK_ORDER_BUDGET_SEC` で 1 tick に使う観測時間を区切り、**続きは次の tick へ持ち越す**
(`_settle_cursors` が最後に観測した WorkOrder を覚え、次の tick はその次から回る)。予算を
使い切っても取りこぼしが恒久化しないのはこの巡回のため。

**予算が抑えるのは WorkOrder 数に比例する増分だけで、tick 全体への締切は依然として無い** —
候補観測も `run_jobs` (sessions / effects / resources) も予算の外を走り、CLI 1 回ぶんを抑える
のは各 subprocess の timeout だけ。**台帳が育った後に tick が client の応答待ちを超えて伸びる
可能性は残る** (未着手)。
"""

import sys
import time
import traceback

from dispatch_v2 import (
    adapters,
    cl_record,
    declaration,
    escalation_rules,
    fold,
    git_worktrees,
    observation,
    ports,
    project,
    refs,
    rule_catalog,
    session,
    session_runtime,
    vocabulary,
    worker_sessions,
)

# job ごとの polling 周期 (設計 §9)
TICK_INTERVAL_SEC = 60  # tracker / CL の観測
SESSION_INTERVAL_SEC = 10  # SessionRuntime の観測
EFFECT_INTERVAL_SEC = 60  # 有効な機械作用
RESOURCE_INTERVAL_SEC = 300  # 台帳外資源の巡回

# server へ知らせる間隔。**最短の job 周期に合わせる** — ここが粗いと、どの job も自分の周期
# より遅くしか回らない (job の周期は `Schedule` が持つので、ここは pacing だけを決める)
PACE_INTERVAL_SEC = SESSION_INTERVAL_SEC

# 1 tick で **WorkOrder の観測に**使ってよい時間。超えたら残りは次の tick へ回す (rule 評価に
# 必要な観測が WorkOrder 数に比例して増えるため)。tracker の観測周期 (60 秒) の 1/3。
#
# **tick 全体の締切ではない** — 候補観測も `run_jobs` (sessions / effects / resources) もこの
# 外側で走り、1 件目の WorkOrder 観測は予算に関わらず最後まで走る (巡回を止めないため)。
# 抑えられるのは「WorkOrder が増えたぶんだけ tick が伸びる」という本 issue が足した増分だけ
WORK_ORDER_BUDGET_SEC = 20

# bind から最初の tick までの猶予。**lazy 起動した client の health 1 往復を通すため**の間で、
# 観測の周期とは別物 (詳細は `PacedTick`)。tick は serve と同じスレッドなので、この猶予が無いと
# 起動直後の観測が accept を塞ぎ、client が「daemon が居ない」と読んで二重起動する
FIRST_TICK_DELAY_SEC = 2

# 観測 store の名前 (連続失敗を数える単位)。issue 置き場と CL 置き場は別 tracker でよいので
# 別々に数える — 片方の障害でもう片方の観測まで「落ちている」と報告しない
STORE_TRACKER = "tracker"
STORE_CHANGE_HOST = "change_host"

#: `sessions_of` が返す観測の出所。**evidence に必ず載る** — 機械遷移は terminal (不変) を
#: 確定させるので、「Session 不在」が何を根拠にした不在なのかを後から人が読めないと、
#: 誤分類を監査できない (ADR 0055 の「訂正は annotate + 新 WorkOrder」が成立する前提)。
#: 返り値の形の正本は `ports.session_observation`
SESSIONS_FROM_LEDGER = "ledger_live_sessions"


class Schedule:
    """job ごとの次回実行時刻。**monotonic を引数で受ける**ので実時刻を待たずに検証できる。"""

    def __init__(self):
        self._due_at = {}

    def due(self, job, interval, now):
        """その job を今走らせてよいか。走らせるなら次回時刻を進める。"""
        if now < self._due_at.get(job, 0):
            return False
        self._due_at[job] = now + interval
        return True


class TickContext:
    """1 tick 1 project ぶんの文脈。

    観測・記帳・escalation の各段が同じ 6 つ (台帳 / cache / 2 port / 宣言 / 観測時刻) を必ず
    一緒に必要とするので、**組にして 1 つ渡す**。位置引数で持ち回ると引数順の取り違えが
    型でも名前でも止まらない。`now` を context が持つことで、**1 tick の全観測に同じ観測時刻が
    付く**ことが構造で決まる (連続失敗の同一 tick 判定がこれに依存している)。
    """

    def __init__(self, *, project_key, book, cache, tracker, change_host, declared, now):
        self.project_key = project_key
        self.book = book
        self.cache = cache
        self.tracker = tracker
        self.change_host = change_host
        self.declared = declared
        self.now = now

    @property
    def cl_links(self):
        """紐づき (issue → CL) の源。tracker から引ける置き場ならその adapter、引けなければ台帳。

        **選択の分岐はここ 1 箇所**で、判定は class の部分型関係が持つ (`ports.CLLinkPort` を
        実装するか) — adapter 名で分岐すると、adapter を足すたびにここへ書き足す必要が出る。
        台帳が源になる置き場の理由と代償は `cl_record` の docstring にある。
        """
        if isinstance(self.tracker, ports.CLLinkPort):
            return self.tracker
        return cl_record.LedgerCLLinks(self.book)

    @property
    def issue_repo(self):
        return self.declared["issue"]["repo"]

    @property
    def ready_label(self):
        return self.declared["issue"]["ready_label"]


class Reconciler:
    """observation cache を tick を跨いで保持し、1 tick ぶんの観測と記帳を回す。

    project ごとの cache を抱えるので、**daemon が 1 つ持って使い回す** (tick ごとに作ると
    候補集合の差分も連続失敗も毎回リセットされる)。
    """

    def __init__(
        self,
        registry,
        *,
        runtime=None,
        run_git=project.run_git,
        build_adapters=None,
        sessions_of=None,
        monotonic=time.monotonic,
        log=sys.stderr,
    ):
        self.registry = registry
        # runtime を持たない Reconciler は**台帳だけで回る job も含めて runtime 依存を飛ばす**
        # (`Api` と同じ線)。組み立て経路では必ず渡るので、既定を herdr にはしない —
        # reconciler が特定 backend を知らないことが port を分けた理由そのもの
        self._runtime = runtime
        self._run_git = run_git
        # 宣言 → (Tracker, ChangeHost) の組み立て。差し替えられるのは、外部 CLI を起こさずに
        # tick の振る舞いを検査するため (`principle-test-double-boundary`: gh は unmanaged)
        self._build_adapters = build_adapters or adapters_for
        self._sessions_of = sessions_of or self._sessions_from_ledger
        self._monotonic = monotonic
        self._log = log
        self._caches = {}
        self._schedules = {}
        self._declaration_complaints = set()
        # 届かなかった空 nudge の宛先 (成功したら忘れる。`_note_nudge_failure`)
        self._failed_nudges = set()
        # 1 tick の予算を使い切った位置。次の tick はその次の WorkOrder から回る
        self._settle_cursors = {}
        # 空 nudge の宛先 (project ごと)。**台帳へは書かない** — runtime handle は
        # orchestrator が起動し直すたび変わる揮発値で、append-only の正本 (人が grep で
        # 診断する — ADR 0057) に心拍として積む種類の事実ではない。daemon が落ちれば宛先も
        # 失われるが、そのとき失われるのは nudge だけで escalation は inbox に残る
        self._notify_targets = {}

    def aim_nudge_at(self, project_key, handle):
        """その project の空 nudge の宛先を覚える。

        **inbox を pull した相手を宛先にする** — 配送の正は pull なので、起こすべき相手は
        「前回 pull した誰か」以外に定義しようがない。宛先を宣言 config に置くと揮発値を人が
        書き換え続けることになり、daemon の起動元 pane に固定すると lazy 起動の競合次第で
        無関係な worker を起こす。

        **忘れる経路は持たない** — 宛先が古びても撃ち損じるだけで、escalation は inbox に
        残る。忘れる要件が出た時点で足す。
        """
        self._notify_targets[project_key] = handle

    def nudge_target(self, project_key):
        """その project の空 nudge の宛先 (まだ誰も pull していなければ None)。"""
        return self._notify_targets.get(project_key)

    def _sessions_from_ledger(self, project_key, wo_id):
        """Session 生死の既定の観測: **台帳が持つ非終端 Session**。

        runtime を直に問い合わせないのは、lifecycle の記帳が `sessions` job の担当だから —
        観測の入口を 1 つに保つと、機械遷移が読む「不在」と台帳の Session 記録が食い違わない。
        """
        live = session.live_session_for(self.registry.ledger_for(project_key).state, wo_id)
        return ports.session_observation(
            sessions=[live["session_id"]] if live is not None else [],
            source=SESSIONS_FROM_LEDGER,
        )

    def cache_for(self, project_key):
        """project の観測 cache (無ければ作る)。"""
        if project_key not in self._caches:
            self._caches[project_key] = observation.ObservationCache()
        return self._caches[project_key]

    def run_tick(self, now, monotonic=None):
        """registry が知っている全 project を 1 巡する。返り値は project ごとの結果 (診断用)。

        巡る前に root を走査し直す (`LedgerRegistry.adopt_existing_projects`) — 起動後に
        置かれた台帳 / 宣言を次の tick から観測するため。走査自体の失敗で tick を止めない。
        """
        try:
            self.registry.adopt_existing_projects()
        except Exception:  # noqa: BLE001 — 走査の失敗で全 project の tick を止めない
            self._report_unexpected("project の走査")
        return [self._tick_one(key, now, monotonic) for key in self.registry.known_projects()]

    def _tick_one(self, project_key, now, monotonic=None):
        """1 project を巡り、**その project の失敗を他へ波及させない**。

        `tick_project` が想定する失敗 (観測失敗・宣言の不備) は中で畳まれるが、台帳側の失敗
        (`FoldError` / 追記の `OSError`) はここまで上がる。掬わないと `sorted()` 順で後ろの
        project がその tick 以降ずっと観測されない — 起動時に全台帳を迎え入れるので、
        1 件の破損がマシン上の全 project を止めることになる。
        """
        try:
            return self.tick_project(project_key, now, monotonic)
        except Exception as exc:  # noqa: BLE001 — project 境界での隔離がこの 1 箇所
            reason = f"tick が失敗: {type(exc).__name__}: {exc}"
            # **cache にも残す**。log の重複抑止は同じ文面を 2 度出さないので、決定的に落ち
            # 続ける tick は最初の 1 行きり黙る。tool 側が「まだ観測が成功していない」(待てば
            # 埋まる) と答え続けるのを防ぐには、理由が読める場所へ置くしかない
            self.cache_for(project_key).set_skip_reason(reason)
            self._complain_once(project_key, reason)
            return {"project_key": project_key, "observed": False, "reason": reason}

    def tick_project(self, project_key, now, monotonic=None):
        """project 1 つを観測して記帳する。**due な job だけ**を回す。

        `monotonic` は job の周期を測る単調時刻 (未指定なら組み立て時に受けた時計)。`now` の
        ISO 時刻とは別物 — 前者は間隔の計測、後者は記帳に載る観測時刻。
        """
        elapsed = self._monotonic() if monotonic is None else monotonic
        schedule = self._schedules.setdefault(project_key, Schedule())
        book = self.registry.ledger_for(project_key)
        # **配送された escalation の数を tick の前後で比べる**。配送は複数の job に散っている
        # ので、collector を job へ配って回るより正本から数えるほうが取りこぼさない
        delivered_before = fold.delivered_escalation_count(book.state)
        if self._runtime is not None:
            run_jobs(
                book,
                runtime=self._runtime,
                run_git=self._run_git,
                schedule=schedule,
                now=elapsed,
                log=self._log,
            )
        report = (
            self._observe_stores(project_key, now, book)
            if schedule.due("stores", TICK_INTERVAL_SEC, elapsed)
            else {"project_key": project_key, "observed": False, "reason": None}
        )
        self._nudge_if_delivered(project_key, book, delivered_before)
        return report

    def _nudge_if_delivered(self, project_key, book, delivered_before):
        """この tick で escalation を配送していたら、宛先を**空 nudge で 1 度だけ**起こす。

        **1 tick に 1 回**。escalation 1 件ごとに撃つと、5 件立った tick が 5 回 worker を
        叩き起こす — nudge は「inbox を見に来い」以上の意味を持たないので、回数に意味が無い。

        **どう失敗しても観測を巻き込まない**。配送の正は inbox の pull で、nudge は起こすだけ
        (設計 決定 11)。ここから例外を上げると、届かなかった nudge が tick ごと落として、
        同じ tick で成功していた store 観測まで「まだ観測できていない」に化ける — 一番弱い
        経路が一番強い保証を壊す形になる。

        **loud さは失敗の種類で分ける**:

        - 届かなかった (`SessionRuntimeError`) → 宛先ごとに 1 度だけ log。宛先が古びるのは
          日常で、毎 tick 出すと 60 秒ごとに同じ行が積まれる
        - それ以外 (port の実装漏れ等) → **毎 tick traceback ごと log**。直るまで直らない
          種類の失敗なので、抑止すると port を実装し損ねた backend が静かに回り続ける
        """
        if fold.delivered_escalation_count(book.state) == delivered_before:
            return
        handle = self.nudge_target(project_key)
        if handle is None or self._runtime is None:
            # まだ誰も inbox を pull していない (= 起こす相手が観測できていない)。
            # pull されれば宛先が決まり、escalation は inbox に残ったまま読まれる
            return
        try:
            self._runtime.notify(handle)
        except session_runtime.SessionRuntimeError as exc:
            self._note_nudge_failure(project_key, handle, exc)
            return
        except Exception:  # noqa: BLE001 — nudge の失敗で観測を止めない (docstring 参照)
            self._report_unexpected("空 nudge の配送")
            return
        self._failed_nudges.discard((project_key, handle))

    def _note_nudge_failure(self, project_key, handle, error):
        """届かなかった nudge を **その宛先につき 1 度だけ** log へ出す。

        宛先ごとに覚えるので、pane が直って再び壊れれば 2 度目も出る (成功で忘れる)。
        毎 tick 出すと 60 秒ごとに同じ行で log が埋まり、1 度きりにすると壊れっぱなしの
        宛先が二度と見えなくなる — その中間がこの形。

        **`_declaration_complaints` とは別に持つ**。あちらは人が宣言 config を直すまで永久に
        続く不備で、こちらは宛先が変われば消える一過性の失敗。同じ set に混ぜると、名前が
        どちらも言えなくなる。
        """
        if (project_key, handle) in self._failed_nudges:
            return
        self._failed_nudges.add((project_key, handle))
        print(
            f"[dispatch-v2] {project_key}: 空 nudge を届けられない ({handle}): {error}",
            file=self._log,
        )

    def _observe_stores(self, project_key, now, book):
        """tracker / ChangeHost を観測する job (**置き場の宣言が要る唯一の job**)。"""
        cache = self.cache_for(project_key)
        # **tick の境界をここで刻む**。連続失敗を「1 tick でも失敗したか」で数えるので、
        # 観測の成功ごとに数え直すのではなく tick の頭で前 tick の結果を確定させる
        cache.begin_tick(now)
        directory = self.registry.ledger_dir_for(project_key)
        try:
            declared = declaration.load(directory, known_rule_ids=rule_catalog.rule_ids())
        except declaration.DeclarationError as exc:
            return self._skip(project_key, cache, f"宣言 config を読めない: {exc}")
        if declared is None:
            return self._skip(
                project_key,
                cache,
                f"{declaration.DECLARATION_FILENAME} が無い "
                f"({declaration.declaration_path(directory)})",
            )
        if declared["issue"] is None:
            # 宣言はあるが置き場を書いていない (`[rules]` だけ等)。**「宣言が無い」と分けて
            # 返す** — 置き忘れた table と、置き忘れた file では、人が次に開く場所が違う
            return self._skip(
                project_key,
                cache,
                f"{declaration.DECLARATION_FILENAME} に [issue] が無い ({declared['path']})",
            )
        # **宣言が読めた時点で理由を消す**。adapter の組み立てより後ろに置くと、そこで落ちた
        # ときに前 tick の理由が残り、直っていない理由を tool が返し続ける
        cache.clear_skip_reason()
        tracker, change_host = self._build_adapters(declared)
        context = TickContext(
            project_key=project_key,
            book=book,
            cache=cache,
            tracker=tracker,
            change_host=change_host,
            declared=declared,
            now=now,
        )
        self._observe_candidates(context)
        self._settle_work_orders(context)
        return {"project_key": project_key, "observed": True, "reason": None}

    def _skip(self, project_key, cache, reason):
        """観測しない理由を cache へ残し、log にも 1 度だけ出す。

        **理由を cache に残す**のは、`observe_candidates` が `null` を返すときに「宣言が無い」
        と「まだ観測が成功していない」を呼び出し側へ区別して届けるため — 前者は置き忘れで、
        後者は待てば埋まる。stderr だけだと daemon の再起動で消え、事後に読める場所が無い。
        """
        cache.set_skip_reason(reason)
        self._complain_once(project_key, f"観測しない: {reason}")
        return {"project_key": project_key, "observed": False, "reason": reason}

    # --- 候補観測 (rule #8) -----------------------------------------------------

    def _observe_candidates(self, context):
        """候補集合を観測して subject を組み、`candidate_pool` scope の rule を当てる。

        **母集団を絞るのは宣言 config が渡した label だけ** — 「どの issue が着手可か」の資格
        判定は reconciler が持たない (ADR 0026 / 0056)。選定は orchestrator (LLM) のまま。
        """
        try:
            facts = context.tracker.fetch_candidates(
                repo=context.issue_repo,
                label=context.ready_label,
                observed_at=context.now,
            )
        except ports.ObservationError as exc:
            self._note_failure(context, STORE_TRACKER, context.issue_repo, exc)
            return
        context.cache.put_candidates(facts, observed_at=context.now)
        observed_refs = [fact["issue_ref"] for fact in facts]
        engaged = [
            work_order["issue_ref"]
            for work_order in context.book.list_work_orders()
            if work_order["phase"] not in vocabulary.TERMINAL_PHASES
        ]
        raise_escalations(
            context.book,
            escalation_rules.SCOPE_CANDIDATE_POOL,
            escalation_rules.CandidatePoolSnapshot(
                observed=observed_refs,
                announced=fold.announced_candidates(context.book.state),
                engaged=engaged,
                repo=context.issue_repo,
                ready_label=context.ready_label,
                observed_at=context.now,
            ),
        )
        # **rule を当てた後に基準を進める**。先に記帳すると、今 tick の観測が自分の差分の基準に
        # なって「現れた」が永久に立たない
        context.book.record_candidate_observation(candidates=observed_refs, engaged=engaged)

    # --- WorkOrder の観測 (rule #1〜#7 と完了の機械遷移) --------------------------

    def _settle_work_orders(self, context):
        """非終端 WorkOrder を 1 件ずつ観測し、rule を当ててから terminal の機械遷移を判定する。

        rule #1〜#7 は WorkOrder ごとに issue + 紐づき + closes CL を観測するので、台帳が
        育つほど tick が伸び、tick の間は inbox を pull する HTTP が止まる。そこで
        **観測 1 件ごとに予算の残りを見て、尽きたら残りを次の tick へ回す**。

        予算の起点は**このループに入った時刻**で、tick の開始時刻ではない。tick の頭から
        測ると、候補観測に時間を食った project や、同じ tick で先に回った別 project の
        遅れがそのまま WorkOrder の観測時間から引かれ、**先頭 1 件しか観測できない状態が
        恒久化する**。

        巡回の起点を覚えるのは、**予算切れの取りこぼしを恒久化させない**ため — 毎回先頭から
        回すと、予算の内側に入る先頭の数件だけが永久に観測され、後ろは一度も見られない。
        """
        pending = [
            work_order
            for work_order in context.book.list_work_orders()
            if work_order["phase"] not in vocabulary.TERMINAL_PHASES
        ]
        if not pending:
            return
        start = self._resume_index(context.project_key, pending)
        rotated = pending[start:] + pending[:start]
        deadline = self._monotonic() + WORK_ORDER_BUDGET_SEC
        for observed, work_order in enumerate(rotated):
            if observed and self._monotonic() >= deadline:
                # 1 件目は予算に関わらず観測する。**予算が尽きた状態でループに入っても巡回が
                # 止まらない**ようにするため (1 件ずつでも順に進む)
                break
            # **観測する前に位置を進める**。観測が例外で抜けても同じ WorkOrder に張り付かない
            self._settle_cursors[context.project_key] = work_order["wo_id"]
            self._visit_work_order(context, work_order)

    def _resume_index(self, project_key, pending):
        """前回最後に観測した WorkOrder の**次**の位置 (見つからなければ先頭)。"""
        last = self._settle_cursors.get(project_key)
        for index, work_order in enumerate(pending):
            if work_order["wo_id"] == last:
                return (index + 1) % len(pending)
        return 0

    def _visit_work_order(self, context, work_order):
        """WorkOrder 1 件を観測し、rule の発火と terminal の機械遷移を判定する。"""
        observed = self._observe_work_order(context, work_order)
        if observed is None:
            return  # 観測できなかった。**根拠が欠けたまま rule も遷移も判定しない**
        issue, snapshot = observed
        raise_escalations(context.book, escalation_rules.SCOPE_WORK_ORDER, snapshot)
        self._settle_one(context, work_order, issue, snapshot)

    def _observe_work_order(self, context, work_order):
        """WorkOrder 1 件ぶんの観測を `(IssueFact, snapshot)` で返す。

        **issue を観測できなければ None** (WorkOrder 全体の判断材料が無い)。closes CL だけが
        読めなかったときは `closes_cls=None` を載せて返す — **CL を読まない rule (#6
        session_blocked / #7 issue_closed_alive) まで黙らせないため**。1 つの CL の権限が
        落ちただけで、その WorkOrder が inbox から永久に消えることになる (「inbox が空 =
        判断待ちが無い」という tool の契約が破れる)。

        Session は**台帳から読む** (`sessions` job が runtime を観測して記帳した結果)。観測の
        入口を 1 つに保つと、rule が読む Session と台帳の記録が食い違わない。
        """
        issue_ref = work_order["issue_ref"]
        if refs.split_issue_ref(issue_ref)["tracker"] != context.declared["issue"]["tracker"]:
            # **宣言の tracker と違う ref は観測失敗ではなく恒久的な構成の食い違い**。
            # `_note_failure` へ流すと、健全な store が「到達できない」ことにされ (streak が
            # 復帰しないので escalation は打ち切りまで再送される)、しかも本当の条件
            # (観測しようがない WorkOrder がある) が覆い隠される
            self._complain_once(
                context.project_key,
                f"{issue_ref} は宣言の tracker ({context.declared['issue']['tracker']}) と "
                "違うので観測しない (WorkOrder は非終端のまま残る)",
            )
            return None
        try:
            issue = context.tracker.fetch_issue(
                issue_ref, repo=context.issue_repo, observed_at=context.now
            )
        except ports.ObservationError as exc:
            self._note_failure(context, STORE_TRACKER, context.issue_repo, exc)
            return None
        context.cache.put_issue(issue)
        try:
            snapshot = escalation_rules.work_order_snapshot(
                work_order,
                issue=issue,
                closes_cls=self._observe_closes_cls(context, issue_ref),
                session=session.latest_session_for(context.book.state, work_order["wo_id"]),
            )
        except ports.VocabularyMismatch as exc:
            # **観測失敗ではなく adapter と語彙のずれ**。`_note_failure` へ流すと健全な store が
            # 「到達できない」ことにされ、素通しすると project の tick ごと落ちて、同じ tick で
            # 成功していた候補観測まで「未観測」に化ける。この WorkOrder だけを飛ばす
            self._complain_once(
                context.project_key, f"{issue_ref} の CL を畳めない (adapter と語彙のずれ): {exc}"
            )
            return None
        return issue, snapshot

    def _settle_one(self, context, work_order, issue, snapshot):
        """terminal が確定する WorkOrder を記帳する。

        遷移の条件 (設計 §6 / ADR 0055):

        - **Session 不在**が前提。生きた worker が居るうちに terminal へ落とすと、terminal は
          不変なので worker の帰り道が無くなる。何を根拠に不在と判断したかは evidence に残す
        - issue が closed であることが引き金。closes CL に merged が 1 件でもあれば
          `completed`、無ければ `abandoned`
        - どちらも **evidence 必須**。terminal は逆行しないので、誤分類を後から人が監査する
          材料はこれしかない

        **Session 不在の判定は `_sessions_of` の継ぎ目から読む** — rule が読む Session
        (台帳の最後の 1 件) とは別軸で、こちらは「何を根拠に不在と言えるか」を evidence に
        載せる必要がある (`ports.session_observation`)。
        """
        if snapshot.issue_state != "closed" or snapshot.cl_status is None:
            # **CL が未観測なら terminal を確定させない**。観測できなかった CL を「無かった」
            # として畳むと、merge 済みの成果が `abandoned` に分類され、terminal は逆行しない
            return
        closes = list(snapshot.closes_cls)
        if not closes and not context.cl_links.absence_is_evidence:
            # **紐づき 0 件が「CL は無い」の証拠にならない源** (台帳) では確定させない。
            # 誰もまだ記録していないだけかもしれず、`abandoned` は不変 (ADR 0059)。
            # **止めるのはここだけ** — rule は 0 件を 0 件として読み、`session_died_empty` は立つ
            return
        sessions = self._sessions_of(context.project_key, work_order["wo_id"])
        if sessions["sessions"]:
            return
        merged = [fact for fact in closes if fact["status"] == "merged"]
        context.book.transition(
            wo_id=work_order["wo_id"],
            phase="completed" if merged else "abandoned",
            note=None,
            actor="reconciler",
            evidence={
                "trigger": "issue_closed",
                "issue": issue,
                "closes_cls": closes,
                # 「Session 不在」を何から読んだか。台帳が Session を知らないだけなのか、
                # SessionRuntime を観測した結果なのかで、誤分類の疑い方が変わる
                "sessions": sessions,
                "summary": _settlement_summary(issue, merged, closes),
            },
        )

    def _observe_closes_cls(self, context, issue_ref):
        """issue の closes CL を観測して CLFact の列を返す。1 件でも観測できなければ None。

        **mention は混ぜない** — 無関係な CL の merge で issue が completed になるのを防ぐ。

        紐づきの源は `context.cl_links` が決める (tracker か台帳か)。**源によらず 0 件は
        `[]` で返る** — その 0 件を terminal の根拠にしてよいかは源が `absence_is_evidence` で
        答え、判定するのは `_settle_one`。ここで `None` に倒すと CL を読む rule が軒並み降りる。
        """
        try:
            links = context.cl_links.fetch_cl_links(issue_ref, repo=context.issue_repo)
        except ports.ObservationError as exc:
            self._note_failure(context, STORE_TRACKER, context.issue_repo, exc)
            return None
        context.cache.put_cl_links(issue_ref, links, observed_at=context.now)
        facts = []
        for link in links:
            if link["role"] != ports.ROLE_CLOSES:
                continue
            try:
                fact = context.change_host.fetch_cl(
                    link["cl_ref"], repo=link["repo"], observed_at=context.now
                )
            except ports.ObservationError as exc:
                self._note_failure(context, STORE_CHANGE_HOST, link["repo"], exc)
                return None
            context.cache.put_cl(fact)
            facts.append(fact)
        return facts

    # --- 観測失敗の観測 (rule #9) と rule の適用口 --------------------------------

    def _note_failure(self, context, store, repo, error):
        """観測失敗を数え、`store` scope の rule を当てる。

        **cache の事実は消さない** — 空で上書きすると、復帰した次の tick で候補が全件「新規」に
        見えて escalation が溢れる (観測できなかったことは「無かった」ではない)。
        """
        failure = context.cache.record_failure(store, error=str(error), at=context.now)
        if failure.last_delivered_at == context.now:
            # **1 tick に 1 回だけ配送する**。1 tick で同じ store を何度も観測する (候補 1 回 +
            # 非終端 WorkOrder のぶん) ので、観測ごとに配送すると 3 回の予算が 1 tick で尽きる。
            # 連続何回で上げるか (条件) は rule の担当で、ここが持つのは配送の間隔だけ
            return
        raised = raise_escalations(
            context.book,
            escalation_rules.SCOPE_STORE,
            escalation_rules.StoreFailureSnapshot(
                store=store,
                repo=repo,
                streak=failure.streak,
                error=failure.error,
                first_failed_at=failure.first_failed_at,
                last_failed_at=failure.last_failed_at,
            ),
        )
        if raised:
            failure.last_delivered_at = context.now

    # --- 補助 -------------------------------------------------------------------

    def _complain_once(self, project_key, message):
        """同じ project の同じ不備を 1 度だけ log へ出す (毎 tick 同じ行で log を埋めない)。"""
        key = (project_key, message)
        if key in self._declaration_complaints:
            return
        self._declaration_complaints.add(key)
        print(f"[dispatch-v2] {project_key}: {message}", file=self._log)

    def _report_unexpected(self, step):
        """想定外の失敗を daemon の log へ残す (握り潰さずに、落ちもしない)。"""
        print(
            f"[dispatch-v2] {step} が想定外の失敗:\n{traceback.format_exc()}", file=self._log
        )


def raise_escalations(book, scope, subject):
    """subject に scope の rule を当てて、立った Finding を台帳へ配送する。

    **観測と配送の間に判断を挟まない**のがこの 1 行の意味 — 条件は catalog、dedup と
    打ち切りは台帳、reconciler が持つのは「何を観測して subject を組むか」だけ。

    class の外に置いてあるのは、**台帳だけで回る job (`run_jobs` 配下) からも同じ形で呼ぶ**
    ため。instance method にすると、`self` を持たない job だけが同じ式を書き写すことになる。
    """
    return escalation_rules.deliver(book, escalation_rules.evaluate(scope, subject))


def adapters_for(declared):
    """宣言から (Tracker, ChangeHost) を組む。

    issue 置き場と CL 置き場が同じ tracker なら**同じ instance を両 port として使う** —
    別に作ると CLI の起動境界が 2 つになり、テストで argv を pin する地点が割れる。

    表を引くだけで分岐を持たないのは、**adapter を足す作業を登録表に閉じる**ため
    (`adapters` module が表の正本。`declaration` が未対応 tracker を先に落とすので、
    ここに来る tracker 名は必ず表にある)。
    """
    tracker = adapters.ADAPTERS[declared["issue"]["tracker"]]()
    if declared["cl"]["tracker"] == declared["issue"]["tracker"]:
        return tracker, tracker
    return tracker, adapters.ADAPTERS[declared["cl"]["tracker"]]()


# --- 台帳だけで回る job (置き場の宣言に依らない) --------------------------------


def run_jobs(book, *, runtime, run_git, schedule, now, log=sys.stderr):
    """1 project 分の台帳側 job を、due なものだけ走らせる。走らせた job と結果を返す。

    観測の失敗で tick を止めない。**止めると 1 つの store の不調が全 project の記帳を止める**
    ので、失敗は結果に載せて次の job へ進む。
    """
    result = {}
    if schedule.due("sessions", SESSION_INTERVAL_SEC, now):
        result["sessions"] = _guard(lambda: worker_sessions.observe(book, runtime=runtime), log)
    if schedule.due("resources", RESOURCE_INTERVAL_SEC, now):
        result["resources"] = _guard(
            lambda: _report_orphans(book, runtime=runtime, run_git=run_git), log
        )
    if schedule.due("effects", EFFECT_INTERVAL_SEC, now):
        result["effects"] = _guard(
            lambda: _apply_effects(book, runtime=runtime, run_git=run_git), log
        )
    return result


def _apply_effects(book, *, runtime, run_git):
    """宣言を読んで、有効な作用だけを 1 巡させる。

    **宣言の読み込みと作用の実行を 1 つの guard に入れてある** — 宣言が読めないときに
    「既定で走らせる」と、off にしたつもりの作用が動く。読めなければ何も作用させない。
    """
    enabled = declaration.enabled_rules(
        declaration.load(book.directory, known_rule_ids=rule_catalog.rule_ids()),
        rule_catalog.CATALOG,
    )
    outcomes = rule_catalog.apply_effects(book, enabled, runtime=runtime, run_git=run_git)
    raise_escalations(
        book,
        escalation_rules.SCOPE_EFFECT,
        escalation_rules.EffectSnapshot(
            clone_path=book.clone_path(),
            deploy_outcome=outcomes.get(rule_catalog.DEPLOY_RULE),
            deploy_summary=rule_catalog.rule_summaries()[rule_catalog.DEPLOY_RULE],
        ),
    )
    return outcomes


def _report_orphans(book, *, runtime, run_git):
    """台帳外の資源を報告し、**project に閉じるもの (worktree) だけ** escalation に載せる。

    session の handle を escalation に載せないのは、**runtime が machine 単位だから** —
    workspace には他 project の worker も人間自身の pane も居るので、project ごとの tick が
    それを上げると、どの project の inbox も無関係な pane で埋まる。台帳外 session は
    `worktree_sweep` の応答に出す (spec が求める「検出・報告」はそこで満たす)。

    `unverified` も載せない — `.git` を読めなかっただけの生きたツリーを回収対象として
    報告しないため (ADR 0048)。こちらも応答には残る。
    """
    report = worker_sessions.reclaim_report(book, run_git=run_git, runtime=runtime)
    raise_escalations(
        book,
        escalation_rules.SCOPE_RESOURCES,
        escalation_rules.ResourceSnapshot(orphan_worktrees=report["orphan_worktrees"]),
    )
    return report


def _guard(job, log):
    """job を 1 つ走らせる。予期した失敗様式は結果に写して tick を続ける。"""
    try:
        return job()
    except (
        session_runtime.SessionRuntimeError,
        git_worktrees.WorktreeError,
        declaration.DeclarationError,
        fold.FoldError,
    ) as exc:
        print(f"[dispatch-v2] tick の job が失敗: {exc}", file=log)
        return {"failed": str(exc)}


class PacedTick:
    """周期を測って `Reconciler.run_tick` を呼ぶ callable (server の `service_actions` へ渡す)。

    server は毎ループ知らせるだけで、**周期を持つのはこちら**。観測の周期が store ごとに違う
    (tracker / CL 60 秒、SessionRuntime 10 秒) ので、周期を server 側に置くと store を足すたびに
    server を変えることになる。

    **最初の tick は短く待つ**。tick は serve と同じスレッドなので、bind 直後に観測を始めると
    その間 accept が止まる — daemon を lazy 起動した client は health が返らないのを「起動して
    いない」と読み、2 つ目の daemon を spawn して flock で死ぬ (`client.ensure_daemon`)。
    最初の 1 往復を通してから観測に入れば、この競合は構造で消える。
    """

    def __init__(
        self,
        reconciler,
        *,
        clock,
        interval_sec=PACE_INTERVAL_SEC,
        first_delay_sec=FIRST_TICK_DELAY_SEC,
    ):
        self._reconciler = reconciler
        self._clock = clock
        self._interval_sec = interval_sec
        self._first_delay_sec = first_delay_sec
        self._due_at = None

    def __call__(self, now_monotonic):
        if self._due_at is None:
            self._due_at = now_monotonic + self._first_delay_sec
            return None
        if now_monotonic < self._due_at:
            return None
        # 次回は「今から interval 後」。**前回予定時刻からの加算にしない** — tick が interval
        # より長くかかった場合に、遅れを取り戻そうと連続で走る (観測が重い環境で暴れる)
        self._due_at = now_monotonic + self._interval_sec
        return self._reconciler.run_tick(self._clock(), monotonic=now_monotonic)


def _settlement_summary(issue, merged, closes):
    """機械遷移の evidence に載せる 1 行 (設計 §5 の `gh!734@abc123` の形)。"""
    if merged:
        stamps = ", ".join(f"{fact['cl_ref']}@{fact['head_sha']}" for fact in merged)
        return f"{stamps} merged / {issue['issue_ref']} closed"
    if closes:
        stamps = ", ".join(f"{fact['cl_ref']}({fact['status']})" for fact in closes)
        return f"{issue['issue_ref']} closed / closes CL に merged 無し: {stamps}"
    return f"{issue['issue_ref']} closed / closes CL 無し"


