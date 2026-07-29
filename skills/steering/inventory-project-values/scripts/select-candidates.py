#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""inventory-project-values の候補絞り込み script (mart → 読み順が確定した slice)。

scan-user-prompts.py が出す mart は実測で 1,295 prompt / 934 KB あり、LLM に全件を
読ませる前提は成立しない。本 script は mart を**決定的に絞り込み・並べ替え**て、
LLM が上から順に読める slice JSON を出す。

**判定は一切しない** — 「どれがフィードバックか」「どれが価値観か」は 3 段階モデル
(docs/steering.md §1) どおり LLM の具体化と人間の判定に委ねる。ここで行うのは
長さ (`text_chars`) / `repo` / **正規形の完全一致**という機械的に観測できる属性
だけによる絞り込みと整列であり、bucket も発話型も知らない。

**既定の入口は 60 字以上の帯** (`--min-chars`、default 60)。単体では価値観を復元
できない承認語 (`OK` / `全部` / `A` — 直前の提案とセットで初めて意味を持つ) は
1-59 字帯に集中しており、60 字以上の発話は単体で意味が通る。長さは**絞り込みには
使えるが判定には使えない** (301 字以上にはエラーログ貼り付けも混じる) ため、閾値は
入口の定義に留める。

**定型文の除外は正規形の完全一致だけで行う** (`BOILERPLATE_MIN_GROUP` 件以上の群を
定型とみなす)。ハードコードしたパターン列も類似度計算も持たない — 定型文は正規化
(URL → path → 数値 → 空白畳み) 後にバイト一致するのに対し、価値観の再出現は表層語を
ほぼ共有しないため、両者は構造的に衝突しない。閾値は 3〜5 で結果が変わらない
(分布が二峰性) ため、チューニング余地を CLI に出さず定数に固定する。

**除外は silent にしない**: 閾値で落とした件数を帯 (band) 別ヒストグラムとして、
定型と判定した正規形を件数付きの `boilerplate_forms` として meta に出す。`--limit`
で打ち切った場合も `truncated_by_limit` に出す。定型一覧は「同じ文を何度もコピペ
している」= 未ルール化の強いシグナルでもあるため、**第 2 の候補源**として人間が
読み返せる形で残す (逐語反復された価値観が定型判定される経路が実在する)。

**repo scope**: `--repo` 未指定時は **cwd の git remote** に既定解決する (`--all-repos`
で全 project 横断に開ける)。他 project で実行したときに無関係な repo の prompt が
slice に載らないようにするための既定であり、解決できなければ黙って全 repo に
倒さず fail する。

出力: --output-dir に candidates-<timestamp>.json を書き、path を stdout に print する。
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _transcript_lib import resolve_now, resolve_repo_at, truncate  # noqa: E402

# --- 定数 --------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = Path("/tmp/inventory-values")

# 候補帯の下限 (字)。復元不能な承認語が集中する 1-59 字帯の直上。根拠は module
# docstring と SKILL.md「2. 候補の絞り込み」。閾値を動かすときは両方を同時に更新する。
DEFAULT_MIN_CHARS = 60

# 帯の定義。(label, lower, upper)。upper が None なら上限なし。境界は
# `DEFAULT_MIN_CHARS` と揃える (揃えないと `in_scope` が閾値を跨ぐ帯で嘘になる)。
BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("1-10", 1, 10),
    ("11-59", 11, 59),
    ("60-120", 60, 120),
    ("121-300", 121, 300),
    ("301+", 301, None),
)

# 定型判定の閾値。正規形が完全一致する群がこの件数以上なら定型とみなす。CLI には
# 出さない — 実測で 3〜5 のどこに置いても結果が変わらず (分布が二峰性)、可変にすると
# 恣意的なチューニング余地だけが増えるため。
BOILERPLATE_MIN_GROUP = 3

# 正規化の適用順 (この順序が仕様)。URL を先に潰さないと path / 数値パターンが URL の
# 内部を先に食い、同一 URL が別の正規形になる。path は**行頭または非単語文字の直後の
# `/` `~/`** だけを対象にする — 無制限の `\w+/\w+` にすると `A/B テスト` のような散文
# まで潰れ、異なる価値観の発話が同一正規形に化けて黙って候補から消える。
NORMALIZERS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"(?<!\w)~?/[\w.\-/]+"), "<path>"),
    (re.compile(r"\d+"), "<n>"),
    (re.compile(r"\s+"), " "),
)

# 定型一覧に載せる正規形・原文の表示上限 (字)。一覧は人間が読み返す第 2 の候補源
# なので、判別できる長さは残す。
FORM_PREVIEW_CHARS = 200

# `--repo` をどう決めたか。cwd 既定解決を暗黙の推定にしないため meta に出す。
REPO_SCOPE_EXPLICIT = "explicit"
REPO_SCOPE_CWD = "cwd"
REPO_SCOPE_ALL = "all"

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
                   help="repo で絞り込む (mart の repo field と完全一致)。"
                        "省略時は cwd の git remote に解決する")
    p.add_argument("--all-repos", action="store_true",
                   help="repo 絞り込みを外して全 project 横断で見る")
    p.add_argument("--limit", type=int, default=None,
                   help="出力件数の上限。打ち切りは meta に出る (silent cap しない)")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help="slice 出力先。default /tmp/inventory-values")
    p.add_argument("--now", type=str, default=None,
                   help="ISO timestamp。出力ファイル名の stamp を固定 (テスト用)")
    p.add_argument("--stdout-slice", action="store_true",
                   help="ファイルに書かず slice JSON を stdout に出す (テスト用)")
    args = p.parse_args(argv)
    if args.repo is not None:
        args.repo_scope = REPO_SCOPE_EXPLICIT
    elif args.all_repos:
        args.repo_scope = REPO_SCOPE_ALL
    else:
        # cwd 解決は git 呼び出しを伴うため main() が行う (select() は pure に保つ)。
        args.repo_scope = REPO_SCOPE_CWD
    return args


