"""project の識別 (project key) と台帳ディレクトリの配置。

reconciler daemon は **マシンに 1 プロセスで複数 project を抱える** (ADR 0056) ので、
台帳は `<root>/<project key>/events.jsonl` に分かれる。root を 1 つ決めれば daemon 1 つと
台帳の集合が決まり、root を変えれば別の daemon になる (テストが hermetic になる継ぎ目)。

project key の導出は v1 (`mcp/dispatch-ops/repo_key.py`) と同じ規則を copy した — 同じ repo が
v1 / v2 で同じ key に落ちると、切替時に台帳を目で対応付けられる。v1 とコードは共有しない
(併走中の結合を作らない)。

**v2 の台帳 root は v1 と別ディレクトリ** (`~/.claude/dispatch-v2`)。設計
`docs/design/dispatch-v2/system.md`「10. 移行方針」の「v2 は v1 の隣に新設し、台帳
ディレクトリを分ける」に対応する。

issue 置き場 / CL 置き場の宣言 config は `declaration` module が持つ (台帳ディレクトリ直下の
`dispatch-project.toml`)。本 module が持つのは **identity と配置**だけで、置き場の識別子は
持たない — 台帳の在り処と観測対象は別の軸で、混ぜると片方の変更がもう片方を巻き込む。
"""

import hashlib
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from dispatch_v2 import proc

ROOT_ENV = "DISPATCH_V2_ROOT"
DEFAULT_ROOT = Path.home() / ".claude" / "dispatch-v2"

EVENTS_FILENAME = "events.jsonl"

# `sockaddr_un.sun_path` の実効上限 (macOS 104 / Linux 108)。余白を見て 100 で切る
MAX_SOCKET_PATH_BYTES = 100

# git metadata の照会 (rev-parse / remote get-url) は network を待たないので、本来は秒で返る。
# 15 は「返らない」を打ち切るための上限であって期待値ではない — cold な NFS / 巨大 repo で
# 数秒かかる実測があるため余裕を取る。ここが固まると台帳を開けず全 tool が止まるので、
# **tracker CLI と同じ 15 秒に揃える** (`gh_adapter.SUBPROCESS_TIMEOUT_SEC`)。60 秒のままだと
# client の応答待ち (20 秒) を 1 回で超え、健康な daemon が「居ない」と読まれる (gh#956)
GIT_METADATA_TIMEOUT_SEC = 15

# 作業ツリーの生成・回収 (`worktree add` / `worktree remove` / `clone` / `push`) に許す上限。
# **ツリー生成を待つので metadata と同じ枠には収まらない** — ここを 15 秒へ縮めると
# 正常な spawn が落ちる。長いことは前提にしたうえで、その間も daemon が答えられるように
# `/health` を台帳の門の外へ出してある (`http_app.LedgerGate`)。これらを撃つのは
# **request 経路** (人が起こした 1 回) なので、長い枠が周期で繰り返し借りられることはない
GIT_WORKSPACE_TIMEOUT_SEC = 60

# remote の応答を待つ git (`pull` / `fetch`) に許す上限。**周期処理が周期で必ず撃つ**ので、
# 枠は tick 全体の締切 (`reconciler.TICK_BUDGET_SEC`) に収まる長さにする (gh#967)。
# どの枠も門の内側で走る点は同じで、違うのは「その窓がどれだけ続くか」。
#
# 到達不能な remote は SSH の `ConnectTimeout` でも切れない (TCP / DNS の段で無反応になる)
# ことが実測されているので、**上限はこちら側で決めるしかない**。正常な `pull --ff-only` は
# 数秒で終わるため、ここを超えたら「remote が応答していない」を意味し、deploy は縮退として
# escalation に載る (`deploy.STATUS_FAILED`)
GIT_REMOTE_TIMEOUT_SEC = 10

#: metadata より長い枠を借りてよい git。**先頭 2 語まで見て決める** — 同じ `worktree` でも
#: `list` は metadata で `add` / `remove` はツリー操作なので、動詞まで読まないと分かれない。
#:
#: **枠を決めるのは呼び出し元ではなくコマンド**。呼び出し元 (`git_worktrees` / `deploy`) は
#: 長い git と metadata の git を同じ runner で混ぜて撃つので、注入する側で枠を選ぶと
#: `status --porcelain` や `worktree list` まで 60 秒枠を借りることになる (gh#956 のレビュー
#: 指摘)。
#:
#: **2 つの表に分けてあるのは「何が撃つか」が違うから** (gh#967): remote 系は周期処理が
#: 周期で必ず撃ち、workspace 系は request 経路で人が起こした 1 回だけ撃つ。ここに無い git は
#: metadata 扱いで、追加は「remote を待つか / ツリーを触るか」で振り分ける
REMOTE_GIT_COMMANDS = frozenset({("fetch",), ("pull",)})
WORKSPACE_GIT_COMMANDS = frozenset(
    {("clone",), ("push",), ("worktree", "add"), ("worktree", "remove")}
)

