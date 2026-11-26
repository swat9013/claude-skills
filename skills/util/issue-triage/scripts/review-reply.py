#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""ready-for-agent-review の返答を issue ごとに振り分け、次の行き先を出す。

usage: review-reply.py <返答の file> --invoked <N>=<回数> [<N>=<回数> ...]

返答の file は `swat-skills:ready-for-agent-review` の返答を逐語で写したもの。回数はその issue で invoke した回数
(まとめての 1 回目を 1 と数える。1 か 2)。

## 出力

--invoked に並べた順に、issue ごとに 1 行 `#<N> <行き先>` を出す。上から順に当て、最初に当たったものにする。

1. `## ready-for-agent-review: #<N>` のブロックが無い: `停止: ブロックが返らなかった`
2. ブロックの節の見出しより前に `停止:` 行がある (次の手順はブロックの最後の `次の手順:` 行): `停止: <停止理由> / 次の手順: <次の手順>`
3. `### 指摘` の節が「該当なし」でも指摘の見出し (`#### `) を持つのでもない: `停止: 指摘の節を読めない`
4. `### 指摘` が「該当なし」で、未反証の節も無い: `ready-for-agent`
5. 指摘がすべて修正案の行を持たず `深掘り` の行を持ち、未反証の節も無い: `区分 4 (判断)`
6. 回数が 2 未満: `invoke し直す`
7. 2 回目の後に未反証の節が残った: `停止: 2 回目の invoke の後も未反証が残った`
8. 2 回目の後に指摘が残った: `振り直し` (区分 2・4 (判断)・4 (人手)・`needs-info` のどれにするかは論点で決める)

## 終了コード

- 0: 出力した
- 2: 返答の file を読めない・回数が 1 か 2 でない・同じ issue のブロックが 2 つある。stdout に何も出さない

invariant:

1. 「該当なし」を確かめられない指摘の節を `ready-for-agent` にしない
2. 同じ issue のブロックが 2 つある返答は振り分けない (どちらの回の結果か決まらない)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BLOCK = re.compile(r"^## ready-for-agent-review: #(\d+)\s*$", re.MULTILINE | re.ASCII)
# ready-for-agent-review の手順 5 の雛形の節の見出し。ほかの `### ` の行 (引用・修正案の中) では節を切らない
SECTIONS = ("指摘", "落とした候補", "未反証 — 承認材料にしない")
SECTION = re.compile(r"^### (" + "|".join(map(re.escape, SECTIONS)) + r")\s*$", re.MULTILINE)
FINDING = re.compile(r"^#### ", re.MULTILINE)


class Unusable(Exception):
    """入力が使えない。"""


def blocks(text: str) -> dict[int, str]:
    found: dict[int, str] = {}
    heads = list(BLOCK.finditer(text))
    for m, nxt in zip(heads, [*heads[1:], None]):
        n = int(m[1])
        if n in found:
            raise Unusable(f"#{n} のブロックが 2 つある")
        found[n] = text[m.end() : nxt.start() if nxt else len(text)]
    return found


def sections(block: str) -> tuple[str, dict[str, str]]:
    """節の見出しより前 (header) と、見出しごとの中身。"""
    heads = list(SECTION.finditer(block))
    header = block[: heads[0].start()] if heads else block
    body = {m[1]: block[m.end() : nxt.start() if nxt else len(block)] for m, nxt in zip(heads, [*heads[1:], None])}
    return header, body


def line_value(text: str, key: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(key):
            return line[len(key) :].strip()
    return None


def route(block: str | None, invoked: int) -> str:
    if block is None:
        return "停止: ブロックが返らなかった"
    header, secs = sections(block)
    reason = line_value(header, "停止:")
    if reason is not None:
        steps = [line[len("次の手順:") :].strip() for line in block.splitlines() if line.startswith("次の手順:")]
        return f"停止: {reason} / 次の手順: {steps[-1] if steps else '(書かれていない)'}"
    findings = secs.get("指摘")
    # 見出しが雛形から崩れていても、未反証の節が残ったものとして扱う
    unrefuted = re.search(r"^### 未反証", block, re.MULTILINE) is not None
    if findings is None:
        return "停止: 指摘の節を読めない"
    items = FINDING.split(findings)[1:]
    if not items:
        if findings.strip() != "該当なし":
            return "停止: 指摘の節を読めない"
        if not unrefuted:
            return "ready-for-agent"
    elif not unrefuted and all(
        not re.search(r"^- 修正案:", i, re.MULTILINE) and re.search(r"^- 深掘り", i, re.MULTILINE) for i in items
    ):
        return "区分 4 (判断)"
    if invoked < 2:
        return "invoke し直す"
    if unrefuted:
        return "停止: 2 回目の invoke の後も未反証が残った"
    return "振り直し"


def parse_invoked(values: list[str]) -> list[tuple[int, int]]:
    pairs = []
    for v in values:
        n, sep, count = v.partition("=")
        if not sep or not re.fullmatch(r"#?\d+", n, re.ASCII) or count not in ("1", "2"):
            raise Unusable(f"--invoked の値 {v!r} が <N>=<1 か 2> でない")
        pairs.append((int(n.lstrip("#")), int(count)))
    return pairs


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file", type=Path, help="返答を逐語で写した file")
    p.add_argument("--invoked", nargs="+", required=True, help="<N>=<invoke した回数>")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        pairs = parse_invoked(args.invoked)
        text = args.file.read_text(encoding="utf-8")
        found = blocks(text)
    except (OSError, UnicodeDecodeError) as e:
        print(f"読めない: {args.file} ({e})", file=sys.stderr)
        return 2
    except Unusable as e:
        print(f"振り分けられない: {e}", file=sys.stderr)
        return 2
    print("\n".join(f"#{n} {route(found.get(n), count)}" for n, count in pairs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
