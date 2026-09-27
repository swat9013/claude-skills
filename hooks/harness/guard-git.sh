#!/bin/sh
# jq 必須 (fail-closed)
if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"jq not found on PATH; git guard fails closed"}}'
  exit 0
fi

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

# git のグローバルオプション (`git -C <dir>` / `git -c <k>=<v>` / `--git-dir=` 等) は
# subcommand の**前**に置ける。この形は 2 つの層を同時に素通りする:
#   - permission rule: 照合はコマンド先頭からの prefix なので `Bash(git push --force:*)`
#     のような deny に `git -C x push --force` は当たらない (docs/settings-knowledge.md の
#     「permission rule の照合はコマンドの綴りに依存する」と同じ構造の穴)
#   - 本 hook: 行頭アンカー判定 (`^git branch` / `^git push` 等) が前置で外れる
# そこで判定用に前置を剥がした写し (SUBJECT) を作る。**原文 (COMMAND) は書き換えない** —
# 剥がすのは deny 判定を届かせるためで、allow の射程は広げない (下の push 許可を参照)。
# 限界: 値が空白を含む引用形 (`git -c "user.name=Foo Bar" push --force`) は 1 token として
# 剥がせず素通りする。剥がしを quote-aware にすると sh の範囲では別の誤爆を招くので広げない。
strip_git_global_opts() {
  _s=$1
  _i=0
  while [ "$_i" -lt 8 ]; do
    _n=$(printf '%s\n' "$_s" | sed -E 's/(^|[[:space:]])git[[:space:]]+(-C[[:space:]]+[^[:space:]]+|-c[[:space:]]+[^[:space:]]+|--git-dir=[^[:space:]]+|--work-tree=[^[:space:]]+|--namespace=[^[:space:]]+|--exec-path=[^[:space:]]+|--no-pager|--paginate|-p)[[:space:]]+/\1git /g')
    [ "$_n" = "$_s" ] && break
    _s=$_n
    _i=$((_i + 1))
  done
  printf '%s\n' "$_s"
}
SUBJECT=$(strip_git_global_opts "$COMMAND")

deny() {
  jq -nc --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# pass (判断を出さない) 出口はすべてここを通す。無出力の exit は transcript に attachment を
# 残さず、棚卸しで「壊れて死んだ guard」と「窓内に出番が無かった guard」が同じ見え方になる
# (#587 / ADR 0043)。permissionDecision を持たない envelope は通常の permission フローへ
# 委ねるので、判断の意味論は無出力のときと変わらない。逐語で 1 行に保つ (テストが全 guard の
# 一致を見る)。
passthrough() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse"},"suppressOutput":true}\n'
  exit 0
}

# 先頭の空白を許容するため `^[[:space:]]*git` を共通プレフィクスにする。

# `git add` の一括指定は deny しない。秘匿情報の巻き込みは repo ごとの gitleaks
# (pre-commit) で防ぐ (ADR 0077)。

# 破壊的 git 操作
# 理由文で stash を勧めない: stash の stack は worktree・並列 session 間で共有され、
# `git stash -u && git stash drop` で他者の未 commit 編集ごと消える事故が実際に起きた (refuter / i853)。
RESTORE_GUIDE="自分の変更を退避するなら一時 WIP commit を作る。自分が作っていない未 commit 変更は触らず、ユーザーに確認する"
if printf '%s\n' "$SUBJECT" | grep -qE 'git reset.*--hard|git clean.*(--force|-f)'; then
  deny "破壊的git操作は禁止。${RESTORE_GUIDE}"
fi
if printf '%s\n' "$SUBJECT" | grep -qE 'git checkout -- '; then
  deny "git checkout -- は禁止。${RESTORE_GUIDE}"
fi
if printf '%s\n' "$SUBJECT" | grep -qE '^[[:space:]]*git restore( |$)' && ! printf '%s\n' "$SUBJECT" | grep -qE 'git restore --staged'; then
  deny "git restore（ワーキングツリー変更の破棄）は禁止。${RESTORE_GUIDE}"
fi

