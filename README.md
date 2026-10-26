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
| `inventory-claude-md` | コマンド | project の CLAUDE.md 系 (root / `CLAUDE.local.md` / サブディレクトリ / `.claude/rules/`) を静的観測し、行単位で 7 bucket の見直し候補レポートを作る棚卸し。 |
| `inventory-permissions` | コマンド | Claude Code の permission (allow/deny/ask) / sandbox / guard hook を transcript の tool_use 実績と突合し、5 bucket (revoke / promote / refine / sandbox / keep) の候補を人間の判定へ差し出す棚卸し。 |
| `inventory-project-values` | コマンド | 実行中 project の transcript から手入力プロンプトを観測し、再出現した規範を証拠 anchor 付きで候補化して、候補ごとの人間判定を経た承認分だけを cwd repo の CLAUDE.md / `.claude/rules/` へ反映する棚卸し。 |
| `inventory-skill-mcp` | コマンド | install 済みの skill / MCP を直近 30 日の transcript invocation と突合し、単位別 (skill / MCP tool / MCP server / plugin) の削除・見直し・保持候補を証拠付きで提示する棚卸し (判定は人間)。 |
| `skill-usage-audit` | コマンド | 指定 skill の実呼出 transcript を一次証拠に、SKILL.md 記述と実挙動の乖離および工程の消費構造 (時間 / token) を監査し、記述起因の欠陥だけを最小差分で改善して最適化候補を報告する監査ループ。 |
| `write-for-harness` | コマンド / 自動 | CLAUDE.md / .claude/rules/ / SKILL.md 指示文 / hook 注入文 / agent 定義 (Inferential harness) を規範に照らして書き、検証 subagent と構造 reviewer の指摘まで反映する手順。 |
| `compact-skill` | コマンド | 名指しした 1 skill の SKILL.md と references/ を行・節ごとにパッチ候補へ分類し、人間が候補ごとに承認したものだけを 1 件ずつ反映して、振る舞いを落とさずに本文を短くする見直し。 |

### knowledge

実装・レビュー・調査に着手する前に、判断基準と書き方の規約を引き当てる。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `claude-config-reference` | 自動 | Claude Code の Computational harness (settings / permission / hook script) の仕様・レビュー観点と、仕様を一次情報で確かめるときの情報源の順を持つ参照知識。 |
| `dev-env-best-practices` | 自動 | 開発環境構築時に言語/FW 別のベストプラクティス reference と構築観点を引き出す。 |
| `principle-index` | コマンド / 自動 | 原則 leaf 全件の適用条件を 1 行ずつ並べた索引。 |
| `python-single-file-script` | 自動 | PEP 723 インラインメタデータ + uv run で単一ファイル Python スクリプトを新規作成・編集する場面に参照する。 |
| `shell-script` | 自動 | bash で .sh single-file script を新規作成・編集する場面に参照する。 |

### procedure

