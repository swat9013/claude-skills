#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""inventory-project-values の観測 script (data mart 生成)。

transcript (~/.claude/projects/<cwd-hash>/<sid>.jsonl) を data lake として、直近
N 日 (--days、default 30) の **ユーザーが手入力したプロンプト**だけを抽出し、
session_id / repo / timestamp の証拠 anchor 付きの生 mart JSON を出力する。

script の責務は決定的な抽出まで — **「どれがフィードバックか」「どれが価値観か」の
判定は一切行わない**。bucket を知らない生データだけを出し、判断は棚卸し実行時の
人間、文章の具体化のみ LLM が担う (docs/steering.md §1 の 3 段階モデル)。

**採用条件は 2 系統の gate の AND** で、除外は record ごとに 1 理由へ確定させる
(EXCLUSION_REASONS の順に評価)。集計 `excluded` と採用件数の和は走査した user
record 総数に一致する (totality 不変条件、テストで固定)。

- content gate: tool_result / slash command 展開 / `#rule ` 捕捉対象を弾く
- provenance gate: `promptSource` が typed|queued、`origin.kind` が human、
  isMeta / isSidechain / isCompactSummary がいずれも立っていないこと

`promptSource` は Claude Code 2.1.196 以降の user record に付く。付かない record は
`no_prompt_source` として除外し件数を meta に出すので、CLI 側の schema 変更で観測が
劣化したときは mart 上で顕在化する (silent zero にはならない)。

出力: --output-dir に mart-<timestamp>.json を書き、path を stdout に print する。
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _transcript_lib import (  # noqa: E402
    _iter_jsonl,
    resolve_now,
    truncate,
    walk_transcripts,
)
from _transcript_lib import resolve_repo_at as _resolve_repo_at  # noqa: E402

# --- 定数 --------------------------------------------------------------------

DEFAULT_DAYS = 30
DEFAULT_TRANSCRIPTS_DIR = Path("~/.claude/projects").expanduser()
DEFAULT_OUTPUT_DIR = Path("/tmp/inventory-values")

# 1 prompt あたりの保存上限。実測 p99 ≈ 2.8k 字で、超過分はエラーログ等の貼り付けが
# 大半のため後段の価値観抽出に効かない。切れたことは truncated flag で残す。
DEFAULT_TEXT_LIMIT = 4000

# 人間が UI に入力した経路を表す promptSource の値。sdk (subagent) / system
# (task-notification / peer) はここに入らない。
HUMAN_PROMPT_SOURCES = frozenset({"typed", "queued"})

# origin.kind が human 以外 (system / task-notification / peer) なら非手入力。
# origin 自体が無い record は判定材料なしとして通す (promptSource 側で担保済み)。
HUMAN_ORIGIN_KIND = "human"

# `#rule ` 運用メモの判定 prefix。A-strict = 非空行が 1 行以上あり、その全てが
# `#rule ` (末尾スペース込み・行頭一致) で始まる prompt のみ該当し、通常行との
# 混在 prompt は手入力として残す。
#
# 捕捉 hook 自体は ADR 0018 で撤去済み (`#rule ` prompt は今後 block されず
# transcript に残る) が、除外は**意図的に維持する** — 撤去前後で mart の観測帯を
# 揺らさないため。観測帯の拡張は別 issue の領分であり、本 gate の削除はそこで
# 判断する。仕様正本は本コメント (参照先 doc / script は現存しない)。
RULE_PREFIX = "#rule "

# slash command 呼出しは `<command-message>` / `<command-name>` で**始まる**展開
# record として transcript に残る。人間の手入力そのものではないため除外する。
# 部分一致にしないのは、これらの tag を本文中に引用しただけの手入力 prompt を
# 巻き込まないため (同種の false positive は scan-invocations.py が実 transcript で
# 観測済み)。`<command-args>` の有無は条件にしない — 引数なし呼出しには付かない。
SLASH_COMMAND_TAGS = ("<command-name>", "<command-message>")

# 採用を表す reason。excluded 集計と同じ Counter に載せて totality を保つ。
ACCEPTED = "accepted"

