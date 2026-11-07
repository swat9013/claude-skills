"""harness hook の共有 prelude — payload 読み取り・PreToolUse envelope の出力・shell 文字列の前処理。

Why: harness hook は 12 本以上あり、どれも「stdin の JSON を読む」「`hookSpecificOutput`
envelope を stdout へ出す」を各自で綴っている。逐語重複そのものは 40〜50 行と小さいが、
実害は行数ではなく **fail-posture がコピー元の偶然で決まること**にある — #250 の決定後に
追加された 4 本の guard は、いずれも根拠コメントが「他の guard と同じ側へ倒す」のような参照で
あって脅威モデルではない。

本 module は posture を**関数の選択**として表に出す: hook 冒頭 1 行が
`read_payload_or_deny()` なら fail-closed、`read_payload_or_pass()` なら fail-open と読める。
bool 引数にしないのは、既定値の側へ黙って倒れる余地を残さないため。

配置と共有可否の基準は ADR 0013 (サブシステム内実装共有)。同 ADR の配置規約は
`hooks/_prelude.py` と綴っているが、harness hook の subsystem root は `hooks/harness/` なので
消費者と同じ階層に置く (#250 決定 2 の綴りに合わせる)。ADR 0013 が受容済みの副作用
「script 単体の自己完結性低下」— hook を 1 本だけ他環境へコピーしても動かなくなる — は
本 module にもそのまま効く。

**この module は hook ではない** (shebang も実行権限も持たない)。消費側の hook は冒頭で
`sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` してから import する。
`abspath` は symlink を解決しないので、`${CLAUDE_PLUGIN_ROOT}` 経由の symlink 起動でも
同じディレクトリの本 module を掴む。

判断を出す関数 (`deny()` / `allow()` / `rewrite()` / `passthrough()` 等) は stdout にも exit にも
触らず、出力する本文を `Finished` に載せて raise する。消費側の hook は判定を `judge(stdin_text)` に
書き、`main(stdin_text)` で `decide(judge, stdin_text)` を返す — これが `.claude/rules/hook-tests.md`
が python hook に求める `main(stdin_text) -> (exit_code, stdout_text)` の形で、テストは hook を
import して in-process で呼べる (#1690)。process 境界 (stdin の読み取り・stdout への書き出し・exit) に
触るのは hook 末尾の `if __name__ == "__main__":` だけにする:

    if __name__ == "__main__":
        exit_code, stdout_text = main(read_stdin_text())
        write_stdout_text(stdout_text)
        sys.exit(exit_code)

stdlib のみを使う。hook は Bash 呼び出しのたびに起動するため、外部依存の解決コストと
不在時の分岐をここで消す。
"""
from __future__ import annotations

import bisect
import json
import os
import re
import sys
from typing import Callable, NoReturn

# 対象 hook は全て PreToolUse (#250 決定 2)。他 event の hook が prelude を要るようになった
# 時点で引数化する — 今 1 つしかない値を引数にしても呼び出し側の綴りが増えるだけ。
HOOK_EVENT_NAME = "PreToolUse"


def hook_name() -> str:
    """起動された hook のファイル名。deny 理由の attribution に使う。

    手渡しの hook 名は typo と改名時の取り残しの温床なので `sys.argv[0]` から導出する
    (#250 決定 2)。argv[0] が空になる異常系では素の呼び出し名を返す。
    """
    return os.path.basename(sys.argv[0]) or "<unknown hook>"


class Finished(Exception):
    """判断が確定したことを `decide()` へ運ぶ。stdout へ書く本文 (1 行の envelope と改行) を持つ。

    判断を出す関数が直接 stdout / exit を触らないのは、テストが hook を import して in-process で
    呼べるようにするため (#1690)。process 境界に触るのは hook 末尾の `__main__` だけ。
    """

    def __init__(self, stdout_text: str) -> None:
        super().__init__(stdout_text)
        self.stdout_text = stdout_text


def decide(judge: Callable[[str], object], stdin_text: str) -> tuple[int, str]:
    """`judge(stdin_text)` を走らせ、確定した判断を (exit code, stdout 本文) で返す。

    `judge` は必ず判断を出す関数 (`deny()` / `passthrough()` 等) か `read_payload_or_*()` の
    読み取り失敗で終わる。判断を出さずに return した `judge` は出口の書き漏れなので、
    無出力の exit 0 (= 痕跡の無い素通し、ADR 0043) へ黙って落とさず例外で止める。
    """
    try:
        judge(stdin_text)
    except Finished as finished:
        return 0, finished.stdout_text
    raise AssertionError("judge() が判断を出さずに return した (出口は passthrough() 等で閉じる)")


def read_stdin_text() -> str:
    """hook の stdin を全部読んで文字列で返す。`__main__` から `main()` へ渡す。

    UTF-8 として読めない byte は U+FFFD に置き換えて読み進める — 移植元の sh guard が使う `jq` と同じ
    扱いで、判定を変えない移植の前提 (#1436。`audit-bash-output.py` も同じ読み方)。text mode の
    `sys.stdin.read()` は locale 次第で例外を投げ、hook が exit 1 で終わると判断なしとして扱われる。
    """
    return sys.stdin.buffer.read().decode("utf-8", errors="replace")


