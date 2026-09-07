"""reconciler daemon の HTTP/JSON 面 (UDS 上で listen する)。

設計 `docs/design/dispatch-v2/system.md`「7. reconciler daemon」: MCP server (stdio・セッション
ごと) は daemon への薄い client で、接続は **UDS 上の HTTP/JSON**。dashboard も後続 issue で
同じ daemon に同居する。

- **単一スレッド**で回す (`socketserver.UnixStreamServer` の既定)。書き手が 1 つに揃うので
  追記の直列性が構造で真になり、in-process の lock が要らなくなる
- **data を返す応答に `as_of` を載せる** (設計 system.md「9. MCP tool 面」の「全応答に観測時刻
  (鮮度) を明記する」)。
  台帳コアの段階では外部 store の観測 cache がまだ無いので、`as_of` は **daemon が memory の
  state から答えた時刻**を意味する。error 応答は data ではなく、MCP 面では応答 object ですら
  なく `ToolError` の message になるので載せない
- 例外は表に載せた系統だけを status へ写す。それ以外は 500 で本文に型名を出し、traceback を
  daemon の log へ残す (握り潰さない)
"""

import functools
import http.server
import json
import re
import socketserver
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dispatch_v2 import (
    declaration,
    escalation_rules,
    event_log,
    fold,
    git_worktrees,
    ledger,
    observation,
    project,
    refs,
    rule_catalog,
    session_runtime,
    vocabulary,
    worker_sessions,
)

# path segment 中の project key / woId。project key の文字集合は project module が正本で、
# ここでは「segment を切り出す」ところまでしかやらない (検証は require_project_key)
_SEGMENT = r"[^/]+"

# 呼び出し側が名乗る runtime の識別子 (空 nudge の宛先 handle / 割り元 anchor の handle と
# workspace) の形。**綴りは runtime ごとに違う**ので語彙は縛らず、「1 行の短い印字可能文字列」
# だけを保証する — これらの値は後で外部 CLI の引数になるので、改行や制御文字を通さない
RUNTIME_ID_PATTERN = re.compile(r"^[\w:@./+-]{1,128}$")

# 1 接続が読み待ちで daemon を占有してよい上限 (秒)。**単一スレッドなので、黙った接続 1 本が
# daemon 全体を止める** — keep-alive で idle した client (dashboard を開いた browser) も、
# request の途中で死んだ client も同じ。並行化は「単一書き手を単一スレッドで保証する」不変
# 条件と衝突するので、read を諦める側で上限を付ける。同梱 client は 1 往復ごとに閉じるので
# 通常は効かず、効くのは黙った接続を掴んだときだけ。**既定の所在はここ 1 箇所**
REQUEST_IDLE_TIMEOUT_SEC = 10

