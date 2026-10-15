#!/usr/bin/env python3
"""guard-chmod-x.py — PreToolUse / Bash: `chmod +x` を path で allow/deny/passthrough に分岐する。

Why: PR #275 で `Bash(chmod +x:*)` 一律 allow を revert した (commit 08798b3)。
    「chmod +x したスクリプトの実行は Bash allow で別途ゲートされる」二重ゲート論は
    Claude Code セッション内に閉じており、以下 2 経路を防げない。

    1. セッション外の自動発火 path: .git/hooks/*, ~/Library/LaunchAgents/*,
       ~/.claude/hooks/*, /etc/cron.*/* に +x されると次の commit / システム起動 /
       harness hook 発火で自動実行される。Bash allow ゲートは通らない。
    2. 広い allow pattern への相乗り: 将来 `Bash(uv run --with pytest:*)` のような
       広い pattern が入った場合、+x した script を pattern の到達範囲に置けば
       実行 allow に相乗りする。

    そこで chmod 自体を PreToolUse で捕捉し、path prefix で 3 分岐する:
      - allow prefix (本 plugin 配下 / repo scripts / .githooks / hooks) → auto allow
        (改行で続けた 2 行目を持つ command は除く — #1698)
      - deny prefix (自動発火 path) → deny (reason label 付き)
      - それ以外 → passthrough (既存 ask フローに委ねる)

fail-posture: **fail-closed** (ADR 0046)。payload を読めない / コマンドを tokenize できない
    ときは判断保留 = 素通しにせず deny する。guard は deny 権能を持つ以上原則 fail-closed で、
    fail-open を選ぶには「素通しの損害が回復可能であること」を示す脅威モデルを要する。本 hook に
    それは無い — `+x` の付与は後続実行を解禁する security 寄りの権能で、素通しの損害
    (自動発火 path への +x) は不可逆であるのに対し、deny の損害は「chmod が 1 回止まる」だけ。
    停止範囲も発火条件 `Bash(chmod:*)` に閉じており、全 Bash には波及しない。

    deny するのは payload / コマンドを**読めなかった**ときだけ。読めたうえで「chmod ではない」
    「`+x` を付けない」「allow / deny のどちらの prefix でもない」は判定結果なので従来どおり
    passthrough する (検知ロジックは ADR 0046 のスコープ外)。

Registration: hooks/hooks.json PreToolUse / Bash matcher / if: Bash(chmod:*)
"""
from __future__ import annotations

import fnmatch
import os
import shlex
import subprocess
import sys
from typing import NoReturn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _prelude import (  # noqa: E402
    allow,
    continues_on_another_line,
    decide,
    deny,
    hook_name,
    passthrough,
    read_payload_or_deny,
    read_stdin_text,
    tool_input_text,
)

DENY_REASON_LABEL = "chmod-x-auto-trigger-path"


# --- chmod command parse ----------------------------------------------------


def parse_chmod(tokens: list[str]) -> tuple[str, list[str], bool] | None:
    """(mode, paths, recursive) を返す。chmod 呼び出しでなければ None。"""
    i = 0
    # optional `sudo` (with its flags; `-u USER` / `-g GROUP` は次トークンを消費)
    if i < len(tokens) and tokens[i] == "sudo":
        i += 1
        while i < len(tokens) and tokens[i].startswith("-"):
            if tokens[i] in ("-u", "-g", "-p", "-C", "-D", "-h", "-r", "-t") and i + 1 < len(tokens):
                i += 2
            else:
                i += 1
    if i >= len(tokens):
        return None
    if os.path.basename(tokens[i]) != "chmod":
        return None
    i += 1
    recursive = False
    # flags (chmod は - or + で始まる引数を持つが、+ / = で始まるものは symbolic mode)
    while i < len(tokens) and tokens[i].startswith("-") and tokens[i] != "--":
        tok = tokens[i]
        if tok == "--recursive" or (len(tok) >= 2 and tok[1] != "-" and "R" in tok[1:]):
            recursive = True
        i += 1
    if i < len(tokens) and tokens[i] == "--":
        i += 1
    if i >= len(tokens):
        return None
    mode = tokens[i]
    i += 1
    # mode と path の間にも `--` が入りうる (POSIX 一般的には flag セクションの
    # 直後だが、実装により mode 後の `--` も許容)
    if i < len(tokens) and tokens[i] == "--":
        i += 1
    paths = tokens[i:]
    if not paths:
        return None
    return mode, paths, recursive


def mode_adds_execute(mode: str) -> bool:
    """symbolic mode に `+x` 系の execute bit 追加が含まれるか。

    対応: `+x` / `a+x` / `u+x` / `ug+x` / `+rwx` / `+wx` / `u+xw` 等の任意順、
    複数 clause (`,` 区切り) のいずれかに含まれば True。数値モード (755 等) は
    範囲外 (issue #279 spec に合わせる)。
    """
    for clause in mode.split(","):
        # 各 clause 内の `+` から次の非 perm 文字 (or 末尾) までに `x` が含まれるか
        idx = 0
        while True:
            plus = clause.find("+", idx)
            if plus < 0:
                break
            j = plus + 1
            while j < len(clause) and clause[j] in "rwxXstugo":
                j += 1
            if "x" in clause[plus + 1: j]:
                return True
            idx = j
    return False


