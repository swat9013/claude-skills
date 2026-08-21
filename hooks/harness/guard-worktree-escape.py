#!/usr/bin/env python3
"""guard-worktree-escape.py — worktree セッションから main checkout への逸脱を deny する guard。

Claude Code には、git worktree 内のセッションから起動した subagent が path を
worktree ではなく main repository root で解決する既知バグがある
(anthropics/claude-code#29083 / #31546 / #44557)。subagent 側では hook 入力の
cwd 自体が汚染されうるため、cwd だけでは逸脱を判別できない。

そこで信頼できる唯一の情報源であるメインセッションの cwd を session_id キーの
状態ファイルに記録し (session_id は親と subagent で共通)、tool 呼び出し時に照合する:

  - SessionStart / メインセッション (agent_id 無し) の tool 呼び出し → 状態を更新
  - subagent (agent_id 有り) の tool 呼び出し → 状態を参照するのみ (汚染防止)

deny するのはバグの signature に一致する場合のみ:
  セッションの正しい root が linked worktree W のとき、
  - Edit/Write の file_path (realpath) が同一 repo の main checkout 配下かつ W 外
  - Bash の cwd (realpath) が同一 repo の main checkout 配下かつ W 外

以下はすべて素通し (fail-open):
  - isolation:"worktree" subagent 自身の worktree 配下への操作
    (cwd が別の linked worktree を指す場合はそれも許可 root に加える)
  - 別 repo / scratchpad / $HOME など main checkout 外への操作
  - Read / Glob / Grep (symlink 経由の reference Read は正規フロー。
    Read を matcher に含めるのは状態の鮮度維持のため)
  - git 情報が取れない・状態が無く cwd も worktree でない等、確証が持てないケース

限界: Bash のコマンド文字列内に埋め込まれた main checkout への絶対 path
(例: `cat /path/to/main/file`) までは検査しない。cwd の照合のみ行う。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

STATE_DIR_ENV = "WORKTREE_GUARD_STATE_DIR"
STATE_TTL_SECONDS = 7 * 24 * 3600
GUARDED_WRITE_TOOLS = ("Edit", "Write")

# 発火した event 名。payload を読めた時点で main() が設定し、passthrough() が観測痕跡を
# 出すかどうかの判定に使う (本 hook は SessionStart と PreToolUse の両方に登録されている)。
HOOK_EVENT = ""


def passthrough() -> None:
    """判定しない (通常の permission フローに委ねる)。

    PreToolUse で発火したときだけ pass の観測痕跡を 1 行出す (#587 / ADR 0043)。無出力の
    exit は transcript に attachment を残さず、棚卸しで「壊れて死んだ guard」と「窓内に
    出番が無かった guard」が同じ見え方になる。`permissionDecision` を持たない envelope は
    通常の permission フローへ委ねるので、判断の意味論は無出力のときと変わらない。

    **SessionStart は対象外**にする。本 hook は 2 event に登録されており、SessionStart の
    hook stdout は**モデルの文脈に入る** (実 transcript で確認済み) — PreToolUse と違って
    観測の代金を文脈汚染で払うことになるため、そちら側は無出力のままにする。payload を
    読めなかった経路も event が不明なので無出力 (痕跡は「hook が起動した」の証拠であって、
    判定まで到達した証拠ではない)。
    """
    if HOOK_EVENT == "PreToolUse":
        sys.stdout.write(
            '{"hookSpecificOutput":{"hookEventName":"PreToolUse"},"suppressOutput":true}\n'
        )
    sys.exit(0)


def emit_deny(tool: str, target: str, main_root: str, session_root: str) -> None:
    reason = (
        f"worktree セッション逸脱を検知: {tool} の対象 ({target}) は"
        f" main checkout ({main_root}) 配下です。このセッションの作業 root は"
        f" {session_root} です (既知の subagent path 解決バグの可能性)。"
        f" 対象 path を {session_root} 配下の絶対 path に置き換えて再実行してください"
        f" (Bash の場合はコマンド先頭に `cd {session_root} && ` を付ける)。"
    )
    json.dump(
        {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }},
        sys.stdout, ensure_ascii=False,
    )
    sys.exit(0)


def git_rev_parse(cwd: str, flag: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", flag],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def worktree_root(path: str) -> str | None:
    """path が属する working tree の root (realpath)。git 外なら None。"""
    if not path or not os.path.isdir(path):
        return None
    top = git_rev_parse(path, "--show-toplevel")
    return os.path.realpath(top) if top else None


def is_linked_worktree(root: str) -> bool:
    """root が linked worktree (git worktree add で作られた副 tree) なら True。"""
    git_dir = git_rev_parse(root, "--absolute-git-dir")
    common = git_rev_parse(root, "--git-common-dir")
    if not git_dir or not common:
        return False
    common_abs = os.path.realpath(os.path.join(root, common))
    return os.path.realpath(git_dir) != common_abs


def main_checkout_root(linked_root: str) -> str | None:
    """linked worktree が属する main checkout の root。bare repo 等は None。"""
    common = git_rev_parse(linked_root, "--git-common-dir")
    if not common:
        return None
    common_abs = os.path.realpath(os.path.join(linked_root, common))
    if os.path.basename(common_abs) != ".git":
        return None
    return os.path.dirname(common_abs)


def is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


# --- session 状態 (session_id → メインセッションの worktree root) ---------


def state_path(session_id: str) -> str | None:
    safe = "".join(c for c in session_id if c.isalnum() or c in "._-")
    if not safe:
        return None
    base = os.environ.get(STATE_DIR_ENV) or os.path.join(
        os.path.expanduser("~"), ".claude", "state", "worktree-guard")
    return os.path.join(base, safe)


def read_state(session_id: str) -> str | None:
    path = state_path(session_id)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            value = f.read().strip()
    except OSError:
        return None
    return value or None


def write_state(session_id: str, root: str) -> None:
    path = state_path(session_id)
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(root + "\n")
    except OSError:
        return  # 状態が書けなくても tool 実行は妨げない
    prune_stale_states(os.path.dirname(path))


def prune_stale_states(state_dir: str) -> None:
    """終了済みセッションの状態を掃除する (best-effort、失敗は無視)。"""
    cutoff = time.time() - STATE_TTL_SECONDS
    try:
        entries = list(os.scandir(state_dir))
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                os.unlink(entry.path)
        except OSError:
            continue


# --- main ----------------------------------------------------------------


def main() -> None:
    global HOOK_EVENT

    try:
        data = json.loads(sys.stdin.read())
    except (ValueError, TypeError):
        passthrough()
    if not isinstance(data, dict):
        passthrough()

    HOOK_EVENT = str(data.get("hook_event_name") or "")
    session_id = str(data.get("session_id") or "")
    cwd = str(data.get("cwd") or "")
    is_subagent = bool(data.get("agent_id") or data.get("agent_type"))
    cwd_root = worktree_root(cwd)

    if data.get("hook_event_name") == "SessionStart":
        if session_id and cwd_root:
            write_state(session_id, cwd_root)
        passthrough()

    # メインセッションの cwd は正 → 状態を追従させる (ExitWorktree 後の自己修復)
    if not is_subagent and session_id and cwd_root:
        write_state(session_id, cwd_root)

    session_root = (read_state(session_id) if session_id else None) or cwd_root

    # 許可 root: セッションの正しい worktree + (isolation subagent 用) cwd の worktree
    allowed_roots = sorted(
        {r for r in (session_root, cwd_root) if r and is_linked_worktree(r)}
    )
    if not allowed_roots:
        passthrough()  # worktree セッションでない → guard 対象外

    tool = str(data.get("tool_name") or "")
    tool_input = data.get("tool_input") or {}
    if tool in GUARDED_WRITE_TOOLS:
        raw_target = str(tool_input.get("file_path") or "")
        if not os.path.isabs(raw_target):
            passthrough()  # 相対 path は解決先の確証なし
        target = os.path.realpath(raw_target)
    elif tool == "Bash":
        if not cwd:
            passthrough()
        target = os.path.realpath(cwd)
    else:
        passthrough()  # Read 等は状態更新のみで deny しない

    if any(is_under(target, root) for root in allowed_roots):
        passthrough()
    for root in allowed_roots:
        main_root = main_checkout_root(root)
        if main_root and is_under(target, main_root):
            emit_deny(tool, target, main_root, root)
    passthrough()


if __name__ == "__main__":
    main()