ROUTES = (
    ("GET", re.compile(r"^/health$"), "health"),
    ("POST", re.compile(rf"^/projects/(?P<project_key>{_SEGMENT})/workorders$"), "create_work_order"),
    ("GET", re.compile(rf"^/projects/(?P<project_key>{_SEGMENT})/workorders$"), "list_work_orders"),
    (
        "GET",
        re.compile(rf"^/projects/(?P<project_key>{_SEGMENT})/workorders/(?P<wo_id>{_SEGMENT})$"),
        "get_work_order",
    ),
    (
        "POST",
        re.compile(
            rf"^/projects/(?P<project_key>{_SEGMENT})/workorders/(?P<wo_id>{_SEGMENT})/transition$"
        ),
        "transition_work_order",
    ),
    (
        "POST",
        re.compile(
            rf"^/projects/(?P<project_key>{_SEGMENT})/workorders/(?P<wo_id>{_SEGMENT})/annotate$"
        ),
        "annotate_work_order",
    ),
    (
        "POST",
        re.compile(
            rf"^/projects/(?P<project_key>{_SEGMENT})/workorders/(?P<wo_id>{_SEGMENT})/cls$"
        ),
        "record_cl",
    ),
    (
        "POST",
        re.compile(
            rf"^/projects/(?P<project_key>{_SEGMENT})/workorders/(?P<wo_id>{_SEGMENT})/cls/forget$"
        ),
        "forget_cl",
    ),
    ("GET", re.compile(rf"^/projects/(?P<project_key>{_SEGMENT})/candidates$"), "observe_candidates"),
    ("GET", re.compile(rf"^/projects/(?P<project_key>{_SEGMENT})/cls$"), "observe_cls"),
    ("GET", re.compile(rf"^/projects/(?P<project_key>{_SEGMENT})/inbox$"), "read_inbox"),
    (
        "POST",
        re.compile(
            rf"^/projects/(?P<project_key>{_SEGMENT})/inbox/(?P<escalation_id>{_SEGMENT})/ack$"
        ),
        "ack_escalation",
    ),
    # Session と資源 (worktree) の経路。WorkOrder の下に居るものは WorkOrder の path の下、
    # 既に立っている Session への操作は id で直に引く
    (
        "POST",
        re.compile(
            rf"^/projects/(?P<project_key>{_SEGMENT})/workorders/(?P<wo_id>{_SEGMENT})/outcome$"
        ),
        "report_outcome",
    ),
    (
        "POST",
        re.compile(
            rf"^/projects/(?P<project_key>{_SEGMENT})/workorders/(?P<wo_id>{_SEGMENT})/sessions$"
        ),
        "spawn_session",
    ),
    (
        "POST",
        re.compile(
            rf"^/projects/(?P<project_key>{_SEGMENT})/workorders/(?P<wo_id>{_SEGMENT})/worktree/reclaim$"
        ),
        "reclaim_worktree",
    ),
    ("GET", re.compile(rf"^/projects/(?P<project_key>{_SEGMENT})/sessions$"), "list_sessions"),
    (
        "POST",
        re.compile(
            rf"^/projects/(?P<project_key>{_SEGMENT})/sessions/(?P<session_id>{_SEGMENT})/send$"
        ),
        "send_to_session",
    ),
    (
        "POST",
        re.compile(
            rf"^/projects/(?P<project_key>{_SEGMENT})/sessions/(?P<session_id>{_SEGMENT})/close$"
        ),
        "close_session",
    ),
    ("GET", re.compile(rf"^/projects/(?P<project_key>{_SEGMENT})/resources$"), "read_resources"),
)


class BadRequest(ValueError):
    """request の形が受け付けられない (JSON でない / 必須欄が無い)。"""


class RuntimeNotConfigured(RuntimeError):
    """worker runtime を持たない daemon で Session 系の経路が呼ばれた (server 側の構成不備)。"""


# domain 例外 → HTTP status。**未知の例外をここへ足さない**限り 500 に落ちるので、
# 新しい失敗様式が「それらしい 4xx」に化けない
STATUS_BY_ERROR = (
    (fold.NotInLedgerError, 404),
    (fold.FoldError, 409),
    (ledger.LedgerError, 400),
    (project.ProjectError, 400),
    (vocabulary.VocabularyError, 400),
    (refs.RefError, 400),
    # 資源の前提不成立 (回収資格を満たさない / 既に誰かの物 / Session がまだ起動していない) は
    # 呼び出し側が直せるので 409。runtime の失敗は上流の不調なので 502 に分ける — 呼び出し側の
    # 引数では直らない。runtime を持たない daemon 構成は server 側の設定不備なので 500
    (git_worktrees.WorktreeError, 409),
    (worker_sessions.SessionNotReady, 409),
    (worker_sessions.SpawnFailed, 502),
    (RuntimeNotConfigured, 500),
    (session_runtime.SessionRuntimeError, 502),
    (declaration.DeclarationError, 400),
    (event_log.EventLogError, 500),
)


def _is_project_dir(entry):
    """daemon が迎え入れる project ディレクトリか (台帳がある / 置き場を宣言している)。"""
    return (entry / project.EVENTS_FILENAME).exists() or (
        entry / declaration.DECLARATION_FILENAME
    ).exists()