# --- path classification ----------------------------------------------------


def resolve_path(raw: str, cwd: str) -> str:
    """~ 展開 + cwd 基準の絶対化 + normpath。存在は問わない (chmod は新規 file 相手にも打てる)。"""
    p = os.path.expanduser(raw)
    if not os.path.isabs(p):
        p = os.path.join(cwd, p) if cwd else p
    return os.path.normpath(p)


def repo_root_of(cwd: str) -> str | None:
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def contains_segment(path: str, segments: tuple[str, ...]) -> bool:
    """path に `.git/hooks/` のような連続 segment が含まれるか。

    `.git/hooks/**` 判定に使う (worktree 検出 + submodule / 別 repo でも安全側)。
    """
    parts = path.split(os.sep)
    n = len(segments)
    for i in range(len(parts) - n + 1):
        if tuple(parts[i:i + n]) == segments:
            return True
    return False


def plugin_root() -> str:
    """本 hook を含む plugin の root (= `${CLAUDE_PLUGIN_ROOT}` の実体)。

    hooks.json は `"${CLAUDE_PLUGIN_ROOT}"/hooks/harness/<file>` で起動するため、
    自ファイルの 3 階層上が plugin root になる。install 形態 (skills ディレクトリ
    プラグインの symlink / marketplace install / repo 直) を問わず一意に解決される。

    realpath は取らない: chmod 対象 path 側も expanduser のみで正規化する
    (resolve_path) ため、symlink を解決すると両者の表記系がずれて突合できない。
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def classify(path: str, repo_root: str | None, home: str, plugin: str) -> str:
    """path を "allow" / "deny" / "neutral" に分類。deny 判定を優先する。"""
    # --- deny prefixes ---
    if contains_segment(path, (".git", "hooks")):
        return "deny"
    if is_under(path, os.path.join(home, "Library", "LaunchAgents")):
        return "deny"
    if is_under(path, os.path.join(home, "Library", "LaunchDaemons")):
        return "deny"
    if is_under(path, os.path.join(home, ".claude", "hooks")):
        return "deny"
    if fnmatch.fnmatch(path, "/etc/cron.*") or fnmatch.fnmatch(path, "/etc/cron.*/*"):
        return "deny"
    if path == os.path.join(home, ".crontab"):
        return "deny"

    # --- allow prefixes ---
    if is_under(path, plugin):
        return "allow"
    if repo_root:
        if is_under(path, os.path.join(repo_root, "scripts")):
            return "allow"
        if is_under(path, os.path.join(repo_root, ".githooks")):
            return "allow"
        if is_under(path, os.path.join(repo_root, "hooks")):
            return "allow"

    return "neutral"


# --- main -------------------------------------------------------------------


def judge(stdin_text: str) -> NoReturn:
    payload = read_payload_or_deny(stdin_text)

    # command が空 / 不在なのは正常系 (読めている) なので passthrough。
    command = tool_input_text(payload, "command")
    if not command:
        passthrough()

    try:
        tokens = shlex.split(command)
    except ValueError:
        # unbalanced quotes 等でコマンドを tokenize できない = 読み取り失敗。素通しにすると
        # `chmod +x '<自動発火 path>` が無検査で通るので deny する (ADR 0046)。
        deny(
            "コマンドを tokenize できないため停止した (fail-closed): "
            f"quote の対応が取れていない [{hook_name()}]"
        )
    if not tokens:
        passthrough()

    parsed = parse_chmod(tokens)
    if parsed is None:
        passthrough()
    mode, paths, recursive = parsed

    if not mode_adds_execute(mode):
        passthrough()

    cwd = str(payload.get("cwd") or "")
    repo_root = repo_root_of(cwd)
    home = os.path.expanduser("~")

    resolved = [resolve_path(p, cwd) for p in paths]
    verdicts = [classify(p, repo_root, home, plugin_root()) for p in resolved]

    # deny 優先: 1 つでも deny prefix にヒットしたら deny (原因 path を明示)
    for path, v in zip(resolved, verdicts):
        if v == "deny":
            deny(
                f"guard: {DENY_REASON_LABEL}: {path} は自動発火 path 配下 "
                f"(`.git/hooks` / LaunchAgents / ~/.claude/hooks / /etc/cron.* 等) のため +x 禁止"
            )

    # `-R` は allow prefix でも auto-allow しない (再帰配下の symlink や submodule
    # 越しに想定外の file まで +x される可能性を排除)
    if recursive:
        passthrough()

    # 改行の後の行は別のコマンドとして走るので auto allow しない (#1698)。shlex は改行を語の
    # 区切りとしか見ず、2 行目の語 (+x した script の起動など) が chmod の path に混ざる。
    # deny は上で済ませてあるので、外れるのは allow だけ
    if continues_on_another_line(command):
        passthrough()

    # 全 path が allow prefix にマッチしたときのみ auto allow
    if all(v == "allow" for v in verdicts):
        allow("chmod +x の対象が全て allow prefix 配下")

    passthrough()


def main(stdin_text: str) -> tuple[int, str]:
    """hook の純粋な入口。(exit code, stdout 本文) を返す。"""
    return decide(judge, stdin_text)


if __name__ == "__main__":
    exit_code, stdout_text = main(read_stdin_text())
    sys.stdout.write(stdout_text)
    sys.exit(exit_code)
