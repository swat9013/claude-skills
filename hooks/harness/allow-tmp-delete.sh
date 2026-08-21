#!/bin/sh
# allow-tmp-delete.sh — tmp ルート配下のみの rm を allow する許可専用 PreToolUse hook。
#
# fail-safe: 静的に安全と確証できないケースは一切 allow せず passthrough() で
# 素通しし、guard-destructive.sh の deny 判定と settings の ask に委ねる。
# guard-destructive.sh (deny) とは逆方向 (こちらは allow 専用)。
#
# 承認ルート: /tmp /private/tmp $HOME/.claude/tmp <cwd>/tmp の各「配下」
# (ルート自体は対象外)。recursive 削除 (-rf) 可。glob/変数/置換/quote/未知 flag/
# tmp 外混在/.. 脱出/ルート自体 は素通し。
#
# 受理する rm の綴り (リテラル一致のみ): `rm` / `/bin/rm` / `/usr/bin/rm`。
# それぞれ先頭に単一 token `command` を伴ってよい。allow は「その実行ファイルを
# 承認なしで起動する」決定なので、basename が rm というだけの任意パス
# (`./rm` / `/opt/x/bin/rm` 等) は受理しない。`\rm` / `env rm` / `sudo rm` も素通し。
#
# hooks.json 側の `if` gate は付けない。gate は token 境界照合のため `/bin/rm` を
# 弾いてしまい、綴り違いで自動許可を失う (issue #361)。判定は本 script に一本化する。

# pass (allow を出さない) 出口はすべてここを通す。無出力の exit は transcript に attachment を
# 残さず、棚卸しで「壊れて死んだ hook」と「窓内に出番が無かった hook」が同じ見え方になる
# (#587 / ADR 0043)。permissionDecision を持たない envelope は通常の permission フローへ
# 委ねるので、判断の意味論は無出力のときと変わらない。逐語で 1 行に保つ (テストが全 guard の
# 一致を見る)。
passthrough() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse"},"suppressOutput":true}\n'
  exit 0
}

# jq 必須 (無ければ allow しない = fail-safe)
command -v jq >/dev/null 2>&1 || passthrough

INPUT=$(cat)
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty')

[ -n "$COMMAND" ] || passthrough

# --- G1: コマンド形状 ---
trimmed=$(printf '%s' "$COMMAND" | sed 's/^[[:space:]]*//')
# 連結 / リダイレクト / 置換 / 変数 / glob / quote を含むなら素通し
printf '%s' "$COMMAND" | grep -q '[|&;<>]' && passthrough   # パイプ/連結(&& ||)/リダイレクト
printf '%s' "$COMMAND" | grep -q '[$`]'    && passthrough   # 変数展開 / コマンド置換
printf '%s' "$COMMAND" | grep -q '[*?]'    && passthrough   # glob (* ?)
printf '%s' "$COMMAND" | grep -q '\['      && passthrough   # glob ([...])
printf '%s' "$COMMAND" | grep -q "['\"]"   && passthrough   # quote (tokenize 単純化)
printf '%s' "$COMMAND" | grep -q '[\]'     && passthrough   # backslash (\rm / エスケープ)

set -f                       # glob 無効化 (二重防御)
# shellcheck disable=SC2086
set -- $trimmed              # IFS 空白で token 分割 (quote/glob は上で排除済)

# 先頭 token が受理する rm の綴りであること。sudo rm / rmtrash / find 等を排除。
[ "${1:-}" = "command" ] && shift
case "${1:-}" in
  rm|/bin/rm|/usr/bin/rm) shift ;;
  *) passthrough ;;
esac

# --- G2: フラグ検査 + target 抽出 ---

targets=""
afterdd=0
for tok in "$@"; do
  if [ "$afterdd" -eq 1 ]; then
    targets="$targets
$tok"
    continue
  fi
  case "$tok" in
    --) afterdd=1 ;;
    --recursive|--force|--verbose|--interactive|--dir) : ;;
    --*) passthrough ;;                       # 未知 long flag
    -*)
      opt=${tok#-}
      case "$opt" in
        ""|*[!rRfivd]*) passthrough ;;        # "-" 単体 or 許可外文字
        *) : ;;                          # r R f i v d クラスタのみ
      esac
      ;;
    *) targets="$targets
$tok" ;;
  esac
done

[ -n "$targets" ] || passthrough              # target 0 個

# --- G3: 全 target が承認ルート配下か ---
HOME_DIR=${HOME:-}

# 承認ルートの正規化パス一覧を構築 (cd できる = 存在するものだけ)。
# 各ルートは pwd -P で symlink を解決する。/tmp→/private/tmp (macOS) を吸収する
# 一方、symlink な root はその実体まで allow 集合を広げる点に注意。
# 特に <cwd>/tmp が symlink だと作業ディレクトリ外を指しうるが、root を仕込むには
# その場所への書き込み権限が前提のため、本ハーネスの脅威モデルでは許容する。
canon_roots=""
add_root() {
  [ -n "$1" ] || return 0
  cr=$(cd "$1" 2>/dev/null && pwd -P) || return 0
  canon_roots="$canon_roots
$cr"
}
add_root "/tmp"
add_root "/private/tmp"
[ -n "$HOME_DIR" ] && add_root "$HOME_DIR/.claude/tmp"
[ -n "$CWD" ] && add_root "${CWD%/}/tmp"

[ -n "$canon_roots" ] || passthrough

# canon_parent が承認ルートのいずれかと等しい or その配下なら 0。
under_root() {
  cp="$1"
  oi=$IFS
  IFS='
'
  # shellcheck disable=SC2086
  for r in $canon_roots; do
    [ -n "$r" ] || continue
    case "$cp/" in
      "$r/"*) IFS=$oi; return 0 ;;
    esac
  done
  IFS=$oi
  return 1
}

OLDIFS=$IFS
IFS='
'
# shellcheck disable=SC2086
for t in $targets; do
  [ -n "$t" ] || continue
  # ~ 展開: case でリテラル ~ を比較する (tilde 展開は不要、SC2088 無効化)
  # ${t#~/} は macOS /bin/sh で ~ を HOME に展開するため \~/ でエスケープ
  # shellcheck disable=SC2088
  case "$t" in
    "~"|"~/") IFS=$OLDIFS; passthrough ;;     # home 自体は対象外
    "~/"*) t="$HOME_DIR/${t#\~/}" ;;
    ~*) IFS=$OLDIFS; passthrough ;;            # ~user 非対応
  esac
  # 相対 → cwd 基準
  case "$t" in
    /*) abs="$t" ;;
    *) [ -n "$CWD" ] || { IFS=$OLDIFS; passthrough; }; abs="${CWD%/}/$t" ;;
  esac
  leaf=$(basename "$abs")
  case "$leaf" in
    "."|"..") IFS=$OLDIFS; passthrough ;;      # . / .. は素通し
  esac
  parent=$(dirname "$abs")
  canon_parent=$(cd "$parent" 2>/dev/null && pwd -P) || { IFS=$OLDIFS; passthrough; }
  if ! under_root "$canon_parent"; then
    IFS=$OLDIFS
    passthrough
  fi
done
IFS=$OLDIFS

# --- G4: 全 target 通過 → allow ---
jq -nc '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow",permissionDecisionReason:"tmp ルート配下の削除を自動許可"}}'
exit 0
