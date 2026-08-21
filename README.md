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
| `apply-swat-settings` | コマンド | swat-skills の正本 settings (permission / sandbox) を、原則に照らして cwd の project へ適用するインストーラ。 |
| `inventory-claude-md` | コマンド | project の CLAUDE.md (root + project-local `CLAUDE.local.md` + サブディレクトリ + `.claude/rules/*.md`) を静的観測し、行単位で 6 bucket (keep-inline / move-to-path-scoped / move-to-skill / move-to-lint / delete / merge) の候補提示まで LLM に運ばせる棚卸し。 |
| `inventory-permissions` | コマンド | Claude Code の permission (allow/deny/ask) / sandbox / guard hook を transcript の tool_use 実績と突合し、両軸集計 (設定 pattern × 実績) と bypass 系列を単位別に 5 bucket (revoke / promote / refine / sandbox / keep) の候補提示まで LLM に運ばせる棚卸し。 |
| `inventory-project-values` | コマンド | 実行中の project の標準 transcript から、ユーザーが手入力したプロンプト (直近 30 日 / 60 字以上) を決定的に観測し、同一規範の再出現回数を判定材料にして project 規範の候補を証拠 anchor 付きで具体化する棚卸し。 |
| `inventory-skill-mcp` | コマンド | 全 plugin skill + personal skill + project skill + MCP (claude.ai connectors 含む) の直近 30 日 invocation を transcript から決定的に集計し、単位別 (skill / MCP tool / MCP server / plugin) の削除/見直し/保持候補を LLM 具体化まで運ぶ棚卸し。 |
| `skill-usage-audit` | コマンド | 指定 skill が実際に呼び出された直近 transcript を特定し、SKILL.md の目的・成功条件・制約と実挙動を突合して逸脱を検証、skill 記述起因の欠陥を worktree + PR で改善する監査ループ。 |

### knowledge

実装・レビュー・調査に着手する前に、判断基準と書き方の規約を引き当てる。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `claude-config-review` | コマンド / 自動 | Claude Code 設定 (skill / hook / CLAUDE.md / settings / rules) を 5 種コンポーネント別の subagent に分散してレビューする。 |
| `coding-principles` | 自動 | コード行レベルのコーディング原則の規則集 (命名・実装指針・構造設計・成果物ごとの表現・品質基準)。 |
| `dev-env-best-practices` | 自動 | 開発環境構築時に言語/FW 別のベストプラクティス reference と構築観点を引き出す。 |
| `dialogue` | コマンド | 安易な解決に走らない・sycophancy 禁止の対話モード。 |
| `engineering-judgment` | 自動 | swat9013 のエンジニアリング価値観を蒸留した決定規則集。 |
| `pr-quality` | 自動 | Google Engineering Practices を蒸留した PR 品質の決定規則集。 |
| `prompting-principles` | 自動 | Claude 向け指示文 (プロンプト) の文面をどう書くかの決定規則集。 |
| `python-single-file-script` | 自動 | PEP 723 インラインメタデータ + uv run で単一ファイル Python スクリプトを新規作成・編集する場面に参照する。 |
| `repo-agent-maturity` | コマンド / 自動 | repo (省略時は cwd) をコーディングエージェント (Claude Code / Cursor / Windsurf) 受け入れ準備度で Lv.1〜5 に採点する。 |
| `shell-script` | 自動 | bash で .sh single-file script を新規作成・編集する場面に参照する。 |
| `single-file-html` | 自動 | Use when building a self-contained single-file HTML artifact (explainer doc, dashboard, report, graphical page with inline SVG) that must open standalone with zero external dependencies, when asked to create/build a one-file HTML page or embed diagrams as inline SVG, or when rendering and visually checking an HTML file in a browser. |
| `test-strategy` | 自動 | swat9013 のテスト設計・戦略を蒸留した決定規則集。 |

### dev

