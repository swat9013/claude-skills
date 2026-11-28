#!/usr/bin/env bash
set -euo pipefail

# goal-drive の対話セッションの名前 goal-dialog-<語>-<n> を、herdr が受け付ける 32 字以内で出す。
# 識別子の形の正本は SKILL.md の「goal の記録」。<語> は識別子から先頭の日付 (YYYY-MM-DD-) を除いたもの。32 字を超えるなら、語の末尾の -<番号>
# (記録の名前が重なったときに足したもの) より前を末尾から削って収める。

USAGE="usage: $0 <goal の識別子> <n (対話の pane の数。今開いた pane を含む)>"
ID="${1:?$USAGE}"
N="${2:?$USAGE}"
[[ $N =~ ^[0-9]+$ ]] || { echo "n は数字にする: $N" >&2; exit 2; }

WORD="${ID#[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-}"
SUFFIX=""
if [[ $WORD =~ ^(.+)(-[0-9]+)$ ]]; then
  WORD="${BASH_REMATCH[1]}"
  SUFFIX="${BASH_REMATCH[2]}"
fi

PREFIX="goal-dialog-"
TAIL="${SUFFIX}-${N}"
ROOM=$((32 - ${#PREFIX} - ${#TAIL}))
(( ROOM > 0 )) || { echo "n が長すぎて 32 字に収まらない: $N" >&2; exit 2; }
WORD="${WORD:0:ROOM}"
while [[ $WORD == *- ]]; do WORD="${WORD%-}"; done

NAME="${PREFIX}${WORD}${TAIL}"
[[ $NAME =~ ^[a-z][a-z0-9-]*$ ]] || { echo "herdr が受け付けない字を含む: $NAME" >&2; exit 2; }
echo "$NAME"
