# claude-skills

Claude Code で使う skill 集。skill・guard hook・subagent を 1 つの plugin として配る。

## インストール

Claude Code のセッション内で 2 手順:

```
/plugin marketplace add swat9013/claude-skills
/plugin install swat-skills@swat9013
```

インストール後、`/` 補完に skill が並ぶ。

一部の skill は [mattpocock-skills](https://github.com/mattpocock/skills) の skill を呼ぶため、swat-skills は依存 plugin として `mattpocock-skills@claude-plugins-official` を宣言している。公式 marketplace (`claude-plugins-official`) が登録済みなら、install 時に一緒に入る。

公式 marketplace が未登録の環境 (非対話環境では自動登録されないことがある) では依存が未解決のまま残り、mattpocock-skills を呼ぶ skill が動かない。その場合は次を実行すると、依存が自動で入る:

```
/plugin marketplace add anthropics/claude-plugins-official
```

## 同梱している skill

<!-- generated:skills -->
「起動」列の **コマンド** は `/swat-skills:<skill 名>` で呼び出せること、**自動** は Claude が場面に応じて自ら参照することを指す。

### steering

Claude Code 自身の設定 (CLAUDE.md / permission / skill / MCP) を実際の利用実績と突き合わせて棚卸しし、見直し候補を出す。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `inventory-claude-md` | コマンド | project の CLAUDE.md 系 (root / `CLAUDE.local.md` / サブディレクトリ / `.claude/rules/`) を静的観測し、行単位で 6 bucket の移設候補レポートを作る棚卸し。 |
| `inventory-permissions` | コマンド | Claude Code の permission (allow/deny/ask) / sandbox / guard hook を transcript の tool_use 実績と突合し、5 bucket (revoke / promote / refine / sandbox / keep) の候補を人間の判定へ差し出す棚卸し。 |
| `inventory-project-values` | コマンド | 実行中 project の transcript から手入力プロンプトを観測し、再出現した規範を証拠 anchor 付きで候補化して、候補ごとの人間判定を経た承認分だけを cwd repo の CLAUDE.md / `.claude/rules/` へ反映する棚卸し。 |
| `inventory-skill-mcp` | コマンド | install 済みの skill / MCP を直近 30 日の transcript invocation と突合し、単位別 (skill / MCP tool / MCP server / plugin) の削除・見直し・保持候補を証拠付きで提示する棚卸し (判定は人間)。 |
| `skill-usage-audit` | コマンド | 指定 skill の実呼出 transcript を一次証拠に、SKILL.md 記述と実挙動の乖離および工程の消費構造 (時間 / token) を監査し、記述起因の欠陥だけを最小差分で改善して最適化候補を報告する監査ループ。 |
| `write-for-harness` | コマンド / 自動 | CLAUDE.md / .claude/rules/ / SKILL.md 指示文 / hook 注入文 (Inferential harness) を規範に照らして書き、検証 subagent と構造 reviewer の指摘まで反映する手順。 |

### knowledge

実装・レビュー・調査に着手する前に、判断基準と書き方の規約を引き当てる。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `dev-env-best-practices` | 自動 | 開発環境構築時に言語/FW 別のベストプラクティス reference と構築観点を引き出す。 |
| `pr-quality` | 自動 | Google Engineering Practices を蒸留した PR 品質の決定規則集。 |
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
| `playbook-implementation` | コマンド / 自動 | 実装作業を、原則の索引 Read から 2 軸レビュー通過までの番号付き step で通す playbook. |
| `playbook-plan-verification` | コマンド | 計画・spec の検証を、原則の索引 Read から判定の差し戻しまでの番号付き step で通す playbook。 |
| `playbook-ci-fix` | コマンド | 既存 CL の CI 失敗修正を、原則の索引 Read から既存 branch への push までの番号付き step で通す playbook。 |
| `playbook-conflict-resolution` | コマンド | 既存 CL の merge conflict 解消を、原則の索引 Read から既存 branch への push までの番号付き step で通す playbook。 |
| `playbook-research` | コマンド | 調査作業を、原則の索引 Read から成果物の受け渡しまでの番号付き step で通す playbook。 |
| `playbook-review-response` | コマンド | 既存 CL の未解決 review thread への対応を、原則の索引 Read から push と thread の resolve までの番号付き step で通す playbook。 |
| `playbook-skill-creation` | コマンド | 新規 skill 作成を、skill 化の要否判定から 2 軸レビュー通過までの番号付き step で通す playbook。 |
| `playbook-test-optimization` | コマンド | テストスイートの 4 軸診断を、原則の索引 Read から適用判断の返却までの番号付き step で通す playbook。 |
| `playbook-test-perf-tuning` | コマンド | テスト実行時間の短縮を、原則の索引 Read から 2 軸レビュー通過までの番号付き step で通す playbook。 |
| `two-axis-review` | コマンド / 自動 | 差分を原則 rubric 軸と Spec 軸で並列にレビューし、具体物のある反証だけで指摘を落として軸ごとに併記する。 |
| `frontend-review` | コマンド / 自動 | 実装済みの HTML/CSS を デザインシステムの規範 (Refactoring UI tactics / WCAG AA / Rams・Nielsen heuristics) に照らして検査する。 |
| `stacked-implement` | コマンド | 名指しした実装 issue 集合を依存順に直列実装し、issue ごとに CL (PR / MR) を 1 本ずつ作ってスタックする。 |

### design

設計ドキュメント (システム関連図・ユースケース・画面設計) を対話で作る。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `ui-design` | コマンド / 自動 | UI 設計ドキュメント一式を 画面インベントリ → 画面遷移 → 領域/レイアウト → component 分解 → デザイントークン の順の対話的セッションで作成・更新する。 |
| `sud-design` | コマンド / 自動 | 設計ドキュメント一式をシステム関連図 → ユースケース → ドメインモデル図の順の 対話的セッションで作成・更新する。 |

### util

worktree 環境や並列セッションの起動など、作業の進め方そのものを補助する。

| skill | 起動 | 用途 |
| --- | --- | --- |
| `setup` | コマンド | plugin 利用者の repo に swat-skills の初期セットアップ項目 (settings 適用を含む) の 充足を検査し、不足だけを理由付きで提案する doctor。 |
| `dialogue` | コマンド | 安易な解決に走らない・sycophancy 禁止の対話モード。 |
| `worktree-setup` | コマンド | 対象リポジトリに Claude Code の worktree 並列セッション環境 (settings の worktree キー / .worktreeinclude / 初期化 hook) をセットアップする。 |
| `dispatcher` | コマンド | issue → CL の自律オーケストレーションを project に導入する手順と、観測 script が書いた指示ファイルを受けて着手を判断する orchestrator の手順。 |
| `dispatcher-setup` | コマンド | 対象 repo で dispatcher を回すための常駐環境 (宣言 config / claim label / gh 認証 / crontab) の 充足を検査し、副作用の無い試運転を撃ち、不足だけを理由付きで提案して承認分を適用する doctor。 |
| `inventory-launcher` | コマンド | herdr (AI agent 向け terminal multiplexer) session 内で、inventory 系 3 skill (inventory-permissions / inventory-claude-md / inventory-project-values) をそれぞれ独立した Claude Code セッション (分割 pane) として並列起動する launcher。 |
| `issue-triage` | コマンド | open issue を読み取りで調査し、label・コメント・close の推奨案を人間の承認を通して tracker へ反映する対話 triage。 |
| `ready-for-agent-review` | コマンド / 自動 | issue 本文と triage コメントを対象 repo の規範と照らし、規範違反の指定・AI 委任範囲の未閉鎖・規範同士の衝突を反証済みの修正提案として返す (tracker へは書き込まない). |
<!-- /generated:skills -->

## 同梱している subagent

Task tool 経由で skill やメインの Claude から委譲される、専用ツールだけを持つ agent。

<!-- generated:agents -->
| subagent | 用途 |
| --- | --- |
| `web-research` | WebSearch / WebFetch だけを持つ読み込み専用の Web 調査 subagent。 |
| `refuter` | レビュー指摘・欠陥候補を 1 件受け取り、実コードの読解で反証が成立するかを判定する読み取り専用 subagent。 |
<!-- /generated:agents -->

## 同梱している hook

Bash / Write / WebFetch の危険操作を実行前に deny する PreToolUse guard 群 (`hooks/` に配置、登録は `hooks/hooks.json`)。deny 条件に当たらない入力はすべて素通しし、Claude Code 標準の permission フローに委ねる。**入力そのものを読めなかったときは素通しせず deny する** (fail-closed。下記 jq の前提を参照)。

hook は install した環境でそのまま動くが、以下を前提にしている。

- **`jq` が PATH にあること**。guard は **原則 fail-closed** で、payload を読めない (jq が無い / JSON が不正) ときは判定不能として **deny 側に倒れる**。例外は `guard-worktree-escape.py` の 1 本だけ (素通しの損害が git で回復可能なため fail-open。判断の正本は本 repo の `docs/adr/0046-guard-fail-closed-by-default.md`)。発火条件を持たない guard が複数あるため、jq が無い環境では **Bash がほぼ全面的に拒否される**。hook を使うなら jq を入れる
- **guard の方針は作者の運用に合わせて固定されている**。例: `git push --force` (`--force-with-lease` を除く) を deny、`bash`/`sh`/`zsh` の直接起動を deny、WebFetch は `guard-webfetch.sh` の `ALLOWLIST` に載るドメインのみ許可。合わない場合は該当 script を編集する
- **protected branch の防御は hook では持たない**。main/master への直接 push の禁止は GitHub / GitLab の branch protection 側で設定する前提 (hook はローカルにしか効かないため)
- **`guard-worktree-escape.py` は `~/.claude/state/worktree-guard/` に session ごとの作業 root を書く** (7 日で自動削除)。書けない環境では guard が無効化されるだけで、tool 実行は妨げない

## dispatcher を使う場合の前提

`dispatcher` skill (issue を選んで headless の Claude Code セッションへ配車し、CL まで自走させる仕組み) だけは、install しただけでは動かない。**cron から観測 script を周期起動する**、**適用先 project の settings に permission / sandbox の追記が要る**、**issue 置き場の宣言 config を環境ごとに置く**、の 3 点が前提になる。

導入と運用の手順は [`skills/util/dispatcher/README.md`](skills/util/dispatcher/README.md) に書いてある。dispatcher 以外の skill にはこの前提は掛からない。

## 更新

```
/plugin marketplace update swat9013
/plugin update swat-skills@swat9013
```

バージョンは commit を固定しない。marketplace を更新すれば最新の内容が入る。更新の適用には Claude Code の再起動が要る。

更新でも依存 plugin (mattpocock-skills) が未導入なら一緒に入る。依存が未解決のまま残ったときの対処は「インストール」節と同じ。

## ライセンス

MIT ([LICENSE](LICENSE))