ADR・コミットメッセージ・フロントエンドなど、成果物を規約どおりに作る。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `contextual-commits` | 自動 | Adds structured action lines to commit bodies. |
| `frontend-refine` | コマンド / 自動 | HTML/フロントエンドを作成・改善するとき、デザインシステムの規範 (トークン 3 層 / Refactoring UI tactics / WCAG AA / Rams・Nielsen heuristics) に照らして、Prep (骨格トークン生成) → Build (tactics 参照) → Review (51 項目 self-review + 静的検査) の 3 フェーズで洗練を担保する。 |
| `worktree-setup` | コマンド | 対象リポジトリに Claude Code の worktree 並列セッション環境をセットアップする。 |

### util

並列セッションの起動など、作業の進め方そのものを補助する。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `dispatch-setup` | コマンド | dispatch 機構 (orchestrator / observer / dispatch-ops) を新しい project で使えるようにする初期設定ステップ。 |
| `inventory-dispatch` | コマンド | herdr (AI agent 向け terminal multiplexer) session 内で、inventory 系 3 skill (inventory-permissions / inventory-claude-md / inventory-project-values) をそれぞれ独立した Claude Code セッション (分割 pane) として並列起動する launcher。 |
| `observer` | コマンド / 自動 | dispatch 機構の observer 本体。 |
| `orchestrator` | コマンド | herdr (AI agent 向け terminal multiplexer) session 内で、着手可能な open issue を選んで Claude Code セッションを分割 pane として起動し、worker の質問と observer の escalation で回収・駐機・補充を回す常駐 orchestrator。 |
<!-- /generated:skills -->

## 同梱している subagent

Task tool 経由で skill やメインの Claude から委譲される、専用ツールだけを持つ agent。

<!-- generated:agents -->
| subagent | 用途 |
| --- | --- |
| `web-research` | WebSearch / WebFetch だけを持つ読み込み専用の Web 調査 subagent。 |
<!-- /generated:agents -->

## 同梱している hook

Bash / Write / WebFetch の危険操作を実行前に deny する PreToolUse guard 群 (`hooks/` に配置、登録は `hooks/hooks.json`)。deny 条件に当たらない入力はすべて素通しし、判定できないケースは Claude Code 標準の permission フローに委ねる。

hook は install した環境でそのまま動くが、以下を前提にしている。

- **`jq` が PATH にあること**。無い場合、`guard-git.sh` / `guard-pipe-execute.sh` / `guard-destructive.sh` / `guard-webfetch.sh` は判定不能として **deny 側に倒れる** (fail-closed)。該当する Bash / WebFetch 呼び出しがすべて拒否されるため、hook を使うなら jq を入れる
- **guard の方針は作者の運用に合わせて固定されている**。例: `git push --force` (`--force-with-lease` を除く) を deny、`bash`/`sh`/`zsh` の直接起動を deny、WebFetch は `guard-webfetch.sh` の `ALLOWLIST` に載るドメインのみ許可。合わない場合は該当 script を編集する
- **protected branch の防御は hook では持たない**。main/master への直接 push の禁止は GitHub / GitLab の branch protection 側で設定する前提 (hook はローカルにしか効かないため)
- **`guard-worktree-escape.py` は `~/.claude/state/worktree-guard/` に session ごとの作業 root を書く** (7 日で自動削除)。書けない環境では guard が無効化されるだけで、tool 実行は妨げない

## dispatch 機構を使う場合の前提

`orchestrator` / `observer` skill と同梱 MCP server `dispatch-ops` からなる **dispatch 機構** (issue を選んで Claude Code セッションへ配車し、PR まで自走させる仕組み) だけは、install しただけでは動かない。**herdr が必須** (tmux 非対応)、**適用先 project の settings に permission / sandbox の追記が要る**、**issue 置き場の宣言 config を環境ごとに置く**、の 3 点が前提になる。

前提の全量と、満たされていないときにどう見えるか (黙って壊れるもの / 起動時に止まるもの) は [`skills/util/orchestrator/README.md`](skills/util/orchestrator/README.md) に書いてある。dispatch 以外の skill にはこの前提は掛からない。

## 更新

```
/plugin marketplace update swat9013
/plugin update swat-skills@swat9013
```

バージョンは commit を固定しない。marketplace を更新すれば最新の内容が入る。更新の適用には Claude Code の再起動が要る。

## ライセンス

MIT ([LICENSE](LICENSE))
