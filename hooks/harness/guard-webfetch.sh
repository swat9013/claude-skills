#!/bin/sh
# jq 必須 (fail-closed)
if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"jq not found on PATH; WebFetch blocked (fail-closed)"}}'
  exit 0
fi

INPUT=$(cat)
URL=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.url // empty')
TRANSCRIPT=$(printf '%s\n' "$INPUT" | jq -r '.transcript_path // empty')

# RFC 3986: scheme://[userinfo@]host[:port]/path?query#fragment
# Bypass を防ぐため userinfo を先に剥がす。awk の単純 split だと
# `https://github.com:fake@evil.example/` を github.com と誤抽出する。
HOST=$(printf '%s\n' "$URL" | sed -E \
  -e 's|^[a-zA-Z][a-zA-Z0-9+.-]*://||' \
  -e 's|[/?#].*$||' \
  -e 's|^.*@||' \
  -e 's|:.*$||' \
  | tr '[:upper:]' '[:lower:]')

deny() {
  jq -nc --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

if [ -z "$HOST" ]; then
  deny "URL からホスト名を抽出できませんでした"
fi

# allowlist (完全一致)。subdomain は展開されないので、許可したい host は
# 1 つずつ列挙する (`claude.com` を書いても `platform.claude.com` は通らない)。
# docs.anthropic.com は platform.claude.com へ 301 済みだが、旧 URL を載せた
# 第三者記事が残るため entry も残す。
ALLOWLIST="code.claude.com platform.claude.com docs.anthropic.com claude.com www.anthropic.com github.com raw.githubusercontent.com gist.githubusercontent.com gitlab.com docs.python.org peps.python.org doc.rust-lang.org pkg.go.dev developer.mozilla.org docs.rs crates.io pypi.org npmjs.com registry.npmjs.org rubygems.org stackoverflow.com qiita.com zenn.dev martinfowler.com"

for allowed in $ALLOWLIST; do
  if [ "$HOST" = "$allowed" ]; then
    exit 0
  fi
done

# ユーザーが明示的に指示で渡した URL なら allowlist 外でも許可する。
# 照合対象は transcript 内の「人間が入力したテキスト」(string content と
# text ブロック) のみ。tool_result は WebFetch/Bash 等の出力で攻撃者が
# 混入しうるため除外する (prompt injection 経由の任意 URL fetch を防ぐ)。
# 不一致時は下の deny に落ちるため fail は「許可」でなく「拒否」側に倒れる。
if [ -n "$TRANSCRIPT" ] && [ -r "$TRANSCRIPT" ]; then
  URL_NOSCHEME=$(printf '%s\n' "$URL" | sed -E 's|^[a-zA-Z][a-zA-Z0-9+.-]*://||')
  USER_TEXT=$(jq -r '
    select(.type == "user" and (.toolUseResult == null))
    | .message.content
    | if type == "string" then . else (.[]? | select(.type == "text") | .text) end
  ' "$TRANSCRIPT" 2>/dev/null)
  if printf '%s' "$USER_TEXT" | grep -Fq -- "$URL" ||
    printf '%s' "$USER_TEXT" | grep -Fq -- "$URL_NOSCHEME"; then
    exit 0
  fi
fi

deny "ドメイン $HOST は WebFetch allowlist に未登録 (ユーザーが指示で渡した URL でもない)。web-research subagent 経由で取得するか、ユーザーがチャットに URL を貼って指示してください。恒久許可は guard-webfetch.sh の allowlist に追加"
