# コンポーネントアーキテクチャ

## 位置づけ

Claude Code のハーネス（guide + sensor）を実装する 5 コンポーネント (skill / hook / CLAUDE.md / settings / rules) を、編集前の Claude が **どの slot に規制を入れるか** を意思決定するための文書。本文書は Martin Fowler の harness engineering / sensors の枠組みを参考にした **slot**（guide/sensor × Computational/Inferential）と、Claude Code の **実装機構** (コンポーネント) を分離して扱う。

読み方:

1. 入れたい規制から **slot** を 1 つ選ぶ (下の「Slot 選択フロー」)
2. その slot を埋める **実装機構** を選ぶ (下の「4 slot 表」)
3. 該当コンポーネントの `.md` を Read してチェックリストに従う

## 4 slot 表

Slot は 2 軸の組み合わせ:

- **Computational / Inferential**: 規制が決定論的なパターン処理 (C) か、semantic 判断 (I) か
- **Guide / Sensor**: 規制が行動前の事前 sanction (Guide) か、行動後の観測・介入 (Sensor) か

|              | **Guide (事前)**                                                                       | **Sensor (事後)**                                                                  |
|--------------|---------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------|
| **Computational** | `settings.permissions.allow` / `hook` (SessionStart / UserPromptSubmit inject)        | `settings.permissions.deny` / `hook` (PreToolUse deny / PostToolUse linter check) |
| **Inferential**   | `skill` (proactive 用途) / `CLAUDE.md` / `rules`                                       | `skill` (evaluative 用途。例: review-code skill / security-review skill)              |

注記:

- **skill は dual-use**。proactive (事前に手順をロード) なら I/Guide、evaluative (事後に semantic 判断を返す) なら I/Sensor。同じ skill ファイルでも呼ばれ方で slot が変わる。
- **I/Sensor の auto-trigger** は `hook` (Stop / PostToolUse 等) + `skill` の組み合わせで実装する。手動呼び出しの review skill は単独で I/Sensor。
- **settings は機構ではなく configuration**。permissions パターンは C/Guide / C/Sensor を、`hooks` セクションは hook 機構の発火点を定義する。

## Slot 選択フロー

```
入れたい規制
   │
   ├─ semantic な判断が必要？
   │     │
   │     ├─ YES → Inferential
   │     │       │
   │     │       ├─ 事前にコンテキストとして注入したい → I/Guide
   │     │       │      候補: skill (proactive) / CLAUDE.md / rules
   │     │       │
   │     │       └─ 行動後に評価したい → I/Sensor
   │     │              候補: skill (evaluative)
   │     │              自動発火: hook + skill
   │     │
   │     └─ NO (パターンマッチ / 構文で足りる) → Computational
   │             │
   │             ├─ 事前 sanction (allow / 注入) → C/Guide
   │             │      候補: settings.allow / hook (SessionStart inject)
   │             │
   │             └─ 行動後に止める / 検査する → C/Sensor
   │                    候補: settings.deny / hook (PreToolUse deny / PostToolUse check)
   │
   └─ 選んだ slot のコンポーネント doc へ
         (skill / hook / claude-md / settings / rules)
```

判断のコツ:

- 「semantic か?」の判定: 規制を破ったかどうかを文字列パターンや構文木で確定できるなら Computational。ユーザーの意図 / コードの意味の解釈が必要なら Inferential。
- 「事前か事後か?」の判定: 行動を起こす前に止められる / 文脈を増やせるなら Guide。行動の結果を観測して反応するなら Sensor。
- Guide で十分なら Guide を優先する (事前に防ぐ方が安く確実)。
- Inferential は確率的なので、絶対に守らせたいものは Computational 側で重ねる (例: `settings.deny` + CLAUDE.md 規範)。
- Inferential はコンテキスト肥大に脆弱。Lost in the Middle (中盤情報の見落とし) や context dilution (信号希釈による精度低下) で steering 力が落ちる。対策は (1) 重要規範を冒頭と末尾に置く (primacy / recency bias 活用)、(2) 見出し階層で navigable structure を確保し index-first に読ませる、(3) 肥大時は分割する → [models](./models.md)

