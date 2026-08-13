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
from pathlib import Path
from typing import Any

from adapter.transcript import resolve_now
from artifacts import prepare_output_dir
from store import ingest
from store import store as store_mod

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
    except QueryRejected as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 2
    result = run_query(body, None, max(1, min(args.limit, MAX_ROW_LIMIT)),
                       args.transcripts_dir, args.cache_dir, resolve_now(args.now))
    print(emit(result, args.output_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
