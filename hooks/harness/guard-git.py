#!/usr/bin/env python3
"""guard-git.py — PreToolUse / Bash(git:*): 取り返しのつかない git 操作を deny し、残りのうち引数の文法の列挙に一致した push を allow する。

置く理由: backstop

`reset --hard` と force push は、失った変更を戻せないので、抜け道があると承知のうえで止める (ADR 0108)。
ref の削除 (`branch -D` / `tag -d` / `push --delete`) は、deny されない別の書き方で同じ削除が通るので止めない (同 ADR)。
次の deny はどの置く理由にも当たらず、外すと決まっている (実装は各 issue で行う):

- `clean -f` / checkout・restore による作業ツリーの復元 (#1288 の決定): stash や cp で同じ効果が出るうえ、
  守る対象 (agent が作っていない未 commit 変更) は worktree の隔離が構造で守るので、backstop として残す
  価値が往復の費用を上回らない

fail-posture: **fail-closed** (ADR 0046)。payload を読めなかったときは素通しせず deny する。読み取り失敗を
素通しにすると command が空文字になり、下の判定が全部不成立のまま passthrough して、本 hook が塞いでいる
破壊的 git 操作が無検査で通る。停止範囲は発火条件 (hooks.json の `Bash(git:*)`) に閉じる。

`git add` の一括指定は deny しない。秘匿情報の巻き込みは repo ごとの gitleaks (pre-commit) で防ぐ (ADR 0077)。

Registration: hooks/hooks.json PreToolUse / Bash matcher / if: Bash(git:*)
"""
from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from typing import NoReturn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _prelude import (  # noqa: E402
    allow_without_reason,
    command_segments,
    continues_on_another_line,
    decide,
    deny,
    matches_any_line,
    normalized_command,
    passthrough,
    posix_class_view,
    read_payload_or_deny,
    read_stdin_text,
    strip_here_documents,
    substitute_lines,
    write_stdout_text,
)

# git のグローバルオプション (`git -C <dir>` / `git -c <k>=<v>` / `--git-dir=` 等) は subcommand の**前**に
# 置ける。この形は 2 つの層を同時に素通りする:
#   - permission rule: 照合はコマンド先頭からの prefix なので `Bash(git push --force:*)` のような deny に
#     `git -C x push --force` は当たらない (docs/reference/settings-knowledge.md の「permission rule の照合は
#     コマンドの綴りに依存する」と同じ構造の穴)
#   - 本 hook: 行頭アンカー判定 (`^git push`) が前置で外れる
# そこで判定用に前置を剥がした写し (subject) を作る。**原文 (command) は書き換えない** — 剥がすのは deny 判定を
# 届かせるためで、allow の射程は広げない (下の push の allow を参照)。
# 限界: 値が空白を含む引用形 (`git -c "user.name=Foo Bar" push --force`) は 1 token として剥がせず素通りする。
_GLOBAL_OPTION = re.compile(
    r"(^|\s)git\s+(-C\s+\S+|-c\s+\S+|--git-dir=\S+|--work-tree=\S+|--namespace=\S+|--exec-path=\S+"
    r"|--no-pager|--paginate|-p)\s+"
)
_GLOBAL_OPTION_PASSES = 8

# 理由文で stash を勧めない: stash の stack は worktree・並列 session 間で共有され、
# `git stash -u && git stash drop` で他者の未 commit 編集ごと消える事故が実際に起きた (refuter / i853)。
RESTORE_GUIDE = "自分の変更を退避するなら一時 WIP commit を作る。自分が作っていない未 commit 変更は触らず、ユーザーに確認する"

# `git` と subcommand の間で、次の語を値に取る global option (`git --git-dir <path> reset`)。`=` で値を繋ぐ形と
# 値を持たない option は 1 語なので、`-` 始まりの語を読み飛ばすだけで足りる
_GLOBAL_OPTIONS_WITH_VALUE = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--attr-source", "--config-env", "--shallow-file"}
)

# `--hard` と省略形。git は long option の一意な前方一致を受け付ける (`git reset --h` も hard reset になる)
_HARD_FLAG = re.compile(r"--hard|(?<![\w/=-])--h(?:ar?)?(?![\w-])")

# reset の subcommand。語の境界は空白に限らない (`git reset>log --hard` も reset)。値の綴りの `reset.quiet=true` と
# `reset/sub`、引用の中の文章の `reset --hard の説明` は subcommand でない
_RESET_SUBCOMMAND = re.compile(r"reset(?![\w\s./-])")

