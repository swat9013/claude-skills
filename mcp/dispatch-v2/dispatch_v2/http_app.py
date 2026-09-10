"""reconciler daemon の HTTP/JSON 面 (UDS 上で listen する)。

設計 `docs/design/dispatch-v2/system.md`「7. reconciler daemon」: MCP server (stdio・セッション
ごと) は daemon への薄い client で、接続は **UDS 上の HTTP/JSON**。dashboard も後続 issue で
同じ daemon に同居する。

- **接続ごとにスレッドを立て、台帳へ触れる作業だけを `LedgerGate` で 1 つずつに直列化する**
  (gh#956)。追記の直列性は門が保証し、**`/health` だけが門を通らない** — 台帳が長い作業に
  占有されている間も「誰が何秒掴んでいるか」を答えられないと、詰まりを外から観測できず、
  client は health の無応答を「daemon が居ない」と読んで起こし直しに行く (`client.ensure_daemon`)
- **門へ入れなかった呼び出しは待ち続けず 503 で諦める**。client の応答待ちより短い予算で
  切り上げ、占有している route 名と経過秒を本文に載せる (timeout は「何が起きたか」を運ばない)
- **data を返す応答に `as_of` を載せる** (設計 system.md「9. MCP tool 面」の「全応答に観測時刻
  (鮮度) を明記する」)。
  台帳コアの段階では外部 store の観測 cache がまだ無いので、`as_of` は **daemon が memory の
  state から答えた時刻**を意味する。error 応答は data ではなく、MCP 面では応答 object ですら
  なく `ToolError` の message になるので載せない
- 例外は表に載せた系統だけを status へ写す。それ以外は 500 で本文に型名を出し、traceback を
  daemon の log へ残す (握り潰さない)
"""

import contextlib
import functools
import http.server
import json
import re
import socketserver
import sys
import threading
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

# 1 接続が読み待ちでスレッドを占有してよい上限 (秒)。黙った接続 (keep-alive で idle した
# browser / request の途中で死んだ client) がスレッドを掴んだままにならないよう、read を
# 諦める側で上限を付ける。同梱 client は 1 往復ごとに閉じるので通常は効かない。
# **既定の所在はここ 1 箇所**
REQUEST_IDLE_TIMEOUT_SEC = 10

# 台帳の門へ入れるのを待ってよい上限 (秒)。**client の応答待ち (`client.REQUEST_TIMEOUT_SEC`
# = 20 秒) より短く取る** — client が先に諦めると、応答に載せた「誰が掴んでいるか」が誰にも
# 届かないまま、呼び出し側には無応答としてしか見えない (gh#956 が報告した見え方そのもの)。
# 掴んだ側の作業そのもの (git / herdr) が続く時間も client の予算に収まる必要があるので、
# 半分を待ちに、半分を作業に残す
GATE_WAIT_BUDGET_SEC = 10

#: 台帳の門を通さない route。**health だけ**が例外で、それ以外は読みも書きも門の内側で走る
UNGATED_ROUTE = "health"

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


class LedgerBusy(RuntimeError):
    """待ち予算の内に台帳の門へ入れなかった (別の作業が掴んでいる)。

    **`RuntimeNotConfigured` のような構成不備と分けてある** — こちらは待てば消える一過性の
    混雑で、呼び出し側の正しい反応は「引数を直す」ではなく「掴んでいる作業を見て、待つか
    降ろすか決める」。message が掴んでいる route と経過秒を名乗るのはそのため。
    """