class LedgerRegistry:
    """project key → Ledger。daemon がマシンに 1 つで複数 project を抱えるための保持面。

    台帳は **要求された project の分だけ lazy に開く**。ただし daemon の起動時には
    `adopt_existing_projects` で root 配下の既存台帳を 1 度だけ迎え入れる — polling tick が
    回るのは registry が知っている project だけなので、走査しないと再起動後に誰かが tool を
    叩くまで観測が止まる。
    """

    def __init__(self, root, *, clock=event_log.now_iso):
        self.root = root
        self._clock = clock
        self._ledgers = {}

    def ledger_for(self, project_key):
        key = project.require_project_key(project_key)
        if key not in self._ledgers:
            self._ledgers[key] = ledger.Ledger.open(
                self.ledger_dir_for(key), clock=self._clock
            )
        return self._ledgers[key]

    def ledger_dir_for(self, project_key):
        """project の台帳ディレクトリ。宣言 config も同じディレクトリに置く。"""
        return project.ledger_dir(self.root, project_key)

    def known_projects(self):
        """既に開いた project key。**root 配下の走査はしない** (`adopt_existing_projects` が別)。"""
        return sorted(self._ledgers)

    def adopt_existing_projects(self):
        """root 配下に台帳がある project を全部開く。開けた key を返す。

        **起動時に 1 度だけ呼ぶ**。tick が回るのは registry が知っている project だけなので、
        走査しないと「daemon を再起動したあと、誰かが tool を 1 回叩くまで観測が始まらない」
        になる — 設計 §9 が v1 の「起動 cwd の project しか観測できない」制約を解消すると
        置いた以上、既存の台帳は起動の時点で観測対象に入る。

        採るのは **台帳があるか、置き場を宣言している project**。宣言だけの project を外すと、
        「宣言を置いた直後、まだ 1 件も記帳が無い」状態で観測が始まらず、tracer bullet
        (issue を開くと 1 tick 以内に candidate_appeared) が誰かが台帳へ書くまで成立しない —
        宣言こそが「この project を観測せよ」の意思表示なので、そちらを取りこぼさない。

        壊れた台帳は 1 件だけ loud に落として他を止めない — 1 project の破損で daemon 全体が
        起動しないのは重い。
        """
        root = Path(self.root)
        if not root.is_dir():
            return []
        adopted = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or not _is_project_dir(entry):
                continue
            try:
                self.ledger_for(entry.name)
            except (
                project.ProjectError,
                fold.FoldError,
                event_log.EventLogError,
                # `Ledger.open` の `recover()` は正本を読み書きするので、権限や I/O の失敗は
                # 素の OSError で出る。捕らえないと 1 project の破損で daemon が起動しない
                OSError,
            ) as exc:
                print(f"[dispatch-v2] {entry.name}: 台帳を開けない: {exc}", file=sys.stderr)
                continue
            adopted.append(entry.name)
        return adopted


