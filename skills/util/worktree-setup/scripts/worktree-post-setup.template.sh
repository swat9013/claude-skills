#!/usr/bin/env bash
# worktree 初期化 hook (追加型): 新しい worktree の中で 1 回だけ初期化コマンドを走らせる。
#
# 登録先 (どれも cwd = 対象 worktree。2026-07-29 実証 / Claude Code 2.1.220):
#   SessionStart              … `claude --worktree` / worktree 内での起動・resume
#   PostToolUse (matcher: EnterWorktree) … セッション中の worktree 切り替え
#   SubagentStart             … `isolation: worktree` の subagent
#
# WorktreeCreate と違い worktree 生成を置き換えないため、.worktreeinclude /
# worktree.sparsePaths / worktree.symlinkDirectories はネイティブのまま効く。
#
# stdout は SessionStart ではモデルの context に入る。初期化を実行したときだけ 1 行出し、
# コマンド出力はすべて stderr に落とす (下の実行ブロックが >&2 でまとめて閉じ込める)。
set -uo pipefail

# 1. worktree の中でだけ動く (main checkout では何もしない)
git_dir=$(git rev-parse --path-format=absolute --git-dir 2>/dev/null) || exit 0
common_dir=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
[ "$git_dir" = "$common_dir" ] && exit 0

# 2. 冪等ガード: marker は .git 側に置く。working tree に置くと worktree が untracked を
#    持ち、Claude Code の自動 cleanup / sweep 判定 (未追跡があれば残す) を狂わせる。
marker="$git_dir/worktree-setup-done"
[ -e "$marker" ] && exit 0

# 3. 初期化。失敗しても marker を作らないので次のセッションで再試行される。
{
  # ===== 初期セットアップ: 次の 1 行を対象 repo のコマンドに置換する (例: npm ci) =====
  :  # SETUP_COMMANDS
  # ===== 初期セットアップここまで =====
} >&2
status=$?

if [ "$status" -ne 0 ]; then
  echo "worktree-setup: 初期化に失敗 (exit $status)。手動で実行後 'touch $marker' で抑止できる" >&2
  exit 0
fi

: > "$marker"
echo "worktree-setup: この worktree の初期化を実行した"
exit 0
