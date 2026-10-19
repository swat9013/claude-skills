#!/bin/sh
# guard-git.sh — PreToolUse / Bash(git:*): 取り返しのつかない git 操作を deny し、push 1 断片だけの
# 呼び出しを allow する。
#
# 置く理由: backstop
#     `reset --hard` と force push は、失った変更を戻せないので、抜け道があると承知のうえで
#     止める (ADR 0108)。
#     次の deny はどの置く理由にも当たらず、外すと決まっている (実装は各 issue で行う):
#       - `clean -f` / checkout・restore による作業ツリーの復元 (#1288 の決定): stash や cp で同じ効果が
#         出るうえ、守る対象 (agent が作っていない未 commit 変更) は worktree の隔離が構造で守るので、
#         backstop として残す価値が往復の費用を上回らない
#       - `branch -D` / `tag -d` / `push --delete` (ADR 0108。実装は #1752)
#
# Registration: hooks/hooks.json PreToolUse / Bash matcher / if: Bash(git:*)

# stdin は jq 検査より先に読み切る。読まずに deny して先に exit すると書き手側が EPIPE を
# 踏みうる。
INPUT=$(cat)

# payload を読めなかったとき (jq が無い / stdin が空 / JSON として不正 / tool_input が object
# でない) は素通しせず deny する。
# 読み取り失敗を素通しにすると COMMAND が空文字になり、下の判定が
# 全部不成立のまま passthrough して、本 hook が塞いでいる破壊的 git 操作が無検査で通る。
# guard は deny 権能を持つので原則 fail-closed (ADR 0046)。停止範囲は発火条件
# (hooks.json の `Bash(git:*)`) に閉じるので、deny 理由の主語も git にする。
deny_unreadable() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"payload を読めなかったため git を停止した (fail-closed): %s [guard-git.sh]"}}\n' "$1"
  exit 0
}

command -v jq >/dev/null 2>&1 ||
  deny_unreadable 'jq が見つからない'

# 空白だけの stdin も空として扱う — `jq .` は空白だけの入力を exit 0・無出力で通す。
case $INPUT in
  *[![:space:]]*) ;;
  *) deny_unreadable 'stdin が空' ;;
esac

# JSON 検査と shape 検査で jq を 2 回呼ぶ。jq は parse error も error() も同じ exit 5 を返す
# ため、1 回に畳むと「不正な JSON」と「期待した形でない」が deny 理由から区別できなくなる。
# `-e` は付けない — 値が null / false の正しい JSON でも非 0 で終わり、形の不一致を
# 「parse できない」と誤報する (形の判定は次段の tool_input 検査が持つ)。
printf '%s\n' "$INPUT" | jq . >/dev/null 2>&1 ||
  deny_unreadable 'JSON として parse できない'

# command が空 / 不在なのは正常系。deny するのは tool_input 自体が object でないとき。
COMMAND=$(printf '%s\n' "$INPUT" | jq -r 'if (.tool_input | type) != "object" then error("tool_input is not an object") else (.tool_input.command // empty) end' 2>/dev/null) ||
  deny_unreadable 'tool_input が object でない'

# git のグローバルオプション (`git -C <dir>` / `git -c <k>=<v>` / `--git-dir=` 等) は
# subcommand の**前**に置ける。この形は 2 つの層を同時に素通りする:
#   - permission rule: 照合はコマンド先頭からの prefix なので `Bash(git push --force:*)`
#     のような deny に `git -C x push --force` は当たらない (docs/reference/settings-knowledge.md の
#     「permission rule の照合はコマンドの綴りに依存する」と同じ構造の穴)
#   - 本 hook: 行頭アンカー判定 (`^git branch` / `^git push` 等) が前置で外れる
# そこで判定用に前置を剥がした写し (SUBJECT) を作る。**原文 (COMMAND) は書き換えない** —
# 剥がすのは deny 判定を届かせるためで、allow の射程は広げない (下の push 許可を参照)。
# 限界: 値が空白を含む引用形 (`git -c "user.name=Foo Bar" push --force`) は 1 token として
# 剥がせず素通りする。剥がしを quote-aware にすると sh の範囲では別の誤爆を招くので広げない。
strip_git_global_opts() {
  _s=$1
  _i=0
  while [ "$_i" -lt 8 ]; do
    _n=$(printf '%s\n' "$_s" | sed -E 's/(^|[[:space:]])git[[:space:]]+(-C[[:space:]]+[^[:space:]]+|-c[[:space:]]+[^[:space:]]+|--git-dir=[^[:space:]]+|--work-tree=[^[:space:]]+|--namespace=[^[:space:]]+|--exec-path=[^[:space:]]+|--no-pager|--paginate|-p)[[:space:]]+/\1git /g')
    [ "$_n" = "$_s" ] && break
    _s=$_n
    _i=$((_i + 1))
  done
  printf '%s\n' "$_s"
}
SUBJECT=$(strip_git_global_opts "$COMMAND")

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