class Api:
    """route 名 → 応答 body。HTTP の作法を持たない (handler が status と serialize を持つ)。"""

    def __init__(
        self,
        registry,
        *,
        reconciler,
        clock=event_log.now_iso,
        health_facts=None,
        runtime=None,
        run_git=project.run_git,
    ):
        self.registry = registry
        self._clock = clock
        self._health_facts = health_facts or (lambda: {})
        # 観測 cache の持ち主。**必須** — 観測経路を持たない daemon は組み立て経路に存在しない
        # ので、任意にすると「未観測」の理由語彙が本番に無い分岐のぶんだけ増える
        self.reconciler = reconciler
        # runtime を持たない Api は Session 系の経路だけが使えない (台帳の読み書きは動く)。
        # 既定を herdr にしないのは、**HTTP 層が特定 runtime を知らない**ため
        self._runtime = runtime
        self._run_git = run_git

    def health(self, _params, _query, _body):
        return {**self._health_facts(), "projects": self.registry.known_projects()}

    def create_work_order(self, params, _query, body):
        book = self.registry.ledger_for(params["project_key"])
        # **WorkOrder を切った時点で稼働 clone を観測する**。deploy はここから走れるように
        # なるので、spawn まで待つと「dispatch しているのに pull されない」窓ができる
        if body.get("repo_root") is not None:
            book.observe_clone_path(clone_path=_require_directory(body, "repo_root"))
        return {
            "work_order": book.create_work_order(
                issue_ref=_require(body, "issue_ref"),
                note=body.get("note"),
                actor=_require(body, "actor"),
            )
        }

    def list_work_orders(self, params, query, _body):
        book = self.registry.ledger_for(params["project_key"])
        # **絞り込み語彙も境界で検証する** — 通さないと綴り誤りが 400 ではなく「0 件」として
        # 返り、呼び出し側は「まだ無い」と読む (書き込み経路は create が既に検証している)
        phases = [vocabulary.require_phase(phase) for phase in query.get("phase") or []]
        issue_ref = (query.get("issue_ref") or [None])[0]
        return {
            "work_orders": book.list_work_orders(
                phases=phases or None,
                issue_ref=refs.require_issue_ref(issue_ref) if issue_ref else None,
            )
        }

    def get_work_order(self, params, _query, _body):
        book = self.registry.ledger_for(params["project_key"])
        return {"work_order": book.get(params["wo_id"])}

    def transition_work_order(self, params, _query, body):
        book = self.registry.ledger_for(params["project_key"])
        return {
            "work_order": book.transition(
                wo_id=params["wo_id"],
                phase=_require(body, "phase"),
                note=body.get("note"),
                actor=_require(body, "actor"),
            )
        }

    def annotate_work_order(self, params, _query, body):
        book = self.registry.ledger_for(params["project_key"])
        return {
            "work_order": book.annotate(
                wo_id=params["wo_id"],
                note=_require(body, "note"),
                actor=_require(body, "actor"),
            )
        }

    def record_cl(self, params, _query, body):
        """WorkOrder へ issue → CL の紐づきを記帳する (tracker から引けない置き場の join)。

        **ref の書式検証は fold 側 (`cl_record`)** に置く。ここで先に弾くと、同じ検査が境界に
        2 つ生まれ、片方だけ緩めた状態が作れる。

        **宣言との突き合わせだけはここにある** — fold は宣言を読まない (台帳の不変条件は
        置き場の宣言に依らない) ので、「宣言した CL 置き場と違う tracker の ref」を落とせるのは
        宣言を解決できるこの層だけ。
        """
        book = self.registry.ledger_for(params["project_key"])
        self._require_declared_cl_tracker(params["project_key"], body.get("cl_ref"))
        return {
            "work_order": book.record_cl(
                wo_id=params["wo_id"],
                cl_ref=_require(body, "cl_ref"),
                repo=_require(body, "repo"),
                role=_require(body, "role"),
                actor=_require(body, "actor"),
            )
        }

    def _require_declared_cl_tracker(self, project_key, cl_ref):
        """記録する CL ref が、宣言した CL 置き場の tracker のものであることを要求する。

        **通すと台帳の誤記録が「store が落ちている」として報告される。** 宣言が
        `[pr] tracker = "glab"` の project へ `gh!401` を記録すると、以後の tick は毎回
        glab adapter へその ref を渡し、`adapter_cli.cl_number` が `ObservationError` を投げる —
        観測失敗として数えられ、決定的に失敗し続けるので streak が復帰せず、`store_unreachable`
        (rule #9) が打ち切りまで再送される。その間その WorkOrder は永久に settle しない。

        宣言が読めない project (宣言が無い / `[issue]` が無い / 壊れている) では検査しない。
        **突き合わせる相手が無いだけ**で、記録そのものは台帳の不変条件を満たしている。
        """
        try:
            declared = declaration.load(
                self.registry.ledger_dir_for(project_key),
                known_rule_ids=rule_catalog.rule_ids(),
            )
        except declaration.DeclarationError:
            return
        cl_place = (declared or {}).get("cl")
        if cl_place is None or not cl_ref:
            return
        try:
            tracker = refs.split_cl_ref(cl_ref)["tracker"]
        except refs.RefError:
            # **書式不正はここで答えない。** 宣言の有無で `RefError` (400) と `FoldError` (409) に
            # 分かれると、同じ入力の答えが project ごとに変わる。綴り違いは fold が 409 で返す
            return
        if tracker != cl_place["tracker"]:
            raise BadRequest(
                f"{cl_ref} は宣言した CL 置き場の tracker ({cl_place['tracker']}) と違う。"
                "記録すると、以後の観測がその置き場の adapter へ渡って毎回失敗し、"
                "台帳の誤記録が「置き場が落ちている」として報告される"
            )

    def forget_cl(self, params, _query, body):
        """記帳した紐づきを取り消す (誤った CL ref を機械遷移の根拠から外す)。"""
        book = self.registry.ledger_for(params["project_key"])
        return {
            "work_order": book.forget_cl(
                wo_id=params["wo_id"],
                cl_ref=_require(body, "cl_ref"),
                actor=_require(body, "actor"),
            )
        }

    def observe_candidates(self, params, _query, _body):
        """候補プールの観測 cache。**未観測は `null`** で、観測して 0 件は `[]`。

        `reason` は「なぜ未観測か」で、`candidates` が `null` のときだけ埋まる — 宣言 config が
        無い project を「候補ゼロ」として返すと、置き忘れが「今は候補が無い」に化ける。
        """
        cache = self._cache_for(params["project_key"])
        candidates = cache.candidates()
        return {
            "candidates": candidates,
            "observed_at": cache.candidates_observed_at(),
            # **観測が止まっている理由は candidates が埋まっていても返す**。一度観測に成功した
            # 後で宣言を消した / 壊した project は古い候補を持ち続けるので、理由を `null` の
            # ときだけ返すと、止まっていることが誰にも見えないまま stale な列が配られる
            "reason": observation.stall_reason(cache, observed=candidates is not None),
        }

    def observe_cls(self, params, _query, _body):
        """観測済みの CLFact と、issue → CL の紐づき観測。**未観測は `null`**。

        候補側と同じ線を引く — 宣言 config を置き忘れた project が `cls: []` に見えると
        「CL が 1 件も無い」と読める。観測が走っていない理由は `reason` に出る。
        """
        cache = self._cache_for(params["project_key"])
        reason = observation.stall_reason(cache, observed=cache.has_observed())
        if not cache.has_observed():
            return {"cls": None, "cl_links": None, "reason": reason}
        return {"cls": cache.cls(), "cl_links": cache.cl_links_by_issue(), "reason": reason}

    def _cache_for(self, project_key):
        """project の観測 cache (project key の検証を通してから引く)。

        **同時に project を registry へ登録する**。tick が回るのは registry が知っている
        project だけなので、登録しないと「宣言を書いて `observe_candidates` を呼ぶ」経路で
        観測が永久に始まらず、tool は「まだ観測が成功していない」を返し続ける (待てば埋まる
        という意味の理由なのに、実際は誰かが `wo_create` を呼ぶか daemon を再起動するまで
        埋まらない)。起動時の迎え入れが解いたのと同じ穴が、実行時に残っていた。
        """
        key = project.require_project_key(project_key)
        self.registry.ledger_for(key)
        return self.reconciler.cache_for(key)

    def read_inbox(self, params, query, _body):
        """未 ack の escalation を返し、**pull した相手を空 nudge の宛先として覚える**。

        `notify_handle` は呼び出し側 (MCP server) が自分の runtime handle として渡す観測値。
        渡さない呼び出しも合法で、そのときは宛先が更新されないだけ — 宛先が無くても escalation
        は inbox に残るので、pull できる限り何も失われない (設計 決定 11)。

        条件文 (`condition`) は catalog から**読むたびに引く**。台帳へ書くと、条件の言い回しを
        直したときに過去の escalation だけ古い文面を持つ (正本に載せるのは観測事実だけ)。
        """
        book = self.registry.ledger_for(params["project_key"])
        self._aim_nudge(params["project_key"], (query.get("notify_handle") or [None])[0])
        return {
            "escalations": [
                {
                    **escalation,
                    "condition": escalation_rules.condition_of(escalation["rule_id"]),
                }
                for escalation in book.read_inbox()
            ]
        }

    def _aim_nudge(self, project_key, handle):
        """pull した相手を空 nudge の宛先として覚える (形が合わなければ採らない)。

        **形の合わない handle で pull そのものを落とさない** — nudge は届かなくても何も
        失わない経路なので、その入力の不備で「全 escalation が読める」という配送の前提を
        壊してはいけない。採らずに log へ出し、応答は escalation を返す。
        """
        if not handle:
            return
        if not RUNTIME_ID_PATTERN.match(handle):
            print(
                f"[dispatch-v2] {project_key}: notify_handle が runtime handle の形をして "
                f"いないので宛先に採らない: {handle!r}",
                file=sys.stderr,
            )
            return
        self.reconciler.aim_nudge_at(project_key, handle)

    def ack_escalation(self, params, _query, body):
        book = self.registry.ledger_for(params["project_key"])
        return {
            "escalation": book.ack_escalation(
                escalation_id=params["escalation_id"], actor=_require(body, "actor")
            )
        }

    # --- Session と資源 -------------------------------------------------------

    def report_outcome(self, params, _query, body):
        book = self.registry.ledger_for(params["project_key"])
        return {
            "work_order": book.report_outcome(
                wo_id=params["wo_id"],
                outcome=_require(body, "outcome"),
                summary=body.get("summary"),
                actor=_require(body, "actor"),
            )
        }

    def spawn_session(self, params, _query, body):
        book = self.registry.ledger_for(params["project_key"])
        return worker_sessions.spawn(
            book,
            runtime=self._require_runtime(),
            run_git=self._run_git,
            wo_id=params["wo_id"],
            prompt=_require_text(body, "prompt"),
            model=_optional_text(body, "model"),
            actor=_require(body, "actor"),
            # 稼働 clone は**呼び出し側 (cwd を知っている MCP server) が渡した観測値**。
            # 正本へ書く値なので、境界で形を検証してから内側へ渡す
            clone_path=_require_directory(body, "repo_root"),
            anchor=_optional_anchor(body),
        )

    def list_sessions(self, params, _query, _body):
        book = self.registry.ledger_for(params["project_key"])
        return {"sessions": book.list_sessions()}

    def send_to_session(self, params, _query, body):
        book = self.registry.ledger_for(params["project_key"])
        return {
            "session": worker_sessions.send(
                book,
                runtime=self._require_runtime(),
                session_id=params["session_id"],
                text=_require(body, "text"),
            )
        }

    def close_session(self, params, _query, body):
        book = self.registry.ledger_for(params["project_key"])
        return {
            "session": worker_sessions.close(
                book,
                runtime=self._require_runtime(),
                session_id=params["session_id"],
                actor=_require(body, "actor"),
            )
        }

    def read_resources(self, params, _query, _body):
        book = self.registry.ledger_for(params["project_key"])
        return worker_sessions.reclaim_report(
            book, run_git=self._run_git, runtime=self._require_runtime()
        )

    def reclaim_worktree(self, params, _query, body):
        book = self.registry.ledger_for(params["project_key"])
        return worker_sessions.reclaim(
            book, run_git=self._run_git, wo_id=params["wo_id"], actor=_require(body, "actor")
        )

    def now(self):
        return self._clock()

    def _require_runtime(self):
        if self._runtime is None:
            raise RuntimeNotConfigured(
                "この daemon は worker runtime を持たずに起動している (Session 系の経路は使えない)"
            )
        return self._runtime


