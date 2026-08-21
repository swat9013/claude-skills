#!/bin/sh
# PreToolUse (Write|Edit): file_path が機密パスなら deny する最終防衛線。
#
# 本 hook は fail-closed: payload を読めなかったとき (JSON として不正 / 期待した形でない /
# jq が無い) は判断保留 = 素通しにせず deny する。読み取り失敗を素通しにすると、payload が
# 想定外の形になった瞬間に .env / 鍵 / credentials への Write・Edit が無検査で通る (#598)。
# 受容コスト: jq の無い環境では Write / Edit が全て止まる。同じ harness の他の guard
# (guard-shell.sh 等) は依存欠落時の素通しを意図して選んでいるが、本 hook が守るのは漏れたら
# 取り返しがつかない機密ファイルで、素通しの損害が停止の損害を上回るため posture を分ける。
# 停止した場合は deny 理由に内訳が出るので、それを見て jq を入れる。

INPUT=$(cat)

# 読み取り失敗の deny。内訳 ($1) を理由に載せる — 理由の分からない deny は踏んだ人間が
# hook 自体を無効化する方向に働くため、機密パス deny (下段) と文言で区別する。
deny_unreadable() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"payload を読めなかったため Write / Edit を停止した (fail-closed): %s"}}\n' "$1"
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

# file_path が空 / 不在なのは正常系 (NotebookEdit 等 file_path を持たない payload) なので
# deny しない。deny するのは tool_input 自体が object でないとき。
FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r 'if (.tool_input | type) != "object" then error("tool_input is not an object") else (.tool_input.file_path // empty) end' 2>/dev/null) ||
  deny_unreadable 'tool_input が object でない'

if printf '%s\n' "$FILE_PATH" | grep -qE '\.(env|key|pem|p12|pfx|crt)$|id_rsa|\.netrc|/credentials$|/secrets/'; then
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"機密ファイルへの書き込みは禁止"}}\n'
  exit 0
fi

passthrough
