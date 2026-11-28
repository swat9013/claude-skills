#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""委譲の前に変更の控えを取り、戻った後に控えと照合して、許可 path の外で変わった path を stdout へ出す。

    snapshot-changes.py take <控え file> [--repo <repo root>] [--keep <path>...]
    snapshot-changes.py compare <控え file> [<許可 path>...]

- take: `--repo` を渡すと、その repo の `git rev-parse HEAD` と `git status --porcelain --untracked-files=all` の
  path を控え、status に載った path ごとに `git hash-object` の値 (file が無ければ「不在」) を控える。
  `--keep` に渡した path は中身 (無ければ「不在」) を控える。build-review-prompts.py が、この中身を
  パッチの前の状態として diff を作る (控えより前の未 commit の変更を diff に混ぜない)
- compare: `git diff --name-only <控えた HEAD>` と今の status の path を合わせ、控えた status に載っていた path のうち
  hash が控えと同じもの (「不在」と控えた path は今も不在のもの) を除き、残りのうち許可 path の外にある path を
  repo root 相対で 1 行 1 件出す。許可 path は repo root 相対で比べ、絶対 path を渡したら repo root 相対に直す

出した path を止める・記録する・人へ返すかは呼び出し側が決める。

invariant:

1. compare は、--repo を付けずに取った控えを受けたら stderr へ 1 行出して exit 1
2. git が失敗したら stderr へ 1 行出して exit 1 (控えも照合の結果も出さない)
3. 対象外の path があってもなくても、照合できたら exit 0
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


class GitError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        first_line = (proc.stderr.strip().splitlines() or ["(stderr なし)"])[0]
        raise GitError(f"git {' '.join(args)}: {first_line}")
    return proc.stdout


def _status_paths(root: Path) -> list[str]:
    """`git status --porcelain -z` の path。rename は新旧の両方を返す。"""
    fields = _git(root, "status", "--porcelain", "--untracked-files=all", "-z").split("\0")
    paths: list[str] = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC" or entry[1] in "RC":  # -z の rename / copy は次の field に元の path を置く
            paths.append(fields[i])
            i += 1
    return paths


def _hash(root: Path, rel: str) -> str | None:
    if not (root / rel).is_file():
        return None
    return _git(root, "hash-object", "--", rel).strip()


def take(out: Path, repo: Path | None, keep: list[str]) -> dict:
    snapshot: dict = {"repo": None, "head": None, "status": {}, "kept": {}}
    if repo is not None:
        # file の path を渡されても git -C が動くよう、親ディレクトリから引く
        root = Path(_git(repo.parent if repo.is_file() else repo, "rev-parse", "--show-toplevel").strip())
        snapshot["repo"] = str(root)
        snapshot["head"] = _git(root, "rev-parse", "HEAD").strip()
        snapshot["status"] = {rel: _hash(root, rel) for rel in _status_paths(root)}
    for given in keep:
        path = Path(os.path.realpath(Path(given).expanduser()))
        snapshot["kept"][str(path)] = path.read_text(encoding="utf-8") if path.is_file() else None
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    return snapshot


def compare(snapshot: dict, allowed: list[str]) -> list[str]:
    root = Path(snapshot["repo"])
    changed = {p for p in _git(root, "diff", "--name-only", "-z", snapshot["head"]).split("\0") if p}
    changed |= set(_status_paths(root))
    unchanged = {rel for rel, digest in snapshot["status"].items() if rel in changed and _hash(root, rel) == digest}
    # git の root は symlink を解いた path なので、絶対 path の許可も realpath にしてから root 相対に直す
    allowed_rel = {
        # 親 directory だけを解く (file 自体が symlink なら、git はその path を 1 file として出す)
        os.path.relpath(Path(os.path.realpath(Path(a).expanduser().parent)) / Path(a).name, root)
        if Path(a).expanduser().is_absolute() else os.path.normpath(a)
        for a in allowed
    }
    return sorted(p for p in changed - unchanged if p not in allowed_rel)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)
    t = sub.add_parser("take", help="控えを取る")
    t.add_argument("snapshot", type=Path, help="控えを書く file")
    t.add_argument("--repo", type=Path, help="git の状態を控える repo (中の任意の file か directory でよい)")
    t.add_argument("--keep", nargs="+", default=[], help="中身を控える path")
    c = sub.add_parser("compare", help="控えと照合する")
    c.add_argument("snapshot", type=Path, help="take が書いた控え")
    c.add_argument("allowed", nargs="*", help="書いてよい path (repo root 相対か絶対)")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.mode == "take":
            if args.repo is None and not args.keep:
                print("snapshot-changes: --repo か --keep のどちらかを渡す", file=sys.stderr)
                return 1
            take(args.snapshot, args.repo, args.keep)
            return 0
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        if not snapshot.get("repo"):
            print("snapshot-changes: 控えに repo が無い (take に --repo を渡して取り直す)", file=sys.stderr)
            return 1
        for path in compare(snapshot, args.allowed):
            print(path)
        return 0
    except (GitError, OSError, ValueError) as exc:
        print(f"snapshot-changes: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
