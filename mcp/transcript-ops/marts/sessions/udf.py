"""sessions slice の純関数 (ADR 0031 の UDF 規律)。

**I/O・clock・store アクセスを持たない。** deny 文言の fallback 判定は permissions
mart の述語をそのまま登録する — 同じ tool_result に 2 つの mart が別の deny 判定を
返すと、#476 (2 scanner の outcome 判定が割れた) と同型の矛盾になるため再実装しない。
"""

from __future__ import annotations

import sqlite3

from marts.permissions.udf import (
    looks_like_automode_denial,
    looks_like_permission_denial,
)

# SQL 名 → (関数, 引数) の登録表。**query.sql が呼ぶ名前の単一ソース**。
REGISTERED = (
    ("looks_like_permission_denial", 1, looks_like_permission_denial),
    ("looks_like_automode_denial", 1, looks_like_automode_denial),
)


def register(conn: sqlite3.Connection) -> None:
    """接続に UDF を登録する。すべて deterministic (同じ入力に同じ答え)。"""
    for name, argument_count, function in REGISTERED:
        conn.create_function(name, argument_count, function, deterministic=True)
