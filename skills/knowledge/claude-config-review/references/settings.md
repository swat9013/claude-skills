# settings.json のレビュー観点

## いつこの doc を Read するか

`settings.json` / `settings.local.json` を編集する前、または **`permissions.allow` / `permissions.deny` / `hooks` の slot 配置 (どの軸の規制をどこで宣言するか)** に迷ったときに Read する。

## 責務

`~/.claude/settings.json` は Claude Code の設定ファイル。permission / hook 登録 / 環境変数 / permissions.defaultMode などを定義する。

- **目的**: ハーネス全体の構成を宣言的に管理する。
- **対象外**: per-machine な秘匿設定 (それは `~/.claude/settings.local.json` の責務)、コードのプロンプト規範 (それは CLAUDE.md の責務 → [claude-md](./claude-md.md))、hook スクリプト本体のレビュー (それは [hook](./hook.md) の責務 — settings 側は event/matcher の登録までを担当)。

## 仕様

主要セクション:

| セクション | 意味 |
|---|---|
| `permissions.allow` | 自動許可する tool / コマンドのパターン |
| `permissions.deny` | 明示拒否する tool / コマンドのパターン |
| `permissions.defaultMode` | デフォルトのプロンプト挙動。候補値は schema / 公式 docs を参照 (散文に列挙すると drift する) |
| `hooks` | event ごとの hook 登録 ([hook](./hook.md)) |
| `env` | セッションに注入する環境変数 |

詳細仕様は [sources](./sources.md) 経由で公式 docs を確認する。

### settings.local.json との分担

同名のファイルが役割の違う 4 箇所に現れるので、どれを指しているかを先に確定する:

| 実体 | 役割 | VCS |
|---|---|---|
| `~/.claude/settings.json` | ユーザー全環境の既定 | dotfiles 側の管理下 |
| `<repo>/.claude/settings.json` | リポジトリで共有する設定 | 追跡する |
| `<repo>/.claude/settings.local.json` | per-machine な実行時設定・秘匿値 | **管理外** |
| 配布用テンプレート (例: `settings/settings.local.json`) | 各環境の local へ反映させる原本 | **追跡する** |

機密情報 (API キー / トークン) や個人環境固有のパスは runtime local 側に置く。配布テンプレートが追跡下にあるのは矛盾ではない — 原本を共有し、反映先が管理外という分担。

## チェックリスト

settings.json を編集する前に確認する。

- [ ] `permissions.allow` が最小権限原則に従っているか (広すぎる pattern はないか)
- [ ] `permissions.deny` が allow と矛盾していないか (deny が allow を覆す挙動を理解しているか)
- [ ] hook 登録の event / matcher が妥当か → [hook](./hook.md) のチェックリストも参照
- [ ] `settings.local.json` との分担が守られているか (per-machine 設定が settings.json に混ざっていないか)。上表で対象がどの実体かを先に確定する — 追跡下の配布テンプレートを「管理外のはず」と誤判定しない
- [ ] `permissions.defaultMode` の選択が意図通りか (`bypassPermissions` は強い権限を与えるので慎重に)
- [ ] 環境変数が秘匿情報を含んでいないか (秘匿は local 側へ)
- [ ] 同じ目的で複数の hook が重複登録されていないか

## アンチパターン

- **過剰な `permissions.allow`**: 広範な許可は事故源。
  - × `Bash(*)` / `Bash(git:*)`
  - ○ `Bash(npm run:*)` / `Bash(git diff:*)` のようにサブコマンド + ワイルドカードで絞る
- **`deny` での「あとから禁止」依存**: allow が広く、deny で穴を塞ぐ設計は穴が増えるたびに脆くなる。allow を絞る方が安全。
- **per-machine 設定の settings.json への混入**: 個人パス / マシン固有秘匿が共有 settings に入ると別マシンで動かない / 漏洩する。
- **同 event への hook 過剰登録**: PreToolUse などへの hook が多すぎると挙動の追跡が困難になる。役割を統合できないか検討 (実装本体の重複は [hook](./hook.md) 側で判断)。
- **`permissions.defaultMode` に `bypassPermissions` を恒久指定**: 短期作業時の便利設定を恒久化すると、後から発生した重大操作も無確認で通る。
- **`env` への secret 平文記録**: settings.json は VCS で共有されるので、secret は local 側か外部 secret store に置く。

## 参照

- 共通: [architecture](./architecture.md) (`permissions.allow` = C/Guide / `permissions.deny` = C/Sensor / `hooks` = hook 機構の発火点宣言 の slot 配置根拠) / [models](./models.md) (env で指定するモデル ID の管理) / [sources](./sources.md) (公式仕様の引き方)
- 関連: [hook](./hook.md) (hook 実装本体のレビュー観点)
- 公式: Claude Code settings ドキュメント (URL は [sources](./sources.md) 経由で確認)