実装・レビュー・診断を、名指しで起動する番号付きの手順で進める。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `claude-config-review` | コマンド / 自動 | Claude Code の Computational harness (settings / permission / hook script) を component 別の subagent に分散してレビューする。 |
| `fitness-function-audit` | コマンド | 対象 repo を scan し、導入・改善すべきアーキテクチャ適応度関数 (fitness function) を HTML report で提案し、選んだ候補の閾値と運用を対話で確定する。 |
| `playbook-diagnosis` | コマンド | bug の診断を、原則の索引 Read から根本原因の記録・返却までの番号付き step で通す playbook。 |
| `playbook-implementation` | コマンド / 自動 | 実装作業を、原則の索引 Read から PR を ready にするまでの番号付き step で通す. |
| `playbook-plan-verification` | コマンド | 計画・spec の検証を、原則の索引 Read から判定の差し戻しまでの番号付き step で通す playbook。 |
| `playbook-ci-fix` | コマンド | 既存 CL の CI 失敗修正を、原則の索引 Read から既存 branch への push までの番号付き step で通す playbook。 |
| `playbook-conflict-resolution` | コマンド | 既存 CL の merge conflict 解消を、原則の索引 Read から既存 branch への push までの番号付き step で通す playbook。 |
| `playbook-research` | コマンド | 調査作業を、原則の索引 Read から成果物の受け渡しまでの番号付き step で通す playbook。 |
| `playbook-review-response` | コマンド | 既存 CL の未解決 review thread への対応を、原則の索引 Read から push と thread の resolve までの番号付き step で通す playbook。 |
| `playbook-skill-creation` | コマンド | 新規 skill 作成を、skill 化の要否判定からレビュー工程の通過までの番号付き step で通す playbook。 |
| `playbook-test-optimization` | コマンド | テストスイートの軸ごとの診断を、原則の索引 Read から適用判断の返却までの番号付き step で通す playbook。 |
| `playbook-test-perf-tuning` | コマンド | テスト実行時間の短縮を、原則の索引 Read からレビュー工程の通過までの番号付き step で通す playbook。 |
| `create-verification-skill` | コマンド | Generate a project-local verification skill that drives your app the way a user does — any language, framework, or platform. |
| `maintain-verification-skill` | コマンド | Bring a project's verification skill and feature map back in line with the app after it changes. |
| `create-formal-verification-skill` | コマンド | Generate a project-local skill that formally verifies the code where a bug costs the most and tests reach the least, in any language, with whichever verification tool fits. |
| `maintain-formal-verification-skill` | コマンド | Bring a project's formal verification back in line with the code after the code changes. |
| `review-stage` | コマンド / 自動 | CL を出す前の差分を、差分の重さに応じた複数の観点で並列にレビューし、PR へ貼れる 1 枚で返す。 |
| `frontend-review` | コマンド / 自動 | 実装済みの HTML/CSS を デザインシステムの規範 (Refactoring UI tactics / WCAG AA / Rams・Nielsen heuristics) に照らして検査する。 |

### design

設計ドキュメント (システム関連図・ユースケース・画面設計) を対話で作る。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `ui-design` | コマンド / 自動 | UI 設計ドキュメント一式を 画面インベントリ → 画面遷移 → 領域/レイアウト → component 分解 → デザイントークン の順の対話的セッションで作成・更新する。 |
| `sud-design` | コマンド / 自動 | 設計ドキュメント一式を目的 → システム関連図 → ユースケース → ドメインモデル図の順の 対話的セッションで作成・更新する。 |
| `grill-with-docs` | コマンド | 計画・設計を共有理解に至るまで問い詰め、決まった用語を用語集に、戻しにくい決定を ADR に、同じ種類の判断で再び使える決定を規範に、設計を変える決定を設計 doc に書き残す。 |

### util

worktree 環境や並列セッションの起動など、作業の進め方そのものを補助する。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `setup` | コマンド | plugin 利用者の repo に swat-skills の初期セットアップ項目 (settings 適用を含む) の 充足を検査し、不足だけを理由付きで提案する doctor。 |
| `dialogue` | コマンド | 安易な解決に走らない・sycophancy 禁止の対話モード。 |
| `worktree-setup` | コマンド | 対象リポジトリに Claude Code の worktree 並列セッション環境 (settings の worktree キー / .worktreeinclude / 初期化 hook) をセットアップする。 |
| `inventory-launcher` | コマンド | herdr (AI agent 向け terminal multiplexer) session 内で、inventory 系 3 skill (inventory-permissions / inventory-claude-md / inventory-project-values) をそれぞれ独立した Claude Code セッション (分割 pane) として並列起動する launcher。 |
| `issue-restructure` | コマンド | open issue を根本課題ごとのクラスタに束ねて課題型の issue へまとめ直す案を、人間の承認を通して tracker へ反映する、triage の前段の対話。 |
| `backlog-triage` | コマンド | open issue 全件を issue-triage に通し、AFK 境界の外側に残った判定と止まったものを、人間と設問のラウンドで決めて tracker と規範へ反映する対話 triage。 |
| `issue-triage` | コマンド / 自動 | issue を 1 件以上受けて AFK 境界で振り分け、境界の内側 (ready-for-agent・close・wontfix) は tracker へ反映し、外側の判定 (規範の文案・深掘り・人手の作業・報告者への質問) と止まったものを呼び出し元へ返す. |
| `ready-for-agent-review` | コマンド / 自動 | issue 本文と triage コメントを対象 repo の規範と照らし、規範違反の指定・AI 委任範囲の未閉鎖・規範同士の衝突を反証済みの修正提案として issue ごとに返す (issue を N 件まとめて受け、tracker へは書き込まない). |
| `stacked-implement` | コマンド | 名指しした実装 (issue 集合・分割計画・単一の仕様) を CL 計画に分け、CL ごとに CL エージェントを並列に起動して実装させ、merge まで追従する。 |

