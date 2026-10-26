# モデル特性と prompt 設計指針

## このドキュメントの目的

Claude の主要モデル (fable / opus / sonnet / haiku) の特性を明文化し、skill の `model:` frontmatter 選定と、本文 prompt の書き方の判断軸を提供する。

## モデル一覧と現行 ID

上位から並べる。

| モデル | ID (執筆時点) | 強み | 主な向き先 |
|---|---|---|---|
| Fable 5.1 | `claude-fable-5-1` | 最高能力 (frontier)。長時間の自律実行で計画を切り替え・自己チェックできる / vision (図表・スクリーンショットからの構造抽出) | 数時間〜数日規模の agentic タスク / 大規模移行 / 前例のない複雑さ・曖昧さを持つ問題 |
| Opus 5.5 | `claude-opus-5-5` | Fable に迫る推論深度を Fable の 4 割ほどの単価で。**公式が推す既定の上位選択** | 設計 / 仕様検討 / 多段推論 / 創造的タスク |
| Sonnet 5.5 | `claude-sonnet-5-5` | バランス (速度 × 推論 × コスト) | 通常の実装 / レビュー / 中程度の推論 |
| Haiku 5.5 | `claude-haiku-5-5` | 最速 / 最低単価 | 分類 / 抽出 / routing / 大量・低レイテンシの subagent 作業 |

**単価の段差** (選定判断に効くのは絶対額でなく比): **Fable ≒ Opus × 2.5、Opus ≒ Sonnet × 2、Sonnet ≒ Haiku × 20** (2026-10 時点の公式 pricing)。Haiku 5.5 だけは prompt の長さで単価が変わり、100k token を超える prompt では Sonnet との差が 4 倍まで縮む — 長い入力を読ませる仕事は、Haiku に下ろしても安くなる幅が小さい。絶対額は改定されるので本表に書かない ([sources](../../../knowledge/claude-config-reference/references/sources.md) 経由で確認する)。

**context window / max output は Fable と Opus で同等** (1M / 128k)。両者の差は「扱える長さの上限」ではなく、能力と単価にある — 長い入力を読ませたいだけなら Fable に上げる理由にならない。

モデル ID は変更されうる。最新 ID は `claude-code-guide` subagent で確認する ([sources](../../../knowledge/claude-config-reference/references/sources.md))。本節と以降の特性・使い分けは 2026-07-30 に公式一次情報 (anthropic.com のモデル発表 / platform.claude.com の choosing-a-model・pricing・effort・prompting ガイド) で確認し、Fable 5.1 / Opus 5.5 の行は 2026-09-29 に公式の migration guide / prompting ガイドで確認した。Sonnet 5.5 / Haiku 5.5 の行と単価比は 2026-10-08 に公式の models overview・pricing・model-config で確認した (両モデルの prompting ガイドの差分は、まだ [models/](./models/) に起こしていない)。

## 文面の基準モデル

指示文をどのモデルに合わせて書くかの基準。本書は基本的に「どのモデルを選ぶか」を扱うが、この基準はモデル ID の表と同時に動くので本書に置く。**現在は Opus 5.5** (差分は [models/opus-5-5.md](./models/opus-5-5.md))。

**対象モデルの決め方** (write-for-harness の各所はここを引く): 実行モデルを宣言している指示文 (skill の `model:` / agent 定義の `model:` や Agent tool の `model` 引数で指名する subagent への依頼文) はそのモデル。宣言の無い指示文 (`model:` 未指定の skill / CLAUDE.md / rules / hook 注入文) は文面の基準モデル。`opus` / `sonnet` のような alias は、解決先のモデル (`claude-code-guide` で確かめる) に合わせる。

基準は常に最新の Opus に置く。ただし移すのは、新しい Opus の差分 reference を [models/](./models/) に新設するのと同じ変更で行う — 差分の無いモデルを基準にすると、そのモデル固有の対策が空白のまま文面が書かれる。

## モデル別の向き先と避ける帯

梯子は **haiku → sonnet → opus → fable の 4 段**。既定は sonnet、深い推論が要るなら opus、opus でも届かない規模・曖昧さに限って fable。1 段ずつ上げ、上げる根拠 (下位で何が落ちるか) を言えないなら上げない。

**prompt 本文の書き方 (文面) の正本は [writing.md](./writing.md)** — 共通原則をそこに、モデル別の差分を [models/](./models/) に置いた。本書は「どのモデルを選ぶか」に効く特性 (と上の文面の基準モデル) だけを扱い、文面の規則は書かない (二重管理を避ける)。

### Fable

