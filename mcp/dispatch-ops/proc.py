"""外部コマンドの起動境界 (server 唯一の subprocess 呼び出し地点)。

**timeout を持たない runner を作れない形にしてある** のが本 module の要点。起動元ごとに
待ち時間の上限と例外クラスを束ねた callable を `command_runner` で作り、呼び出し側は argv
だけを渡す。timeout 無しの起動が 1 本でも残ると、外部コマンドの hang が server プロセス
全体の停止になり、E2BIG 発症下で唯一の in-session 回収経路 (ADR 0028) を失う。

timeout と起動失敗はどちらも起動元の例外クラスへ包み直す。`subprocess` 由来の例外を
そのまま外へ出すと、tool 層が「この server の失敗」として扱えない。
"""

import subprocess


def command_runner(*, error, timeout_sec):
    """`argv → (returncode, stdout, stderr)` の callable を作る。

    Args:
        error: 起動失敗と timeout を包む例外クラス (起動元の domain error)
        timeout_sec: 1 回の起動に許す上限秒

    返る callable は stdout と stderr を分離したまま返す。**非 0 exit は失敗にしない** —
    exit code を読んで判断するのは起動元 (`git status --porcelain` のように非 0 が
    正常な情報である呼び出しが混ざる)。
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
