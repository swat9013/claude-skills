"""transcript の on-disk 語彙 (ingest だけが知ってよい名前) の単一ソース。

ADR 0031 の format isolation は「**query 層 SQL に transcript の生 key 名が出たら
違反**」という機械検査で守る。その「生 key 名」の定義を本 module 1 箇所に置き、
gate script (`scripts/gate/verify-query-format-isolation.py`) が参照する。

deny 語彙は `ON_DISK_NAMES` から **store schema が宣言している識別子を引いた差**
として導出する (`forbidden_names`)。2 つを別々に手書きすると、store 側に列を足した
ときに「schema にあるのに gate が禁じる名前」が生まれて query が書けなくなる。
差の計算で構造的に排除する。

`GENERIC_NAMES` を deny から外すのは、これらが SQL の別名として自然に現れるうえ、
store に存在しない識別子を SQL が参照しても**実行時に落ちる** (on-disk データへは
到達しようがない) ため。gate が守るのは可読性と語彙の規律であって、到達可能性の
封じ込めは store schema そのものが担う。
"""

from __future__ import annotations

import re
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# ingest / adapter が読む on-disk の key 名・type 名・埋め込みタグ。
# 追加した知識は必ずここへ足す (足し忘れは gate の見逃しになる)。
ON_DISK_NAMES: frozenset[str] = frozenset({
    # record 共通の anchor
    "sessionId", "parentUuid", "isMeta", "isSidechain", "userType",
    "timestamp", "uuid",
    # tool 実行
    "toolUseResult", "toolDenialKind", "is_error", "tool_result",
    "userModified", "interrupted",
    # user record (手入力 prompt の判定材料)
    "promptSource", "origin", "isCompactSummary", "gitBranch", "version",
    # attachment 本体
    "attachment", "hookName", "hookEvent", "exitCode", "durationMs",
    "timedOut", "timeoutMs", "hookInfos",
    "hook_success", "hook_cancelled", "hook_additional_context",
    "hook_system_message",
    "nested_memory", "displayPath", "contentDiffersFromDisk", "globs",
    "skill_listing", "skillCount", "mcp_instructions_delta", "addedNames",
    "readdedNames", "removedNames", "agent_listing_delta", "addedTypes",
    "deferred_tools_delta", "addedLines", "addedBlocks",
    # system record
    "stop_hook_summary", "compact_boundary", "compactMetadata",
    "preTokens", "postTokens", "cumulativeDroppedTokens",
    # assistant の token 経済・帰属
    "input_tokens", "output_tokens",
    "cache_creation_input_tokens", "cache_read_input_tokens", "iterations",
    "attributionSkill",
    # tool_use の input key (Agent の識別引数)。"skill" は on-disk key でもあるが
    # mart 本文の一般語 (「skill を呼ぶ」等) と衝突するため deny に加えない —
    # GENERIC_NAMES と同じ理由 (docstring 参照)
    "subagent_type",
    # slash 展開 record の埋め込みタグ
    "command-name", "command-args", "command-message", "transcript-data",
    # lake の物理配置
    "jsonl",
})

# SQL の別名として自然に現れるため deny しない一般語 (docstring 参照)。
GENERIC_NAMES: frozenset[str] = frozenset({
    "id", "name", "type", "content", "input", "message", "role", "usage",
})

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


def schema_identifiers() -> frozenset[str]:
    """`schema.sql` が宣言している table 名 / column 名 / index 名の集合。"""
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    # コメント行を落としてから識別子を拾う (説明文中の生 key 名を拾わないため)
    body = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("--"))
    return frozenset(_IDENTIFIER_RE.findall(body))


def forbidden_names() -> frozenset[str]:
    """query 層 SQL に現れてはならない名前の集合。"""
    return ON_DISK_NAMES - GENERIC_NAMES - schema_identifiers()


def violations(sql_text: str) -> list[str]:
    """SQL 本文に現れた禁止語を出現順・重複なしで返す。

    照合は語境界つきの完全一致。`record_uuid` のような store 側の識別子が
    `uuid` に部分一致して偽陽性になるのを避ける。
    """
    forbidden = forbidden_names()
    found: list[str] = []
    for token in _IDENTIFIER_RE.findall(sql_text):
        if token in forbidden and token not in found:
            found.append(token)
    return found
