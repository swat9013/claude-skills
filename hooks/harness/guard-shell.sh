#!/bin/sh
INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

# bash/sh/zsh -n <file> は構文チェックのみ (実行しない) で安全。allow 済の syntax-check.sh
# wrapper へ変換して pre-sanction 枠へ誘導する (permission=意図表明 / hook=変換)。settings.deny の
# Bash(bash|sh|zsh:*) は hook の allow より優先されるため、deny にマッチしない wrapper 経由で
# native の -n を実行させる。wrapper が第1引数を bash/sh/zsh に限定し踏み台化を防ぐ。
#
# wrapper は harness 外 (利用者環境の設定資産) にあり、本 hook 単体では同梱できない。
# 不在環境では rewrite せず素の `-n` をそのまま allow する: `-n` は構文解析のみで
# スクリプトを実行しないため、wrapper 経由でなくとも安全側に閉じている。
# (SHELL_SYNTAX_CHECK_BIN はテスト注入点。emit する command は tilde 表記のまま — 利用者の
#  permission entry がテキスト一致で照合するため、絶対 path へ展開してはならない)
# shellcheck disable=SC2088  # 展開させない: emit 先 (permission 照合) が tilde 表記を要求する
SYNTAX_CHECK_TILDE="~/.claude/scripts/syntax-check.sh"
# ${var#\~/} の `~` は macOS /bin/sh で展開されるためエスケープする (allow-tmp-delete.sh と同様)
SYNTAX_CHECK_BIN="${SHELL_SYNTAX_CHECK_BIN:-$HOME/${SYNTAX_CHECK_TILDE#\~/}}"

# 以下 2 つの allow 分岐は「単一コマンドである」ことを前提にする。区切り (`;` `&` `|`) /
# 置換 (`$(` backtick) / リダイレクト / subshell を含む場合は allow せず、下の複合行 rewrite
# または deny 判定へ落とす。`bash -n a.sh;whoami` のような追い焚きを 1 つの allow で通さない
# ため。hook の allow は deny / ask rule までは越えない (公式 permissions: "Claude Code
# evaluates deny and ask rules regardless of what a PreToolUse hook returns") が、どちらの
# rule も持たないコマンドは素通りするので、allow を出す範囲は単一コマンドに絞る。
# shellcheck disable=SC2016  # 展開させない: `$` `` ` `` は検出対象の文字そのもの
if printf '%s\n' "$COMMAND" | grep -q '[;&|<>`$()]'; then
  SINGLE_COMMAND=0
else
  SINGLE_COMMAND=1
fi

# 置換・リダイレクトの有無だけを別に見る (区切り `;` `&` `|` は含めない)。下の複合行 rewrite は
# 区切りを許容するが、置換 (`$(` backtick) とリダイレクトは許容しない — 引数の中に紛れ込むと
# 書き換え後も残り、permission 層が読めない位置でコマンドが走るため
# (`bash -n 'a.sh`whoami`'` のような綴り)。
# shellcheck disable=SC2016  # 展開させない: `$` `` ` `` は検出対象の文字そのもの
if printf '%s\n' "$COMMAND" | grep -q '[<>`$()]'; then
  HAS_SUBSTITUTION=1
else
  HAS_SUBSTITUTION=0
fi

if [ "$SINGLE_COMMAND" = 1 ] && printf '%s\n' "$COMMAND" | grep -qE '^(bash|sh|zsh) -n [^ ]+$'; then
  SH=$(printf '%s\n' "$COMMAND" | cut -d' ' -f1)
  FILE=$(printf '%s\n' "$COMMAND" | sed -E 's/^(bash|sh|zsh) -n //')
  if [ -x "$SYNTAX_CHECK_BIN" ]; then
    jq -nc --arg cmd "$SYNTAX_CHECK_TILDE $SH $FILE" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow",updatedInput:{command:$cmd}}}'
  else
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"bash/sh/zsh -n は構文チェックのみで実行しないため許可"}}\n'
  fi
  exit 0
fi

