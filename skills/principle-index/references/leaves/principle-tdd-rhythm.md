---
name: principle-tdd-rhythm
description: テストを書く対象の機能実装・バグ修正に着手するとき、テストを先に書くか後に書くか決めるときに適用する。開発リズムとしての TDD。
---

# Red-Green-Refactor で進める

- **TDD で進める** (Beck『Test-Driven Development: By Example』の Red-Green-Refactor)。書くと決めたテストは実装より先に書く。対象にテストを書くか・どれだけ厚くするかは扱わない (`principle-test-thickness-by-property` が決める)。スパイク (探索目的の使い捨てコード) は TDD 免除 — ただしスパイクと明示し、本流に採用する時点で TDD で書き直す
- **先に書くのは、テストを設計フィードバックとリグレッション防御の手段として使うため** (t-wada の整理)。実装の後に書いたテストは実装の形を追認するだけになり、設計へのフィードバックが返らない

TDD は開発リズム、投資配分は `principle-test-level-allocation` が扱う (統合テストでも Red-Green-Refactor は回せるので矛盾しない)。
