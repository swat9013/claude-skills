---
name: principle-test-level-allocation
description: 新しくどのレベルにテストを書くか決めるとき (単体 / 統合 / e2e の配分) に適用する。Testing Trophy による投資配分。
user-invocable: false
---

# 統合レベルへ投資を寄せる

- **受け入れ条件・ユースケースの振る舞いを検査する自動テスト (e2e・統合テスト) を最優先で書く**。ユースケースは受け入れ条件であり、自動テストに落とす (`swat-skills:principle-design-derivation-order` の進め方と同一原則)
- 投資配分は Testing Trophy (Dodds — 標語は Rauch "Write tests. Not too many. Mostly integration.")。ソフトウェアの実際の使われ方に似たテストほど高い信頼を与える
- **単体テストは複雑なドメインロジック (計算・条件分岐が集中する箇所) へ集中投資する**。委譲だけの glue code に単体テストを書かない — 統合テストが間接的にカバーする
- e2e は主要ユースケースの happy path + 重要経路 (決済・認可・データ削除) の異常系に絞る。網羅は統合レベルで行う (e2e は実行コスト・flaky リスクが最も高い層)

既存テストを残すか削るかの判断は `swat-skills:principle-test-pruning` が扱う。
