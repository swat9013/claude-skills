#!/usr/bin/env python3
"""issue-dispatch 用の repo tidy。default branch の最新化と merged branch/worktree 掃除を決定論化する。

issue-dispatch skill の手順 3 (起動時) と手順 7.4 (監視ループの毎サイクル) から呼ぶ。
herdr には触れない純 git script — dispatch した pane が PR を merge した後に残る
local branch と worktree を回収し、次の dispatch が古い base から始まらないようにする。

subcommand:
- run [--cwd DIR] [--active 217,221]
  fetch --prune → default branch へ checkout → pull --ff-only → merged branch 列挙 →
  (worktree があれば remove してから) branch -d → worktree prune。
  結果は {"ok":..., "default_branch":..., "pull":..., "removed_worktrees":[...],
  "deleted_branches":[...], "skipped":[...], "failed":[...]}。

除外規則 (削除しないもの):
- protected 名 (main / master / develop / development / release / pre-release /
  staging / production) と default branch 自身
- 現在 HEAD の branch (`git branch --merged` の `*` 行)
- `--active` で渡された稼働中 issue の branch / worktree。PR が merge された直後に
  pane がまだ生きている瞬間があり、そこで消すと走行中セッションの足元が消える。
  branch 名 (`i<N>` / `worktree-i<N>`) と worktree path (`.claude/worktrees/i<N>`)
  の両方で照合する — Claude Code の `--worktree i<N>` は branch 名を `worktree-i<N>`
  にするため、branch 名だけの照合では取りこぼす
- dirty worktree を持つ branch (未回収の変更があるため skip する)
- `branch -d` が拒む未 merge branch (`-D` への昇格はしない — 成果喪失に直結する)

失敗の扱い (fail-open): 前提不成立 (work tree 外 / linked worktree からの呼び出し /
default branch 解決不能) だけを TidyError で即死させ、fetch / checkout / pull の失敗は
`pull.ok=false` に記録して掃除フェーズを続行する。merged 判定は default branch 基準
なので checkout できなくても正しく効く。dispatch 本体を止めない位置づけの操作であり、
skill 側は非 0 exit を「報告して続行」として扱う。

終了コード: 0=成功 (dirty skip を含む) / 1=前提不成立・pull 系失敗・削除失敗 / 2=引数エラー。
"""

import argparse
import json
import os
import re
import subprocess
import sys

ISSUE_BRANCH = re.compile(r"^(?:worktree-)?i(\d+)$")
PROTECTED = frozenset(
    {
        "main",
        "master",
        "develop",
        "development",
        "release",
        "pre-release",
        "staging",
        "production",
    }
)
SUBPROCESS_TIMEOUT_SEC = 120


class TidyError(Exception):
    """前提不成立 (work tree 外 / linked worktree / default branch 解決不能)。"""


def run_command(argv, timeout=SUBPROCESS_TIMEOUT_SEC):
    """subprocess 境界。stdout と stderr を分離したまま返す (テストで monkeypatch する)。"""
    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )
    return proc.returncode, proc.stdout, proc.stderr


def _git(cwd, args):
    """git 実行。(rc, stdout, stderr) をそのまま返す — 呼び出し側が失敗の扱いを決める。"""
    return run_command(["git", "-C", cwd, *args])


def _git_out(cwd, args):
    """成功前提の git 実行。非 0 exit は TidyError で即表面化する (fail-closed)。"""
    rc, out, err = _git(cwd, args)
    if rc != 0:
        raise TidyError(f"git {args[0]} failed (exit {rc}): {err.strip()}")
    return out


def resolve_root(cwd):
    """repo root を返す。linked worktree から呼ばれていたら TidyError。

    tidy は checkout / pull で main の working tree を動かすため、dispatch した pane の
    worktree から呼ぶと対象を取り違える。呼び出しは repo root からに限定する。
    """
    git_dir = _git_out(cwd, ["rev-parse", "--git-dir"]).strip()
    common_dir = _git_out(cwd, ["rev-parse", "--git-common-dir"]).strip()
    if os.path.realpath(os.path.join(cwd, git_dir)) != os.path.realpath(
        os.path.join(cwd, common_dir)
    ):
        raise TidyError("linked worktree から呼ばれた (repo root から実行する)")
    return _git_out(cwd, ["rev-parse", "--show-toplevel"]).strip()