class LedgerGate:
    """台帳の状態へ触れる作業を 1 つずつに直列化する門 (gh#956)。

    v1 の設計は「serve も tick も単一スレッドなので追記の直列性が構造で真」に頼っていたが、
    その構造は **1 本の長い作業が health まで巻き込む**ことと引き換えだった。門に置き換えると
    直列性はそのままで、門を通らない route (`/health`) を作れる。

    `principle-isolate-shared-writes` が「lock は設計の見直し合図」と言うとおり、まず共有を
    解消できないかを見た: 台帳の追記・fold の state・観測 cache は **1 つの可変オブジェクトを
    全経路が読み書きする** (WorkOrder を書く経路と読む経路が同じ fold 結果を指す) ので、
    書き込み先を分ける形にはならない。残った共有を構造で直列化するのがこの門。

    **誰が掴んでいるかを名乗れる**のが素の `Lock` との差 (`principle-operability-first`) —
    詰まりを外から観測できないことが gh#956 の実害そのものだった。
    """

    def __init__(self, *, monotonic=time.monotonic):
        self._lock = threading.Lock()
        self._monotonic = monotonic
        # 掴んでいる作業 (route 名, 掴んだ時刻)。**lock の外から読む** — 詰まりの観測が
        # 詰まりの原因の後ろに並んだら観測にならない
        self._holder = None

    @contextlib.contextmanager
    def enter(self, label, *, wait_sec):
        """門を通って台帳へ触れる。待ち予算を超えたら `LedgerBusy`。

        `wait_sec=0` は「空いていなければ即諦める」— 周期処理 (tick / 投影) はこちらを使う。
        周期処理が request の後ろに並んで待つ理由は無く、諦めても次の周回で入り直せる
        (待つ形にすると、request が続く間 周期処理が門の待ち行列を占める)。
        """
        if not self._lock.acquire(timeout=wait_sec):
            raise LedgerBusy(f"台帳は {self._describe_holder()}。{label} は入れなかった")
        self._holder = (label, self._monotonic())
        try:
            yield
        finally:
            self._holder = None
            self._lock.release()

    def busy(self):
        """今 門を掴んでいる作業。health と error message が読む。

        **`None` は「空いている」の証拠ではない** — 門を取ってから名乗るまでの 2 命令の間に
        撮ると `None` が返る。掴んでいる作業が居れば名乗る、までが保証。
        """
        holder = self._holder
        if holder is None:
            return None
        label, since = holder
        return {"route": label, "held_for_sec": round(self._monotonic() - since, 3)}

    def _describe_holder(self):
        holder = self.busy()
        if holder is None:
            # 待っている間に相手が抜けた (次の呼び出しは通る)。**「空いている」とは言わない** —
            # 入れなかったのは事実なので、観測できなかったことをそのまま名乗る
            return "掴んでいた作業が判らないまま塞がっていた"
        return f"{holder['route']} が掴んでいる ({holder['held_for_sec']} 秒経過)"


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
    # 混雑は上流の不調でも呼び出し側の誤りでもない「今は無理」なので 503。retry で消える種類の
    # 失敗を 4xx / 5xx に混ぜると、呼び出し側が retry してよいかを status から読めない
    (LedgerBusy, 503),
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
        """既に開いた project key。**root 配下の走査はしない** (`adopt_existing_projects` が別)。

        **`/health` だけが台帳の門を通らない**ので (`LedgerGate`)、ここは門の内側で project を
        迎え入れている最中に走りうる。`sorted(dict)` は CPython では単一の C ループで写すので
        「dictionary changed size during iteration」にはならない — **Python の for で回さない**
        ことがその保証で、並行して迎え入れている最中なら写した時点の集合が返る (health の
        project 一覧は鮮度を売りにしていないので、それで足りる)。
        """
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
        gate=None,
    ):
        self.registry = registry
        self._clock = clock
        self._health_facts = health_facts or (lambda: {})
        # 台帳へ触れる作業を直列化する門。**Api が持ち主**で、handler は `server.api.gate` から
        # 引く — 門と台帳の持ち主が別だと、門を通さずに台帳へ触れる経路を足せてしまう
        self.gate = gate or LedgerGate()
        # 観測 cache の持ち主。**必須** — 観測経路を持たない daemon は組み立て経路に存在しない
        # ので、任意にすると「未観測」の理由語彙が本番に無い分岐のぶんだけ増える
        self.reconciler = reconciler
        # runtime を持たない Api は Session 系の経路だけが使えない (台帳の読み書きは動く)。
        # 既定を herdr にしないのは、**HTTP 層が特定 runtime を知らない**ため
        self._runtime = runtime
        self._run_git = run_git

    def health(self, _params, _query, _body):
        """daemon の生存と**今 台帳を掴んでいる作業**を答える (門を通らない唯一の route)。

        `busy` を載せるのが gh#956 の要 — これが読めれば「詰まっている」と「daemon が居ない」を
        呼び出し側が言い分けられる。載せないと、健康な daemon の無応答が「起動していない」と
        読まれて起こし直され、同じ作業がまた詰まる。

        **`周期処理` が短く名乗るのは正常**。周期処理スレッドは 0.5 秒ごとに門を取るので、
        健康な daemon でも `held_for_sec` が 0 に近い `周期処理` がしばしば見える。
        読むべきは**名前と経過秒の組**で、`busy` が埋まっていること自体は詰まりの証拠ではない。
        """
        return {
            **self._health_facts(),
            "projects": self.registry.known_projects(),
            "busy": self.gate.busy(),
        }

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
            anchor=_require_anchor(body),
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


