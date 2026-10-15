"""`query` tool の実体 — store への **read-only な ad-hoc query** ([ADR 0031](../../../docs/adr/0031-transcript-store-elt.md))。

mart は「毎回同じ問いに同じ答えを返す観測契約」であり、想定外の追加検査には使えない。
その用途は従来 `90-mart.json` (数 MB の全量書き出し) が担っていたが、全 tool 呼び出しに
数 MB の書き出しコストを課す形だった。**全量書き出しを retire し、必要なときだけ
問いを書く経路**に置き換えたのが本 tool ([ADR 0030](../../../docs/adr/0030-inventory-claude-md-transcript-consumption.md)
が「消費者不在」で見送った任意データ抽出の、消費者が実在した形)。

安全側の制約は 3 つ:

- **read-only 接続** (`mode=ro`) で開く。書き込み文は SQLite 側で失敗する
- **単一の SELECT / WITH 文だけ**を通す。`ATTACH` / `PRAGMA` / 複数文は拒否する
  (`sqlite3` の `execute` が複数文を拒むのに加え、先頭 keyword を明示検査する)
- **行数 cap** つきで、結果は `/tmp` へ書いて **path だけを返す** (他 tool と同じ
  「大きく出して絞って読む」消費パターン。context に本体を載せない)

store の schema は `SELECT sql FROM sqlite_master` で引ける。列の意味は
`store/schema.sql` が正本。

**mart の代替にしない**: 恒常的に必要になった集計は mart の分割出力 / derived view の
拡張として提案する。ad-hoc query を手順に埋めると、観測契約を持たない集計が棚卸しの
既定経路になり、再現性 (同じ lake + 同じ窓 → 同じ答え) の保証が外れる。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from enum import Enum, auto
from pathlib import Path
from typing import Any, NamedTuple

from adapter.transcript import resolve_now
from artifacts import prepare_output_dir
from store import ingest
from store import store as store_mod
from store.lock import SyncLockTimeout

DEFAULT_TRANSCRIPTS_DIR = Path("~/.claude/projects").expanduser()
DEFAULT_OUTPUT_DIR = Path("/tmp/transcript-ops-query")

# 既定の行数 cap と、引数で許す上限。上限を置くのは「返るのは path」でも書き出し
# コストと読み手の負荷が青天井にならないようにするため。
DEFAULT_ROW_LIMIT = 500
MAX_ROW_LIMIT = 10_000

# 通す先頭 keyword。read-only 接続でも `PRAGMA` / `ATTACH` は通ってしまうため、
# **接続の権限とは独立に**文の形で絞る。
ALLOWED_PREFIXES = ("select", "with")

# 先頭 keyword を見る前に落とす行コメント / ブロックコメント。
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


class QueryRejected(ValueError):
    """read-only 契約に反する SQL。**黙って空を返さず失敗させる**。"""


class QueryFailed(RuntimeError):
    """契約は満たしたが、文の実行が失敗した。

    失敗の種類は `classify_failure` が決め、どの失敗を包むか (`is_store_failure`)・
    添える次の一手 (`next_step_for`)・載せる例外の記述 (`failure_detail`) は種類から
    `_HANDLING` を引く。
    """


# 失敗の種類ごとの次の一手 (エラー文言はここを参照する)。
SCHEMA_HINT = "schema は `SELECT sql FROM sqlite_master` で引ける"
READ_ONLY_HINT = ("store は read-only で開いている。WITH 句の後は SELECT で終える "
                  "(書き込み文は実行できない)")
BIND_HINT = "tool は bind 引数を渡さない。値は SQL にリテラルで書く"
RETRY_HINT = "store を別の処理が使用中。少し待って同じ文を再試行する"
DECODE_HINT = "TEXT として読めない値がある。hex() か CAST(... AS BLOB) で読む"
NEXT_STEP_HINTS = (SCHEMA_HINT, READ_ONLY_HINT, BIND_HINT, RETRY_HINT, DECODE_HINT)

# store 側 (環境・本 module の誤用) の失敗を表す primary result code。SQLite の
# result code 一覧 (https://sqlite.org/rescode.html) のうち、書き手がどう SQL を
# 書き換えても直らないものだけを挙げる:
# INTERNAL (SQLite 内部の不整合) / PERM・CANTOPEN・NOLFS (file を開けない・権限) /
# NOMEM・FULL (資源切れ) / IOERR (I/O 障害) / CORRUPT・NOTADB・FORMAT (store の
# 破損) / NOTFOUND・PROTOCOL・MISUSE (VFS や API の誤用 = 本 module の不具合)。
# 拡張 code の READONLY_* (DBMOVED / CANTLOCK 等) も環境の失敗で、これは
# `classify_failure` が別に STORE へ振る。
_STORE_FAILURE_CODES = frozenset({
    sqlite3.SQLITE_INTERNAL, sqlite3.SQLITE_PERM, sqlite3.SQLITE_NOMEM,
    sqlite3.SQLITE_IOERR, sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTFOUND,
    sqlite3.SQLITE_FULL, sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_PROTOCOL,
    sqlite3.SQLITE_MISUSE, sqlite3.SQLITE_NOLFS, sqlite3.SQLITE_FORMAT,
    sqlite3.SQLITE_NOTADB,
})

# 一時的な競合 (再試行で直る) を表す primary result code。
_RETRYABLE_CODES = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})

# schema を引けば直せる SQLITE_ERROR の文言の頭。
_MISSING_NAME_PREFIXES = ("no such column", "no such table")

# 閉じた接続 / cursor を使った `ProgrammingError` の文言の頭 (本 module の誤用)。
_CLOSED_OBJECT_PREFIX = "Cannot operate on a closed"

# 結果を UTF-8 として読めなかった CPython の文言の頭と、text の手前までの形。
# text 以降にセルの中身が入る。列名は式そのものなら改行を含みうる。
_DECODE_PREFIX = "Could not decode"
_DECODE_COLUMN_RE = re.compile(
    rf"({re.escape(_DECODE_PREFIX)} to UTF-8 column '.*?') with text '", re.DOTALL)

# SQLite の文言の中で `'` が引く範囲。SQLite は値を `'...'` で引き、値の中の `'` を
# `''` に書く。最初の `'` から最後の `'` までをまとめて取るのは、`''` で引用が途中で
# 切れても残りを返さないため。引用が複数あると間の文言も落ちる。CPython 側の文言は
# ここを通さない (種類ごとの `Wording` に従い、そのまま返すか列名だけ残すか省く)。
_QUOTED_RE = re.compile(r"'.*'", re.DOTALL)

# code を持たない (CPython 側の) bind の誤りの文言の頭。引くのは SQL 中の parameter
# 名・番号と、渡された引数の型名。bind は文の実行前に失敗するので、store の値は文言に
# 入りえない。頭で見るのは、文言のどこかに語があるだけ (値の中の語) で bind と見なさない
# ため。
_BIND_PREFIXES = (
    "You did not supply a value for binding parameter",
    "Binding ",
    "Incorrect number of bindings supplied",
    "Error binding parameter",
    "parameters are of unsupported type",
)

# SQL 中の null 文字の CPython 側の文言の頭 (値を引かない)。
_NULL_CHARACTER_PREFIX = "the query contains a null character"

# 文言を落としたことを示す印。落とした文言が無かったのか秘匿で落ちたのかを読み分ける。
OMITTED_WORDING = "(値を含みうる未知の文言なので省いた)"

# QueryFailed の文面に載せる例外の文言の上限長。文言は SQL の書き手が直すために
# 返すもので、上限を超えるのは長い名前 (別名の無い式そのものの列名・長い parameter
# 名・存在しない長い列名) で、超えた分は直す手掛かりにならない。
MAX_FAILURE_DETAIL_CHARS = 300


class FailureKind(Enum):
    """`sqlite3.Error` の種類。種類ごとの扱いは `_HANDLING` が持つ。"""

    STORE = auto()               # 環境・store の破損 (SQL を直しても通らない)
    RETRYABLE = auto()           # 一時的な競合
    READ_ONLY = auto()           # read-only 接続への書き込み
    MISSING_NAME = auto()        # 存在しない列 / 表の名前
    DECODE = auto()              # 結果を UTF-8 として読めない
    BIND = auto()                # bind 引数の誤り
    NULL_CHARACTER = auto()      # SQL 中の null 文字
    CLOSED_OBJECT = auto()       # 閉じた接続 / cursor の使用 (本 module の誤用)
    OTHER_SQLITE_ERROR = auto()  # code を持つ残り (構文の誤り・TOOBIG 等)
    UNKNOWN = auto()             # code を持たず、文言の頭が既知でない


class Wording(Enum):
    """`failure_detail` が例外の文言をどう載せるか。"""

    AS_IS = auto()          # store の値を引かない文言なので、そのまま
    COLUMN_ONLY = auto()    # decode の文言。列名だけを残す
    QUOTED_HIDDEN = auto()  # SQLite の文言。`'` が引く範囲を `'…'` に置き換える
    OMITTED = auto()        # 値を引く形か判別できない文言。`OMITTED_WORDING` に置き換える


class _Handling(NamedTuple):
    crashes: bool           # 包まず crash として上げるか
    next_step: str | None   # 包むときに添える次の一手
    wording: Wording


# crash 側 (STORE / CLOSED_OBJECT) の文言は文面に載らないので、秘匿側の OMITTED を置く。
_HANDLING = {
    FailureKind.STORE: _Handling(True, None, Wording.OMITTED),
    FailureKind.RETRYABLE: _Handling(False, RETRY_HINT, Wording.QUOTED_HIDDEN),
    FailureKind.READ_ONLY: _Handling(False, READ_ONLY_HINT, Wording.QUOTED_HIDDEN),
    FailureKind.MISSING_NAME: _Handling(False, SCHEMA_HINT, Wording.QUOTED_HIDDEN),
    FailureKind.DECODE: _Handling(False, DECODE_HINT, Wording.COLUMN_ONLY),
    FailureKind.BIND: _Handling(False, BIND_HINT, Wording.AS_IS),
    FailureKind.NULL_CHARACTER: _Handling(False, None, Wording.AS_IS),
    FailureKind.CLOSED_OBJECT: _Handling(True, None, Wording.OMITTED),
    FailureKind.OTHER_SQLITE_ERROR: _Handling(False, None, Wording.QUOTED_HIDDEN),
    FailureKind.UNKNOWN: _Handling(False, None, Wording.OMITTED),
}


def classify_failure(exc: sqlite3.Error) -> FailureKind:
    """失敗の種類を決める。包むか・一手・文言の扱いはどれもこの結果から引く。

    `sqlite_errorcode` を持たない失敗は CPython が投げたもので、型と文言の頭で見る。
    crash 側は本 module の誤用 (`InterfaceError` と、閉じた接続 / cursor の
    `ProgrammingError`) だけで、文言の頭が既知でなければ UNKNOWN (文言を省く) に倒す。

    code を持つ失敗は code で見る。**列挙するのは crash 側だけで、残りは包む** — 書き手が
    原因の失敗は種類が開いている (SQLITE_ERROR・TOOBIG 等) ので、許可側を列挙すると、
    列挙し漏れた種類が黙って不透明な crash に倒れる。文言で見るのは decode の頭と
    SQLITE_ERROR の中の名前の誤りの 2 つで、名前の誤りの文言が版で変わると
    OTHER_SQLITE_ERROR に落ちて一手が付かなくなる。

    decode の文言は値を引くので、code の有無を問わず頭で見て、再試行・read-only・名前の
    誤りより先に DECODE に振る (秘匿は広い側に倒す)。ただし crash 側の判定はさらに先に
    置く — store 側の失敗は文言を問わず包まない。
    """
    message = str(exc)
    code = getattr(exc, "sqlite_errorcode", None)
    if code is None:
        if isinstance(exc, sqlite3.InterfaceError):
            return FailureKind.STORE
        if (isinstance(exc, sqlite3.ProgrammingError)
                and message.startswith(_CLOSED_OBJECT_PREFIX)):
            return FailureKind.CLOSED_OBJECT
        if message.startswith(_DECODE_PREFIX):
            return FailureKind.DECODE
        if message.startswith(_BIND_PREFIXES):
            return FailureKind.BIND
        if message.startswith(_NULL_CHARACTER_PREFIX):
            return FailureKind.NULL_CHARACTER
        return FailureKind.UNKNOWN
    primary = code & 0xFF
    if primary in _STORE_FAILURE_CODES or (
            primary == sqlite3.SQLITE_READONLY and code != sqlite3.SQLITE_READONLY):
        return FailureKind.STORE
    if message.startswith(_DECODE_PREFIX):
        return FailureKind.DECODE
    if primary in _RETRYABLE_CODES:
        return FailureKind.RETRYABLE
    if code == sqlite3.SQLITE_READONLY:
        return FailureKind.READ_ONLY
    if code == sqlite3.SQLITE_ERROR and message.startswith(_MISSING_NAME_PREFIXES):
        return FailureKind.MISSING_NAME
    return FailureKind.OTHER_SQLITE_ERROR


def is_store_failure(exc: sqlite3.Error) -> bool:
    """包まず crash として上げる失敗 (書き手が SQL を直しても通らない) か。"""
    return _HANDLING[classify_failure(exc)].crashes


def next_step_for(exc: sqlite3.Error) -> str | None:
    """包む失敗に添える次の一手 (`NEXT_STEP_HINTS` のどれか)。無ければ None。"""
    return _HANDLING[classify_failure(exc)].next_step


def failure_detail(exc: sqlite3.Error) -> str:
    """QueryFailed の文面に載せる例外の記述。store の値 (セルの中身) を載せない。

    書き手が SQL を直す手掛かりとして残すのは、例外の型名、SQLite の result code 名
    (`SQLITE_ERROR` 等。CPython 側の失敗には無い)、種類ごとの `Wording` で扱った文言
    (文言ごと落としたときは `OMITTED_WORDING`) の 3 つ。SQLite の文言は値を `'...'` で
    引く (`bad JSON path: '<セルの値>'` 等) ので、引用された SQL 中の literal も落ちる。
    名前の誤り (`no such column: x`) や構文の誤り (`near "x"`) は引用しないので残る。
    decode の文言から列名を取れない形に文言が変わったら、列名も落とす。

    残った文言は `MAX_FAILURE_DETAIL_CHARS` で切り詰める。
    """
    code_name = getattr(exc, "sqlite_errorname", None)
    label = f"{type(exc).__name__} {code_name}" if code_name else type(exc).__name__
    wording = _HANDLING[classify_failure(exc)].wording
    if wording is Wording.OMITTED:
        return f"{label}: {OMITTED_WORDING}"
    message = str(exc)
    if wording is Wording.COLUMN_ONLY:
        column = _DECODE_COLUMN_RE.match(message)
        message = column.group(1) if column else _DECODE_PREFIX
    elif wording is Wording.QUOTED_HIDDEN:
        message = _QUOTED_RE.sub("'…'", message)
    if len(message) > MAX_FAILURE_DETAIL_CHARS:
        omitted = len(message) - MAX_FAILURE_DETAIL_CHARS
        message = f"{message[:MAX_FAILURE_DETAIL_CHARS]}… (以下 {omitted} 文字を省略)"
    return f"{label}: {message}"


def strip_comments(sql: str) -> str:
    return _BLOCK_COMMENT_RE.sub(" ", _LINE_COMMENT_RE.sub(" ", sql or "")).strip()


def ensure_read_only(sql: str) -> str:
    """単一の SELECT / WITH 文であることを確かめて本文を返す。

    `;` の検査は文字列リテラル内の `;` も複数文と見なす近似で、**誤って通すことは
    なく誤って弾くことはある**。read-only 契約を守る側に倒した設計で、弾かれたら
    リテラルを `char(59)` 等へ書き換える。
    """
    body = strip_comments(sql).rstrip().rstrip(";")
    if not body:
        raise QueryRejected("SQL が空")
    if ";" in body:
        raise QueryRejected("複数文は受け付けない (1 回 1 文)")
    head_match = re.match(r"[A-Za-z]+", body)
    head = head_match.group(0).lower() if head_match else ""
    if head not in ALLOWED_PREFIXES:
        raise QueryRejected(
            f"先頭が {' / '.join(ALLOWED_PREFIXES)} の文だけを受け付ける (受領: {head})")
    return body


def _cell(value: Any) -> Any:
    """JSON へ落とせる値へ。bytes は中身を出さず長さだけ残す。"""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    return value


def run_query(sql: str, params: dict | list | None, limit: int,
              transcripts_dir: Path, cache_dir: Path | None,
              now: dt.datetime) -> dict:
    """差分 sync を前置してから read-only 接続で 1 文を実行する。

    sync (書き込み) と query (読み取り) で接続を分けるのは、**read-only を接続の
    属性として持たせる**ため。同じ接続を使い回すと「書けない接続」であることを
    呼び出し順に依存して保証することになる。
    """
    conn, sync_report = ingest.open_synced(transcripts_dir, cache_dir=cache_dir,
                                           now=now)
    try:
        anomalies = store_mod.anomalies(conn)
    finally:
        conn.close()

    path = store_mod.store_path(transcripts_dir, cache_dir)
    reader = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    reader.row_factory = sqlite3.Row
    try:
        cursor = reader.execute(sql, params if params is not None else {})
        columns = [description[0] for description in (cursor.description or [])]
        # cap + 1 件取って「打ち切られたか」を silent にしない
        fetched = cursor.fetchmany(limit + 1)
    except sqlite3.Error as exc:
        if is_store_failure(exc):
            raise
        reason = f"SQL の実行に失敗した ({failure_detail(exc)})"
        hint = next_step_for(exc)
        raise QueryFailed(f"{reason}。{hint}" if hint else reason) from exc
    finally:
        reader.close()

    truncated = len(fetched) > limit
    rows = [{column: _cell(row[column]) for column in columns}
            for row in fetched[:limit]]
    return {
        "meta": {
            "generated_at": now.isoformat().replace("+00:00", "Z"),
            "transcripts_dir": str(transcripts_dir),
            "store_path": str(path),
            "sql": sql,
            "params": params,
            "limit": limit,
            "row_count": len(rows),
            "truncated_by_limit": truncated,
            "columns": columns,
            "store": {
                **anomalies,
                "skipped_nested_files": sync_report.skipped_nested_files,
                "synced_files": sync_report.ingested_files,
            },
        },
        "rows": rows,
    }


def emit(result: dict, output_dir: Path) -> str:
    """`query-<timestamp>.json` を 0700 で書いて path を返す。"""
    resolved = prepare_output_dir(output_dir)
    generated = dt.datetime.fromisoformat(result["meta"]["generated_at"])
    out = resolved / f"query-{generated.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    return str(out)


def run(
    sql: str,
    params: dict | None = None,
    limit: int = DEFAULT_ROW_LIMIT,
    transcripts_dir: str = str(DEFAULT_TRANSCRIPTS_DIR),
    output_dir: str = str(DEFAULT_OUTPUT_DIR),
    now: str | None = None,
) -> dict[str, Any]:
    """tool 側の入口。結果本体は返さず、書いた path と件数 meta だけを返す。"""
    body = ensure_read_only(sql)
    capped = max(1, min(limit, MAX_ROW_LIMIT))
    result = run_query(body, params, capped, Path(transcripts_dir), None,
                       resolve_now(now))
    return {"path": emit(result, Path(output_dir)), "meta": result["meta"]}


# --- CLI ---------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="store への read-only ad-hoc query")
    p.add_argument("--sql", required=True)
    p.add_argument("--limit", type=int, default=DEFAULT_ROW_LIMIT)
    p.add_argument("--transcripts-dir", type=Path, default=DEFAULT_TRANSCRIPTS_DIR)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--cache-dir", type=Path, default=None)
    p.add_argument("--now", type=str, default=None)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        body = ensure_read_only(args.sql)
        result = run_query(body, None, max(1, min(args.limit, MAX_ROW_LIMIT)),
                           args.transcripts_dir, args.cache_dir,
                           resolve_now(args.now))
    except (QueryRejected, QueryFailed, SyncLockTimeout) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 2
    print(emit(result, args.output_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
