#!/usr/bin/env python3
"""guard-shell.py — PreToolUse / Bash(bash|sh|zsh:*): bash/sh/zsh の直接実行を deny し、構文チェックとスクリプト起動を書き換える。

置く理由: 抜け道を塞ぐ・補正
    判定ごとに理由が違う (ADR 0108)。stdin script (`bash <<EOF`) の deny は、permission rule が
    中身を検査しない経路を止める抜け道を塞ぐ側。script の直接実行は塞ぐ側に加えて補正でもある —
    `./x.sh` で同じ効果が出るので、単一コマンドで interpreter の直後に path を明示した形 (`bash ./x.sh` /
    `bash ../x.sh` / `bash /abs/x.sh`) は直接起動へ書き換え、それ以外 (bare name の `bash x.sh`・複合行・
    リダイレクト付き・option が前に付く `bash -x ./x.sh`) は deny の
    理由文で直接パス起動へ案内して、正しい綴りへ直す。

fail-posture: **fail-closed** (#250 決定 3 / #596 / ADR 0046)。payload を読めなかったときは
判断保留 = 素通しにせず deny する。読み取り失敗を素通しにすると command が空文字になり、
下の全判定が不成立のまま passthrough して bash/sh/zsh の直接実行が無検査で通る。
受容コスト: hooks.json の gate (`if: Bash(bash|sh|zsh:*)`) に載る呼び出しが全て止まる。
**既定 deny の直接起動だけでなく、Claude Code の matcher が Bash(sh:*) へ過剰マッチさせる
for/while ループ (本来 passthrough) も巻き添えで deny される**。gate があるので Bash 全体は
止まらない。

判定の regex は `grep -E` / `sed -E` (POSIX ERE) と同じ判定・置換になるように書き、行単位の照合と文字クラスは
`_prelude.matches_any_line` / `substitute_lines` が grep / sed に揃える。

Registration: hooks/hooks.json PreToolUse / Bash matcher / if: Bash(bash:*) / Bash(sh:*) / Bash(zsh:*)
"""
from __future__ import annotations

import os
import re
import sys
from typing import NoReturn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _prelude import (  # noqa: E402
    allow,
    allow_rewritten,
    continues_on_another_line,
    decide,
    deny,
    matches_any_line,
    passthrough,
    read_payload_or_deny,
    read_stdin_text,
    rewrite,
    strip_here_documents,
    substitute_lines,
    tool_input_text,
)

# bash/sh/zsh -n <file> は構文チェックのみ (実行しない) で安全。allow 済の syntax-check.sh
# wrapper へ変換して pre-sanction 枠へ誘導する (permission=意図表明 / hook=変換)。settings.deny の
# Bash(bash|sh|zsh:*) は hook の allow より優先されるため、deny にマッチしない wrapper 経由で
# native の -n を実行させる。wrapper が第1引数を bash/sh/zsh に限定し踏み台化を防ぐ。
#
# wrapper は harness 外 (利用者環境の設定資産) にあり、本 hook 単体では同梱できない。
# 不在環境では rewrite せず素の `-n` をそのまま allow する: `-n` は構文解析のみで
# スクリプトを実行しないため、wrapper 経由でなくとも安全側に閉じている。
# (SHELL_SYNTAX_CHECK_BIN はテスト注入点。emit する command は tilde 表記のまま — 利用者の
#  permission entry がテキスト一致で照合するため、絶対 path へ展開してはならない)
SYNTAX_CHECK_TILDE = "~/.claude/scripts/syntax-check.sh"

# 以下 2 つの allow 分岐は「単一コマンドである」ことを前提にする。区切り (`;` `&` `|` 改行) /
# 置換 (`$(` backtick) / リダイレクト / subshell を含む場合は allow せず、下の複合行 rewrite
# または deny 判定へ落とす。`bash -n a.sh;whoami` のような追い焚きを 1 つの allow で通さない
# ため。hook の allow は deny / ask rule までは越えない (公式 permissions: "Claude Code
# evaluates deny and ask rules regardless of what a PreToolUse hook returns") が、どちらの
# rule も持たないコマンドは素通りするので、allow を出す範囲は単一コマンドに絞る。
COMPOUND = re.compile(r"[;&|<>`$()]")
# 置換・リダイレクトの有無だけを別に見る (区切り `;` `&` `|` は含めない)。下の複合行 rewrite は
# 区切りを許容するが、置換 (`$(` backtick) とリダイレクトは許容しない — 引数の中に紛れ込むと
# 書き換え後も残り、permission 層が読めない位置でコマンドが走るため
# (`bash -n 'a.sh`whoami`'` のような綴り)。
SUBSTITUTION = re.compile(r"[<>`$()]")

SYNTAX_CHECK = re.compile(r"^(bash|sh|zsh) -n [^ ]+$")
SYNTAX_CHECK_PREFIX = re.compile(r"^(bash|sh|zsh) -n ")
COMPOUND_SYNTAX_CHECK = re.compile(r"(^|[;&|])\s*(bash|sh|zsh) -n [^ ;&|]+")
COMPOUND_SYNTAX_CHECK_PREFIX = re.compile(r"(^|[;&|]\s*)(bash|sh|zsh) -n ")
SCRIPT_PATH_INVOCATION = re.compile(r"^(bash|sh|zsh) +(/|\./|\.\./)")
INTERPRETER_PREFIX = re.compile(r"^(bash|sh|zsh) +")
# bash/sh/zsh を「コマンド位置」(単純コマンドの先頭語) で直接起動する形。interpreter が
# 文頭 or 区切り (`;` `&` `|` `(` `{` backtick、`&&`/`||` 含む) の直後にあり、間に他の
# コマンド語が無いこと。任意の path 接頭辞 (`/bin/`, `/usr/bin/` 等) は許容し `/bin/sh` も
# 捕捉する。引数・文字列位置のトークン (`grep bash` `man zsh` `commit -m "...bash..."`) と
# `foo.sh` 等のスクリプトパス引数、制御構文 (for/while ループ) は誤検知しない。trailing は
# 語境界 (`bashx`/`shell` を除外。`[^\w.-]` は grep の `[^[:alnum:]_.-]` に当たる)。
DIRECT_INVOCATION = re.compile(r"(^|[;&|({`])\s*([^\s;&|()]*/)?(bash|sh|zsh)([^\w.-]|$)")


