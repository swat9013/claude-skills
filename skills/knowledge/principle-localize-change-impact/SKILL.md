---
name: principle-localize-change-impact
description: 設計原則同士が競合したとき (DRY と KISS、共通化と独立性のどちらを取るか) に適用する。競合の解決規則。
user-invocable: false
---

# 変更影響の局所化が他のすべての原則に優先する

- **変更影響の局所化がすべての原則に優先する**: ある箇所の変更が他モジュールへ波及しない構造を選ぶ (coupling / cohesion — Stevens, Myers & Constantine 1974)。DRY・SOLID・KISS と競合したら波及を抑える側を選ぶ。結合の最小化そのものは目的ではない — 過剰分割 (分散モノリス・早すぎる抽象化) はこの原則の違反
- **DRY の対象は「知識」でありコード行ではない** (Hunt & Thomas『The Pragmatic Programmer』)。異なる理由で変わる似たコードは重複のまま残す。共通化がモジュール間結合を生むなら重複を許容する

結合の強さそのものを評価するときは `swat-skills:principle-balance-coupling` を読む。
