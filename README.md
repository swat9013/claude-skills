# claude-skills

Claude Code で使う skill 集。skill・guard hook・subagent を 1 つの plugin として配る。

## インストール

Claude Code のセッション内で 2 手順:

```
/plugin marketplace add swat9013/claude-skills
/plugin install swat-skills@swat9013
```

インストール後、`/` 補完に skill が並ぶ。

## 同梱している skill

<!-- generated:skills -->
「起動」列の **コマンド** は `/swat-skills:<skill 名>` で呼び出せること、**自動** は Claude が場面に応じて自ら参照することを指す。

### steering

Claude Code 自身の設定 (CLAUDE.md / permission / skill / MCP) を実際の利用実績と突き合わせて棚卸しし、見直し候補を出す。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `apply-swat-settings` | コマンド | swat-skills の正本 settings (permission / sandbox) の原則を cwd の project へ適用するインストーラ。 |
| `inventory-claude-md` | コマンド | project の CLAUDE.md 系 (root / `CLAUDE.local.md` / サブディレクトリ / `.claude/rules/`) を静的観測し、行単位で 6 bucket の移設候補レポートを作る棚卸し。 |
| `inventory-permissions` | コマンド | Claude Code の permission (allow/deny/ask) / sandbox / guard hook を transcript の tool_use 実績と突合し、5 bucket (revoke / promote / refine / sandbox / keep) の候補を人間の判定へ差し出す棚卸し。 |
| `inventory-project-values` | コマンド | 実行中 project の transcript から手入力プロンプトを観測し、再出現した規範を証拠 anchor 付きで候補化して、候補ごとの人間判定を経た承認分だけを cwd repo の CLAUDE.md / `.claude/rules/` へ反映する棚卸し。 |
| `inventory-skill-mcp` | コマンド | install 済みの skill / MCP を直近 30 日の transcript invocation と突合し、単位別 (skill / MCP tool / MCP server / plugin) の削除・見直し・保持候補を証拠付きで提示する棚卸し (判定は人間)。 |
| `skill-usage-audit` | コマンド | 指定 skill の実呼出 transcript を一次証拠に、SKILL.md の目的・成功条件・制約と実挙動の乖離を検証し、記述起因の欠陥だけを最小差分で改善する監査ループ。 |
| `write-for-harness` | コマンド / 自動 | CLAUDE.md / .claude/rules/ / SKILL.md 指示文 / hook 注入文 (Inferential harness) を規範に照らして書き、検証 subagent と構造 reviewer の指摘まで反映する手順。 |

### knowledge

