"""harness hook の共有 prelude — payload 読み取り・PreToolUse envelope の出力・shell 文字列の前処理。

Why: harness hook は 12 本以上あり、どれも「stdin の JSON を読む」「`hookSpecificOutput`
envelope を stdout へ出す」を各自で綴っている。逐語重複そのものは 40〜50 行と小さいが、
実害は行数ではなく **fail-posture がコピー元の偶然で決まること**にある — #250 の決定後に
追加された 4 本 (guard-rm-force / guard-find / guard-xargs / guard-bulk-stage) は、いずれも
根拠コメントが「guard-find.sh と同じ側へ倒す」のような参照であって脅威モデルではない。

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
        sys.stdout.write(stdout_text)
        sys.exit(exit_code)

stdlib のみを使う。hook は Bash 呼び出しのたびに起動するため、外部依存の解決コストと
不在時の分岐 (現行 sh hook の jq 検査) をここで消す。
"""
from __future__ import annotations

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
    列挙してこの逐語と突き合わせる — sh guard は prelude を import できないので、
    同じ 1 行を各 script が綴り、一致はテストが担保する (ADR 0043)。
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


def allow(reason: str, updated_input: dict | None = None) -> NoReturn:
    """判断を allow で確定して `judge` を抜ける (`Finished` を raise する)。`updated_input` を渡すと入力を書き換える。"""
    fields: dict[str, object] = {
        "permissionDecision": "allow",
        "permissionDecisionReason": reason,
    }
    if updated_input is not None:
        fields["updatedInput"] = updated_input
    _emit(_envelope(**fields))


def allow_rewritten(updated_input: dict) -> NoReturn:
    """入力を書き換えて allow する。理由は載せない。

    allow の 3 つの出し方は意図で選ぶ: 判断の理由を利用者に見せるなら `allow(reason, ...)`、
    書き換え先そのものが判断を語る (書き換えた command が出力に載る) なら本関数、判断を出さず
    書き換え後の行を通常の permission 評価に回すなら `rewrite()`。迷ったら `rewrite()` に倒す —
    allow は deny / ask rule を越えないまま「hook が許した」痕跡を残す。
    """
    _emit(_envelope(permissionDecision="allow", updatedInput=updated_input))


def rewrite(updated_input: dict) -> NoReturn:
    """判断を出さずに入力だけ書き換えて `judge` を抜ける。

    書き換え後の行に対して通常の permission 評価を走らせたいときに使う — allow を付けると
    deny / ask rule を越えないまま「hook が許した」痕跡だけが残る。
    """
    _emit(_envelope(updatedInput=updated_input))


def passthrough() -> NoReturn:
    """判断を出さずに `judge` を抜ける。settings の permission 層と他の guard に委ねる。

    判断は出さないが**出力はする** (#587)。無出力で exit した hook は transcript に
    attachment を残さず、`scan_permissions` の hook 軸では `fire_count: null` になる —
    「壊れて静かに死んだ guard」と「窓内に出番が無かった guard」が棚卸しで同じ見え方に
    なる。`permissionDecision` を持たない `hookSpecificOutput` は仕様上合法で、通常の
    permission フローへ完全に委ねる (意味論は無出力と同じ) が、非空 stdout なので
    `hook_success` record は残る。`suppressOutput` は transcript mode の表示だけを
    抑止するもので record は抑止しない (実 transcript で確認済み)。
    """
    _emit_pass_trace()


