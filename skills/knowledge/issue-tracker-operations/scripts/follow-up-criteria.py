#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""対象 repo の起票の基準から「単位の外」の箇条を引き、欠けていれば既定の箇条を返す。

usage: follow-up-criteria.py --defaults <filing-criteria-defaults.md の path> [--root <対象 repo の root>]

`--root` の既定は cwd。`<root>/docs/agents/issue-tracker.md` の見出し「follow-up issue の起票」(後ろに括弧の補足が付いてもよい) の節 (見出しから
次の同レベル以上の見出しの直前まで。fenced block の中の行は見出しにも箇条にも数えない) から、太字の見出し語 `**単位の外**` で始まる最上位の箇条を、字下げした続きの行 (空行を挟むものも含む) ごと
逐語で出す。節にその箇条が無ければ、`--defaults` の file の同じ箇条を出す。節が足した補足は読まないので、
補足は呼び出し元が節を読んで当てる。

## 出力

stdout の 1 行目が出典、2 行目以降が箇条の逐語。

- `出典: repo`: 節の箇条
- `出典: 既定の値`: 節に箇条が無く、既定の値を当てた

## 終了コード

- 0: 箇条を出した
- 2: `issue-tracker.md` があるのに読めないか、既定の値の file を読めないか、その file に箇条が無い。stdout に何も出さない
- 3: 対象 repo に節が無い (`issue-tracker.md` が無いか、その見出しが無い)。stdout に何も出さず、stderr に `節なし: <理由>`

invariant:

1. 節が無いときは既定の値を当てない (exit 3) — 節が無い repo は基準で閉じない
2. 節に箇条があれば、既定の値の file を読まずに節の箇条を出す
3. fenced block の中の行を見出しにも箇条にも数えない
4. `issue-tracker.md` があるのに読めなければ、節なし (exit 3) とせず exit 2 で終える
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SECTION_TITLE = "follow-up issue の起票"
CRITERION = "- **単位の外**"
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
# 見出しの語の後ろに括弧の補足 (「(運用)」など) を付けた見出しも同じ節に数える
TITLE = re.compile(re.escape(SECTION_TITLE) + r"(\s*[(（].*[)）])?")


class Failure(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# 最上位の fence は字下げ 0〜3 (それより深い行は indented code)。箇条の中の fence は字下げが深くなる
TOP_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
NESTED_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def fence_states(lines: list[str], fence: re.Pattern[str]) -> list[str]:
    """行ごとに `out` (fence の外)・`open` (開き)・`in` (中)・`close` (閉じ)。閉じは開きと同じ字で長さが開き以上。"""
    states, opener = [], None
    for line in lines:
        m = fence.match(line)
        if opener is None:
            if m:
                opener = m[1]
                states.append("open")
            else:
                states.append("out")
        elif m and m[1][0] == opener[0] and len(m[1]) >= len(opener) and not line.strip()[len(m[1]) :].strip():
            opener = None
            states.append("close")
        else:
            states.append("in")
    return states


def section(lines: list[str]) -> list[str] | None:
    start = level = None
    for i, (line, state) in enumerate(zip(lines, fence_states(lines, TOP_FENCE))):
        m = HEADING.match(line) if state == "out" else None
        if not m:
            continue
        if start is None:
            if TITLE.fullmatch(m[2]):
                start, level = i + 1, len(m[1])
        elif len(m[1]) <= level:
            return lines[start:i]
    return None if start is None else lines[start:]


def criterion(lines: list[str]) -> list[str] | None:
    states = fence_states(lines, NESTED_FENCE)
    for i, line in enumerate(lines):
        if states[i] == "out" and line.startswith(CRITERION):
            end = i + 1
            for j in range(i + 1, len(lines)):
                # fence の中と閉じの行は字下げを問わず続きに数え、字下げしない行か fence の開きで箇条を閉じる
                if states[j] in ("out", "open") and lines[j].strip() and lines[j][:1] not in (" ", "\t"):
                    break
                if lines[j].strip():
                    end = j + 1
            return lines[i:end]
    return None


def resolve(root: Path, defaults: Path) -> tuple[str, list[str]]:
    tracker = root / "docs" / "agents" / "issue-tracker.md"
    try:
        text = tracker.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise Failure(3, f"節なし: {tracker} が無い") from e
    except (OSError, UnicodeDecodeError) as e:
        raise Failure(2, f"読めない: {tracker} ({e})") from e
    body = section(text.splitlines())
    if body is None:
        raise Failure(3, f"節なし: {tracker} に見出し「{SECTION_TITLE}」が無い")
    found = criterion(body)
    if found is not None:
        return "repo", found
    try:
        fallback = criterion(defaults.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError) as e:
        raise Failure(2, f"既定の値を読めない: {defaults} ({e})") from e
    if fallback is None:
        raise Failure(2, f"既定の値の file に {CRITERION} の箇条が無い: {defaults}")
    return "既定の値", fallback


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--defaults", type=Path, required=True, help="filing-criteria-defaults.md の path")
    p.add_argument("--root", type=Path, default=None, help="対象 repo の root (既定は cwd)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        source, lines = resolve(args.root or Path.cwd(), args.defaults)
    except Failure as e:
        print(str(e), file=sys.stderr)
        return e.code
    print(f"出典: {source}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