実装・レビュー・調査に着手する前に、判断基準と書き方の規約を引き当てる。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `claude-config-review` | コマンド / 自動 | Claude Code の Computational harness (settings / permission / hook script) を component 別の subagent に分散してレビューする。 |
| `coding-principles` | 自動 | コード行レベルのコーディング原則の規則集 (命名・実装指針・構造設計・成果物ごとの表現・品質基準)。 |
| `dev-env-best-practices` | 自動 | 開発環境構築時に言語/FW 別のベストプラクティス reference と構築観点を引き出す。 |
| `dialogue` | コマンド | 安易な解決に走らない・sycophancy 禁止の対話モード。 |
| `engineering-judgment` | 自動 | swat9013 のエンジニアリング価値観を蒸留した決定規則集。 |
| `playbook-implementation` | コマンド | 実装作業を、原則の索引 Read から 2 軸レビュー通過までの番号付き step で通す playbook。 |
| `playbook-plan-verification` | コマンド | 計画・spec の検証を、原則の索引 Read から判定の差し戻しまでの番号付き step で通す playbook。 |
| `playbook-research` | コマンド | 調査作業を、原則の索引 Read から成果物の受け渡しまでの番号付き step で通す playbook。 |
| `playbook-triage` | コマンド | issue の triage を、原則の索引 Read から tracker への記入までの番号付き step で通す playbook。 |
| `pr-quality` | 自動 | Google Engineering Practices を蒸留した PR 品質の決定規則集。 |
| `principle-ai-delegation-boundary` | 自動 | AI エージェントへ自律実行を委譲する範囲を決めるとき、許可と禁止を設計するとき、人間へ返す境界を決めるときに適用する。 |
| `principle-artifact-register` | 自動 | README・正本・蒸留版・spec / plan / PR 説明文・エージェント向け指示書といった文書を書くときに適用する。 |
| `principle-automate-when-it-hurts` | 自動 | 反復作業を自動化するか決めるとき、規約の遵守をどう守らせるか決めるとき、LLM 推論を script へ追い出すか判断するときに適用する。 |
| `principle-balance-coupling` | 自動 | モジュール / サービス境界の結合を評価するとき、境界を分割するか統合するか判断するときに適用する。 |
| `principle-build-vs-buy` | 自動 | 自前実装と既製品が拮抗したとき、言語・フレームワーク・ライブラリを選定するときに適用する。 |
| `principle-collective-ownership` | 自動 | コードの所有範囲・レビューの目的・設計判断の記録先を決めるときに適用する。 |
| `principle-comment-intent` | 自動 | コメントや docstring を書くか迷うとき、変更の意図をどこに残すか決めるときに適用する。 |
| `principle-context-budget` | 自動 | 大きな出力・長いファイル・委譲や参照の設計で context を消費するときに適用する。 |
| `principle-coverage-as-floor` | 自動 | カバレッジやミューテーションスコアを運用するとき、数値目標や閾値を決めるときに適用する。 |
| `principle-debt-quadrant` | 自動 | 品質と速度が競合したとき、技術的負債を作るか・いつ返すか決めるときに適用する。 |
| `principle-delegate-opaquely` | 自動 | 他の skill / module / service へ処理を委譲する側を書くとき、委譲先の前提・失敗・コストを呼び出し側でどう扱うか決めるときに適用する。 |
| `principle-deletability` | 自動 | 拡張しやすさと捨てやすさが競合したとき、機能・仕組みを撤去するか決めるとき、撤去した後に何を残すか決めるときに適用する。 |
| `principle-design-derivation-order` | 自動 | アーキテクチャや構造を新しく決めるとき、不可逆な決定を今下すか遅らせるか迷ったときに適用する。 |
| `principle-fail-loudly` | 自動 | エラー処理・例外・フォールバック・縮退の挙動を書くときに適用する。 |
| `principle-fix-root-causes` | 自動 | バグを直すとき・デバッグするとき、回避策で症状だけ消したくなったときに適用する。 |
| `principle-idempotent-operations` | 自動 | 再実行・再起動・リトライが起こりうる処理 (コマンド・ライフサイクル・処理ループ) を設計するときに適用する。 |
| `principle-index` | 自動 | 原則 leaf 全件の適用条件を 1 行ずつ並べた索引。 |
| `principle-isolate-shared-writes` | 自動 | 並行する書き手が同じ書き込み先 (ファイル・キー・状態オブジェクト) へ触れうる設計をするときに適用する。 |
| `principle-localize-change-impact` | 自動 | 設計原則同士が競合したとき (DRY と KISS、共通化と独立性のどちらを取るか) に適用する。 |
| `principle-mechanism-not-policy` | 自動 | 他の project へ配布されるもの (skill / CLI / library / template) の本文・手順・description を書くときに適用する。 |
| `principle-observable-behavior` | 自動 | テストの検査対象を決めるとき、振る舞い不変のリファクタリングでテストが壊れたときに適用する。 |
| `principle-operability-first` | 自動 | 運用性 (可観測性・デプロイ容易性) を設計するとき、監視と observability の投資順序を決めるとき、信頼性の余力をどう使うか決めるときに適用する。 |
| `principle-quarantine-flaky` | 自動 | 再実行すると結果が変わるテスト (flaky) を検知したときに適用する。 |
| `principle-review-ready-bar` | 自動 | 変更を完了と宣言する前、レビューへ出す前に適用する。 |
| `principle-risk-based-security` | 自動 | セキュリティ検査をどの工程に置くか、防御にどれだけ投資するか決めるときに適用する。 |
| `principle-short-lived-integration` | 自動 | branch の寿命・リリース単位・展開方法を決めるとき、作業の土台をいつ最新化するか決めるときに適用する。 |
| `principle-signature-intent` | 自動 | 関数・メソッドのシグネチャと引数を設計するとき、boolean 引数やオプショナル引数を足したくなったときに適用する。 |
| `principle-structure-simplicity` | 自動 | 関数・クラス・モジュールの構造を決めるとき、責務の置き場や共通化の有無で迷ったときに適用する。 |
| `principle-tdd-rhythm` | 自動 | 機能実装・バグ修正に着手するとき、テストを先に書くか後に書くか決めるときに適用する。 |
| `principle-test-as-spec` | 自動 | 個々のテストを書くときに適用する。 |
| `principle-test-double-boundary` | 自動 | テストで依存を差し替えるとき (mock / stub / fake / 実物のどれを使うか)、時刻・乱数・環境変数を制御するときに適用する。 |
| `principle-test-level-allocation` | 自動 | 新しくどのレベルにテストを書くか決めるとき (単体 / 統合 / e2e の配分) に適用する。 |
| `principle-test-pruning` | 自動 | 既存テストを削るか決めるとき、テストが多すぎる・保守コストが高いと感じたときに適用する。 |
| `principle-ubiquitous-naming` | 自動 | 変数・関数・概念・論点に名前を付けるとき、記号や略号で済ませたくなったときに適用する。 |
| `principle-validate-at-boundaries` | 自動 | 入力検証・型の絞り込み・防御的なエラー処理をどこに置くか決めるときに適用する。 |
| `principle-verify-the-real-artifact` | 自動 | 作業を完了と宣言する前、委譲した作業を受け取るときに適用する。 |
| `python-single-file-script` | 自動 | PEP 723 インラインメタデータ + uv run で単一ファイル Python スクリプトを新規作成・編集する場面に参照する。 |
| `repo-agent-maturity` | コマンド / 自動 | コーディングエージェント (Claude Code / Cursor / Windsurf) の受け入れ準備度で repo を Lv.1〜5 に採点する。 |
| `shell-script` | 自動 | bash で .sh single-file script を新規作成・編集する場面に参照する。 |
| `single-file-html` | 自動 | Use when building a self-contained single-file HTML artifact (explainer doc, dashboard, report, graphical page with inline SVG) that must open standalone with zero external dependencies, when asked to create/build a one-file HTML page or embed diagrams as inline SVG, or when rendering and visually checking an HTML file in a browser. |
| `test-strategy` | 自動 | swat9013 のテスト設計・戦略を蒸留した決定規則集。 |
| `two-axis-review` | コマンド / 自動 | 差分を原則 rubric 軸と Spec 軸で並列にレビューし、具体物のある反証だけで指摘を落として軸ごとに併記する。 |

