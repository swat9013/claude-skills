"""外部コマンド (tracker CLI) の起動境界。

v1 (`mcp/dispatch-ops/proc.py`) の `command_runner` を copy した — v1 とコードを共有しないのは
併走中の結合を作らないため (issue #850 の方針)。**待たない起動 (`spawn_detached`) は持ってこない**:
v2 で detach するのは daemon の lazy 起動だけで、それは `client._spawn_daemon` が既に自前で持つ。

**待つ起動が timeout を持たない runner を作れない形にしてある**のが要点。timeout 無しの待つ
起動が 1 本でも残ると、CLI の hang が台帳の門 (`http_app.LedgerGate`) を握ったままになり、
`/health` 以外の全 tool が 503 で落ち続ける。**締切は CLI 1 回を切れない**ので、tick 側の
締切で詰められるのは単位の切れ目までで、1 回ぶんを抑えるのはここしかない。

起動そのものは `run_bounded` 1 本に集める (gh#967)。tracker CLI も git も**孫を持つ**ので、
上限を過ぎたときに倒す相手は直の子ではなく process group — その規則を経路ごとに書くと、
片方だけが孫を置き去りにする形が残る。
"""

import contextlib
import os
import signal
import subprocess
import threading

#: 走っている外部コマンドの process group leader (= 子の pid)。**daemon の停止で倒す**ために
#: 持つ (`terminate_running`)。プロセスに 1 つの表なのは、倒す相手が OS のプロセスだから
_running_groups = set()
_running_groups_lock = threading.Lock()


class CommandTimedOut(RuntimeError):
    """上限を過ぎたので子孫ごと倒した。**起動失敗と分けてある** — 待った相手が答えなかった。"""


def run_bounded(argv, *, timeout_sec):
    """外部コマンドを 1 回撃つ。上限を過ぎたら**子孫ごと**倒して `CommandTimedOut`。

    返り値は `subprocess.CompletedProcess`。`subprocess.run(timeout=...)` を使わないのは、
    あちらが倒すのが**直の子だけ**だから — `git pull` は `git fetch` を子に持つので、
    remote が無反応な間ずっと孫が残り、周期処理のたびに 1 つずつ積み上がる (gh#967 で
    daemon の子孫として観測された `git-core/git fetch`)。

    子を新しい session (= process group) で起こしておき、超過したら group ごと SIGKILL する。
    """
    with subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as running:
        _remember(running.pid)
        try:
            stdout, stderr = running.communicate(timeout=timeout_sec)
        except subprocess.TimeoutExpired as exc:
            _kill_group(running)
            raise CommandTimedOut(
                f"{' '.join(argv)} が {timeout_sec} 秒で終わらない (応答しない相手を待っている)"
            ) from exc
        except BaseException:
            # 停止合図などで巻き戻すときも、起こした子孫を置き去りにしない
            _kill_group(running)
            raise
        finally:
            _forget(running.pid)
    return subprocess.CompletedProcess(argv, running.returncode, stdout, stderr)


def terminate_running():
    """まだ走っている外部コマンドを子孫ごと倒す。返り値は `{"killed", "unreachable"}`。

    停止の合図 (SIGTERM) が届くのは main thread だけで、周期処理スレッドは外部コマンドを
    待ったまま止まらない。`start_new_session` で別 session に居るので、daemon が消えても孫は
    誰にも回収されない — **置き去りにしないための最後の 1 回** (`principle-idempotent-operations`
    の「起動時に残骸を人手で片付ける前提を作らない」)。

    **撃てた数と撃てなかった相手を分けて返す**。既に居ない group は正常なので数えないが、
    権限で撃てない group は置き去りが確定するので pid を名乗る — これを潰すと、残った孫が
    誰にも見えないまま残る (`principle-fail-loudly`)。
    """
    with _running_groups_lock:
        leaders = tuple(_running_groups)
    killed = 0
    unreachable = []
    for leader in leaders:
        try:
            os.killpg(leader, signal.SIGKILL)
        except ProcessLookupError:
            continue  # 既に居ない (倒す相手が居なかっただけ)
        except PermissionError:
            unreachable.append(leader)
        else:
            killed += 1
    return {"killed": killed, "unreachable": unreachable}


def _remember(leader):
    with _running_groups_lock:
        _running_groups.add(leader)


def _forget(leader):
    with _running_groups_lock:
        _running_groups.discard(leader)


def _kill_group(running):
    """起こした子とその子孫をまとめて倒す。既に居なければ何もしない。

    子は `start_new_session` で group leader になっているので、**この 1 発に直の子も含まれる**
    (`Popen.kill` を重ねて撃つ必要は無い)。

    倒せなかった場合も例外を出さない — ここから `OSError` を出すと、呼び出し側の
    `except OSError` に拾われて **timeout が「起動できない」に化ける**。timeout は
    `CommandTimedOut` として出し切る (倒し損ねは `terminate_running` が停止時に名乗る)。
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(running.pid, signal.SIGKILL)


def command_runner(*, error, timeout_sec):
    """`argv → (returncode, stdout, stderr)` の callable を作る。

    Args:
        error: 起動失敗と timeout を包む例外クラス (起動元の domain error)
        timeout_sec: 1 回の起動に許す上限秒

    **非 0 exit は失敗にしない** — exit code を読んで判断するのは起動元 (`gh` の非 0 が
    「remote 未設定」のような正常な観測であることがある)。
    """
    if timeout_sec <= 0:
        raise ValueError(f"timeout_sec は 1 以上で渡す (受け取った値: {timeout_sec})")

    def run(argv):
        try:
            completed = run_bounded(argv, timeout_sec=timeout_sec)
        except CommandTimedOut as exc:
            # **文面は `run_bounded` が組む**。ここで組み直すと argv と秒の描き方が経路ごとに
            # 分かれる
            raise error(str(exc)) from exc
        except OSError as exc:
            raise error(f"{argv[0]} を起動できない: {exc}") from exc
        return completed.returncode, completed.stdout, completed.stderr

    return run