def write_stdout_text(text: str) -> None:
    """`main()` の stdout 本文を UTF-8 で書く。

    text mode の `sys.stdout.write()` は locale の encoding で書くので、UTF-8 でない locale では日本語の deny 理由が
    例外になり、hook が異常終了して deny の判断と理由が host へ届かない。
    """
    sys.stdout.buffer.write(text.encode("utf-8"))


def _emit(envelope: dict) -> NoReturn:
    text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        # 対の無い surrogate (JSON の `\ud800` 由来) は UTF-8 で書けない。書けずに落ちると exit 1 で
        # 判断が消えるので、その envelope だけ `\uXXXX` に escape して出す
        text = json.dumps(envelope, ensure_ascii=True, separators=(",", ":"))
    raise Finished(text + "\n")


def _envelope(**fields: object) -> dict:
    return {"hookSpecificOutput": {"hookEventName": HOOK_EVENT_NAME, **fields}}


def _emit_pass_trace() -> NoReturn:
    """pass の観測痕跡。全 harness guard が**逐語で同じ 1 行**を出す (#587)。

    綴りを揃える理由は record の見え方ではなく、テスト側の期待値を 1 つに保つため。
    `hooks/harness/tests/test_pass_trace.py` が hooks.json の PreToolUse entry を全数
    列挙してこの逐語と突き合わせる (ADR 0043)。
    """
    _emit({"hookSpecificOutput": {"hookEventName": HOOK_EVENT_NAME},
           "suppressOutput": True})


def deny(reason: str) -> NoReturn:
    """判断を deny で確定して `judge` を抜ける (`Finished` を raise する)。

    reason は逐語で出す — 既存 guard の理由は「git stash で退避してから操作してください」の
    ように利用者への指示文になっており、hook 名を機械的に足すと全 guard の文面が変わる。
    attribution が要るのは「なぜ止まったか分からない deny」= fail-closed 側だけなので、
    そこは `read_payload_or_deny()` が自分で付ける。
    """
    _emit(_envelope(permissionDecision="deny", permissionDecisionReason=reason))


def allow(reason: str) -> NoReturn:
    """判断を allow で確定して `judge` を抜ける (`Finished` を raise する)。"""
    _emit(_envelope(permissionDecision="allow", permissionDecisionReason=reason))


def _with_command(tool_input: dict, command: str) -> dict:
    """`tool_input` の `command` だけを差し替えた入力全体を返す。

    host は `updatedInput` を入力全体の置き換えとして扱い、写さなかった `timeout` などは呼び出しから
    落ちる (`docs/reference/settings-knowledge.md` の実測、#2272)。
    """
    return {**tool_input, "command": command}


def allow_rewritten(tool_input: dict, command: str) -> NoReturn:
    """`command` を書き換えて allow する。理由は載せない。

    allow の出し方は意図で選ぶ: 判断の理由を利用者に見せるなら `allow(reason)`、
    書き換え先そのものが判断を語る (書き換えた command が出力に載る) なら本関数、入力を書き換えず
    理由も見せないなら `allow_without_reason()`、判断を出さず書き換え後の行を通常の permission 評価に
    回すなら `rewrite()`。迷ったら `rewrite()` に倒す — allow は deny / ask rule を越えないまま
    「hook が許した」痕跡を残す。
    """
    _emit(_envelope(permissionDecision="allow", updatedInput=_with_command(tool_input, command)))


def allow_without_reason() -> NoReturn:
    """入力を書き換えず、理由も載せずに allow する。"""
    _emit(_envelope(permissionDecision="allow"))


def rewrite(tool_input: dict, command: str) -> NoReturn:
    """判断を出さずに `command` だけ書き換えて `judge` を抜ける。

    書き換え後の行に対して通常の permission 評価を走らせたいときに使う — allow を付けると
    deny / ask rule を越えないまま「hook が許した」痕跡だけが残る。
    """
    _emit(_envelope(updatedInput=_with_command(tool_input, command)))


def passthrough() -> NoReturn:
    """判断を出さずに `judge` を抜ける。settings の permission 層と他の guard に委ねる。

    判断は出さないが**出力はする** (#587)。無出力で exit した hook は transcript に
    attachment を残さず、named query `permissions` の hook 軸では `fire_count: null` になる —
    「壊れて静かに死んだ guard」と「窓内に出番が無かった guard」が棚卸しで同じ見え方に
    なる。`permissionDecision` を持たない `hookSpecificOutput` は仕様上合法で、通常の
    permission フローへ完全に委ねる (意味論は無出力と同じ) が、非空 stdout なので
    `hook_success` record は残る。`suppressOutput` は transcript mode の表示だけを
    抑止するもので record は抑止しない (実 transcript で確認済み)。
    """
    _emit_pass_trace()