def _each_line(transform, text: str) -> str:
    """`printf '%s\\n' "$TEXT" | <行ごとの変換>` を `$(...)` で受けた値 (末尾の改行は剥がれる)。"""
    return "\n".join(transform(line) for line in text.split("\n")).rstrip("\n")


def syntax_check_bin() -> str:
    """wrapper の実体の path。tilde 表記を `$HOME` で展開する (`SHELL_SYNTAX_CHECK_BIN` が注入点)。"""
    injected = os.environ.get("SHELL_SYNTAX_CHECK_BIN")
    if injected:
        return injected
    return os.environ.get("HOME", "") + "/" + SYNTAX_CHECK_TILDE[len("~/"):]


def judge(stdin_text: str) -> NoReturn:
    payload = read_payload_or_deny(stdin_text)
    # 末尾の改行は落とす (shell の `$(...)` と同じ)。書き換えの出力に効く
    command = tool_input_text(payload, "command").rstrip("\n")
    wrapper_exists = os.access(syntax_check_bin(), os.X_OK)
    # COMPOUND は行ごとに照合するので改行を見ない。改行の後の行は別のコマンドとして走るため、
    # 2 行目を持つ command も単一コマンドとみなさない (#1698)
    single_command = not (matches_any_line(COMPOUND, command) or continues_on_another_line(command))
    has_substitution = matches_any_line(SUBSTITUTION, command)

    if single_command and matches_any_line(SYNTAX_CHECK, command):
        if wrapper_exists:
            shell = _each_line(lambda line: line.split(" ", 1)[0], command)
            target = substitute_lines(SYNTAX_CHECK_PREFIX, "", command).rstrip("\n")
            allow_rewritten({"command": f"{SYNTAX_CHECK_TILDE} {shell} {target}"})
        allow("bash/sh/zsh -n は構文チェックのみで実行しないため許可")

    # 複合行に混ざった `<shell> -n <file>` も wrapper 形へ書き換える。`bash -n a.sh && shellcheck a.sh`
    # のように構文チェックを他の検査と 1 行に繋ぐのはモデルの自然な綴りだが、上の単一コマンド分岐は
    # 区切りを含む行を対象外にするため deny へ落ちていた (観測窓 30 日で bash -n 36 件 / zsh -n 12 件)。
    # 危険だったのは「1 つの allow で行全体を通す」ことのほうなので、ここでは **allow を返さず
    # rewrite だけ返す**。permission 評価は書き換え後の行に対して通常どおり走る。
    #
    # 書き換え後に interpreter の直接起動が残る場合 (`bash -n a.sh && bash deploy.sh` 等) は
    # rewrite せず下の deny へ落とす。wrapper 不在環境も同様 — 書き換え先が無い以上、deny を
    # 回避する手段が無い (hook の allow は permission の deny を越えられない)。
    if (not single_command and not has_substitution and wrapper_exists
            and matches_any_line(COMPOUND_SYNTAX_CHECK, command)):
        rewritten = substitute_lines(COMPOUND_SYNTAX_CHECK_PREFIX, rf"\1{SYNTAX_CHECK_TILDE} \2 ", command)
        rewritten = rewritten.rstrip("\n")
        if not matches_any_line(DIRECT_INVOCATION, rewritten):
            rewrite({"command": rewritten})

    # bash/sh/zsh <script> で「絶対パス (/) または明示相対 (./ ../) のスクリプトファイル」を実行
    # する場合は、deny でなく interpreter prefix を除去した直接パス起動へ書き換えて allow する
    # (permission=意図表明 / hook=変換)。bare name (`bash deploy.sh`) は PATH 検索/cwd 依存で意味が
    # 変わるため対象外とし下の deny で `./` 付き起動を案内する。
    if single_command and matches_any_line(SCRIPT_PATH_INVOCATION, command):
        allow_rewritten({"command": substitute_lines(INTERPRETER_PREFIX, "", command).rstrip("\n")})

    # here-document の本体は実行されないので、掴むと誤 deny になるだけで防御にならない。
    # 本体を落とした写しは **deny の判定にだけ使う** — 上の allow / rewrite に使うと、本体に
    # 紛れた綴りで許可の射程が広がる。
    if matches_any_line(DIRECT_INVOCATION, strip_here_documents(command)):
        deny(
            "bash/sh/zshの直接実行は禁止。スクリプトの実行は直接パス起動（./script.sh または "
            "/abs/path/script.sh）を使用。構文チェックは bash -n <file> / zsh -n <file>。"
        )

    # bash/sh/zsh の直接起動を含まない (for/while ループ等の制御構文) → passthrough
    passthrough()


def main(stdin_text: str) -> tuple[int, str]:
    """hook の純粋な入口。(exit code, stdout 本文) を返す。"""
    return decide(judge, stdin_text)


if __name__ == "__main__":
    exit_code, stdout_text = main(read_stdin_text())
    sys.stdout.write(stdout_text)
    sys.exit(exit_code)
