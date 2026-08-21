#!/bin/sh
# jq 必須 (fail-closed)
if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"jq not found on PATH; git guard fails closed"}}'
  exit 0
fi

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

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

# `git add` の一括指定は guard-bulk-stage.sh が担う。本 hook は hooks.json 側の
# `if: "Bash(git:*)"` gate で先頭 token が git のときしか起動せず、判定も行頭アンカーだった
# ため `echo x && git add -A` / `git status --short && git add -A` を取り逃していた
# (gate を外すと下の非アンカー判定が全 Bash にかかって誤爆するので、判定ごと移した)。

# 破壊的 git 操作
if printf '%s\n' "$COMMAND" | grep -qE 'git reset.*--hard|git clean.*(--force|-f)'; then
  deny "破壊的git操作は禁止。git stashで退避してから操作してください"
fi
if printf '%s\n' "$COMMAND" | grep -qE 'git checkout -- '; then
  deny "git checkout -- は禁止。git stashで退避してから操作してください"
fi
if printf '%s\n' "$COMMAND" | grep -qE '^[[:space:]]*git restore( |$)' && ! printf '%s\n' "$COMMAND" | grep -qE 'git restore --staged'; then
  deny "git restore（ワーキングツリー変更の破棄）は禁止。git stashで退避してください"
fi

# Force delete branch: -D, --delete --force, --force --delete (順序逆転を含む)
if printf '%s\n' "$COMMAND" | grep -qE '^[[:space:]]*git[[:space:]]+branch[[:space:]]'; then
  if printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:]])-D([[:space:]]|$)'; then
    deny "git branch -D は禁止。git branch -d で merged 確認、未 merged の場合はユーザーに確認"
  fi
  if printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:]])--delete([[:space:]]|$)' && \
     printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:]])--force([[:space:]]|$)'; then
    deny "git branch --delete --force は禁止 (順序問わず)。git branch -d で merged 確認"
  fi
fi

# tag delete: -d, --delete (annotated tag は再現不能)
if printf '%s\n' "$COMMAND" | grep -qE '^[[:space:]]*git[[:space:]]+tag[[:space:]]'; then
  if printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:]])(-d|--delete)([[:space:]]|$)'; then
    deny "git tag -d / --delete は禁止。tag 削除はユーザーに確認してください"
  fi
fi

# Remote branch delete (git push --delete / git push origin :branch)
if printf '%s\n' "$COMMAND" | grep -qE '^[[:space:]]*git[[:space:]]+push[[:space:]]+(--delete([[:space:]]|$)|[^[:space:]]+[[:space:]]+:[^[:space:]])'; then
  deny "リモートブランチ削除 (git push --delete / push origin :branch) は禁止"
fi

# git push 条件付き許可
# main/master への直接 push 判定は持たない (GitHub/GitLab の branch protection に委譲 → docs/adr/0019)。
if printf '%s\n' "$COMMAND" | grep -qE '^[[:space:]]*git push'; then
  if printf '%s\n' "$COMMAND" | grep -qE -- '--force($| )|-f($| )' && ! printf '%s\n' "$COMMAND" | grep -q -- '--force-with-lease'; then
    deny "force pushは禁止。ユーザーに確認を求めてください"
  fi
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}'
  exit 0
fi

passthrough