# clean / checkout / restore の token の区切りは、空白に加えてリダイレクト (`<` `>`) とクォート (`"` `'`)。
# `git clean -fd>/dev/null` / `git clean "-fd"` の flag を 1 token として読む。空白へ置き換えてから分割へ渡すので、
# 分割はクォートを 1 つも見ない
_TOKEN_BREAKS = str.maketrans("<>\"'", "    ")

# checkout によるファイルの復元 (作業ツリーの変更を捨てる) の引数。`.` / `..` そのものか `./` / `../` で始まる
# path (`git checkout .` / `./src` / `../x`)。この形は branch 名になれない (git の refname 規則) ので、
# `git checkout <branch>` を巻き込まない
_CHECKOUT_RELATIVE_PATH = re.compile(r"\.\.?(/.*)?")

# 短縮 flag の束ね (`-f` / `-SW` 等)
_SHORT_FLAGS = re.compile(r"-[A-Za-z]+")

# 「これは git push か」の判定は token 境界まで見る。リテラル 1 空白だと `git  push --force` が素通りし、
# `^git push` だけだと `git pushx --force` を force push として掴む。境界は空白に限らない (`git push>log` も
# push)。除くのは識別子が続く形 (`git pushx` / `git push-foo`)
_PUSH_COMMAND = re.compile(r"^\s*git\s+push([^\w-]|$)")
# 左に ref 名の文字が続く形 (`fix-f` / `drop--force`) は branch 名で、flag と読まない。それ以外の左の文字は見ない —
# `\-f` や `""-f` はシェルが `-f` に解くので、語の先頭に限ると force push を取りこぼす
_FORCE_PUSH_FLAG = re.compile(r"(?<![\w./-])(?:--force|-f)($| )")

# push の allow を出す文法 (docs/adr/0122)。`git push [OPT...] [<remote> [<refspec>...]]`
# 語の区切りは空白とタブ、文字は ASCII の明示の集合に限る (`\s` / `\w` は Unicode の空白・文字に当たる)。
# remote と ref は語の先頭に `-` を置けない (置けると `--tags` / `--no-verify` が名前として一致する)。remote の先頭の
# `.` も外す (`.` / `..` は git が path として読む)
_PUSH_OPTION = r"(?:-u|--set-upstream|-q|--quiet|-v|--verbose|-n|--dry-run)"
_PUSH_REMOTE = r"[A-Za-z0-9_][A-Za-z0-9._-]*"
_PUSH_REF = r"[A-Za-z0-9._/][A-Za-z0-9._/-]*"
_PUSH_REFSPEC = rf"{_PUSH_REF}(?::{_PUSH_REF})?"
_PUSH_GRAMMAR = re.compile(
    rf"[ \t]*git[ \t]+push(?:[ \t]+{_PUSH_OPTION})*(?:[ \t]+{_PUSH_REMOTE}(?:[ \t]+{_PUSH_REFSPEC})*)?[ \t]*"
)


def strip_git_global_options(command: str) -> str:
    """`git` の前置 option を剥がした写しを返す (1 回の置換で消えるのは git 1 語につき 1 つなので繰り返す)。"""
    subject = command
    for _ in range(_GLOBAL_OPTION_PASSES):
        stripped = substitute_lines(_GLOBAL_OPTION, r"\1git ", subject)
        if stripped == subject:
            break
        subject = stripped
    return subject


def is_git(word: str) -> bool:
    """path 付き (`/usr/bin/git`) と `\\git` も git として読む。"""
    return word.removeprefix("\\").rsplit("/", 1)[-1] == "git"


def clean_forces(arguments: list[str]) -> bool:
    """短縮 flag の `f` は束ねのどこにあってもよい (`-fd` / `-df` / `-xdf`)。"""
    return any(word == "--force" or (_SHORT_FLAGS.fullmatch(word) and "f" in word) for word in arguments)


def checkout_restores(arguments: list[str]) -> bool:
    """pathspec の区切り `--` があるか (`git checkout -- x` / `git checkout HEAD -- x`)、相対 path の形の引数があるか。

    当たらない形: dotfile の path (`git checkout .gitignore`) と、`--` の無い `git checkout <path>` /
    `git checkout <rev> <path>`。
    """
    return any(word == "--" or _CHECKOUT_RELATIVE_PATH.fullmatch(word) for word in arguments)