def _parse(raw: str) -> tuple[dict | None, str]:
    """payload を検査して (payload, 失敗理由) を返す。成功時の理由は空文字。

    `tool_input` が object でなければ読み取り失敗として扱う。**不在 (null) も失敗側に含める** —
    jq の `(.tool_input | type) != "object"` で判定する sh guard (`guard-git.sh` 等) では null が
    ここに落ちるため。実際の PreToolUse payload には常に `tool_input` があるので到達
    しない入力だが、**contract を定義する側が兄弟と食い違っていること自体が欠陥**なので揃える
    (逆方向へ「直される」のを防ぐテストは `test_prelude.py` の parity 検査が持つ)。

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


# here-document の開始。quote は開閉が揃っていなくても素の語を delimiter にする
_HERE_DOCUMENT_START = re.compile(r"<<(-?)[ \t]*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")


def strip_here_documents(command: str) -> str:
    """here-document の本体と終端行を落とした写しを返す。

    本体は shell がデータとして読むだけで実行しないため、判定対象から落とす。危険なコマンドの
    説明文を `cat > doc.md <<EOF` で書くだけで deny される誤爆を塞ぐ。開始行そのものは残す —
    `find . -exec rm {} + && cat <<EOF` のように実コマンドが同居する形を取り逃さないため。終端が
    現れない入力は末尾まで落とす (実 shell も残りを本体として扱う)。引用文字列中の `<<`
    (`git commit -m "a << b"`) を開始と誤認する余地は残るが、shell parser 無しに実コマンドと
    区別する術は無いので受容する。

    **deny の判定にだけ使う** — 落とした写しで allow / 書き換えを判断すると、本体に紛れた綴りで
    許可の射程が広がる。
    """
    kept: list[str] = []
    pending: list[tuple[str, bool]] = []
    for line in command.split("\n"):
        if pending:
            delimiter, ignores_leading_tabs = pending[0]
            body = line.lstrip("\t") if ignores_leading_tabs else line
            if body == delimiter:
                pending.pop(0)  # 同じ行の複数の here-document を順に閉じる
            continue
        kept.append(line)
        for match in _HERE_DOCUMENT_START.finditer(line):
            if match.start() > 0 and line[match.start() - 1] == "<":
                continue  # `<<<` は here-string (本体を持たない)
            pending.append((match.group(2), bool(match.group(1))))
    return "\n".join(kept)


# grep / sed の POSIX 文字クラスと Python の `\s` / `\w` で分類が割れる文字。macOS の
# /usr/bin/grep (en_US.UTF-8) を全コードポイントで実測すると、`[[:space:]]` は `\s` から
# 区切り制御文字 (\x1c-\x1f) と NEL (\x85) を除いた集合、`[[:alnum:]]` は Python の英数字から
# Unicode カテゴリ No / Nl (`²` `Ⅳ` など) を除いた集合だった。
_CLASS_SPLITTING_CONTROLS = "\x1c\x1d\x1e\x1f\x85"
_CLASS_SPLITTING_CANDIDATE = re.compile("[\x1c-\x1f\x80-\U0010ffff]")
# 割れる文字の置き換え先。空白でも英数字でもなく、guard の regex の literal にも現れない
_NEUTRAL = "\x00"


class _NeutralizingTable(dict):
    """`str.translate` の表。文字ごとの置き換え先を初出で決めて覚える (長い非 ASCII の行で
    文字ごとに Python の callback を呼ぶと、guard 1 本で秒単位かかるため)。"""

    def __missing__(self, codepoint: int) -> int:
        char = chr(codepoint)
        if char in _CLASS_SPLITTING_CONTROLS:
            target = ord(_NEUTRAL)
        else:
            import unicodedata  # 非 ASCII を含む行でだけ要る

            target = ord(_NEUTRAL) if unicodedata.category(char) in ("No", "Nl") else codepoint
        self[codepoint] = target
        return target


_NEUTRALIZING_TABLE = _NeutralizingTable()


def _posix_class_view(line: str) -> str:
    """`\\s` が `[[:space:]]`、`[^\\W_]` が `[[:alnum:]]` と同じ文字に当たる写し (長さは元と同じ)。"""
    if _CLASS_SPLITTING_CANDIDATE.search(line) is None:
        return line
    return line.translate(_NEUTRALIZING_TABLE)


def matches_any_line(pattern: re.Pattern, text: str) -> bool:
    """`printf '%s\\n' "$TEXT" | grep -qE` と同じく、どれかの行が `pattern` に一致するかを返す。

    行ごとに照合するので `^` `$` は行の端に当たり、否定の文字クラスも改行を越えない。
    `pattern` の `\\s` / `\\S` / `\\w` は grep の `[[:space:]]` / `[^[:space:]]` /
    `[[:alnum:]_]` と同じ文字に当たる。
    """
    return any(pattern.search(_posix_class_view(line)) for line in text.split("\n"))


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
        for match in pattern.finditer(_posix_class_view(line)):
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

    末尾の改行を落とすのは sh の `$(...)` と同じ。sh のまま残る `guard-git.sh` の push の allow は
    `$(...)` で読んだ command に改行が 1 つでもあれば allow しないので、同じ結果になる
    (一致は `tests/test_prelude_continues_on_another_line.py` が見る)。
    """
    return "\n" in command.rstrip("\n")


_SEGMENT_SEPARATOR = re.compile(r"[;|&()`\n]+")
_WORD_SEPARATOR = re.compile(r"[ \t\n]+")


def command_segments(command: str) -> list[list[str]]:
    """コマンドを区切り (`;` `|` `&` `(` `)` backtick 改行) で segment に割り、各 segment を語に分ける。

    引数や文字列中の語ではなく、単独コマンドの先頭語を見るための分割。quote は落として
    先頭語の綴り (`'find'`) を素の語に揃え、継続行 (`\\` + 改行) は 1 つの segment に繋ぐ。
    語の区切りは空白と tab だけ (awk の `split(s, a, " ")` と同じ)。空の segment は返さない。
    """
    text = (command.replace('"', "").replace("'", "") + "\n").replace("\\\n", " ")
    segments = (_WORD_SEPARATOR.split(segment) for segment in _SEGMENT_SEPARATOR.split(text))
    return [words for words in ([word for word in segment if word] for segment in segments) if words]
