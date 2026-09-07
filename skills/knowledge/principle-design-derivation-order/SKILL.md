---
name: principle-design-derivation-order
description: アーキテクチャや構造を新しく決めるとき、不可逆な決定を今下すか遅らせるか迷ったときに適用する。制約 → アーキテクチャ特性 → 構造の導出順序。
user-invocable: false
---

# 制約から構造を導出する

- **導出順序**: 制約 (ビジネス・チーム規模・非機能要件) → アーキテクチャ特性 (architecture characteristics — 影響度の高い -ilities のみ選抜する。Richards & Ford『Fundamentals of Software Architecture』) → 構造。設計後に見つかった観点で制約を問い直すループを回す
- **決定の遅延**: 不可逆な決定は last responsible moment (Poppendieck『Lean Software Development』) まで遅らせる。ただし遅らせすぎて「未決定」がデフォルトの決定になるのは失敗
- **進め方**: ユースケース洗い出し → ドメインモデル抽出 → 最小構造で実装 → 実装知見をユースケース・モデル・構造へ還流する。ユースケースは受け入れ条件であり、自動テストに落とす (Clean Architecture / DDD 系譜のユースケース駆動)
- **最適化目標**: 少人数で効率よく運用できるシステム。回復不能な構造 (最悪) の回避が、局所最適より先