### third

この plugin の skill が呼ぶ、または一緒に使う前提の、外部 repo の skill の同梱版。別の plugin を入れなくても動く。license は上流ごとの LICENSE を skills/third/ に同梱している。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `domain-modeling` | コマンド / 自動 | Build and sharpen a project's domain model. |
| `grilling` | コマンド / 自動 | Grill the user relentlessly about a plan, decision, or idea. |
| `pr` | コマンド / 自動 | Use when writing a PR body. |
| `handoff` | コマンド | Compact the current conversation into a handoff document for another agent to pick up. |
| `prototype` | コマンド / 自動 | Build a throwaway prototype to answer a design question. |
| `research` | コマンド / 自動 | Investigate a question against high-trust primary sources and capture the findings as a Markdown file in the repo. |
| `setup-matt-pocock-skills` | コマンド | Configure this repo for the engineering skills: set up its issue tracker, triage label vocabulary, and domain doc layout. |
| `to-tickets` | コマンド | Break a plan, spec, or the current conversation into a set of tracer-bullet tickets, each declaring its blocking edges, published to the configured tracker (edges as text in one file per ticket locally, or native blocking links on a real tracker). |
| `wayfinder` | コマンド | Plan a huge chunk of work (more than one agent session can hold) as a shared map of decision tickets on your issue tracker, and resolve them one at a time until the way to the destination is clear. |
| `writing-for-agents` | コマンド / 自動 | Writing documents for agents. |
| `retro` | コマンド | Conduct a retrospective on a coding session. |
| `improve-codebase-architecture` | コマンド | Scan a codebase for deepening opportunities, present them as a visual HTML report, then grill through whichever one you pick. |
| `codebase-design` | コマンド / 自動 | Shared vocabulary for designing deep modules. |
| `statechart` | コマンド | Draw how any system moves between states, such as a product flow, a protocol, or a service, as a Mermaid statechart from a TLA+ model. |
<!-- /generated:skills -->

## 同梱している subagent

Agent tool 経由で skill やメインの Claude から委譲される、専用ツールだけを持つ agent。

<!-- generated:agents -->
| subagent | 用途 |
| --- | --- |
| `web-research` | WebSearch / WebFetch だけを持つ読み込み専用の Web 調査 subagent。 |
| `adversarial-verifier` | 差分が宣言する主張 (仕様・要件・docstring・既存の契約・暗黙の主張) を、主張ごとに先に定めたオラクルに照らして壊しにいき、破れた主張を欠陥として返す攻撃役の subagent。 |
| `refuter` | レビュー指摘・欠陥候補を 1 件受け取り、実コードの読解で反証が成立するかを判定する読み取り専用 subagent。 |
| `reviewer` | レビュー skill の reviewer 工程を担う subagent。 |
| `harness-component-reviewer` | Claude Code harness の component 1 種 (skill / CLAUDE.md / rules / hook / settings / agent 定義など) の file を、呼び出し元が渡す reference に照らして読み、違反と改善余地を findings の JSON で返す読み取り専用 subagent。 |
| `harness-structure-reviewer` | Claude Code harness に書いた file の配置・統廃合・統治構造を、呼び出し元が渡す reference と harness 全体の一覧に照らして読み、構造提案を JSON で返す読み取り専用 subagent。 |
| `cl-agent` | 並列 CL 実装の CL 計画の 1 行を受け持ち、担当範囲の CL を 1 本実装して出し、merge されるまでその CL への依頼と conflict 解消に応える subagent。 |
| `transcript-auditor` | skill の実呼出 transcript 1 件を読み、渡された監査基準 checklist に照らした逸脱と、工程ごとの境界 (最初と最後の record の uuid) と手戻りを決まった形式で報告する subagent。 |
<!-- /generated:agents -->