def _parse(raw: str) -> tuple[dict | None, str]:
    """payload を検査して (payload, 失敗理由) を返す。成功時の理由は空文字。

    `tool_input` が object でなければ読み取り失敗として扱う。**不在 (null) も失敗側に含める**。
    実際の PreToolUse payload には常に `tool_input` があるので到達しない入力だが、hook ごとに
    食い違わせない (逆方向へ「直される」のを防ぐテストは `test_prelude.py` の parity 検査が持つ)。

    通ったら `payload["tool_input"]` は必ず dict なので、消費側は `.get(...)` を 1 段で書ける。
    `tool_input` の中身が空なのは正常系 (`command` / `file_path` を持たない payload がある)。
    """
    if not raw.strip():
        return None, "stdin が空"
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError):
        return None, "JSON として parse できない"
    if not isinstance(payload, dict):
        return None, "payload が object でない"
    if not isinstance(payload.get("tool_input"), dict):
        return None, "tool_input が object でない"
    return payload, ""


def read_payload_or_deny(stdin_text: str) -> dict:
    """fail-closed に payload を読む。読めなければ deny で `judge` を抜ける。

    守る対象の素通しが停止より高くつく guard 向け。deny 理由には内訳と hook 名を載せる —
    理由の分からない deny は、踏んだ人間が hook 自体を無効化する方向に働くため (#598)。
    """
    payload, failure = _parse(stdin_text)
    if payload is None:
        deny(
            f"payload を読めなかったため停止した (fail-closed): {failure} "
            f"[{hook_name()}]"
        )
    return payload


def read_payload_or_pass(stdin_text: str) -> dict:
    """fail-open に payload を読む。読めなければ判断を出さずに `judge` を抜ける (pass の痕跡だけを出す)。

    停止の損害が素通しの損害を上回る guard と、確証があるときだけ allow を出す hook 向け。
    """
    payload, _failure = _parse(stdin_text)
    if payload is None:
        passthrough()
    return payload


def tool_input_text(payload: dict, key: str) -> str:
    """`tool_input[key]` を文字列で返す。不在・null・false は空文字。

    sh guard が `jq -r '.tool_input.<key> // empty'` で読んでいた値と同じものを返す — 文字列は
    そのまま、それ以外の値は JSON の綴り。実 payload の `command` / `file_path` は常に文字列。
    """
    value = payload["tool_input"].get(key)
    if value is None or value is False:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def normalized_command(payload: dict) -> str:
    """判定に使う `tool_input.command` を返す。shell のコマンド置換と同じく NUL と末尾の改行を落とす。

    NUL を残すと `git re\\0set --hard` が判定の語に当たらない。
    """
    return tool_input_text(payload, "command").replace("\0", "").rstrip("\n")


# here-document の開始。delimiter は shell の語として読む。閉じない引用は語にしない
_HERE_DOCUMENT_START = re.compile(
    r"""<<(-?)[ \t]*((?:[^ \t\n;|&()<>`'"\\]|\\[^\n]|'[^'\n]*'|"(?:[^"\\\n]|\\.)*")+)"""
)
_DELIMITER_PART = re.compile(r"""'([^'\n]*)'|"((?:[^"\\\n]|\\.)*)"|\\(.)""")
_DOUBLE_QUOTE_ESCAPE = re.compile(r"""\\([$`"\\])""")
# 綴りを読み切れない delimiter (`$'…'`・`$"…"`)。語が続く形とともに本体を落とさない
_UNREADABLE_DELIMITER = re.compile(r"""\$['"]""")
_DELIMITER_FOLLOWERS = frozenset(" \t\n;|&<>)")
# `$'…'` の本体と閉じる引用。単一引用と違い、backslash で打ち消した `'` では閉じない
_ANSI_C_QUOTE_BODY = re.compile(r"((?:[^'\\]|\\.)*)'", re.DOTALL)
_ESCAPABLE_IN_DOUBLE_QUOTE = frozenset('$`"\\')
_SEGMENT_SEPARATORS = frozenset(";|&()`")
# 文の先頭語の前に置ける語。`case` を文の先頭語と見分け、`((` を算術の文と読むのに使う
_RESERVED_PREFIXES = frozenset({"time", "coproc", "!", "{", "if", "then", "elif", "else", "while", "until", "do"})
# 語に続けて `((` を書ける語。`if((…))` の `(` は語を切り、算術を開く
_ARITHMETIC_KEYWORDS = _RESERVED_PREFIXES | {"for"}
_TIME_OPTIONS = frozenset({"-p", "--"})  # `time` の後で、文の先頭語の前に置ける語
# `<shell> -c` の形に限って、引用の中で割らないことによる deny の後退を避ける表。この表の外の
# 実行経路の後退は受け入れる (ADR 0106 の 2026-11-04 追記)
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash", "csh", "tcsh", "fish"})
_INLINE_FLAG = re.compile(r"-[A-Zabd-z]*c")  # `-c`・`-lc`・`-ce`・`-c'…'`

