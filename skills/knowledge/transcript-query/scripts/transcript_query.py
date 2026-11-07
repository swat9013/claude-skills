#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb==2.0.0"]
# ///
"""transcript の lake を DuckDB で直接読み、成果物を出力先へ書いて path と件数 meta を返す。

lake の on-disk 形式は `views.sql` と `schema-snapshot.json` だけが知る。本 script は
lake を読み取り専用で開き、照合と実行と書き出しだけを持つ。

invariant:
1. named query は schema snapshot の照合を通らなければ答えを返さない。ad-hoc の問いは止めずに照合の結果を meta に載せる
2. DuckDB は lake の外を読めず、どこにも書けない。設定は lake の中への書き出しを塞がないので、
   問いは 1 本の SELECT に限る
3. 成果物の本体を stdout に出さない。出力 dir は 0700 で、他人の dir や symlink には書かない
4. 劣化の件数は成果物と同じ母集団で数える
5. 失敗の本文に lake の値を載せない (値を引用しない種類の失敗だけ DuckDB の文言を返す)
6. lake に読める transcript が無ければ、理由を返して止まる
7. named query は問いが宣言した条件だけを受け、宣言の無い名前と必須の条件の欠けは lake を読む前に止める

stdout は 1 個の JSON。exit 0 = 答えた / 1 = 止まった。
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import math
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Mapping

import duckdb

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from named_query import ALL_POPULATION, RECOVERY, NamedQuery, QueryRun, Stopped, Window  # noqa: E402
from named_query import resolve_repo as _resolve_repo  # noqa: E402

SKILL_DIR = SCRIPTS_DIR.parent
VIEWS_SQL = SKILL_DIR / "views.sql"
NAMED_QUERIES_DIR = SCRIPTS_DIR / "named_queries"
SCHEMA_SNAPSHOT = SKILL_DIR / "schema-snapshot.json"

DEFAULT_LAKE = Path.home() / ".claude" / "projects"
OUTPUT_ROOT = Path("/tmp")
# JSON の解釈は chunk ごとに thread の数だけ領域を積むので、巨大な行が続く chunk を読める値にする。
# spill を無効にするので、超えた問いは失敗する
MEMORY_LIMIT = "4GB"
THREADS = 4


def _load_named_queries() -> dict[str, NamedQuery]:
    """`named_queries/<名前>.py` の `QUERY` を集める。`_` で始まる file は問いの部品で、問いではない。

    呼び出し名は file 名の `_` を `-` にしたもの。消費者が実在する問いだけを足す。上限は 10 本。
    """
    return {path.stem.replace("_", "-"): importlib.import_module(f"named_queries.{path.stem}").QUERY
            for path in sorted(NAMED_QUERIES_DIR.glob("[!_]*.py"))}


NAMED_QUERIES = _load_named_queries()


def _load_snapshot() -> dict[str, list[dict]]:
    return json.loads(SCHEMA_SNAPSHOT.read_text(encoding="utf-8"))


def record_structure(entries: list[dict]) -> str:
    """json_transform に渡す列の宣言。入れ子を持つ top の key は JSON のまま受ける。"""
    structure: dict[str, str] = {}
    for entry in entries:
        top, _, rest = entry["path"].partition(".")
        structure[top] = "JSON" if rest else entry["type"]
    return json.dumps(structure)


def _walk_lake(lake: Path) -> tuple[list[dict], list[str]]:
    """lake の file の一覧と、中へ入れなかった dir。読めるかはここで確かめる。

    symlink は読めない file と dir に数える。DuckDB は symlink の先が lake の外だと問い全体を拒むので、読む一覧に
    入れない。dir の symlink は `os.walk` が黙って降りないので、ここで拾う。
    """
    unreadable_dirs: list[str] = []
    entries = []
    for root, dirs, names in os.walk(lake, onerror=lambda error: unreadable_dirs.append(error.filename)):
        unreadable_dirs.extend(os.path.join(root, name) for name in dirs if os.path.islink(os.path.join(root, name)))
        for name in names:
            if not name.endswith((".jsonl", ".json")):
                continue
            path = os.path.join(root, name)
            try:
                info = os.lstat(path)
            except OSError:
                info = None
            entries.append({
                "path": path,
                "rel": os.path.relpath(path, lake),
                "readable": info is not None and stat.S_ISREG(info.st_mode) and os.access(path, os.R_OK),
                "size_bytes": info.st_size if info else 0,
                "mtime": info.st_mtime if info else 0.0,
            })
    return entries, [os.path.relpath(path, lake) for path in unreadable_dirs]


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _lock_down(con: duckdb.DuckDBPyConnection, lake: Path) -> None:
    """lake の読み込みだけを残す。spill は prompt の本文を一時 dir に出すので無効にする。"""
    for statement in (
        f"SET allowed_directories=[{_sql_string(str(lake))}]",
        "SET temp_directory=''",
        "SET enable_external_access=false",
        "SET lock_configuration=true",
    ):
        con.execute(statement)


def _prepare_repo_table(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    """問いが repo を読むときだけ、record の cwd を repo へ解決した表を 1 度作る。

    git は DuckDB の中から呼べない (Python の関数を登録すると numpy が要り、取得する量が増える)。
    """
    if "lake_cwd_repo" not in sql or con.execute(
            "SELECT 1 FROM duckdb_tables() WHERE table_name = 'lake_cwd_repo'").fetchone():
        return
    cwds = [row[0] for row in con.execute("SELECT DISTINCT cwd FROM lake_record WHERE cwd <> ''").fetchall()]
    resolved = [_resolve_repo(cwd) for cwd in cwds]
    con.execute(
        "CREATE TEMP TABLE lake_cwd_repo AS "
        "SELECT unnest(?::VARCHAR[]) AS cwd, unnest(?::VARCHAR[]) AS repo, unnest(?::VARCHAR[]) AS unresolved_cause",
        [cwds, [r["repo"] for r in resolved], [r["cause"] for r in resolved]])


def _named_session(lake: Path, transcript: str) -> str:
    """名指しされた transcript の lake からの相対 path から `.jsonl` を外す。lake の外は読まない。"""
    path = Path(transcript).expanduser().resolve()
    if path.suffix != ".jsonl" or not path.is_relative_to(lake):
        raise Stopped("bad_argument", f"transcript は {lake} の下の .jsonl を渡してください")
    return str(path.relative_to(lake).with_suffix(""))


def open_lake(lake: Path, population: tuple[str, ...],
              transcript: str | None = None) -> tuple[duckdb.DuckDBPyConnection, list[dict], int]:
    """読み取り専用で固めた接続に view を載せる。母集団は接続ごとに決め、`transcript` はそれを 1 session に絞る。"""
    lake = lake.resolve()
    if not lake.is_dir():
        raise Stopped("lake_missing", f"{lake} に lake がありません。Claude Code を使った環境で呼んでください")
    named_session = None if transcript is None else _named_session(lake, transcript)
    entries, dirs = _walk_lake(lake)
    con = duckdb.connect(config={"memory_limit": MEMORY_LIMIT, "threads": str(THREADS)})
    _lock_down(con, lake)
    con.execute(
        "CREATE TEMP TABLE lake_entry AS "
        "SELECT unnest(?) AS path, unnest(?) AS rel, unnest(?) AS readable, unnest(?) AS size_bytes",
        [[e["path"] for e in entries], [e["rel"] for e in entries],
         [e["readable"] for e in entries], [e["size_bytes"] for e in entries]])
    con.execute("SET VARIABLE population = ?", [list(population)])
    con.execute("SET VARIABLE named_session = ?::VARCHAR", [named_session])
    con.execute("SET VARIABLE lake_root_length = ?", [len(str(lake))])
    con.execute("SET VARIABLE record_structure = ?", [record_structure(_load_snapshot()["record"])])
    try:
        con.execute(VIEWS_SQL.read_text(encoding="utf-8"))
    except duckdb.InvalidInputException as error:
        if "lake_empty" not in str(error):
            raise
        if named_session is None:
            raise Stopped("lake_empty", f"{lake} に母集団 {list(population)} の読める transcript がありません") from error
    if named_session is not None and con.execute(
            "SELECT 1 FROM lake_file WHERE readable AND transcript_kind = 'main' AND rel = ?",
            [named_session + ".jsonl"]).fetchone() is None:
        raise Stopped("bad_argument", f"{transcript} は lake の読める main の transcript ではありません")
    unreadable_dirs = con.execute("SELECT count(*) FROM unnest(?::VARCHAR[]) AS d(rel) WHERE lake_dir_in_population(rel)",
                                  [dirs]).fetchone()[0]
    return con, entries, unreadable_dirs


def _json_path(path: str) -> str:
    return "$." + path.replace("[]", "[*]")


def _window_condition(window: Window) -> str:
    bounds = []
    if window.since is not None:
        bounds.append(f"lake_line_epoch(line) >= {window.since}")
    if window.until is not None:
        bounds.append(f"lake_line_epoch(line) <= {window.until}")
    return " AND ".join(bounds) or "true"


# snapshot の型 → json_type が返す型
_JSON_TYPES = {"VARCHAR": ["VARCHAR"], "BIGINT": ["BIGINT", "UBIGINT"], "BOOLEAN": ["BOOLEAN"]}


def _path_appears(con: duckdb.DuckDBPyConnection, entry: dict, window: Window) -> bool:
    """窓の中に path が宣言の型で 1 件以上現れるか。path ごとに LIMIT 1 で打ち切る。

    全 path を 1 回の走査にまとめると、JSON の解釈の領域が式の数だけ積まれて memory の上限を超える。
    """
    path = entry["path"]
    json_path = _sql_string(_json_path(path))
    types = _JSON_TYPES.get(entry["type"])
    if "[]" in path:
        appears = (f"list_has_any(json_type(line, {json_path}), {types})" if types
                   else f"list_contains(json_exists(line, {json_path}), true)")
    else:
        appears = f"list_contains({types}, json_type(line, {json_path}))" if types else f"json_exists(line, {json_path})"
    leaf = path.replace("[]", "").rsplit(".", 1)[-1]
    return con.execute(
        "SELECT 1 FROM lake_read_lines(getvariable('snapshot_paths')) "
        f"WHERE CASE WHEN contains(line, {_sql_string(chr(34) + leaf + chr(34))}) AND lake_is_record(line) "
        f"THEN {appears} AND {_window_condition(window)} ELSE false END LIMIT 1").fetchone() is not None


def _meta_path_appears(con: duckdb.DuckDBPyConnection, entry: dict) -> bool:
    """subagent の meta に path が現れるか。meta が 1 つも無い lake では現れない。"""
    return con.execute(
        "SELECT 1 FROM lake_subagent_meta WHERE CASE WHEN json_valid(meta_text) "
        f"THEN json_exists(meta_text, {_sql_string(_json_path(entry['path']))}) ELSE false END LIMIT 1").fetchone() is not None


def check_snapshot(con: duckdb.DuckDBPyConnection, entries: list[dict], window: Window) -> list[str]:
    """schema snapshot の照合。必須の path が窓に無ければ止める。返すのは窓に無かった任意の path。

    `optional` の path は、その機能を使っていなければ現れないのが正常なので止めない。
    main の transcript を新しい順に読む。subagent の record も同じ形式で、読む量だけが数倍になる。
    transcript を名指す問いも lake 全体で照らす。1 件の transcript には使わなかった機能の path が現れない。
    """
    main = {row[0] for row in con.execute(
        "SELECT path FROM lake_entry WHERE readable AND lake_file_kind_of(rel) = 'main'").fetchall()}
    recent = sorted((e for e in entries if e["path"] in main and (window.since is None or e["mtime"] >= window.since)),
                    key=lambda e: e["mtime"], reverse=True)
    con.execute("SET VARIABLE snapshot_paths = ?", [[e["path"] for e in recent]])
    if not recent or con.execute(
            "SELECT 1 FROM lake_read_lines(getvariable('snapshot_paths')) "
            f"WHERE CASE WHEN lake_is_record(line) THEN {_window_condition(window)} ELSE false END LIMIT 1").fetchone() is None:
        raise Stopped("empty_window", "観測窓に record がありません。窓を広げて呼び直してください")
    snapshot = _load_snapshot()
    missing: list[str] = []
    absent_optional = [entry["path"] for entry in snapshot["subagent_meta"] if not _meta_path_appears(con, entry)]
    for entry in snapshot["record"]:
        if not _path_appears(con, entry, window):
            (absent_optional if entry.get("optional") else missing).append(entry["path"])
    if missing:
        raise Stopped("schema_mismatch", RECOVERY, missing_paths=missing, absent_optional_paths=absent_optional)
    return absent_optional


def _output_file(consumer: str, kind: str) -> Path:
    """0700 の出力 dir の下に、まだ無い file を用意する。共有の /tmp なので他人の dir には書かない。"""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", consumer):
        raise Stopped("bad_argument", "consumer は英小文字・数字・ハイフンで渡してください")
    out_dir = OUTPUT_ROOT / consumer
    try:
        out_dir.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = out_dir.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise Stopped("output_rejected", f"{out_dir} が自分の dir ではありません。別の consumer 名で呼んでください")
    out_dir.chmod(0o700)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return out_dir / f"{kind}-{stamp}.json"


def _write_result(consumer: str, kind: str, columns: list[str], rows: list[tuple],
                  contract: dict[str, Any] | None, meta: Mapping[str, Any] | None = None) -> Path:
    if len(set(columns)) != len(columns):
        raise Stopped("syntax", "列の名前が重なると値が失われます。AS で別の名前を付けて呼び直してください")
    path = _output_file(consumer, kind)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        # 呼び出し元は Read で頁を分けて読むので、1 行に潰さない
        body = {"contract": contract, **({"meta": meta} if meta else {}),
                "rows": [dict(zip(columns, row)) for row in rows]}
        json.dump(body, out, ensure_ascii=False, default=str, indent=2)
    return path


def _degradation(con: duckdb.DuckDBPyConnection, unreadable_dirs: int) -> dict:
    cursor = con.execute("SELECT * FROM lake_degradation")
    columns = [column[0] for column in cursor.description]
    return {**dict(zip(columns, cursor.fetchone())), "unreadable_dirs": unreadable_dirs}


def _stop_on_query_failure(error: duckdb.Error) -> Stopped:
    """失敗の種類と次にやることへ写す。DuckDB の文言を返すのは、問いの文字列と path しか引用しない種類だけ。

    Binder の文言は `read_csv` の header から作った列名を候補に出すので、lake の行を引用しうる。
    """
    if isinstance(error, duckdb.PermissionException):
        return Stopped("permission", "lake の外は読めず、どこにも書けません。結果は出力先に書かれるので、書き出しは要りません",
                       message=str(error))
    if isinstance(error, duckdb.ParserException):
        return Stopped("syntax", "SQL を直して呼び直してください", message=str(error))
    if isinstance(error, (duckdb.BinderException, duckdb.CatalogException)):
        return Stopped("syntax", "列と view の名前を views.sql で確かめて呼び直してください")
    if isinstance(error, duckdb.OutOfMemoryException):
        return Stopped("memory", "窓か列を絞って memory の上限に収めてください")
    return Stopped("query_failed", "SQL を見直して呼び直してください", error_type=type(error).__name__)


def _stop_on_named_query_failure(error: duckdb.Error) -> Stopped:
    """呼び出し元は named query の SQL を持たないので、直す先を plugin の保守者にする。"""
    if isinstance(error, duckdb.OutOfMemoryException):
        return _stop_on_query_failure(error)
    return Stopped("query_failed", "named query の実行に失敗しました。plugin の保守者へ報告してください",
                   error_type=type(error).__name__)


def _require_single_select(sql: str) -> None:
    """問いを 1 本の SELECT に限る。COPY と ATTACH は lake の中なら設定をすり抜ける。"""
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as error:
        raise _stop_on_query_failure(error) from error
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise Stopped("permission", "問いは 1 本の SELECT で渡してください。結果は出力先に書かれるので、書き出しは要りません")


def _snapshot_meta(con: duckdb.DuckDBPyConnection, entries: list[dict], window: Window) -> dict:
    """ad-hoc の問いの照合の結果。止めずに meta へ載せる — 形式の変化を黙った 0 件にしない。"""
    try:
        return {"missing_paths": [], "absent_optional_paths": check_snapshot(con, entries, window)}
    except Stopped as stopped:
        if stopped.kind == "schema_mismatch":
            return stopped.detail
        return {"snapshot_not_checked": stopped.kind}
    except duckdb.Error as error:
        return {"snapshot_not_checked": _stop_on_query_failure(error).kind}


def run_ad_hoc(lake: Path, sql: str, limit: int, consumer: str, window: Window = Window()) -> dict:
    """想定外の問い。view か lake の生の JSON への SELECT を、行数の上限まで実行する。

    窓は照合の範囲で、問いの絞り込みは SQL が持つ。全期間の照合は変化の前の record で通る。
    """
    if limit < 1:
        raise Stopped("bad_argument", "--limit は 1 以上で渡してください")
    _require_single_select(sql)
    con, entries, unreadable_dirs = open_lake(lake, ALL_POPULATION)
    try:
        _prepare_repo_table(con, sql)
        cursor = con.execute(sql)
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchmany(limit + 1)
        # 成果物は最後に書く。後の工程が止まると、path を返せない file が残る
        degradation = _degradation(con, unreadable_dirs)
        snapshot = _snapshot_meta(con, entries, window)
        path = _write_result(consumer, "query", columns, rows[:limit], None)
        return {"path": str(path), "rows": min(len(rows), limit), "truncated": len(rows) > limit,
                "degradation": degradation, **snapshot}
    except duckdb.Error as error:
        raise _stop_on_query_failure(error) from error


def _resolve_params(name: str, query: NamedQuery, given: Mapping[str, str]) -> dict[str, str]:
    """問いが定める条件を解決する。問いが持たない名前と、既定の無い条件の欠けは lake を読む前に止める。"""
    unknown = sorted(set(given) - set(query.params))
    if unknown:
        raise Stopped("bad_argument", f"{name} の --param は {sorted(query.params)} だけです。渡された名前: {unknown}")
    params = {**query.params, **given}
    lacking = sorted(key for key, value in params.items() if value is None)
    if lacking:
        raise Stopped("bad_argument", f"{name} は --param {lacking} が要ります")
    return params


def _run_sql(con: duckdb.DuckDBPyConnection, sql: str, window: Window,
             params: Mapping[str, str], binds: Mapping[str, Any]) -> tuple[list[str], list[tuple]]:
    """`$since` と `$until` と問いが定める条件を、SQL が読むものだけ束縛して実行する。"""
    _prepare_repo_table(con, sql)
    # 片側を開いた窓は無限大で束縛する (NULL で束縛すると比較が全行を落とす)
    bounds = {"since": -math.inf if window.since is None else window.since,
              "until": math.inf if window.until is None else window.until}
    values = {**bounds, **params, **binds}
    cursor = con.execute(sql, {key: value for key, value in values.items() if re.search(rf"\${key}\b", sql)})
    return [column[0] for column in cursor.description], cursor.fetchall()


def run_named_query(lake: Path, name: str, window: Window, consumer: str,
                    given: Mapping[str, str] | None = None) -> dict:
    """named query を 1 本走らせる。照合を通らなければ集計を返さない。"""
    query = NAMED_QUERIES.get(name)
    if query is None:
        raise Stopped("unknown_query", f"named query の一覧にありません: {sorted(NAMED_QUERIES)}")
    params = _resolve_params(name, query, given or {})
    transcript = params[query.named_transcript] if query.named_transcript else None
    con, entries, unreadable_dirs = open_lake(lake, query.population(params), transcript)
    meta: dict[str, Any] = {}
    try:
        absent_optional = check_snapshot(con, entries, window)
        if query.build is None:
            columns, rows = _run_sql(con, query.sql, window, params, {})
        else:
            built = query.build(QueryRun(con, window, params,
                                         lambda sql, binds: _run_sql(con, sql, window, params, binds),
                                         _resolve_repo, meta))
            columns = list(built[0]) if built else []
            rows = [tuple(row[column] for column in columns) for row in built]
        path = _write_result(consumer, name, columns, rows, query.contract, meta)
        return {"path": str(path), "rows": len(rows), "absent_optional_paths": absent_optional,
                "degradation": _degradation(con, unreadable_dirs), **({"meta": meta} if meta else {})}
    except duckdb.Error as error:
        raise _stop_on_named_query_failure(error) from error


def _epoch(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise Stopped("bad_argument", "観測窓は ISO 8601 で渡してください") from error
    return (moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)).timestamp()


def _param(text: str) -> tuple[str, str]:
    key, separator, value = text.partition("=")
    if not key or not separator:
        raise Stopped("bad_argument", "--param は <名前>=<値> で渡してください")
    return key, value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lake", type=Path, default=DEFAULT_LAKE)
    commands = parser.add_subparsers(dest="command", required=True)
    named = commands.add_parser("named")
    named.add_argument("name")
    named.add_argument("--consumer", required=True)
    named.add_argument("--since", help="観測窓の始まり (ISO 8601)")
    named.add_argument("--until", help="観測窓の終わり (ISO 8601)")
    named.add_argument("--param", action="append", default=[], help="問いが定める条件 (<名前>=<値>)。繰り返せる")
    ad_hoc = commands.add_parser("ad-hoc")
    ad_hoc.add_argument("--sql", required=True)
    ad_hoc.add_argument("--limit", type=int, required=True)
    ad_hoc.add_argument("--consumer", required=True)
    ad_hoc.add_argument("--since", help="形式の照合の窓の始まり (ISO 8601)")
    ad_hoc.add_argument("--until", help="形式の照合の窓の終わり (ISO 8601)")
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        window = Window(_epoch(args.since), _epoch(args.until))
        if args.command == "ad-hoc":
            result = run_ad_hoc(args.lake, args.sql, args.limit, args.consumer, window)
        else:
            result = run_named_query(args.lake, args.name, window, args.consumer,
                                     dict(_param(text) for text in args.param))
    except Stopped as stopped:
        print(json.dumps(stopped.as_output(), ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