# project key に許す文字。ディレクトリ名として安全で、`github.com` の `.` や `swat-skills` の
# `-` を潰さずに読めるままにする。**HTTP の path segment にもなる**ので、この集合が
# `..` のような traversal を構造で閉める
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
PROJECT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

# scp 形式の remote (`git@github.com:swat9013/swat-skills.git`) は URL scheme を持たない
_SCP_LIKE = re.compile(r"^(?:(?P<user>[^@/]+)@)?(?P<host>[^:/]+):(?P<path>.+)$")

# remote を持たない repo の project key 接頭辞 (remote 由来 key と衝突しないことを目で確かめる)
PATH_KEY_PREFIX = "path__"


class ProjectError(RuntimeError):
    """project key を導出できない / 受け取った key が使えない。"""


class GitCommandFailed(ProjectError):
    """git が非 0 exit で返した。

    **起動失敗・timeout と分けてあるのは、非 0 exit だけが fallback してよい失敗だから** —
    `remote get-url origin` の非 0 は「remote 未設定」という正常な観測なので path 由来 key へ
    倒す。git が固まった / 起動できなかったときに同じ fallback へ落とすと、remote を持つ repo が
    黙って別の台帳を書く。
    """


def git_budget_sec(args):
    """その git に許す上限秒。**枠はコマンドが決める** (`WORKSPACE_GIT_COMMANDS`)。

    照合は先頭からの位置一致なので、**global option を前置した形は判定できない**
    (`["-c", "x=y", "fetch"]` は `fetch` を見つけられない)。黙って metadata 枠へ落とすと、
    長い git が設定ミスではなく timeout として現れるので、判定できない形は loud に断る
    (`principle-fail-loudly`)。現状どの呼び出しも動詞から始めるので、これは将来の追加に対する
    歯止めであって既存経路の分岐ではない。
    """
    if args and str(args[0]).startswith("-"):
        raise ProjectError(
            f"git の枠を判定できない (global option が前置されている): {' '.join(args)}"
        )
    for length in (2, 1):
        head = tuple(args[:length])
        if head in REMOTE_GIT_COMMANDS:
            return GIT_REMOTE_TIMEOUT_SEC
        if head in WORKSPACE_GIT_COMMANDS:
            return GIT_WORKSPACE_TIMEOUT_SEC
    return GIT_METADATA_TIMEOUT_SEC