- **thinking は常時 on** (無効化不可)。深さは `effort` で制御する → 「effort の使いどころ」。
- **Opus 向け prompt をそのまま流用できない**: 上げるときは既存 prompt / skill の文面監査がコストとして付く (監査観点は [models/fable-5.md](./models/fable-5.md) と [models/fable-5-1.md](./models/fable-5-1.md))。互換範囲は公式が明示しておらず、それ以外は実測で確かめる。
- **避ける**: 短時間で終わる定型作業。単価が Opus の約 2.5 倍で、差が出るのは長時間・高曖昧性の帯に限られる。ただし Fable 5.1 は `low` effort でもタスク当たりのコストで Opus / Sonnet に匹敵することが多いので、下位モデルを高い effort で回す案と比べる。

### Opus

- **thinking は常時 on** (Opus 5.5。無効化不可)。深さは `effort` で制御し、既定は `medium` → 「effort の使いどころ」。
- **長い文脈を活かす**: 関連ファイル群を Read したうえで整合性を取らせるタスクが向く。
- **迷ったときの上位選択はここ**: 公式も「不確実ならまず Opus」と位置づける。ベンチマークによっては Opus が Fable の最良結果を約 1/3 のコストで上回る (Opus 5 / Fable 5 の世代で確認)。
- **避ける**: 下方向は単純な穴埋めや lint チェックだけのタスク (コスト過剰)。上方向は数時間〜数日の自律実行や前例のない複雑さ (Fable の領分であり、Opus に投げても計画の切り替えが続かない)。

### Sonnet

- 一般的な実装・レビュー作業はまず sonnet から検討する。
- **思考不足は上げる理由にならない場合がある**: 浅い推論を観測したら、model を 1 段上げる前に effort を上げて解消するか判断する (理由は [models/sonnet-5.md](./models/sonnet-5.md) の effort 厳守)。

### Haiku

- **単純指示 + チェックリスト + 厳密な出力フォーマット**が最も効く (Haiku 5.5 には公式のモデル別 prompting ガイドがあるが、差分 reference は [models/](./models/) にまだ無いので、haiku の文面要点は当面ここが正本)。
- **`low` effort は長い agent prompt で早期停止しやすい**: 検索の省略・確認の省略・作業途中での停止が起きる。検索や複数手順を伴う subagent は `medium` に置く。
- **推論誘導テンプレートは逆効果**: 「まず考えてから」「複数案を出して」のような思考誘導は、応答時間とコストを悪化させる割に効果が薄い。
- 1 段の判定 / 分類 / 抽出に絞る。複雑になりそうなら sonnet に上げる (梯子は 1 段ずつ。haiku から直接 opus / fable へ飛ばさない)。

## `model:` frontmatter 選定基準

タスク負荷 × 呼び出し頻度 × コストの 3 軸で判断する。

| タスク類型 | 推奨モデル | 理由 |
|---|---|---|
| 数時間〜数日の自律実行 / 大規模移行 / 前例のない曖昧さ | fable | 長時間の計画切り替え・部分委譲・自己チェックが前提。ここだけが opus で届かない帯 |
| 下位で読み違えた図表・画面の再挑戦 | fable | vision 精度 (図から数値を正確に読む / 画面からソースを再構成する水準)。ただし opus が落ちる証拠を得てから上げる — 画像を扱うだけでは根拠にならない |
| 仕様検討 / アーキテクチャ判断 | opus | 推論深度。低頻度 × 高価値で総コスト許容。fable に迫る性能を fable の 4 割ほどの単価で出す |
| 創造的設計 / 複数案比較 | opus | 思考の幅 |
| 単発の実装 / 通常レビュー | sonnet | バランス。デフォルト選択 |
| 呼び出し元のセッションと同じ深さで動くこと自体が要件の agent 定義の役割 | `inherit` | 選ぶ条件は [agent 定義](./components/agent.md) の「model と effort の置き場」が持つ |
| コードフォーマット判定 / 軽い分類 | haiku | 高速・低コスト。高頻度呼び出しでも採算合う |
| 大量ファイルの構造的検査 (機械的) | haiku | 並列・反復で上位モデルはコスト過剰 |

最重量帯を機械的に fable へ寄せない。**単発の重い推論は opus、実行が長く伸びて計画の組み替えが要るものが fable** という分け方をする (公式も不確実なら opus を既定に置く)。

skill 単位で `model:` を固定するときは「最も負荷が高いケース」に合わせる。例: 通常は sonnet で十分でも、エッジケースで opus 級の判断が要るなら opus を指定する。梯子の天井は fable だが、1 段上げるコストは単価差そのものなので、上げる根拠を書けないなら上げない。

