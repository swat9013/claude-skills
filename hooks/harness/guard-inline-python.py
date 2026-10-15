#!/usr/bin/env python3
"""guard-inline-python.py — PreToolUse / Bash: shell の inline 実行 (bash -c / sh -c / zsh -c) を deny する。

file 名の `python` は、python / perl / ruby / node の inline も見ていた頃の名残。

Why: permission rule は `bash -c "…"` の中身を検査しない。`-c` の文字列に包めば
permissions.deny の entry (`git push --force` 等) を迂回できるので、本 deny は
**抜け道を塞ぐ側**にある (ADR 0077)。

python / perl / ruby / node の inline (`-c` / `-e` / stdin の `-`) は deny しない (ADR 0077)。
script file に書いて実行すれば同じ能力で通る — 抜け道がある deny は往復を生むだけで、
塞ごうとした任意コード実行の経路を塞いでいなかった。

fail-posture: **fail-closed** (#250 決定 3 / #596 / ADR 0046)。payload を読めなかったときは
判断保留 = 素通しにせず deny する。読み取り失敗を素通しにすると command が空文字になり、
下の判定が不成立のまま passthrough して `bash -c` 系のインライン実行が無検査で通る。
受容コスト: 本 hook は hooks.json に `if` gate を持たず全 Bash 呼び出しで発火するため、
読めない payload では Bash が全面停止する (#596 で人間が判断)。

here-document の本体は判定から落とす (`_prelude.strip_here_documents`)。

Registration: hooks/hooks.json PreToolUse / Bash matcher (`if` 無し)
"""
from __future__ import annotations

import os
import re
import sys
from typing import NoReturn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _prelude import (  # noqa: E402
    decide,
    deny,
    matches_any_line,
    passthrough,
    read_payload_or_deny,
    read_stdin_text,
    strip_here_documents,
    tool_input_text,
)

# `grep -E '(^|[; &|])(bash|sh|zsh) +-c( |$)'` と同じ判定
INLINE_SHELL = re.compile(r"(^|[; &|])(bash|sh|zsh) +-c( |$)")


def judge(stdin_text: str) -> NoReturn:
    payload = read_payload_or_deny(stdin_text)
    command = strip_here_documents(tool_input_text(payload, "command"))
    if matches_any_line(INLINE_SHELL, command):
        deny(
            "bash -c / sh -c / zsh -c のインライン実行は禁止。スクリプトファイル化するか、"
            "個別の Bash ツール呼び出しに分解してください"
        )
    passthrough()


def main(stdin_text: str) -> tuple[int, str]:
    """hook の純粋な入口。(exit code, stdout 本文) を返す。"""
    return decide(judge, stdin_text)


if __name__ == "__main__":
    exit_code, stdout_text = main(read_stdin_text())
    sys.stdout.write(stdout_text)
    sys.exit(exit_code)