def run_git(args, cwd):
    """git を起動して stdout を返す。非 0 exit は GitCommandFailed。

    上限秒は撃つコマンドから決める — **注入する側で選ばせない**。`git_worktrees` / `deploy` は
    長い git (`worktree add` / `pull`) と metadata の git (`worktree list` / `status --porcelain`)
    を同じ runner で混ぜて撃つので、注入点で枠を選ぶと metadata まで長い枠を借りる (gh#956)。

    起動そのものは `proc.run_bounded` が持つ (上限を過ぎたら子孫ごと倒す — gh#967)。
    """
    try:
        completed = proc.run_bounded(
            ["git", "-C", str(cwd), *args], timeout_sec=git_budget_sec(args)
        )
    except proc.CommandTimedOut as exc:
        # 文面は `run_bounded` が組む (argv も上限秒も既に入っている)
        raise ProjectError(str(exc)) from exc
    except OSError as exc:
        raise ProjectError(f"git {' '.join(args)} を起動できない: {exc}") from exc
    if completed.returncode != 0:
        raise GitCommandFailed(
            f"git {' '.join(args)} が失敗 (exit {completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def default_root():
    """台帳 root を返す (環境変数 > 既定)。"""
    override = os.environ.get(ROOT_ENV)
    return Path(override).expanduser() if override else DEFAULT_ROOT


def ledger_dir(root, project_key):
    """project の台帳ディレクトリ。"""
    return Path(root) / require_project_key(project_key)


def events_path(root, project_key):
    """project の events.jsonl。"""
    return ledger_dir(root, project_key) / EVENTS_FILENAME


def lock_path(root):
    """daemon の占有印。**保持者プロセスの生存 (flock) で判定する**ので、残っていても残骸。"""
    return Path(root) / "daemon.lock"


def view_path(root):
    """dashboard が読む SQLite の投影 (`materialized_view`)。

    **root 直下に 1 つ**。daemon はマシンに 1 プロセスで複数 project を抱えるので、投影も
    project ごとに分けず 1 file にまとめる (横断の一覧がそのまま 1 query になる)。正本では
    ないので、消えても次の投影で作り直される。
    """
    return Path(root) / "view.sqlite3"


def daemon_log_path(root):
    """lazy 起動した daemon の stdout / stderr の落ち先 (起動失敗の診断に読む)。"""
    return Path(root) / "daemon.log"


def socket_path(root):
    """daemon が listen する UDS の path。

    root 配下ではなく tmp 直下に置き、**root の hash で名前を分ける**。理由は 2 つ:

    - `sun_path` は 104 byte 程度の上限があり、root が深いと bind が失敗する。pytest の
      `tmp_path` は容易に 80 文字を超える
    - hash で分けることで root ごとに別の daemon になる = テストが hermetic になる

    上限を超える組み立てになったら **loud に落とす** (長い TMPDIR の環境で沈黙して壊れない)。
    """
    digest = hashlib.sha256(str(Path(root).expanduser().resolve()).encode("utf-8")).hexdigest()
    path = Path(tempfile.gettempdir()) / f"dispatch-v2-{digest[:8]}.sock"
    if len(str(path).encode("utf-8")) > MAX_SOCKET_PATH_BYTES:
        raise ProjectError(
            f"UDS の path が長すぎる ({len(str(path))} byte > {MAX_SOCKET_PATH_BYTES}): {path} "
            "(TMPDIR を短い path に設定する)"
        )
    return path


def require_project_key(project_key):
    """外から来た project key を検証して返す (HTTP path segment の境界検証)。

    `..` や `/` を含む key をそのまま path に連結すると root の外を書きうる。
    """
    if not isinstance(project_key, str) or not PROJECT_KEY_PATTERN.match(project_key):
        raise ProjectError(
            f"不正な project key: {project_key!r} (英数字・`.`・`_`・`-` のみ)"
        )
    if project_key in (".", ".."):
        raise ProjectError(f"不正な project key: {project_key!r}")
    return project_key


def main_worktree_root(cwd, run=run_git):
    """cwd (linked worktree の中でもよい) から main worktree の root を解決する。

    dispatcher (repo root) と worker (`.claude/worktrees/i<N>` の中) が同じ project key に
    収束しないと、両者が別の台帳を書く。
    """
    common_dir = Path(run(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd))
    return common_dir.parent if common_dir.name == ".git" else common_dir


def split_remote_url(url):
    """remote URL を (host, [path segment...]) に分解する。host を取れない形は None。"""
    if not url or not url.strip():
        return None
    url = url.strip()
    host = None
    path = None
    if "://" in url:
        parts = urlsplit(url)
        if parts.scheme == "file":
            return None
        host = parts.hostname  # userinfo と port を落とす
        path = parts.path
    else:
        matched = _SCP_LIKE.match(url)
        if matched is not None:
            host = matched.group("host")
            path = matched.group("path")
    if not host or not path:
        return None
    if path.endswith(".git"):
        path = path[: -len(".git")]
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return None
    return host.lower(), segments


def project_key_from_remote(url):
    """remote URL から project key を組み立てる。取れない形なら None。

    例: `git@github.com:swat9013/swat-skills.git` → `github.com__swat9013__swat-skills`
    """
    split = split_remote_url(url)
    if split is None:
        return None
    host, segments = split
    return "__".join(_sanitize(token) for token in (host, *segments))


def project_key_from_path(path):
    """main worktree の実パスから project key を組み立てる (remote 無し repo の fallback)。"""
    text = str(Path(path))
    return PATH_KEY_PREFIX + _sanitize(text.replace("/", "_").lstrip("_"))


def _sanitize(token):
    sanitized = _SAFE_CHARS.sub("_", token)
    if not sanitized:
        raise ProjectError(f"project key の要素が空になった: {token!r}")
    return sanitized


def derive_project_key(cwd=None, run=run_git):
    """cwd から project key を導出する。remote があれば remote 由来、無ければパス由来。"""
    cwd = Path(cwd) if cwd is not None else Path(os.getcwd())
    root = main_worktree_root(cwd, run=run)
    try:
        url = run(["remote", "get-url", "origin"], root)
    except GitCommandFailed:
        url = None  # remote 未設定は正常な fallback 経路 (エラーにしない)
    key = project_key_from_remote(url) if url else None
    return require_project_key(key or project_key_from_path(root))
