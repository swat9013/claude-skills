#!/bin/sh
# インラインコード組み立て実行を遮断する PreToolUse guard。
# bash -c / sh -c / zsh -c / perl -e / ruby -e / node -e を対象とする。
# LLM が任意コードを生成・実行する経路を塞ぐ（Computational First / プロンプトインジェクション対策）。
#
# python の inline (`-c` / stdin の `-`) は guard-bulk-stage.sh が担う。
# 同じ系統を 2 hook が deny すると、どちらの message が届くかが仕様化されていないため、
# 代替形つきの message を持つ側へ一本化した (env 代入つきの綴り `env FOO=1 python3 -c` も
# 向こうの head 判定が読み飛ばして捕捉する)。
#
# スコープ外（Feedback で捕捉しない領域）:
# - `print('hypothesis')` など「仮説メモを Python ソースに埋め込む」用法は構文的に区別不能なため対象外。
#   これは Feedforward（指示文側で「思考メモは自然文で書く」と規定する）で抑止する。
# - 通常の `python3 script.py` や `uv run script.py` は対象外（スクリプト実行は許可）。

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

# bash -c / sh -c / zsh -c
if printf '%s\n' "$COMMAND" | grep -qE '(^|[; &|])(bash|sh|zsh) +-c( |$)'; then
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"bash -c / sh -c / zsh -c のインライン実行は禁止。スクリプトファイル化するか、個別の Bash ツール呼び出しに分解してください"}}\n'
  exit 0
fi

# perl -e / ruby -e / node -e
if printf '%s\n' "$COMMAND" | grep -qE '(^|[; &|])(perl|ruby|node) +-e( |$)'; then
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"perl/ruby/node の -e によるインライン実行は禁止。スクリプトファイル化してください"}}\n'
  exit 0
fi

exit 0