def restore_discards(arguments: list[str]) -> bool:
    """例外は index だけを戻す形 (`--staged` / `-S`) で、作業ツリーも書き換える `--worktree` / `-W` を伴えば deny する。

    例外はその断片の中だけで見る — 断片の外へ漏らすと `git restore --staged . && git restore x` の後段まで
    免除される。
    """
    staged = worktree = False
    for word in arguments:
        if word == "--":  # 以降は pathspec
            break
        if word == "--staged":
            staged = True
        elif word == "--worktree":
            worktree = True
        elif word.startswith("-s"):  # -s<tree> は値を伴う (束ねの文字を flag と読まない)
            continue
        elif _SHORT_FLAGS.fullmatch(word):
            staged = staged or "S" in word
            worktree = worktree or "W" in word
    return not staged or worktree


_DISCARD_JUDGES = {"clean": clean_forces, "checkout": checkout_restores, "restore": restore_discards}


def discarding_subcommands(subject: str) -> set[str]:
    """作業ツリーの変更を捨てる形で呼ばれた subcommand (clean / checkout / restore) を返す。

    3 判定は同じ断片を見る (判定ごとに区切りを綴ると、片方だけ境界が割れて抜け道になる)。判定の起点は断片内の
    任意の位置の `git <subcommand>` で、前に `{` / `then` / `do` / `time` / `FOO=1` / `xargs` 等が付いても飛ばさない。
    flag と引数は subcommand の後ろから断片の終わりまでを見るので、`git clean -n && rm -f x` の `-f` を clean の
    flag と読まない。
    """
    hits: set[str] = set()
    for words in command_segments(subject.translate(_TOKEN_BREAKS)):
        for index, word in enumerate(words[:-1]):
            if is_git(word):
                hits.update(
                    name for name, discards in _DISCARD_JUDGES.items()
                    if words[index + 1] == name and discards(words[index + 2:])
                )
    return hits


def is_reset(word: str) -> bool:
    """語が reset の subcommand か。`matches_any_line` と同じ文字クラスで読む。"""
    return _RESET_SUBCOMMAND.match(posix_class_view(word)) is not None


def subcommand_position(words: list[str], start: int) -> int:
    """`start` から global option とその値を読み飛ばした位置を返す。subcommand が無ければ `len(words)`。

    字句解析は空の語を返さないので、`git -C "" reset --hard` の値は語に無い。値の位置の reset と `-` 始まりの語は値と
    読まない。空の語を返すようになれば (#2326)、この読みは消す。
    """
    position = start
    while position < len(words) and words[position].startswith("-"):
        following = words[position + 1] if position + 1 < len(words) else "-"
        value_follows = not following.startswith("-") and not is_reset(following)
        position += 2 if words[position] in _GLOBAL_OPTIONS_WITH_VALUE and value_follows else 1
    return position


def value_is_cut_off(words: list[str]) -> bool:
    """`git` の断片が、置換で切れた option の値で終わるか。

    最後の語が置換の前の片 (`-C $` / `--git-dir=`) か、値を取る option か (`-C` / `-C x` の後ろのバッククォート)。
    """
    return words[-1].endswith(("$", "=")) or _GLOBAL_OPTIONS_WITH_VALUE.intersection(words[-2:]) != set()


def continued_reset_tail(words: list[str]) -> list[str]:
    """置換で切れた値の続きの断片から、`reset` の subcommand より後ろの語を返す。無ければ空。

    先頭の語が path の綴りなら値の残りとして飛ばす (`--git-dir=$(pwd)/.git reset`)。2 語目の `reset` も値の残りの
    後ろと読む (`-C "$(pwd)"sub reset`)。
    """
    for position in (subcommand_position(words, 1 if words[0].startswith(("/", ".")) else 0), 1):
        if position < len(words) and is_reset(words[position]):
            return words[position:]
    return []


def reset_tails(fragments: list[list[str]]) -> Iterator[list[str]]:
    """断片ごとに、最初の `git reset` の subcommand から後ろの語を返す。起点は断片内の任意の位置の `git` の語。

    option の値の置換で断片が切れると (`git -C "$(pwd)" reset`)、subcommand は後ろの断片に移る。その後は断片の先頭から
    option を読み飛ばした語も見る。字句解析が置換を含む値を 1 語で返すようになれば (#2326)、この読みは消す。読み飛ばした
    値は起点にしないので、入力の長さに線形で済む。
    """
    subcommand_cut_off = False
    for words in fragments:
        continued = continued_reset_tail(words) if subcommand_cut_off else []
        if continued:
            yield continued
            continue
        index = 0
        while index < len(words):
            if not is_git(words[index]):
                index += 1
                continue
            index = subcommand_position(words, index + 1)
            if index >= len(words):
                subcommand_cut_off = subcommand_cut_off or value_is_cut_off(words)
                break
            if is_reset(words[index]):
                yield words[index:]
                break
            if not is_git(words[index]):  # `GIT_DIR=/x/git git reset` の 2 語目も起点にする
                index += 1


