#!/usr/bin/env python3
"""allow-tmp-delete.py — PreToolUse / Bash: tmp ルート配下のみの rm を allow する許可専用 hook。

fail-safe: 静的に安全と確証できないケースは一切 allow せず passthrough() で素通しし、通常の
permission フロー (settings の ask / auto mode classifier) に委ねる。payload を読めないときも
同じ (`read_payload_or_pass()`。allow-hook は fail-safe 固定、ADR 0046)。rm を deny する guard は
持たない (ADR 0077 の基準で削除)。

承認ルート: /tmp /private/tmp $HOME/.claude/tmp <cwd>/tmp の各「配下」(ルート自体は対象外)。
recursive 削除 (-rf) 可。glob/変数/置換/quote/未知 flag/tmp 外混在/.. 脱出/ルート自体/
改行で続けた 2 行目 は素通し。

受理する rm の綴り (リテラル一致のみ): `rm` / `/bin/rm` / `/usr/bin/rm`。それぞれ先頭に単一
token `command` を伴ってよい。allow は「その実行ファイルを承認なしで起動する」決定なので、
basename が rm というだけの任意パス (`./rm` / `/opt/x/bin/rm` 等) は受理しない。`\\rm` /
`env rm` / `sudo rm` も素通し。

hooks.json 側の `if` gate は付けない。gate は token 境界照合のため `/bin/rm` を弾いてしまい、
綴り違いで自動許可を失う (issue #361)。判定は本 script に一本化する。

判定は shell の意味論に揃える: 語の分割は空白・tab・改行だけ、
path の解決は rm が実際に辿る物理的な解決 (`..` は直前の symlink を解決した先の親。字面で畳まない。
末尾 `/` 付きの target は最後の component の symlink も辿った先の dir で判定する)。

Registration: hooks/hooks.json PreToolUse / Bash matcher (`if` 無し)
"""
from __future__ import annotations

import os
import re
import sys
from typing import NoReturn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _prelude import (  # noqa: E402
    allow,
    continues_on_another_line,
    decide,
    matches_any_line,
    passthrough,
    read_payload_or_pass,
    read_stdin_text,
    substitute_lines,
    tool_input_text,
)

# 連結 (`|` `&` `;`) / リダイレクト / 変数展開・コマンド置換 / glob / quote / backslash を
# 含むなら素通しする。tokenize を単純な空白分割に留めるための前提
UNSUPPORTED_SHELL_SYNTAX = re.compile(r"[|&;<>$`*?\[\"'\\]")
LEADING_SPACE = re.compile(r"^\s+")
WORD_SEPARATOR = re.compile(r"[ \t\n]+")
RM_SPELLINGS = frozenset({"rm", "/bin/rm", "/usr/bin/rm"})
KNOWN_LONG_FLAGS = frozenset({"--recursive", "--force", "--verbose", "--interactive", "--dir"})
SHORT_FLAG_CLUSTER = re.compile(r"^-[rRfivd]+$")


def rm_targets(command: str) -> list[str] | None:
    """受理する綴りの rm なら削除対象の語を返す。受理できない形なら None。"""
    if matches_any_line(UNSUPPORTED_SHELL_SYNTAX, command):
        return None
    # `sed 's/^[[:space:]]*//'` と同じく各行の行頭の空白を落としてから、IFS (空白・tab・改行) で分割する
    trimmed = substitute_lines(LEADING_SPACE, "", command)
    words = [word for word in WORD_SEPARATOR.split(trimmed) if word]
    if words[:1] == ["command"]:
        words = words[1:]
    if not words or words[0] not in RM_SPELLINGS:
        return None
    targets: list[str] = []
    after_double_dash = False
    for word in words[1:]:
        if after_double_dash:
            targets.append(word)
        elif word == "--":
            after_double_dash = True
        elif word in KNOWN_LONG_FLAGS:
            continue
        elif word.startswith("--"):
            return None  # 未知 long flag
        elif word.startswith("-"):
            if not SHORT_FLAG_CLUSTER.match(word):
                return None  # "-" 単体 or 許可外文字
        else:
            targets.append(word)
    return targets


