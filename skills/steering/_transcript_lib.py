"""steering scanner の transcript-walk 共通処理 (pure module)。

inventory-permissions (scan-permissions.py) と inventory-skill-mcp
(scan-invocations.py) に逐語コピーされていた transcript 走査系ユーティリティを
集約する。両 scanner は PEP 723 単一ファイル (deps=[], uv run) 制約下にあるため、
`sys.path.insert(0, Path(__file__).resolve().parents[2])` +
`from _transcript_lib import ...` の形で読み込む。

共有スコープは transcript-walk 系 5 定義 (#253 決定) + **repo 識別子の解決** (#315)。
出力規約 helper・extract 層は各 scanner 側に残す。underscore 始まりのファイル名は
「直接実行しない pure module」を表す (uv run の entry point ではない)。

repo 解決 (`resolve_repo_at`) を共有するのは、mart を書く scan-user-prompts.py と
mart を repo で絞る select-candidates.py が**同一の repo 表現**を出す必要があるため
(表現がずれると絞り込みが黙って 0 件になる)。一致をテストの assertion ではなく
呼び先の単一性で構造的に保証する。祖先遡り (消えた worktree の救済) は
scan-user-prompts.py 側の観測契約なので昇格させない。
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path
from typing import Iterable

# git 解決の上限 (秒)。解決不能でも観測は続けるため失敗は空文字に潰す。
GIT_TIMEOUT_SEC = 5

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


def git_output(cwd: Path, argv: list[str]) -> str:
    """`git -C <cwd> <argv>` の stdout。失敗・timeout・git 不在は空文字に潰す。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *argv],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout


def resolve_repo_at(dir_path: Path) -> str | None:
    """実在するディレクトリ 1 つに対して git の repo 識別子を返す。

    解決手順は origin remote URL → git-common-dir の親の順。worktree からでも
    git-common-dir が親 repo に寄るため、同一 repo の worktree は同じ識別子になる。
    """
    url = git_output(dir_path, ["remote", "get-url", "origin"]).strip()
    if url:
        return url.splitlines()[0].strip()
    common_dir = git_output(dir_path, ["rev-parse", "--git-common-dir"]).strip()
    if not common_dir:
        return None
    common_path = Path(common_dir)
    if not common_path.is_absolute():
        common_path = dir_path / common_path
    try:
        return str(common_path.resolve().parent)
    except OSError:
        return None


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
