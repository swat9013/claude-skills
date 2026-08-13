"""mart 層 (store への query として観測契約を表現する)。

mart 1 つ = 1 ディレクトリ。中身の役割は固定する:

| file | 役割 |
|---|---|
| `query.sql` | 関係代数。**transcript の生 key 名を書かない** (gate が検査) |
| `udf.py` | SQL から呼ぶ純関数。**I/O・clock・store アクセス禁止** |
| `settings.py` | 設定側の分母 (窓を持たない「現在の状態」なので store に入れない) |
| `contract.py` | 出力の機械可読 contract (mart schema 知識の単一ソース) |
| `present.py` | 組み立てと emit。**唯一 I/O を持つ層** |
"""

from __future__ import annotations

import re
from pathlib import Path

# `query.sql` を名前付き文へ分割する marker。`-- name: <識別子>` の次行から
# 次の marker までが 1 文。1 file 1 クエリにすると co-location が崩れるため、
# mart の SQL は 1 file にまとめて名前で引く。
_NAME_RE = re.compile(r"^--\s*name:\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")


def load_statements(path: Path) -> dict[str, str]:
    """`-- name:` marker で区切られた SQL を {名前: 本文} で返す。"""
    statements: dict[str, list[str]] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _NAME_RE.match(line)
        if match:
            current = match.group(1)
            statements[current] = []
            continue
        if current is not None:
            statements[current].append(line)
    return {name: "\n".join(body).strip() for name, body in statements.items()}
