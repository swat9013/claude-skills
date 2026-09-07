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

fail-posture: **fail-open — ADR 0046 の「guard は原則 fail-closed」に対する明示的な例外**。
本 hook が原則から外れるのは、原則の根拠 (素通しの損害は不可逆・deny の損害は回復可能、と
いう非対称性) がここでは成立しないため。3 点で判断している:

  1. **停止範囲が過大**: 発火条件が `Bash|Edit|Write|Read` + SessionStart と全ツール級に広い。
     fail-closed にすると、状態ファイルが書けない / git 情報が取れないだけで Read まで含む
     ほぼ全操作が止まる。同じ posture でも sh guard 4 本は Bash 止まりで、こちらはそれより広い
  2. **守る対象が security ではない**: 塞いでいるのは main checkout への逸脱という **workflow
     規律**であって、bypass primitive でも権限昇格でもない。素通しで通るのは「正しい木の外で
     編集してしまう」ことであり、guard 機構が守る境界を越えるものではない
  3. **素通しの損害が回復可能**: 逸脱した編集は main checkout の working tree に残るだけで、
     git が全量を追跡している。取り消しも移送も後から効く

この 3 点が崩れたら (例: 発火条件を絞れる / 守る対象が security 相当になる) posture の
再評価が要る。原則の適用を免れているのは見落としではなく認定された例外である。

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


class _Finished(Exception):
    """判定が確定したことを main() へ運ぶ。stdout へ書く本文を持つ。

    判定関数が直接 stdout / exit を触らないのは、テストが hook を import して in-process で
    呼べるようにするため (subprocess 起動を省く)。process 境界に触るのは末尾の wrapper だけ。
    """

    def __init__(self, stdout_text: str) -> None:
        super().__init__(stdout_text)
        self.stdout_text = stdout_text


def passthrough(hook_event: str) -> None:
    """判定しない (通常の permission フローに委ねる)。

    `hook_event` は発火した event 名 (payload を読めるまでは空文字)。本 hook は SessionStart
    と PreToolUse の両方に登録されており、観測痕跡を出すかどうかをこれで決める。

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
    if hook_event == "PreToolUse":
        raise _Finished(
            '{"hookSpecificOutput":{"hookEventName":"PreToolUse"},"suppressOutput":true}\n'
        )
    raise _Finished("")


def emit_deny(tool: str, target: str, main_root: str, session_root: str) -> None:
    reason = (
        f"worktree セッション逸脱を検知: {tool} の対象 ({target}) は"
        f" main checkout ({main_root}) 配下です。このセッションの作業 root は"
        f" {session_root} です (既知の subagent path 解決バグの可能性)。"
        f" 対象 path を {session_root} 配下の絶対 path に置き換えて再実行してください"
        f" (Bash の場合はコマンド先頭に `cd {session_root} && ` を付ける)。"
    )
    raise _Finished(json.dumps(
        {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }},
        ensure_ascii=False,
    ))


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


def judge(stdin_text: str) -> None:
    """payload を判定し、確定したら _Finished を送出する (必ず送出して終わる)。"""
    hook_event = ""  # payload を読めるまでは event 不明 (痕跡を出さない)

    try:
        data = json.loads(stdin_text)
    except (ValueError, TypeError):
        passthrough(hook_event)
    if not isinstance(data, dict):
        passthrough(hook_event)

    hook_event = str(data.get("hook_event_name") or "")
    session_id = str(data.get("session_id") or "")
    cwd = str(data.get("cwd") or "")
    is_subagent = bool(data.get("agent_id") or data.get("agent_type"))
    cwd_root = worktree_root(cwd)

    if data.get("hook_event_name") == "SessionStart":
        if session_id and cwd_root:
            write_state(session_id, cwd_root)
        passthrough(hook_event)

    # メインセッションの cwd は正 → 状態を追従させる (ExitWorktree 後の自己修復)
    if not is_subagent and session_id and cwd_root:
        write_state(session_id, cwd_root)

    session_root = (read_state(session_id) if session_id else None) or cwd_root

    # 許可 root: セッションの正しい worktree + (isolation subagent 用) cwd の worktree
    allowed_roots = sorted(
        {r for r in (session_root, cwd_root) if r and is_linked_worktree(r)}
    )
    if not allowed_roots:
        passthrough(hook_event)  # worktree セッションでない → guard 対象外

    tool = str(data.get("tool_name") or "")
    tool_input = data.get("tool_input") or {}
    if tool in GUARDED_WRITE_TOOLS:
        raw_target = str(tool_input.get("file_path") or "")
        if not os.path.isabs(raw_target):
            passthrough(hook_event)  # 相対 path は解決先の確証なし
        target = os.path.realpath(raw_target)
    elif tool == "Bash":
        if not cwd:
            passthrough(hook_event)
        target = os.path.realpath(cwd)
    else:
        passthrough(hook_event)  # Read 等は状態更新のみで deny しない

    if any(is_under(target, root) for root in allowed_roots):
        passthrough(hook_event)
    for root in allowed_roots:
        main_root = main_checkout_root(root)
        if main_root and is_under(target, main_root):
            emit_deny(tool, target, main_root, root)
    passthrough(hook_event)


def main(stdin_text: str) -> tuple[int, str]:
    """hook の純粋な入口。(exit code, stdout 本文) を返す。"""
    try:
        judge(stdin_text)
    except _Finished as finished:
        return 0, finished.stdout_text
    raise AssertionError("judge() は必ず _Finished で終わる")


if __name__ == "__main__":
    exit_code, stdout_text = main(sys.stdin.read())
    sys.stdout.write(stdout_text)
    sys.exit(exit_code)
