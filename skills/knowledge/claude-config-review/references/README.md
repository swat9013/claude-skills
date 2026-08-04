# Claude Code 改善用 参照ドキュメント

## このドキュメント群の位置づけ

Claude Code の 5 種コンポーネント (skill / hook / CLAUDE.md / settings / rules) を編集・新設するときの参照知識。仕様とレビュー観点は汎用だが、配置選好や例示は本 repo の構成 (Skills ディレクトリプラグイン) を前提にした箇所がある。

claude-config-review skill の `references/` 配下に正本配置され、5 種コンポーネントを編集する前に Claude が Read することを想定する。

## 利用方針

- コンポーネント編集前にまず `architecture.md` を Read して、入れたい規制が 4 slot のどれに入るかを決める。
- 続いて該当コンポーネントの `.md` を Read する (`skill.md` / `hook.md` / `claude-md.md` / `settings.md` / `rules.md`)。
- 共通事項は `models.md` (モデル特性と prompt 設計) と `sources.md` (公式情報源 + エスカレーション) を参照する。思想的バックグラウンド (Fowler の harness engineering / long-context 研究等) の URL は `references.md` に集約。
- プロジェクト固有の価値観文書 (例: プロジェクト直下の `CONTEXT.md` / プロジェクト CLAUDE.md / ADR) がある場合は併用し、矛盾時はプロジェクト側を優先する。本ドキュメント群はあくまで「Claude Code 仕様 + 汎用ベストプラクティス」のみを扱う。

## 索引

| 種別 | ファイル | 内容 |
|---|---|---|
| 共通 | [architecture](./architecture.md) | コンポーネントの Fowler 2x2 マッピング (slot 選択フロー + I/Guide 観点) |
| 共通 | [models](./models.md) | fable / opus / sonnet / haiku の特性と prompt 設計指針 |
| 共通 | [sources](./sources.md) | 公式情報源とエスカレーション戦略 |
| 共通 | [references](./references.md) | 思想的バックグラウンド (3rd-party 記事 / 論文 / engineering blog) |
| コンポーネント | [skill](./skill.md) | skill (frontmatter + 本文) のレビュー観点 |
| コンポーネント | [hook](./hook.md) | hook (event handler) のレビュー観点 |
| コンポーネント | [claude-md](./claude-md.md) | CLAUDE.md (グローバル / プロジェクト) のレビュー観点 |
| コンポーネント | [settings](./settings.md) | settings.json のレビュー観点 |
| コンポーネント | [rules](./rules.md) | rules/*.md のレビュー観点 |

## 更新ポリシー

- 古い情報や仕様変更を発見したら `claude-code-guide` subagent で確認し、該当 `.md` を edit する (詳細は [sources](./sources.md))。
- 更新作業自体もコンポーネント編集なので、自己参照的に本 docs を Read してから行う。
