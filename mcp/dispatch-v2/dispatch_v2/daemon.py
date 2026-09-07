"""reconciler daemon のライフサイクル (マシンに 1 プロセス・UDS・単一書き手)。

ADR 0056: reconciler は LLM を持たない常駐プロセス。本 module が持つのは**起動の収束と占有、
そして観測経路の配線**だけ。観測 / rule 評価 / escalation の中身は `reconciler` module にあり、
tick は `serve_forever` と**同じスレッド**で回る (`http_app.UnixHttpServer.service_actions`)。

起動の収束 (`principle-idempotent-operations`):

```
root を作る
  └ daemon.lock を flock (非 block)
      ├ 取れない → 既に daemon が居る。loud に降りる (先行を kill しない)
      └ 取れた   → socket file が残っていれば残骸なので unlink → bind → serve
```

**占有印は保持者プロセスの生存で判定する** — lock を握れたということは前の daemon が
死んでいるということなので、socket file の有無で二重起動を判定しない (残骸を人手で
消してもらう前提を作らない)。
"""

import fcntl
import os
import signal
import sys
from pathlib import Path

from dispatch_v2 import (
    dashboard,
    event_log,
    http_app,
    materialized_view,
    project,
    reconciler,
    runtime_herdr,
)

DAEMON_VERSION = "0.1.0"


class AlreadyRunning(RuntimeError):
    """同じ root の daemon が既に走っている。"""


class DaemonLock:
    """`daemon.lock` の排他保持。取得できたプロセスだけが root の唯一の書き手になる。"""

    def __init__(self, path):
        self.path = Path(path)
        self._descriptor = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            raise AlreadyRunning(
                f"{self.path} を保持している daemon が既に居る (マシンに 1 プロセス)"
            ) from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode("utf-8"))
        os.fsync(descriptor)
        self._descriptor = descriptor
        return self

    def release(self):
        if self._descriptor is None:
            return
        fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        os.close(self._descriptor)
        self._descriptor = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_exc):
        self.release()


def build_server(
    root,
    *,
    clock=event_log.now_iso,
    started_at=None,
    idle_timeout=None,
    runtime=None,
    dashboard_facts=lambda: None,
):
    """lock を握った前提で UDS の HTTP server を組み立てる (bind まで)。

    `runtime` を渡せるのは、**実 herdr を叩かずに配線を検証する**ため (既定は herdr adapter)。

    `dashboard_facts` は同居 dashboard の bind 結果を `/health` へ載せる口。**dashboard の
    生死は daemon の生死と別**なので、ここでは事実を読む callable だけを受ける (bind の実行は
    `run` の担当)。
    """
    socket_path = project.socket_path(root)
    if socket_path.exists():
        # lock を握れている = 前の daemon は死んでいる。残骸は消して bind する
        socket_path.unlink()
    registry = http_app.LedgerRegistry(root, clock=clock)
    # 既存の台帳を起動時に迎え入れる。tick が回るのは registry が知っている project だけなので、
    # ここを飛ばすと再起動後は誰かが tool を叩くまで観測が始まらない
    adopted = registry.adopt_existing_projects()
    if adopted:
        print(f"[dispatch-v2] 既存の台帳 {len(adopted)} 件を観測対象にした", file=sys.stderr)
    # runtime の選択は daemon が持つ (HTTP 層も reconciler も特定 backend を知らない)。adapter が
    # 増えたらここが分岐点になる — 今は 1 つしか無いので表を作らない
    worker_runtime = runtime or runtime_herdr.HerdrRuntime()
    # 観測 cache と job の周期を tick を跨いで保つので、**daemon が 1 つだけ持って使い回す**
    # (tick ごとに作り直すと候補集合の差分も連続失敗の数も job の周期も毎回リセットされ、
    # rule が永久に発火しない)
    machine = reconciler.Reconciler(registry, runtime=worker_runtime)
    # dashboard が読む投影。**書き手はこの main thread だけ**で、dashboard 側は同じ file を
    # read-only で開く (`materialized_view` の docstring が thread 境界の正本)
    view = materialized_view.MaterializedView.open(project.view_path(root))
    api = http_app.Api(
        registry,
        clock=clock,
        reconciler=machine,
        runtime=worker_runtime,
        health_facts=lambda: {
            "status": "ok",
            "version": DAEMON_VERSION,
            "pid": os.getpid(),
            "started_at": started_at or clock(),
            "root": str(root),
            "socket": str(socket_path),
            "dashboard": dashboard_facts(),
        },
    )
    return http_app.make_server(
        socket_path,
        api,
        idle_timeout=idle_timeout,
        tick=reconciler.PacedTick(machine, clock=clock),
        refresh=materialized_view.Projection(
            view, registry=registry, reconciler=machine, clock=clock
        ),
    )


def run(root, *, clock=event_log.now_iso):
    """daemon を起動して serve し続ける。既に走っていれば AlreadyRunning。

    dashboard は**別スレッドで serve する**。UDS 側の serve ループは単一スレッドで tick も
    そこで回るので、同居させると長い tick がそのまま画面の停止になる — 接点は投影の SQLite
    file だけに保つ (`materialized_view` の docstring)。
    """
    root = Path(root).expanduser()
    lock = DaemonLock(project.lock_path(root)).acquire()
    board = dashboard.start_from_environ(project.view_path(root), clock=clock)
    try:
        server = build_server(
            root, clock=clock, started_at=clock(), dashboard_facts=board.facts
        )
    except BaseException:
        # bind した port を掴んだまま落ちない (次の起動が自分の残骸で塞がる)
        board.stop()
        lock.release()
        raise
    socket_path = Path(server.server_address)
    _install_shutdown_handlers(server)
    board.serve_in_background()
    print(f"[dispatch-v2] daemon 起動 pid={os.getpid()} socket={socket_path}", file=sys.stderr)
    if board.facts()["bound"]:
        print(f"[dispatch-v2] dashboard {board.facts()['url']}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass  # SIGTERM / SIGINT による正常停止 (_install_shutdown_handlers)
    finally:
        board.stop()
        server.server_close()
        socket_path.unlink(missing_ok=True)
        lock.release()
        print("[dispatch-v2] daemon 停止", file=sys.stderr)


def _install_shutdown_handlers(server):
    """SIGTERM / SIGINT で serve_forever を抜けさせ、socket と lock を後片付けさせる。"""

    def stop(_signum, _frame):
        # serve_forever は同一スレッドから shutdown() を呼ぶと deadlock するので、
        # 例外で抜けさせて finally の後片付けへ渡す
        raise KeyboardInterrupt

    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, stop)