## 同梱している hook

Bash / Write の危険操作を実行前に deny する PreToolUse guard 群 (`hooks/` に配置、登録は `hooks/hooks.json`)。deny 条件に当たらない入力はすべて素通しし、Claude Code 標準の permission フローに委ねる。**入力そのものを読めなかったときは素通しせず deny する** (fail-closed。下記 python3 と jq の前提を参照)。

hook は install した環境でそのまま動くが、以下を前提にしている。

- **`jq` が PATH にあること** (git 系の guard `guard-git.sh` が使う)。guard は **原則 fail-closed** で、payload を読めない (jq が無い / JSON が不正) ときは判定不能として **deny 側に倒れる**。例外は `guard-worktree-escape.py` の 1 本だけ (素通しの損害が git で回復可能なため fail-open。判断の正本は本 repo の `docs/adr/0046-guard-fail-closed-by-default.md`)。jq が無い環境では **git コマンドが拒否される**。hook を使うなら jq を入れる
- **Python 3.9 以上の `python3` が PATH にあること**。hook の多くは python で書かれており、`#!/usr/bin/env python3` で起動する (macOS の Command Line Tools が入れる `/usr/bin/python3` で動く)。`python3` が無い環境では hook が起動に失敗し、Claude Code はその失敗を判断なしとして扱うので、**その guard は効かないまま tool が実行される**
- **guard の方針は作者の運用に合わせて固定されている**。例: `git push --force` (`--force-with-lease` を除く) を deny、`bash`/`sh`/`zsh` の直接起動を deny。合わない場合は該当 script を編集する
- **protected branch の防御は hook では持たない**。main/master への直接 push の禁止は GitHub / GitLab の branch protection 側で設定する前提 (hook はローカルにしか効かないため)
- **plugin は WebFetch の外向き通信を判定しない**。auto mode の classifier が走る環境では classifier が判定するが、classifier が走らない mode や、広い `WebFetch(domain:...)` allow を置いた環境では判定する層が無い
- **`guard-worktree-escape.py` は `~/.claude/state/worktree-guard/` に session ごとの作業 root を書く** (7 日で自動削除)。書けない環境では guard が無効化されるだけで、tool 実行は妨げない

## 更新

```
/plugin marketplace update swat9013
/plugin update swat-skills@swat9013
```

バージョンは commit を固定しない。marketplace を更新すれば最新の内容が入る。更新の適用には Claude Code の再起動が要る。

## ライセンス

MIT ([LICENSE](LICENSE))

[`skills/third/`](skills/third/) の skill はそれぞれの上流 repo のもので、上流の license に従う。上流ごとの LICENSE を `skills/third/LICENSE.<owner>-<repo>` として同梱している。

[`skills/knowledge/principle-index/`](skills/knowledge/principle-index/) の原則 leaf のうち上流の文書を翻案したものは、上流の MIT license に従う。許諾文を [`LICENSE.cursor-plugins-pstack`](skills/knowledge/principle-index/LICENSE.cursor-plugins-pstack) として同梱している。

[`skills/procedure/`](skills/procedure/) の `create-verification-skill` / `maintain-verification-skill` / `create-formal-verification-skill` / `maintain-formal-verification-skill` は上流の skill を改変したもので、上流の MIT license に従う。許諾文を各 skill の dir に `LICENSE.cursor-plugins-pstack` として同梱している。
