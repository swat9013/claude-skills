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

# pass (判断を出さない) 出口はすべてここを通す。無出力の exit は transcript に attachment を
# 残さず、棚卸しで「壊れて死んだ guard」と「窓内に出番が無かった guard」が同じ見え方になる
# (#587 / ADR 0043)。permissionDecision を持たない envelope は通常の permission フローへ
# 委ねるので、判断の意味論は無出力のときと変わらない。逐語で 1 行に保つ (テストが全 guard の
# 一致を見る)。
passthrough() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse"},"suppressOutput":true}\n'
  exit 0
}

if [ -z "$HOST" ]; then
  deny "URL からホスト名を抽出できませんでした"
fi

# allowlist (完全一致)。subdomain は展開されないので、許可したい host は
# 1 つずつ列挙する (`claude.com` を書いても `platform.claude.com` は通らない)。
# docs.anthropic.com は platform.claude.com へ 301 済みだが、旧 URL を載せた
# 第三者記事が残るため entry も残す。
# 末尾 5 件は一次資料の発行元 (二次まとめ記事のドメインは載せない)。
# ar5iv.labs.arxiv.org は arXiv 論文の HTML 版で、完全一致のため arxiv.org
# とは別 entry が要る。
ALLOWLIST="code.claude.com platform.claude.com docs.anthropic.com claude.com www.anthropic.com github.com raw.githubusercontent.com gist.githubusercontent.com gitlab.com docs.python.org peps.python.org doc.rust-lang.org pkg.go.dev developer.mozilla.org docs.rs crates.io pypi.org npmjs.com registry.npmjs.org rubygems.org stackoverflow.com qiita.com zenn.dev martinfowler.com arxiv.org ar5iv.labs.arxiv.org www.oreilly.com dora.dev www.thoughtworks.com"

for allowed in $ALLOWLIST; do
  if [ "$HOST" = "$allowed" ]; then
    passthrough
  fi
done

# ユーザーが明示的に指示で渡した URL なら allowlist 外でも許可する。
# 照合対象は transcript 内の「人間が入力したテキスト」に絞る。
# `type == "user"` は「人間が入力した」を意味しない — harness は peer
# セッションからのメッセージ・subagent の完了通知・`!cmd` の stdout・
# system-reminder をすべて同じ entry 型に流し込む。いずれも攻撃者が内容を
# 混入しうるので、tool_result (toolUseResult != null) と同様に除外する
# (prompt injection / cross-session 経由の任意 URL fetch を防ぐ)。
#
# 除外は 2 段構え。どちらか一方でも生き残れば穴が開くため両方を通す:
#   1. `.origin.kind` — harness が記録する注入元の分類 (human / peer /
#      task-notification / unclassified)。タグ名の改名に影響されない。
#      ただし古い transcript や subagent (sdk-cli) の entry には無いので
#      これ単独では足りない
#   2. wrapper タグの文字列照合 — origin が無い entry を拾う保険。タグ名は
#      harness 側の実装詳細なので、改名されれば穴が再発する (1 が受け皿)
#
# 2 の照合は **entry 単位**で行い、1 ブロックでも当たれば entry ごと捨てる。
# 実測ではタグと本文は同一ブロックに入るが、harness が wrapper と payload を
# 別ブロックへ分ける構成になった瞬間、ブロック単位の除外は payload を素通しする。
#
# system-reminder だけは span 単位で削る。人間の入力と同じ text ブロックに
# 追記される構成があり、ブロックごと捨てると本物の貼り付けを誤って弾くため。
# 対で閉じていない残骸があればそのブロックは丸ごと捨てる (fail-closed)。
# 削除は 2 の照合より**先**に行う — reminder の本文がたまたま wrapper タグ名を
# 含むと (この repo の files がまさにそう)、同居する人間の入力まで巻き添えで
# 消えるため。逆順にしても穴は開かない: wrapper タグは攻撃者が書ける本文より
# 必ず手前にあるので、gsub がタグ自体を食うことはない。
#
# 不一致時は下の deny に落ちるため fail は「許可」でなく「拒否」側に倒れる。
if [ -n "$TRANSCRIPT" ] && [ -r "$TRANSCRIPT" ]; then
  URL_NOSCHEME=$(printf '%s\n' "$URL" | sed -E 's|^[a-zA-Z][a-zA-Z0-9+.-]*://||')
  USER_TEXT=$(jq -r '
    select(.type == "user" and (.toolUseResult == null))
    | select((.origin.kind? // "") | . != "peer" and . != "task-notification")
    | [ .message.content
        | if type == "string" then . else (.[]? | select(.type == "text") | .text) end
        | gsub("<system-reminder>[\\s\\S]*?</system-reminder>"; "")
        | select(test("<system-reminder") | not) ] as $blocks
    | select(any($blocks[];
        test("<(teammate-message|cross-session-message|task-notification|local-command-stdout|local-command-stderr|agent-message|remote-message)\\b|\\[MESSAGE FROM NON-USER SOURCE")) | not)
    | $blocks[]
  ' "$TRANSCRIPT" 2>/dev/null)
  if printf '%s' "$USER_TEXT" | grep -Fq -- "$URL" ||
    printf '%s' "$USER_TEXT" | grep -Fq -- "$URL_NOSCHEME"; then
    passthrough
  fi
fi

deny "ドメイン $HOST は WebFetch allowlist に未登録 (ユーザーが指示で渡した URL でもない)。web-research subagent 経由で取得するか、ユーザーがチャットに URL を貼って指示してください。恒久許可は guard-webfetch.sh の allowlist に追加"