def resolve_repo_filter(
    args: argparse.Namespace,
    resolver: Callable[[Path], str | None] | None = None,
) -> str | None:
    """`--repo` 未指定時に cwd の repo 識別子を解決する (`main()` 専用)。

    解決に使うのは scan-user-prompts.py と同じ `_transcript_lib.resolve_repo_at`
    なので、mart の `repo` 値と表現が一致する。**解決できないときに全 repo へ倒さ
    ない** — 他 project の prompt を黙って slice に載せないことが `--repo` 既定化の
    目的そのものなので、明示を要求して fail する。
    """
    if args.repo_scope != REPO_SCOPE_CWD:
        return args.repo
    return (resolver or resolve_repo_at)(Path.cwd())


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


def normalize_text(text: str) -> str:
    """定型判定用の正規形。`NORMALIZERS` の順に潰す (順序は仕様)。"""
    for pattern, replacement in NORMALIZERS:
        text = pattern.sub(replacement, text)
    return text.strip()


def build_boilerplate_index(prompts: list[dict]) -> dict[str, list[dict]]:
    """`BOILERPLATE_MIN_GROUP` 件以上ある正規形 → その prompt 群。

    母数は **min_chars を通した全 repo の prompt**。repo で絞ってから数えると、
    repo をまたいで撒かれる定型 (issue 実行の起動文等) が小さい repo で閾値に届かず
    定型を素通しするため、検出は corpus 全体で行い、除外の帰属だけ repo scope 側で
    数える (`meta.excluded.boilerplate`)。
    """
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for prompt in prompts:
        grouped[normalize_text(str(prompt.get("text") or ""))].append(prompt)
    return {
        form: items for form, items in grouped.items()
        if len(items) >= BOILERPLATE_MIN_GROUP
    }


def build_boilerplate_forms(
    index: dict[str, list[dict]],
    excluded_counts: collections.Counter,
) -> list[dict]:
    """定型と判定した正規形の一覧 (件数付き)。**人間が拾い戻すための第 2 の候補源**。

    逐語反復された価値観が定型判定される経路が実在するため、silent に落とさず
    件数・repo 数・実例 anchor を添えてレポートへ転記できる形で出す。
    """
    forms = [
        {
            "normalized": truncate(form, FORM_PREVIEW_CHARS),
            "count": len(items),
            "excluded_from_slice": excluded_counts.get(form, 0),
            "repo_count": len({item.get("repo") for item in items}),
            "sample": {
                "session_id": items[0].get("session_id"),
                "timestamp": items[0].get("timestamp"),
                "repo": items[0].get("repo"),
                "text": truncate(str(items[0].get("text") or ""), FORM_PREVIEW_CHARS),
            },
        }
        for form, items in index.items()
    ]
    forms.sort(key=lambda entry: (-entry["count"], entry["normalized"]))
    return forms


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

    in_band = [
        prompt for prompt in prompts
        if int(prompt.get("text_chars") or 0) >= args.min_chars
    ]
    boilerplate_index = build_boilerplate_index(in_band)

    below_min = 0
    other_repo = 0
    boilerplate = 0
    boilerplate_excluded: collections.Counter = collections.Counter()
    matched: list[dict] = []
    # 除外理由は **below_min_chars → other_repo → boilerplate** の順に確定させる
    # (record ごとに 1 理由。scan-user-prompts.py の EXCLUSION_REASONS と同じ規約)。
    for prompt in prompts:
        if int(prompt.get("text_chars") or 0) < args.min_chars:
            below_min += 1
            continue
        if args.repo is not None and prompt.get("repo") != args.repo:
            other_repo += 1
            continue
        form = normalize_text(str(prompt.get("text") or ""))
        if form in boilerplate_index:
            boilerplate += 1
            boilerplate_excluded[form] += 1
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
            "repo_scope": args.repo_scope,
            "limit": args.limit,
            "total_prompts": len(prompts),
            "selected": len(matched),
            "emitted": len(candidates),
            "truncated_by_limit": len(matched) - len(candidates),
            "excluded": {
                "below_min_chars": below_min,
                "other_repo": other_repo,
                "boilerplate": boilerplate,
            },
            "band_histogram": build_band_histogram(prompts, args.min_chars),
            "boilerplate": {
                "min_group": BOILERPLATE_MIN_GROUP,
                "detection_scope": "min_chars を通した全 repo の prompt",
                "detected_forms": len(boilerplate_index),
                "detected_prompts": sum(len(v) for v in boilerplate_index.values()),
            },
            "read_order": (
                "text_chars 降順 → timestamp → session_id → uuid (全順序・再現可能)"
            ),
        },
        "boilerplate_forms": build_boilerplate_forms(
            boilerplate_index, boilerplate_excluded
        ),
        "repos": build_repo_index(candidates),
        "candidates": candidates,
    }


# --- main --------------------------------------------------------------------

def load_mart(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    now: dt.datetime = resolve_now(args.now)

    args.repo = resolve_repo_filter(args)
    if args.repo_scope == REPO_SCOPE_CWD and args.repo is None:
        print(
            f"[!] cwd から repo を解決できない: {Path.cwd()}\n"
            "    他 repo の prompt を黙って載せないため、"
            "`--repo <repo>` か `--all-repos` を明示する",
            file=sys.stderr,
        )
        return 1

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
