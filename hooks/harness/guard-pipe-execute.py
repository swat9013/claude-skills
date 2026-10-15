#!/usr/bin/env python3
"""guard-pipe-execute.py — PreToolUse / Bash: download-and-execute (`curl ... | bash` 等) を deny する。

fail-posture: **fail-closed** (ADR 0046)。payload を読めなかったとき (stdin が空 / JSON として
不正 / tool_input が object でない) は素通しせず deny する。読み取り失敗を素通しにすると
command が空文字になり、下の判定が全部不成立のまま passthrough して、本 hook が塞いでいる
download-and-execute が無検査で通る。本 hook は hooks.json に `if` を持たないので、読めない
payload では全 Bash が止まる。

here-document の本体は判定から落とす (`_prelude.strip_here_documents`)。

download-and-execute を 4 系統で検出する:
  (1) パイプ shell: curl ... | (sudo)? (/path/to/)? bash|sh|zsh|dash|ksh — 絶対パス指定も捕捉
  (2) パイプ xargs shell: curl ... | xargs (sudo)? bash|sh
  (3) process substitution: bash <(curl ...) / source <(curl ...) / . <(curl ...)
  (4) eval of command substitution: eval "$(curl ...)" / eval `curl ...`

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
DENY_RULES = [
    (re.compile(r"\|\s*(sudo\s+)?([^\s|;&]*/)?(bash|sh|zsh|dash|ksh)(\s|$)"),
     "curl|bash パターンは禁止。スクリプトを一時ファイルに保存しレビュー後に実行してください"),
    (re.compile(r"\|\s*xargs\s+(sudo\s+)?(bash|sh|zsh)(\s|$)"),
     "curl | xargs bash パターンは禁止"),
    (re.compile(r"(^|\s)(bash|sh|zsh|source|\.)\s+<\(\s*(curl|wget)(\s|$)"),
     "bash <(curl ...) / source <(curl ...) パターンは禁止"),
    (re.compile(r"eval\s+[\"']?\$\(\s*(curl|wget)(\s|$)"),
     'eval "$(curl ...)" パターンは禁止'),
    (re.compile(r"eval\s+[\"']?`\s*(curl|wget)(\s|$)"),
     "eval `curl ...` パターンは禁止"),
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
