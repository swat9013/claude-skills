---
name: principle-debt-quadrant
description: 品質と速度が競合したとき、技術的負債を作るか・いつ返すか決めるとき、根本原因を直さず回避策で進めるか決めるときに適用する。Technical Debt Quadrant による許容判定。
---

# 負債は deliberate かつ prudent なものだけ許容する

- **まずフェーズを特定する**。探索フェーズ = 速度優先、安定フェーズ = 品質優先。フェーズは人が決める優先度で、repo の宣言や issue から読めなければ人しか持たない入力に当たり、判断を人に返す (返し方は `principle-ai-delegation-boundary`)
- **技術的負債の許容判定は Technical Debt Quadrant** (Fowler, 2009) で行う。許容できるのは deliberate かつ prudent な負債 — 所在と悪化条件を把握し記録した負債のみ。分かっていて記録せず放置するのは reckless であり許容しない。根本原因を残して回避策で症状を消すのも負債を作ることに当たり、同じ判定にかける
- **記録する中身は所在・悪化条件・返済トリガー**。返済を作業として追うなら issue にも残す。コメントか ADR かは `principle-intent-placement` の表で決める
- **返済は Boy Scout Rule が基本** (Robert C. Martin『Clean Code』— checkout 時より綺麗に checkin する)。大きな構造変更はビジネス価値と照らして計画返済に切り替える
