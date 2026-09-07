---
name: principle-debt-quadrant
description: 品質と速度が競合したとき、技術的負債を作るか・いつ返すか決めるときに適用する。Technical Debt Quadrant による許容判定。
user-invocable: false
---

# 負債は deliberate かつ prudent なものだけ許容する

- **まずフェーズを特定する**。探索フェーズ = 速度優先、安定フェーズ = 品質優先。フェーズが不明なら判断前にユーザーへ確認する
- **技術的負債の許容判定は Technical Debt Quadrant** (Fowler, 2009) で行う。許容できるのは deliberate かつ prudent な負債 — 所在と悪化条件を把握し記録した負債のみ。分かっていて記録せず放置するのは reckless であり許容しない
- **負債を作るときは必ず記録する**: 所在・悪化条件・返済トリガーをコメント / ADR / issue のいずれかに残す
- **返済は Boy Scout Rule が基本** (Robert C. Martin『Clean Code』— checkout 時より綺麗に checkin する)。大きな構造変更はビジネス価値と照らして計画返済に切り替える
