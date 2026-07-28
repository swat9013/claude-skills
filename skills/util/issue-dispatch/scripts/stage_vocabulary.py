"""issue-dispatch の stage 語彙 (label 集合) の正本。

STAGE_TABLE (dispatch_tracker.py) と PROMPT_TEMPLATES (herdr_ops.py) は stage ごとの
値が別関心 (優先度 / prompt) のため dict 統合はしない (#255)。代わりに key 集合の
正本を本 module に置き、各 dict は定義直後に require_full_coverage() を呼んで
import 時に検証する — 集合不一致を spawn 時の HerdrError まで繰り延べず、
どの実行経路でも最初の import で落とす。

stage を追加・削除するときは STAGES を先に更新し、各 script の dict を追従させる。
"""

STAGES = (
    "ready-for-agent",
    "wayfinder:grilling",
    "wayfinder:research",
    "wayfinder:prototype",
    "wayfinder:task",
    "needs-triage",
)


def require_full_coverage(mapping, owner):
    """mapping の key 集合が STAGES と完全一致することを強制する (不一致は ValueError)。"""
    missing = set(STAGES) - set(mapping)
    extra = set(mapping) - set(STAGES)
    if missing or extra:
        raise ValueError(
            f"{owner}: stage 語彙が stage_vocabulary.STAGES と不一致 "
            f"(missing={sorted(missing)}, extra={sorted(extra)})"
        )
