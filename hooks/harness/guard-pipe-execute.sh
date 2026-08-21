#!/bin/sh
# jq 必須 (fail-closed)
if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"jq not found on PATH; pipe-execute guard fails closed"}}'
  exit 0
fi

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

# here-document の本体は shell がデータとして読むだけで実行しないため、判定対象から
# 落とす。危険なコマンドの説明文を `cat > doc.md <<EOF` で書くだけで deny される誤爆を
# 塞ぐ。開始行そのものは残す — `curl u | sh && cat <<EOF` のように実コマンドが
# 同居する形を取り逃さないため。終端が現れない入力は EOF まで落とす: 実 shell も残りを
# 本体として扱うので、実行されない範囲だけを落としている。引用文字列中の `<<`
# (`git commit -m "a << b"`) を開始と誤認する余地は残るが、shell parser 無しに実
# コマンドと区別する術は無いので受容する。awk の program は single quote で括るため、
# 単引用符は regex 中で `\047` と綴る。
COMMAND=$(printf '%s\n' "$COMMAND" | awk '
  { line = $0
    if (pending > 0) {                                     # here-document の本体を読んでいる
      body = line
      if (dash[1]) sub(/^\t+/, "", body)                   # `<<-` は行頭 tab を無視して終端照合
      if (body == delim[1]) {
        for (i = 1; i < pending; i++) { delim[i] = delim[i+1]; dash[i] = dash[i+1] }
        pending--                                          # 同一行の複数 here-document を順に閉じる
      }
      next                                                 # 本体行・終端行とも判定対象から落とす
    }
    print line
    pos = 1
    while (match(substr(line, pos), /<<-?[ \t]*[\047"]?[A-Za-z_][A-Za-z0-9_]*[\047"]?/)) {
      at = pos + RSTART - 1
      word = substr(line, at, RLENGTH)
      pos = at + RLENGTH
      if (at > 1 && substr(line, at - 1, 1) == "<") continue  # `<<<` は here-string (本体なし)
      pending++
      dash[pending] = (word ~ /^<<-/)
      sub(/^<<-?[ \t]*/, "", word)
      gsub(/[\047"]/, "", word)                            # quote つき delimiter を素の語へ揃える
      delim[pending] = word
    }
  }
')

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

passthrough