# 除外理由。**この順で評価する** (record ごとに 1 理由へ確定させるため順序が仕様)。
EXCLUSION_REASONS = (
    "tool_result",              # content が tool_result の運搬
    "slash_command_expansion",  # slash command の展開 record
    "rule_capture",             # `#rule ` A-strict 捕捉対象
    "compact_summary",          # context 圧縮の要約注入
    "meta_injected",            # isMeta (skill 本文注入 / SDK observer 等)
    "sidechain",                # subagent 側の prompt
    "no_prompt_source",         # promptSource 欠落 (旧 CLI / システム生成)
    "non_human_prompt_source",  # promptSource が sdk / system
    "non_human_origin",         # origin.kind が human 以外
    "empty_text",               # 抽出結果が空
)

# repo をどこから解決したか。cwd 直接か、消えた worktree の実在祖先経由か。
REPO_SOURCE_CWD = "cwd"
REPO_SOURCE_ANCESTOR = "ancestor"
REPO_SOURCES = (REPO_SOURCE_CWD, REPO_SOURCE_ANCESTOR)


# --- データ ------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Classification:
    """user record 1 件の判定結果。reason == ACCEPTED のときのみ text が有効。"""

    reason: str
    text: str


@dataclasses.dataclass
class Prompt:
    session_id: str
    uuid: str
    timestamp: str
    cwd: str
    repo: str | None
    repo_source: str | None
    project_dir: str
    git_branch: str
    prompt_source: str
    cli_version: str
    text: str
    text_chars: int
    truncated: bool


# --- CLI ---------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=DEFAULT_DAYS,
                   help="観測窓 (日)。default 30")
    p.add_argument("--transcripts-dir", type=Path, default=DEFAULT_TRANSCRIPTS_DIR,
                   help="transcript の data lake。default ~/.claude/projects")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help="mart 出力先。default /tmp/inventory-values")
    p.add_argument("--now", type=str, default=None,
                   help="ISO timestamp。窓の起点を固定 (テスト・再現用)")
    p.add_argument("--text-limit", type=int, default=DEFAULT_TEXT_LIMIT,
                   help="1 prompt あたりの保存文字数上限。default 4000")
    p.add_argument("--stdout-mart", action="store_true",
                   help="ファイルに書かず mart JSON を stdout に出す (テスト用)")
    return p.parse_args(argv)


# --- 窓判定 ------------------------------------------------------------------

def within_window(ts_str: str, cutoff: dt.datetime) -> bool:
    """record の timestamp が観測窓内か。

    walk_transcripts の mtime filter だけでは長寿命 session の古い record を
    落とせないため、record 単位でも判定する。timestamp 欠損・不正は保守的に
    窓内扱いにする (inventory-skill-mcp の `_within_window` と同挙動)。

    **`_transcript_lib` へは昇格させない** (#295 で判断)。窓の解釈 (欠損 record を
    どちらに倒すか) は各 scanner の観測契約に属し、共有すると片方の契約変更が
    もう片方の mart を黙って変える。extract 層を共有しない判断と同じ線
    ([ADR 0013](../../../docs/adr/0013-intra-subsystem-implementation-sharing.md))。
    """
    try:
        rec_ts = dt.datetime.fromisoformat(
            ts_str.replace("Z", "+00:00")
        ).astimezone(dt.timezone.utc)
    except (ValueError, AttributeError):
        return True
    return rec_ts >= cutoff


# --- 判定 --------------------------------------------------------------------

def collect_text(content: Any) -> tuple[str, bool]:
    """user turn の message.content から (text, tool_result を含むか) を返す。

    typed prompt でも画像添付時は content が list になるため、str / list の
    双方を扱う。list の場合は text block のみを改行連結する。
    """
    if isinstance(content, str):
        return content, False
    if isinstance(content, list):
        parts: list[str] = []
        has_tool_result = False
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "tool_result":
                has_tool_result = True
            elif block_type == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts), has_tool_result
    return "", False


def is_rule_capture_prompt(text: str) -> bool:
    """A-strict 判定で `#rule ` 運用メモかを返す。

    非空行が 1 行以上あり、その全てが `#rule ` (末尾スペース込み・行頭一致) で
    始まる場合のみ True。混在 prompt は通常の手入力として残す (RULE_PREFIX 参照)。
    """
    total = 0
    matched = 0
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        total += 1
        if line.startswith(RULE_PREFIX):
            matched += 1
    return total > 0 and matched == total