# 先頭の空白を許容するため `^[[:space:]]*git` を共通プレフィクスにする。

# `git add` の一括指定は deny しない。秘匿情報の巻き込みは repo ごとの gitleaks
# (pre-commit) で防ぐ (ADR 0077)。

# 破壊的 git 操作
# 理由文で stash を勧めない: stash の stack は worktree・並列 session 間で共有され、
# `git stash -u && git stash drop` で他者の未 commit 編集ごと消える事故が実際に起きた (refuter / i853)。
RESTORE_GUIDE="自分の変更を退避するなら一時 WIP commit を作る。自分が作っていない未 commit 変更は触らず、ユーザーに確認する"
# reset --hard は断片に閉じず、コマンドのどこに現れても当てる (下の 3 判定とは別)。断片に
# 閉じても通る綴りが増えるだけで、断片をまたいだ誤検知の報告も無い。
if printf '%s\n' "$SUBJECT" | grep -qE "git reset.*--hard"; then
  deny "破壊的git操作は禁止。${RESTORE_GUIDE}"
fi
# clean / checkout / restore は、コマンドを断片に分けてから 1 つの awk で判定する。分割は
# END の 1 か所だけに置き、3 判定が同じ断片を見る (判定ごとに区切りを綴ると、片方だけ境界が
# 割れて抜け道になる)。
#   - 断片の区切り: `;` `&` `|` `(` `)` バッククォート 改行 (行末 `\` の継続行は先につなぐ)
#   - token の区切り: 空白に加えてリダイレクト (`<` `>`) とクォート (`"` `'`)。
#     `git clean -fd>/dev/null` / `git clean "-fd"` の flag を 1 token として読む
#   - 判定の起点: 断片内の任意の位置の `git <subcommand>`。前に `{` / `then` / `do` / `time` /
#     `FOO=1` / `xargs` 等が付いても飛ばさない。path 付き (`/usr/bin/git`) と `\git` も含める。
#     flag と引数は subcommand の後ろから断片の終わりまでを見る
# 出力は当たった判定の名前 (clean / checkout / restore) を 1 行ずつ。理由文は下の case が
# clean → checkout → restore の順で選ぶ。awk が非 0 で終わったら deny する — 出力が空のまま
# 進むと 3 判定が全部不成立で素通しになり、guard の効き目が awk の実装 (mawk / gawk / busybox)
# の違いに左右される (ADR 0046 の fail-closed)。代入の終了コードは pipeline の最後 (awk) のもの。
DISCARDS=$(printf '%s\n' "$SUBJECT" | awk -v quote="'" '
  function is_git(word) {
    sub(/^\\/, "", word)
    return word ~ /(^|\/)git$/
  }
  # clean の force は短縮 flag の束ねのどこにあってもよい (`-f` / `-fd` / `-df` / `-xdf`)。
  # 断片の中だけを見るので `git clean -n && rm -f x` の `-f` を clean の flag と読まない。
  function clean_forces(token, from, count,   i) {
    for (i = from; i <= count; i++)
      if (token[i] == "--force" || token[i] ~ /^-[A-Za-z]*f[A-Za-z]*$/) return 1
    return 0
  }
  # checkout によるファイルの復元 (作業ツリーの変更を捨てる)。pathspec の区切り `--` があるか
  # (`git checkout -- x` / `git checkout HEAD -- x`)、引数が `.` / `..` そのものか `./` / `../`
  # で始まる path のとき (`git checkout .` / `./src` / `../x`)。この形は branch 名になれない
  # (git の refname 規則) ので、`git checkout <branch>` を巻き込まない。
  # 当たらない形: dotfile の path (`git checkout .gitignore`) と、`--` の無い
  # `git checkout <path>` / `git checkout <rev> <path>`。
  function checkout_restores(token, from, count,   i) {
    for (i = from; i <= count; i++)
      if (token[i] == "--" || token[i] ~ /^\.\.?(\/.*)?$/) return 1
    return 0
  }
  # restore の例外は index だけを戻す形 (`--staged` / `-S`) で、作業ツリーも書き換える
  # `--worktree` / `-W` を伴えば deny する。例外はその断片の中だけで見る — 断片の外へ漏らすと
  # `git restore --staged . && git restore x` の後段まで免除される。
  function restore_discards(token, from, count,   i, staged, worktree) {
    staged = 0; worktree = 0
    for (i = from; i <= count; i++) {
      if (token[i] == "--") break                  # 以降は pathspec
      if (token[i] == "--staged") staged = 1
      else if (token[i] == "--worktree") worktree = 1
      else if (token[i] ~ /^-s/) continue          # -s<tree> は値を伴う (束ねの文字を flag と読まない)
      else if (token[i] ~ /^-[A-Za-z]+$/) {        # 短縮 flag の束ね (-S / -W / -SW 等)
        if (token[i] ~ /S/) staged = 1
        if (token[i] ~ /W/) worktree = 1
      }
    }
    return !staged || worktree
  }
  { all = all $0 "\n" }
  END {
    gsub(/\\\n/, " ", all)
    gsub(/[<>"]/, " ", all)
    gsub(quote, " ", all)
    segments = split(all, segment, "[;&|()`\n]+")
    for (s = 1; s <= segments; s++) {
      count = split(segment[s], token, " ")       # 空白・タブの連なりを 1 境界にする
      for (j = 1; j < count; j++) {
        if (!is_git(token[j])) continue
        verb = token[j + 1]
        if (verb == "clean" && clean_forces(token, j + 2, count)) hit["clean"] = 1
        if (verb == "checkout" && checkout_restores(token, j + 2, count)) hit["checkout"] = 1
        if (verb == "restore" && restore_discards(token, j + 2, count)) hit["restore"] = 1
      }
    }
    for (name in hit) print name
  }
') ||
  deny "断片の判定 (awk) に失敗したため git を停止した (fail-closed) [guard-git.sh]"
case $DISCARDS in
  *clean*) deny "破壊的git操作は禁止。${RESTORE_GUIDE}" ;;
esac
case $DISCARDS in
  *checkout*) deny "git checkout によるファイルの復元 (-- <path> / .) は禁止。${RESTORE_GUIDE}" ;;
esac
case $DISCARDS in
  *restore*) deny "git restore（ワーキングツリー変更の破棄）は禁止。index だけを戻すなら git restore --staged <path>。${RESTORE_GUIDE}" ;;
