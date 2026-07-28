# rules/*.md のレビュー観点

## いつこの doc を Read するか

`<repo>/.claude/rules/*.md` または `~/.claude/rules/*.md` を新設・編集する前に Read する。rules の固有性は **frontmatter `paths:` による path-scoped trigger**。`paths:` が無い rules は CLAUDE.md と等価で常時ロードされ、rules の存在意義が無い。

## 責務

rules ファイルは CLAUDE.md から分割される、発火条件付きの細則。

- **目的**: 特定領域の詳細規約を `paths:` で絞ってロードし、無関係セッションで high-signal を薄めない。
- **対象外**: 一般指針 (それは CLAUDE.md 直下の責務 → [claude-md](./claude-md.md))、自動実行 (それは hook の責務 → [hook](./hook.md))。

## 仕様

- 単一の markdown ファイル。frontmatter `paths:` で発火条件 (対象ファイルの glob、カンマ区切りで複数指定可) を宣言する。該当ファイル編集時のみコンテキストに読み込まれる。
  - ○ `paths: "dot_claude/skills/**, **/SKILL.md"` のように対象を絞る
  - × `paths:` 欠落 → 全セッションで毎回ロードされ CLAUDE.md と等価
- 1 ファイル 1 関心が原則 (例: `chezmoi-managed-files.md` は chezmoi source 編集規約に限定)。

公式 spec は [sources](./sources.md) の memory / Subagents 節 (`paths:` frontmatter 仕様) から辿る。

## チェックリスト

rules ファイルを新設・編集する前に確認する。

rules 固有 (= `paths:` で path-scoped にすることで価値が出る) の項目のみここに残す。aspirational / global vs project / recurring failure のような汎用判断は [claude-md](./claude-md.md) のチェックリストに従う。

- [ ] frontmatter `paths:` で発火条件を宣言したか (無いなら rules ではなく CLAUDE.md に置く)
- [ ] **既存自動化との重複回避**: 追加しようとする細則が既に `~/.dotfiles/dot_claude/hooks/` / `~/.dotfiles/dot_claude/settings.json` / 他 rules / CLAUDE.md で defend されていないか `grep -r` で確認したか (重複は drift と context bloat の原因)
- [ ] `paths:` の glob が編集対象ファイルを正確に捉えているか (狭すぎて未起動 / 広すぎて常時ロードのどちらでもない)
- [ ] 1 ファイル 1 関心の責務分割になっているか (1 つの `paths:` で発火する内容が単一テーマか)
- [ ] CLAUDE.md 索引との一貫性: 新規 rules は CLAUDE.md の索引表に追加したか / 削除 rules は索引から外したか
- [ ] 他 rules / CLAUDE.md 本体と内容が重複していないか (重複は片方しか更新されず drift する)
- [ ] ファイル名が `paths:` のスコープを端的に表すか (kebab-case 推奨)

## アンチパターン

rules 固有のアンチパターンに絞る。aspirational / 解決済み問題の残骸など CLAUDE.md と共通の罠は [claude-md](./claude-md.md) を参照。

- **`paths:` 欠落の rules**: 発火条件なしでは CLAUDE.md と等価で全セッションで常時ロードされる。rules に置く意味が無い。
- **多目的 rules ファイル**: 「common.md」「misc.md」のような無焦点ファイルは `paths:` を 1 つに絞れず、結局広いスコープでロードされる。
- **CLAUDE.md 索引との不整合**: 新規 rules を作ったのに索引に追加せず、Claude が存在を認識しない。
- **過度に細分化された rules**: 1 ファイル数行の rules が同じ `paths:` で多数発火すると、context は節約できず索引だけが肥大化する。関連トピックは統合する。
- **CLAUDE.md と rules の二重管理**: 同じ規約が両方に書かれていると編集時に片方しか更新されず drift する。

## 参照

- 共通: [models](./models.md) (rules の長さがモデルの context 利用に与える影響) / [sources](./sources.md) (公式仕様の引き方)
- 関連: [claude-md](./claude-md.md) (rules を索引する CLAUDE.md 本体のレビュー観点)
- 公式: Claude Code memory ドキュメント (URL は [sources](./sources.md) 経由で確認)
