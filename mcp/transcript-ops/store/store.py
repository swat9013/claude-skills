"""store の置き場・接続・lifecycle (ADR 0031)。

**中心命題**: store は lake の純関数であり、authored data を 1 bit も含まない。
store を消しても結果は変わらない — 変わるのは所要時間だけ。ここから

- migration を書かない (schema 変更 = `STORE_VERSION` を上げて file 名を変え、
  drop & rebuild する)
- 破損したら捨てて作り直す (データ損失は原理的に起きない)

が導かれる。**`STORE_VERSION` の上げ忘れは人の注意力に頼らない** — schema.sql の
digest を store 側に持ち、不一致なら connect が drop & rebuild へ倒す
(`schema_digest`)。version は「別 file として共存させたい」ときの明示手段として残る。

store は手入力 prompt の原文を持つため、削除の痕跡も残さない
(`PRAGMA secure_delete`)。lake から消えた transcript の本文が cache file の
未使用領域に残る経路を塞ぐ。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

# schema.sql を変更したら上げる。上げると file 名が変わり、次回 sync が full
# rebuild になる (実測 20 秒台)。上げ忘れても `schema_digest` が拾って同じ
# rebuild へ倒すので、version は「旧 store を残したまま切り替えたい」ときの手段。
STORE_VERSION = 2

DEFAULT_CACHE_DIR = Path("~/.cache/claude-transcript-ops").expanduser()

# 既定の置き場を上書きする環境変数。既定 path が書けない環境 (sandbox / CI) でも
# 動かせるようにするための唯一の逃げ道で、暗黙の fallback は持たない
# (書けなければ例外を上げる)。
CACHE_DIR_ENV = "TRANSCRIPT_OPS_CACHE_DIR"

# store は手入力 prompt 由来の文字列を含みうるため 0700 で作る (共有 /tmp や
# 共有 home で world-readable にしない)。
CACHE_DIR_MODE = 0o700

# SQLite の write lock を待つ時間 (ミリ秒)。**full ingest の所要時間はここでは
# 賄わない** — 並行 sync の直列化は `lock.sync_lock` が担い、本値が効くのは
# lock を取った後に残る短い競合 (別 lake の観測との I/O 競合等) だけ。
BUSY_TIMEOUT_MS = 10_000

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

STORE_NAME_TEMPLATE = "store-v{version}-{digest}.sqlite3"

# store_meta の key。schema.sql の digest を持ち、connect が現物と突き合わせる。
SCHEMA_DIGEST_KEY = "schema_digest"

# spine (record) に対する projection。ts 欠損の可視化 (`ANOMALIES_SQL`) と
# file 再取得時の全置換 (`ingest._delete_projection_rows`) が同じ表を見る。
PROJECTION_TABLES = (
    "tool_use", "hook_firing", "user_prompt", "slash_invocation", "user_turn",
    "assistant_turn", "presented_name", "static_payload", "memory_injection",
    "compact_boundary",
)


def resolve_cache_dir(cache_dir: str | Path | None = None) -> Path:
    """store を置くディレクトリ。明示指定 > 環境変数 > 既定 の順で決める。"""
    if cache_dir is not None:
        return Path(cache_dir).expanduser()
    env = os.environ.get(CACHE_DIR_ENV)
    if env:
        return Path(env).expanduser()
    return DEFAULT_CACHE_DIR


def lake_digest(transcripts_dir: str | Path) -> str:
    """lake の絶対 path から store file 名に混ぜる短い digest。"""
    resolved = Path(transcripts_dir).expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:
        pass
    return hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]


def schema_digest() -> str:
    """`schema.sql` の意味的な内容の digest (コメント行 / 空行は勘定しない)。

    `STORE_VERSION` を上げ忘れたまま schema.sql を変えると、列追加なら INSERT が
    `OperationalError` で落ちるが、**table 追加は file の fingerprint が一致する
    ため再取得されず、新しい projection が黙って 0 件になる**。規律ではなく
    digest の突合で検出し、drop & rebuild へ倒す。

    コメントを digest から外すのは、注記の書き換えだけで全量 rebuild (20 秒台) を
    強いないため。
    """
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines()
                     if line.strip() and not line.lstrip().startswith("--"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def store_path(transcripts_dir: str | Path,
               cache_dir: str | Path | None = None) -> Path:
    """store file の path。

    file 名は **version + lake の digest**。version を持つのは migration を
    書かないため (schema 変更 = file 名の変更 + drop & rebuild)。lake の digest を
    持つのは、`transcripts_dir` が引数で切り替わるため — 1 つの store を別 lake で
    共有すると、sync の「lake から消えた file の row を消す」規則が互いの row を
    消し合う。**1 lake = 1 store** を file 名で構造的に保証する。
    """
    return resolve_cache_dir(cache_dir) / STORE_NAME_TEMPLATE.format(
        version=STORE_VERSION, digest=lake_digest(transcripts_dir))


def superseded_store_paths(transcripts_dir: str | Path,
                           cache_dir: str | Path | None = None) -> list[Path]:
    """同じ lake の、現行 version でない store file。

    `STORE_VERSION` を上げても旧 file は残る (実 lake の store は 545 MB)。
    version を上げるたびに cache へ積むので、次の connect で回収する。
    """
    directory = resolve_cache_dir(cache_dir)
    if not directory.is_dir():
        return []
    current = store_path(transcripts_dir, cache_dir)
    pattern = STORE_NAME_TEMPLATE.format(
        version="*", digest=lake_digest(transcripts_dir))
    return [path for path in sorted(directory.glob(pattern)) if path != current]


def connect(transcripts_dir: str | Path,
            cache_dir: str | Path | None = None) -> sqlite3.Connection:
    """store へ接続し、必要なら schema を作る。

    接続前に (1) 同じ lake の旧 version store を回収し、(2) schema.sql の digest が
    既存 store と食い違っていれば捨てる。どちらも「store は lake の純関数」だから
    できる後始末で、データ損失は原理的に起きない。

    journal_mode は WAL を試み、通らない環境 (WAL を信用できない NFS 等) は
    DELETE へ後退する。**後退したこと自体は `store_meta.journal_mode` に残す** —
    黙って別モードで動くと、並行 sync の挙動が説明できなくなる。
    """
    path = store_path(transcripts_dir, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=CACHE_DIR_MODE)
    for superseded in superseded_store_paths(transcripts_dir, cache_dir):
        _unlink_store_files(superseded)
    if _schema_drifted(path):
        _unlink_store_files(path)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    # 削除した row の内容を free page に残さない。lake から消えた transcript の
    # 手入力 prompt が store file のバイト列に残留する経路を塞ぐ (ADR 0031 の
    # cache invariant を物理層まで通す)
    conn.execute("PRAGMA secure_delete = ON")
    journal_mode = _set_journal_mode(conn)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    set_meta(conn, "store_version", str(STORE_VERSION))
    set_meta(conn, SCHEMA_DIGEST_KEY, schema_digest())
    set_meta(conn, "journal_mode", journal_mode)
    set_meta(conn, "transcripts_dir", str(Path(transcripts_dir).expanduser()))
    conn.commit()
    return conn


def _schema_drifted(path: Path) -> bool:
    """既存 store の schema が現在の `schema.sql` と食い違っているか。

    store がまだ無いのは drift ではない (これから作る)。**開けない store は drift
    扱い**にする — 破損の唯一の復旧手順が drop & rebuild なので、原因を分けても
    行き先が同じだから。
    """
    if not path.exists():
        return False
    try:
        probe = sqlite3.connect(path)
        try:
            row = probe.execute(
                "SELECT value FROM store_meta WHERE key = ?",
                (SCHEMA_DIGEST_KEY,)).fetchone()
        finally:
            probe.close()
    except sqlite3.DatabaseError:
        return True
    return row is None or str(row[0]) != schema_digest()


def _unlink_store_files(path: Path) -> None:
    """store 本体と併走 file (WAL / SHM) をまとめて消す。

    suffix を列挙せず prefix 一致で消すのは、併走 file の種類が journal_mode で
    変わるため (消し漏れると次の接続が別世代の WAL を掴む)。

    **sync lock は対象外** — 本関数は drop & rebuild の一部として lock を握ったまま
    呼ばれるので、lock file を消すと排他が失われる。`lock.lock_path` が store の
    prefix の外 (`<stem>.lock`) に置くことで、この glob に掛からないようにしてある。
    掃除の対象を広げるときは lock を巻き込まないか確かめる。
    """
    for candidate in sorted(path.parent.glob(path.name + "*")):
        candidate.unlink(missing_ok=True)


def _set_journal_mode(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute("PRAGMA journal_mode = WAL").fetchone()
    except sqlite3.DatabaseError:
        row = None
    mode = str(row[0]).lower() if row else ""
    if mode == "wal":
        return mode
    row = conn.execute("PRAGMA journal_mode = DELETE").fetchone()
    return str(row[0]).lower() if row else "unknown"


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO store_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM store_meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


# ts 欠損 record のうち、**mart が実際に読む projection を持つもの**だけを数える
# ための述語。窓 (`ts_epoch IS NULL OR ts_epoch >= cutoff`) を無条件で通過する行が
# ここに出る。spine にしか無い record type (last-prompt / mode 等、実測 65,907 件) は
# どの mart も読まないので数えない — 数えると常時 0 でない値が出て警告が鈍る。
_TS_MISSING_PREDICATE = "\n           OR ".join(
    f"EXISTS (SELECT 1 FROM {table} p "
    f"WHERE p.file_id = r.file_id AND p.line_no = r.line_no)"
    for table in PROJECTION_TABLES
)

# 観測の劣化シグナル。0 でない値が出ること自体が読み手への警告になる。
# **2 つ目の消費者 (prompts mart, #497) が出たため permissions mart から引き上げた**
# — mart ごとに SQL をコピーすると、同じ数字が mart によって違う書き方で出る事故が
# 起きうる。全 mart がここを 1 回呼ぶ。
ANOMALIES_SQL = f"""
SELECT
    (SELECT count(*) FROM file) AS ingested_files,
    (SELECT count(*) FROM record) AS records,
    (SELECT coalesce(sum(broken_lines), 0) FROM file) AS broken_lines,
    (SELECT count(*) FROM unreadable_file) AS unreadable_files,
    (SELECT count(*) FROM (SELECT record_uuid FROM record
                           WHERE record_uuid <> ''
                           GROUP BY record_uuid HAVING count(*) > 1))
        AS duplicate_record_uuid_groups,
    (SELECT count(*) FROM record r
      WHERE r.ts_epoch IS NULL
        AND ({_TS_MISSING_PREDICATE})) AS ts_missing_events
"""


def anomalies(conn: sqlite3.Connection) -> dict:
    """store 全体の観測劣化シグナル (file 単位の破損 / 未読 / 重複 uuid / ts 欠損)。"""
    return dict(conn.execute(ANOMALIES_SQL).fetchone())


def drop(transcripts_dir: str | Path,
         cache_dir: str | Path | None = None) -> None:
    """store を捨てる。次回 connect で作り直される (破損時の唯一の復旧手順)。"""
    _unlink_store_files(store_path(transcripts_dir, cache_dir))