### dev

ADR・コミットメッセージ・フロントエンドなど、成果物を規約どおりに作る。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `contextual-commits` | 自動 | Adds structured action lines to commit bodies. |
| `frontend-refine` | コマンド / 自動 | HTML/フロントエンドの成果物 1 つを作成・改善するとき、デザインシステムの規範 (トークン 3 層 / Refactoring UI tactics / WCAG AA / Rams・Nielsen heuristics) に照らして Prep (骨格トークン生成) → Build (tactics 参照) → Review (51 項目 self-review + 静的検査) の 3 フェーズで洗練させる。 |
| `worktree-setup` | コマンド | 対象リポジトリに Claude Code の worktree 並列セッション環境 (settings の worktree キー / .worktreeinclude / 初期化 hook) をセットアップする。 |

### util

並列セッションの起動など、作業の進め方そのものを補助する。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `dispatch-dashboard` | コマンド / 自動 | dispatch v2 の現況を人間が 1 枚で読む read-only な dashboard の開き方と読み方。 |
| `dispatch-setup` | コマンド | dispatch v1 (`dispatch-ops`) を新しい project で使えるようにする初期設定ステップ。 |
| `inventory-launcher` | コマンド | herdr (AI agent 向け terminal multiplexer) session 内で、inventory 系 3 skill (inventory-permissions / inventory-claude-md / inventory-project-values) をそれぞれ独立した Claude Code セッション (分割 pane) として並列起動する launcher。 |
| `orchestrator` | コマンド | herdr session 内で着手可能な open issue を分割 pane の Claude Code セッションへ dispatch し、worker の質問と reconciler の escalation を捌く常駐 orchestrator。 |
<!-- /generated:skills -->