# `nesting` の種類
_DOUBLE_QUOTE = '"'
_PARAMETER_IN_DOUBLE_QUOTE = '"{'  # 二重引用の中の `${…}`。`'` は引用を開かない
_SUBSTITUTION = "("  # `$(…)`・`<(…)`・`>(…)`
_BACKTICK = "`"
_ARITHMETIC = "(("  # `$((…))`・`((…))`
_PARAMETER = "{"  # `${…}`
_OLD_ARITHMETIC = "["  # `$[…]`
_READS_AS_DOUBLE_QUOTE = frozenset({_DOUBLE_QUOTE, _PARAMETER_IN_DOUBLE_QUOTE})
_READS_NO_STATEMENT = frozenset({_ARITHMETIC, _PARAMETER, _OLD_ARITHMETIC})


def _delimiter_text(word: str) -> str:
    """here-document の delimiter の語から、shell が終端と照らす綴りを返す。"""

    def unquoted(part: re.Match) -> str:
        single, double, escaped = part.groups()
        if single is not None:
            return single
        return escaped if double is None else _DOUBLE_QUOTE_ESCAPE.sub(r"\1", double)

    return _DELIMITER_PART.sub(unquoted, word)


def _line_ends_by_text(text: str, ignores_leading_tabs: bool) -> dict[str, list[int]]:
    """行の綴りから、その綴りの行末の位置の一覧を引く索引を返す。"""
    line_ends: dict[str, list[int]] = {}
    line_start = 0
    for line in text.split("\n"):
        line_end = line_start + len(line)
        line_ends.setdefault(line.lstrip("\t") if ignores_leading_tabs else line, []).append(line_end)
        line_start = line_end + 1
    return line_ends


class _UnclosedQuote(Exception):
    """閉じない引用に当たった。`position` は一番外の閉じない引用の位置。"""

    def __init__(self, position: int) -> None:
        super().__init__(position)
        self.position = position


class _Nesting:
    """開いている二重引用・置換・算術・パラメータ展開 1 つ。"""

    __slots__ = ("kind", "opening", "depth", "open_cases")

    def __init__(self, kind: str, opening: int, depth: int = 0) -> None:
        self.kind = kind
        self.opening = opening
        self.depth = depth  # `$(…)` と算術の括弧の深さ
        self.open_cases = 0  # 閉じていない `case` の数。その間の `)` は pattern の閉じ


