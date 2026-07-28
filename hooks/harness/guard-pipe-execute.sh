#!/bin/sh
# jq 必須 (fail-closed)
if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"jq not found on PATH; pipe-execute guard fails closed"}}'
  exit 0
fi

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

deny() {
  jq -nc --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# download-and-execute パターンを 4 系統で検出。
# (1) パイプ shell: curl ... | (sudo)? (/path/to/)? bash|sh|zsh|dash|ksh
# (2) パイプ xargs shell: curl ... | xargs (sudo)? bash|sh
# (3) process substitution: bash <(curl ...) / source <(curl ...) / . <(curl ...)
# (4) eval of command substitution: eval "$(curl ...)" / eval `curl ...`

# (1) パイプ shell — 絶対パス指定 (`| /bin/bash`) も catch
if printf '%s\n' "$COMMAND" | grep -qE '\|[[:space:]]*(sudo[[:space:]]+)?([^[:space:]|;&]*/)?(bash|sh|zsh|dash|ksh)([[:space:]]|$)'; then
  deny "curl|bash パターンは禁止。スクリプトを一時ファイルに保存しレビュー後に実行してください"
fi

# (2) パイプ xargs shell
if printf '%s\n' "$COMMAND" | grep -qE '\|[[:space:]]*xargs[[:space:]]+(sudo[[:space:]]+)?(bash|sh|zsh)([[:space:]]|$)'; then
  deny "curl | xargs bash パターンは禁止"
fi

# (3) process substitution into shell
if printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:]])(bash|sh|zsh|source|\.)[[:space:]]+<\([[:space:]]*(curl|wget)([[:space:]]|$)'; then
  deny "bash <(curl ...) / source <(curl ...) パターンは禁止"
fi

# (4) eval of command substitution with curl/wget
if printf '%s\n' "$COMMAND" | grep -qE 'eval[[:space:]]+["'\'']?\$\([[:space:]]*(curl|wget)([[:space:]]|$)'; then
  deny "eval \"\$(curl ...)\" パターンは禁止"
fi
if printf '%s\n' "$COMMAND" | grep -qE 'eval[[:space:]]+["'\'']?`[[:space:]]*(curl|wget)([[:space:]]|$)'; then
  deny "eval \`curl ...\` パターンは禁止"
fi

exit 0
