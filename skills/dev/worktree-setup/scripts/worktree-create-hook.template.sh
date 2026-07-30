#!/usr/bin/env bash
# WorktreeCreate hook (置き換え型): worktree 作成 + .worktreeinclude コピー + 初期セットアップ。
# 既定は追加型 (worktree-post-setup.template.sh)。本 template は「初期化失敗で worktree
# 作成ごと中止したい」「git 以外の VCS」の例外ケース専用。
# hook 登録時は worktree 作成が本 script の責務になり、path を stdout に返さないと作成自体が失敗する。
# hook 経路では .worktreeinclude のコピーも settings の worktree.sparsePaths /
# worktree.symlinkDirectories も一切適用されないため、必要なら本 script 側で行う。
# stdin: {"session_id","transcript_path","cwd","hook_event_name","name"}
#   (2026-07-15 実証。cwd = 元 repo root、name = worktree 名)
# stdout: 作成した worktree の絶対 path 1 行のみ。診断・コマンド出力はすべて stderr へ。
set -Eeuo pipefail

# ===== native 設定の再現 (必要な場合のみ列挙。空なら従来どおり全 checkout / symlink なし) =====
# SPARSE_PATHS: worktree.sparsePaths 相当。repo root 相対の**ディレクトリ**のみ。
#   root 直下のファイルは常に checkout される。root 直下のディレクトリは列挙しない限り入らないので、
#   worktree 内で repo root の .claude/ が要るなら ".claude" を含める。例: SPARSE_PATHS=(".claude" "packages/api")
# SYMLINK_DIRS: worktree.symlinkDirectories 相当。元 repo の同名ディレクトリへ symlink する。
#   例: SYMLINK_DIRS=("node_modules")。symlink は .gitignore の "node_modules/" (末尾 /) にマッチせず
#   untracked 扱いになるため、末尾 / なしのパターンで ignore しておく (さもないと片付けに --force が要る)。
SPARSE_PATHS=()
SYMLINK_DIRS=()

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

if [ ${#SPARSE_PATHS[@]} -gt 0 ]; then
  # sparse: checkout 前に対象を絞る (--no-checkout → sparse-checkout set → checkout)
  git -C "$cwd" worktree add --no-checkout -b "$branch" "$worktree_path" "$base_ref" >&2
  trap cleanup_on_failure ERR
  git -C "$worktree_path" sparse-checkout set "${SPARSE_PATHS[@]}" >&2
  git -C "$worktree_path" checkout >&2
else
  git -C "$cwd" worktree add -b "$branch" "$worktree_path" "$base_ref" >&2
  # add 成功後の失敗のみ cleanup 対象 (add 前に arm すると branch 衝突等の add
  # 失敗時に既存の worktree/branch まで巻き添えで壊れる)
  trap cleanup_on_failure ERR
fi

# symlink: 元 repo の実体を共有する (コピーしないので容量と初期化時間を節約できる)
for d in ${SYMLINK_DIRS[@]+"${SYMLINK_DIRS[@]}"}; do
  if [ ! -d "$cwd/$d" ]; then
    echo "worktree-setup hook: symlink 元がないので skip: $cwd/$d" >&2
    continue
  fi
  if [ -e "$worktree_path/$d" ] || [ -L "$worktree_path/$d" ]; then
    echo "worktree-setup hook: worktree 側に既に存在するので skip: $d" >&2
    continue
  fi
  mkdir -p "$(dirname "$worktree_path/$d")"
  ln -s "$cwd/$d" "$worktree_path/$d"
done

# .worktreeinclude: gitignored かつパターン一致のファイルのみコピー (追跡ファイルは worktree に既にある)
# SPARSE_PATHS 外のディレクトリ配下でもコピーする (ネイティブ経路の挙動に合わせた。2026-07-29 実証)
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