class _ShellLexer:
    """command を先頭から 1 度読み、segment と here-document の本体の位置を集める。

    `nesting` は開いている二重引用・置換・算術・パラメータ展開の積み上げで、here-document は
    開いた深さを持つ。置換の中は実行されるので、二重引用の中でも引用の外として読む。
    """

    def __init__(self, text: str, quotes_end: int, reads_here_documents: bool, *, ignores_quotes: bool = False) -> None:
        self.text = text
        self.quotes_end = quotes_end  # ここから後ろは引用も置換も here-document も開かない
        self.reads_here_documents = reads_here_documents
        self.ignores_quotes = ignores_quotes  # 区切りだけを読み、backslash も読まない
        self.segments: list[list[str]] = []
        self.statements: list[list[str]] = [[]]  # `;` `|` `&` 改行で割った語。置換をまたいで語を持つ
        self.here_document_bodies: list[tuple[int, int]] = []
        self.leaves_a_here_document_open = False
        self.words: list[str] = []
        self.word: list[str] = []
        self.nesting: list[_Nesting] = []
        self.at_statement_head = True  # 文の先頭語の前に置ける語しか読んでいない
        self.after_time = False  # 文の先頭の `time` とその option の直後
        self.pending: list[tuple[str, bool, int]] = []  # delimiter・`<<-` か・開いた nesting の深さ
        self.terminators: dict[bool, dict[str, list[int]]] = {}
        self.in_comment = False

    def run(self) -> None:
        index = 0
        while index < len(self.text):
            if self._top_kind() in _READS_AS_DOUBLE_QUOTE:
                index = self._read_in_double_quote(index)
            else:
                index = self._read_outside_quote(index)
        if any(nesting.kind in _READS_AS_DOUBLE_QUOTE for nesting in self.nesting):
            raise _UnclosedQuote(self._outermost_quote(index))
        if self.nesting:  # 閉じない置換や展開の後ろは、here-document を読めていない
            self.leaves_a_here_document_open = True
        self._end_segment()

    def _top_kind(self) -> str:
        return self.nesting[-1].kind if self.nesting else ""

    def _end_word(self) -> None:
        if not self.word:
            return
        word = "".join(self.word)
        is_unquoted = self.word == list(word)  # 引用と backslash は空の片を積むので、片と文字が揃わない
        self.word = []
        if self._top_kind() == _SUBSTITUTION and self.at_statement_head and is_unquoted and not self.in_comment:
            self._count_case(self.nesting[-1], word)
        if word:
            self.words.append(word)
            self.statements[-1].append(word)
            is_a_time_option = self.after_time and word in _TIME_OPTIONS
            self.at_statement_head = self.at_statement_head and (word in _RESERVED_PREFIXES or is_a_time_option)
            self.after_time = self.at_statement_head and (word == "time" or is_a_time_option)

    def _count_case(self, substitution: _Nesting, word: str) -> None:
        if word == "case":  # 引数の語や引用された語の `case` は文を開かない
            substitution.open_cases += 1
        elif word == "esac" and substitution.open_cases:
            substitution.open_cases -= 1

    def _end_segment(self) -> None:
        self._end_word()
        self.at_statement_head = True
        self.after_time = False
        if self.words:
            self.segments.append(self.words)
            self.words = []

    def _outermost_quote(self, index: int) -> int:
        return next((nesting.opening for nesting in self.nesting if nesting.kind in _READS_AS_DOUBLE_QUOTE), index)

    def _read_escape(self, index: int, in_double_quote: bool) -> int:
        """`\\` が打ち消す 1 文字を語へ足す。継続行 (`\\` + 改行) は語を繋ぐ。

        バッククォートの中の `` \\` `` は入れ子の置換を開くので、区切りとして読む。
        """
        if index + 1 == len(self.text):
            self.word.append("\\")
            return index + 1
        escaped = self.text[index + 1]
        if escaped == "`" and self._top_kind() == _BACKTICK:
            self._end_segment()
        elif escaped != "\n":
            literal = not in_double_quote or escaped in _ESCAPABLE_IN_DOUBLE_QUOTE
            self.word.extend((escaped if literal else "\\" + escaped, ""))
        return index + 2

    def _open_substitution(self, index: int) -> int | None:
        """`index` が `$(…)`・`$((…))`・`$[…]` を開くなら積み、次に読む位置を返す。開かなければ None。

        `${…}` は引用の中か外かで読み方が違うので、呼び出し側が積む。
        """
        if self.text.startswith("$((", index):
            self.word.append("$")
            self._end_segment()
            self.nesting.append(_Nesting(_ARITHMETIC, index, depth=2))
            return index + 3
        if self.text.startswith("$(", index):
            self.word.append("$")
            self._end_segment()
            self.nesting.append(_Nesting(_SUBSTITUTION, index, depth=1))
            return index + 2
        if self.text.startswith("$[", index):
            self.word.append("$[")
            self.nesting.append(_Nesting(_OLD_ARITHMETIC, index, depth=1))
            return index + 2
        return None

    def _read_in_double_quote(self, index: int) -> int:
        char = self.text[index]
        if char == "\\":
            return self._read_escape(index, in_double_quote=True)
        if char == '"':
            if self._top_kind() == _DOUBLE_QUOTE:
                self.nesting.pop()
            else:
                self.nesting.append(_Nesting(_DOUBLE_QUOTE, index))
            return index + 1
        if char == "}" and self._top_kind() == _PARAMETER_IN_DOUBLE_QUOTE:
            self.nesting.pop()
            self.word.append(char)
            return index + 1
        if char == "`":
            self._end_segment()
            self.nesting.append(_Nesting(_BACKTICK, index))
            return index + 1
        if self.text.startswith("$$", index):  # `"$$("` の `(` は置換を開かない
            self.word.append("$$")
            return index + 2
        if self.text.startswith("${", index):
            self.word.append("${")
            self.nesting.append(_Nesting(_PARAMETER_IN_DOUBLE_QUOTE, index))
            return index + 2
        opened = self._open_substitution(index)
        if opened is not None:
            return opened
        self.word.append(char)
        return index + 1

    def _closes_nesting(self, char: str) -> bool:
        """`char` で一番内側の置換か展開が閉じるかを返す。括弧の深さはここで数える。"""
        kind = self._top_kind()
        if kind in (_SUBSTITUTION, _ARITHMETIC):
            if char in "()":
                self._end_word()  # `esac)` の `esac` を `)` より先に数える
                nesting = self.nesting[-1]
                if not nesting.open_cases:
                    nesting.depth += 1 if char == "(" else -1
                return nesting.depth == 0
            return False
        if kind == _OLD_ARITHMETIC:
            if char in "[]":
                self.nesting[-1].depth += 1 if char == "[" else -1
            return self.nesting[-1].depth == 0
        return (kind, char) in ((_BACKTICK, "`"), (_PARAMETER, "}"))

    def _close_nesting(self, char: str) -> None:
        closed = self.nesting.pop()
        self._drop_here_documents_opened_inside()
        self.in_comment = False  # バッククォートは comment の中でも閉じる
        if closed.kind in (_PARAMETER, _OLD_ARITHMETIC):
            self.word.append(char)
            return
        self._end_segment()
        self.at_statement_head = False  # 外の文は続いている
        if closed.kind != _ARITHMETIC or self.text[closed.opening] == "$":
            self.word.append("")  # 閉じた直後の `#` は語の途中。`((…))` は文なので語を続けない

    def _read_single_quote(self, index: int) -> int:
        closing = self.text.find("'", index + 1)
        if closing < 0:
            raise _UnclosedQuote(self._outermost_quote(index))
        self.word.extend((self.text[index + 1:closing], ""))
        return closing + 1

    def _read_ansi_c_quote(self, index: int) -> int:
        body = _ANSI_C_QUOTE_BODY.match(self.text, index + 2)
        if body is None:
            raise _UnclosedQuote(self._outermost_quote(index))
        self.word.extend((body.group(1), ""))
        return body.end()

    def _delimiter_is_readable(self, start: re.Match) -> bool:
        follower = self.text[start.end():start.end() + 1]
        ends_the_word = not follower or follower in _DELIMITER_FOLLOWERS or (
            follower == "`" and self._top_kind() == _BACKTICK
        )
        return ends_the_word and _UNREADABLE_DELIMITER.search(start.group(2)) is None

    def _read_here_document_start(self, index: int) -> int:
        if self.text.startswith("<<<", index):  # here-string (本体を持たない)
            self.word.append("<<<")
            return index + 3
        if self._top_kind() in _READS_NO_STATEMENT:
            self.word.append("<<")
            return index + 2
        start = _HERE_DOCUMENT_START.match(self.text, index)
        if start is None:
            self._stop_reading_syntax(index)
            self.word.append("<<")
            return index + 2
        if not self._delimiter_is_readable(start):
            self._stop_reading_syntax(index)
        elif self.reads_here_documents:
            self.pending.append((_delimiter_text(start.group(2)), bool(start.group(1)), len(self.nesting)))
        self.word.append(start.group(0))
        return start.end()

    def _stop_reading_syntax(self, index: int) -> None:
        """読めない here-document の開始の後ろは、本体の引用文字を対にしないよう構文を読まない。"""
        self.leaves_a_here_document_open = True
        self.quotes_end = min(self.quotes_end, index)

    def _terminator_line_end(self, newline: int, delimiter: str, ignores_leading_tabs: bool) -> int | None:
        """`newline` の次の行から終端の行を探し、その行末の位置を返す。無ければ None。

        開始ごとに残りの行を読み直すと、終端の無い開始が並ぶ入力で行数の 2 乗の時間がかかるので、
        行の索引を 1 度だけ作る。
        """
        if ignores_leading_tabs not in self.terminators:
            self.terminators[ignores_leading_tabs] = _line_ends_by_text(self.text, ignores_leading_tabs)
        line_ends = self.terminators[ignores_leading_tabs].get(delimiter, [])
        following = bisect.bisect_right(line_ends, newline)
        return line_ends[following] if following < len(line_ends) else None

    def _skip_here_document_bodies(self, newline: int) -> int:
        """この深さで開いた本体を開いた順に読み飛ばし、次に読む位置を返す。

        外の深さで開いた本体は、外の改行まで読まない。終端が無い本体から後ろは読み続ける。
        """
        depth = len(self.nesting)
        ready: list[tuple[str, bool, int]] = []
        while self.pending and self.pending[-1][2] == depth:
            ready.append(self.pending.pop())
        end = newline
        for delimiter, ignores_leading_tabs, _ in reversed(ready):
            terminator_end = self._terminator_line_end(end, delimiter, ignores_leading_tabs)
            if terminator_end is None:
                self.leaves_a_here_document_open = True
                break
            end = terminator_end
        if end == newline:
            return newline + 1
        self.here_document_bodies.append((newline, end))
        return end

    def _drop_here_documents_opened_inside(self) -> None:
        """閉じた置換の中で開いた here-document を捨てる。

        本体は置換の中で終わるので、閉じた後の行を本体と読まず判定に残す。
        """
        depth = len(self.nesting)
        while self.pending and self.pending[-1][2] > depth:
            self.pending.pop()
            self.leaves_a_here_document_open = True

    def _opens_arithmetic_statement(self, index: int) -> bool:
        if self._top_kind() in _READS_NO_STATEMENT or not self.text.startswith("((", index):
            return False
        word = "".join(self.word)
        return not word or word in _ARITHMETIC_KEYWORDS or (self.after_time and word in _TIME_OPTIONS)

    def _read_outside_quote(self, index: int) -> int:
        char = self.text[index]
        if char == "\\" and not self.in_comment and not self.ignores_quotes:
            return self._read_escape(index, in_double_quote=False)
        reads_syntax = index < self.quotes_end and not self.in_comment
        if reads_syntax and self._opens_arithmetic_statement(index):
            self._end_segment()
            self.nesting.append(_Nesting(_ARITHMETIC, index, depth=2))
            return index + 2
        if (not self.in_comment or self._top_kind() == _BACKTICK) and self._closes_nesting(char):
            self._close_nesting(char)
            return index + 1
        if reads_syntax:
            if char == "'":
                return self._read_single_quote(index)
            if self.text.startswith("$$", index):  # `$$'…'` の `'` は ANSI-C 引用を開かない
                self.word.append("$$")
                return index + 2
            if self.text.startswith("$'", index):
                return self._read_ansi_c_quote(index)
            if char == '"':
                self.word.append("")  # 空の引用でも語は始まる
                self.nesting.append(_Nesting(_DOUBLE_QUOTE, index))
                return index + 1
            if self.text.startswith("${", index):
                self.word.append("${")
                self.nesting.append(_Nesting(_PARAMETER, index))
                return index + 2
            opened = self._open_substitution(index)
            if opened is not None:
                return opened
            if char == "`":
                self._end_segment()
                self.nesting.append(_Nesting(_BACKTICK, index))
                return index + 1
            if self.text.startswith(("<(", ">("), index) and self._top_kind() not in _READS_NO_STATEMENT:
                self.word.append(char)
                self._end_segment()
                self.nesting.append(_Nesting(_SUBSTITUTION, index, depth=1))
                return index + 2
        elif char in "'\"":
            return index + 1
        if char == "#" and not self.word and self._top_kind() not in _READS_NO_STATEMENT:
            self.in_comment = True
        if reads_syntax and self.text.startswith("<<", index):
            return self._read_here_document_start(index)
        if char == "\n":
            self.in_comment = False
            self._end_segment()
            self.statements.append([])
            return self._skip_here_document_bodies(index)
        if char in " \t":
            self._end_word()
        elif char in _SEGMENT_SEPARATORS:
            self._end_segment()
            if char in ";|&" and self.text[index - 1:index + 1] not in (">&", "<&"):  # `2>&1` は文を割らない
                self.statements.append([])
        else:
            self.word.append(char)
        return index + 1


