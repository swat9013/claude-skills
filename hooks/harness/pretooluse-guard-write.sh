#!/bin/sh
INPUT=$(cat)
FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0

if printf '%s\n' "$FILE_PATH" | grep -qE '\.(env|key|pem|p12|pfx|crt)$|id_rsa|\.netrc|/credentials$|/secrets/'; then
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"機密ファイルへの書き込みは禁止"}}\n'
  exit 0
fi

exit 0
