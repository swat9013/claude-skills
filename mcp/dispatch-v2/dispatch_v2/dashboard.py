"""daemon に同居する dashboard HTTP (設計 `docs/design/dispatch-v2/system.md`「7. reconciler daemon」)。

**利用者は人間**で、LLM の入力ではない。orchestrator の応答文か tool の JSON を読むしかなかった
現況を、project 横断の 1 枚へ落とす (v1 dashboard の「起動 cwd の project しか観測できない」
制約の解消 — 設計 決定 6 / 10)。

## read-only を構造で保証する (規約で守らない)

3 段とも「書けないようにする」であって「書かないと決める」ではない:

1. **`mode=ro` の SQLite 接続しか持たない** (`view_read.ViewReader`)。正本
   (events.jsonl) にも daemon の memory 上の state にも触らない
2. **`do_GET` 以外の method を実装しない**。`BaseHTTPRequestHandler` は実装の無い method を
   501 で返すので、POST / PUT / DELETE は route 表に届く前に落ちる
3. **bind 先は `DASHBOARD_HOST` 固定**。引数で受けないので、設定ミスで公開されることが無い

## daemon 本体との関係

serve するのは**別スレッド**で、daemon の serve ループとは投影の SQLite file だけで繋がる。
なぜその境界なのかは `materialized_view` の docstring が正本。

**dashboard の失敗で daemon を止めない**。port が塞がっていても daemon の本務 (台帳と観測) は
続き、bind できなかった事実と理由は `/health` と stderr に出る — 沈黙はしないが、致命でもない。
"""

import json
import os
import sys
import threading
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from dispatch_v2 import event_log, view_read

#: bind 先。**引数にしない** — dashboard は台帳と観測の生データを丸ごと見せる面なので、
#: 設定を 1 つ間違えたら LAN へ出る、という形にしない
DASHBOARD_HOST = "127.0.0.1"

#: 既定の port。v1 dashboard (8765) と別の値を採り、併走中に取り合わない
DEFAULT_PORT = 8790

PORT_ENV = "DISPATCH_V2_DASHBOARD_PORT"

#: serve thread の終了を待つ上限。**超えても socket は手放す** (待つのは診断のためだけ)
THREAD_JOIN_TIMEOUT_SEC = 5

#: 画面本体。**request のたびに読む** — 数十 KB の file 1 つで、代わりに daemon を再起動せずに
#: 画面を直せる
PAGE_PATH = Path(__file__).resolve().parent / "dashboard.html"

#: 応答 1 つ。HTTP の作法 (header / status 行) は `Handler` が持ち、ここは中身だけを運ぶ
Response = namedtuple("Response", "status content_type body")


class DashboardError(RuntimeError):
    """dashboard の設定が読めない (port の綴り等)。"""


def configured_port(environ):
    """環境から port を読む。**読めない綴りは既定へ落とさず落とす**。

    黙って既定へ倒すと、port を指定したつもりの利用者が別の場所を見に行って「dashboard が
    出ない」と読む (`principle-fail-loudly` の「暗黙のフォールバックは禁止」)。落ちた先は
    daemon の停止ではなく dashboard の不成立で、理由は `/health` に出る。
    """
    configured = environ.get(PORT_ENV)
    if configured is None or configured == "":
        return DEFAULT_PORT
    try:
        port = int(configured)
    except ValueError as exc:
        raise DashboardError(f"{PORT_ENV} が数値でない: {configured!r}") from exc
    if not 1 <= port <= 65535:
        raise DashboardError(f"{PORT_ENV} が port の範囲外: {port}")
    return port


class DashboardApi:
    """path → 応答。**投影を読むだけ**で、台帳にも観測 cache にも触れない。"""

    def __init__(self, reader, *, clock=event_log.now_iso):
        self._reader = reader
        self._clock = clock

    def respond(self, target):
        """1 request ぶんの応答を組む (query string は route の判定に使わない)。"""
        path = urlsplit(target).path
        if path == "/":
            return Response(200, "text/html; charset=utf-8", PAGE_PATH.read_bytes())
        if path == "/api/overview":
            return _json_response(200, {"as_of": self._clock(), **self._reader.overview()})
        return _json_response(404, {"error": f"未知の経路: {path}"})


