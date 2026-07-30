# モデル特性と prompt 設計指針

## このドキュメントの目的

Claude の主要モデル (opus / sonnet / haiku) の特性を明文化し、skill の `model:` frontmatter 選定と、本文 prompt の書き方の判断軸を提供する。

## モデル一覧と現行 ID

| モデル | ID (執筆時点) | 強み | 主な向き先 |
|---|---|---|---|
| Opus 5 | `claude-opus-5` | 推論深度 / 長文脈処理 / 複雑な意思決定 | 設計 / 仕様検討 / 多段推論 / 創造的タスク |
| Sonnet 5 | `claude-sonnet-5` | バランス (速度 × 推論 × コスト) | 通常の実装 / レビュー / 中程度の推論 |
| Fable 5 | `claude-fable-5` | — | — |
| Haiku 4.5 | `claude-haiku-4-5-20251001` | 高速 / 低コスト | 機械的判定 / 軽い分類 / 高頻度呼び出し |

モデル ID は変更されうる。最新 ID は `claude-code-guide` subagent で確認する ([sources](./sources.md))。

## モデル別 prompt 設計指針

### Opus

- **多段推論を引き出す書き方**: 前提を宣言し、反証を検討させ、代替案を要求する。
- **長い文脈を活かす**: 関連ファイル群を Read したうえで整合性を取らせるタスクが向く。
- **避ける**: 単純な穴埋めや lint チェックだけのタスク (コスト過剰)。

### Sonnet

- **構造化指示 + 少数例示 + 出力フォーマット指定**を組み合わせる。
- **役割宣言**は短く 1〜2 行で済ませる (opus ほど長文の前置きを必要としない)。
- 一般的な実装・レビュー作業はまず sonnet から検討する。

### Haiku

- **単純指示 + チェックリスト + 厳密な出力フォーマット**が最も効く。
- **推論誘導テンプレートは逆効果**: 「まず考えてから」「複数案を出して」のような思考誘導は、応答時間とコストを悪化させる割に効果が薄い。
- 1 段の判定 / 分類 / 抽出に絞る。複雑になりそうなら sonnet に上げる。

## `model:` frontmatter 選定基準

タスク負荷 × 呼び出し頻度 × コストの 3 軸で判断する。

| タスク類型 | 推奨モデル | 理由 |
|---|---|---|
| 仕様検討 / アーキテクチャ判断 | opus | 推論深度。低頻度 × 高価値で総コスト許容 |
| 実装 / 通常レビュー | sonnet | バランス。デフォルト選択 |
| コードフォーマット判定 / 軽い分類 | haiku | 高速・低コスト。高頻度呼び出しでも採算合う |
| 大量ファイルの構造的検査 (機械的) | haiku | 並列・反復で sonnet/opus はコスト過剰 |
| 創造的設計 / 複数案比較 | opus | 思考の幅 |

skill 単位で `model:` を固定するときは「最も負荷が高いケース」に合わせる。例: 通常は sonnet で十分でも、エッジケースで opus 級の判断が要るなら opus を指定する。

## thinking budget の使いどころ

- **厚く** (extended thinking 有効): 仕様検討 / 設計判断 / 複雑な debug。
- **薄く** (デフォルト): 機械的タスク / 既定パターンの実装 / 1 段判定。

extended thinking は opus / sonnet で意味が大きく、haiku では費用対効果が低い。

## コンテキスト管理 (Inferential コンポーネントの設計制約)

Inferential コンポーネント (skill / CLAUDE.md / rules / 評価系 skill) の設計は LLM の long-context 挙動に依存する。architecture.md の「Inferential 制約整合」観点はここで定義する現象を根拠とする。

### Lost in the Middle

長文脈 (おおよそ 20k tokens を超えるあたりから顕著) で **中盤に置いた情報が無視されやすい** 現象 (Liu et al. 2023)。冒頭と末尾は recall されやすく、中盤は精度が落ちる。U 字型の attention パターン。

### Primacy / recency bias

LLM は context の冒頭 (primacy) と末尾 (recency) を強く weight する。Anthropic / OpenAI / Google で共通して推奨されるプラクティス:

- 重要な指示・規範は **冒頭か末尾、または両方** に置く
- 長文ドキュメントを先に、query / instruction を末尾に配置 (recency bias 活用)
- XML タグ等で領域を明示的に区切る

### Context dilution

token 数の増加に伴い signal-to-noise 比が下がり、関連情報が他の token に希釈されて性能が落ちる現象 (context degradation / context rot とほぼ互換)。Lost in the Middle が「中盤の位置」由来であるのに対し、context dilution は「全体の signal 密度」由来であり独立した現象として扱う。

対策:

- 高 signal な token を選別し、関連性の低い token を削る
- doc を分割・階層化して必要部分だけ読ませる

### Navigable structure / index-first navigation

長文脈をまとめて読ませず、index → 必要部分だけ動的にロードさせる構造。

- 見出し階層を skimmable にする
- ディレクトリに index (README) を置き、agent に「最初に index、必要な doc だけ追加 Read」させる

### Inferential コンポーネント設計への適用

- skill / CLAUDE.md / rules を肥大化させない (context dilution 回避)
- 重要規範は冒頭と末尾に配置する (primacy / recency bias 活用)
- doc が大きくなったら分割し、index 経由で navigable に
- 詳細な slot 評価観点は [architecture](./architecture.md) を参照

## アンチパターン

- **haiku に多段推論を強要**: 「step by step で考えて」「複数案を比較して」を haiku に流すと、応答が冗長になり推論精度も上がらない。
- **opus に rote な lint チェックだけさせる**: コスト過剰。haiku か sonnet で十分な作業に opus を使わない。
- **モデル ID をハードコードして放置**: skill の `model:` に書いた ID が deprecated になっても気付かない。定期的に `claude-code-guide` で確認する ([sources](./sources.md))。
- **プロンプトと指定モデルのミスマッチ**: opus 用に書いた多段推論プロンプトを haiku モデルで動かしても効かない。本文と `model:` は常にペアで設計する。

## 更新トリガー

以下のいずれかが発生したら本ドキュメントを更新する。

- 新モデルの発表 (世代が上がったら本表の ID を差し替える。`skill.md` の `model` 行にも同じ ID 一覧があるので対で直す)
- 既存モデルの ID 変更
- 既存モデルの deprecation 通知
- 公式 prompt engineering ガイドの大きな更新
- Long-context 挙動 (Lost in the Middle / context dilution 等) に関する主要研究 / ベンダー doc の更新

確認方法: `claude-code-guide` subagent → 不足あれば `web-research` ([sources](./sources.md))。

## 参照

- 思想的バックグラウンド (Lost in the Middle / primacy-recency bias / context dilution / navigable structure の典拠): [references](./references.md)
