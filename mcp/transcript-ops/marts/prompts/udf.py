"""prompts mart の純関数 (ADR 0031 の UDF 規律)。SQL からも Python からも呼ぶ。

**I/O・clock・store アクセスを持たない。** 入力は値だけ、出力は値だけ。repo 解決
(git 呼び出し) は I/O なのでここに置かない — `present.py` が post-query で行う。

「SQL は関係代数だけ、意味は UDF」の配置規則により、text の意味解釈 (slash 展開判定 /
`#rule ` 捕捉 / 発話型分類 / 定型判定の正規化) はすべてここに来る。
"""

from __future__ import annotations

import re
import sqlite3

from adapter.transcript import is_slash_expansion_record

# --- 除外判定の text 解釈 ------------------------------------------------------

# `#rule ` 運用メモの判定 prefix。A-strict = 非空行が 1 行以上あり、その全てが
# `#rule ` (末尾スペース込み・行頭一致) で始まる prompt のみ該当し、通常行との
# 混在 prompt は手入力として残す。
#
# 捕捉 hook 自体は ADR 0018 で撤去済み (`#rule ` prompt は今後 block されず
# transcript に残る) が、除外は**意図的に維持する** — 撤去前後で mart の観測帯を
# 揺らさないため。観測帯の拡張は別 issue の領分であり、本 gate の削除はそこで
# 判断する。仕様正本は本コメント (参照先 doc / script は現存しない)。
RULE_PREFIX = "#rule "


def is_rule_capture(text: str) -> bool:
    """A-strict 判定で `#rule ` 運用メモかを返す。

    非空行が 1 行以上あり、その全てが `#rule ` (末尾スペース込み・行頭一致) で
    始まる場合のみ真。混在 prompt は通常の手入力として残す (`RULE_PREFIX` 参照)。

    `bool` を返す (SQLite は int 部分型として自動変換する) — Python 側の呼び先
    (テスト等) が `is True/False` で比較できるようにするため。
    """
    total = 0
    matched = 0
    for line in (text or "").split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        total += 1
        if line.startswith(RULE_PREFIX):
            matched += 1
    return total > 0 and matched == total


def is_slash_expansion(text: str) -> bool:
    """store が持つ flatten 済み `text` が slash 展開 record の形か。

    判定そのものは `adapter.transcript.is_slash_expansion_record` を再利用する
    (述語を複製しない — scan_invocations / find_invocations と同じ答えを出すため)。
    list content (画像添付等) は ingest 側で text block だけへ既に潰れているため、
    残るのは「plain str の content が行頭一致するか」の判定と同値になる。
    """
    return is_slash_expansion_record(text)


def is_blank(text: str) -> bool:
    """空白だけ (改行・空白のみ) の text か。SQL の `trim()` は空白しか削らず
    改行を残すため、Python の `str.strip()` と同じ判定を UDF で行う。
    """
    return not (text or "").strip()


def char_length(text: str) -> int:
    """文字数 (Python `len()` と一致させる — SQLite `length()` の実装依存を避ける)。"""
    return len(text or "")


# --- steering pattern (発話型の決定的 lexical 分類) --------------------------
# 手入力 prompt を 4 型に分ける。**判定ではなく観測** — 「この発話が規範か」は
# 依然として人間が決める。分類の値打ちは `correct` (訂正) にあり、訂正 prompt は
# **規範とのずれが露出した瞬間**なので、候補の優先帯として使える (#478 P3)。
#
# **この順で評価する** (prompt ごとに 1 型へ確定させるため順序が仕様)。correct を
# 先に見るのは、訂正発話が疑問形・命令形の外見を取ることが多いため
# (「なぜ勝手に消した?」は question の形をした correct)。
STEERING_PATTERNS = ("correct", "question", "instruct", "steer")

# 訂正語を探す範囲 (先頭 N 字)。**全文を見ると長文の task brief が correct に化ける** —
# 実測 (直近 5 日 / 270 prompt) で、issue 実行の定型 brief 6 件が本文中の「ではなく」
# 「するな」に反応して correct になり、優先帯が定型で埋まった。訂正は冒頭で述べられる
# ので先頭だけを見る。120 字だと実在の訂正 2 件を取り落とし、200 字で定型 0 件・
# 実訂正 5 件になった。
CORRECT_HEAD_CHARS = 200

# 直前の出力を**否定・差し戻す**表現。ここに挙げるのは「既に起きたこと」への
# 反応語だけで、単なる否定語 (「ない」等) は入れない (通常の説明文に頻出するため)。
CORRECT_MARKERS = (
    "ではなく", "じゃなく", "ではなくて", "そうじゃない", "違う", "違います",
    "間違", "やめて", "やめろ", "戻して", "元に戻", "勝手に", "余計な",
    "しないで", "するな", "ダメ", "駄目", "why did you", "don't ", "do not ",
    "revert", "undo", "that's wrong", "incorrect",
)

