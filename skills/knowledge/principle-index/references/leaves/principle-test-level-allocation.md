---
name: principle-test-level-allocation
description: 新しくどのレベルにテストを書くか決めるとき (単体 / 統合 / e2e の配分)、ユースケースや受け入れ条件をテストに落とすときに適用する。Testing Trophy による投資配分。
---

# 統合レベルへ投資を寄せる

- **テストを書く対象のうちユースケースに当たる振る舞いは、受け入れ条件なので最優先で自動テストにする**
- 投資配分は Testing Trophy (Dodds — 標語は Rauch "Write tests. Not too many. Mostly integration.")。ソフトウェアの実際の使われ方に似たテストほど高い信頼を与える
- **単体テストは複雑なドメインロジック (計算・条件分岐が集中する箇所) へ集中投資する**。委譲だけの glue code に単体テストを書かない — 統合テストが間接的にカバーする
- e2e は主要ユースケースの happy path + 重要経路 (決済・認可・データ削除) の異常系に絞る。網羅は統合レベルで行う (e2e は実行コスト・flaky リスクが最も高い層)

対象にテストを書くか・どれだけ厚くするかは `principle-test-thickness-by-property`、既存テストを残すか削るかは `principle-test-pruning` が扱う。