def _require(body, field):
    value = body.get(field)
    if value is None:
        raise BadRequest(f"request body に {field} が無い")
    return value


def _require_text(body, field):
    """文字列であることを境界で確かめて返す。

    JSON は数値も object も運べるので、型は境界で絞る — 内側 (command の組み立て等) で
    初めて落ちると、その手前までの記帳が半端に残る。
    """
    value = _require(body, field)
    if not isinstance(value, str):
        raise BadRequest(f"{field} は文字列でなければならない ({type(value).__name__} が来た)")
    return value


def _optional_text(body, field):
    """省略できる文字列欄。渡されたなら型を確かめる。"""
    return None if body.get(field) is None else _require_text(body, field)


def _optional_anchor(body):
    """呼び出し側が観測した割り元 (`anchor_handle` + `anchor_workspace`)。無ければ `None`。

    **省略できる**のは version skew のため — 新しい MCP server と、まだ再起動していない古い
    daemon が同居しうる。省略された spawn は今日どおり daemon 自身の割り元へ縮退する。

    片方だけ渡された anchor は **400 にする** (縮退させない) — workspace が無いと adapter は
    観測窓の内か外かを判定できず、渡した側は「割り元を指定した」と思ったまま別の pane から
    割られる。
    """
    handle = body.get("anchor_handle")
    workspace = body.get("anchor_workspace")
    if handle is None and workspace is None:
        return None
    if not isinstance(handle, str) or not RUNTIME_ID_PATTERN.match(handle):
        raise BadRequest(f"anchor_handle が runtime の識別子の形をしていない: {handle!r}")
    if not isinstance(workspace, str) or not RUNTIME_ID_PATTERN.match(workspace):
        raise BadRequest(f"anchor_workspace が runtime の識別子の形をしていない: {workspace!r}")
    return session_runtime.RuntimeAnchor(handle=handle, workspace=workspace)


