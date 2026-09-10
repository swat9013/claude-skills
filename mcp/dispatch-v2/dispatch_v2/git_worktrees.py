"""git worktree の作用面 (作成・回収・観測・台帳外の検出)。

worktree は port にしていない (設計 決定 5 — 代替が現実的でないので volatility が低い)。
本 module は git CLI を直接叩くが、**起動は引数で受ける** (`run_git`) のでテストは実 repo を
作らずに済む。

3 つの安全規則を持つ:

- **既に在るものを黙って乗っ取らない**。目的の path に登録外のディレクトリが在ったら
  ADR 0048 の残骸なので loud に止める (回収は repo 側の手順)
- **別の WorkOrder が所有している path は使わない**。同じ issue の再着手で前の作業ツリーを
  引き継ぐと、所有者の付け替えが台帳に残らないまま起こる
- **削除は名指しの 1 回だけ**。走査して該当を全部消す経路を持たない (設計 Q8)
"""

import os
from pathlib import Path

from dispatch_v2 import project, worktree

# 作業ツリーの置き場と branch の綴り。v1 (`mcp/dispatch-ops/worktree.py`) と同じ規約を copy
# した — 同じ clone を v1 / v2 の両方が見る移行期間に、人が目で対応付けられるようにする
WORKTREES_SUBDIR = (".claude", "worktrees")
BRANCH_PREFIX = "worktree-"

# `.git` の状態から読む台帳外ディレクトリの種別 (ADR 0048 の判定表)。**報告の理由はこの表
# からしか作らない** — 綴りだけの新しい理由が報告に混ざると、読み手が種別で数えられなくなる
NO_GIT = "no-git"
STALE_GITDIR = "stale-gitdir"
UNVERIFIED = "unverified"
UNREGISTERED_IN_LEDGER = "unregistered"

ORPHAN_REASONS = {
    NO_GIT: "`.git` が無い (worktree remove がツリー削除の途中で失敗した残骸)",
    STALE_GITDIR: "`.git` が指す admin dir が無い (登録だけが先に消えた残骸)",
    UNVERIFIED: "`.git` を読めない (残骸かどうか判定できない — 回収対象に数えない)",
    UNREGISTERED_IN_LEDGER: "git には登録されているが台帳のどの WorkOrder も所有していない",
}


class WorktreeError(RuntimeError):
    """作業ツリーの作成・回収の前提が成立しない。"""


def worktrees_dir(clone_path):
    """dispatch が作業ツリーを置くディレクトリ。"""
    return Path(clone_path).joinpath(*WORKTREES_SUBDIR)


def worktree_path(clone_path, slug):
    """issue slug から作業ツリーの path を組み立てる。"""
    return worktrees_dir(clone_path) / slug


def branch_name(slug):
    """issue slug から作業ブランチ名を組み立てる。"""
    return f"{BRANCH_PREFIX}{slug}"


def ensure(run_git, *, clone_path, slug, recorded, foreign_paths):
    """WorkOrder の作業ツリーを用意する。既に所有していれば作らずそれを返す。

    `recorded` はその WorkOrder が台帳で所有している worktree (無ければ None)、
    `foreign_paths` は**他の** WorkOrder が所有している path の集合。

    返すのは `{"path", "branch", "created"}`。`created` が偽なら Session を跨いで同じツリーを
    引き継いだということ (設計 シナリオ 2)。
    """
    path = recorded["path"] if recorded else str(worktree_path(clone_path, slug))
    branch = (recorded or {}).get("branch") or branch_name(slug)
    registered_paths = {entry["path"] for entry in registered(run_git, clone_path=clone_path)}
    if recorded is not None and path in registered_paths:
        # 前の Session が使っていたツリーをそのまま引き継ぐ (worktree は WorkOrder 所有)
        return {"path": path, "branch": branch, "created": False}
    if recorded is None:
        _require_unclaimed(path, registered_paths, foreign_paths)
    elif Path(path).exists():
        # 記録はあるが git の登録が無い = ADR 0048 の残骸。作り直すと残骸の上に重ねてしまう
        raise WorktreeError(_ORPHAN_REFUSAL.format(path=path))
    _add(run_git, clone_path=clone_path, path=path, branch=branch)
    return {"path": path, "branch": branch, "created": True}


_ORPHAN_REFUSAL = (
    "{path} は git の登録に無いディレクトリとして残っている "
    "(ADR 0048 の残骸。回収は repo 側の手順で行う — dispatch は削除しない)"
)


def _require_unclaimed(path, registered_paths, foreign_paths):
    """まだ誰の物でもない path であることを確かめる (所有者の付け替えを黙って起こさない)。"""
    if path in foreign_paths:
        raise WorktreeError(
            f"{path} は別の WorkOrder が所有している "
            "(同じ issue の再着手でも前のツリーは引き継がない — 先に worktree_tidy で回収する)"
        )
    if path in registered_paths:
        raise WorktreeError(
            f"{path} は git に登録済みだが台帳のどの WorkOrder も所有していない "
            "(worktree_sweep が台帳外として報告する。採否は人が決める)"
        )
    if Path(path).exists():
        raise WorktreeError(_ORPHAN_REFUSAL.format(path=path))


def remove(run_git, *, clone_path, path):
    """作業ツリーを 1 件だけ回収する。既に無ければ何もしない (再実行で同じ終状態へ)。"""
    registered_paths = {entry["path"] for entry in registered(run_git, clone_path=clone_path)}
    if path not in registered_paths:
        return False
    _run(run_git, ["worktree", "remove", path], clone_path)
    return True


