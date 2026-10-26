#!/usr/bin/env python3
"""guard-write.py — PreToolUse / Write|Edit|NotebookEdit: 書き込み先が機密パスなら deny する。

置く理由: backstop
    Bash の redirect (`printf x > .env`) という抜け道はあるが、範囲の重なる `Edit()` deny は利用者の
    global settings にしか無く plugin の配布先に届かないので、配布先ではこの hook が唯一の層になる
    (docs/design/hooks/README.md「backstop と範囲が重なる guard」・ADR 0108)。

fail-posture: **fail-closed** (#598 / ADR 0046)。payload を読めなかったとき (stdin が空 / JSON
として不正 / tool_input が object でない) は判断保留 = 素通しにせず deny する。読み取り失敗を
素通しにすると、payload が想定外の形になった瞬間に .env / 鍵 / credentials への書き込みが
無検査で通る。受容コスト: 読めない payload では Write / Edit / NotebookEdit が全て止まる。本 hook が守るのは
漏れたら取り返しがつかない機密ファイルで、素通しの損害が停止の損害を上回る。

書き込み先は Write / Edit が `file_path`、NotebookEdit が `notebook_path` で渡すので両方を見る。
登録した tool はどれもどちらかを持つので、どちらも空 / 不在なら payload が想定外の形になったとみて
同じく fail-closed で deny する。

照合は大文字小文字を区別しない — macOS の既定 file system は区別しないので、`.ENV` も `.env` を指す。
区別する file system (Linux 等) では `/SECRETS/` のような別 file まで止まるが、素通しより停止の側へ倒す。

Registration: hooks/hooks.json PreToolUse / Write|Edit|NotebookEdit matcher
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
    hook_name,
    passthrough,
    read_payload_or_deny,
    read_stdin_text,
    tool_input_text,
)

# `.env.<環境>` は file 名が `.env.` で始まるものだけを見る (`app.env.ts` のような source を巻き込まない)。
# 秘匿値を持たないものは外す: file 名の最後の語が雛形の接尾辞のもの (`.env.example` / `.env.local.example`。
# `.env.example.local` は環境ごとの実値なので外さない) と、公開鍵 (`id_rsa.pub` / `id_ed25519_sk.pub`)。
# 秘密鍵は file 名の先頭で見る (`test_valid_dsa.py` のような source を巻き込まない)。
# path の区切りはどの分岐も Windows の `\` を数える
SENSITIVE_PATH = re.compile(
    r"(^|[/\\])\.env\.(?!([^/\\]*\.)?(example|sample|template|dist|defaults)$)[^/\\]+$"
    r"|\.(env|key|pem|p12|pfx|crt)$"
    r"|(^|[/\\])id_(rsa|dsa|ecdsa|ed25519)(?![^/\\]*\.pub$)"
    r"|\.netrc|[/\\]credentials$|[/\\]secrets[/\\]",
    re.IGNORECASE,
)
# Windows は名前の末尾の `.` / 空白と `::$DATA` (本体の stream) を落として同じ file を開くので、落としてから照合する
WIN32_SAME_FILE_SUFFIX = re.compile(r"(::\$DATA|[. ])+$", re.IGNORECASE)


def judge(stdin_text: str) -> NoReturn:
    payload = read_payload_or_deny(stdin_text)
    paths = (tool_input_text(payload, "file_path"), tool_input_text(payload, "notebook_path"))
    if not any(paths):
        deny(f"書き込み先 (file_path / notebook_path) を payload から読めなかったため停止した (fail-closed) [{hook_name()}]")
    if any(matches_any_line(SENSITIVE_PATH, WIN32_SAME_FILE_SUFFIX.sub("", path)) for path in paths):
        deny("機密ファイルへの書き込みは禁止。値の書き換えは人に依頼する")
    passthrough()


def main(stdin_text: str) -> tuple[int, str]:
    """hook の純粋な入口。(exit code, stdout 本文) を返す。"""
    return decide(judge, stdin_text)


if __name__ == "__main__":
    exit_code, stdout_text = main(read_stdin_text())
    sys.stdout.write(stdout_text)
    sys.exit(exit_code)
