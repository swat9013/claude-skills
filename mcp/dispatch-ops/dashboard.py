#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""dispatch 台帳 × tracker × pane × worktree を dispatch entry 主語で見る read-only dashboard。

**利用者は人間**で、LLM の入力ではない。orchestrator の応答文か `resolve` tool の JSON を
読むしかなかった現況を、1 枚の画面へ落とす。**記帳も pane 操作もしない** — HTTP 面は
`do_GET` だけ、tracker 面 (`CachedTracker`) と pane 面 (`HerdrSnapshotPort`) は読み取り
method しか実装しない。read-only を規約ではなく構造で保証する。

本 script は同梱 MCP server の兄弟 module (`ledger` / `vocabulary` / `resolve` / `scope` /
`project` / `tracker` / `worktree` / `pane`) を import して組み立てる。**drift 判定も phase
語彙も持ち込まない** — 突合は `resolve.resolve` を呼び、表示語彙は `resolve.DRIFT_KINDS` と
`vocabulary.PHASE_ATTRIBUTES` から引く。同じ判定を dashboard 側に書くと、server が判定を
変えたときに画面だけが古い規則で色を塗る。

依存ゼロ (標準ライブラリのみ) で、`127.0.0.1` にしか bind しない。

データ源と鮮度:

| 源 | 鮮度 | 置き場 |
|---|---|---|
| 台帳 (`state.json` / `events.jsonl`) | request ごとに読み直す (画面は 5 秒 polling) | `~/.claude/issue-dispatch/<repo_key>/` |
| tracker (gh / glab) | 60 秒 TTL の**サーバー側**キャッシュ | `CachedTracker` |
| pane (herdr) | request ごと。直近出力は要求されたときだけ | `HerdrSnapshotPort` |
| git worktree | request ごと | `worktree.GitWorktrees` |

キャッシュを port の面に置くのは、endpoint の面に置くと endpoint を 1 つ足すたびに
キャッシュを通らない経路が増えるため。

**観測できなかった列は「未観測」として出し、空や偽値へ潰さない** (`resolve` の `checked` と
同じ思想)。herdr に届かない環境でも起動し、pane 列だけが未観測になる。

起動は **dispatch-ops server プロセス側**が行う (`dashboard_autostart`)。server が起動するたびに
port を見て、居なければここを起こす。以下は手動起動 (`autostart` を切ってある環境と、下の引数を
渡したいとき) の綴り:

    uv run --script mcp/dispatch-ops/dashboard.py [--port 8765] [--workspace wN] [--candidate-label ...]
