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
      - deny prefix (自動発火 path) → deny (reason label 付き)
      - それ以外 → passthrough (既存 ask フローに委ねる)

Registration: hooks/hooks.json PreToolUse / Bash matcher / if: Bash(chmod:*)
"""
from __future__ import annotations

import fnmatch
import json
import os
import shlex
import subprocess
import sys

DENY_REASON_LABEL = "chmod-x-auto-trigger-path"


def passthrough() -> None:
    """判断を出さずに終了する (既存 ask フローに委ねる)。

    判断は出さないが観測痕跡は残す (#587 / ADR 0043)。無出力の exit は transcript に
    attachment を残さず、棚卸しで「壊れて死んだ guard」と「窓内に出番が無かった guard」が
    同じ見え方になる。`permissionDecision` を持たない envelope は通常の permission フローへ
    委ねるので、判断の意味論は無出力のときと変わらない。逐語で 1 行に保つ (テストが全 guard
    の一致を見る)。
    """
    sys.stdout.write(
        '{"hookSpecificOutput":{"hookEventName":"PreToolUse"},"suppressOutput":true}\n'
    )
    sys.exit(0)


def emit_deny(reason: str) -> None:
    json.dump(
        {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"guard: {DENY_REASON_LABEL}: {reason}",
        }},
        sys.stdout, ensure_ascii=False,
    )
    sys.exit(0)


def emit_allow() -> None:
    json.dump(
        {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }},
        sys.stdout, ensure_ascii=False,
    )
    sys.exit(0)


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


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (ValueError, TypeError):
        passthrough()
    if not isinstance(data, dict):
        passthrough()

    tool_input = data.get("tool_input") or {}
    command = str(tool_input.get("command") or "")
    if not command:
        passthrough()

    try:
        tokens = shlex.split(command)
    except ValueError:
        # unbalanced quotes 等の parse 不能: fail-open で ask に委ねる
        passthrough()
    if not tokens:
        passthrough()

    parsed = parse_chmod(tokens)
    if parsed is None:
        passthrough()
    mode, paths, recursive = parsed

    if not mode_adds_execute(mode):
        passthrough()

    cwd = str(data.get("cwd") or "")
    repo_root = repo_root_of(cwd)
    home = os.path.expanduser("~")

    resolved = [resolve_path(p, cwd) for p in paths]
    verdicts = [classify(p, repo_root, home, plugin_root()) for p in resolved]

    # deny 優先: 1 つでも deny prefix にヒットしたら deny (原因 path を明示)
    for path, v in zip(resolved, verdicts):
        if v == "deny":
            emit_deny(
                f"{path} は自動発火 path 配下 (`.git/hooks` / LaunchAgents / "
                f"~/.claude/hooks / /etc/cron.* 等) のため +x 禁止"
            )

    # `-R` は allow prefix でも auto-allow しない (再帰配下の symlink や submodule
    # 越しに想定外の file まで +x される可能性を排除)
    if recursive:
        passthrough()

    # 全 path が allow prefix にマッチしたときのみ auto allow
    if all(v == "allow" for v in verdicts):
        emit_allow()

    passthrough()


if __name__ == "__main__":
    main()
