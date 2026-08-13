"""prompts mart の機械可読 contract (mart schema 知識の単一ソース)。

scan_prompts (mart) / select_candidates (slice) が共有する定数を持つ。ここにある
のは絞り込み / 並べ替えのパラメータであって **bucket 判定ではない**。bucket
(フィードバック / 価値観 / 器の分類) の確定は本 server の責務外 — 観測・集計は
決定的に、判断は人間に (ADR 0011 の 3 層分離)。

ADR 0032 の決定的ルールで本 domain に在るのは **engineering-values の採用 gate
(`repo_count >= 2`) 1 本だけ**で、`rules.py` が持つ。`inventory-project-values` 側は
SKILL.md 自身が「決定的シグナルが存在しない」と明文化しており対象外。

**tool docstring も本 contract を参照し、読み方を再エンコードしない** — docstring は
全利用セッションの context に常駐するコストを払うが、注記が要るのは mart / slice を
読む段階だけなので、置き場はここ (ADR 0031)。
"""

from __future__ import annotations

# 発話型は `marts.prompts.udf.STEERING_PATTERNS` が正本 (SQL からも呼ぶ純関数の
# 定数と同居させる)。ここでは re-export のみ。
from .udf import STEERING_PATTERNS  # noqa: F401

DEFAULT_DAYS = 30

# 1 prompt あたりの mart 保存上限。実測 p99 ≈ 2.8k 字で、超過分はエラーログ等の貼り付けが
# 大半のため後段の価値観抽出に効かない。切れたことは `truncated` flag で残す。
# **mart にだけ適用する** — store は全文を持ち、select_candidates の候補は truncate
# しない (ADR 0031: truncate 解除)。
DEFAULT_TEXT_LIMIT = 4000

# 人間が UI に入力した経路を表す promptSource の値。sdk (subagent) / system
# (task-notification / peer) はここに入らない。
HUMAN_PROMPT_SOURCES = frozenset({"typed", "queued"})

# origin.kind が human 以外 (system / task-notification / peer) なら非手入力。
HUMAN_ORIGIN_KIND = "human"

# 採用を表す reason。excluded 集計と同じ台帳に載せて totality を保つ。
ACCEPTED = "accepted"

# 除外理由。**query.sql の CASE がこの順で評価する** (record ごとに 1 理由へ
# 確定させるため順序が仕様)。
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

# --- select_candidates ---------------------------------------------------------

# 候補帯の下限 (字)。復元不能な承認語が集中する 1-59 字帯の直上。閾値を動かすときは
# 姉妹 SKILL.md (inventory-project-values / inventory-engineering-values) と
# 同時に更新する。
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

# 定型一覧に載せる正規形・原文の表示上限 (字)。一覧は人間が読み返す第 2 の候補源
# なので、判別できる長さは残す。
FORM_PREVIEW_CHARS = 200

# `--repo` をどう決めたか。cwd 既定解決を暗黙の推定にしないため meta に出す。
REPO_SCOPE_EXPLICIT = "explicit"
REPO_SCOPE_CWD = "cwd"
REPO_SCOPE_ALL = "all"

# slice の候補 record に載せる field。cwd / project_dir / prompt_source /
# cli_version は読み手の判断材料にならないため落とす (slice を小さく保つ)。
# `truncated` は store 全文採用後は常に false — slice は truncate しない
# (ADR 0031)。field 自体は既存消費者 (SKILL.md 手順) の参照を壊さないため残す。
CANDIDATE_FIELDS = (
    "session_id",
    "uuid",
    "timestamp",
    "repo",
    "git_branch",
    "text_chars",
    "truncated",
    "steering_pattern",
    "text",
)

# 優先帯の先頭に置く発話型。訂正 prompt は**規範とのずれが露出した瞬間**なので、
# 同じ観測帯の中で先に読む値打ちがある (#478 P3)。**読み順 (`rank`) は変えない**。
PRIORITY_PATTERN = "correct"

# --- contract (mart / slice に埋め込んで LLM 段階へ渡す) -------------------------

# 読み手側の将来分岐用に単調増加させる。v1 で rule 層 + contract を追加 (ADR 0032)。
SCHEMA_VERSION = 1

MART_NOTES = (
    "mart は全 project 横断で作る。repo での絞り込みは select_candidates の領分。",
    "excluded.no_prompt_source が急増していたら CLI の schema 変更で観測が劣化した"
    "疑い (silent zero にはならない設計)。急増の判定には前回 mart が要る。",
    "store.broken_lines / store.unreadable_files / store.skipped_nested_files が"
    "0 でなければ分母が欠けている (件数だけ出す。判断は読み手)。",
)

SLICE_NOTES = (
    "読み順 (`rank`) は text_chars 降順の全順序であって**提示順ではない**。",
    "候補 text は store から切り詰めずに取得する (`truncated` は常に false)。",
    "boilerplate_forms は定型として除外した正規形の一覧で、**逐語反復された規範が"
    "ここに落ちる**ため拾い戻しの候補源として読む。repo_count は正規化後 "
    "(host/owner/name) の distinct で、解決できなかった repo は数えない。",
    "repo / all_repos を渡さないと repo_root の git remote に解決する。解決できない"
    "ときは全 repo へ倒さず失敗する。",
)


def build_mart_contract() -> dict:
    return {"schema_version": SCHEMA_VERSION, "notes": list(MART_NOTES)}


def build_slice_contract(rule_catalog: list[dict]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "rules": list(rule_catalog),
        "notes": list(SLICE_NOTES),
    }
