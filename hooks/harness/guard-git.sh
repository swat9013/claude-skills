#!/bin/sh
# jq 必須 (fail-closed)
if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"jq not found on PATH; git guard fails closed"}}'
  exit 0
fi

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

# git のグローバルオプション (`git -C <dir>` / `git -c <k>=<v>` / `--git-dir=` 等) は
# subcommand の**前**に置ける。この形は 2 つの層を同時に素通りする:
#   - permission rule: 照合はコマンド先頭からの prefix なので `Bash(git push --force:*)`
#     のような deny に `git -C x push --force` は当たらない (settings/README.md の
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

# `git push` が出力を実際に渡すパイプだけを見るため、原文をその 1 つの pipeline まで削る
# (`| tail` 判定が使う。#897)。段ごとに落とすものが違うので 1 段ずつ並べる。
# 限界: 入れ子の引用・substitution は解けず、素通り側へ倒れる (strip_git_global_opts の
# 「限界」と同じ性質。fail-closed にすると誤 deny の方が増える)。
strip_to_push_pipeline() {
  # 論理行へ戻す — 行継続 (`\` 終端) と pipeline の途中改行 (`|` 終端) は同じ 1 コマンド
  _p=$(printf '%s\n' "$1" | sed -e :a -e '/\\$/N' -e 's/\\\n//' -e ta | sed -e :b -e '/\|$/N' -e 's/\|\n/| /' -e tb)
  # push の行だけ残す。改行も list 区切りなので、他の行のコマンドは push の出力を受け取らない
  # (`git push x` + 改行 + `git log | tail` の後段を push のパイプと読まないため)
  _p=$(printf '%s\n' "$_p" | sed -E -n -e '/^[[:space:]]*git[[:space:]]+push([^[:alnum:]_-]|$)/{p;q;}')
  # 綴りに `|` を持つがパイプではないもの (`$(git branch … | head -1)` / `'a|tail'`) を落とす。
  # 逆に引用の中の `;` は list 区切りではないので、次の段へ渡す前にここで消す
  # shellcheck disable=SC2016  # 展開させない: `$(` は sed へ渡す検出対象の綴りそのもの
  _p=$(printf '%s\n' "$_p" | sed -E -e 's/\$\([^)]*\)//g' -e 's/`[^`]*`//g' -e 's/"[^"]*"//g' -e "s/'[^']*'//g")
  # 最初の `&&` / `||` / `;` から後ろは別コマンドで、そのパイプは push のものではない
  printf '%s\n' "$_p" | sed -E 's/[[:space:]]*(&&|\|\||;).*$//'
}

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

# `git add` の一括指定は guard-bulk-stage.sh が担う。本 hook は hooks.json 側の
# `if: "Bash(git:*)"` gate で先頭 token が git のときしか起動せず、判定も行頭アンカーだった
# ため `echo x && git add -A` / `git status --short && git add -A` を取り逃していた
# (gate を外すと下の非アンカー判定が全 Bash にかかって誤爆するので、判定ごと移した)。

# 破壊的 git 操作
if printf '%s\n' "$SUBJECT" | grep -qE 'git reset.*--hard|git clean.*(--force|-f)'; then
  deny "破壊的git操作は禁止。git stashで退避してから操作してください"
fi
if printf '%s\n' "$SUBJECT" | grep -qE 'git checkout -- '; then
  deny "git checkout -- は禁止。git stashで退避してから操作してください"
fi
if printf '%s\n' "$SUBJECT" | grep -qE '^[[:space:]]*git restore( |$)' && ! printf '%s\n' "$SUBJECT" | grep -qE 'git restore --staged'; then
  deny "git restore（ワーキングツリー変更の破棄）は禁止。git stashで退避してください"
