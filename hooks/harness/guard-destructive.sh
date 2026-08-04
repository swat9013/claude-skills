#!/bin/sh
# jq 必須 (fail-closed)
if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"jq not found on PATH; destructive guard fails closed"}}'
  exit 0
fi

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

deny() {
  jq -nc --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# rm with recursive AND force flag が、root / system / home パスを対象に
# している場合のみ deny。短縮形 (-rf / -fr / -rfv)、長形式 (--recursive --force /
# --force --recursive)、分離形 (-r -f) を網羅。
# system パス: /etc /usr /var /Users /home /bin /sbin /Library /System
# /Applications /opt。home パス: ~ / ~/ / $HOME / ${HOME}。
#
# rm の検出は任意の path 接頭辞 (`/bin/rm` `/usr/bin/rm` `./bin/rm`) を許容する
# (guard-shell.sh が `/bin/sh` を捕捉するのと同型)。素の `rm` だけを見ていた頃は
# 接頭辞の `/` が語頭境界を満たさず `/bin/rm -rf /` が素通りしていた。接頭辞は
# 空白以外を許すため `cd /tmp;/bin/rm` のような区切り直後の綴りも捕捉する。
# `rm` の直前にリテラル `/` を要求するので `--rm` / `alarm` / `/tmp/alarm` は非該当。
# 空白境界の alternative は維持する (`command rm` / `env rm` の既存捕捉を保つ)。
#
# 語頭・末尾の境界クラスには quote を含める。削除**対象**側の判定は元から quote を
# 境界に含んでいたのに、コマンド語側だけ想定していなかったため `"/bin/rm" -rf /etc` /
# `'rm' -rf /etc` が検出前に落ちて deny 判定が一切走らなかった。flag 側 (has_r /
# has_f) も同様に広げる — コマンド語だけ quote 対応しても `'rm' '-rf' '/'` が抜ける。
# 代償として `git commit -m "rm -rf /"` のような文字列引数も deny に落ちるが、
# shell parser 無しに実コマンドと区別する術は無く、fail-closed 側なので受容する
# (回帰テスト test_rm_rf_inside_quoted_argument_is_denied_by_design で pin 済み)。
#
# 境界クラスには区切り (`;` `&` `|` `(` `)`) も含める。接頭辞つきの `cd /tmp;/bin/rm`
# は接頭辞が `/` で終わるので旧境界でも捕捉できたが、接頭辞の無い `cd /tmp;rm -rf /` /
# `(rm -rf /etc)` / `true&&rm -rf /etc` は語頭が区切りで境界を満たさず素通りしていた。
has_rm=$(printf '%s\n' "$COMMAND" | grep -cE "(^|[[:space:]\"';&|()])([^[:space:]]*/)?rm([[:space:]\"';&|()]|\$)" || true)
if [ "$has_rm" -gt 0 ]; then
  has_r=$(printf '%s\n' "$COMMAND" | grep -cE "(^|[[:space:]\"';&|()])(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)([[:space:]\"';&|()]|\$)" || true)
  has_f=$(printf '%s\n' "$COMMAND" | grep -cE "(^|[[:space:]\"';&|()])(-[a-zA-Z]*f[a-zA-Z]*|--force)([[:space:]\"';&|()]|\$)" || true)
  if [ "$has_r" -gt 0 ] && [ "$has_f" -gt 0 ]; then
    # 以降の path 判定は「削除対象」に対して行う。rm バイナリ自身の綴り (`/bin/rm`
    # `/usr/bin/rm`) は対象ではないので、コマンド語の path 接頭辞だけを剥がした
    # TARGETS に対して判定する (剥がさないと `/bin/rm -rf ./node_modules` が自分の
    # `/bin` で system path deny に落ちる)。剥がす範囲は区切り (`;` `&` `|` `(` `)`) を
    # またがせない — またがせると `rm -rf /etc && /bin/rm ...` の `/etc` まで巻き込んで
    # 消え、deny 対象が見えなくなる (安全側 = 消さない)。
    # quote つきコマンド語 (`"/bin/rm"` `'/bin/rm'`) も剥がす — 剥がさないと
    # `"/bin/rm" -rf ./node_modules` が自分の `/bin` で system path deny に落ちる。
    # 末尾側には区切りを足さない。足すと `rm -rf /etc/rm;ls` の削除対象まで
    # コマンド語と誤認して剥がしてしまう (剥がす範囲は狭いほど安全側)。
    TARGETS=$(printf '%s\n' "$COMMAND" | sed -E "s#(^|[[:space:];&|()\"'])[^[:space:];&|()\"']*/rm([[:space:]\"']|\$)#\1rm\2#g")
    # system path 検出。/etc 配下まで含めて deny (system 配下は全部危険)。
    # 境界: 先頭 = 空白/quote、末尾 = 空白/quote/区切り/EOL/`/`。
    # 末尾に区切り (`;` `&` `|` `(` `)`) を含めないと `rm -rf /etc;ls` が素通りする
    # (`rm -rf /etc && ls` は空白があるので従来から deny されていた非対称)。
    if printf '%s\n' "$TARGETS" | grep -qE "(^|[[:space:]\"'])/(etc|usr|var|Users|home|bin|sbin|Library|System|Applications|opt)([[:space:]\"'/;&|()]|$)"; then
      deny "rm -rf 系で system path (/etc /usr /var 等) への破壊操作は禁止"
    fi
    # 裸 root: `/` 単独 / `/*`。
    if printf '%s\n' "$TARGETS" | grep -qE "(^|[[:space:]\"'])/([[:space:]\"';&|()]|\*|$)"; then
      deny "rm -rf / は禁止"
    fi
    # 裸 home: `~` 単独 / `~/` 単独 (`~/foo` は元の spec で allow)。
    # 末尾の `/` を含めないため `~/foo` は match しない。
    if printf '%s\n' "$TARGETS" | grep -qE "(^|[[:space:]\"'])(~|~/)([[:space:]\"';&|()]|\$)"; then
      deny "rm -rf ~ は禁止"
    fi
    # $HOME / ${HOME} は変数展開で広範囲指定なので、削除対象 (TARGETS) 中に現れたら
    # 位置を問わず deny。rm バイナリ自身の path (`$HOME/bin/rm`) は TARGETS 生成時に
    # 剥がされるため対象外 — 原 spec の「位置を問わず」から、この 1 点だけ狭めている。
    # shellcheck disable=SC2016  # 展開させない: `$HOME` は検出対象の文字列そのもの
    if printf '%s\n' "$TARGETS" | grep -qE '(\$HOME|\$\{HOME\})'; then
      deny "rm -rf \$HOME は禁止"
    fi
  fi
fi

# find / docker の検出も rm と同じく任意の path 接頭辞 (`/usr/bin/find`
# `/usr/local/bin/docker`) を許容する。素の語頭境界だけを見ていた頃は接頭辞の `/` が
# 境界を満たさず素通りしていた。接頭辞は直前にリテラル `/` を要求するので `xfind` /
# `nodocker` は非該当。語頭境界に区切りを含めるのも rm 側と同じ理由
# (接頭辞なしの `cd /tmp;find . -delete` が素通りしていた)。
# quote 対応は rm 側のみ。find / docker の語頭境界に quote を足すと
# `git commit -m "docker rm -f x"` のような文字列引数まで deny に落ちるため据え置く
# (rm 側は quote つきコマンド語の実害が大きく、過剰マッチを受容する判断)。

# find ... -delete
if printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:];&|()])([^[:space:]]*/)?find[[:space:]].*-delete([[:space:]]|$)'; then
  deny "find -delete は禁止。事前に -print で対象を確認してから個別削除してください"
fi

# docker dangerous ops
if printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:];&|()])([^[:space:]]*/)?docker[[:space:]]+rm[[:space:]]+(-f|--force)([[:space:]]|$)'; then
  deny "docker rm -f は禁止。docker stop の後で削除してください"
fi
if printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:];&|()])([^[:space:]]*/)?docker[[:space:]]+volume[[:space:]]+rm([[:space:]]|$)'; then
  deny "docker volume rm はデータ消去のため禁止"
fi
if printf '%s\n' "$COMMAND" | grep -qE '(^|[[:space:];&|()])([^[:space:]]*/)?docker[[:space:]]+system[[:space:]]+prune([[:space:]]+.*)?[[:space:]]+(-f|--force)([[:space:]]|$)'; then
  deny "docker system prune -f は禁止"
fi

exit 0
