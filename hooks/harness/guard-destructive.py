#!/usr/bin/env python3
"""guard-destructive.py — PreToolUse / Bash: 不可逆な破壊的操作 (`find -delete` / docker の削除系) を deny する。

fail-posture: **fail-closed** (ADR 0046)。payload を読めなかったとき (stdin が空 / JSON として
不正 / tool_input が object でない) は素通しせず deny する。読み取り失敗を素通しにすると
command が空文字になり、下の判定が全部不成立のまま passthrough して、本 hook が塞いでいる
破壊的コマンドが無検査で通る。本 hook は hooks.json に `if` を持たないので、読めない payload
では全 Bash が止まる。

here-document の本体は判定から落とす (`_prelude.strip_here_documents`)。

find / docker の検出は任意の path 接頭辞 (`/usr/bin/find` `/usr/local/bin/docker`) を許容する。
素の語頭境界だけを見ていた頃は接頭辞の `/` が境界を満たさず素通りしていた。接頭辞は直前に
リテラル `/` を要求するので `xfind` / `nodocker` は非該当。語頭境界に区切り (`;` `&` `|` `(`
`)`) を含めるのは、接頭辞なしの `cd /tmp;find . -delete` が素通りしていたため。語頭境界に
quote は足さない — 足すと `git commit -m "docker rm -f x"` のような文字列引数まで deny に落ちる。

rm の判定は持たない。`rm -rf` の deny は `-f` を外した `rm -r` で同じ削除ができる抜け道を
持つため削除した (ADR 0077 の基準。root / home への削除は Claude Code の組み込み保護が止める)。

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

# 判定の regex は `grep -E` (POSIX ERE) と同じ判定になるように書く。`\s` / `\S` は `[[:space:]]` / `[^[:space:]]` に
# 当たる (`matches_any_line` が文字クラスを grep に揃える)
_COMMAND_START = r"(^|[\s;&|()])(\S*/)?"
DENY_RULES = [
    (re.compile(_COMMAND_START + r"find\s.*-delete(\s|$)"),
     "find -delete は禁止。事前に -print で対象を確認してから個別削除してください"),
    (re.compile(_COMMAND_START + r"docker\s+rm\s+(-f|--force)(\s|$)"),
     "docker rm -f は禁止。docker stop の後で削除してください"),
    (re.compile(_COMMAND_START + r"docker\s+volume\s+rm(\s|$)"),
     "docker volume rm はデータ消去のため禁止"),
    (re.compile(_COMMAND_START + r"docker\s+system\s+prune(\s+.*)?\s+(-f|--force)(\s|$)"),
     "docker system prune -f は禁止"),
]


def judge(stdin_text: str) -> NoReturn:
    payload = read_payload_or_deny(stdin_text)
    command = strip_here_documents(tool_input_text(payload, "command"))
    for pattern, reason in DENY_RULES:
        if matches_any_line(pattern, command):
            deny(reason)
    passthrough()


def main(stdin_text: str) -> tuple[int, str]:
    """hook の純粋な入口。(exit code, stdout 本文) を返す。"""
    return decide(judge, stdin_text)


if __name__ == "__main__":
    exit_code, stdout_text = main(read_stdin_text())
    sys.stdout.write(stdout_text)
    sys.exit(exit_code)