## 同梱している subagent

Task tool 経由で skill やメインの Claude から委譲される、専用ツールだけを持つ agent。

<!-- generated:agents -->
| subagent | 用途 |
| --- | --- |
| `web-research` | WebSearch / WebFetch だけを持つ読み込み専用の Web 調査 subagent。 |
| `refuter` | レビュー指摘・欠陥候補を 1 件受け取り、実コードを読んで反証が成立するかを判定する subagent。 |
<!-- /generated:agents -->

## 同梱している hook

Bash / Write / WebFetch の危険操作を実行前に deny する PreToolUse guard 群 (`hooks/` に配置、登録は `hooks/hooks.json`)。deny 条件に当たらない入力はすべて素通しし、Claude Code 標準の permission フローに委ねる。**入力そのものを読めなかったときは素通しせず deny する** (fail-closed。下記 jq の前提を参照)。

hook は install した環境でそのまま動くが、以下を前提にしている。

- **`jq` が PATH にあること**。guard は **原則 fail-closed** で、payload を読めない (jq が無い / JSON が不正) ときは判定不能として **deny 側に倒れる**。例外は `guard-worktree-escape.py` の 1 本だけ (素通しの損害が git で回復可能なため fail-open。判断の正本は本 repo の `docs/adr/0046-guard-fail-closed-by-default.md`)。発火条件を持たない guard が複数あるため、jq が無い環境では **Bash がほぼ全面的に拒否される**。hook を使うなら jq を入れる
- **guard の方針は作者の運用に合わせて固定されている**。例: `git push --force` (`--force-with-lease` を除く) を deny、`bash`/`sh`/`zsh` の直接起動を deny、WebFetch は `guard-webfetch.sh` の `ALLOWLIST` に載るドメインのみ許可。合わない場合は該当 script を編集する
- **protected branch の防御は hook では持たない**。main/master への直接 push の禁止は GitHub / GitLab の branch protection 側で設定する前提 (hook はローカルにしか効かないため)
- **`guard-worktree-escape.py` は `~/.claude/state/worktree-guard/` に session ごとの作業 root を書く** (7 日で自動削除)。書けない環境では guard が無効化されるだけで、tool 実行は妨げない

## dispatch 機構を使う場合の前提

`orchestrator` skill と同梱 MCP server `dispatch-v2` からなる **dispatch 機構** (issue を選んで Claude Code セッションへ配車し、PR まで自走させる仕組み) だけは、install しただけでは動かない。**herdr が必須** (tmux 非対応)、**適用先 project の settings に permission / sandbox の追記が要る**、**issue 置き場の宣言 config を環境ごとに置く**、の 3 点が前提になる。

前提の全量と、満たされていないときにどう見えるか (黙って壊れるもの / 起動時に止まるもの) は [`skills/util/orchestrator/README.md`](skills/util/orchestrator/README.md) に書いてある。dispatch 以外の skill にはこの前提は掛からない。

## 更新

```
/plugin marketplace update swat9013
/plugin update swat-skills@swat9013
```

バージョンは commit を固定しない。marketplace を更新すれば最新の内容が入る。更新の適用には Claude Code の再起動が要る。

## ライセンス

MIT ([LICENSE](LICENSE))
