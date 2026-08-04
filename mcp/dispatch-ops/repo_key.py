"""台帳ディレクトリ名 (repo-key) の導出。

spec §3.1: `git remote get-url origin` の host + path を正規化した値を使い、remote を
持たない repo は main worktree 実パスの slug に fallback する。

**cwd が linked worktree の中でも main worktree root へ解決してから導出する** のが要点
(spec §3.1)。dispatcher (repo root) と pane (`.claude/worktrees/i<N>` の中) が同じ
repo-key に収束しないと、両者が別の台帳を書いて drift 検出が成立しない。
"""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import proc

# git metadata の照会 (rev-parse / remote get-url) だけを撃つので、network を待つ tracker CLI
# より短くてよい。ここが固まると台帳を開けず、server の全 tool が止まる
SUBPROCESS_TIMEOUT_SEC = 60

# repo-key に許す文字。ディレクトリ名として安全で、かつ `github.com` の `.` や
# `swat-skills` の `-` を潰さずに読めるままにする (spec §3.1 の例に合わせる)
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")

# scp 形式の remote (`git@github.com:swat9013/swat-skills.git`)。URL scheme を持たない
# ため urlsplit では host を取れず、専用の分解が要る
_SCP_LIKE = re.compile(r"^(?:(?P<user>[^@/]+)@)?(?P<host>[^:/]+):(?P<path>.+)$")

# remote を持たない repo の repo-key 接頭辞。remote 由来の key (host 始まり) と
# 衝突しないことを目で確かめられるようにする
PATH_KEY_PREFIX = "path__"


class RepoKeyError(RuntimeError):
    """repo-key を導出できない (git repo でない / git 呼び出しに失敗した)。"""


class GitCommandFailed(RepoKeyError):
    """git が非 0 exit で返した。

    **起動失敗・timeout と分けてあるのは、非 0 exit だけが fallback してよい失敗だから** —
    `remote get-url origin` の非 0 は「remote 未設定」という正常な観測なので path 由来 key へ
    倒す。一方 git が固まった / 起動できなかったときに同じ fallback へ落とすと、remote を持つ
    repo が黙って別の台帳 (path 由来 key) を書き、dispatcher と pane の drift 検出が壊れる。
    """


_run_git_command = proc.command_runner(error=RepoKeyError, timeout_sec=SUBPROCESS_TIMEOUT_SEC)


def run_git(args, cwd):
    """git を起動して stdout を返す。非 0 exit は GitCommandFailed。

    テストと #403 以降の adapter がここを差し替えられるよう、git 呼び出しを 1 関数に
    まとめてある (その下の起動そのものは `proc` が持つ)。
    """
    rc, out, err = _run_git_command(["git", "-C", str(cwd), *args])
    if rc != 0:
        raise GitCommandFailed(f"git {' '.join(args)} が失敗 (exit {rc}): {err.strip()}")
    return out.strip()


def main_worktree_root(cwd, run=run_git):
    """cwd (linked worktree の中でもよい) から main worktree の root を解決する。

    `--git-common-dir` は linked worktree からでも main 側の `.git` を指すので、
    その親が main worktree root になる。
    """
    common_dir = Path(run(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd))
    # 通常の repo は `<root>/.git`。bare repo は repo ディレクトリ自身が返る
    return common_dir.parent if common_dir.name == ".git" else common_dir


def split_remote_url(url):
    """remote URL を (host, [path segment...]) に分解する。

    host を取れない形 (ローカルパス remote / file://) は None を返し、呼び出し側の
    パス fallback へ倒す。
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None

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


def repo_key_from_remote(url):
    """remote URL から repo-key を組み立てる。取れない形なら None。

    例: `git@github.com:swat9013/swat-skills.git` → `github.com__swat9013__swat-skills`
    """
    split = split_remote_url(url)
    if split is None:
        return None
    host, segments = split
    return "__".join(_sanitize(token) for token in (host, *segments))


def repo_key_from_path(path):
    """main worktree の実パスから repo-key を組み立てる (remote 無し repo の fallback)。"""
    text = str(Path(path))
    return PATH_KEY_PREFIX + _sanitize(text.replace("/", "_").lstrip("_"))


def _sanitize(token):
    sanitized = _SAFE_CHARS.sub("_", token)
    if not sanitized:
        raise RepoKeyError(f"repo-key の要素が空になった: {token!r}")
    return sanitized


def derive_repo_key(cwd=None, run=run_git):
    """cwd から repo-key を導出する。remote があれば remote 由来、無ければパス由来。"""
    cwd = Path(cwd) if cwd is not None else Path(os.getcwd())
    root = main_worktree_root(cwd, run=run)
    try:
        url = run(["remote", "get-url", "origin"], root)
    except GitCommandFailed:
        url = None  # remote 未設定は正常な fallback 経路 (エラーにしない)
    key = repo_key_from_remote(url) if url else None
    return key or repo_key_from_path(root)
