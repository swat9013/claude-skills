"""named query の登録の形。`named_queries/<名前>.py` が `QUERY` にこの型の値を置く。

問いは `transcript_query.py` が file 名から集めるので、問いを足す差分は 1 file に閉じる。
"""

from __future__ import annotations

import dataclasses
import functools
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

RECOVERY = ("transcript の形式が変わっています。この skill の views.sql と schema-snapshot.json の修正が要るので、"
            "plugin の保守者へ missing_paths を報告してください")
MAIN_POPULATION = ("main",)
ALL_POPULATION = ("main", "subagent")
GIT_TIMEOUT_SEC = 5


class Stopped(Exception):
    """答えを返さずに止まる。`kind` は失敗の種類、`next_action` は次にやること。"""

    def __init__(self, kind: str, next_action: str, **detail: Any) -> None:
        super().__init__(kind)
        self.kind = kind
        self.next_action = next_action
        self.detail = detail

    def as_output(self) -> dict:
        return {"stopped": self.kind, "next_action": self.next_action, **self.detail}


@dataclasses.dataclass(frozen=True)
class Window:
    """観測窓 (unix 秒)。None は片側を開く。"""

    since: float | None = None
    until: float | None = None


@dataclasses.dataclass(frozen=True)
class QueryRun:
    """`build` が受け取る 1 回の実行。

    `select` は `NamedQuery.sql` と同じ束縛で SELECT を走らせる。`resolve_repo` は実在する dir 1 つの
    `repo` と、解決できないときの `cause` を返す。
    `meta` は行に載らない件数の置き場で、`build` が書くと成果物と返り値に同じ値が載る。
    """

    con: Any
    window: Window
    params: Mapping[str, str]
    _execute: Callable[[str, Mapping[str, Any]], tuple[list[str], list[tuple]]]
    resolve_repo: Callable[[str], Mapping[str, str | None]] | None = None
    meta: dict[str, Any] = dataclasses.field(default_factory=dict)

    def select(self, sql: str, **binds: Any) -> list[dict]:
        columns, rows = self._execute(sql, binds)
        return [dict(zip(columns, row)) for row in rows]


@functools.cache
def resolve_repo(cwd: str) -> dict[str, str | None]:
    """cwd から repo の表現を引く。実在しない cwd は祖先の repo に寄せない。"""
    if not os.path.isdir(cwd):
        return {"repo": None, "cause": "cwd_missing"}

    def git(*argv: str) -> tuple[str, str | None]:
        try:
            proc = subprocess.run(["git", "-C", cwd, *argv], capture_output=True, text=True,
                                  timeout=GIT_TIMEOUT_SEC, check=False)
        except (OSError, subprocess.SubprocessError):
            return "", "git_unavailable"
        return (proc.stdout.strip(), None) if proc.returncode == 0 else ("", "not_a_repo")

    url, _ = git("remote", "get-url", "origin")
    if url:
        # URL に埋めた認証情報を成果物へ出さない。password は `@` や `/` を含みうるので、最後の `@` まで落とす
        return {"repo": re.sub(r"://.*@", "://", url.splitlines()[0].strip()), "cause": None}
    common_dir, cause = git("rev-parse", "--git-common-dir")
    if not common_dir:
        return {"repo": None, "cause": cause}
    return {"repo": str((Path(cwd) / common_dir).resolve().parent), "cause": None}


def main_population(_params: Mapping[str, str]) -> tuple[str, ...]:
    return MAIN_POPULATION


@dataclasses.dataclass(frozen=True)
class NamedQuery:
    """呼び出し元が名前で呼ぶ観測の問い。

    `sql` は view への SELECT で、`$since`・`$until`・`params` の名前を `$<名前>` で取れる。
    declared state を読むなど SELECT 1 本で答えが出ない問いは、`sql` の代わりに `build` を持つ。
    `build` は行の dict の list を返し、全行が同じ key を持つ。
    `contract` は成果物の読み方の注記 (data contract) で、成果物と一緒に書く。
    `params` は問いが定める条件の名前と既定値で、既定値が None の条件は必須にする。
    `population` は条件から母集団を決める。母集団を選ばせる問いだけが差し替える (設計規則 (10))。
    `named_transcript` は transcript の path を受ける条件の名前で、読む範囲をその main の file と
    subagent の file に絞る。劣化の件数も同じ範囲で数え、schema snapshot の照合だけは lake 全体で行う。
    """

    sql: str = ""
    contract: dict[str, Any] = dataclasses.field(default_factory=dict)
    params: Mapping[str, str | None] = dataclasses.field(default_factory=dict)
    population: Callable[[Mapping[str, str]], tuple[str, ...]] = main_population
    build: Callable[[QueryRun], list[dict]] | None = None
    named_transcript: str | None = None