esac

# Force delete branch: -D, --delete --force, --force --delete (順序逆転を含む)
if printf '%s\n' "$SUBJECT" | grep -qE '^[[:space:]]*git[[:space:]]+branch[[:space:]]'; then
  if printf '%s\n' "$SUBJECT" | grep -qE '(^|[[:space:]])-D([[:space:]]|$)'; then
    deny "git branch -D は禁止。git branch -d で merged 確認、未 merged の場合はユーザーに確認"
  fi
  if printf '%s\n' "$SUBJECT" | grep -qE '(^|[[:space:]])--delete([[:space:]]|$)' && \
     printf '%s\n' "$SUBJECT" | grep -qE '(^|[[:space:]])--force([[:space:]]|$)'; then
    deny "git branch --delete --force は禁止 (順序問わず)。git branch -d で merged 確認"
  fi
fi

# tag delete: -d, --delete (annotated tag は再現不能)
if printf '%s\n' "$SUBJECT" | grep -qE '^[[:space:]]*git[[:space:]]+tag[[:space:]]'; then
  if printf '%s\n' "$SUBJECT" | grep -qE '(^|[[:space:]])(-d|--delete)([[:space:]]|$)'; then
    deny "git tag -d / --delete は禁止。tag 削除はユーザーに確認してください"
  fi