def _lex_shell(command: str, *, reads_here_documents: bool) -> _ShellLexer:
    """`command` を shell の引用の規則で読む。here-document の除去と segment への分割の共通の土台。

    字句解析は shell の近似で、解釈できない部分は判定に残す側へ倒す (ADR 0046): 閉じない引用は
    引用として解釈せず、その位置から末尾までを引用の外として読み直す。ここから後ろは
    here-document も開かない。二重引用の中の `$(…)` とバッククォートは実行されるので引用の外として
    読む。comment の中の引用文字は対にならない (`# don't` の `'` が後ろの行を引用に閉じ込めないように)。
    comment の語は判定に残す。
    """
    quotes_end = len(command)
    while True:
        lexer = _ShellLexer(command, quotes_end, reads_here_documents)
        try:
            lexer.run()
        except _UnclosedQuote as unclosed:
            quotes_end = unclosed.position
            continue
        return lexer


def here_document_body_spans(command: str) -> list[tuple[int, int]]:
    """here-document の本体を `command` の中の (開始, 終了) で開いた順に返す。

    区間は開始行の末尾の改行から終端の行末までで、落とすと開始行の直後に後続の行が続く。
    引用・comment・算術の中の `<<WORD` は開始でない。終端が見つからない本体は区間に含めない —
    開始の読み違いで後続の行を判定から落とさないため (実 shell は残りを本体として読むので、残した
    行はデータかもしれない。書き換える側は `leaves_a_here_document_open` で確かめる)。
    """
    if "<<" not in command:
        return []
    return _lex_shell(command, reads_here_documents=True).here_document_bodies