def physical_directory(path: str) -> str | None:
    """kernel が path を先頭から辿って行き着くディレクトリ。辿れない・入れないなら None。

    rm は path を kernel にそのまま渡すので、`..` は直前の component の symlink を解決した先の
    親を指す (#1691)。`..` を字面で先に畳むと、`/tmp/<tmp の外を指す symlink>/..` が `/tmp` に
    見えて、実際に消える場所 (symlink 先の親) と食い違う。
    """
    if not (os.path.isdir(path) and os.access(path, os.X_OK)):
        return None
    return os.path.realpath(path)


def posix_dirname(path: str) -> str:
    stripped = path.rstrip("/") or "/"
    if stripped == "/":
        return "/"
    head = stripped.rsplit("/", 1)[0] if "/" in stripped else "."
    return head.rstrip("/") or "/"


def posix_basename(path: str) -> str:
    stripped = path.rstrip("/")
    return stripped.rsplit("/", 1)[-1] if stripped else "/"


def approved_roots(home: str, cwd: str) -> list[str]:
    candidates = ["/tmp", "/private/tmp"]
    if home:
        candidates.append(home + "/.claude/tmp")
    if cwd:
        candidates.append(_strip_one_trailing_slash(cwd) + "/tmp")
    return [root for root in map(physical_directory, candidates) if root is not None]


def is_under_tmp_root(target: str, home: str, cwd: str, roots: list[str]) -> bool:
    if target in ("~", "~/"):
        return False  # home 自体は対象外
    if target.startswith("~/"):
        target = home + "/" + target[2:]
    elif target.startswith("~"):
        return False  # ~user 非対応
    if not target.startswith("/"):
        if not cwd:
            return False
        target = _strip_one_trailing_slash(cwd) + "/" + target
    if posix_basename(target) in (".", ".."):
        return False
    location = _removed_location(target)
    return location is not None and any(location.startswith(root + "/") for root in roots)


def _removed_location(target: str) -> str | None:
    """rm が実際に消す場所の物理的な path。辿れないなら None。

    末尾 `/` があると rm は最後の component の symlink も辿り、辿った先の dir を消すので、
    target 自身を解決する。無ければ最後の component は辿らないので、親だけを解決して名前を足す。
    """
    if target.endswith("/"):
        return physical_directory(target)
    parent = physical_directory(posix_dirname(target))
    return None if parent is None else parent.rstrip("/") + "/" + posix_basename(target)


def _strip_one_trailing_slash(path: str) -> str:
    """sh の `${CWD%/}` — 末尾の `/` を 1 つだけ落とす。"""
    return path[:-1] if path.endswith("/") else path


def judge(stdin_text: str) -> NoReturn:
    payload = read_payload_or_pass(stdin_text)
    command = tool_input_text(payload, "command")
    cwd = str(payload.get("cwd") or "").rstrip("\n")
    # 2 行目は別のコマンドとして走るので allow しない (#1698)。語の分割は改行も区切りに含めるので、
    # 通すと 2 行目の語 (`git push ...`) が cwd 相対の削除対象に見え、cwd が tmp 配下なら allow される
    if continues_on_another_line(command):
        passthrough()
    targets = rm_targets(command)
    if not targets:
        passthrough()
    home = os.environ.get("HOME", "")
    roots = approved_roots(home, cwd)
    if not roots:
        passthrough()
    if all(is_under_tmp_root(target, home, cwd, roots) for target in targets):
        allow("tmp ルート配下の削除を自動許可")
    passthrough()


def main(stdin_text: str) -> tuple[int, str]:
    """hook の純粋な入口。(exit code, stdout 本文) を返す。"""
    return decide(judge, stdin_text)


if __name__ == "__main__":
    exit_code, stdout_text = main(read_stdin_text())
    sys.stdout.write(stdout_text)
    sys.exit(exit_code)
