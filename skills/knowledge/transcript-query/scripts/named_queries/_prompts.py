"""手入力 prompt の mart と slice が共有する抽出の層 (問いの部品)。

ここにあるのは手入力の抽出と絞り込みの材料で、bucket の判定ではない。「どれがフィードバックか」
「どれが規範か」は持たない (設計規則 (5))。発話の分類と正規化は表層語の決定的な処理で、view の形式の
事実ではなく mart の schema に属するので、view に置かない (設計 doc「adapter に置かないもの」)。

成果物の行は `section` と `body` の 2 列で、1 枚の成果物に種類の違う表を載せる。
"""

from __future__ import annotations

import collections
import datetime as dt
import re
from pathlib import Path

from named_query import QueryRun, Stopped, Window

TRANSCRIPT_KIND = "main"
# 実測 p99 ≈ 2.8k 字で、超える分はエラーログなどの貼り付けが大半なので mart では切る。slice は切らない
MART_TEXT_LIMIT = 4000

HUMAN_PROMPT_SOURCES = frozenset({"typed", "queued"})
HUMAN_ORIGIN_KIND = "human"

ACCEPTED = "accepted"
# record ごとに 1 理由へ確定させるので順序が仕様。`_verdict` がこの順に評価する
EXCLUSION_REASONS = (
    "tool_result",
    "slash_command_expansion",
    "rule_capture",
    "compact_summary",
    "meta_injected",
    "sidechain",
    "no_prompt_source",
    "non_human_prompt_source",
    "non_human_origin",
    "empty_text",
)

# 捕捉の hook は撤去済みだが、撤去の前後で観測帯を揺らさないよう除外を保つ (ADR 0018)
RULE_PREFIX = "#rule "

STEERING_PATTERNS = ("correct", "question", "instruct", "steer")
# 全文を見ると長文の依頼の本文が correct に化ける。訂正は冒頭で述べられるので先頭だけを見る
CORRECT_HEAD_CHARS = 200
# 「既に起きたこと」への反応語だけを挙げる。単なる否定語は説明文に頻出するので入れない
CORRECT_MARKERS = (
    "ではなく", "じゃなく", "そうじゃない", "違う", "違います",
    "間違", "やめて", "やめろ", "戻して", "元に戻", "勝手に", "余計な",
    "しないで", "するな", "ダメ", "駄目", "why did you", "don't ", "do not ",
    "revert", "undo", "that's wrong", "incorrect",
)
QUESTION_MARKERS = ("ですか", "でしょうか", "ますか", "どう思う")
QUESTION_SUFFIXES = ("?", "？")
INSTRUCT_MARKERS = (
    "してください", "して下さい", "してほしい", "して欲しい", "してくれ",
    "お願いします", "please ",
)
# 末尾の `て` は依頼形を広く拾う。instruct と steer の取り違えは優先帯 (correct だけ) に効かない
INSTRUCT_SUFFIXES = ("て", "しろ", "せよ", "ください", "下さい")
SENTENCE_END_PUNCTUATION = "。．.!！"

# 適用の順が仕様。URL を先に潰さないと path と数値が URL の内部を先に食う。path は行頭か非単語文字の
# 直後の `/` `~/` だけを潰す — 無制限にすると `A/B テスト` のような散文まで潰れ、別の発話が同じ正規形に化ける
NORMALIZERS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"(?<!\w)~?/[\w.\-/]+"), "<path>"),
    (re.compile(r"\d+"), "<n>"),
    (re.compile(r"\s+"), " "),
)

# remote URL の 3 表記を `host/owner/name` へ落とす
_SCP_REMOTE_RE = re.compile(r"^(?:[\w.\-]+@)?([\w.\-]+):(?!/)(.+)$")
_URL_REMOTE_RE = re.compile(r"^[a-zA-Z][\w+.\-]*://(?:[^@/]+@)?([^/]+)/(.+)$")

PROMPT_SQL = """
SELECT project_dir, session_id, record_uuid, ts, cwd, git_branch, cli_version, prompt_source, body,
       has_tool_result, lake_is_slash_expansion(body) AS slash_expansion, is_compact_summary, is_meta, is_sidechain,
       origin_is_object, origin_kind
FROM lake_user_prompt
WHERE ts_epoch IS NULL OR ts_epoch BETWEEN $since AND $until
"""


def _is_rule_capture(text: str) -> bool:
    """非空行が 1 行以上あり、その全部が `#rule ` で始まるか。通常行と混ざる prompt は手入力として残す。"""
    lines = [line for line in text.split("\n") if line.strip()]
    return bool(lines) and all(line.startswith(RULE_PREFIX) for line in lines)


