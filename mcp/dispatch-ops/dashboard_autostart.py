"""dashboard を server プロセスの起動時に立ち上げる (**LLM の介在を置かない**)。

dashboard は「orchestrator が動いていれば上がっている」ものにしたいが、その起動を
orchestrator の手順 (SKILL.md の step) に足すと、LLM が読み落とせば上がらない・毎回
context を食う・compaction をまたぐと忘れる、の 3 つを同時に抱える。そこで**起動主体を
server プロセスにする** — 判断も実行も機械側で完結し、skill 本文に step は増えない。

dispatch-ops server は **dispatch-ops を読む全 session で 1 つずつ起動する**ので、本 module は
何度呼ばれても同じ終状態 (`port` に listener が 1 つ) へ収束する形でなければならない:

```
  session A の server ─┐
  session B の server ─┼─► ensure_dashboard ─► port に listener が居る?
  session C の server ─┘                        ├ 居る  → 再利用 (起こさない・落とさない)
                                                └ 居ない → 起こす
```

**落とす経路を持たない。** 起動した dashboard は親の session が終わっても生き続ける
(`proc.spawn_detached` が新しい session へ置く)。orchestrator の再入・compaction・session
終了で画面が消えないことのほうが、余った常駐 1 つより価値が高い。

先に立っている listener が dashboard かどうかまでは確かめない。**この port を誰かが使って
いるなら、そこへ別プロセスを重ねない**が判定のすべてで、HTTP を喋らせて素性を問うと、
`port` を変えた利用者の環境で「別物が居るから起こす → bind に失敗する」を繰り返す。

競合 (2 つの server が同時に「居ない」と見る) は OS が解決する — bind に成功するのは 1 つ
だけで、負けた子は起動直後に終わる。終状態は同じなので lock は置かない
(`principle-isolate-shared-writes` の「ロックを既定の答えにしない」)。
"""

import os
import socket
import sys
from datetime import datetime
from pathlib import Path

import dashboard as dashboard_mod
import ledger as ledger_mod
import proc

# 起動する実体。**import している module 自身の path** を使う (別に組み立てると、install 形態や
# ディレクトリ移動で import 先と起動先がずれうる)
DASHBOARD_SCRIPT = Path(dashboard_mod.__file__).resolve()

# log の file 名。台帳 root 直下に置くのは、dashboard が project 横断の画面で特定 project の台帳
# ディレクトリに属さないから (台帳ディレクトリは `state.json` を持つものだけを
# `dashboard.ledger_directories` が拾うので、ここに file を足しても project には見えない)。
#
# **決定と子の出力を同じ file に混ぜない。** dashboard は request ごとに 1 行書き、画面は 5 秒
# ごとに polling するので、混ぜると 1 日で数万行になり、調査の起点である決定行 (session あたり
# 1 行) が末尾から押し出される
LOG_FILENAME = "dashboard-autostart.log"
CHILD_LOG_FILENAME = "dashboard.log"

# listener の有無を見るだけの接続に許す秒数。相手は同じホストの 127.0.0.1 なので、届くなら
# 即答する。**server の起動を待たせない上限**として置く
PROBE_TIMEOUT_SEC = 1.0

# 子プロセスへ渡さない環境変数。`pane_spawn` が worker へ注入する台帳 anchor は **project 1 つ**を
# 指すが、dashboard は台帳 root 配下の全 project を並べる画面。継がせると、どの session が port を
# 取ったかで宣言 (`dispatch-project.toml`) の解決先が変わる。台帳 root の差し替え
# (`ISSUE_DISPATCH_LEDGER_ROOT`) は環境全体の設定なので継ぐ — 落とすと server と別の root を読む
_UNINHERITED_ENV = (ledger_mod.LEDGER_DIR_ENV,)

# 待たない起動の継ぎ目 (テストはここを差し替えて argv と env を pin する)
spawn_detached = proc.spawn_detached


class DashboardLaunchError(RuntimeError):
    """dashboard の子プロセスを起こせない (`uv` が無い / log を開けない)。"""


def default_log_path():
    """決定と子プロセス出力の行き先 (台帳 root 直下)。

    root は**呼ぶたびに解く** — `ISSUE_DISPATCH_LEDGER_ROOT` を差し替えた環境で、子へ渡す
    root と log の落ち先が別々になるのを避ける (module 定数に畳むと import 時の値で固まる)。
    """
    return ledger_mod.default_ledger_root() / LOG_FILENAME


