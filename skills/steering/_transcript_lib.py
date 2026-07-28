"""steering scanner の transcript-walk 共通処理 (pure module)。

inventory-permissions (scan-permissions.py) と inventory-skill-mcp
(scan-invocations.py) に逐語コピーされていた transcript 走査系ユーティリティを
集約する。両 scanner は PEP 723 単一ファイル (deps=[], uv run) 制約下にあるため、
`sys.path.insert(0, Path(__file__).resolve().parents[2])` +
`from _transcript_lib import ...` の形で読み込む。

**挙動変更ゼロ** — 定義は移設元と完全一致 (walk_transcripts は docstring 付きの
scan-permissions.py 版を採用)。共有スコープは以下 5 定義に限定し (#253 決定)、
出力規約 helper・extract 層は各 scanner 側に残す。underscore 始まりのファイル名は
「直接実行しない pure module」を表す (uv run の entry point ではない)。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Iterable

# user-reject の best-effort 判定文言。false positive は避けるので content 文字列を
# includes で軽く見る程度。#29499 の限界 (別種の user-reject を拾えない / 別種の
# error 文言を user-reject に誤検知する余地あり) は各 scanner の SKILL.md 側で注記する。
USER_REJECT_PATTERNS = (
    "the user doesn't want",
    "user rejected the",
    "request interrupted by user",
    "tool call was rejected",
)


def resolve_now(now_str: str | None) -> dt.datetime:
    if now_str:
        raw = now_str.replace("Z", "+00:00")
        return dt.datetime.fromisoformat(raw).astimezone(dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc)


def truncate(s: str | None, limit: int) -> str:
    if not s:
        return ""
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[:limit]


def _iter_jsonl(fp) -> Iterable[dict]:
    for line in fp:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            yield rec


def walk_transcripts(
    transcripts_dir: Path,
    cutoff: dt.datetime,
) -> Iterable[tuple[str, Path]]:
    """transcript file を (project_dir_name, path) で yield。

    lake の dir 名エンコードが lossy なため directory 単位のスコープ絞りは
    行わない。scope は event.cwd で filter する (_filter_events_for_project)。
    mtime による cutoff filter のみ適用。
    """
    if not transcripts_dir.is_dir():
        return
    cutoff_ts = cutoff.timestamp()
    for project_dir in sorted(transcripts_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        for jsonl in sorted(project_dir.glob("*.jsonl")):
            try:
                mtime = jsonl.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff_ts:
                continue
            yield project_dir.name, jsonl
