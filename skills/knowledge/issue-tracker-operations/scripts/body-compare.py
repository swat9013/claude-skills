#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""基準の本文と書き戻す直前の本文を比べ、一致か競合かを出す。

usage: body-compare.py <基準の file> <比べる本文の file>

一致とみなす差の規則は `references/body-rewrite.md`「書き戻し」の 2 が持ち、この script はその規則で 2 つの file を比べる。

終了コード:

- 0: 一致。stdout に `一致`
- 1: 競合。stdout に `競合` と、続けて unified diff
- 2: file を読めない。stdout に何も出さず、stderr に理由

invariant:

1. body-rewrite.md「書き戻し」の 2 が一致とする差のほかに差があれば、競合とする
2. file を読めなければ一致とも競合とも出さない (exit 2)
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path


def normalized(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip(" \t"):
        lines.pop()
    return "\n".join(lines)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("base", type=Path, help="基準の file")
    p.add_argument("current", type=Path, help="比べる本文の file")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        base, current = (normalized(p.read_text(encoding="utf-8")) for p in (args.base, args.current))
    except (OSError, UnicodeDecodeError) as e:
        print(f"読めない: {e}", file=sys.stderr)
        return 2
    if base == current:
        print("一致")
        return 0
    print("競合")
    for line in difflib.unified_diff(
        base.splitlines(), current.splitlines(), str(args.base), str(args.current), lineterm=""
    ):
        print(line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