def leaves_a_here_document_open(command: str) -> bool:
    """here-document を読み切れた確証が無いかを返す。

    本体の終端が無いときのほか、読めない delimiter や、閉じない置換・展開があるときも真を返す。
    持つ command の残りの行は実 shell がデータとして読みうるので、deny の判定には残すが、綴りを
    書き換えてはならない (データの中身が変わる)。
    """
    return "<<" in command and _lex_shell(command, reads_here_documents=True).leaves_a_here_document_open


def strip_here_documents(command: str) -> str:
    """here-document の本体と終端行を落とした写しを返す。

    本体は shell がデータとして読むだけで実行しないため、判定対象から落とす。危険なコマンドの
    説明文を `cat > doc.md <<EOF` で書くだけで deny される誤爆を塞ぐ。開始行そのものは残す —
    `find . -exec rm {} + && cat <<EOF` のように実コマンドが同居する形を取り逃さないため。
    何を本体と読むかは `here_document_body_spans` が決める。

    **allow と、許可の射程を広げる書き換えの判断には使わない** — 落とした写しで判断すると、本体に
    紛れた綴りで許可の射程が広がる。guard-cd-relative-glob-read の書き換えは、読み取り先へ path を
    前置するだけで許可を広げないので、この写しで判断してよい。
    """
    kept: list[str] = []
    position = 0
    for start, end in here_document_body_spans(command):
        kept.append(command[position:start])
        position = end
    kept.append(command[position:])
    return "".join(kept)


# C locale の grep / sed の POSIX 文字クラスと Python の `\s` / `\w` で分類が割れる文字。非 ASCII の文字は C locale の
# byte の読みと同じく、空白でも英数字でもない (#2299)。空白と読むと、語の中に非 ASCII を挟んだ破壊的な形が deny から
# 外れる。`\x1c-\x1f` は `\s` にだけ当たる ASCII の区切り制御文字
_CLASS_SPLITTING_CHARACTER = re.compile("[\x1c-\x1f\x80-\U0010ffff]")
# 割れる文字の置き換え先。空白でも英数字でもなく、guard の regex の literal にも現れない
_NEUTRAL = "\x00"