def classify_user_record(rec: dict) -> Classification:
    """user record 1 件を採用 / 除外理由へ確定させる (EXCLUSION_REASONS の順)。"""
    text, has_tool_result = collect_text((rec.get("message") or {}).get("content"))

    if has_tool_result:
        return Classification("tool_result", "")
    if text.lstrip().startswith(SLASH_COMMAND_TAGS):
        return Classification("slash_command_expansion", "")
    if is_rule_capture_prompt(text):
        return Classification("rule_capture", "")
    if rec.get("isCompactSummary") is True:
        return Classification("compact_summary", "")
    if rec.get("isMeta") is True:
        return Classification("meta_injected", "")
    if rec.get("isSidechain") is True:
        return Classification("sidechain", "")

    prompt_source = rec.get("promptSource")
    if not isinstance(prompt_source, str):
        return Classification("no_prompt_source", "")
    if prompt_source not in HUMAN_PROMPT_SOURCES:
        return Classification("non_human_prompt_source", "")

    origin = rec.get("origin")
    if isinstance(origin, dict) and origin.get("kind") != HUMAN_ORIGIN_KIND:
        return Classification("non_human_origin", "")

    if not text.strip():
        return Classification("empty_text", "")
    return Classification(ACCEPTED, text)


# --- repo 解決 ---------------------------------------------------------------

def resolve_repo(cwd: str) -> tuple[str | None, str | None]:
    """cwd から repo 識別子を解決し、(repo, 解決元) を返す。

    1 ディレクトリの解決は `_transcript_lib.resolve_repo_at` (origin remote URL →
    git-common-dir の親) に委ねる。select-candidates.py の `--repo` 既定解決も同じ
    関数を呼ぶため、mart の `repo` 値と絞り込みキーの表現一致が構造的に保証される。
    remote が付いていない repo でも path 表現に落ちるため、絞り込みが空にならない。

    issue ごとに worktree を作って merge 後に削除する運用では、観測時点で cwd が
    既に存在しない record が大量に出る。そのままでは repo 単位の絞り込みが 3 割
    近く欠落するため、**cwd が消えている場合に限り実在する最近祖先まで遡って**
    解決する (REPO_SOURCE_ANCESTOR)。削除済み worktree は repo 配下にあるので
    親 repo に正しく寄る。repo 配下でない path は祖先も repo でないため None のまま。

    解決元は record と meta の双方に出す — 直接解決と祖先経由を後段が区別できる
    ようにし、暗黙の推定にしないため。
    """
    if not cwd:
        return None, None
    cwd_path = Path(cwd)
    if cwd_path.is_dir():
        repo = _resolve_repo_at(cwd_path)
        return (repo, REPO_SOURCE_CWD) if repo else (None, None)
    for ancestor in cwd_path.parents:
        if not ancestor.is_dir():
            continue
        repo = _resolve_repo_at(ancestor)
        return (repo, REPO_SOURCE_ANCESTOR) if repo else (None, None)
    return None, None


# --- 抽出 --------------------------------------------------------------------

def extract_prompts(
    jsonl_path: Path,
    cutoff: dt.datetime,
    project_dir: str,
    text_limit: int,
) -> tuple[list[Prompt], collections.Counter]:
    """1 transcript file から手入力 prompt を抽出し、判定内訳と併せて返す。

    Counter は ACCEPTED と各除外理由を同じ台帳に載せる (合計 = 走査 user record 数)。
    """
    prompts: list[Prompt] = []
    verdicts: collections.Counter = collections.Counter()

    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as fp:
            for rec in _iter_jsonl(fp):
                if rec.get("type") != "user":
                    continue
                timestamp = rec.get("timestamp") or ""
                if not within_window(timestamp, cutoff):
                    continue
                verdict = classify_user_record(rec)
                verdicts[verdict.reason] += 1
                if verdict.reason != ACCEPTED:
                    continue
                text = verdict.text.strip()
                prompts.append(Prompt(
                    session_id=str(rec.get("sessionId") or rec.get("session_id") or ""),
                    uuid=str(rec.get("uuid") or ""),
                    timestamp=timestamp,
                    cwd=str(rec.get("cwd") or ""),
                    repo=None,  # collect() が cwd 単位でまとめて解決する
                    repo_source=None,
                    project_dir=project_dir,
                    git_branch=str(rec.get("gitBranch") or ""),
                    prompt_source=str(rec.get("promptSource") or ""),
                    cli_version=str(rec.get("version") or ""),
                    text=truncate(text, text_limit),
                    text_chars=len(text),
                    truncated=len(text) > text_limit,
                ))
    except OSError:
        return [], collections.Counter()
    return prompts, verdicts


