#!/bin/sh
# shell の inline 実行 (bash -c / sh -c / zsh -c) を遮断する PreToolUse guard。
# file 名の `python` は、python / perl / ruby / node の inline も見ていた頃の名残。
#
# Why: permission rule は `bash -c "…"` の中身を検査しない。`-c` の文字列に包めば
# permissions.deny の entry (`git push --force` 等) を迂回できるので、本 deny は
# **抜け道を塞ぐ側**にある (ADR 0077)。
#
# python / perl / ruby / node の inline (`-c` / `-e` / stdin の `-`) は deny しない (ADR 0077)。
# script file に書いて実行すれば同じ能力で通る — 抜け道がある deny は往復を生むだけで、
# 塞ごうとした任意コード実行の経路を塞いでいなかった。
#
# 本 hook は fail-closed: payload を読めなかったとき (jq が無い / JSON として不正 / 期待した
# 形でない) は判断保留 = 素通しにせず deny する (#250 決定 3 / #596)。読み取り失敗を素通しに
# すると `COMMAND` が空文字になり、下の全判定が不成立のまま passthrough して
# `bash -c` 系のインライン実行が無検査で通る。
#
# 受容コスト: 本 hook は hooks.json に `if` gate を持たず **全 Bash 呼び出しで発火する**ため、
# jq が PATH から消えた瞬間に Bash が全面停止する。harness の他の guard より停止範囲が広い
# posture であることを承知のうえで倒している (#596 で人間が判断)。deny 理由に内訳と hook 名が
# 出るので、それを見て jq を入れれば復旧する。
#
# stdin は jq 検査より先に読み切る。読まずに deny して先に exit すると書き手側が EPIPE を
# 踏みうる (pretooluse-guard-write.sh / guard-shell.sh と同順)。
INPUT=$(cat)

deny_unreadable() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"payload を読めなかったため Bash を停止した (fail-closed): %s [guard-inline-python.sh]"}}\n' "$1"
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

command -v jq >/dev/null 2>&1 ||
  deny_unreadable 'jq が見つからない'

[ -n "$INPUT" ] ||
  deny_unreadable 'stdin が空'

# JSON 検査と shape 検査で jq を 2 回呼ぶ。jq は parse error も error() も同じ exit 5 を返す
# ため、1 回に畳むと「不正な JSON」と「期待した形でない」が deny 理由から区別できなくなる。
printf '%s\n' "$INPUT" | jq -e . >/dev/null 2>&1 ||
  deny_unreadable 'JSON として parse できない'

# command が空 / 不在なのは正常系。deny するのは tool_input 自体が object でないとき —
# #598 で実測した素通し経路そのもの。
COMMAND=$(printf '%s\n' "$INPUT" | jq -r 'if (.tool_input | type) != "object" then error("tool_input is not an object") else (.tool_input.command // empty) end' 2>/dev/null) ||
  deny_unreadable 'tool_input が object でない'

# here-document の本体は shell がデータとして読むだけで実行しないため、判定対象から
# 落とす。危険なコマンドの説明文を `cat > doc.md <<EOF` で書くだけで deny される誤爆を
# 塞ぐ。開始行そのものは残す — `bash -c id && cat <<EOF` のように実コマンドが
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

# bash -c / sh -c / zsh -c
if printf '%s\n' "$COMMAND" | grep -qE '(^|[; &|])(bash|sh|zsh) +-c( |$)'; then
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"bash -c / sh -c / zsh -c のインライン実行は禁止。スクリプトファイル化するか、個別の Bash ツール呼び出しに分解してください"}}\n'
  exit 0
fi

passthrough
