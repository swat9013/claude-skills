"""稼働 clone への ff-only deploy (ADR 0047 の後継、設計 §7 の built-in 例外)。

**機械が自発的に外部へ及ぼしてよい唯一の作用**。冪等で前進のみ (`--ff-only`) なので
「副作用の可逆性」の線の内側にある。既定 on で、project の宣言 config で off にできる。

ADR 0047 との違いは実行者だけ: あちらは observer が「導出なしに正しい場所に居る」ことを
根拠に選ばれていたが、v2 の daemon はマシンに 1 プロセスで複数 project を抱えるのでその
性質が無い。**稼働 clone の path は MCP server が cwd から解決して渡した観測値**を使う。

沈黙してよいのは成功だけ。ADR 0047 が挙げる 4 事象 (dirty / ff 不可 / 一過性の失敗 /
恒久的な不成立) はすべて escalation に載る。

- `git pull --ff-only` を引数なしで撃つ (現在の checkout の upstream 設定に従わせ、既定
  ブランチの綴りを持ち込まない)
- fetch を前置しない (pull は fetch を内包する。足すと同じ通信を 2 度払う)
"""

from dispatch_v2 import project

# 恒久的な不成立。**再試行では直らない**ので、ack されるまで同じ dedup key で 1 通だけ立つ
# (ADR 0047 の「最初の 1 度だけ escalation し、以後スキップ」を escalation の ack で表す)
STATUS_INERT = "inert"
# pull は通ったが working tree が汚れている。`git pull` が sandbox の除外に無い環境で
# 自己改変保護が半適用になる (HEAD 据え置き + working tree だけ書き換わる) 経路がある
STATUS_DIRTY = "dirty"
# ff で進めない (分岐した / 手元にコミットがある)
STATUS_FF_BLOCKED = "ff_blocked"
# 一過性の失敗 (network 断・衝突・その他)
STATUS_FAILED = "failed"
# 稼働 clone をまだ観測していない (どこを pull すればよいか分からない)
STATUS_UNKNOWN_CLONE = "unknown_clone"
# 取り込めた (沈黙してよい唯一の結果)
STATUS_DEPLOYED = "deployed"

# escalation を立てる結果。**pull を撃った上での失敗はすべて**上げる — 「escalation が
# 来ない」を「pull が成功している」と読めるようにするため (ADR 0047「沈黙を許すのは成功だけ」)。
#
# `unknown_clone` は含めない。稼働 clone は WorkOrder を切った時点で観測されるので、これが
# 出るのは**その project から一度も dispatch していない**ときだけ — 取り込む先が無い状態で、
# 反映されない変更も存在しない。ここを上げると、台帳を作っただけの project が毎回 inbox を
# 汚す (実測: daemon を起こす既存テストの inbox が汚れた)
DEGRADED_STATUSES = (STATUS_INERT, STATUS_DIRTY, STATUS_FF_BLOCKED, STATUS_FAILED)

# **再試行では直らない**結果。ADR 0047 が「最初の 1 度だけ escalation し、以後スキップ」に
# 分類したもので、残り (ff 不能 / network 断 / dirty) は再発したら改めて上げる側
PERMANENT_STATUSES = (STATUS_INERT,)

# ff で進めなかったことを表す git の出力。**ここだけ綴りに依存する** — 恒久的な不成立
# (repo でない / detached / upstream 未設定) は下の pre-check が exit code で決めるので、
# 綴りが変わっても「一過性の失敗」に倒れるだけで、恒久を見逃す側には落ちない
FF_BLOCKED_MARKERS = ("not possible to fast-forward", "diverging", "diverged")

# escalation の evidence に載せる git 出力の長さ。診断に足りる程度に留める
DETAIL_LENGTH = 400


def run_deploy(run_git, clone_path):
    """稼働 clone を 1 回 pull して結果を返す。返り値は `{"status", "detail"}`。

    例外を投げないのは、**deploy の失敗が tick 全体を止める理由にならない**から。結果は
    呼び出し側 (rule catalog) が escalation に写す。
    """
    inert = _permanent_obstacle(run_git, clone_path)
    if inert is not None:
        return _outcome(STATUS_INERT, inert)
    pull = _attempt(run_git, ["pull", "--ff-only"], clone_path)
    if pull["failed"]:
        return _outcome(_classify_failure(pull["detail"]), pull["detail"])
    status = _attempt(run_git, ["status", "--porcelain"], clone_path)
    if status["failed"]:
        return _outcome(STATUS_FAILED, f"pull 後の status が読めない: {status['detail']}")
    if status["detail"].strip():
        return _outcome(STATUS_DIRTY, status["detail"])
    return _outcome(STATUS_DEPLOYED, "")


def unknown_clone():
    """稼働 clone をまだ観測していない project の結果 (pull は撃たない)。

    **結果の組み立てはこの module 1 箇所**に閉じる — 呼び出し側で dict を手組みすると、
    detail の切り詰めのような共通の作法がその経路だけ効かなくなる。
    """
    return _outcome(
        STATUS_UNKNOWN_CLONE, "稼働 clone をまだ観測していない (この project から dispatch していない)"
    )


def _permanent_obstacle(run_git, clone_path):
    """再試行では直らない不成立を **exit code で** 判定する。無ければ None。

    3 つとも git が専用の照会を持っているので、pull の人間向けメッセージを綴りで読まない
    (文言は git の版と locale で変わる)。
    """
    if _attempt(run_git, ["rev-parse", "--is-inside-work-tree"], clone_path)["failed"]:
        return f"git repo として読めない: {clone_path}"
    if _attempt(run_git, ["symbolic-ref", "-q", "HEAD"], clone_path)["failed"]:
        return "detached HEAD (追従する branch が無い)"
    if _attempt(run_git, ["rev-parse", "--abbrev-ref", "@{u}"], clone_path)["failed"]:
        return "upstream が未設定 (追従先が無い)"
    return None


def _classify_failure(detail):
    if any(marker in detail.lower() for marker in FF_BLOCKED_MARKERS):
        return STATUS_FF_BLOCKED
    return STATUS_FAILED


def _attempt(run_git, args, clone_path):
    """git を 1 回撃つ。**git 由来の失敗だけ**を返り値で表す (呼び出し側が分類する)。

    広く捕まえないのは、`run_git` の呼び出し規約違反 (TypeError 等) まで「一過性の失敗」に
    化けさせないため — それは 60 秒ごとに再試行してよい失敗ではない。
    """
    try:
        return {"failed": False, "detail": run_git(args, clone_path)}
    except project.ProjectError as exc:
        return {"failed": True, "detail": str(exc)}


def _outcome(status, detail):
    return {"status": status, "detail": detail[:DETAIL_LENGTH]}
