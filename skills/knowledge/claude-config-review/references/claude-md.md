# CLAUDE.md のレビュー観点

## いつこの doc を Read するか

`CLAUDE.md` (global / project / project-local) を新設・編集する前に Read する。1 回限りの指示は対象外。発火条件付きで絞れる細則は [rules](./rules.md) 側へ。

## 責務

CLAUDE.md は Claude Code がセッション開始時に常時読み込む指示ファイル。

- **目的**: モデルが従うべき不変の規範 / プロジェクト固有制約 / よく忘れる注意点を、毎セッション自動的に注入する。
- **対象外**: 1 回限りの指示 (それは prompt で十分)、自動実行 (それは hook の責務 → [hook](./hook.md))、特定タスク手順 (それは skill の責務 → [skill](./skill.md))、特定ファイル編集時のみ必要な細則 (それは rules の責務 → [rules](./rules.md))、機械的に検査可能な制約 (それは linter / settings の責務 → [settings](./settings.md))。

## 仕様

### memory 階層

Claude Code の memory は最大 4 層が階層的にロードされる。

| 層 | パス | 用途 |
|---|---|---|
| enterprise | プラットフォーム依存の管理パス | 組織横断ポリシー |
| user (global) | `~/.claude/CLAUDE.md` | ユーザー個人のグローバル規範 |
| project | `<repo>/CLAUDE.md` (cd 下のサブディレクトリ CLAUDE.md も含む) | リポジトリ共有規範 |
| project-local | `<repo>/CLAUDE.local.md` | VCS 管理外の個人 override |

### import 構文

本文中の `@path/to/file` は該当ファイルをロード時に展開する公式手段。CLAUDE.md 本体を肥大化させずに細則を切り出せる。

```
@.claude/rules/coding-style.md
```

発火条件付きで細則をロードしたい場合は import ではなく rules ファイル (frontmatter `paths:`) を使う → [rules](./rules.md)。

### ロードコストと長さ

- markdown 形式。frontmatter は不要 (rules と異なる)。
- セッション開始時に全行がコンテキストにロードされるため、量がそのままコスト・ノイズに直結する → [models](./models.md)。
- **重要規範ほど先頭側に置く**。具体: セッション開始直後に判断する規範 (言語選定 / asking policy 等) は最初の 2 セクション以内に配置。逆例: `Revision Policy` のようなメタ規約は末尾に置く。モデルの先頭バイアス特性は [models](./models.md)。

### 機械検査の委譲

行数 / 索引一貫性 / 単純重複など、定量化できる制約は linter (CI / pre-commit) に逃がす。CLAUDE.md 本体に「~ 行を超えないこと」と書いても確率的にしか守られず、コストとノイズだけが残る。チェックリストには人間判断が必要な項目のみを残す。

## チェックリスト

CLAUDE.md を新設・編集する前に確認する。**機械検査で済む項目は前節「機械検査の委譲」に従って linter に逃がし、ここには載せない**。

- [ ] **recurring failure の特定**: 新規ルールを追加する前に、対応する反復失敗事例を memory / incident 記録 / PR レビュー履歴のいずれかで 1 件以上特定する。特定できなければ aspirational ルール → 追加しない
- [ ] **責務分離 (global vs project)**: グローバルにプロジェクト固有制約を入れていないか / プロジェクトに環境横断規範を書いていないか
- [ ] **責務分離 (CLAUDE.md vs rules)**: 発火条件付きで絞れる規約は rules に移したか → [rules](./rules.md)。全セッションで必要な汎用規範のみ CLAUDE.md に残す
- [ ] **責務分離 (規範 vs 自動化)**: hook / settings / linter で決定論的に守らせられる項目を「気をつけろ」と書いていないか。書くなら自動化を実装する方が優先 ([hook](./hook.md) / [settings](./settings.md))
- [ ] **既存自動化との重複回避 (逆向きチェック)**: 追加しようとする規範が既に `~/.dotfiles/dot_claude/hooks/` (PreToolUse / PostToolUse) / `~/.dotfiles/dot_claude/settings.json` (permissions) / `~/.dotfiles/dot_claude/rules/` で機械的に defend されていないか `grep -r` で確認したか。defend 済みなら追加しない — feedforward 重複は確率的な再現と context bloat にしかならない → [models](./models.md)
- [ ] **モデル先頭バイアスへの配置**: 重要規範を先頭側に寄せたか → [models](./models.md)
- [ ] **import への切り出し**: 本文に直書きする必然性のない細則は `@path` import で外出ししたか
- [ ] **古い情報の除去**: 既に解決した bug への暫定対処 / 撤去済み機能への参照が残っていないか

## アンチパターン

- **抽象的な aspirational ルール**: 「丁寧に書く」「ベストを尽くす」のような測定不能な指示はモデル挙動を変えない。具体的な行動規範 (「assumption を宣言してから書く」) に置き換える。
- **自動化可能な制約を「気をつけろ」で済ます**: モデルは確率的にしか守らない。決定論的に守らせたいなら hook / linter / settings deny で実装する。CLAUDE.md は「自動化で代替できないもの」専用。
- **プロジェクト固有制約をグローバルに混入**: 別プロジェクトに不要な制約まで毎セッションロードされる。
- **発火条件で絞れる規約を CLAUDE.md 直書き**: rules / `paths:` で絞れる規約まで CLAUDE.md に置くと無関係なセッションで毎回ロードされ、high-signal が薄まる。
- **rules / import への分割を怠る**: 同種の規約を CLAUDE.md 直下と rules / import 先で二重管理すると drift する。
- **解決済み問題の残骸**: 過去の bug 回避ルールが永久に残る。定期的な見直しが必要。

## 参照

- 共通: [models](./models.md) (context window とロード量 / 先頭バイアス) / [sources](./sources.md) (公式仕様の引き方)
- 関連: [rules](./rules.md) (CLAUDE.md から分割される細則ファイル) / [hook](./hook.md) (自動化への委譲先) / [settings](./settings.md) (permission / hook 登録)
- 公式: Claude Code memory ドキュメント (URL は [sources](./sources.md) 経由で確認)
