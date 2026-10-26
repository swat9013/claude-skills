#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""harness の file を path のパターンで component に分け、`<path> - <component>` を 1 行 1 件 stdout へ出す。

component の分類の正本。write-for-harness (手順 1 の対象の振り分けと、手順 4 の構造 reviewer に渡す harness 全体一覧)
と claude-config-review (「ファイル種類判定」節の type の判定) がこの script の出力を使う。

- 引数なし: `git ls-files` の file (repo root 相対) を分類する。分類できなかった file は出さない
- path を渡す: 渡した path を、渡した表記のまま分類する。分類できなかった path は stderr へ `分類外: <path>` を 1 行ずつ出す。
  git を使わないので、repo の外の path (`~/.claude/` 系) も渡せる

| component | パターン |
|---|---|
| hook | `**/hooks/hooks.json`、`**/hooks/**/*.sh`、`**/hooks/**/*.py` (`hooks/` の下の `tests/` にある file は除く) |
| skill | `**/SKILL.md` |
| claude-md | `**/CLAUDE.md` |
| rules | `**/rules/*.md` (`.claude/rules/` を含む) |
| agent | `**/.claude/agents/*.md` と、plugin root (`.claude-plugin/` を持つディレクトリ) 直下の `agents/*.md` |
| settings | `**/settings*.json` |

上の行から順に当て、最初に当たった component にする。hook が注入文を持つか (Inferential の hook-inject か、
Computational か) は中身を読まないと決まらないので、ここでは `hook` までにとどめ、決めるのは呼び出し側が持つ。
パターンに当たらない場所の hook script (設定の command が `hooks/` の外を指すもの) は分類しない。

invariant:

1. stdout には分類できた path だけを `<path> - <component>` で出す
2. `hooks/` の下の `tests/` にある file は hook にしない
3. `agents/*.md` は、親が `.claude/` か plugin root のときだけ agent にする
4. path を渡したとき、分類できなかった path は stderr へ 1 件 1 行で出す
5. 引数なしで git が動かなければ exit 1
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

HOOK_SCRIPT_SUFFIXES = (".sh", ".py")


def _under_hooks(path: Path) -> bool:
    dirs = path.parts[:-1]
    if "hooks" not in dirs:
        return False
    # hook script の自動テストは hook 設定そのものではないので、hooks/ の下の tests/ は hook にしない
    return "tests" not in dirs[dirs.index("hooks") + 1:]


def _is_agent(path: Path) -> bool:
    if path.suffix != ".md" or path.parent.name != "agents":
        return False
    owner = path.parent.parent
    return owner.name == ".claude" or (owner / ".claude-plugin").is_dir()


def classify(path: Path) -> str | None:
    """path の component。どのパターンにも当たらなければ None。"""
    if _under_hooks(path) and (
        (path.name == "hooks.json" and path.parent.name == "hooks") or path.suffix in HOOK_SCRIPT_SUFFIXES
    ):
        return "hook"
    if path.name == "SKILL.md":
        return "skill"
    if path.name == "CLAUDE.md":
        return "claude-md"
    if path.suffix == ".md" and path.parent.name == "rules":
        return "rules"
    if _is_agent(path):
        return "agent"
    if fnmatch(path.name, "settings*.json"):
        return "settings"
    return None


def tracked_files() -> tuple[Path, list[str]]:
    """repo root と、`git ls-files` の root 相対 path。"""
    def git(*args: str) -> str:
        proc = subprocess.run(["git", *args], capture_output=True, text=True)
        if proc.returncode != 0:
            first_line = (proc.stderr.strip().splitlines() or ["(stderr なし)"])[0]
            raise RuntimeError(f"git {' '.join(args)}: {first_line}")
        return proc.stdout

    root = Path(git("rev-parse", "--show-toplevel").strip())
    return root, [p for p in git("-C", str(root), "ls-files", "-z").split("\0") if p]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="*", help="分類する path。省くと git ls-files の file を分類する")
    return p.parse_args(argv)


def main(args: argparse.Namespace) -> int:
    if args.paths:
        for given in args.paths:
            component = classify(Path(given).expanduser())
            if component:
                print(f"{given} - {component}")
            else:
                print(f"分類外: {given}", file=sys.stderr)
        return 0
    try:
        root, files = tracked_files()
    except (RuntimeError, OSError) as e:
        print(f"{e} — harness の file を列挙できなかった。一覧を作らずに止まる。", file=sys.stderr)
        return 1
    for rel in files:
        # plugin root の判定は filesystem を見るので、root を付けて判定し、出力は root 相対のままにする
        component = classify(root / rel)
        if component:
            print(f"{rel} - {component}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)