# 問いの形。文末の疑問符と、文中に現れる日本語の疑問終助詞。
QUESTION_MARKERS = ("ですか", "でしょうか", "ますか", "どう思う")
QUESTION_SUFFIXES = ("?", "？")

# 依頼・命令の形。文中に現れる依頼語と、文末に来る命令形。
INSTRUCT_MARKERS = (
    "してください", "して下さい", "してほしい", "して欲しい", "してくれ",
    "お願いします", "please ",
)
# 末尾の `て` は日本語の依頼形 (「書いて」「直して」) を広く拾う。instruct と steer の
# 取り違えは優先帯 (correct のみ) に影響しないので、広めに取ってよい。
INSTRUCT_SUFFIXES = ("て", "しろ", "せよ", "ください", "下さい")

# 文末判定の前に落とす句読点。`?` は question 側で見るのでここに入れない。
SENTENCE_END_PUNCTUATION = "。．.!！"


def classify_steering_pattern(text: str) -> str:
    """手入力 prompt 1 件の発話型 (`STEERING_PATTERNS` の順に評価)。

    表層語だけを見る決定的分類で、**意味の判定はしない**。取りこぼし
    (訂正なのに correct と出ない) はあるが、誤検知を増やさない側に倒してある —
    優先帯は候補の**並べ替え**にしか使わないので、漏れは順位が下がるだけで
    候補から消えることはない。**truncate 前の全文で行う** — 末尾の命令形が切れると
    型が変わるため (store は常に全文を持つので、この UDF に切り詰めは不要)。
    """
    text = text or ""
    lowered = text.lower()
    if any(marker in lowered[:CORRECT_HEAD_CHARS] for marker in CORRECT_MARKERS):
        return "correct"
    stripped = text.rstrip()
    if stripped.endswith(QUESTION_SUFFIXES) or any(
            marker in text for marker in QUESTION_MARKERS):
        return "question"
    tail = stripped.rstrip(SENTENCE_END_PUNCTUATION)
    if tail.endswith(INSTRUCT_SUFFIXES) or any(
            marker in lowered for marker in INSTRUCT_MARKERS):
        return "instruct"
    return "steer"


# --- 定型判定の正規化 ----------------------------------------------------------

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


def normalize_text(text: str) -> str:
    """定型判定用の正規形。`NORMALIZERS` の順に潰す (順序は仕様)。"""
    text = text or ""
    for pattern, replacement in NORMALIZERS:
        text = pattern.sub(replacement, text)
    return text.strip()


# --- repo 識別子の正規化 --------------------------------------------------------

# remote URL の 3 表記 (`git@host:owner/name.git` / `https://host/owner/name` /
# `ssh://git@host/owner/name`) を `host/owner/name` へ落とす。
_SCP_REMOTE_RE = re.compile(r"^(?:[\w.\-]+@)?([\w.\-]+):(?!/)(.+)$")
_URL_REMOTE_RE = re.compile(r"^[a-zA-Z][\w+.\-]*://(?:[^@/]+@)?([^/]+)/(.+)$")


def normalize_repo_identifier(repo: str | None) -> str | None:
    """repo 識別子を `host/owner/name` へ正規化する。解決不能なら None。

    **表記違いで同じ repo が別 repo に見えるのを潰す**のが目的 (実測で
    `git@github.com:swat9013/swat-skills.git` / `https://github.com/...` /
    `ssh://git@github.com/...` / `.git` suffix の有無が混在する)。正規化せずに
    distinct を数えると repo 数が水増しされ、単一 repo の規範が汎用性の証拠を
    持ったように見える。

    remote URL でない識別子 (git-common-dir 由来の絶対 path) はそのまま返す —
    path は表記が 1 通りしかない。
    """
    if not repo:
        return None
    value = repo.strip()
    if not value:
        return None
    match = _URL_REMOTE_RE.match(value) or _SCP_REMOTE_RE.match(value)
    if match is None:
        return value
    host, path = match.group(1), match.group(2)
    return f"{host.lower()}/{path.strip('/').removesuffix('.git')}"


# SQL 名 → (関数, 引数) の登録表。**query.sql が呼ぶ名前の単一ソース**。
REGISTERED = (
    ("is_rule_capture", 1, is_rule_capture),
    ("is_slash_expansion", 1, is_slash_expansion),
    ("is_blank", 1, is_blank),
    ("char_length", 1, char_length),
    ("classify_steering_pattern", 1, classify_steering_pattern),
    ("normalize_text", 1, normalize_text),
)


def register(conn: sqlite3.Connection) -> None:
    """接続に UDF を登録する。すべて deterministic (同じ入力に同じ答え)。"""
    for name, argument_count, function in REGISTERED:
        conn.create_function(name, argument_count, function, deterministic=True)
