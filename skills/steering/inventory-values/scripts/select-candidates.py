#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""inventory-values の候補絞り込み script (mart → 読み順が確定した slice)。

scan-user-prompts.py が出す mart は実測で 1,295 prompt / 934 KB あり、LLM に全件を
読ませる前提は成立しない。本 script は mart を**決定的に絞り込み・並べ替え**て、
LLM が上から順に読める slice JSON を出す。

**判定は一切しない** — 「どれがフィードバックか」「どれが価値観か」は 3 段階モデル
(docs/steering.md §1) どおり LLM の具体化と人間の判定に委ねる。ここで行うのは
長さ (`text_chars`) と `repo` という**機械的に観測できる属性だけによる絞り込みと
整列**であり、bucket も発話型も知らない。

**既定の入口は 121 字以上の帯** (`--min-chars`、default 121)。実測 1,295 件の分布で
60 字以下が 71% (919 件) を占め、その中身は `OK` / `全部` / `A` のような承認語
(単体では価値観を復元できない — 直前の提案とセットで初めて意味を持つ) と短い操作
指示だった。121 字以上は 139 件 (10.7%) で、設計方針・FB がこの帯に集中する。
長さは**絞り込みには使えるが判定には使えない** (301 字以上にはエラーログ貼り付けも
混じる) ため、閾値は入口の定義に留める。

**除外は silent にしない**: 閾値で落とした件数を帯 (band) 別ヒストグラムとして
meta に出す。`--limit` で打ち切った場合も `truncated_by_limit` に出す。slice の
meta をそのまま候補レポートに転記すれば「919 件を意図的に対象外にした」ことが
証跡として残る。

出力: --output-dir に candidates-<timestamp>.json を書き、path を stdout に print する。
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _transcript_lib import resolve_now  # noqa: E402

# --- 定数 --------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = Path("/tmp/inventory-values")

# 候補帯の下限 (字)。実測分布の 121-300 字帯の下端。根拠は module docstring と
# SKILL.md「2. 候補の絞り込み」。閾値を動かすときは両方を同時に更新する。
DEFAULT_MIN_CHARS = 121

# 帯の定義。(label, lower, upper)。upper が None なら上限なし。**この境界は
# issue #295 の実測表と一対一**で、候補レポートに転記して除外件数の根拠にする。
BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("1-10", 1, 10),
    ("11-60", 11, 60),
    ("61-120", 61, 120),
    ("121-300", 121, 300),
    ("301+", 301, None),
)

# slice の候補 record に載せる mart 側 field。cwd / project_dir / prompt_source /
# cli_version は読み手の判断材料にならないため落とす (slice を小さく保つ)。
CANDIDATE_FIELDS = (
    "session_id",
    "uuid",
    "timestamp",
    "repo",
    "git_branch",
    "text_chars",
    "truncated",
    "text",
)


# --- CLI ---------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mart", type=Path, required=True,
                   help="scan-user-prompts.py が出した mart JSON の path")
    p.add_argument("--min-chars", type=int, default=DEFAULT_MIN_CHARS,
                   help=f"候補に含める text_chars の下限。default {DEFAULT_MIN_CHARS}")
    p.add_argument("--repo", type=str, default=None,
                   help="repo で絞り込む (mart の repo field と完全一致)")
    p.add_argument("--limit", type=int, default=None,
                   help="出力件数の上限。打ち切りは meta に出る (silent cap しない)")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help="slice 出力先。default /tmp/inventory-values")
    p.add_argument("--now", type=str, default=None,
                   help="ISO timestamp。出力ファイル名の stamp を固定 (テスト用)")
    p.add_argument("--stdout-slice", action="store_true",
                   help="ファイルに書かず slice JSON を stdout に出す (テスト用)")
    return p.parse_args(argv)


# --- 集計 --------------------------------------------------------------------

def band_of(chars: int) -> str | None:
    """文字数が属する帯 label を返す。どの帯にも入らなければ None (0 字等)。"""
    for label, lower, upper in BANDS:
        if chars >= lower and (upper is None or chars <= upper):
            return label
    return None