def _require_directory(body, field):
    """絶対 path で実在するディレクトリであることを境界で確かめて返す。

    **正本 (events.jsonl) へ書く値だから境界で検証する** — 相対 path や実在しない path を
    記帳すると、以後 daemon がその path で `git pull` と `worktree add` を撃ち続ける。
    git repo かどうかまではここで見ない (git 側の失敗として loud に出る)。
    """
    value = _require(body, field)
    path = Path(str(value))
    if not path.is_absolute() or not path.is_dir():
        raise BadRequest(f"{field} は実在する絶対 path のディレクトリでなければならない: {value!r}")
    return str(path)


class UnixHttpServer(socketserver.UnixStreamServer):
    """UDS 上の HTTP server。単一スレッドで 1 request ずつ処理し、**同じスレッドで tick も回す**。

    `BaseHTTPRequestHandler` は client_address を (host, port) の組として読むが、AF_UNIX の
    accept が返すのは空文字なので、ここで組へ差し替える。

    polling tick を `service_actions()` に乗せるのは、**書き手を 1 スレッドに保つため**。
    `serve_forever` は毎ループ同じスレッドから `service_actions()` を呼ぶので、ここから tick を
    駆動すれば event log の書き手が増えない (別スレッドにすると、この class が前提にしている
    「単一スレッドなので追記の直列性が構造で真」が崩れる)。代償は tick 中 HTTP が塞がること
    で、tracker CLI の timeout を client の応答待ちより短く取ることで受けている。
    """

    def __init__(
        self,
        socket_path,
        handler_class,
        api,
        *,
        tick=None,
        refresh=None,
        monotonic=time.monotonic,
    ):
        self.api = api
        # **tick を別スレッドにしない継ぎ目**。`serve_forever` は毎ループ同じスレッドから
        # `service_actions()` を呼ぶので、ここへ載せれば「書き手は daemon の 1 スレッド」
        # という不変条件を保ったまま周期処理が回る。`tick(now_monotonic)` は「今 tick を回す
        # べきなら回す」callable で、**周期の判断は tick 側** — server が周期を持つと、周期の
        # 違う観測 (tracker 60 秒 / SessionRuntime 10 秒) を足すたびに server を変えることになる
        self._tick = tick
        # dashboard が読む投影の更新 (`materialized_view.Projection`)。**tick と別の口にする**
        # のは周期が違うから — tick は 60 秒の観測周期を持つが、投影は HTTP 経由の記帳も
        # 画面へ届かせるために毎周回走る (書くかどうかは投影側が版で決める)
        self._refresh = refresh
        self._monotonic = monotonic
        super().__init__(str(socket_path), handler_class)

    def get_request(self):
        request, _ = super().get_request()
        return request, ("uds", 0)

    def service_actions(self):
        """`serve_forever` が毎ループ呼ぶ。周期処理は `drive_periodic_work` へ渡す。"""
        super().service_actions()
        drive_periodic_work(self._periodic_work())

    def _periodic_work(self):
        """このループで回す (名前, 呼び出し) の列。"""
        if self._tick is not None:
            yield "tick", functools.partial(self._tick, self._monotonic())
        if self._refresh is not None:
            yield "投影の更新", self._refresh


