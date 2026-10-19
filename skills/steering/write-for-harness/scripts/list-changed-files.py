#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""作業ツリーと branch で変更された file を、絶対 path で 1 行 1 件 stdout へ出す。

列挙するのは次の union で、作業ツリーに実在する file だけを sort して出す。削除された file は読めないので出さず、
外したことを stderr へ 1 行出す。

- 作業ツリーの変更: staged / unstaged / 未追跡。未追跡ディレクトリは中の file ごと、rename は新 path で出す
- branch の変更: 起点 branch との merge-base から HEAD まで。作業ツリーの変更があっても常に足す

起点 branch は、現在 branch が local branch を追跡していればその branch (`git switch -c <b> --track <local>`
で積んだ branch の前段)、それ以外は origin/HEAD → origin/main → main の順で最初に解決できたもの。

write-for-harness と claude-config-review の手順 1 が引数なしで呼び、それぞれのパターンで振り分ける。
振り分けは呼び出し側が持つので、ここでは種類で絞らない。

列挙が欠けるとき (起点 branch が無い・merge-base が無い・削除された file がある) は、欠けた範囲と読み手への指示を
stderr へ 1 件 1 行で出して exit 0 で続ける。読み手への指示を stderr の行に持たせるので、呼び出し側の手順には
扱いを書かなくてよい。git が動かない (repo の外など) ときは stderr へ 1 行出して exit 1。

invariant:

1. 削除された file は列挙に出さず、外したことを stderr へ 1 行出す
2. 起点 branch か merge-base が無ければ branch diff を省いたことを stderr へ出し、作業ツリーの変更は出す
3. git が動かなければ exit 1
4. 追跡先の local branch を起点にできず既定ブランチへ戻ったときは、そのことを stderr へ出す
   (既定ブランチも解決できないときは 2 の告知だけを出す)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_BASE_CANDIDATES = ("origin/HEAD", "origin/main", "main")
# git status --porcelain=v1 -z で、path の後に旧 path の field が続く状態 (rename / copy)
STATUS_WITH_ORIGINAL_PATH = ("R", "C")
# 削除が多い branch で stderr の 1 行が context を埋めないよう、名前を挙げる件数を絞る (残りは件数だけ)
GONE_PATHS_SHOWN = 10
PARTIAL_LISTING_NOTICE ="利用者にこの行をそのまま告げてから、出力された file で進める。"


class GitError(Exception):
    pass


def _run_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _git(*args: str) -> str:
    proc = _run_git(*args)
    if proc.returncode != 0:
        first_line = (proc.stderr.strip().splitlines() or ["(stderr なし)"])[0]
        raise GitError(f"git {' '.join(args)}: {first_line}")
    return proc.stdout


def _resolves(ref: str) -> bool:
    return _run_git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0


def work_tree_paths() -> tuple[set[str], set[str]]:
    """staged / unstaged / 未追跡の変更 path と、rename / copy の旧 path。

    -uall で未追跡ディレクトリを file に展開する。旧 path は新 path で列挙済みなので、削除の告知から外すために返す。
    """
    fields = _git("status", "--porcelain=v1", "-z", "-uall").split("\0")
    paths: set[str] = set()
    moved_from: set[str] = set()
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        paths.add(path)
        if status[0] in STATUS_WITH_ORIGINAL_PATH or status[1] in STATUS_WITH_ORIGINAL_PATH:
            moved_from.add(fields[i])
            i += 1
    return paths, moved_from


def start_branch() -> tuple[str | None, str | None]:
    """起点 branch と、追跡先の local branch を使えなかったときの告知。"""
    notice = None
    branch = _run_git("symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    if branch and _run_git("config", f"branch.{branch}.remote").stdout.strip() == ".":
        tracked = _run_git("config", f"branch.{branch}.merge").stdout.strip()
        # 自分自身を追跡する設定は起点にならない (merge-base が HEAD になり、commit 済みの変更が消える)
        if tracked and tracked != f"refs/heads/{branch}" and _resolves(tracked):
            return tracked, None
        notice = f"追跡先の local branch {tracked or '(未設定)'} を起点にできず、既定ブランチを起点にした"
    for candidate in DEFAULT_BASE_CANDIDATES:
        if _resolves(candidate):
            if notice:
                notice += f" ({candidate})。前段の branch の変更も列挙に入っている。"
            return candidate, notice
    return None, None


def branch_paths() -> tuple[set[str], str | None]:
    """起点 branch との merge-base から HEAD までに変わった path と、欠けたときの告知。"""
    if not _resolves("HEAD"):
        return set(), None  # commit が 1 つも無い branch には commit 済みの変更が無い
    base, fallback_notice = start_branch()
    if base is None:
        return set(), (
            "branch diff を省いた: 起点 branch (" + " / ".join(DEFAULT_BASE_CANDIDATES)
            + ") がどれも解決できず、branch の commit 済みの変更は列挙に入っていない。"
        )
    fork = _run_git("merge-base", base, "HEAD")
    if fork.returncode != 0 and not fork.stderr.strip():
        # 共通の祖先が無いときだけ、git merge-base は stderr を出さずに exit 1 で終わる
        return set(), (
            f"branch diff を省いた: {base} と HEAD の merge-base が無く (shallow clone・共通の祖先の無い branch)、"
            "branch の commit 済みの変更は列挙に入っていない。"
        )
    if fork.returncode != 0:
        raise GitError(f"git merge-base {base} HEAD: {fork.stderr.strip().splitlines()[0]}")
    out = _git("diff", "--name-only", "-z", f"{fork.stdout.strip()}..HEAD")
    return {p for p in out.split("\0") if p}, fallback_notice


def _is_listable(path: Path) -> bool:
    # git は directory を指す symlink も 1 file として記録するので、symlink は directory 判定より先に通す。
    # 実在する directory (submodule) は読む file ではないので出さない
    return path.is_symlink() or path.is_file()


def _gone_notice(gone: list[Path]) -> str:
    shown = ", ".join(json.dumps(str(p), ensure_ascii=False) for p in gone[:GONE_PATHS_SHOWN])
    rest = f" ほか {len(gone) - GONE_PATHS_SHOWN} 件" if len(gone) > GONE_PATHS_SHOWN else ""
    return f"削除された file {len(gone)} 件は読めないので列挙から外した: {shown}{rest}。{PARTIAL_LISTING_NOTICE}"


def main() -> int:
    try:
        # status / diff は cwd によらず root 相対の path を出すので、root を付けて絶対 path にする
        root = Path(_git("rev-parse", "--show-toplevel").strip())
        paths, moved_from = work_tree_paths()
        committed, notice = branch_paths()
    except (GitError, OSError) as e:
        print(f"{e} — 対象を列挙できなかった。対象を決めずに止まる。", file=sys.stderr)
        return 1
    changed = sorted(root / p for p in paths | committed)
    # 作業ツリーで rename / copy した旧 path は新 path で列挙済みなので、削除として告げない
    gone = [p for p in changed if not os.path.lexists(p) and p not in {root / m for m in moved_from}]
    if notice:
        print(f"{notice} {PARTIAL_LISTING_NOTICE}", file=sys.stderr)
    if gone:
        print(_gone_notice(gone), file=sys.stderr)
    for path in changed:
        if _is_listable(path):
            print(path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
