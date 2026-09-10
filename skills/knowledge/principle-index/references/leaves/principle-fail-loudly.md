---
name: principle-fail-loudly
description: エラー処理・例外・フォールバック・縮退の挙動を書くときに適用する。失敗を即座に可視化する原則。
---

# 失敗は即座に可視的にする

- **fail fast + Design by Contract**。事前条件・事後条件・不変条件 (Meyer『Object-Oriented Software Construction』) を明示し、違反は即座に可視的に失敗させる (Shore "Fail Fast", IEEE Software 2004)。DbC は fail fast の実装手段の一つ
- **開発時は fail fast、本番は graceful degradation** (コア機能を維持して縮退)。沈黙の失敗 (握りつぶし・暗黙の自動回復) は最悪
- 暗黙のフォールバックは禁止。エラーは明示的に処理する — 対象は仕様化されない握り潰しや暗黙代入。仕様化・テストで固定した fail-open / fail-closed や DI seam の default (`x if x is not None else RealImpl()`) は本禁則の対象外