def _require_anchor(body):
    """呼び出し側が観測した割り元 (`anchor_handle` + `anchor_workspace`)。

    **境界で必須にする** — 割り元は「どの実行単位の隣に worker を並べるか」で、知っているのは
    呼び出し側だけ (gh#952)。名乗らない spawn を内側へ通すと、作業ツリーを作り Session を
    `launch_error` で記帳してから、決して成功しえない起動が失敗する。呼び出し側が直せる不備
    なので、runtime の不調 (502) ではなく 400 で返す。

    片方だけ渡された anchor も **400 にする** — workspace が無いと adapter は観測窓をそこへ
    広げられず、割った worker が列挙から漏れたまま生き続ける。
    """
    handle = body.get("anchor_handle")
    workspace = body.get("anchor_workspace")
    if handle is None and workspace is None:
        raise BadRequest(
            "anchor_handle / anchor_workspace が無い (herdr session の中から dispatch する。"
            "割り元を名乗らない spawn は、呼び出し元と無関係な workspace へ worker を開く)"
        )
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


class UnixHttpServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """UDS 上の HTTP server。**接続ごとにスレッドを立て、台帳は `LedgerGate` で直列化する**。

    `BaseHTTPRequestHandler` は client_address を (host, port) の組として読むが、AF_UNIX の
    accept が返すのは空文字なので、ここで組へ差し替える。

    ## なぜ単一スレッドをやめたか (gh#956)

    以前は「serve も tick も 1 スレッド」で追記の直列性を構造から得ていた。代償は
    **1 本の長い作業 (session_spawn の `git worktree add`、育った台帳の tick) が accept ごと
    止めること**で、`/health` すら答えられなくなる。client はそれを「daemon が居ない」と読んで
    起こし直し (`client.ensure_daemon`)、起こし直した先で同じ作業がまた詰まる — 復旧手段が
    人手の `kill` しか残らない。直列性は門で保てるが、**答える口は門の外に出せる**。

    ## なぜ周期処理を serve ループから降ろしたか (gh#967)

    門を作った後も、周期処理だけは `service_actions()` (serve ループと同一スレッド) から
    回していた。**その間 `accept` が止まる**ので、remote 到達不能な project の `git pull` を
    掴んだ tick は `/health` まで沈黙させる — 門の外に出した口が、門とは別の理由で塞がる。
    周期処理を専用スレッドへ移すと、serve ループは `accept` だけを持つ。

    ```
    accept スレッド ─┬─ /health          → 門を通らない。常に即答 (busy を名乗る)
                     └─ それ以外          → 門へ入る。入れなければ 503
    周期処理スレッド ── PeriodicWorker    → 門が空いていれば tick / 投影を回す (待たない)
    ```

    `daemon_threads` / `block_on_close` を両方倒すのは、**`server_close()` が handler スレッドを
    join しないため**。片方だけでは join が残り、長い git を掴んだスレッドが居ると停止がそこで
    固まる = gh#956 が訴えた「戻らない」をこちらで再生産する (`ThreadingMixIn.server_close` は
    `block_on_close` だけを見る)。
    """

    daemon_threads = True
    block_on_close = False

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
        # **周期処理の継ぎ目**。回すのは `PeriodicWorker` の専用スレッドで (gh#967)、台帳へは
        # 門を通って触るので書き手が増える心配は無い。server がこの 2 つを抱えるのは、
        # serve と周期処理のライフサイクルを 1 箇所で合わせるため。
        # `tick(now_monotonic)` は「今 tick を回すべきなら回す」callable で、**周期の判断は
        # tick 側** — server が周期を持つと、周期の違う観測 (tracker 60 秒 / SessionRuntime
        # 10 秒) を足すたびに server を変えることになる
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

    def serve_forever(self, poll_interval=0.5):
        """`accept` を回し続ける。**周期処理は専用スレッドが持つ** (gh#967)。

        worker を止めるのは合図だけで、**join しない** — 長い git を掴んだ worker を待つと、
        `daemon_threads` / `block_on_close` で避けたはずの「停止が戻らない」をこちらで
        再生産する。daemon thread なのでプロセスの終了は妨げない。
        """
        worker = PeriodicWorker(self.api.gate, self._periodic_work)
        worker.start()
        try:
            super().serve_forever(poll_interval)
        finally:
            worker.stop()

    def _periodic_work(self):
        """このループで回す (名前, 呼び出し) の列。"""
        if self._tick is not None:
            yield "tick", functools.partial(self._tick, self._monotonic())
        if self._refresh is not None:
            yield "投影の更新", self._refresh

    def handle_error(self, request, client_address):
        """handler から抜けた例外の後始末。**client の切断だけを 1 行へ落とす**。"""
        if report_request_failure(sys.exc_info()[1], client_address=client_address):
            return
        super().handle_error(request, client_address)