def sight(run_git, *, clone_path, path):
    """作業ツリーの現況 (在るか / git が登録しているか / 未コミットがあるか / lock)。

    **`git status` は登録されているツリーでしか撃たない** — `.git` を失ったディレクトリでは
    git が親 repo まで遡って答えるので、残骸が親の clean を借りて「回収してよい」に見える
    (ADR 0048 が `_classify_unregistered` で避けているのと同じ罠)。
    """
    entries = {entry["path"]: entry for entry in registered(run_git, clone_path=clone_path)}
    entry = entries.get(path)
    present = Path(path).is_dir()
    dirty = (
        bool(_run(run_git, ["status", "--porcelain"], path).strip())
        if present and entry is not None
        else False
    )
    return worktree.TreeSighting(
        path=path,
        present=present,
        registered=entry is not None,
        dirty=dirty,
        locked=bool(entry and entry["locked"]),
    )


def registered(run_git, *, clone_path):
    """`git worktree list --porcelain` の登録一覧 (path / branch / locked)。"""
    return _parse_worktree_list(_run(run_git, ["worktree", "list", "--porcelain"], clone_path))


def scan_orphans(clone_path, *, registered_entries, owned_paths, excluded_paths):
    """台帳外の作業ツリーを**報告する**(削除はしない — ADR 0048)。

    返すのは `{"orphan_worktrees", "unverified_worktrees"}`。**判定できなかったもの
    (`unverified`) を残骸と混ぜない** — 権限で `.git` を読めないだけの生きたツリーを
    回収対象として報告しないため (ADR 0048 の判定表)。

    残骸として数えるのは 2 系統:

    - git の登録に無いディレクトリ (`worktree remove` がツリー削除に失敗した残骸)
    - git には登録されているが、台帳のどの WorkOrder も所有していないツリー

    `excluded_paths` は別の報告が担当する path (非終端 WorkOrder の消失、rule #12) — 同じ path を
    2 つの報告に載せない。
    """
    directory = worktrees_dir(clone_path)
    registered_paths = {entry["path"] for entry in registered_entries}
    orphans = [
        _finding(entry["path"], UNREGISTERED_IN_LEDGER, branch=entry["branch"])
        for entry in registered_entries
        if entry["path"] not in owned_paths and _is_under(entry["path"], directory)
    ]
    unverified = []
    for child in _child_dirs(directory):
        if str(child) in registered_paths or str(child) in excluded_paths:
            continue
        reason = _classify_unregistered(child)
        if reason == UNVERIFIED:
            unverified.append(_finding(str(child), reason))
        elif reason is not None:
            orphans.append(_finding(str(child), reason))
    return {
        "orphan_worktrees": sorted(orphans, key=lambda finding: finding["path"]),
        "unverified_worktrees": sorted(unverified, key=lambda finding: finding["path"]),
    }


def empty_scan():
    """走査していないときの結果 (欄の形は `scan_orphans` と同じ)。"""
    return {"orphan_worktrees": [], "unverified_worktrees": []}


def _finding(path, reason, *, branch=None):
    """報告 1 件。**理由の綴りと説明を表から引く** ので、表に無い理由は組み立てられない。"""
    return {"path": path, "branch": branch, "reason": reason, "detail": ORPHAN_REASONS[reason]}


# --- git の呼び出し ------------------------------------------------------------


def _add(run_git, *, clone_path, path, branch):
    """`git worktree add` を撃つ。branch が既に在れば作り直さず checkout する。"""
    existing = _run(run_git, ["branch", "--list", branch, "--format=%(refname:short)"], clone_path)
    args = (
        ["worktree", "add", path, branch]
        if existing.strip() == branch
        else ["worktree", "add", "-b", branch, path]
    )
    _run(run_git, args, clone_path)


def _run(run_git, args, cwd):
    try:
        return run_git(args, cwd)
    except project.ProjectError as exc:
        raise WorktreeError(str(exc)) from exc


def _parse_worktree_list(text):
    """`--porcelain` の block を (path, branch, locked) に畳む。"""
    entries = []
    current = None
    for line in text.splitlines():
        if line.startswith("worktree "):
            current = {"path": line[len("worktree ") :], "branch": None, "locked": False}
            entries.append(current)
        elif current is None:
            continue
        elif line.startswith("branch "):
            current["branch"] = line[len("branch ") :].removeprefix("refs/heads/")
        elif line == "locked" or line.startswith("locked "):
            current["locked"] = True
    return entries


# --- 台帳外ディレクトリの判定 (ADR 0048) ---------------------------------------


def _child_dirs(directory):
    try:
        return sorted(child for child in Path(directory).iterdir() if child.is_dir())
    except OSError:
        return []  # 置き場そのものが無い = 作業ツリーを 1 つも作っていない


def _classify_unregistered(path):
    """登録に無いディレクトリの種別。残骸でなければ None。

    **`git -C <dir> status` を生存判定に使わない** — `.git` を失ったディレクトリでは git が
    親 repo まで遡って答えるので、残骸が親の状態を借りて「生きている」に見える (ADR 0048)。
    """
    link = path / ".git"
    if not link.exists():
        return NO_GIT
    if link.is_dir():
        return None  # 独立した clone。dispatch の作業ツリーではない
    try:
        text = link.read_text(encoding="utf-8")
    except OSError:
        return UNVERIFIED
    gitdir = _parse_gitdir(text, path)
    if gitdir is None:
        return UNVERIFIED
    return None if gitdir.exists() else STALE_GITDIR


def _parse_gitdir(text, base):
    for line in text.splitlines():
        if line.startswith("gitdir:"):
            target = Path(line[len("gitdir:") :].strip())
            return target if target.is_absolute() else (base / target)
    return None


def _is_under(path, directory):
    return os.path.commonpath([str(Path(path)), str(Path(directory))]) == str(Path(directory))
