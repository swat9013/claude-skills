#!/usr/bin/env python3
"""guard-write.py — PreToolUse / Write|Edit: file_path が機密パスなら deny する最終防衛線。

fail-posture: **fail-closed** (#598 / ADR 0046)。payload を読めなかったとき (stdin が空 / JSON
として不正 / tool_input が object でない) は判断保留 = 素通しにせず deny する。読み取り失敗を
素通しにすると、payload が想定外の形になった瞬間に .env / 鍵 / credentials への Write・Edit が
無検査で通る。受容コスト: 読めない payload では Write / Edit が全て止まる。本 hook が守るのは
漏れたら取り返しがつかない機密ファイルで、素通しの損害が停止の損害を上回る。

file_path が空 / 不在なのは正常系 (NotebookEdit 等 file_path を持たない payload) なので
deny しない。

Registration: hooks/hooks.json PreToolUse / Write|Edit matcher
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
    tool_input_text,
)

# `grep -E` (POSIX ERE) と同じ判定になるように書く
SENSITIVE_PATH = re.compile(r"\.(env|key|pem|p12|pfx|crt)$|id_rsa|\.netrc|/credentials$|/secrets/")


def judge(stdin_text: str) -> NoReturn:
    payload = read_payload_or_deny(stdin_text)
    if matches_any_line(SENSITIVE_PATH, tool_input_text(payload, "file_path")):
        deny("機密ファイルへの書き込みは禁止")
    passthrough()


def main(stdin_text: str) -> tuple[int, str]:
    """hook の純粋な入口。(exit code, stdout 本文) を返す。"""
    return decide(judge, stdin_text)


if __name__ == "__main__":
    exit_code, stdout_text = main(read_stdin_text())
    sys.stdout.write(stdout_text)
    sys.exit(exit_code)