fi

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
# 保護があるため意図通りとして許容している (settings/README.md)。hook 側も同じ扱いにする。
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
# 許可の条件に **Bash tool timeout の明示**を課す。`.githooks/pre-push` が全 pytest を走らせ、
# 既定の 120s では終わらず SIGTERM (exit 143) で push が死ぬ (#899 の実測: 単独 131s /
# 2 セッション同時 406s)。exit code は timeout に食われて成功と区別が付かないので、実行前に返す。
# 床の判定も原文で行う — 前置つき (`git -C <他 repo> push`) は本 repo の pre-push を走らせる
# とは限らず、床を課すと他 repo への push を誤って塞ぐ。同じ理由で `git commit -m x && git push`
# のような複合形も床の外にある (行頭アンカーが外れる。#897 系の既知の穴と同じ構造)。
# 床は Bash tool timeout の上限と同値なので、suite の所要がこれを超えたら timeout の指定では
# 逃げられない — その先は suite 側を削る対処になる (#898)。本判定は対症であることを前提に置く。
# 判定は token 境界まで見る。`^git push` だけだと `git pushx` のような別コマンドまで巻き込み、
# 床を課した後は素通りではなく deny になる (差分前は無条件 allow だったので実害が無かった)。
# 境界は空白に限らない — `git push;echo x` / `git push&&foo` / `git push>log` は先頭が本物の
# push なので床の内側に残す。除くのは識別子が続く形 (`git pushx` / `git push-foo`) だけ。
PUSH_TIMEOUT_FLOOR_MS=600000
if printf '%s\n' "$COMMAND" | grep -qE '^[[:space:]]*git[[:space:]]+push([^[:alnum:]_-]|$)'; then
  # 診断を飲むパイプ (`| tail` / `| head`) を先に落とす。SIGTERM で殺されると tail / head は
  # バッファを抱えたまま 1 行も出さず、pre-push がどこまで進んだかが transcript から消える
  # (#897 の実測: push timeout 12 件すべてで result_text が 43 文字の exit 143 通知だけになった)。
  # 判定順は「不可逆 (force push) > 診断の消失 (本判定) > 所要 (下の床)」。床より前に置くのは、
  # timeout 未指定の `git push … | tail` に床の文言を返すと「timeout を足す」書き換えへ誘導され、
  # パイプを外す代替形が載らないまま診断が消える形が残るため。
  #
  # 対象を tail / head の 2 コマンドに絞るのは、代替形を 1 つに定められる範囲がここだからである。
  # push の出力は成功時 5 行程度なので「パイプを外す」が常に正しい助言になる。同じくバッファする
  # `| grep` / `| wc` へ広げると、正しい代替形を持てない deny になる (代替形の無い deny は往復を
  # 増やすだけで、本 hook の目的の逆を行く。先例 guard-cd-relative-glob-read.py の emit_deny)。
  # 本判定は #899 の床と同じく対症で、根本は pre-push の所要そのもの (#898)。suite が縮んで
  # push が timeout しなくなれば、診断が消える経路ごと不要になる。
  #
  # 判定は原文 (COMMAND) を push の pipeline まで削ってから当てる (削る順は関数側にある)。
  PUSH_PIPELINE=$(strip_to_push_pipeline "$COMMAND")
  # `|&` は bash の stderr 込みパイプ、`([^[:space:]]*/)?` は path 修飾 (`| /usr/bin/tail`)。
  # 見るのはコマンド名の綴りだけで、`| xargs tail` / `| env tail` のような間接起動は外にある —
  # 実測で観測された形が `| tail -N` 直結だけであり、間接形まで追うと引用の解けない sh の
  # 範囲では誤 deny が増える (先に潰すべきは実在する形)。
  if printf '%s\n' "$PUSH_PIPELINE" | grep -qE '\|&?[[:space:]]*([^[:space:]]*/)?(tail|head)([^[:alnum:]_-]|$)'; then
    deny "git push を \`| tail\` / \`| head\` へ通さないでください。timeout で SIGTERM が来ると tail / head はバッファを抱えたまま 1 行も出力せず、pre-push がどこまで進んだかが transcript に残りません。パイプを外し、Bash tool の timeout に ${PUSH_TIMEOUT_FLOOR_MS} を指定して \`git push origin <branch> 2>&1\` を実行してください (push の出力は成功時 5 行程度で、切り詰める必要がありません) [guard-git.sh]"
  fi
  # jq 側で数値へ寄せる: float (`600000.0`) は整数へ落とし、数値でない値・未指定は空にする。
  # 巨大な値は指数表記 (`1e+23`) になり、下の桁検査で弾かれる (shell の整数範囲を超えた
  # `[ -lt ]` がエラー終了して allow へ抜ける fail-open を塞ぐ)。
  TIMEOUT_MS=$(printf '%s\n' "$INPUT" | jq -r 'try (.tool_input.timeout | floor) catch empty')
  # 未指定・null・非数はすべて fail-closed で deny する (docs/adr/0046 /
  # principle-fail-loudly)。読めない値を床通過とみなすと guard が黙って無効になる。
  case $TIMEOUT_MS in
    '' | *[!0-9]*)
      deny "git push は Bash tool の timeout に ${PUSH_TIMEOUT_FLOOR_MS} (10 分。Bash tool の上限値) を指定して実行してください。pre-push が全 pytest を回すため、既定の 120s では SIGTERM (exit 143) で push が死にます"
      ;;
  esac
  if [ "$TIMEOUT_MS" -lt "$PUSH_TIMEOUT_FLOOR_MS" ]; then
    deny "git push の timeout ${TIMEOUT_MS} ms では pre-push の全 pytest が終わりません。${PUSH_TIMEOUT_FLOOR_MS} (10 分。Bash tool の上限値) を指定して実行してください"
  fi
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}'
  exit 0
fi

passthrough
