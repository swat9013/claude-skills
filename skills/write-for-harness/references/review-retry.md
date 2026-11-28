# 検証 subagent と構造 reviewer の返答に JSON が無いとき

SKILL.md 手順 4 の「返答の読み方」で、`scripts/classify-review-reply.py` の `判定` が `得た` 以外のときに Read する。script は `unread_refs` の path が実在するか (`${` を含む path は、本 skill の絶対 path に置き換えて確かめる) で、やり直すかを決めて出す。

- `やり直す`: その subagent だけを 1 回やり直す。prompt は SKILL.md 手順 4 の `build-review-prompts.py` で組み立て直し、返答は `--retried` を付けて script に渡し直す
- `未実施`: やり直さない。その subagent の結果の位置に、出力の `未実施行` の文と `次の手順` の文を書く — 実在しない path は、起動し直しても同じ `unread_refs` が返るだけになる
