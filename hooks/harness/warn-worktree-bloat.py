#!/usr/bin/env python3
"""warn-worktree-bloat.py — SessionStart hook。worktree 蓄積を E2BIG 発症前に警告する。

`.claude/worktrees/` に worktree が溜まると Bash sandbox profile の filesystem deny
paths が worktree 数に比例して肥大し、spawn 時の argv + env + profile が ARG_MAX を
超えて `E2BIG: argument list too long` で全 Bash が死ぬ (subagent 含む。実績 8〜9 個で
発症、2026-07-03 以降に複数回再発)。失敗は PreToolUse hook より前の kernel レベルで
起きるため発症後はセッション内から回復できず、唯一の介入点がセッション開始時の事前警告。

仕様 (issue #425):
- 数えるのは main working tree を除いた **linked worktree の数**。prunable な zombie も
  sandbox profile には効くので数に含める (`git worktree list` は prunable も列挙する)。
- 5 個以上で警告、7 個以上で強警告を additionalContext に注入する。4 個以下は
  `suppressOutput` のみ返す (「hook は走って警告不要と判定した」を出力で示す)。
- 掃除経路は 2 つ。**発症前の予防掃除**は dispatch-ops MCP server の `worktree_sweep`
  tool が session 内から使える (server プロセスは Bash sandbox の外で `git worktree
  remove` を `.git/worktrees/` の削除まで完遂できる — 実測 2026-08-03)。**発症後の回復**
  は Claude Code の外の別ターミナルのみ。`.git/worktrees/` の削除は sandbox の
  denyWithinAllow で保護されており、Bash tool 経由 (`!` prefix 含む — 実測 2026-08-01、
  拒否パターンが Bash tool と完全一致) では完遂できない。E2BIG 発症後に MCP tool が
  動くかは未実測のため、発症後の経路として案内しない。
- git が引けないときは黙って exit 0 (hook が session を壊さない)。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

WARN_THRESHOLD = 5
STRONG_THRESHOLD = 7

MECHANISM = (
    "worktree が増えるほど Bash sandbox profile が肥大し、ARG_MAX 超過で全 Bash が "
    "E2BIG (argument list too long) で起動不能になる。発症後はセッション内から回復不能。"
)
CLEANUP = (
    "予防掃除 (発症前): dispatch-ops MCP server の worktree_sweep tool を dry_run で"
    "確認してから実行する (session 内から完遂できる — 実測 2026-08-03)。"
    "発症後の回復: Claude Code の外の別ターミナルで `git worktree remove --force <path>` → "
    "`git worktree prune` を実行し、Claude Code を再起動する "
    "(session 内の Bash は sandbox 保護で完遂できない)。"
)


def count_linked_worktrees(cwd: str) -> int | None:
    """main を除いた登録 worktree 数。git が引けなければ None。"""
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    total = sum(1 for line in proc.stdout.splitlines() if line.startswith("worktree "))
    return max(total - 1, 0)  # porcelain の先頭 entry は必ず main working tree


def build_context(count: int) -> str:
    if count >= STRONG_THRESHOLD:
        head = (
            f"[worktree 蓄積 強警告] linked worktree が {count} 個ある — "
            f"E2BIG 発症圏内 (実績 8〜9 個)。新しい worktree を作る前に掃除する。"
        )
    else:
        head = f"[worktree 蓄積警告] linked worktree が {count} 個ある。"
    return f"{head}{MECHANISM}\n{CLEANUP}"


def emit(context: str | None) -> None:
    if context is None:
        out: dict = {"suppressOutput": True}
    else:
        out = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }
    print(json.dumps(out, ensure_ascii=False))


def main() -> int:
    try:
        data = json.loads(sys.stdin.read())
    except (ValueError, TypeError):
        data = {}
    cwd = str(data.get("cwd") or "") if isinstance(data, dict) else ""
    if not os.path.isdir(cwd):
        cwd = os.getcwd()

    count = count_linked_worktrees(cwd)
    emit(None if count is None or count < WARN_THRESHOLD else build_context(count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
