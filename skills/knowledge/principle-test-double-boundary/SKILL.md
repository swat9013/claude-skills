---
name: principle-test-double-boundary
description: テストで依存を差し替えるとき (mock / stub / fake / 実物のどれを使うか)、時刻・乱数・環境変数を制御するときに適用する。mock を許す境界は unmanaged dependency だけ。
user-invocable: false
---

# mock は unmanaged dependency だけ

- **mock を許すのは unmanaged dependency のみ** (Khorikov): 外部からも観測される out-of-process 依存 (外部 API・メール送信・message bus)。これらとの通信は観測可能な振る舞いの一部であり、通信自体を検証する意味がある
- **managed dependency (自アプリのみが使う DB 等) は mock しない**。実物 (テスト用インスタンス・testcontainers) か fake を使う。DB との通信は実装詳細であり、mock するとリファクタリング耐性を失う
- 時刻・乱数・環境変数は注入で制御する (引数 or 依存として渡す)。テスト内 sleep・実時刻依存は flaky の温床
- **ドメインロジックの協調オブジェクトは実物を使う** (classical / Detroit 派 — Khorikov も同派)。mock だらけのテストは実装詳細への結合が進行している兆候
