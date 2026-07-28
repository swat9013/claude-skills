#!/usr/bin/env bash
# WorktreeCreate hook (置き換え型): worktree 作成 + .worktreeinclude コピー + 初期セットアップ。
# hook 登録時は worktree 作成が本 script の責務になり、path を stdout に返さないと作成自体が失敗する。
# hook 経路では .worktreeinclude のネイティブコピーも走らないため、コピーも本 script が行う。
# stdin: {"session_id","transcript_path","cwd","hook_event_name","name"}
#   (2026-07-15 実証。cwd = 元 repo root、name = worktree 名)
# stdout: 作成した worktree の絶対 path 1 行のみ。診断・コマンド出力はすべて stderr へ。
set -Eeuo pipefail

input=$(cat)
field() {
  printf '%s' "$input" | python3 -c "import json,sys; print(json.load(sys.stdin).get('$1',''))"
}
name=$(field name)
cwd=$(field cwd)

if [ -z "$name" ] || [ -z "$cwd" ]; then
  echo "worktree-setup hook: name / cwd が入力にない" >&2
  exit 1
fi

# base ref: origin のデフォルトブランチ。未設定 (remote なし等) なら HEAD
base_ref=$(git -C "$cwd" symbolic-ref --quiet --short refs/remotes/origin/HEAD || echo HEAD)

worktree_path="$cwd/.claude/worktrees/$name"
branch="worktree-$name"

# 失敗時は作成途中の worktree / branch を片付け、同名でのリトライを可能に保つ
cleanup_on_failure() {
  echo "worktree-setup hook: セットアップ失敗のため作成した worktree / branch を片付ける" >&2
  git -C "$cwd" worktree remove --force "$worktree_path" >&2 || echo "worktree-setup hook: worktree の片付けに失敗: $worktree_path" >&2
  git -C "$cwd" branch -D "$branch" >&2 || echo "worktree-setup hook: branch の片付けに失敗: $branch" >&2
}

git -C "$cwd" worktree add -b "$branch" "$worktree_path" "$base_ref" >&2
# add 成功後の失敗のみ cleanup 対象 (add 前に arm すると branch 衝突等の add
# 失敗時に既存の worktree/branch まで巻き添えで壊れる)
trap cleanup_on_failure ERR

# .worktreeinclude: gitignored かつパターン一致のファイルのみコピー (追跡ファイルは worktree に既にある)
if [ -f "$cwd/.worktreeinclude" ]; then
  (
    cd "$cwd"
    comm -12 \
      <(git ls-files --others --ignored --exclude-standard | sort) \
      <(git ls-files --others --ignored --exclude-from=.worktreeinclude | sort) \
      | while IFS= read -r f; do
          mkdir -p "$worktree_path/$(dirname "$f")"
          cp -p "$f" "$worktree_path/$f"
        done
  ) >&2
fi

cd "$worktree_path"
# ===== 初期セットアップ: 次の 1 行を対象 repo のコマンドに置換する (出力は >&2 に流す。例: npm ci >&2) =====
:  # SETUP_COMMANDS
# ===== 初期セットアップここまで =====

printf '%s\n' "$worktree_path"