#: client が応答を待たずに降りたときに socket が上げる例外。**「daemon の異常」ではない**
CLIENT_GONE_ERRORS = (BrokenPipeError, ConnectionResetError)


def report_request_failure(exc, *, client_address, log=sys.stderr):
    """request の失敗が client の切断なら 1 行 log へ落として True。それ以外は False。

    切断は **正常な事象** (呼び出し側が応答待ちを諦めた) なのに、socketserver の既定は 40 行の
    traceback を積む。daemon の log は詰まりを人が読む唯一の場所なので、正常な事象で埋めると
    本当の異常が見えなくなる。**握り潰すのではなく格を下げる** — 切断が起きたことは残る
    (`principle-fail-loudly` の「沈黙の失敗を作らない」)。
    """
    if not isinstance(exc, CLIENT_GONE_ERRORS):
        return False
    print(
        f"[dispatch-v2] client が応答を待たずに切断した ({client_address}): "
        f"{type(exc).__name__}: {exc}",
        file=log,
    )
    return True


#: 周期処理が門を掴むときに名乗る名前 (`/health` の `busy` と 503 の本文に出る)
PERIODIC_WORK_LABEL = "周期処理"

#: 周期処理スレッドが次の周回まで休む間隔 (秒)。**周期を持つのは job 側** (`PacedTick` /
#: `Schedule`) で、ここが決めるのは「どれくらいの粒度で見に来るか」だけ。最短の job 周期
#: (SessionRuntime の 10 秒) より十分細かければよく、accept 側の `poll_interval` とは無関係
PERIODIC_POLL_INTERVAL_SEC = 0.5