def ensure_dashboard(config):
    """dashboard が上がっている状態へ収束させる。決定を dict で返し、log へ 1 行残す。

    Args:
        config: `user_config.load_or_create` が返した検証済み設定

    log の落ち先も台帳の有無も**台帳 root 1 つから解く** (差し替えは `default_ledger_root` の
    env)。記録先だけを別に受け取れる形にすると、log を書いた場所と台帳を見た場所が食い違った
    状態をテストが作れてしまい、production には無い組み合わせを検査することになる。

    返り値の `action` は 5 つ。

    | action | 意味 |
    |---|---|
    | `disabled` | 宣言で切ってある |
    | `reused` | port に listener が居る |
    | `no_ledger` | 台帳ディレクトリが 1 つも無い (dashboard は起動直後に終わるので起こさない) |
    | `started` | 起こした |
    | `failed` | 起こせなかった / port を確かめられなかった |

    **`failed` を例外にしない。** dashboard は read-only の観測画面で、台帳・tracker・pane の
    操作には要らない。ここで例外を上げると、`uv` の不在や log の書けない環境が
    **dispatch-ops の全 tool の停止**になる — 画面が出ない代償としては高すぎる。代わりに
    理由を log へ残す (捨てると「起こさなかった」と「起こしたが即死した」を区別できない)。

    起こした子が上がりきったかは**確かめない**。bind に負けた子も台帳を読めなかった子も
    `started` として残るので、`started` は「起動を投げた」であって「画面が出ている」ではない
    (出ているかは port を見る)。待って確かめると、server の起動が子の初期化に縛られる。
    """
    target = default_log_path()
    settings = config["dashboard"]
    port = settings["port"]
    if not settings["autostart"]:
        return _record(target, action="disabled", port=port, detail="autostart = false")
    try:
        occupied = has_listener(port)
    except OSError as exc:
        # 空きか使用中かを決められない。起こすと二重起動、起こさないと画面が出ない —
        # どちらも憶測なので、確かめられなかった事実のほうを残す
        return _record(target, action="failed", port=port, detail=f"port を確かめられない: {exc}")
    if occupied:
        return _record(target, action="reused", port=port, detail="port に listener が居る")
    if not _ledger_exists():
        return _record(
            target,
            action="no_ledger",
            port=port,
            detail="台帳ディレクトリが 1 つも無い (dispatch を 1 度も記帳していない)",
        )
    argv = ["uv", "run", "--script", str(DASHBOARD_SCRIPT), "--port", str(port)]
    try:
        pid = spawn_detached(
            argv,
            env=_child_env(),
            log_path=target.with_name(CHILD_LOG_FILENAME),
            error=DashboardLaunchError,
        )
    except DashboardLaunchError as exc:
        return _record(target, action="failed", port=port, detail=str(exc))
    return _record(target, action="started", port=port, detail=" ".join(argv), pid=pid)


def format_decision(decision):
    """決定を 1 行にする。**描き方を知るのは本 module だけ** にする。

    log と server の起動 log が同じ形で読めるようにするのが目的。呼び出し側が dict の key を
    展開して整形すると、key を 1 つ増やすたびに 2 file を直すことになる。

    `pid` は起こしたときにしか無いので、無いときは欄ごと落とす — `pid=None` を毎行書くと、
    起こしていない行まで失敗に見える。
    """
    launched = f" pid={decision['pid']}" if decision["pid"] is not None else ""
    return f"{decision['action']} port={decision['port']}{launched} {decision['detail']}"


def _ledger_exists():
    """台帳ディレクトリが 1 つでもあるか。

    無い環境で起こすと、子は「台帳が 1 つも無い」を出して即座に終わる。**全 session が同じ
    空振りを繰り返し**、その出力が log を埋めて「画面が出ないとき」の調査先を潰す。判定は
    dashboard 側の列挙をそのまま使う — 「台帳ディレクトリとは何か」(`state.json` を持つ dir)
    を 2 箇所に持つと、片方だけが規則を変えたときに空振りが黙って戻る。

    列挙できない (root を読めない) ときは起こす側へ倒す — 子は自分が読めなかった理由を
    書ける。ただし**その log の置き場も同じ root の下**なので、ここで読めなかった事実は
    stderr へ出す (握ると、どの層で止まったかがどこにも残らない)。
    """
    try:
        return bool(dashboard_mod.ledger_directories(ledger_mod.default_ledger_root()))
    except OSError as exc:
        print(f"dashboard autostart: 台帳 root を読めない: {exc}", file=sys.stderr)
        return True


def has_listener(port):
    """`port` に誰かが listen しているか。**bind ではなく接続で見る**。

    public なのは、`ensure_dashboard` の判断を **port の実状態に依らせずに**検査するため。
    port は out-of-process の共有資源で、bind を許さない環境がある。

    bind で空きを確かめると、確かめるために掴んだ socket を離す隙間ができ、そこへ別の
    プロセスが入る。接続なら状態を変えずに見られる。host は dashboard が bind する先と
    同じ正本 (`dashboard.BIND_HOST`) を使う — `localhost` は環境によって `::1` へ解決し、
    IPv4 で待っている dashboard を「居ない」と読む。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(PROBE_TIMEOUT_SEC)
        return probe.connect_ex((dashboard_mod.BIND_HOST, port)) == 0


def _child_env():
    """子へ渡す環境変数 (自プロセスの環境から `_UNINHERITED_ENV` を落としたもの)。"""
    return {key: value for key, value in os.environ.items() if key not in _UNINHERITED_ENV}


def _record(log_path, *, action, port, detail, pid=None):
    """決定を log へ追記して返す。**追記できなくても決定は返す**。

    log を書けないことは dashboard の起動可否と関係が無い。ここで落とすと、log の置き場が
    無い環境で server ごと止まる。書けなかった事実は stderr へ出す (server の起動 log に残る)。
    """
    decision = {"action": action, "port": port, "detail": detail, "pid": pid}
    line = f"{datetime.now().astimezone().isoformat()} {format_decision(decision)}\n"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as sink:
            sink.write(line)
    except OSError as exc:
        print(f"dashboard autostart: {log_path} へ記録できない: {exc}", file=sys.stderr)
    return decision
