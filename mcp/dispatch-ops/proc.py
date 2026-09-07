"""外部コマンドの起動境界 (server 唯一の subprocess 呼び出し地点)。

起動は 2 種類あり、**どちらも timeout の扱いが構造で決まっている**。

| 起動 | 作り方 | timeout | 返るもの |
|---|---|---|---|
| 待つ起動 (出力を読む) | `command_runner` | **必須** (runner に束ねる) | `(returncode, stdout, stderr)` |
| 待たない起動 (detach) | `spawn_detached` | **概念として無い** (待たないので hang しない) | 子の pid |

**待つ起動が timeout を持たない runner を作れない形にしてある** のが本 module の要点。
起動元ごとに待ち時間の上限と例外クラスを束ねた callable を `command_runner` で作り、
呼び出し側は argv だけを渡す。timeout 無しの**待つ**起動が 1 本でも残ると、外部コマンドの
hang が server プロセス全体の停止になり、E2BIG 発症下で唯一の in-session 回収経路
(ADR 0028) を失う。

**待たない起動はこの危険を持たない** — 親は `Popen` から即座に戻り、子の寿命に縛られない。
代わりに子は親より長生きするので、出力の行き先 (log file) を呼び出し側が必ず指定する形に
してある。捨てると、常駐に失敗した子の理由がどこにも残らない。

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


def spawn_detached(argv, *, env, log_path, error):
    """子を起こして**待たずに**返す (常駐プロセスの起動)。返るのは子の pid。

    Args:
        argv: 起動する argv
        env: 子へ渡す環境変数一式 (親の環境を継がせるかは呼び出し側が決める)
        log_path: 子の stdout / stderr の行き先。**追記**で開く
        error: 起動失敗を包む例外クラス (起動元の domain error)

    `start_new_session=True` で新しい session (プロセスグループ) に置く。**親の終了や親が
    受けたシグナルで子を巻き込まないため** — 起動した常駐プロセスが親 (server) の寿命で
    落ちるなら、それは常駐ではない。

    stdin は塞ぐ (`DEVNULL`)。親の stdin は stdio transport の MCP 通信そのもので、子に
    継がせると 2 つのプロセスが同じ入力を奪い合う。
    """
    # log を開けない失敗と起動そのものの失敗を**別の message で**返す。1 つの try に畳むと、
    # 書き出し先の問題が「コマンドを起動できない」として報告され、読み手が原因を argv 側に探す
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        sink = open(log_path, "a", encoding="utf-8")
    except OSError as exc:
        raise error(f"{log_path} を開けない: {exc}") from exc
    try:
        with sink:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
    except OSError as exc:
        raise error(f"{argv[0]} を起動できない: {exc}") from exc
    return process.pid
