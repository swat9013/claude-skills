"""daemon への UDS HTTP client と lazy 起動。

設計 `docs/design/dispatch-v2/system.md`「7. reconciler daemon」: MCP server は daemon への
薄い client で、daemon が居なければ起動する。呼び出し側
(tool 関数) が持つのは呼び出し口だけで、daemon の内部手順は持たない
(`principle-delegate-opaquely`)。

**daemon は stdlib だけで動く**ので、起動は `sys.executable` で直接叩く — uv の cache が
冷えている / offline でも lazy 起動が落ちない。
"""

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from dispatch_v2 import project

# daemon の応答待ち。台帳は memory の fold から答えるので、待つのは起動直後だけ
REQUEST_TIMEOUT_SEC = 20

# lazy 起動後に health が立つまで待つ上限と刻み
STARTUP_TIMEOUT_SEC = 15
STARTUP_POLL_SEC = 0.1

# 停止の合図を送ってから daemon が降りるのを待つ上限と刻み。SIGTERM は serve ループを抜け
# させるだけで、抜けるのは走っている tick が終わってから
STOP_TIMEOUT_SEC = 30
STOP_POLL_SEC = 0.2

DAEMON_ENTRY = Path(__file__).resolve().parents[1] / "daemon_main.py"


class DaemonUnavailable(RuntimeError):
    """daemon へ接続できない / 起動しても health が立たない。"""


class DaemonStopFailed(RuntimeError):
    """停止の合図は送ったのに daemon が期限内に降りない / 合図を送れない。

    **`DaemonUnavailable` と分けてある** — あちらは「daemon が居ない」で、こちらは「居るのに
    退かない」。混ぜると、止められなかったことが「起動できない」として読まれる。
    """


class DaemonError(RuntimeError):
    """daemon が 4xx / 5xx を返した (本文の error をそのまま持つ)。"""

    def __init__(self, status, payload):
        self.status = status
        self.payload = payload
        super().__init__(f"daemon が {status} を返した: {payload.get('error', payload)}")


class UnixHttpConnection(http.client.HTTPConnection):
    """`http.client` の作法のまま UDS へ繋ぐ。"""

    def __init__(self, socket_path, timeout):
        super().__init__("localhost", timeout=timeout)
        self._socket_path = str(socket_path)

    def connect(self):
        stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stream.settimeout(self.timeout)
        try:
            stream.connect(self._socket_path)
        except OSError as exc:
            stream.close()
            raise DaemonUnavailable(f"{self._socket_path} へ接続できない: {exc}") from exc
        self.sock = stream


def request(socket_path, method, path, body=None, timeout=REQUEST_TIMEOUT_SEC):
    """daemon へ 1 往復する。返り値は (status, payload)。"""
    connection = UnixHttpConnection(socket_path, timeout)
    try:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json; charset=utf-8"} if encoded else {}
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        raw = response.read()
    except DaemonUnavailable:
        raise
    except OSError as exc:
        raise DaemonUnavailable(f"daemon との通信に失敗: {exc}") from exc
    finally:
        connection.close()
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DaemonUnavailable(f"daemon の応答が JSON でない: {raw[:200]!r}") from exc
    return response.status, payload


def ensure_daemon(root, *, spawn=None, sleep=time.sleep, monotonic=time.monotonic):
    """daemon が応答する状態にして health を返す。不在なら起動して待つ。

    残骸 socket の掃除は daemon 側の起動収束が持つ (lock を握れた側が消す)。ここは
    「接続できないなら起動する」までしか判断しない。
    """
    socket_path = project.socket_path(root)
    try:
        return _health(socket_path)
    except DaemonUnavailable:
        pass
    (spawn or _spawn_daemon)(root)
    deadline = monotonic() + STARTUP_TIMEOUT_SEC
    last_failure = None
    while monotonic() < deadline:
        try:
            return _health(socket_path)
        except DaemonUnavailable as exc:
            last_failure = exc
            sleep(STARTUP_POLL_SEC)
    raise DaemonUnavailable(
        f"{_absence_or_silence(root)} ({STARTUP_TIMEOUT_SEC} 秒で health が立たない。"
        f"直近: {last_failure}。log: {project.daemon_log_path(root)})"
    )


