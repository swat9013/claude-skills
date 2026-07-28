---
name: contextual-commits
description: >-
  Adds structured action lines to commit bodies.
  Use when「コミットメッセージ」「commit body」「action lines」「コミットして」.
user-invocable: false
---

# Contextual Commits

Conventional Commits の subject line はそのまま維持し、commit body に **typed action lines** を追加して意思決定の痕跡を構造的に残す。diffに見えない「Why」「Why not」「What I learned」を記録する。

## フォーマット

```
type(scope): subject line（50文字目安）

action_type(scope): 説明文
action_type(scope): 説明文
```

- subject line: Conventional Commits 準拠（変更なし）
- body: 1行空行の後に action lines を記述
- 各 action line は `type(scope): description` 形式

> **注意**: subject line の `type` は Conventional Commits の type（`feat`, `fix`, `refactor` 等）。action line の `type` は本スキル定義の Action Types（`intent`, `decision`, `rejected` 等）。両者は別の語彙体系。

## Action Types

| Type | 用途 | 記録する情報 |
|------|------|-------------|
| `intent` | 意図・目的 | ユーザーが何を達成したかったか |
| `decision` | 選択した方針 | 何を選び、なぜそれが最適だったか |
| `rejected` | 却下した案 | 何を検討し、なぜ却下したか（**最重要**） |
| `constraint` | 制約条件 | 実装を制約した技術的・ビジネス的条件 |
| `learned` | 発見した事実 | 非自明な挙動、ドキュメントにない事実、APIの罠 |

**`rejected` が最も価値が高い** — 次のセッションのAIが同じ却下済みアプローチを再提案することを防ぐ。

## 判断基準

### 書くべきとき

- 設計判断を伴う変更（アーキテクチャ、ライブラリ選択、アルゴリズム選択）
- 代替案を検討して却下した場合
- 非自明な制約に遭遇した場合
- ドキュメントにない挙動を発見した場合
- ピボット（当初のアプローチを変更した場合）

### 書かないとき

- trivial な変更（typo修正、フォーマット、import整理）
- 機械的な変更（依存関係更新、生成コード）
- action lines に書ける有意な情報がない場合

### コンテキストがない場合（重要）

セッションの意思決定過程が不明な場合（例: `git add . && git commit` のみ依頼された場合）:
- **捏造禁止** — 推測で action lines を作らない
- diff から読み取れる事実のみで subject line を書く
- action lines は省略するか、確実に言えることだけ書く

## ルール

1. **subject line は Conventional Commits 準拠**（type、scope、日本語の説明文）
2. **action lines は body に記述**（subject line に混ぜない）
3. **scope はコンテキスト識別子**（ファイル名、モジュール名、概念名）
4. **1つの action line は1つの事実**（複数の情報を詰め込まない）
5. **diff の内容を繰り返さない**（diffに見えない情報のみ）
6. **rejected には必ず理由を含める**（`rejected(scope): 却下した対象 — 却下理由` 形式）
7. **英語の action type を維持**（`git log --grep` でのクエリ性）
8. **説明文は日本語で記述**（subject line、action lines ともに日本語）
9. **捏造禁止** — コンテキストがなければ action lines を省略する

## subject line の type / scope 選定指針

### type 選定（Conventional Commits）

1. ユーザー/シナリオ宣言文に「リファクタ」「機能追加」「修正」等のラベルが含まれていれば**それを一次根拠**とする
2. ラベルがない場合は **質的判定軸**で決定（量的基準=規模では決めない）:
   - 新規の **public API**（export 関数 / 公開クラス / 新エンドポイント等）を追加 → `feat`（規模に関わらず）
   - 観測可能な振る舞いを変えずに**既存実装の構造**を変える → `refactor`
   - 既存の**バグ**を修正 → `fix`
   - 内容変更を伴わないドキュメント編集 → `docs`
   - 内容変更を伴わない整形（空白・改行・linter 適合）→ `style`
   - 上記いずれにも該当しない設定/依存/雑務 → `chore`（**この行は最後の砦**、上の判定が決まったら使わない）
3. 1 と 2 が衝突する場合（例: 「リファクタ」宣言だが新規 public API も含む）はユーザー宣言を優先（ユーザーの意図表明が最強の根拠）

> **判定の落とし穴**: 「変更が小規模だから `chore`」「テストだけ追加だから `chore`」は誤り。public API が新たに公開されたら、行数 1 でも `feat`。`chore` は他に該当する type がない場合のみ。

### scope 選定

- **最も短い識別子で文脈が伝わる粒度**を採用
- 優先順位（影響範囲に応じて選ぶ）: 概念名 > モジュール名 > ファイル名
  - 単一ファイル変更・他に同名概念なし → ファイル名（例: `format`）
  - 複数ファイルにまたがる機能・概念変更 → 概念名（例: `cache`、`session`、`auth`）
- subject scope と action line scope は別系統でよい。action line scope は subject より**細かい粒度**を採用可（subject `cache` + action `cache-backend` / `redis` 等、中規模例参照）

## コミット実行手順

改行を含むメッセージは stdin 経由で `git commit -F -` に渡す:

```bash
git commit -F - <<'EOF'
subject line

action line 1
action line 2
EOF
```

`-F` はシェル展開を経ず stdin/ファイルから直接読むため、本文がシェル評価されない。`<<'EOF'` (クオート付き) で heredoc 内の変数展開・コマンド置換も無効化される。

> `-m` 複数指定は各 `-m` 間がダブル改行になり action lines が崩れる。`-F -` (stdin) を使うこと。

## 例

### 最小（1-2 action lines）

```
fix(api): 決済ゲートウェイのnullレスポンスを処理

learned(stripe-api): テストモードでは402時にbodyがnull、本番ではエラーオブジェクトが返る
```

### 中規模（全action type使用）

```
feat(cache): Redisベースのセッションストアを追加

intent(session): アクティブセッションのキャッシュでDB負荷を軽減
decision(cache-backend): TTLとpub/subサポートのためMemcachedではなくRedisを選択
rejected(cache-backend): Memcached — ネイティブのTTL期限切れ通知がない
constraint(session): 水平スケーリングのためクラスタモード対応が必須
learned(redis): maxmemory-policyのデフォルトがnoevictionのため、明示的にallkeys-lru設定が必要
```

### ピボット（アプローチ変更）

```
refactor(parser): 正規表現からAST抽出に切り替え

intent(parser): コードブロック検出の精度を向上
decision(parser): ネスト構造の処理にAST走査を採用
rejected(parser): 正規表現 — 深いネストのmarkdownでO(2^n)のバックトラッキング
constraint(parser): 既存のmarkdown-itプラグインとの互換性を維持する必要あり
learned(markdown-it): トークンストリームにパース済みfenceブロックがあり、独自走査が不要
```