# Force delete branch: -D, --delete --force, --force --delete (順序逆転を含む)
if printf '%s\n' "$SUBJECT" | grep -qE '^[[:space:]]*git[[:space:]]+branch[[:space:]]'; then
  if printf '%s\n' "$SUBJECT" | grep -qE '(^|[[:space:]])-D([[:space:]]|$)'; then
    deny "git branch -D は禁止。git branch -d で merged 確認、未 merged の場合はユーザーに確認"
  fi
  if printf '%s\n' "$SUBJECT" | grep -qE '(^|[[:space:]])--delete([[:space:]]|$)' && \
     printf '%s\n' "$SUBJECT" | grep -qE '(^|[[:space:]])--force([[:space:]]|$)'; then
    deny "git branch --delete --force は禁止 (順序問わず)。git branch -d で merged 確認"
  fi
fi

# tag delete: -d, --delete (annotated tag は再現不能)
if printf '%s\n' "$SUBJECT" | grep -qE '^[[:space:]]*git[[:space:]]+tag[[:space:]]'; then
  if printf '%s\n' "$SUBJECT" | grep -qE '(^|[[:space:]])(-d|--delete)([[:space:]]|$)'; then
    deny "git tag -d / --delete は禁止。tag 削除はユーザーに確認してください"
  fi
fi

# Remote branch delete (git push --delete / git push origin :branch)
if printf '%s\n' "$SUBJECT" | grep -qE '^[[:space:]]*git[[:space:]]+push[[:space:]]+(--delete([[:space:]]|$)|[^[:space:]]+[[:space:]]+:[^[:space:]])'; then
  deny "リモートブランチ削除 (git push --delete / push origin :branch) は禁止"
fi

# force push deny は SUBJECT で判定する — `git -C x push --force` を届かせるため。
# `--force-with-lease` は token 境界の都合で permission 層の deny を通過しており、lease
# 保護があるため意図通りとして許容している (docs/settings-knowledge.md)。hook 側も同じ扱いにする。
# 「これは git push か」の判定は本 file に 3 形あり、綴りが割れると片方だけ穴が空く。
# 下の床判定と同じ token 境界へ揃える — リテラル 1 空白のままだと `git  push --force` が
# 素通りし、逆に `git pushx --force` を force push として掴む。
if printf '%s\n' "$SUBJECT" | grep -qE '^[[:space:]]*git[[:space:]]+push([^[:alnum:]_-]|$)'; then
  if printf '%s\n' "$SUBJECT" | grep -qE -- '--force($| )|-f($| )' && ! printf '%s\n' "$SUBJECT" | grep -q -- '--force-with-lease'; then
    deny "force pushは禁止。ユーザーに確認を求めてください"
  fi
fi

# git push 条件付き許可は**原文**で判定する。SUBJECT を使うと `git -C <他 repo> push` まで
# hook allow に含まれ、permission 層が ask に落としていた前置つき呼び出しを無確認で通して
# しまう。剥がした写しは deny を届かせるためだけに使い、許可の射程は広げない。
# main/master への直接 push 判定は持たない (GitHub/GitLab の branch protection に委譲 → docs/adr/0019)。
#
# Bash tool timeout の床 (pre-push が重い repo 向けの deny) は本 hook に置かない。根拠が
# swat-skills の `.githooks/pre-push` 固有なので、同 repo の .claude/settings.json に登録した
# guard-push-timeout.sh が持つ (docs/adr/0078)。plugin に置くと他 repo の push まで塞ぐ。
# 判定は token 境界まで見る。`^git push` だけだと `git pushx` のような別コマンドまで allow する。
# 境界は空白に限らない — `git push;echo x` / `git push&&foo` / `git push>log` は先頭が本物の
# push なので allow の内側に残す。除くのは識別子が続く形 (`git pushx` / `git push-foo`) だけ。
if printf '%s\n' "$COMMAND" | grep -qE '^[[:space:]]*git[[:space:]]+push([^[:alnum:]_-]|$)'; then
  # `| tail` / `| head` を落とす判定はここに在ったが退役した (#897)。詳細は docs/adr/0067。
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}'
  exit 0
fi

passthrough