def build_band_histogram(prompts: list[dict], min_chars: int) -> list[dict]:
    """帯別の件数。**絞り込み前**の母数に対して集計する (除外件数の根拠になる)。

    `in_scope` は閾値がその帯を丸ごと拾うかで、帯を跨ぐ閾値では下端で判定する
    (レポートに「この帯は入口の外」と書けるようにするためのラベル)。
    """
    counter: collections.Counter = collections.Counter()
    for prompt in prompts:
        label = band_of(int(prompt.get("text_chars") or 0))
        if label:
            counter[label] += 1
    return [
        {"band": label, "count": counter.get(label, 0), "in_scope": lower >= min_chars}
        for label, lower, _ in BANDS
    ]


def build_repo_index(candidates: list[dict]) -> list[dict]:
    """候補の repo 別内訳。`--repo` で次に絞り込むときの入口になる。"""
    grouped: dict[Any, list[dict]] = collections.defaultdict(list)
    for candidate in candidates:
        grouped[candidate.get("repo")].append(candidate)
    index = [
        {
            "repo": repo,
            "candidate_count": len(items),
            "session_count": len({item.get("session_id") for item in items}),
        }
        for repo, items in grouped.items()
    ]
    index.sort(key=lambda entry: (-entry["candidate_count"], str(entry["repo"])))
    return index


# --- 絞り込み ----------------------------------------------------------------

def sort_key(prompt: dict) -> tuple:
    """読み順の全順序キー。

    第 1 キーは `text_chars` 降順 — 長い側ほど設計方針・FB の密度が高く、`--limit`
    で打ち切っても密度の高い順に残る。第 2 キー以降 (timestamp / session_id / uuid)
    は同字数の並びを byte-stable にするためのもので、意味は持たない。
    """
    return (
        -int(prompt.get("text_chars") or 0),
        str(prompt.get("timestamp") or ""),
        str(prompt.get("session_id") or ""),
        str(prompt.get("uuid") or ""),
    )


def select(mart: dict, args: argparse.Namespace, generated_at: str) -> dict:
    """mart から候補 slice を組む (ファイル出力を伴わない、テスト用 entrypoint)。"""
    prompts = mart.get("prompts") or []

    below_min = 0
    other_repo = 0
    matched: list[dict] = []
    for prompt in prompts:
        if int(prompt.get("text_chars") or 0) < args.min_chars:
            below_min += 1
            continue
        if args.repo is not None and prompt.get("repo") != args.repo:
            other_repo += 1
            continue
        matched.append(prompt)

    matched.sort(key=sort_key)
    emitted = matched if args.limit is None else matched[: max(args.limit, 0)]

    candidates = [
        {"rank": rank, **{field: prompt.get(field) for field in CANDIDATE_FIELDS}}
        for rank, prompt in enumerate(emitted, start=1)
    ]

    mart_meta = mart.get("meta") or {}
    return {
        "meta": {
            "generated_at": generated_at,
            "mart_path": str(args.mart),
            "mart_generated_at": mart_meta.get("generated_at"),
            "mart_window_start": mart_meta.get("window_start"),
            "mart_window_end": mart_meta.get("window_end"),
            "min_chars": args.min_chars,
            "repo_filter": args.repo,
            "limit": args.limit,
            "total_prompts": len(prompts),
            "selected": len(matched),
            "emitted": len(candidates),
            "truncated_by_limit": len(matched) - len(candidates),
            "excluded": {
                "below_min_chars": below_min,
                "other_repo": other_repo,
            },
            "band_histogram": build_band_histogram(prompts, args.min_chars),
            "read_order": (
                "text_chars 降順 → timestamp → session_id → uuid (全順序・再現可能)"
            ),
        },
        "repos": build_repo_index(candidates),
        "candidates": candidates,
    }


# --- main --------------------------------------------------------------------

def load_mart(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    now: dt.datetime = resolve_now(args.now)

    try:
        mart = load_mart(args.mart)
    except (OSError, json.JSONDecodeError) as err:
        print(f"[!] mart を読めない: {args.mart} ({err})", file=sys.stderr)
        return 1

    slice_json = select(mart, args, now.isoformat().replace("+00:00", "Z"))
    if args.stdout_slice:
        json.dump(slice_json, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / f"candidates-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(
        json.dumps(slice_json, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(str(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