"""

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

# uv run が PEP 723 script をどう起動しても sibling module を解決できるようにする
# (script ディレクトリの sys.path 追加は起動側の実装に依存させない)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger as ledger_mod  # noqa: E402
import pane as pane_mod  # noqa: E402
import pane_herdr as pane_herdr_mod  # noqa: E402
import proc  # noqa: E402
import project as project_mod  # noqa: E402
import repo_key as repo_key_mod  # noqa: E402
import resolve as resolve_mod  # noqa: E402
import scope as scope_mod  # noqa: E402
import tracker as tracker_mod  # noqa: E402
import vocabulary  # noqa: E402
import worktree as worktree_mod  # noqa: E402

BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# 台帳を読み直す間隔 (画面へ配って JS の polling 周期にする)。台帳は file 読みだけなので
# tracker と違って毎回読み直してよい
LEDGER_POLL_MS = 5000

# tracker 観測のサーバー側 TTL。1 entry あたり CLI が起動するので、台帳と同じ周期で
# 撃つと gh / glab を撃ち続けることになる
TRACKER_TTL_SEC = 60

HERDR_TIMEOUT_SEC = 30

# pane の直近出力を要求されたときに読む行数の既定と上限
DEFAULT_OUTPUT_LINES = 40
MAX_OUTPUT_LINES = 400

# herdr が読めなかったときの理由コード。**「届かない」と「応答形式が読めない」を分ける** —
# 前者は herdr が起動していない / socket が別 (利用者が直せる)、後者は herdr 側の応答が
# 想定と違う (本 script の追随が要る) で、直し方が別。両者を 1 つの「未観測」へ丸めると、
# 追随が要る壊れ方が「herdr を起動していないのだろう」に埋もれる
HERDR_UNREACHABLE = "herdr_unreachable"
HERDR_UNREADABLE = "herdr_response_unreadable"
HERDR_WORKSPACE_UNRESOLVED = "herdr_workspace_unresolved"

# herdr の socket。pane 外から起動する dashboard はこの env で session の socket を指す
HERDR_SOCKET_ENV = "HERDR_SOCKET_PATH"

# 契約上の記帳主体 (`ledger` が正本)。ここに無い actor は契約外として印を付ける
CONTRACT_ACTORS = (
    ledger_mod.DEFAULT_ACTOR,
    ledger_mod.OBSERVER_ACTOR,
    ledger_mod.OUTCOME_ACTOR,
)

# 表示上の読み替え。live 台帳には契約外 actor `orchestrator` の行が混入しており
# (server は actor 語彙を検証しない = fail-open)、生のまま並べると同じ書き手が 2 名に見える。
# **読み替えても契約外である事実は消さない** — 消すと沈黙の失敗になるので、
# `off_contract` を立てて画面に印を出す
ACTOR_DISPLAY_ALIASES = {"orchestrator": ledger_mod.DEFAULT_ACTOR}

# 全体タイムラインの既定件数 (events.jsonl は数百行あり、全量を毎 request 返す意味が無い)
DEFAULT_EVENT_LIMIT = 200
MAX_EVENT_LIMIT = 2000

DASHBOARD_HTML = Path(__file__).resolve().parent / "dashboard.html"

# herdr CLI の起動境界。テストはこの名前を monkeypatch する
run_command = proc.command_runner(error=pane_mod.PaneError, timeout_sec=HERDR_TIMEOUT_SEC)


# --- herdr (preflight を通さない read-only port) --------------------------------------


class HerdrSnapshotPort(pane_mod.PanePort):
    """`herdr api snapshot` 1 回で pane 一覧と agent 状態を読む read-only な `PanePort`。

    **`pane_herdr.HerdrAdapter` と共有しない**。あちらの `ensure_ready` は fail-closed の
    4 段検査 (`HERDR_ENV=1` / hook が現行 / socket 疎通 / 自セッションの受信可否) を要求し、
    自 pane の label を書き換える副作用まで持つ。dashboard は pane 外・herdr session 外から
    起動されるので前提が 1 つも成立せず、**副作用を持つ検査を read-only な画面のために
    通す理由が無い**。到達手段は同じ socket で、env (`HERDR_SOCKET_PATH`) を立てれば pane の
    外からも届く。

    継ぎ目のうち実装するのは読み取り 3 つ (`list_panes` / `get_pane` / `self_pane_id`) だけ。
    `launch_pane` / `close_pane` / `send_text` は基底の `NotImplementedError` のまま残す。

    **観測する workspace を必ず 1 つに決める。** `herdr api snapshot` は session 内の全
    workspace を返すので、絞らずに join へ渡すと別 project の同番号 label (`i775`) を自分の
    追跡対象として拾う。決まらなければ pane 列ごと未観測にする — 誤って join した pane を
    事実として並べるより、見ていないと言うほうが安い。

    **`__init__` は失敗しない。** `scope.get_pane()` は `resolve.observe_stores` から無防備に
    呼ばれており、生成時に投げると pane 列の未観測で済むはずの状況が join 全体の失敗になる。
    失敗は最初の観測 (`ensure_ready`) まで遅らせる。

    寿命は 1 観測分。**成功も失敗も覚える** — `HerdrAdapter` がプロセス寿命ゆえに成功だけを
    覚えるのに対し、本 class は毎観測で作り直されるので、同じ観測の中で 2 度 CLI を起動する
    理由が無い (画面は pane 列と pane 出所の両方を 1 回の観測から読む)。
    """

    backend = pane_herdr_mod.BACKEND

    def __init__(self, workspace=None):
        self._requested_workspace = workspace
        self._ready = None
        self._failure = None
        self._panes = None

    # --- 前提検査 (副作用なし) -----------------------------------------------------

    def ensure_ready(self):
        """snapshot を 1 回引いて観測対象 workspace を確定する。

        失敗は理由コード付きの `PaneError`。**呼び出し側は `resolve` 経由でこの例外を
        store の `error` として受け取る**ので、文字列の先頭にコードを置いて画面が
        「届かない」と「読めない」を出し分けられるようにする。
        """
        if self._failure is not None:
            raise self._failure
        if self._ready is not None:
            return self._ready
        try:
            snapshot = self._fetch_snapshot()
            workspace = self._select_workspace(snapshot)
            panes = [
                record
                for record in _require_list(snapshot, "panes")
                if record.get("workspace_id") == workspace["workspace_id"]
            ]
        except pane_mod.PaneError as exc:
            self._failure = exc
            raise
        self._panes = panes
        self._ready = {
            "backend": self.backend,
            "workspace": workspace["workspace_id"],
            "workspace_label": workspace.get("label"),
            "workspace_source": workspace["source"],
            "socket_path": os.environ.get(HERDR_SOCKET_ENV),
        }
        return self._ready

    def _fetch_snapshot(self):
        rc, out, err = run_command(["herdr", "api", "snapshot"])
        if rc != 0:
            raise pane_mod.PaneError(
                f"{HERDR_UNREACHABLE}: herdr api snapshot が失敗 (exit {rc}): {err.strip()}"
            )
        try:
            payload = json.loads(out)
        except json.JSONDecodeError as exc:
            raise pane_mod.PaneError(
                f"{HERDR_UNREADABLE}: herdr api snapshot が JSON でない ({exc})"
            ) from exc
        snapshot = (payload or {}).get("result", {}).get("snapshot")
        if not isinstance(snapshot, dict):
            raise pane_mod.PaneError(
                f"{HERDR_UNREADABLE}: herdr api snapshot に result.snapshot が無い"
            )
        return snapshot

    def _select_workspace(self, snapshot):
        """観測する workspace を 1 つに決める (明示指定 > 唯一の workspace)。

        複数あって指定が無いときは決めない。cwd や repo_key から workspace を推測する経路を
        作らない — workspace の label は利用者が付けた表示名で、project との対応は宣言されて
        いない。推測して当たれば正しく見え、外れると別 project の pane を混ぜたまま
        「観測できた」と表示するので、外れ方が silent になる。
        """
        workspaces = _require_list(snapshot, "workspaces")
        requested = self._requested_workspace
        if requested is not None:
            for record in workspaces:
                if requested in (record.get("workspace_id"), record.get("label")):
                    return {**record, "source": "requested"}
            raise pane_mod.PaneError(
                f"{HERDR_WORKSPACE_UNRESOLVED}: workspace {requested!r} が herdr に無い "
                f"(在るのは: {_workspace_names(workspaces)})"
            )
        if len(workspaces) == 1:
            return {**workspaces[0], "source": "sole"}
        raise pane_mod.PaneError(
            f"{HERDR_WORKSPACE_UNRESOLVED}: workspace が {len(workspaces)} 個あり、どれがこの "
            f"project か機械では決まらない (--workspace で指定する。在るのは: "
            f"{_workspace_names(workspaces)})"
        )

    # --- 継ぎ目 (読み取りのみ) -----------------------------------------------------

    def list_panes(self):
        return [
            {"pane_id": record.get("pane_id"), "label": record.get("label")}
            for record in self._observed_panes()
        ]

    def get_pane(self, pane_id):
        """pane 1 件。**同じ snapshot から引く** (pane ごとに CLI を起動しない)。

        `label` / `agent` は snapshot に在るときだけ載せる — `None` を載せると
        `list_panes` 側で読めていた label を打ち消し、追跡 pane が追跡外に化ける
        (`pane_herdr.HerdrAdapter.get_pane` と同じ理由)。
        """
        for record in self._observed_panes():
            if record.get("pane_id") != pane_id:
                continue
            mapped = {
                "pane_id": pane_id,
                "agent_status_raw": record.get("agent_status") or pane_mod.UNKNOWN_RAW_STATUS,
            }
            for field in ("label", "agent"):
                if field in record:
                    mapped[field] = record[field]
            return mapped
        raise pane_mod.PaneError(f"{HERDR_UNREADABLE}: pane {pane_id} が snapshot に無い")

    def self_pane_id(self):
        """dashboard は pane の中に居ないので常に None (どの pane も `is_self` にならない)。"""
        return None

    def _observed_panes(self):
        self.ensure_ready()
        return self._panes

    # --- 直近出力 (port の面ではない) ----------------------------------------------

    def read_output(self, pane_id, lines=DEFAULT_OUTPUT_LINES):
        """pane の直近出力 (`herdr pane read`)。

        `PanePort` の継ぎ目にしない — 中立 pane schema に載る値ではなく、人間が画面で
        「今この worker は何をしているか」を読むための生テキストなので、port の面を
        広げずに本 class 固有の method として持つ。

        **要求されたときだけ読む。** 全 pane 分を毎 request 読むと、pane 数だけ CLI が
        起動する経路が 5 秒周期で回る。
        """
        rc, out, err = run_command(
            ["herdr", "pane", "read", pane_id, "--lines", str(lines), "--format", "text"]
        )
        if rc != 0:
            return {
                "pane_id": pane_id,
                "text": None,
                "error": f"{HERDR_UNREACHABLE}: herdr pane read が失敗 (exit {rc}): {err.strip()}",
            }
        return {"pane_id": pane_id, "text": out, "error": None}


def _require_list(snapshot, key):
    value = snapshot.get(key)
    if not isinstance(value, list):
        raise pane_mod.PaneError(
            f"{HERDR_UNREADABLE}: herdr api snapshot の {key} が list でない "
            f"({type(value).__name__})"
        )
    return value


def _workspace_names(workspaces):
    names = ", ".join(
        f"{record.get('workspace_id')} ({record.get('label')})" for record in workspaces
    )
    return names or "なし"


# --- tracker (TTL 付きの読み取り面) ---------------------------------------------------


class CachedTracker:
    """`TrackerPort` の**読み取りだけ**を TTL 付きで包む (dashboard 専用)。

    包む理由は 2 つ:

    - **撃つ回数**: tracker 観測は 1 entry あたり CLI が起動する。台帳と同じ 5 秒周期で
      撃つと gh / glab を撃ち続けるので、port の面で 60 秒だけ結果を使い回す
    - **read-only**: 面を読み取り method だけに閉じる。`issue_claim` / `issue_label` /
      `issue_comment` はここに無いので、dashboard の経路からは呼べない

    `resolve` が新しい読み取り method を使い始めたら `AttributeError` で落ちる。素通しの
    `__getattr__` を置かないのは、**書き込み method まで素通しになる**ため。
    """

    def __init__(self, adapter, *, ttl_sec=TRACKER_TTL_SEC, clock=time.monotonic):
        self._adapter = adapter
        self._ttl_sec = ttl_sec
        self._clock = clock
        self._entries = {}
        self._lock = threading.Lock()

    @property
    def tracker(self):
        return self._adapter.tracker

    @property
    def supports_repo_scope(self):
        return self._adapter.supports_repo_scope

    def observe_issue(self, issue_ref, repo=None):
        return self._cached("observe_issue", issue_ref, repo=repo)

    def observe_issues(self, **kwargs):
        return self._cached("observe_issues", **kwargs)

    def observe_prs(self, issue_ref=None, repo=None):
        return self._cached("observe_prs", issue_ref=issue_ref, repo=repo)

    def observe_pr_refs(self, records, *, issue_ref=None, repo=None):
        return self._cached("observe_pr_refs", records, issue_ref=issue_ref, repo=repo)

    def observe_review_threads(self, records, *, repo=None):
        return self._cached("observe_review_threads", records, repo=repo)

    def require_review_threads(self):
        """adapter が review thread に対応しているか。**CLI を起動しない**のでキャッシュしない。"""
        return self._adapter.require_review_threads()

    def _cached(self, name, *args, **kwargs):
        """TTL 内なら前回の結果を返す。**例外も同じ TTL でキャッシュする**。

        失敗を素通しにすると、tracker が落ちている間だけ 5 秒ごとに CLI を起動し続ける
        (最も撃ちたくない状況で最も撃つ)。

        lock を握ったまま adapter を呼ぶのは、同時に届いた request が同じ観測を二重に
        撃つのを防ぐため — 待った側は埋まったキャッシュを読む。
        """
        key = json.dumps([name, args, kwargs], sort_keys=True, default=str)
        with self._lock:
            found = self._entries.get(key)
            if found is not None and self._clock() - found["at"] < self._ttl_sec:
                return _replay(found)
            try:
                value = self._adapter_call(name, args, kwargs)
            except tracker_mod.TrackerError as exc:
                self._entries[key] = {"at": self._clock(), "value": None, "error": exc}
                raise
            self._entries[key] = {"at": self._clock(), "value": value, "error": None}
            return value

    def _adapter_call(self, name, args, kwargs):
        return getattr(self._adapter, name)(*args, **kwargs)


def _replay(found):
    if found["error"] is not None:
        raise found["error"]
    return found["value"]


# --- project (台帳 1 つ = project 1 つ) ------------------------------------------------


class Project:
    """dashboard が 1 つの台帳ディレクトリについて知っていること。

    **外部 store を観測できるのは、dashboard を起動した cwd の clone に対応する project
    だけ**。台帳の repo_key (`github.com__owner__name`) から clone のパスは復元できず、
    worktree も git 操作も root 無しでは観測できないため。他 project は台帳だけを表示し、
    tracker / pane / worktree の列は理由付きで未観測にする — 推測した root で観測すると、
    別 clone の worktree を「この project のもの」として並べることになる。
    """

    def __init__(self, repo_key, ledger, stores=None, unobservable_reason=None):
        self.repo_key = repo_key
        self.ledger = ledger
        self.stores = stores
        self.unobservable_reason = unobservable_reason


class StoreAccess:
    """1 project 分の外部 store への到達手段 (プロセス寿命で使い回す)。

    tracker adapter と worktree port はプロセス内で 1 つに保ち、**pane port だけ観測ごとに
    作り直す** — pane port は snapshot を 1 回だけ引いて抱えるので、使い回すと画面が起動時の
    pane を見続ける。

    `scope.Scope` をそのまま組み立てて `resolve` へ渡す。port を dashboard 側で並べ替えて
    `resolve` の下位関数を直接呼ぶ形にはしない — 「resolve が scope の何を要るか」の対応表が
    tool 層と dashboard の 2 箇所へ写り、port が 1 つ増えるたびに両方を直すことになる
    (`resolve.resolve` の docstring と同じ理由)。
    """

    def __init__(self, root, declaration, *, workspace=None, ttl_sec=TRACKER_TTL_SEC):
        self.root = root
        self.declaration = declaration
        self.workspace = workspace
        self._tracker = _cached_adapter(declaration["issue"]["tracker"], ttl_sec)
        self._pr_tracker = _cached_adapter(declaration["pr"]["tracker"], ttl_sec)
        self._worktrees = worktree_mod.GitWorktrees(root)

    def scope(self, ledger):
        """この観測 1 回分の `Scope`。"""
        return scope_mod.Scope(
            root=self.root,
            ledger=ledger,
            declaration=self.declaration,
            pane=HerdrSnapshotPort(workspace=self.workspace),
            adapter=self._tracker,
            pr_adapter=self._pr_tracker,
            worktrees={None: self._worktrees},
        )

    def pane_source(self, scope):
        """pane 列の出所 (どの workspace を、どう決めて観測したか)。

        **port を立てた本 class が答える。** `scope.get_pane()` の面 (`PanePort`) には
        workspace の決め方が無く、外から問うと port の実体を決め打ちすることになる。

        観測できたかどうかは `stores.panes` が持つ。ここが答えるのは「何を見たか」で、
        **決まらなかったときは理由コードを載せる** — 「未観測」だけでは herdr が居ないのか
        workspace が決まらないのかが読めない。
        """
        try:
            ready = scope.get_pane().ensure_ready()
        except pane_mod.PaneError as exc:
            return {"workspace": None, "source": None, "error": str(exc)}
        return {
            "workspace": ready["workspace"],
            "workspace_label": ready["workspace_label"],
            "source": ready["workspace_source"],
            "socket_path": ready["socket_path"],
            "error": None,
        }


def _cached_adapter(tracker_name, ttl_sec):
    """tracker 名 → TTL 付き read-only adapter。組み立てられなければ None。

    None は `Scope` にとって「まだ組み立てていない」の意味なので、`get_adapter_optional`
    が実 adapter を作りに行く。宣言が無い / 未実装 tracker (Jira) では即座に `TrackerError`
    になる経路で CLI は起動しない — キャッシュする対象が無い。
    """
    if tracker_name is None:
        return None
    try:
        return CachedTracker(tracker_mod.get_adapter(tracker_name), ttl_sec=ttl_sec)
    except tracker_mod.TrackerError:
        return None


def open_projects(*, ledger_root=None, workspace=None, cwd=None):
    """台帳 root 配下の全 project を開き、`({repo_key: Project}, 既定 repo_key)` を返す。

    cwd が git repo でない / 宣言を解けない場所でも**起動は止めない** — その場合はどの
    project も外部 store を観測できない画面になるだけで、台帳の閲覧自体は成立する。
    """
    root = Path(ledger_root) if ledger_root else ledger_mod.default_ledger_root()
    observable_key, stores, reason = _observable_project(workspace, cwd)
    projects = {}
    for directory in sorted(ledger_directories(root)):
        key = directory.name
        ledger = ledger_mod.Ledger(directory, repo_key=key)
        if key == observable_key:
            projects[key] = Project(key, ledger, stores=stores)
        else:
            projects[key] = Project(key, ledger, unobservable_reason=reason(key))
    return projects, observable_key


def ledger_directories(root):
    """台帳ディレクトリ (state.json を持つもの) だけを拾う。README 等は台帳ではない。

    public なのは、自動起動 (`dashboard_autostart`) が「起こしても即座に終わる環境か」を
    同じ規則で判定するため。**「台帳ディレクトリとは何か」を 2 箇所に持たない。**
    """
    if not root.is_dir():
        return []
    return [
        child
        for child in root.iterdir()
        if child.is_dir() and (child / ledger_mod.STATE_FILENAME).is_file()
    ]


def _observable_project(workspace, cwd):
    """cwd の clone に対応する project → `(repo_key, StoreAccess, 未観測理由の生成)`。"""
    try:
        root = repo_key_mod.main_worktree_root(Path(cwd) if cwd else Path.cwd())
        key = repo_key_mod.derive_repo_key(root)
        declaration = project_mod.resolve_declaration(root)
    except (repo_key_mod.RepoKeyError, project_mod.ProjectError) as exc:
        detail = f"dashboard を起動した cwd から clone を解決できない ({exc})"
        return None, None, lambda _key: detail
    stores = StoreAccess(str(root), declaration, workspace=workspace)

    def reason(other_key):
        return (
            f"外部 store を観測できるのは dashboard を起動した clone の project ({key}) "
            f"だけ。{other_key} の clone のパスは台帳から復元できない (その clone で "
            "dashboard を起動し直す)"
        )

    return key, stores, reason


# --- 観測 (台帳 × 外部 store) ---------------------------------------------------------


def observe_project(project, scope):
    """project 1 つの現況 → `resolve.join` の出力 (観測できなければ同じ形の空観測)。

    未観測を組み立てるのに `resolve.Observation` を使うので、store 名も `checked` の規則も
    dashboard 側へ写らない。
    """
    entries = project.ledger.list_entries()
    if scope is None:
        return _unobserved_result(entries, project.unobservable_reason)
    try:
        return resolve_mod.resolve(entries, scope)
    except (
        tracker_mod.TrackerError,
        worktree_mod.WorktreeError,
        pane_mod.PaneError,
        resolve_mod.ResolveError,
    ) as exc:
        # store 単位の失敗は `resolve` が未観測として畳むので、ここへ来るのは観測の前提
        # (置き場の tracker 名 / repo 識別子の解決) が立たなかったとき。台帳の閲覧まで
        # 巻き添えにしない
        return _unobserved_result(entries, f"外部 store の観測前提が立たない: {exc}")


def _unobserved_result(entries, reason):
    """全 store 未観測の `resolve.join` 相当。drift は 1 件も出ない。"""
    observation = resolve_mod.Observation(
        **{name: resolve_mod.unobserved(reason) for name in resolve_mod.STORES}
    )
    return {
        "tracker": None,
        "scope": None,
        "stores": observation.report(),
        "count": len(entries),
        "current": [_unobserved_view(entry, observation) for entry in entries],
        "drift_count": 0,
        "drift": [],
        "unjoinable": [],
        "unmappable_observations": {},
    }


def _unobserved_view(entry, observation):
    """台帳だけで組み立てた entry 現況。

    `ledger` block に `EntryView.raw` をそのまま載せる (`resolve._entry_view` は同じ raw の
    部分集合を載せる)。**部分集合の綴りを写さない**のは、写した瞬間に「出力に載る台帳の欄」の
    正本が 2 箇所になるため。画面は両経路で同じ key を読み、余分な key は使わない。

    `derived` は null。観測が 1 つも無い状態で `mechanical_done` を組み立てると、
    「未検査だから決められない」を dashboard 側で再現することになり、`resolve` の 3 値規則の
    写しが増える。
    """
    return {
        "issue_ref": entry.issue_ref,
        "phase": entry.phase,
        "ledger": entry.raw,
        "observed": {slot: None for slot in resolve_mod.ENTRY_STORES},
        "checked": {
            slot: observation.checked(store, entry.issue_ref, joinable=False)
            for slot, store in resolve_mod.ENTRY_STORES.items()
        },
        "unjoinable_reason": None,
        "derived": None,
    }


def observe_candidates(project, scope, label):
    """候補プール (claim label の付いていない open issue)。

    着手可 label は **明示引数 → その project の宣言 `issue.ready_label` → 絞らない** の順で
    決まる (tracker 系 tool の `repo` と同じ「明示引数が宣言に勝つ」規則)。dashboard 自身は
    既定の綴りを持たない — 焼き込むと、誰も宣言していないポリシーを画面が事実として表示する。
    宣言が無い project は絞らずに出す (画面は読むだけなので、絞れないことは害にならない)。
    """
    if scope is None:
        return {"observed": False, "error": project.unobservable_reason, "label": label}
    label = label or scope.ready_label()
    adapter = scope.get_adapter_optional()
    if adapter is None:
        return {
            "observed": False,
            "error": "issue 置き場の adapter が無い (宣言が無い / 未実装 tracker)",
            "label": label,
        }
    try:
        observed = adapter.observe_issues(
            state="open",
            # 除外は claim label の不在で判定する (ADR 0053)。assignee で絞ると、人が担当に
            # 付いた着手可 issue が候補から落ち、AI の駐機と人の担当が同じ信号に潰れる
            labels_none=[scope.claim_label()],
            labels_any=[label] if label else None,
            repo=scope.issue_repo(None),
        )
    except tracker_mod.TrackerError as exc:
        return {"observed": False, "error": str(exc), "label": label}
    return {
        "observed": True,
        "error": None,
        "label": label,
        "count": observed["count"],
        "truncated": observed["truncated"],
        "issues": observed["issues"],
    }


# --- 出力 (JSON payload) --------------------------------------------------------------


def projects_payload(projects, default_key):
    """project 一覧 + phase 別の総計。台帳しか読まないので全 project 分を毎回返す。"""
    return {
        "default": default_key,
        "phases": list(vocabulary.PHASES),
        "projects": [
            {
                "repo_key": project.repo_key,
                "ledger_dir": str(project.ledger.directory),
                "observable": project.stores is not None,
                "unobservable_reason": project.unobservable_reason,
                **_phase_totals(entry.phase for entry in project.ledger.list_entries()),
            }
            for project in projects.values()
        ],
    }


def _phase_totals(phases):
    counts = {phase: 0 for phase in vocabulary.PHASES}
    total = 0
    for phase in phases:
        total += 1
        if phase in counts:
            counts[phase] += 1
    return {"total": total, "counts": counts}


def entries_payload(project, *, candidate_label=None, now=None):
    """dispatch entry 主語の現況 1 画面分。

    scope を 1 つ作って観測 3 つ (join / pane 出所 / 候補プール) で共有する — 別々に作ると
    pane の snapshot と tracker のキャッシュ判定が観測ごとにずれる。
    """
    now = now or datetime.now().astimezone()
    scope = project.stores.scope(project.ledger) if project.stores is not None else None
    result = observe_project(project, scope)
    rows = [_row(view, now) for view in result["current"]]
    return {
        "repo_key": project.repo_key,
        "ledger_dir": str(project.ledger.directory),
        "observable": project.stores is not None,
        "unobservable_reason": project.unobservable_reason,
        "observed_at": now.isoformat(),
        "poll_interval_ms": LEDGER_POLL_MS,
        "tracker_ttl_sec": TRACKER_TTL_SEC,
        "tracker": result["tracker"],
        "stores": result["stores"],
        "pane_source": (
            project.stores.pane_source(scope)
            if scope is not None
            else {"workspace": None, "source": None, "error": project.unobservable_reason}
        ),
        "candidates": observe_candidates(project, scope, candidate_label),
        **_phase_totals(row["phase"] for row in rows),
        "entries": rows,
        "drift": result["drift"],
        "drift_count": result["drift_count"],
        "unjoinable": result["unjoinable"],
        "unmappable_observations": result["unmappable_observations"],
        "vocabulary": {
            "phases": list(vocabulary.PHASES),
            "phase_attributes": vocabulary.PHASE_ATTRIBUTES,
            # 画面が既定で畳む phase。**「履歴か現況か」の線は server の語彙から引く** —
            # 台帳は cleaned が積み上がる一方なので、畳まないと現況が履歴に埋もれる。
            # 畳む基準を画面側で決めると、phase が増えたとき黙って古い基準で畳む
            "terminal_phases": [
                phase for phase in vocabulary.PHASES if phase in vocabulary.TERMINAL_PHASES
            ],
            "drift_kinds": {
                kind: {"expected": expected, "observed": observed}
                for kind, (expected, observed) in resolve_mod.DRIFT_KINDS.items()
            },
            # 画面が `checked.<欄>` から `stores.<store>.error` を引くための対応表。
            # **JS 側へ写さず payload で配る** — 写すと store を 1 つ足したときに、
            # 未観測の理由を出せない欄が黙って増える
            "entry_stores": dict(resolve_mod.ENTRY_STORES),
            "contract_actors": list(CONTRACT_ACTORS),
        },
    }


def _row(view, now):
    """`resolve` の現況 1 件 → 画面が読む行。**判定は足さず、経過時間と phase 属性を添える**。"""
    return {
        **view,
        "elapsed_sec": _elapsed_sec((view["ledger"] or {}).get("updated_at"), now),
        "phase_attributes": vocabulary.PHASE_ATTRIBUTES.get(view["phase"]),
    }


def _elapsed_sec(updated_at, now):
    """`updated_at` からの経過秒。読めなければ None (0 に潰さない)。"""
    if not updated_at:
        return None
    try:
        stamp = datetime.fromisoformat(updated_at)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        return None
    return int((now - stamp).total_seconds())


def events_payload(project, *, actor=None, limit=DEFAULT_EVENT_LIMIT):
    """全体タイムライン (新しい順)。actor で絞れる。

    絞り込みは**生の actor** で行う。表示の読み替え (`orchestrator` → `dispatcher`) で絞ると、
    契約外の行が契約内の名前に吸収されて「その actor で絞った」の意味が変わる。
    """
    limit = max(1, min(int(limit), MAX_EVENT_LIMIT))
    events = read_events(project.ledger)
    facets = _actor_facets(events)
    if actor is not None:
        events = [event for event in events if event.get("actor") == actor]
    tail = events[-limit:]
    tail.reverse()
    return {
        "repo_key": project.repo_key,
        "actor": actor,
        "limit": limit,
        "total": len(events),
        "actors": facets,
        "events": [{**event, "actor_display": actor_display(event.get("actor"))} for event in tail],
    }


def read_events(ledger):
    """`events.jsonl` を読む。壊れた行は落とさず `_unparsed` として残す。

    捨てると「その時刻に何も起きていない」に化ける。台帳は追記専用の記録なので、読めない行が
    在ること自体が観測結果。
    """
    path = ledger.events_path
    if not path.is_file():
        return []
    found = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            found.append(json.loads(line))
        except json.JSONDecodeError as exc:
            found.append({"_unparsed": line, "_error": str(exc), "_line": number})
    return found


def actor_display(raw):
    """記帳主体 → 表示名と契約内かどうかの印。

    読み替えても**契約外である事実は消さない**。live 台帳には契約外の `orchestrator` が
    混入しており (server は actor 語彙を検証しない = fail-open)、読み替えるだけだと
    「契約どおり書かれている」と読める画面になる。読み替えと可視化は別の要求なので両方やる。
    """
    if raw is None:
        return {"raw": None, "name": None, "off_contract": True}
    return {
        "raw": raw,
        "name": ACTOR_DISPLAY_ALIASES.get(raw, raw),
        "off_contract": raw not in CONTRACT_ACTORS,
    }


def _actor_facets(events):
    """actor フィルタの選択肢 (生の actor と件数)。"""
    counts = {}
    for event in events:
        counts[event.get("actor")] = counts.get(event.get("actor"), 0) + 1
    return [
        {**actor_display(raw), "count": count}
        for raw, count in sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))
    ]


# --- HTTP ------------------------------------------------------------------------------


class DashboardHandler(BaseHTTPRequestHandler):
    """read-only な HTTP 面。**`do_GET` しか実装しない** (他 method は基底が 501 で返す)。"""

    protocol_version = "HTTP/1.1"

    def __init__(self, *args, projects, default_key, candidate_label, **kwargs):
        self._projects = projects
        self._default_key = default_key
        self._candidate_label = candidate_label
        super().__init__(*args, **kwargs)

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler の綴り)
        parsed = urlsplit(self.path)
        segments = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
        query = parse_qs(parsed.query)
        try:
            self._route(segments, query)
        except _NotFound as exc:
            self._send_json({"error": str(exc)}, status=404)
        except Exception as exc:  # noqa: BLE001 (画面を落とさず理由を JSON で返す)
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    def _route(self, segments, query):
        if not segments:
            return self._send_html()
        if segments[0] != "api":
            raise _NotFound(f"未知のパス: /{'/'.join(segments)}")
        if segments[1:] == ["projects"]:
            return self._send_json(projects_payload(self._projects, self._default_key))
        if len(segments) == 4 and segments[1] == "projects":
            return self._project_route(self._project(segments[2]), segments[3], query)
        raise _NotFound(f"未知のパス: /{'/'.join(segments)}")

    def _project_route(self, project, name, query):
        if name == "entries":
            return self._send_json(
                entries_payload(project, candidate_label=self._candidate_label)
            )
        if name == "events":
            return self._send_json(
                events_payload(
                    project,
                    actor=_first(query, "actor"),
                    limit=_first(query, "limit") or DEFAULT_EVENT_LIMIT,
                )
            )
        if name == "pane-output":
            return self._send_json(self._pane_output(project, query))
        raise _NotFound(f"未知の endpoint: {name}")

    def _pane_output(self, project, query):
        """pane の直近出力。**要求されたときだけ herdr を撃つ**。"""
        pane_id = _first(query, "pane_id")
        if not pane_id:
            raise _NotFound("pane_id クエリが無い")
        if project.stores is None:
            return {"pane_id": pane_id, "text": None, "error": project.unobservable_reason}
        lines = max(1, min(int(_first(query, "lines") or DEFAULT_OUTPUT_LINES), MAX_OUTPUT_LINES))
        port = HerdrSnapshotPort(workspace=project.stores.workspace)
        try:
            port.ensure_ready()
        except pane_mod.PaneError as exc:
            return {"pane_id": pane_id, "text": None, "error": str(exc)}
        return port.read_output(pane_id, lines)

    def _project(self, repo_key):
        """URL の repo_key を**開いてある project の集合から**引く。

        受け取った文字列でパスを組み立てない — 台帳 root の外を読ませない唯一の防ぎ方。
        """
        project = self._projects.get(repo_key)
        if project is None:
            raise _NotFound(f"未知の project: {repo_key}")
        return project

    def _send_html(self):
        try:
            body = DASHBOARD_HTML.read_bytes()
        except OSError as exc:
            raise _NotFound(f"{DASHBOARD_HTML} を読めない: {exc}") from exc
        self._send(body, "text/html; charset=utf-8", 200)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", status)

    def _send(self, body, content_type, status):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 (基底の綴り)
        sys.stderr.write(f"{datetime.now().astimezone().isoformat()} {format % args}\n")


class _NotFound(LookupError):
    """要求されたパス / project / pane が無い。"""


def _first(query, name):
    values = query.get(name) or []
    return values[0] if values else None


def serve(projects, default_key, *, port, candidate_label):
    """127.0.0.1 の指定 port で待ち受ける。**bind 先は引数にしない**。

    台帳には issue の note や PR の状態が入る。他人に見せる前提のデータではないので、LAN へ
    晒す選択肢を CLI の面に置かない。
    """
    handler = partial(
        DashboardHandler,
        projects=projects,
        default_key=default_key,
        candidate_label=candidate_label,
    )
    server = ThreadingHTTPServer((BIND_HOST, port), handler)
    print(f"dispatch dashboard: http://{BIND_HOST}:{port}/ (Ctrl-C で終了)", file=sys.stderr)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"待ち受け port (既定 {DEFAULT_PORT})"
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="観測する herdr workspace の id か label。省略時は workspace が 1 つのときだけ"
        "自動で決まり、複数あれば pane 列は未観測になる",
    )
    parser.add_argument(
        "--herdr-socket",
        default=None,
        help=f"herdr の socket path ({HERDR_SOCKET_ENV} を上書きする)。pane 外から起動する"
        "ときに session の socket を指す",
    )
    parser.add_argument(
        "--candidate-label",
        default=None,
        help="候補プールを絞る着手可 label。省略すると project ごとの宣言 (`[issue] "
        "ready_label`) を使い、宣言も無ければ未 claim の open issue を label で絞らずに出す",
    )
    parser.add_argument(
        "--ledger-root", default=None, help="台帳 root (既定は ~/.claude/issue-dispatch)"
    )
    return parser.parse_args(argv)


def main(args):
    if args.herdr_socket:
        os.environ[HERDR_SOCKET_ENV] = args.herdr_socket
    projects, default_key = open_projects(ledger_root=args.ledger_root, workspace=args.workspace)
    if not projects:
        print("台帳が 1 つも無い (dispatch を 1 度も記帳していない)", file=sys.stderr)
        return 1
    return serve(projects, default_key, port=args.port, candidate_label=args.candidate_label)


if __name__ == "__main__":
    try:
        sys.exit(main(parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)
