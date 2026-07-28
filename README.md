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
| `insights-reflect` | コマンド / 自動 | 標準 /insights を実行し、HTML レポートからの学びを CLAUDE.md に反映する。 |
| `inventory-claude-md` | コマンド | project の CLAUDE.md (root + サブディレクトリ + `.claude/rules/*.md` + `~/.claude/state/rules/` の `#rule` バッファ (captured/archive)) を静的観測し、行単位で 6 bucket (keep-inline / move-to-path-scoped / move-to-skill / move-to-lint / delete / merge) の候補提示まで LLM に運ばせる棚卸し。 |
| `inventory-permissions` | コマンド | Claude Code の permission (allow/deny/ask) / sandbox / guard hook を transcript の tool_use 実績と突合し、両軸集計 (設定 pattern × 実績) と bypass 系列を単位別に 5 bucket (revoke / promote / refine / sandbox / keep) の候補提示まで LLM に運ばせる棚卸し。 |
| `inventory-skill-mcp` | コマンド | 全 plugin skill + personal skill + project skill + MCP (claude.ai connectors 含む) の直近 30 日 invocation を transcript から決定的に集計し、単位別 (skill / MCP tool / MCP server / plugin) の削除/見直し/保持候補を LLM 具体化まで運ぶ棚卸し。 |
| `inventory-values` | コマンド | 標準 transcript のユーザー手入力プロンプト (直近 30 日) を決定的に観測し、121 字以上の帯から価値観候補と反映 diff 案を証拠 anchor 付きで具体化する棚卸し。 |
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
| `python-single-file-script` | 自動 | PEP 723 インラインメタデータ + uv run で単一ファイル Python スクリプトを新規作成・編集する場面に参照する。 |
| `repo-agent-maturity` | コマンド / 自動 | repo (省略時は cwd) をコーディングエージェント (Claude Code / Cursor / Windsurf) 受け入れ準備度で Lv.1〜5 に採点する。 |
| `researcher` | コマンド | 外部技術情報を集めて引用付きの構造化レポートにする調査エンジン (手動呼び出し専用)。 |
| `shell-script` | 自動 | bash で .sh single-file script を新規作成・編集する場面に参照する。 |
| `single-file-html` | 自動 | Use when building a self-contained single-file HTML artifact (explainer doc, dashboard, report, graphical page with inline SVG) that must open standalone with zero external dependencies, when asked to create/build a one-file HTML page or embed diagrams as inline SVG, or when rendering and visually checking an HTML file in a browser. |
| `test-strategy` | 自動 | swat9013 のテスト設計・戦略を蒸留した決定規則集。 |

### dev

ADR・コミットメッセージ・フロントエンドなど、成果物を規約どおりに作る。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `adr` | 自動 | Architecture Decision Record を docs/adr/NNNN-<slug>.md に追加する。 |
| `contextual-commits` | 自動 | Adds structured action lines to commit bodies. |
| `frontend-refine` | コマンド / 自動 | HTML/フロントエンドを作成・改善するとき、デザインシステムの規範 (トークン 3 層 / Refactoring UI tactics / WCAG AA / Rams・Nielsen heuristics) に照らして、Prep (骨格トークン生成) → Build (tactics 参照) → Review (51 項目 self-review + 静的検査) の 3 フェーズで洗練を担保する。 |
| `worktree-setup` | コマンド | 対象リポジトリに Claude Code の worktree 並列セッション環境をセットアップする。 |

### util

並列セッションの起動など、作業の進め方そのものを補助する。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `inventory-dispatch` | コマンド | herdr (AI agent 向け terminal multiplexer) session 内で、inventory 系 3 skill (inventory-permissions / inventory-claude-md / inventory-skill-mcp) をそれぞれ独立した Claude Code セッション (分割 pane) として並列起動し、レポート完成を監視して要約を user に提示、承認された候補の適用指示を pane へ送る一括棚卸し dispatcher。 |
| `issue-dispatch` | コマンド | herdr (AI agent 向け terminal multiplexer) session 内で、ブロックされていない open issue を優先度順に取り出し、空き slot 分だけ対応 skill + issue 番号入りの Claude Code セッションを、呼び出し元と同じ workspace の分割 pane として起動する dispatcher。 |
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
- **guard の方針は作者の運用に合わせて固定されている**。例: `git push` は main/master 宛を deny、`bash`/`sh`/`zsh` の直接起動を deny、WebFetch は `guard-webfetch.sh` の `ALLOWLIST` に載るドメインのみ許可。合わない場合は該当 script を編集する
- **`guard-worktree-escape.py` は `~/.claude/state/worktree-guard/` に session ごとの作業 root を書く** (7 日で自動削除)。書けない環境では guard が無効化されるだけで、tool 実行は妨げない

## 更新

```
/plugin marketplace update swat9013
/plugin update swat-skills@swat9013
```

バージョンは commit を固定しない。marketplace を更新すれば最新の内容が入る。更新の適用には Claude Code の再起動が要る。

## ライセンス

MIT ([LICENSE](LICENSE))
