---
name: principle-balance-coupling
description: モジュール / サービス境界の結合を評価するとき、境界を分割するか統合するか判断するときに適用する。integration strength / distance / volatility の 3 次元。
user-invocable: false
---

# 結合は排除ではなくバランスさせる

- **結合は排除ではなくバランスさせる** (Khononov『Balancing Coupling in Software Design』)。integration strength (知識共有度) / distance (距離) / volatility (変更頻度) の 3 次元で評価し、変更頻度が高い箇所ほど弱い結合にする
- **モジュール / サービス境界の分割・統合そのものを判断するときは `modularity:balanced-coupling` skill を参照する** (本原則の詳細版。第三者 plugin `vladikk/modularity` 同梱で、未導入環境では上記の 3 次元だけで判断する)