# --- mart 生成 ---------------------------------------------------------------

def build_repos_section(prompts: list[Prompt]) -> list[dict]:
    """repo 単位の索引。後段が repo で絞り込むときの入口になる。

    repo 未解決分は id `null` の 1 entry にまとめる (件数を隠さない)。
    """
    grouped: dict[str | None, list[Prompt]] = collections.defaultdict(list)
    for prompt in prompts:
        grouped[prompt.repo].append(prompt)
    section: list[dict] = []
    for repo, items in grouped.items():
        timestamps = sorted(item.timestamp for item in items)
        section.append({
            "repo": repo,
            "prompt_count": len(items),
            "session_count": len({item.session_id for item in items}),
            "first_timestamp": timestamps[0] if timestamps else "",
            "last_timestamp": timestamps[-1] if timestamps else "",
        })
    section.sort(key=lambda entry: (-entry["prompt_count"], str(entry["repo"])))
    return section


def build_mart(
    args: argparse.Namespace,
    prompts: list[Prompt],
    verdicts: collections.Counter,
    cutoff: dt.datetime,
    now: dt.datetime,
) -> dict:
    excluded = {reason: verdicts.get(reason, 0) for reason in EXCLUSION_REASONS}
    by_repo_source = collections.Counter(
        prompt.repo_source for prompt in prompts if prompt.repo_source
    )
    resolved = sum(by_repo_source.values())
    return {
        "meta": {
            "generated_at": now.isoformat().replace("+00:00", "Z"),
            "window_start": cutoff.isoformat().replace("+00:00", "Z"),
            "window_end": now.isoformat().replace("+00:00", "Z"),
            "days": args.days,
            "transcripts_dir": str(args.transcripts_dir),
            "text_limit": args.text_limit,
            "total_prompts": len(prompts),
            "distinct_sessions": len({prompt.session_id for prompt in prompts}),
            "distinct_repos": len({prompt.repo for prompt in prompts if prompt.repo}),
            "scanned_user_records": sum(verdicts.values()),
            "excluded": excluded,
            "repo_resolution": {
                "resolved": resolved,
                "unresolved": len(prompts) - resolved,
                "by_source": {
                    source: by_repo_source.get(source, 0) for source in REPO_SOURCES
                },
            },
        },
        "repos": build_repos_section(prompts),
        "prompts": [dataclasses.asdict(prompt) for prompt in prompts],
    }


# --- main --------------------------------------------------------------------

def collect(
    args: argparse.Namespace,
    repo_resolver: Callable[[str], tuple[str | None, str | None]] = resolve_repo,
) -> dict:
    """ファイル出力を伴わない mart 構築まで (テスト用 entrypoint)。"""
    now = resolve_now(args.now)
    cutoff = now - dt.timedelta(days=args.days)

    prompts: list[Prompt] = []
    verdicts: collections.Counter = collections.Counter()
    for project_dir, jsonl in walk_transcripts(args.transcripts_dir, cutoff):
        file_prompts, file_verdicts = extract_prompts(
            jsonl, cutoff, project_dir, args.text_limit
        )
        prompts.extend(file_prompts)
        verdicts.update(file_verdicts)

    resolved_by_cwd: dict[str, tuple[str | None, str | None]] = {}
    for prompt in prompts:
        if prompt.cwd not in resolved_by_cwd:
            resolved_by_cwd[prompt.cwd] = repo_resolver(prompt.cwd)
        prompt.repo, prompt.repo_source = resolved_by_cwd[prompt.cwd]

    prompts.sort(key=lambda prompt: (prompt.timestamp, prompt.session_id, prompt.uuid))
    return build_mart(args, prompts, verdicts, cutoff, now)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mart = collect(args)
    if args.stdout_mart:
        json.dump(mart, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = resolve_now(args.now).strftime("%Y%m%dT%H%M%SZ")
    out = args.output_dir / f"mart-{stamp}.json"
    out.write_text(
        json.dumps(mart, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(str(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
