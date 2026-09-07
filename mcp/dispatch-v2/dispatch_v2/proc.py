"""外部コマンド (tracker CLI) の起動境界。

v1 (`mcp/dispatch-ops/proc.py`) の `command_runner` を copy した — v1 とコードを共有しないのは
併走中の結合を作らないため (issue #850 の方針)。**待たない起動 (`spawn_detached`) は持ってこない**:
v2 で detach するのは daemon の lazy 起動だけで、それは `client._spawn_daemon` が既に自前で持つ。

**待つ起動が timeout を持たない runner を作れない形にしてある**のが要点。timeout 無しの待つ
起動が 1 本でも残ると、CLI の hang が daemon プロセス全体の停止になる — v2 の daemon は
単一スレッドで HTTP も serve するので、hang は全 tool の停止と同義になる。
"""

import subprocess


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
            completed = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout_sec, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise error(f"{argv[0]} が {timeout_sec} 秒で終わらない: {' '.join(argv)}") from exc
        except OSError as exc:
            raise error(f"{argv[0]} を起動できない: {exc}") from exc
        return completed.returncode, completed.stdout, completed.stderr

    return run
