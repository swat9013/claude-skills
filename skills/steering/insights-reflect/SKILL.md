---
name: insights-reflect
user-invocable: true
description: 標準 /insights を実行し、HTML レポートからの学びを CLAUDE.md に反映する。Historical insights フローの実装。Use when「insights-reflect」「insights 反映」「過去セッションの振り返り」「セッションログを分析して反映」.
---

# insights-reflect

標準 `/insights` の実行から CLAUDE.md 反映までを一貫して処理する dedicated skill。手動起動のみ（自動起動しない）。

## 実行フロー

### Step 1: /insights 実行

Skill tool で標準 `insights` を実行する。HTML レポートが `~/.claude/usage-data/` に生成される。

### Step 2: HTML レビュー

生成された HTML ファイルをブラウザで開く（`open <path>`）。ユーザーにレポートのレビューを依頼する。

### Step 3: 反映方針の確認

AskUserQuestion でユーザーに確認する:

- レポートのどの提案を CLAUDE.md に反映するか
- 追加の気づき（レポートに載っていないがセッションログから読み取れるもの）はあるか
- 反映を見送る提案はあるか

### Step 4: CLAUDE.md 反映

ユーザーの方針に基づき、CLAUDE.md への変更を実施する:

1. 既存ルールとの重複チェック: 提案内容が既に CLAUDE.md / rule / hook で実装済みでないか確認
2. 書き込み先の判断: 下記テーブルに従う
3. 変更を適用する

### Step 5: commit

変更を commit する。

## 書き込み先

| 対象 | 書き込み先 |
|---|---|
| 全プロジェクト共通の規範 | `~/.claude/CLAUDE.md`（global） |
| プロジェクト固有の規範 | `./CLAUDE.md`（カレントプロジェクト） |
| path-scoped 細則 | `./.claude/rules/<name>.md` |
| swat9013 の判断規則・価値観の追加/修正 | 判断規則集: `~/.claude/skills/swat-skills/skills/knowledge/engineering-judgment/`（同 skill の references/ 配下の正本 `values-source.md` を更新し SKILL.md 本文へ蒸留し直す）。テスト戦略なら同 `test-strategy/`、PR 品質なら同 `pr-quality/` |

dotfiles プロジェクトでは chezmoi-managed-files rule が `~/.claude/CLAUDE.md` → `dot_claude/CLAUDE.md` へのリダイレクトを担う。本 skill は chezmoi を意識しない。

判断規則集は蒸留物のため、insights の生ログ引用ではなく決定規則の形（条件 → 既定の選択）に変換して反映する。

## hygiene との使い分け

本 skill は CLAUDE.md 等の**内容**の更新を担う（insights レポート由来の新しい学び・規範の追加）。起点は手動の振り返り。
**構造**の最適化（重複排除・分離・削除。規範の意味は変えない）は hygiene skill の担当で、起点は SessionStart hook の 80 行閾値通知。

判断基準: 新しい規範を足したい → insights-reflect / 既存を減らし整えたい → hygiene。

## やってはいけないこと

- /insights の HTML を読まずに推測で提案を生成しない
- ユーザーのレビュー・方針確認をスキップしない（Historical insights フローは事前承認方式）
- 自動起動しない（手動起動専用）
