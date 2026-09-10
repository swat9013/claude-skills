---
name: principle-tdd-rhythm
description: 機能実装・バグ修正に着手するとき、テストを先に書くか後に書くか決めるときに適用する。開発リズムとしての TDD。
---

# Red-Green-Refactor で進める

- **TDD で進める** (Beck『Test-Driven Development: By Example』の Red-Green-Refactor)。スパイク (探索目的の使い捨てコード) は TDD 免除 — ただしスパイクと明示し、本流に採用する時点で TDD で書き直す
- **テストは目的ではなく手段** — 設計フィードバックとリグレッション防御のためにある (t-wada の整理)。手段の目的化の兆候 (カバレッジ数値の最大化競争・実装詳細を写経しただけのテスト) を見つけたら黙って従わず指摘する

TDD は開発リズム、投資配分は `principle-test-level-allocation` が扱う (統合テストでも Red-Green-Refactor は回せるので矛盾しない)。