def _json_response(status, payload):
    return Response(
        status,
        "application/json; charset=utf-8",
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


class Handler(BaseHTTPRequestHandler):
    """`do_GET` だけを持つ handler。**書き込み method を 1 つも実装しない**のが read-only の 2 段目。

    `api` は `start` が bind した subclass 属性 (handler は request ごとに作られるので、
    instance へ渡す口が無い)。
    """

    protocol_version = "HTTP/1.1"
    server_version = "dispatch-v2-dashboard"

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler の契約)
        response = self.api.respond(self.path)
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        # 画面は polling で読み直す前提なので、browser にも中間にも溜めさせない
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(response.body)

    def log_message(self, format, *args):  # noqa: A002 (BaseHTTPRequestHandler の契約)
        """既定の stderr への 1 行 log を殺す (5 秒 polling で daemon の log が埋まる)。"""


class Dashboard:
    """bind の結果。**成功しても失敗しても同じ形で答える** (失敗を沈黙させないため)。"""

    def __init__(self, server, *, port, reason=None):
        self.server = server
        self._port = port
        self._reason = reason
        self._thread = None

    def facts(self):
        """`/health` に載せる事実。bind できていなければ `url` は無く `reason` が埋まる。"""
        bound = self.server is not None
        return {
            "bound": bound,
            "port": self._port,
            "url": f"http://{DASHBOARD_HOST}:{self._port}/" if bound else None,
            "reason": self._reason,
        }

    def serve_in_background(self):
        """serve ループを別スレッドで回す。**daemon の serve ループとは席を分ける**。"""
        if self.server is None:
            return
        self._thread = threading.Thread(
            target=self.server.serve_forever, name="dispatch-v2-dashboard", daemon=True
        )
        self._thread.start()

    def stop(self):
        """bind した socket を手放す。**serve していなければ `shutdown()` を呼ばない**。

        `BaseServer.shutdown()` は serve ループが終わったことを示す event を待つが、その event
        を立てるのは `serve_forever` の finally なので、**serve を始めていない server に対して
        呼ぶと永久に返らない**。bind の直後に組み立てが失敗した経路 (`daemon.run`) がここを
        通るので、その場合は close だけで手放す。
        """
        if self.server is None:
            return
        if self._thread is not None:
            self.server.shutdown()
            self._thread.join(timeout=THREAD_JOIN_TIMEOUT_SEC)
            if self._thread.is_alive():
                # socket は下の `server_close()` で必ず手放すので port は塞がらないが、
                # **返らない serve thread は daemon の停止を遅らせる**ので黙らせない
                print(
                    f"[dispatch-v2] dashboard の serve thread が "
                    f"{THREAD_JOIN_TIMEOUT_SEC} 秒で終わらない (port {self._port})",
                    file=sys.stderr,
                )
            self._thread = None
        self.server.server_close()


def unavailable(reason, *, port=None):
    """bind を試さずに「使えない」を表す Dashboard (設定が読めなかったとき)。"""
    return Dashboard(None, port=port, reason=reason)


def start(view_path, *, port, clock=event_log.now_iso, server_factory=ThreadingHTTPServer):
    """view を読む dashboard を bind する。**bind に失敗しても例外を投げない**。

    `server_factory` を差し替えられるのは、**socket を掴まずに bind 先と handler を検証する**
    ため (`principle-test-double-boundary`: socket は unmanaged dependency)。
    """
    api = DashboardApi(view_read.ViewReader(view_path), clock=clock)
    # handler は request ごとに作られるので、api は subclass の属性として束ねる
    handler = type("BoundHandler", (Handler,), {"api": api})
    try:
        server = server_factory((DASHBOARD_HOST, port), handler)
    except OSError as exc:
        reason = f"{DASHBOARD_HOST}:{port} に bind できない: {exc}"
        print(f"[dispatch-v2] dashboard を開けない: {reason}", file=sys.stderr)
        return unavailable(reason, port=port)
    return Dashboard(server, port=port)


def start_from_environ(view_path, *, environ=os.environ, clock=event_log.now_iso):
    """環境の設定を読んで dashboard を起こす (daemon の組み立て口)。

    設定が読めないことと bind できないことを**同じ形の Dashboard へ畳む** — 呼び出し側
    (daemon) はどちらでも「本務は続け、理由を `/health` に載せる」しかしないため。
    """
    try:
        port = configured_port(environ)
    except DashboardError as exc:
        print(f"[dispatch-v2] dashboard を開けない: {exc}", file=sys.stderr)
        return unavailable(str(exc))
    return start(view_path, port=port, clock=clock)