## steering loop との関係

**steering loop** はハーネス**外**の人間のメタプロセス（regulator は人間、ADR 0011）。guide / sensor（＝ハーネス）を、人間が sensor の読みをもとに反復改善する。CONTEXT.md の新語彙では harness = guide + sensor で、steering loop は外側。

steering loop の sensor は components の機構を借用して実装する:

- 観測 (sensor 機能): `hook` (Stop など) で session 終了を捉え、`skill` (review-session 系) で semantic 評価。これが I/Sensor slot を埋める形になる。
- 反映: 出力から components (skill / CLAUDE.md / rules / hook / settings) を書き換える。sensor は本来 fix しないが、self-maintenance は loop なので書き換えまで担う。

したがって architecture (本文書) は「regulated 側 = components の slot 配置」に閉じる。regulator 側 (self-maintenance) の設計判断は別文脈 (CONTEXT.md / 各 skill) を参照する。

## 各 component doc を I/Guide として評価する観点

各コンポーネント doc (skill.md / hook.md / claude-md.md / settings.md / rules.md) は、編集前の Claude にとっての **I/Guide** として機能する。proactive にロードされ、編集行動を semantic に steering する。

別セッションで component doc をレビューする際は、I/Guide としての品質を以下の観点で評価する:

- **trigger 接続**: 冒頭で「いつこの doc を Read すべきか」が明確か。汎用語のみだと過剰トリガー or 未起動になる
- **制約の具体性**: 制約条件が「do / don't の対」で実例を伴うか。抽象記述だけだとモデルは確率的にしか守らない
- **出口の明確化**: 他 doc / 公式 spec への link が明示されているか。出口がないと doc 内で完結しようとして肥大化する
- **navigable structure (部分読み許容)**: 見出し階層で必要部分だけ読めるか (index-first navigation を支援するか)。冒頭から線形に読まないと意味が通らない構造は I/Guide として弱い
- **drift 回避**: 機械検査が可能な項目を散文で重複記述していないか。重複は片方しか更新されず drift する
- **過剰トリガー回避**: `description` 相当の冒頭が、無関係な場面で参照を誘発する曖昧記述になっていないか
- **Inferential 制約整合**: doc の総量や中盤への重要情報配置が Lost in the Middle / context dilution による steering 力低下を招いていないか。重要規範は冒頭と末尾に置き (primacy / recency bias 活用)、肥大化したら分割 / navigable structure 化で逃がす → [models](./models.md)
- **モデル特性整合**: 該当する場合 (skill.md / claude-md.md) は、モデルの先頭バイアスや prompt 形式と整合しているか → [models](./models.md)

このリストは別セッションで各 component doc をレビューする際の入力。レビュー結果に応じて、既存の `責務 / 仕様 / チェックリスト / アンチパターン / 参照` 構造のままで I/Guide 観点を満たせるなら改善 edit のみ、構造自体が I/Guide に合わないなら章立てを再設計する。

## 更新トリガー

以下のいずれかが発生したら本文書を更新する。

- ハーネスのコンポーネント数 (現 5) が増減する
- Fowler の harness / sensors の枠組みが更新される (鮮度確認は [sources](./sources.md) のエスカレーション順)
- 新しい slot 候補 (例: scheduled sensor / out-of-band guide) が発見される
- self-maintenance の実装機構が変わり、steering loop の説明が陳腐化する

## 参照

- [README](./README.md): docs/ 全体の索引と利用方針
- 各 component doc: [skill](./skill.md) / [hook](./hook.md) / [claude-md](./claude-md.md) / [settings](./settings.md) / [rules](./rules.md)
- 共通: [models](./models.md) (モデル特性) / [sources](./sources.md) (公式情報源と鮮度確認) / [references](./references.md) (思想的バックグラウンド: Fowler の harness engineering / sensors)
