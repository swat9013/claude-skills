---
name: principle-index
description: 原則 leaf 全件の適用条件を 1 行ずつ並べた索引。今の作業に当たる leaf を Read で読む。Use when「どの原則を読むべきか探す」「原則の一覧を見る」.
user-invocable: true
---

# 原則 leaf の索引

適用条件が今の作業に当たる leaf を Read で読む。当たる leaf が複数あれば全部読む。

leaf は本 file と同じディレクトリの `references/leaves/<name>.md` に置いてある (`<name>` は下の一覧が挙げる名前をそのまま使う)。skill 登録のない素の markdown なので、経路は Read だけ。

<!-- 編集者向け: 以下の一覧は leaf の frontmatter からの生成物。直すのは leaf 側の description で、手書きは scripts/gate/verify-principle-index-sync.py が落とす -->

## leaf 一覧

- **principle-ai-delegation-boundary** — AI エージェントへ自律実行を委譲する範囲を決めるとき、許可と禁止を設計するとき、人間へ返す境界を決めるときに適用する。禁止は副作用の有無で切り、委譲は AFK 境界で切る。
- **principle-artifact-register** — README・正本・蒸留版・spec / plan / PR 説明文・エージェント向け指示書といった文書を書くとき、記述を別文書へ移して正本を立て直すときに適用する。成果物の役割ごとに規範が逆になり、読者と正本の決め方はどの成果物にも共通で掛かる。
- **principle-automate-when-it-hurts** — 反復作業を自動化するか決めるとき、規約の遵守をどう守らせるか決めるとき、LLM 推論を script へ追い出すか判断するときに適用する。自動化は痛みの実測後、遵守は機械で保証する。
- **principle-balance-coupling** — モジュール / サービス境界の結合を評価するとき、境界を分割するか統合するか判断するときに適用する。integration strength / distance / volatility の 3 次元。
- **principle-build-vs-buy** — 自前実装と既製品が拮抗したとき、言語・フレームワーク・ライブラリを選定するとき、既製品を fork して維持するか・確立した仕組みを自前へ置き換えるか決めるときに適用する。既製品を既定に置き、選定軸をテスト容易性・可読性・構築の簡単さと上流への追従コストで切る。
- **principle-collective-ownership** — コードの所有範囲・レビューの目的・設計判断の記録先を決めるときに適用する。レビューは設計改善と知識共有の場、WHY は ADR に残す。
- **principle-comment-intent** — コメントや docstring を書くか迷うとき、変更の意図をどこに残すか決めるときに適用する。意図の 4 側面 (仕様 / 実現方法 / 変更の動機 / 非自明な選択の理由) の表現先を決める。
- **principle-context-budget** — 大きな出力・長いファイル・委譲や参照の設計で context を消費するときに適用する。context は再生しない有限資源として配分する。
- **principle-coverage-as-floor** — カバレッジやミューテーションスコアを運用するとき、数値目標や閾値を決めるときに適用する。カバレッジは負の指標かつ回帰下限。
- **principle-debt-quadrant** — 品質と速度が競合したとき、技術的負債を作るか・いつ返すか決めるときに適用する。Technical Debt Quadrant による許容判定。
- **principle-delegate-opaquely** — 他の skill / module / service へ処理を委譲する側を書くとき、委譲先の前提・失敗・コストを呼び出し側でどう扱うか決めるときに適用する。呼び出し側が持つのは呼び出し口と自分自身の保証だけ。
- **principle-deletability** — 拡張しやすさと捨てやすさが競合したとき、機能・仕組みを撤去するか決めるとき、撤去した後に何を残すか決めるとき、導入する項目を絞るときに適用する。撤去の起点は使用実績と保守コスト、撤去後に残すのは戻れる記録。
- **principle-design-derivation-order** — アーキテクチャや構造を新しく決めるとき、不可逆な決定を今下すか遅らせるか迷ったときに適用する。制約 → アーキテクチャ特性 → 構造の導出順序。
- **principle-fail-loudly** — エラー処理・例外・フォールバック・縮退の挙動を書くときに適用する。失敗を即座に可視化する原則。
- **principle-fix-root-causes** — バグを直すとき・デバッグするとき、回避策で症状だけ消したくなったときに適用する。修正の到達点を根本原因側へ引き直す。
- **principle-idempotent-operations** — 再実行・再起動・リトライが起こりうる処理 (コマンド・ライフサイクル・処理ループ) を設計するときに適用する。前回の残骸があっても同じ終状態へ収束させる。
- **principle-isolate-shared-writes** — 並行する書き手が同じ書き込み先 (ファイル・キー・状態オブジェクト) へ触れうる設計をするときに適用する。共有をまず解消し、残った共有だけを構造で直列化する。
- **principle-localize-change-impact** — 設計原則同士が競合したとき (DRY と KISS、共通化と独立性のどちらを取るか) に適用する。競合の解決規則。
- **principle-mechanism-not-policy** — 他の project へ配布されるもの (skill / CLI / library / template) の本文・手順・description を書くときに適用する。配るのは仕組みだけで、運用方針は実行 project に委ねる。
- **principle-observable-behavior** — テストの検査対象を決めるとき、振る舞い不変のリファクタリングでテストが壊れたときに適用する。リファクタリング耐性は妥協不可。
- **principle-operability-first** — 運用性 (可観測性・デプロイ容易性) を設計するとき、監視と observability の投資順序を決めるとき、信頼性の余力をどう使うか決めるときに適用する。作った者が運用する前提で初期設計に運用性を組み込む。
- **principle-quarantine-flaky** — 再実行すると結果が変わるテスト (flaky) を検知したときに適用する。放置せず隔離して修理か削除に倒す。
- **principle-review-ready-bar** — 変更を完了と宣言する前、レビューへ出す前に適用する。品質の合格線を判定する原則。
- **principle-risk-based-security** — セキュリティ検査をどの工程に置くか、防御にどれだけ投資するか決めるときに適用する。検査は前倒し、投資は資産価値と脅威モデルで配分する。
- **principle-short-lived-integration** — branch の寿命・リリース単位・展開方法を決めるとき、作業の土台をいつ最新化するか決めるときに適用する。差分も土台も短命に保つ側へ倒す。
- **principle-signature-intent** — 関数・メソッドのシグネチャと引数を設計するとき、boolean 引数やオプショナル引数を足したくなったときに適用する。呼び出し側に意図を表現させる原則。
- **principle-structure-simplicity** — 関数・クラス・モジュールの構造を決めるとき、責務の置き場や共通化の有無で迷ったときに適用する。KISS / DRY / SLAP / SRP の原則。
- **principle-tdd-rhythm** — 機能実装・バグ修正に着手するとき、テストを先に書くか後に書くか決めるときに適用する。開発リズムとしての TDD。
- **principle-test-as-spec** — 個々のテストを書くときに適用する。テスト名の付け方・AAA 構造の粒度・テストコード側の DRY をどこまで緩めるかを決める。
- **principle-test-double-boundary** — テストで依存を差し替えるとき (mock / stub / fake / 実物のどれを使うか)、時刻・乱数・環境変数を制御するときに適用する。mock を許す境界は unmanaged dependency だけ。
- **principle-test-level-allocation** — 新しくどのレベルにテストを書くか決めるとき (単体 / 統合 / e2e の配分) に適用する。Testing Trophy による投資配分。
- **principle-test-pruning** — 既存テストを削るか決めるとき、テストが多すぎる・保守コストが高いと感じたときに適用する。限界価値 ÷ 生涯コストの評価。
- **principle-ubiquitous-naming** — 変数・関数・概念・論点に名前を付けるとき、記号や略号で済ませたくなったときに適用する。ドメイン用語をどこまで使い、短縮形をいつ許すかを決める。
- **principle-validate-at-boundaries** — 入力検証・型の絞り込み・防御的なエラー処理をどこに置くか決めるときに適用する。検証は境界に集約し、内側では型を信頼する。
- **principle-verify-the-real-artifact** — 作業を完了と宣言する前、委譲した作業を受け取るときに適用する。代理指標や自己申告ではなく実物を動かして確かめる。

## 外部 plugin

leaf ではないので Read せず、Skill tool で invoke する。

- **modularity:balanced-coupling** — モジュール / サービス境界の分割・統合そのものを判断するときに invoke する。未導入なら `principle-balance-coupling` だけで判断する。