def _verdict(row: dict) -> str:
    if row["has_tool_result"]:
        return "tool_result"
    if row["slash_expansion"]:
        return "slash_command_expansion"
    if _is_rule_capture(row["text"]):
        return "rule_capture"
    if row["is_compact_summary"]:
        return "compact_summary"
    if row["is_meta"]:
        return "meta_injected"
    if row["is_sidechain"]:
        return "sidechain"
    if row["prompt_source"] is None:
        return "no_prompt_source"
    if row["prompt_source"] not in HUMAN_PROMPT_SOURCES:
        return "non_human_prompt_source"
    if row["origin_is_object"] and row["origin_kind"] != HUMAN_ORIGIN_KIND:
        return "non_human_origin"
    if not row["text"]:
        return "empty_text"
    return ACCEPTED


def classify_steering_pattern(text: str) -> str:
    """表層語だけの決定的な分類。correct を先に見るのは、訂正が疑問形や命令形の外見を取るため。"""
    lowered = text.lower()
    if any(marker in lowered[:CORRECT_HEAD_CHARS] for marker in CORRECT_MARKERS):
        return "correct"
    stripped = text.rstrip()
    if stripped.endswith(QUESTION_SUFFIXES) or any(marker in text for marker in QUESTION_MARKERS):
        return "question"
    if stripped.rstrip(SENTENCE_END_PUNCTUATION).endswith(INSTRUCT_SUFFIXES) or any(
            marker in lowered for marker in INSTRUCT_MARKERS):
        return "instruct"
    return "steer"


def normalize_text(text: str) -> str:
    for pattern, replacement in NORMALIZERS:
        text = pattern.sub(replacement, text)
    return text.strip()


def normalize_repo_identifier(repo: str | None) -> str | None:
    """同じ repo が remote URL の表記違いで別 repo に見えるのを潰す。URL でない識別子はそのまま返す。"""
    value = (repo or "").strip()
    if not value:
        return None
    match = _URL_REMOTE_RE.match(value) or _SCP_REMOTE_RE.match(value)
    if match is None:
        return value
    return f"{match.group(1).lower()}/{match.group(2).strip('/').removesuffix('.git')}"


def _repo_of(run: QueryRun, cwd: str) -> tuple[str | None, str | None]:
    """cwd の repo と解決元。worktree を消す運用では cwd が消えた record が多いので、消えた cwd は実在する
    最も近い祖先で解決する (mart の schema に属する解決で、view の解決とは共有しない)。
    """
    if not cwd:
        return None, None
    path = Path(cwd)
    target, source = (path, "cwd") if path.is_dir() else (
        next((ancestor for ancestor in path.parents if ancestor.is_dir()), None), "ancestor")
    repo = run.resolve_repo(str(target))["repo"] if target else None
    return (repo, source) if repo else (None, None)


def read_prompts(run: QueryRun) -> tuple[list[dict], collections.Counter]:
    """窓の中の user record を採用と除外の理由へ確定させる。返すのは読み順に並べた採用分と理由ごとの件数。

    時刻の欠けた record は窓の中へ倒す。
    """
    verdicts: collections.Counter = collections.Counter()
    accepted: list[dict] = []
    for row in run.select(PROMPT_SQL):
        row["text"] = (row.pop("body") or "").strip()
        verdict = _verdict(row)
        verdicts[verdict] += 1
        if verdict == ACCEPTED:
            accepted.append(row)
    accepted.sort(key=lambda row: (row["ts"], row["session_id"], row["record_uuid"]))
    repos = {cwd: _repo_of(run, cwd) for cwd in {row["cwd"] for row in accepted}}
    for row in accepted:
        row["repo"], row["repo_source"] = repos[row["cwd"]]
        row["text_chars"] = len(row["text"])
        row["steering_pattern"] = classify_steering_pattern(row["text"])
    return accepted, verdicts


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def window_meta(window: Window) -> dict:
    return {"generated_at": _iso(dt.datetime.now(dt.timezone.utc).timestamp()),
            "window_start": _iso(window.since), "window_end": _iso(window.until),
            "transcript_kind": TRANSCRIPT_KIND}


def section(name: str, body: dict) -> dict:
    return {"section": name, "body": body}


def int_param(run: QueryRun, name: str) -> int:
    try:
        value = int(run.params[name])
    except ValueError:
        value = -1
    if value < 0:
        raise Stopped("bad_argument", f"--param {name} は 0 以上の整数で渡してください")
    return value