def resolve_default_branch(root):
    """origin/HEAD → main → master の順で default branch を決める。"""
    rc, out, _err = _git(root, ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"])
    if rc == 0 and out.strip():
        # prefix-strip で '/' 含む branch 名 (release/1.0 等) も正しく抽出する
        return out.strip()[len("refs/remotes/origin/") :]
    for candidate in ("main", "master"):
        rc, _out, _err = _git(root, ["show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"])
        if rc == 0:
            return candidate
    raise TidyError("default branch を解決できない (origin/HEAD も local main/master も無い)")


def refresh(root, default_branch):
    """fetch --prune → (必要なら) checkout → pull --ff-only。失敗は記録して返す。"""
    result = {"ok": True, "fetched": False, "checked_out": None, "pulled": False, "error": None}

    rc, _out, err = _git(root, ["fetch", "--prune"])
    if rc != 0:
        result.update(ok=False, error=f"fetch --prune failed: {err.strip()}")
        return result
    result["fetched"] = True

    current = _git_out(root, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    if current != default_branch:
        rc, _out, err = _git(root, ["checkout", default_branch])
        if rc != 0:
            # root が dirty で checkout が拒まれた等。merged 判定は default branch 基準で
            # 効くので、pull を諦めて掃除フェーズへ進む
            result.update(ok=False, error=f"checkout {default_branch} failed: {err.strip()}")
            return result
        result["checked_out"] = default_branch

    rc, _out, err = _git(root, ["pull", "--ff-only"])
    if rc != 0:
        result.update(ok=False, error=f"pull --ff-only failed: {err.strip()}")
        return result
    result["pulled"] = True
    return result


def parse_merged_branches(text):
    """`git branch --merged` の出力 → branch 名。現在 HEAD (`*`) は除き `+` 印は剥がす。

    `+` (他 worktree で checkout 中) は残す — その worktree を remove してから
    branch -d するのが本 script の主目的。
    """
    names = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("*"):
            continue
        if stripped.startswith("+ "):
            stripped = stripped[2:].strip()
        if stripped.startswith("(") or " -> " in stripped:
            # detached HEAD 表示 ("(HEAD detached at ...)") と symref は branch ではない
            continue
        names.append(stripped)
    return names


def parse_worktree_map(text):
    """`git worktree list --porcelain` の出力 → {branch: worktree path}。"""
    mapping = {}
    path = None
    for line in text.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line.startswith("branch refs/heads/") and path:
            mapping[line[len("branch refs/heads/") :]] = path
        elif not line.strip():
            path = None
    return mapping


def is_active(branch, worktree_path, active):
    """稼働中 pane に属する branch / worktree か。

    branch 名 (`i<N>` / `worktree-i<N>`) と worktree path の basename (`i<N>`) の
    両方で照合する。Claude Code の `--worktree i<N>` は path が `i<N>`、branch 名が
    `worktree-i<N>` になるため、片方だけの照合では取りこぼす。
    """
    match = ISSUE_BRANCH.match(branch)
    if match and int(match.group(1)) in active:
        return True
    if worktree_path:
        match = ISSUE_BRANCH.match(os.path.basename(worktree_path.rstrip("/")))
        if match and int(match.group(1)) in active:
            return True
    return False


def select_branches(merged, worktrees, default_branch, active):
    """merged branch → 削除対象と除外理由。除外は報告用に理由付きで残す。"""
    targets, excluded = [], []
    for branch in merged:
        if branch == default_branch or branch in PROTECTED:
            excluded.append({"branch": branch, "reason": "protected"})
            continue
        worktree = worktrees.get(branch)
        if is_active(branch, worktree, active):
            excluded.append({"branch": branch, "reason": "active-pane"})
            continue
        targets.append({"branch": branch, "worktree": worktree})
    return targets, excluded


def remove_target(root, target, result):
    """worktree remove → branch -d。dirty / 失敗は result に積んで False を返す。"""
    branch, worktree = target["branch"], target["worktree"]
    if worktree:
        if _git_out(worktree, ["status", "--porcelain"]).strip():
            result["skipped"].append({"branch": branch, "worktree": worktree, "reason": "dirty"})
            return False
        rc, _out, err = _git(root, ["worktree", "remove", worktree])
        if rc != 0:
            result["failed"].append(
                {"branch": branch, "step": "worktree-remove", "error": err.strip()}
            )
            return False
        result["removed_worktrees"].append({"branch": branch, "worktree": worktree})
    rc, _out, err = _git(root, ["branch", "-d", branch])
    if rc != 0:
        # 未 merge 判定などで拒まれた場合。-D への昇格はしない
        result["failed"].append({"branch": branch, "step": "branch-delete", "error": err.strip()})
        return False
    result["deleted_branches"].append(branch)
    return True


def tidy(cwd, active):
    """default branch 最新化 + merged branch/worktree 掃除を通しで実行する。"""
    root = resolve_root(cwd)
    default_branch = resolve_default_branch(root)
    result = {
        "ok": False,
        "root": root,
        "default_branch": default_branch,
        "pull": refresh(root, default_branch),
        "removed_worktrees": [],
        "deleted_branches": [],
        "skipped": [],
        "excluded": [],
        "failed": [],
    }

    merged = parse_merged_branches(_git_out(root, ["branch", "--merged", default_branch]))
    worktrees = parse_worktree_map(_git_out(root, ["worktree", "list", "--porcelain"]))
    targets, result["excluded"] = select_branches(merged, worktrees, default_branch, active)
    for target in targets:
        remove_target(root, target, result)
    _git_out(root, ["worktree", "prune"])

    result["ok"] = result["pull"]["ok"] and not result["failed"]
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="default branch 最新化 + merged branch/worktree 掃除")
    p_run.add_argument("--cwd", default=".", help="repo root 内の path (default: cwd)")
    p_run.add_argument("--active", default="", help="稼働中 pane の issue 番号 (comma 区切り)")

    return parser.parse_args(argv)


def main(args):
    try:
        active = {int(n) for n in args.active.split(",") if n.strip()}
        result = tidy(args.cwd, active)
    except TidyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main(parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)
