#!/bin/sh
# guard-rm-force.sh — 単独コマンドとしての `rm` の force フラグを deny する PreToolUse hook。
#
# Why: 削除に `-f` を付けると Claude Code の auto mode classifier が承認プロンプトへ
# 回すことがある一方、force なしの `rm` は sandbox 内で auto-allow される
# (settings/README.md の autoAllowBashIfSandboxed / ADR 0024)。`-f` は「対象が
# 無くてもエラーにしない」以上の意味を持たないので、ファイルは `rm <file>`、
# フォルダは `rm -r <dir>` の綴りへ寄せる。deny reason はモデルにフィードバック
# されるため、代替の綴りをそこで示す。
#
# recursive の有無は問わない (`rm -rf` も deny する)。system path / home を対象に
# した `rm -rf` は guard-destructive.sh も deny するが、deny は重なっても結果が
# 変わらないので、force の判定を両者で分掌しない。
#
# 本 hook は fail-closed: payload を読めなかったとき (jq が無い / JSON として不正 / 期待した
# 形でない) は判断保留 = 素通しにせず deny する。guard は deny 権能を持つ以上 **原則
# fail-closed** で、fail-open を選ぶには「素通しの損害が回復可能であること」を示す脅威モデルを
# 要する (ADR 0046)。本 hook にその脅威モデルは無い — 読み取り失敗を素通しにすると `COMMAND`
# が空文字になり、下の全判定が不成立のまま passthrough して `rm -f` が無検査で通る。
#
# 旧 posture (fail-open) は「承認プロンプトを減らすための hook が jq 不在で全 Bash を止めるのは
# 本末転倒」を根拠に挙げていたが、この根拠は実測で空振りしていた: 発火条件を持たない
# guard-destructive.sh / guard-pipe-execute.sh が jq 不在時に既に全 Bash を deny しており、
# 反転の**増分**可用性コストはゼロである (#658)。根拠を他 guard への参照で継承することは
# ADR 0046 が禁じているので、ここでは原則そのものを引く。
#
# 受容コスト: 本 hook は hooks.json に `if` gate を持たず **全 Bash 呼び出しで発火する**ため、
# jq が PATH から消えた瞬間に Bash が全面停止する。jq は guard 機構の宣言済み前提インフラで
# あり、その不在は環境破損として全 Bash 停止で検知するのが正しい縮退挙動 (ADR 0046)。deny 理由に
# 内訳と hook 名が出るので、それを見て jq を入れれば復旧する。
#
# stdin は jq 検査より先に読み切る。読まずに deny して先に exit すると書き手側が EPIPE を
# 踏みうる (guard-inline-python.sh / guard-shell.sh と同順)。
INPUT=$(cat)

deny_unreadable() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"payload を読めなかったため Bash を停止した (fail-closed): %s [guard-rm-force.sh]"}}\n' "$1"
  exit 0
}

# pass (判断を出さない) 出口はすべて passthrough() を通す。無出力の exit は transcript に
# attachment を残さず、棚卸しで「壊れて死んだ guard」と「窓内に出番が無かった guard」が同じ
# 見え方になる (#587 / ADR 0043)。permissionDecision を持たない envelope は通常の permission
# フローへ委ねるので、判断の意味論は無出力のときと変わらない。逐語で 1 行に保つ (テストが
# 全 guard の一致を見る)。
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

# command が空 / 不在なのは正常系。deny するのは tool_input 自体が object でないとき。
COMMAND=$(printf '%s\n' "$INPUT" | jq -r 'if (.tool_input | type) != "object" then error("tool_input is not an object") else (.tool_input.command // empty) end' 2>/dev/null) ||
  deny_unreadable 'tool_input が object でない'

# here-document の本体は shell がデータとして読むだけで実行しないため、判定対象から
# 落とす。危険なコマンドの説明文を `cat > doc.md <<EOF` で書くだけで deny される誤爆を
# 塞ぐ。開始行そのものは残す — `rm -f x && cat <<EOF` のように実コマンドが
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

# 単独コマンドとしての rm だけを見るため、コマンド区切りで segment へ割ってから
# 各 segment の先頭語で判定する。`jq -r -f filter.jq` のような別コマンドの -f を
# rm の flag と誤認しないための境界 (guard-destructive.sh が踏んだ誤爆の再発防止)。
# quote は awk へ渡す前に落として、先頭語の綴り (`'rm'` / `"/bin/rm"`) を素の語に揃える
# (awk 側の program は single quote で括るのでリテラルの `'` を書けない)。
if printf '%s\n' "$COMMAND" | tr -d "\"'" | awk '
  # segment の先頭語が rm で、force フラグが付いていれば 1。
  function is_forced_rm(text,   count, token, i, command_word) {
    count = split(text, token, " ")
    if (count == 0) return 0
    command_word = token[1]
    sub(/^.*\//, "", command_word)             # path 接頭辞つきの綴り (/bin/rm) を揃える
    if (command_word != "rm") return 0         # git rm / docker rm 等のサブコマンドは対象外
    for (i = 2; i <= count; i++) {
      if (token[i] == "--") return 0           # 以降は flag ではなく削除対象
      if (token[i] == "--force") return 1
      if (token[i] ~ /^--/) continue           # 他の long flag (--recursive 等)
      if (token[i] !~ /^-[A-Za-z]+$/) continue # 短縮 flag クラスタ以外 (削除対象など)
      if (token[i] ~ /f/) return 1             # クラスタ内の f (-rf / -fr / -vf 等)
    }
    return 0
  }
  # 行単位で判定すると `rm \` + 改行 + `  -rf x` を取り逃す (2 行目の先頭語が -rf になる)。
  # 全行を溜めてから継続行を連結し、残った改行はコマンド区切りとして segment に割る。
  # backtick も区切りに含める (`` `rm -f x` `` の先頭語を素の rm に揃えるため)。
  { all = all $0 "\n" }
  END {
    gsub(/\\\n/, " ", all)
    count = split(all, segment, "[;|&()`\n]+")
    for (i = 1; i <= count; i++) if (is_forced_rm(segment[i])) found = 1
    if (found) exit 0
    exit 1
  }
'; then
  # 文面は Claude への steering を兼ねる。backtick を含むので single quote で括る
  # (double quote だとコマンド置換として展開され、hook 自身が rm を実行する)。
  jq -nc --arg r 'ファイル削除は `rm <file>`、フォルダ削除は `rm -r <dir>` を使う (sandbox 内なら承認プロンプトなしで通る)。存在しない可能性がある対象は `rm <対象> 2>/dev/null || true` を使う。`-f` は使わない。' \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
fi

passthrough