class PeriodicWorker:
    """周期処理を **serve ループの外** で回す daemon thread (gh#967)。

    `service_actions()` から回していた頃は、remote 到達不能な project の `git pull` を掴んだ
    tick が `accept` ごと止め、門を通らないはずの `/health` まで沈黙した。回す場所を分けると、
    詰まるのは門の内側だけになり、**詰まっていることを外から観測できる状態が保たれる**
    (`principle-operability-first`)。

    **停止は合図だけで、join しない** — 長い外部コマンドを掴んだままの worker を待つと、
    停止が戻らなくなる (`UnixHttpServer` が `block_on_close` を倒しているのと同じ理由)。
    """

    def __init__(self, gate, jobs):
        self._gate = gate
        # 毎周回 (名前, 呼び出し) の列を作り直す callable。**列そのものを受けない**のは、
        # 1 度きりの iterator を使い回すと 2 周目以降が空になるため
        self._jobs = jobs
        self._stopping = threading.Event()

    def start(self):
        # **thread の handle を持たない** — 止めるのは合図だけで join しないので、持っても
        # 誰も読まない (daemon thread なのでプロセスの終了も妨げない)
        threading.Thread(target=self._loop, name="dispatch-v2-periodic", daemon=True).start()

    def stop(self):
        self._stopping.set()

    def _loop(self):
        # **最初に休んでから回す**。起動直後に投影を回すと、まだ何も記帳されていない
        # 台帳を写すだけで 1 周ぶん無駄になる (最初の tick を遅らせる保証は
        # `reconciler.FIRST_TICK_DELAY_SEC` が持つ — こちらは周回の粒度だけを決める)
        while not self._stopping.wait(PERIODIC_POLL_INTERVAL_SEC):
            drive_periodic_work_when_free(self._gate, self._jobs())


def drive_periodic_work_when_free(gate, jobs, log=sys.stderr):
    """門が空いていれば周期処理を回す。掴まれていたら**待たずに諦める**。

    待たないのは、周期処理が request の後ろに並んで待つ理由が無いから — 諦めても
    `PacedTick` の予定時刻は進まないので、次の周回で入り直す。待つ形にすると、request が
    続く間に周期処理が門の待ち行列を占め、request 側の待ち予算を削る。

    **server から切り出してある**のは、socket を bind せずにこの判断を検査できるようにする
    ため (`drive_periodic_work` と同じ理由)。
    """
    try:
        with gate.enter(PERIODIC_WORK_LABEL, wait_sec=0):
            drive_periodic_work(jobs, log=log)
    except LedgerBusy:
        return  # 掴まれている間は回さない (次の周回で入り直す)


def drive_periodic_work(jobs, log=sys.stderr):
    """周期処理スレッドの 1 周回ぶんを順に回す。**1 つの失敗で他を止めない**。

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
                # **body を読むのは門の外**。読み待ちを門の内側でやると、黙った client 1 本が
                # 台帳を掴んだまま idle timeout まで居座る
                body = self._read_json_body()
                payload = self._call_route(api, name, matched.groupdict(), parsed, body)
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

    def _call_route(self, api, name, params, parsed, body):
        """route を呼ぶ。**`/health` 以外は台帳の門を通す** (`LedgerGate`)。"""
        call = functools.partial(getattr(api, name), params, parse_qs(parsed.query), body)
        if name == UNGATED_ROUTE:
            return call()
        with api.gate.enter(name, wait_sec=self.gate_wait_sec):
            return call()

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
    socket_path,
    api,
    idle_timeout=None,
    tick=None,
    refresh=None,
    monotonic=time.monotonic,
    gate_wait_sec=None,
):
    """UDS に bind した HTTP server を返す (serve_forever は呼び出し側が回す)。

    `idle_timeout` / `gate_wait_sec` を server ごとに束ねるのは、待ち時間そのものを検証する
    テストが既定の秒数を実時刻で待たずに済むようにするため (class 変数を書き換えると server
    間で漏れる)。

    `tick` / `monotonic` は polling の駆動と時計。**注入できる形にしてあるのは、周期そのものを
    検証するテストが実時刻を待たずに済むため** (`principle-test-double-boundary`)。
    """
    handler = type(
        "BoundHandler",
        (Handler,),
        {
            "timeout": REQUEST_IDLE_TIMEOUT_SEC if idle_timeout is None else idle_timeout,
            "gate_wait_sec": GATE_WAIT_BUDGET_SEC if gate_wait_sec is None else gate_wait_sec,
        },
    )
    return UnixHttpServer(
        socket_path, handler, api, tick=tick, refresh=refresh, monotonic=monotonic
    )