fi

# Remote branch delete (git push --delete / git push origin :branch)
if printf '%s\n' "$SUBJECT" | grep -qE '^[[:space:]]*git[[:space:]]+push[[:space:]]+(--delete([[:space:]]|$)|[^[:space:]]+[[:space:]]+:[^[:space:]])'; then
  deny "リモートブランチ削除 (git push --delete / push origin :branch) は禁止"
fi

# force push deny は SUBJECT で判定する — `git -C x push --force` を届かせるため。
# `--force-with-lease` は token 境界の都合で permission 層の deny を通過しており、lease
# 保護があるため意図通りとして許容している (docs/reference/settings-knowledge.md)。hook 側も同じ扱いにする。
# 「これは git push か」の判定は本 file に 3 形あり、綴りが割れると片方だけ穴が空く。
# 下の床判定と同じ token 境界へ揃える — リテラル 1 空白のままだと `git  push --force` が
# 素通りし、逆に `git pushx --force` を force push として掴む。
if printf '%s\n' "$SUBJECT" | grep -qE '^[[:space:]]*git[[:space:]]+push([^[:alnum:]_-]|$)'; then
  if printf '%s\n' "$SUBJECT" | grep -qE -- '--force($| )|-f($| )' && ! printf '%s\n' "$SUBJECT" | grep -q -- '--force-with-lease'; then
    deny "force pushは禁止。ユーザーに確認を求めてください"
  fi
fi

# git push 条件付き許可は**原文**で判定する。SUBJECT を使うと `git -C <他 repo> push` まで
# hook allow に含まれ、permission 層が ask に落としていた前置つき呼び出しを無確認で通して
# しまう。剥がした写しは deny を届かせるためだけに使い、許可の射程は広げない。
# main/master への直接 push 判定は持たない (GitHub/GitLab の branch protection に委譲 → docs/adr/0019)。
#
# Bash tool timeout の床 (pre-push が重い repo 向けの deny) は本 hook に置かない。根拠が
# swat-skills の `.githooks/pre-push` 固有なので、同 repo の .claude/settings.json に登録した
# guard-push-timeout.sh が持つ (docs/adr/0078)。plugin に置くと他 repo の push まで塞ぐ。
# 判定は token 境界まで見る。`^git push` だけだと `git pushx` のような別コマンドまで allow する。
# 境界は空白に限らない (`git push>log` も push)。除くのは識別子が続く形 (`git pushx` / `git push-foo`)。
#
# allow はコマンド全体が push の 1 断片だけのときに限る (docs/adr/0093)。hook の allow は ask rule
# と承認プロンプトを越えるので、同居した別のコマンドまで permission 評価を経ずに走る。grep は
# 行単位で照合するので、行頭判定だけでは 2 行目以降の push で呼び出し全体を allow してしまう。
# 判定は危険な文字の列挙でなく、安全な文字の列挙で持つ。列挙の外の文字 (改行・`;`・`&`・`|`・
# バッククォート・`$`・`(` 等) が 1 つでもあれば allow しない — zsh の `=(cmd)` や glob qualifier
# `x(e:'cmd':)` のように、想定していない構文の漏れも allow を失うだけで済む。リダイレクトの `&`
# (`2>&1` / `>&2`) だけは先に消す。`&>` は POSIX sh では背景実行になるので消さない。引用と行継続は
# 解釈しない。どの誤りも allow の外へ倒れるだけで、deny はしない。
if printf '%s\n' "$COMMAND" | grep -qE '^[[:space:]]*git[[:space:]]+push([^[:alnum:]_-]|$)'; then
  WITHOUT_REDIRECT_AMPERSANDS=$(printf '%s\n' "$COMMAND" | sed -E 's/[<>]&/ /g')
  case $WITHOUT_REDIRECT_AMPERSANDS in
    *[![:alnum:][:blank:]_./:@=+,%~\'\"\<\>-]*) passthrough ;;
  esac
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}'
  exit 0
fi

passthrough
