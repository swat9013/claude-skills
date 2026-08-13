"""overhead mart の純関数 (ADR 0031 の UDF 規律)。SQL からも Python からも呼ぶ。

**I/O・clock・store アクセスを持たない。** 入力は値だけ、出力は値だけ。

path 正規化 (`memory_key`) と token 概算 (`estimate_tokens_from_counts`) を
query 層の UDF に置くのは issue #498 の明示要求 (ADR 0031 の配置規則: 導出が
mart の観測契約に属すなら query 層)。`estimate_tokens_from_counts` の係数自体は
`adapter/transcript.py` が単一ソースを持つ (`scan-claude-md.py` との parity 制約) ので
ここでは re-export するだけで再実装しない。
"""

from __future__ import annotations

import re
import sqlite3

from adapter.transcript import estimate_tokens_from_counts

# harness が作る worktree の path 断片。同一 memory file が worktree ごとに別 path で
# 現れるため、注入実績を数える前に畳む (畳まないと 1 file の実績が worktree 数だけ
# 分散し、「めったに注入されない file」に見える)。
WORKTREE_SEGMENT_RE = re.compile(r"/\.claude/worktrees/[^/]+")


def normalize_memory_path(path: str) -> str:
    """worktree 断片を畳んだ memory file の path。

    `<repo>/.claude/worktrees/i478/.claude/rules/x.md` → `<repo>/.claude/rules/x.md`。
    worktree は harness が作る一時的な複製なので、同じ規範が別 file として散ると
    「注入実績が薄い file」に見えてしまう。
    """
    return WORKTREE_SEGMENT_RE.sub("", path)


def memory_key(path: str) -> str:
    """注入実績を数える単位 = **repo 相対の path**。

    絶対 path のままだと、同じ repo を 2 箇所に checkout している環境
    (relocate 前後 / ghq と作業用) で 1 file が 2 行に割れる。突合相手の
    `scan-claude-md.py` が出すのも repo 相対 path なので、ここで揃えておく。

    `.claude/` 配下はそこから、それ以外は末尾 2 セグメント (`docs/CLAUDE.md` と
    root の `CLAUDE.md` を潰さないため)。
    """
    normalized = normalize_memory_path(path)
    idx = normalized.rfind("/.claude/")
    if idx >= 0:
        return normalized[idx + 1:]
    return "/".join(normalized.rsplit("/", 2)[-2:])


# SQL 名 → (関数, 引数) の登録表。**query.sql が呼ぶ名前の単一ソース**。
REGISTERED = (
    ("memory_key", 1, memory_key),
    ("estimate_tokens_from_counts", 2, estimate_tokens_from_counts),
)


def register(conn: sqlite3.Connection) -> None:
    """接続に UDF を登録する。すべて deterministic (同じ入力に同じ答え)。"""
    for name, argument_count, function in REGISTERED:
        conn.create_function(name, argument_count, function, deterministic=True)