def posix_class_view(line: str) -> str:
    """`\\s` が C locale の `[[:space:]]`、`[^\\W_]` が `[[:alnum:]]` と同じ文字に当たる写し (長さは元と同じ)。"""
    return _CLASS_SPLITTING_CHARACTER.sub(_NEUTRAL, line)


def matches_any_line(pattern: re.Pattern, text: str) -> bool:
    """`printf '%s\\n' "$TEXT" | grep -qE` と同じく、どれかの行が `pattern` に一致するかを返す。

    行ごとに照合するので `^` `$` は行の端に当たり、否定の文字クラスも改行を越えない。
    `pattern` の `\\s` / `\\S` / `\\w` は C locale の grep の `[[:space:]]` / `[^[:space:]]` /
    `[[:alnum:]_]` と同じ文字に当たる。
    """
    return any(pattern.search(posix_class_view(line)) for line in text.split("\n"))


_GROUP_REFERENCE = re.compile(r"\\(\d)")


def substitute_lines(pattern: re.Pattern, replacement: str, text: str) -> str:
    """`printf '%s\\n' "$TEXT" | sed -E 's/<pattern>/<replacement>/g'` と同じ置換をする。

    照合は `matches_any_line` と同じ文字クラスで行い、置換結果には元の文字を使う。
    `replacement` の `\\1`〜`\\9` は group の参照。sh の `$(...)` が剥がす末尾の改行は
    呼び出し側が扱う。
    """
    rewritten: list[str] = []
    for line in text.split("\n"):
        pieces: list[str] = []
        end = 0
        for match in pattern.finditer(posix_class_view(line)):
            pieces.append(line[end:match.start()])

            def group_text(reference: re.Match, match: re.Match = match) -> str:
                start, stop = match.span(int(reference.group(1)))
                return line[start:stop] if start >= 0 else ""

            pieces.append(_GROUP_REFERENCE.sub(group_text, replacement))
            end = match.end()
        pieces.append(line[end:])
        rewritten.append("".join(pieces))
    return "\n".join(rewritten)


def continues_on_another_line(command: str) -> bool:
    """`command` が改行の後に 2 行目を持つかを返す。末尾の改行だけなら持たないとみなす。

    shell は改行をコマンドの区切りとして読むので、2 行目は 1 行目とは別のコマンドとして走る。
    allow を出す hook は、2 行目を持つ command に allow を出さない (#1698) — 1 行目だけを見て
    allow すると、2 行目のコマンドが承認なしで走る。行継続 (`\\` + 改行) や quote の中の改行も
    2 行目として数える: 区別するには shell の字句解析が要る。数えた command は allow を失い、
    各 hook の allow 以外の判定 (素通し・書き換え・deny) へ倒れる。
    """
    return "\n" in command.rstrip("\n")


def command_segments(command: str) -> list[list[str]]:
    """コマンドを区切り (`;` `|` `&` `(` `)` backtick 改行) で segment に割り、各 segment を語に分ける。

    引数や文字列中の語ではなく、単独コマンドの先頭語を見るための分割。引用の中の区切りと空白では
    割らず、語からは引用文字を落とす (`'find'` は素の語に揃う)。継続行 (`\\` + 改行) は繋ぐ。
    語の区切りは引用の外の空白と tab だけ。空の語と空の segment は返さない。

    here-document の本体は読まない — guard は `strip_here_documents` の写しを渡すので、写しに残る
    開始行から本体を読み直すと、終端の後ろの実コマンドを本体として落とす。開始は読み、読めない開始の
    後ろは構文を読まない。

    `eval` か `<shell> -c` を持つ command は、引用の中の文字列が実行されるので、全体を引用の中で
    割っていた頃の読みで返す (ADR 0106 の 2026-11-04 追記)。
    """
    lexer = _lex_shell(command, reads_here_documents=False)
    if not any(_runs_a_string(words) for words in lexer.statements):
        return lexer.segments
    return _segments_ignoring_quotes(command)


def _runs_a_string(words: list[str]) -> bool:
    """文の語が `eval` か `<shell> -c` を持つかを返す。

    語の位置を問わないのは、wrapper (`env`・`sudo`・`timeout` など) と option の並びを読まずに済むため。
    `echo sh -c 'a | b'` のように実行されない形も当たるが、引用の中を割っていた頃と同じ結果になる。
    """
    if "eval" in words:
        return True
    shell = next((i for i, word in enumerate(words) if word.rsplit("/", 1)[-1] in _SHELLS), None)
    return shell is not None and any(_INLINE_FLAG.match(word) for word in words[shell + 1:])


def _segments_ignoring_quotes(command: str) -> list[list[str]]:
    """引用・backslash・here-document を読まずに割る。継続行だけは繋ぐ。

    guard が渡す写しの本体はもう落ちている。読み直すと終端の後ろの実コマンドを落とすので、文字列の
    中の本体は判定に残す。
    """
    lexer = _ShellLexer(command.replace("\\\n", " "), 0, reads_here_documents=False, ignores_quotes=True)
    lexer.run()
    return lexer.segments
