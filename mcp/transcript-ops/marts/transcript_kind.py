"""mart が数える transcript の種類 (main session / subagent) の選び方。

store は `file.transcript_kind` に `main` / `subagent` を持つ (#1456。混ぜてはいけない
理由は store/schema.sql の同列の注記)。**既定は main だけ**で、subagent を含めるかを
呼び出し側が選べるのは permissions の mart だけ。permissions と、main に固定した
prompts の mart / slice は、本 module の `kinds_param` が返す JSON 配列を SQL の
`:transcript_kinds` に渡す。他の mart
(invocations / overhead) は選択肢を持たず、SQL に `'main'` を直接書く。
meta.store の劣化シグナル (`store.anomalies`) の集計にも、その成果物と同じ母集団を
本 module の値で渡す。
"""

from __future__ import annotations

import json

DEFAULT = "main"

# 呼び出し側の選択 → SQL に渡す種類の集合。`section` と同じ string enum にする
# (boolean 引数で振る舞いを分岐させない)
_KINDS_BY_CHOICE: dict[str, tuple[str, ...]] = {
    "main": ("main",),
    "all": ("main", "subagent"),
}

# CLI の `choices` など、受け付ける選択肢を列挙する側はここを引く
CHOICES: tuple[str, ...] = tuple(_KINDS_BY_CHOICE)


class TranscriptKindRejected(ValueError):
    """`transcript_kind` が既知の選択肢でない。store を読む前に止める。"""


def kinds_param(choice: str) -> str:
    """SQL の `:transcript_kinds` (`json_each` で展開する JSON 配列) を返す。"""
    kinds = _KINDS_BY_CHOICE.get(choice)
    if kinds is None:
        raise TranscriptKindRejected(
            f"transcript_kind は {' / '.join(CHOICES)} のどれか "
            f"(受け取った値: {choice!r})")
    return json.dumps(list(kinds))
