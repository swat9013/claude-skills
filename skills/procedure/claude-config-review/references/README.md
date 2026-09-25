# Computational harness 参照ドキュメント

## このドキュメント群の位置づけ

Claude Code の Computational コンポーネント (settings / permission / hook script) を編集・新設するときの参照知識。仕様とレビュー観点は汎用だが、配置選好や例示は本 repo の構成 (Skills ディレクトリプラグイン) を前提にした箇所がある。

claude-config-review skill の `references/` 配下に正本配置され、settings / hook script を編集する前に Claude が Read することを想定する。

**Inferential 側 (CLAUDE.md / rules / SKILL.md 指示文 / hook 注入文) の規範は本ディレクトリにない** — `skills/steering/write-for-harness/references/` が正本で、4 slot 表 (`architecture.md`) / モデル特性 (`models.md`) / 参考文献 (`references.md`) / 構造レビュー (`structure.md`) もそちらに置かれている。`sources.md` だけは両者が参照する 1 正本として本ディレクトリに残る。

## 利用方針

- 入れたい規制が Computational か Inferential かは、まず [architecture](../../../steering/write-for-harness/references/architecture.md) の 4 slot 表で決める。Inferential なら `write-for-harness` へ回す。
- Computational と決まったら該当コンポーネントの `.md` を Read する (`settings.md` / `hook.md`)。
- 公式仕様の鮮度確認とエスカレーション順は `sources.md` を参照する。
- プロジェクト固有の価値観文書 (例: プロジェクト直下の `CONTEXT.md` / プロジェクト CLAUDE.md / ADR) がある場合は併用し、矛盾時はプロジェクト側を優先する。本ドキュメント群はあくまで「Claude Code 仕様 + 汎用ベストプラクティス」のみを扱う。

## 索引

| 種別 | ファイル | 内容 |
|---|---|---|
| コンポーネント | [settings](./settings.md) | settings.json のレビュー観点 |
| コンポーネント | [hook](./hook.md) | hook script (event / matcher / fail-posture) のレビュー観点 |
| 共通 | [sources](./sources.md) | 公式情報源とエスカレーション戦略 (write-for-harness からも参照される 1 正本) |

## 更新ポリシー

- 古い情報や仕様変更を発見したら `claude-code-guide` subagent で確認し、該当 `.md` を edit する (詳細は [sources](./sources.md))。
- 更新作業自体もコンポーネント編集なので、自己参照的に本 docs を Read してから行う。