def _process_exists(pid):
    """その pid のプロセスが居るか (signal 0 は届くだけで何もしない)。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # 権限が無い = 別の所有者のプロセスが居る。**「居ない」とは読まない**
        return True
    return True


def _absence_or_silence(root, *, process_exists=_process_exists):
    """health が立たない理由を「居ない」と「居るのに黙っている」に言い分ける。

    **復旧手段が別物だから 1 行を分ける** (gh#956) — 居ないなら起動を待つ / log を読むが、
    居るのに黙っているなら降ろす (`daemon_restart`) しかない。issue の報告者が `kill` しか
    思い付かなかったのは、両方が「daemon を起動したが health が立たない」の 1 文で出ていた
    ため。`/health` は台帳の門を通らないので (`http_app.LedgerGate`)、**掴まれていることは
    黙る理由にならない** — ここまで来た黙りは socket の残骸や起動途中での死にしぼられる。

    **見る前に子を回収する**。ここへ来る典型は「起こした daemon が bind / import で即死した」で、
    その死体は親 (MCP server) が回収するまで zombie として残り、`os.kill(pid, 0)` に生きている
    ものとして映る。回収しないと **一番多い失敗で逆の診断を出す** — 読むべき daemon.log では
    なく `daemon_restart` へ案内してしまう。
    """
    _reap_finished()
    pid = _recorded_pid(root)
    if pid is None:
        return "daemon を起動したが health が立たない"
    if not process_exists(pid):
        return f"daemon を起動したが health が立たない (占有印の pid {pid} は既に居ない)"
    return (
        f"daemon (pid {pid}) は生きているのに health を返さない。"
        "降ろして起こし直す (daemon_restart)"
    )


def restart_daemon(root, *, signal_process=os.kill, sleep=time.sleep, monotonic=time.monotonic, spawn=None):
    """走っている daemon を降ろして起こし直し、止めた pid と新しい health を返す。

    **起こし直した daemon はこのプロセスの環境を継承する** (`_spawn_daemon` が `os.environ` を
    渡す)。呼び出し元の MCP server は呼んでいるセッションの子プロセスなので、これが「新しい
    daemon が今生きている実行単位を割り元として継承する」の実体になる (gh#932)。

    daemon が居なければ**起こすだけ**で成功する — 再実行しても同じ終状態 (新しい daemon が
    1 つ走っている) へ収束させる (`principle-idempotent-operations`)。
    """
    stopped_pid = _stop_daemon(
        root, signal_process=signal_process, sleep=sleep, monotonic=monotonic
    )
    health = ensure_daemon(root, spawn=spawn, sleep=sleep, monotonic=monotonic)
    if stopped_pid is not None and health.get("pid") == stopped_pid:
        # **起こし直したことを応答の pid で確かめる** — 降ろしたつもりの daemon が答え直して
        # いるのに成功を返すと、割り元が入れ替わっていないことが呼び出し側から見えない
        raise DaemonStopFailed(
            f"降ろしたはずの daemon (pid {stopped_pid}) がそのまま答えている "
            f"(log: {project.daemon_log_path(root)})"
        )
    return {"stopped_pid": stopped_pid, "health": health}


def _stop_daemon(root, *, signal_process, sleep, monotonic):
    """今 走っている daemon へ SIGTERM を送り、応答が止まるまで待つ。止めた pid を返す。

    宛先は **応答している daemon が名乗る pid を先に採る** — 占有印 (`daemon.lock`) は書いた
    プロセスが消えても残るので、印だけを信じると、生きている daemon の隣に 2 つ目を起こす。
    印を使うのは、応答が無い (刺さっている) daemon を降ろすときだけ。

    **降りたことは「その pid が応答しなくなったこと」で判定する** — signal を送れたことは
    停止の証拠にならない (SIGTERM は serve ループを抜けさせるだけで、後片付けは daemon 側)。
    """
    pid = _serving_pid(root) or _recorded_pid(root)
    if pid is None:
        return None
    try:
        signal_process(pid, signal.SIGTERM)
    except ProcessLookupError:
        return None  # 占有印だけが残っていた (既に居ない)
    except OSError as exc:
        raise DaemonStopFailed(
            f"daemon (pid {pid}) へ停止の合図を送れない: {exc} "
            f"(lock: {project.lock_path(root)})"
        ) from exc
    deadline = monotonic() + STOP_TIMEOUT_SEC
    while monotonic() < deadline:
        if not _is_serving(root, pid):
            return pid
        sleep(STOP_POLL_SEC)
    # **合図が届いたのか、届いても降りられないのかを分けて言う** — 前者なら後片付けが
    # 終わらない側 (何を掴んでいるかは health の `busy` に出る)、後者なら合図そのものが
    # 主スレッドへ届いていない。次に見る場所が違う (gh#956)
    acknowledged = (
        "停止の合図は受け取っている (後片付けが終わらない。health の busy を見る)"
        if _took_the_stop_signal(root, pid)
        else "停止の合図を受け取った様子が無い"
    )
    raise DaemonStopFailed(
        f"daemon (pid {pid}) が {STOP_TIMEOUT_SEC} 秒で降りない — {acknowledged} "
        f"(log: {project.daemon_log_path(root)})"
    )


def _recorded_pid(root):
    """占有印 (`daemon.lock`) が名乗る pid。読めない / pid でなければ None。"""
    try:
        recorded = project.lock_path(root).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(recorded)
    except ValueError:
        return None


def _serving_pid(root):
    """今 応答している daemon が名乗る pid。誰も答えなければ None。"""
    try:
        return _health(project.socket_path(root)).get("pid")
    except DaemonUnavailable:
        return None


def _is_serving(root, pid):
    """その pid の daemon がまだ応答しているか。別 pid が答えたら「降りた」と読む。

    **`shutting_down` を「降りた」と読まない** — 占有印 (flock) を手放すのは後片付けの最後
    なので、合図を受けた時点で降りたことにすると、次に起こす daemon が生きている flock に
    当たって即死し、誰も serve していない状態で `ensure_daemon` が空振りする。合図を受けた
    かどうかは**待ち切れたときの説明**にだけ使う (`_stop_daemon`)。
    """
    try:
        return _health(project.socket_path(root)).get("pid") == pid
    except DaemonUnavailable:
        return False


def _took_the_stop_signal(root, pid):
    """その pid の daemon が停止の合図を受け取ったと名乗っているか (診断用)。"""
    try:
        health = _health(project.socket_path(root))
    except DaemonUnavailable:
        return False
    return health.get("pid") == pid and bool(health.get("shutting_down"))


def call(root, method, path, body=None):
    """daemon を確保してから 1 往復し、成功 payload を返す。"""
    ensure_daemon(root)
    status, payload = request(project.socket_path(root), method, path, body=body)
    if status >= 400:
        raise DaemonError(status, payload)
    return payload


def _health(socket_path):
    status, payload = request(socket_path, "GET", "/health", timeout=STARTUP_TIMEOUT_SEC)
    if status != 200:
        raise DaemonUnavailable(f"health が {status} を返した: {payload}")
    return payload


def _spawn_daemon(root):
    """daemon を detach して起動する (親セッションが終わっても生き残る)。

    root は環境変数で渡す — daemon は「1 root = 1 プロセス」なので、起動引数ではなく
    daemon 自身が読む既定の解決経路 (`project.default_root`) に合わせる。
    """
    _reap_finished()
    root = Path(root).expanduser()
    # 起動そのものの失敗 (log を置けない / interpreter を起動できない) を素の OSError のまま
    # 上げると、MCP 層の「予期した失敗」の列に載らず message ごと潰れる
    try:
        root.mkdir(parents=True, exist_ok=True)
        with project.daemon_log_path(root).open("ab") as log:
            _SPAWNED.append(
                subprocess.Popen(
                    [sys.executable, str(DAEMON_ENTRY)],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                    env={**os.environ, project.ROOT_ENV: str(root)},
                )
            )
    except OSError as exc:
        raise DaemonUnavailable(f"daemon を起動できない ({DAEMON_ENTRY}): {exc}") from exc


# このプロセスが起こした daemon の handle。**終了した子を回収するためだけに持つ** —
# 起動したまま handle を捨てると、daemon が先に死んだとき親が生きている間ずっと zombie が残る
_SPAWNED = []


def _reap_finished():
    """既に終わっている daemon プロセスを回収する。"""
    _SPAWNED[:] = [process for process in _SPAWNED if process.poll() is None]
