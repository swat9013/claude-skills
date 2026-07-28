#!/bin/sh
# jq 必須 (fail-closed)
if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"jq not found on PATH; destructive guard fails closed"}}'
  exit 0
fi

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

deny() {
  jq -nc --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# rm with recursive AND force flag が、root / system / home パスを対象に
# している場合のみ deny。短縮形 (-rf / -fr / -rfv)、長形式 (--recursive --force /
# --force --recursive)、分離形 (-r -f) を網羅。
# system パス: /etc /usr /var /Users /home /bin /sbin /Library /System
# /Applications /opt。home パス: ~ / ~/ / $HOME / ${HOME}。
has_rm=$(printf '%s\n' "$COMMAND" | grep -cE '(^|[[:space:]])rm([[:space:]]|$)' || true)
if [ "$has_rm" -gt 0 ]; then
  has_r=$(printf '%s\n' "$COMMAND" | grep -cE '(^|[[:space:]])(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)([[:space:]]|$)' || true)
  has_f=$(printf '%s\n' "$COMMAND" | grep -cE '(^|[[:space:]])(-[a-zA-Z]*f[a-zA-Z]*|--force)([[:space:]]|$)' || true)
  if [ "$has_r" -gt 0 ] && [ "$has_f" -gt 0 ]; then
    # system path 検出。/etc 配下まで含めて deny (system 配下は全部危険)。
    # 境界: 先頭 = 空白/quote、末尾 = 空白/quote/EOL/`/`。
    if printf '%s\n' "$COMMAND" | grep -qE "(^|[[:space:]\"'])/(etc|usr|var|Users|home|bin|sbin|Library|System|Applications|opt)([[:space:]\"'/]|$)"; then
      deny "rm -rf 系で system path (/etc /usr /var 等) への破壊操作は禁止"
    fi
    # 裸 root: `/` 単独 / `/*`。
    if printf '%s\n' "$COMMAND" | grep -qE "(^|[[:space:]\"'])/([[:space:]\"']|\*|$)"; then
      deny "rm -rf / は禁止"
    fi
    # 裸 home: `~` 単独 / `~/` 単独 (`~/foo` は元の spec で allow)。
    # 末尾の `/` を含めないため `~/foo` は match しない。
    if printf '%s\n' "$COMMAND" | grep -qE "(^|[[:space:]\"'])(~|~/)([[:space:]\"']|\$)"; then
      deny "rm -rf ~ は禁止"
    fi
    # $HOME / ${HOME} は変数展開で広範囲指定なので位置を問わず deny (原 spec を維持)。
    if printf '%s\n' "$COMMAND" | grep -qE '(\$HOME|\$\{HOME\})'; then
      deny "rm -rf \$HOME は禁止"
    fi
  fi
fi

# find ... -delete
if printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:]])find[[:space:]].*-delete([[:space:]]|$)'; then
  deny "find -delete は禁止。事前に -print で対象を確認してから個別削除してください"
fi

# docker dangerous ops
if printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:]])docker[[:space:]]+rm[[:space:]]+(-f|--force)([[:space:]]|$)'; then
  deny "docker rm -f は禁止。docker stop の後で削除してください"
fi
if printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:]])docker[[:space:]]+volume[[:space:]]+rm([[:space:]]|$)'; then
  deny "docker volume rm はデータ消去のため禁止"
fi
if printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:]])docker[[:space:]]+system[[:space:]]+prune([[:space:]]+.*)?[[:space:]]+(-f|--force)([[:space:]]|$)'; then
  deny "docker system prune -f は禁止"
fi

exit 0