def drive_periodic_work(jobs, log=sys.stderr):
    """serve ループに相乗りする周期処理を順に回す。**1 つの失敗で他を止めない**。

    `(名前, 呼び出し)` の列を受けるのは、**server が周期処理の中身を知らないため** — 名前は
    log にしか使わない。tick が落ちても投影は走る (画面が「観測が止まっている」を表示できる) し、
    投影が落ちても tick は走る。どちらの失敗も daemon の停止理由にしない: HTTP 面が生きて
    いれば台帳の読み書きは続けられる。
    """
    for label, call in jobs:
        try:
            call()
        except Exception:  # noqa: BLE001 — 周期処理の失敗は daemon の停止理由にしない
            # **握り潰さずに traceback を残す** (落ち続けても誰も気付かない、を作らない)
            print(f"[dispatch-v2] {label} が失敗:\n{traceback.format_exc()}", file=log)


class Handler(http.server.BaseHTTPRequestHandler):
    """route 表への配線と、domain 例外 → status の翻訳だけを持つ。"""

    protocol_version = "HTTP/1.1"
    server_version = "dispatch-v2"

    # `timeout` (read の上限) は `make_server` が server ごとに束ねる。ここに既定を置くと、
    # 常に上書きされる到達不能な宣言が残る

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler の契約)
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def log_message(self, format, *args):  # noqa: A002 (BaseHTTPRequestHandler の契約)
        """既定の stderr への 1 行 log を殺す (daemon の log は自前で出す)。"""

    def _dispatch(self, method):
        api = self.server.api
        parsed = urlsplit(self.path)
        for route_method, pattern, name in ROUTES:
            matched = pattern.match(parsed.path)
            if matched is None or route_method != method:
                continue
            try:
                body = self._read_json_body()
                payload = getattr(api, name)(matched.groupdict(), parse_qs(parsed.query), body)
            except Exception as exc:  # noqa: BLE001 — status への翻訳がこの 1 箇所
                status = status_for(exc)
                if status >= 500:
                    # **想定外の失敗だけ traceback を daemon の log へ残す** — 応答本文に
                    # 載るのは 1 行の message だけで、それだけでは原因に辿り着けない。
                    # 4xx は「呼び出し側が直せる失敗」なので応答で足りる
                    print(
                        f"[dispatch-v2] {method} {parsed.path} が {status}:\n"
                        f"{traceback.format_exc()}",
                        file=sys.stderr,
                    )
                self._respond(status, {"error": str(exc), "error_type": type(exc).__name__})
                return
            self._respond(201 if name == "create_work_order" else 200, {"as_of": api.now(), **payload})
            return
        # 経路が無くても body は読み捨てる — 読み残すと keep-alive の次の request 行として
        # 解釈され、その接続の以降が全部ずれる
        self._read_json_body_bytes()
        self._respond(404, {"error": f"未知の経路: {method} {parsed.path}"})

    def _read_json_body_bytes(self):
        """宣言された長さぶんの body を読み切る (読み捨てにも使う)。"""
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _read_json_body(self):
        raw = self._read_json_body_bytes()
        if not raw:
            return {}
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadRequest(f"request body が JSON でない: {exc}") from exc
        if not isinstance(body, dict):
            raise BadRequest("request body が object でない")
        return body

    def _respond(self, status, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def status_for(exc):
    """例外 → HTTP status。**境界の契約なので公開してある** (誤った写像を固定できるように)。"""
    if isinstance(exc, BadRequest):
        return 400
    for error_type, status in STATUS_BY_ERROR:
        if isinstance(exc, error_type):
            return status
    return 500


def make_server(
    socket_path, api, idle_timeout=None, tick=None, refresh=None, monotonic=time.monotonic
):
    """UDS に bind した HTTP server を返す (serve_forever は呼び出し側が回す)。

    `idle_timeout` を server ごとに束ねるのは、待ち時間そのものを検証するテストが既定の
    10 秒を実時刻で待たずに済むようにするため (class 変数を書き換えると server 間で漏れる)。

    `tick` / `monotonic` は polling の駆動と時計。**注入できる形にしてあるのは、周期そのものを
    検証するテストが実時刻を待たずに済むため** (`principle-test-double-boundary`)。
    """
    handler = type(
        "BoundHandler",
        (Handler,),
        {"timeout": REQUEST_IDLE_TIMEOUT_SEC if idle_timeout is None else idle_timeout},
    )
    return UnixHttpServer(
        socket_path, handler, api, tick=tick, refresh=refresh, monotonic=monotonic
    )