# 複合行に混ざった `<shell> -n <file>` も wrapper 形へ書き換える。`bash -n a.sh && shellcheck a.sh`
# のように構文チェックを他の検査と 1 行に繋ぐのはモデルの自然な綴りだが、上の単一コマンド分岐は
# 区切りを含む行を対象外にするため deny へ落ちていた (観測窓 30 日で bash -n 36 件 / zsh -n 12 件)。
# 構文チェックは実行を伴わないので、複合行だからといって危険にはならない — 危険だったのは
# 「1 つの allow で行全体を通す」ことのほうなので、ここでは **allow を返さず rewrite だけ返す**。
# permission 評価は書き換え後の行に対して通常どおり走り、同居する他の sub-command
# (`shellcheck a.sh` / `whoami` 等) はそれぞれの rule で従来どおり評価される。
#
# 書き換え後に interpreter の直接起動が残る場合 (`bash -n a.sh && bash deploy.sh` 等) は
# rewrite せず下の deny へ落とす。wrapper 不在環境も同様 — 書き換え先が無い以上、deny を
# 回避する手段が無い (hook の allow は permission の deny を越えられない)。
if [ "$SINGLE_COMMAND" = 0 ] && [ "$HAS_SUBSTITUTION" = 0 ] && [ -x "$SYNTAX_CHECK_BIN" ] && \
   printf '%s\n' "$COMMAND" | grep -qE '(^|[;&|])[[:space:]]*(bash|sh|zsh) -n [^ ;&|]+'; then
  REWRITTEN=$(printf '%s\n' "$COMMAND" | sed -E "s#(^|[;&|][[:space:]]*)(bash|sh|zsh) -n #\\1${SYNTAX_CHECK_TILDE} \\2 #g")
  if ! printf '%s\n' "$REWRITTEN" | grep -qE '(^|[;&|({`])[[:space:]]*([^[:space:];&|()]*/)?(bash|sh|zsh)([^[:alnum:]_.-]|$)'; then
    jq -nc --arg cmd "$REWRITTEN" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",updatedInput:{command:$cmd}}}'
    exit 0
  fi
fi

# bash/sh/zsh <script> で「絶対パス (/) または明示相対 (./ ../) のスクリプトファイル」を実行
# する場合は、deny でなく interpreter prefix を除去した直接パス起動へ rewrite する (permission=
# 意図表明 / hook=変換)。拒否センサーで突出する `bash /abs/skill.sh` 系の deny を、踏ませてから
# 案内する feedback でなく friction ゼロの feedforward に転換する狙い。bare name (`bash deploy.sh`)
# は PATH 検索/cwd 依存で意味が変わるため対象外とし下の deny で `./` 付き起動を案内する。`-c`/`-n`
# 等オプション始まりは (/|./|../) に非該当ゆえ自然に除外される (`-n` は上で syntax-check へ処理済み)。
if [ "$SINGLE_COMMAND" = 1 ] && printf '%s\n' "$COMMAND" | grep -qE '^(bash|sh|zsh) +(/|\./|\.\./)'; then
  REWRITTEN=$(printf '%s\n' "$COMMAND" | sed -E 's/^(bash|sh|zsh) +//')
  jq -cn --arg cmd "$REWRITTEN" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow",updatedInput:{command:$cmd}}}'
  exit 0
fi

# bash/sh/zsh を「コマンド位置」(単純コマンドの先頭語) で直接起動する場合のみ deny する。
# 判定: interpreter が 文頭 or 区切り (`;` `&` `|` `(` `{` backtick、`&&`/`||` 含む) の直後に
# あり、間に他のコマンド語が無いこと。任意の path 接頭辞 (`/bin/`, `/usr/bin/` 等) は許容し
# `/bin/sh` も捕捉する。これにより `bash x` `cd && sh y` `| zsh` `/bin/sh` は deny、対して
# 引数・文字列位置のトークン (`grep bash` `man zsh` `commit -m "...bash..."`) と `foo.sh` 等の
# スクリプトパス引数、bash/sh/zsh トークンを含まない制御構文 (for/while ループ — Claude Code の
# matcher が Bash(sh:*) へ過剰マッチして本 hook に届く) は誤検知しない。trailing は語境界
# (`bashx`/`shell` を除外)。非該当は passthrough (exit 0 無出力) し、通常の permission 評価 +
# 後続 guard に委ねる。
if printf '%s\n' "$COMMAND" | grep -qE '(^|[;&|({`])[[:space:]]*([^[:space:];&|()]*/)?(bash|sh|zsh)([^[:alnum:]_.-]|$)'; then
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"bash/sh/zshの直接実行は禁止。スクリプトの実行は直接パス起動（./script.sh または /abs/path/script.sh）を使用。構文チェックは bash -n <file> / zsh -n <file>。"}}\n'
  exit 0
fi

# bash/sh/zsh の直接起動を含まない (for/while ループ等の制御構文) → passthrough
exit 0
