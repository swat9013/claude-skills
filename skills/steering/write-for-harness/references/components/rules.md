# rules/*.md のレビュー観点

## いつこの doc を Read するか

`<repo>/.claude/rules/*.md` または `~/.claude/rules/*.md` を新設・編集する前に Read する。rules の固有性は **frontmatter `paths:` による path-scoped trigger**。`paths:` が無い rules は CLAUDE.md と等価で常時ロードされ、rules の存在意義が無い。

## 責務

rules ファイルは CLAUDE.md から分割される、発火条件付きの細則。

- **目的**: 特定領域の詳細規約を `paths:` で絞ってロードし、無関係セッションで high-signal を薄めない。
- **対象外**: 一般指針 (それは CLAUDE.md 直下の責務 → [claude-md](./claude-md.md))、自動実行 (それは hook の責務 → [hook](../../../../knowledge/claude-config-reference/references/hook.md))。

## 仕様

- 単一の markdown ファイル。frontmatter `paths:` で発火条件 (対象ファイルの glob、カンマ区切りで複数指定可) を宣言する。該当ファイル編集時のみコンテキストに読み込まれる。
  - ○ `paths: "skills/**/SKILL.md, .claude-plugin/plugin.json"` のように対象を絞る
  - × `paths:` 欠落 → 全セッションで毎回ロードされ CLAUDE.md と等価
- 分け方は、まず `paths:` (各項の当たる path 集合)、次に関心で分ける (チェックリストの「分ける順」の項)。

公式 spec は [sources](../../../../knowledge/claude-config-reference/references/sources.md) の一次情報源の表「CLAUDE.md の書き方」行 (`paths:` frontmatter 仕様) から辿る。

## チェックリスト

rules ファイルを新設・編集する前に確認する。

rules 固有 (= `paths:` で path-scoped にすることで価値が出る) の項目のみここに残す。aspirational / global vs project / recurring failure のような汎用判断は [claude-md](./claude-md.md) のチェックリストに従う。

- [ ] frontmatter `paths:` で発火条件を宣言したか (無いなら rules ではなく CLAUDE.md に置く)
- [ ] **既存自動化との重複回避**: 追加しようとする細則が既に hook / settings の `permissions` で defend されていないか `grep -r` で確認したか (重複は drift と context bloat の原因)。プロジェクト側と global (`~/.claude/`) の両方を見る
- [ ] `paths:` の glob が、各項の当たる path 集合と一致しているか — 狭すぎれば当たる編集で載らず、広すぎれば当たらない編集でも載る。一部の項だけが狭い path (特定の skill・特定の file) に当たるなら、その path を `paths:` に持つ別の rules へ切り出す
- [ ] 分ける順を守ったか — まず `paths:` (各項の当たる path 集合) で分け、同じ path 集合の中では関心 (テーマ) で分ける。同じ `paths:` で同じ関心の rules が複数あれば 1 つに統合する
- [ ] 他 rules / CLAUDE.md 本体と同じ判断を述べていないか (重複は片方しか更新されず drift する) — rules 同士なら、判断を 1 つの rules に置いて `paths:` を両方の path の和にする。和に広げると当たらない項が置き先の rules にあるなら、その判断だけを持つ rules を新設する
- [ ] ファイル名が `paths:` のスコープを端的に表すか (kebab-case 推奨)。ADR など書き換えない記録がファイル名で引いている rules は、中身が名前とずれても改名・統合で消さず、見出しを中身に合わせる。統合するときは、記録がファイル名で引いている側を残すファイルにする — 改名・統合で消すと、記録の参照が実在しないファイルを指したまま残る

## アンチパターン

rules 固有のアンチパターンに絞る。aspirational / 解決済み問題の残骸など CLAUDE.md と共通の罠は [claude-md](./claude-md.md) を参照。

- **`paths:` 欠落の rules**: 発火条件なしでは CLAUDE.md と等価で全セッションで常時ロードされる。rules に置く意味が無い。
- **多目的 rules ファイル**: 「common.md」「misc.md」のような無焦点ファイルは `paths:` を 1 つに絞れず、結局広いスコープでロードされる。
- **過度に細分化された rules**: 同じ `paths:` で同じ関心の rules が複数発火すると、読むファイルが増えるだけで分けた意味が無い (直し方はチェックリストの「分ける順」)。
- **CLAUDE.md と rules の二重管理**: 同じ規約が両方に書かれていると編集時に片方しか更新されず drift する。

## 参照

- 共通: [models](../models.md) (rules の長さがモデルの context 利用に与える影響) / [sources](../../../../knowledge/claude-config-reference/references/sources.md) (公式仕様の引き方)
- 関連: [claude-md](./claude-md.md) (rules の分割元である CLAUDE.md 本体のレビュー観点)
- 公式: Claude Code memory ドキュメント (URL は [sources](../../../../knowledge/claude-config-reference/references/sources.md) 経由で確認)