## effort の使いどころ

思考の深さは `effort` で制御する。手動の thinking budget は fable / opus / sonnet で受け付けられず、Haiku 5.5 も思考量の制御は effort に移った。

- **上げる**: 仕様検討 / 設計判断 / 複雑な debug。
- **下げる** (`low` / `medium`): 機械的タスク / 既定パターンの実装 / 1 段判定。

既定の段はモデルで違う (Claude Code では Opus 5.5 / Sonnet 5.5 / Haiku 5.5 が `medium`、それ以外は `high`。API の既定とは一致しないモデルがある) ので、skill の `effort:` や agent 定義の `effort:` で明示する。Haiku 5.5 は 5 段すべてに対応するが、深い推論を任せる帯ではない。

**並列・無人の長い実行で model と effort を固定するとき**は、段ごとの止まり方で選ぶ。

- Sonnet 5.5 は `low` / `medium` の長い agentic task で、終える前に止まって確認を求めやすい (公式の prompting ガイド)。無人で走らせるなら `high` が前提で、`high` は tool 呼び出しを増やすので、turn 数に比例する cache_read で単価の差が縮む
- Opus 5.5 の `low` は coding の評価で `medium` に近い結果を低い費用で出し、公式は段に結び付いた早期停止を注意していない。ただし公式の記述ではなく 1 つの agent 定義での実測として、手順の長い定義を `low` で並列に回すと、作業量が小さいと判断して決められた step (playbook の invoke やレビュー) を省く実行が出た。`medium` で省略が止まるかはまだ確かめていない。step を省かれると困る定義は、`low` を避けて `medium` に置くか、`low` のまま省略を名指しして止める文面を置き、どちらも実測で確かめる
- 段によらず、途中経過を書いて turn を終える止まり方は Opus 5.5 にもある。これは effort では防げないので、文面で止まり方を名指しする ([models/opus-5-5.md](./models/opus-5-5.md) の「無人の長い実行は、止まり方を名指しして防ぐ」)

fable・Opus 5.5・Sonnet 5.5 は **thinking を無効化できない** (adaptive thinking が常時 on。Haiku 5.5 は無効化を受け付ける)。「thinking を切って安く回す」は選択肢にならないので、浅く回したいなら effort を下げ、それでも重いなら model を 1 段下げる。

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
- **上位モデルに rote な lint チェックだけさせる**: コスト過剰。haiku か sonnet で十分な作業に opus / fable を使わない。
- **「上位ほど良い」で fable を既定にする**: fable は opus の約 2.5 倍単価で、差が出るのは長時間・高曖昧性の帯に限られる。上位側の既定は opus に置き、fable は帯を名指しできるときだけ選ぶ。
- **モデル ID をハードコードして放置**: skill の `model:` に書いた ID が deprecated になっても気付かない。定期的に `claude-code-guide` で確認する ([sources](../../../knowledge/claude-config-reference/references/sources.md))。
- **プロンプトと指定モデルのミスマッチ**: opus 用に書いた多段推論プロンプトを haiku モデルで動かしても効かない。逆に推論を本文に書き出させる誘導を fable / Opus 5.5 にそのまま流すと refusal を踏む。本文と `model:` は常にペアで設計する (文面側の規則は [writing](./writing.md))。

## 更新トリガー

以下のいずれかが発生したら本ドキュメントを更新する。

- 新モデルの発表 (世代が上がったら本表の ID を差し替える。ID 一覧は本書が唯一の正本なので他 doc の追随は不要)
- **tier の増減** (既存の上位/下位に新しい段が入る)。この場合は ID 差し替えでは済まず、「モデル別の向き先と避ける帯」「`model:` 選定基準」「effort の使いどころ」「アンチパターン」の各節に書かれた**梯子の記述をすべて組み替える**。特性が未確認のまま一覧行だけ足すのは未更新と同義
- 新しい Opus の差分 reference を [models/](./models/) に新設したとき (「文面の基準モデル」をそのモデルへ移す)
- 既存モデルの ID 変更
- 既存モデルの deprecation 通知
- 公式 prompt engineering ガイドの大きな更新 (文面の正本は [writing.md](./writing.md) 側なので、本書と対で見直す)
- Long-context 挙動 (Lost in the Middle / context dilution 等) に関する主要研究 / ベンダー doc の更新

確認方法: [sources](../../../knowledge/claude-config-reference/references/sources.md) の「エスカレーション戦略」の順で当たる。

## 参照

- 思想的バックグラウンド (Lost in the Middle / primacy-recency bias / context dilution / navigable structure の典拠): [references](./references.md)
