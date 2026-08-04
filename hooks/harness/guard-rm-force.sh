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
# jq が無ければ deny せず素通しする (fail-open)。承認プロンプトを減らすための
# hook が jq 不在で全 Bash を止めるのは本末転倒なので、guard-destructive.sh の
# fail-closed とは逆に倒す (allow 専用の allow-tmp-delete.sh と同じ判断)。
command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

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
fi

exit 0
