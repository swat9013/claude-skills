"""store の sync を直列化する file lock (ADR 0031)。

全 tool が呼び出しのたびに差分 sync を前置するため、store が無い状態 (初回 /
`STORE_VERSION` を上げた直後 / schema drift 検出後) では複数プロセスが同時に
full ingest を始める。**SQLite の WAL が分離するのは reader と writer であって
writer 同士は排他**なので、後続は `busy_timeout` の間だけ待って
`database is locked` で落ちる (実測: 実 lake 2,870 file の full ingest 22.5 秒に
対し busy_timeout 10 秒)。

sync 全体をこの lock で直列化し、後続は先行の完了を待ってから差分 sync に入る
(実測 34 ms)。**待ち切れなかったら黙って諦めず例外を上げる** — 観測が silent zero
へ劣化するより、呼び出し側に失敗が見える方がよい。

lock は store file と 1 対 1 で、lake ごとに分かれる (`store_path` が lake digest を
file 名に持つため)。別 lake の観測は互いを待たない。
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import time
from pathlib import Path
from typing import Iterator

from . import store as store_mod

# 先行 sync の完了を待つ上限 (秒)。実測の full ingest (実 lake 2,870 file /
# 1.4 GB で 22.5 秒) に対し、lake が一桁育っても待ち切れる幅を取る。
DEFAULT_TIMEOUT_SEC = 300.0

# lock 獲得の再試行間隔 (秒)。
POLL_INTERVAL_SEC = 0.05

LOCK_SUFFIX = ".lock"


class SyncLockTimeout(RuntimeError):
    """先行する sync が上限内に終わらなかった。"""


def lock_path(store_file: Path) -> Path:
    """store と対になる lock file。

    **store file 名の前方一致にしない** (`<store>.sync.lock` にしない)。store の
    回収 (`store._unlink_store_files`) は prefix 一致で併走 file を消すので、前方一致
    だと **lock を握ったまま自分の lock file を消す**ことになる。unlink 済み inode を
    握った側は排他を失い、後続は新しい inode を作って即座に入る — schema drift の
    drop & rebuild は lock の内側で走るため、version を上げた直後 (全 session が
    cold start に戻る、lock を足した当の場面) に排他が消える。
    """
    return store_file.with_suffix(LOCK_SUFFIX)


@contextlib.contextmanager
def sync_lock(store_file: Path,
              timeout_sec: float | None = None) -> Iterator[Path]:
    """store 1 つにつき 1 プロセスだけが sync に入れるようにする。

    `timeout_sec` は待ち上限の注入点 (省略時は `DEFAULT_TIMEOUT_SEC`)。
    """
    limit = DEFAULT_TIMEOUT_SEC if timeout_sec is None else timeout_sec
    path = lock_path(store_file)
    path.parent.mkdir(parents=True, exist_ok=True,
                      mode=store_mod.CACHE_DIR_MODE)
    # lock file 自体も手入力 prompt を含む store と同じ dir に置くので 0600
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        # 文言が指すのは lock file ではなく store — 読み手が知りたいのは
        # 「どの lake の観測が待たされたか」なので
        _acquire(fd, store_file, limit)
        try:
            yield path
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _acquire(fd: int, store_file: Path, timeout_sec: float) -> None:
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise SyncLockTimeout(
                    f"先行する store の sync が {timeout_sec:g} 秒以内に終わらない: "
                    f"{store_file}。別プロセスの full ingest が続いているか、lock が "
                    f"取り残されている"
                ) from None
            time.sleep(POLL_INTERVAL_SEC)