def resets_hard(command: str) -> bool:
    """`git reset` の後ろに `--hard` か省略形を含む語があるかを返す。

    here-document の本体は落とし、引用の中の文字列は 1 語に閉じるので、どちらの `git` も起点にならない。引用された
    `"--hard"` は flag として読む。語は `--hard` を含めばよい (`--hard=x`・`reset$IFS--hard` も当たる)。

    `git reset` を語で確かめた後は、同じ行の後ろの文字列の `--hard` も見る。断片に閉じると、`git reset $(echo --hard)`
    のように置換や区切りの向こうに置いた形が通る。行に閉じるのは、後の行の別のコマンドを flag と読まないため。省略形を
    読まないのは、別のコマンドの `--h` を flag と読まないため。語に行を持たせれば (#2326)、文字列の照合は消す。
    """
    without_bodies = strip_here_documents(command)
    tails = list(reset_tails(command_segments(without_bodies)))
    if any(_HARD_FLAG.search(posix_class_view(word)) for tail in tails for word in tail):
        return True
    lines = strip_git_global_options(without_bodies).split("\n")
    return bool(tails) and any("--hard" in line.partition("git reset")[2] for line in lines)


def judge_destructive_operations(command: str) -> None:
    """deny に当たる操作を探す。当たらなければ return する。`reset --hard` の後は前置 option を剥がした写しで見る。"""
    if resets_hard(command):
        deny(f"破壊的git操作は禁止。{RESTORE_GUIDE}")

    subject = strip_git_global_options(command)
    discarding = discarding_subcommands(subject)
    if "clean" in discarding:
        deny(f"破壊的git操作は禁止。{RESTORE_GUIDE}")
    if "checkout" in discarding:
        deny(f"git checkout によるファイルの復元 (-- <path> / .) は禁止。{RESTORE_GUIDE}")
    if "restore" in discarding:
        deny(f"git restore（ワーキングツリー変更の破棄）は禁止。index だけを戻すなら git restore --staged <path>。{RESTORE_GUIDE}")

    # `--force-with-lease` は token 境界の都合で permission 層の deny を通過しており、lease 保護があるため
    # 意図通りとして許容している (docs/reference/settings-knowledge.md)。hook 側も同じ扱いにする
    if (
        matches_any_line(_PUSH_COMMAND, subject)
        and matches_any_line(_FORCE_PUSH_FLAG, subject)
        and "--force-with-lease" not in subject
    ):
        deny("force pushは禁止。ユーザーに確認を求めてください")


def is_enumerated_push(command: str) -> bool:
    """コマンド全体が、列挙した文法に一致する push 1 つだけかを返す。

    hook の allow は承認プロンプトを越える (ask rule は越えない) ので、allow は安全と確かめた形にだけ返す
    (docs/adr/0122)。一致しない形 (文法の外の option・URL の remote・`+` や空の src の refspec・リダイレクト・
    同居コマンド・引用) は allow の外へ倒れるだけで、deny はしない。改行は文法でも外れるが、allow を出す hook が
    共通して通す述語を通しておく。
    """
    return not continues_on_another_line(command) and _PUSH_GRAMMAR.fullmatch(command) is not None


def judge(stdin_text: str) -> NoReturn:
    payload = read_payload_or_deny(stdin_text)
    # 末尾の改行は判定に入れない。行として数えないので、`git push x\n` は 1 行の push として読む
    command = normalized_command(payload)

    judge_destructive_operations(command)

    # push の allow は**原文**で判定する。前置を剥がした写しを使うと `git -C <他 repo> push` まで hook allow に含まれ、
    # permission 層が ask に落としていた前置つき呼び出しを無確認で通してしまう。
    # main/master への直接 push 判定は持たない (GitHub/GitLab の branch protection に委譲 → docs/adr/0019)。
    # Bash tool timeout の床 (pre-push が重い repo 向けの deny) は本 hook に置かない。根拠が swat-skills の
    # `.githooks/pre-push` 固有なので、同 repo の .claude/settings.json に登録した guard-push-timeout.py が持つ
    # (docs/adr/0078)。plugin に置くと他 repo の push まで塞ぐ。
    if is_enumerated_push(command):
        allow_without_reason()
    passthrough()


def main(stdin_text: str) -> tuple[int, str]:
    """hook の純粋な入口。(exit code, stdout 本文) を返す。"""
    return decide(judge, stdin_text)


if __name__ == "__main__":
    exit_code, stdout_text = main(read_stdin_text())
    write_stdout_text(stdout_text)
    sys.exit(exit_code)
