# 参考文献 (思想的バックグラウンド)

## このドキュメントの目的

本 references/ 配下の各 doc が思想的バックグラウンドとして参照している外部記事 / 論文 / engineering blog を一覧化する。各 doc は本文に概念名のみ残し、典拠の URL は本書に集約する (= 各 doc を I/Guide として軽く保つ。実行に必要な情報以外を入れない)。

公式 spec の鮮度確認は [sources](./sources.md) を参照 (責務分離: 本書は「設計思想の参照」、sources.md は「実装仕様の鮮度確認 + エスカレーション戦略」)。

## 一覧

### Harness engineering

architecture.md の 2x2 slot 分類 (Computational/Inferential × Guide/Sensor) の出典。

| 文献 | URL | 参照元 |
|---|---|---|
| Birgitta Böckeler, "Harness engineering for coding agent users" | https://martinfowler.com/articles/harness-engineering.html | architecture.md (slot 概念全般) |
| Birgitta Böckeler, "Maintainability sensors for coding agents" | https://martinfowler.com/articles/sensors-for-coding-agents.html | architecture.md (Sensor slot 概念) |

### Long-context behavior

models.md「コンテキスト管理」章の各概念 (Lost in the Middle / primacy-recency bias / context dilution / navigable structure) の典拠。

| 文献 | URL | 参照元 |
|---|---|---|
| Liu et al. 2023, "Lost in the Middle: How Language Models Use Long Contexts" (arxiv 2307.03172) | https://arxiv.org/abs/2307.03172 | models.md (Lost in the Middle 章) |
| Anthropic, "Long context tips" | https://platform.claude.com/docs/en/docs/build-with-claude/prompt-engineering/long-context-tips | models.md (primacy / recency bias 章) |
| OpenAI, "Prompt engineering guide" | https://platform.openai.com/docs/guides/prompt-engineering | models.md (primacy / recency bias 章) |
| Anthropic, "Effective Context Engineering for AI Agents" | https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents | models.md (context dilution / navigable structure 章) |

### Harness self-repair (観測・反映ループ)

agent harness が自己の障害を観測 → 診断 → 修復 → regression として固定する自己修復ループに関する思想的背景。現時点でどの component doc も本文から引用していない (catalog only)。本リポジトリの観測/反映による self-maintenance 設計と思想的に隣接する事例ブログとして記録する。

| 文献 | URL | 参照元 |
|---|---|---|
| Avi Chawla, "Your Agent Harness Should Repair Itself" | https://blog.dailydoseofds.com/p/your-agent-harness-should-repair | — |

## 運用ルール

- 本 references/ 配下の doc 編集中にユーザーから参考資料 (記事 URL / 論文 / engineering blog) を渡された場合、本書の該当セクション (または新規セクション) に追記する
- 各 doc 本文には概念名のみ残し、URL 出典は本書に集約する。各 doc の末尾「## 参照」節に `→ [references](./references.md)` の 1 行出口リンクを置く
- 公式 spec の鮮度確認情報 (docs.anthropic.com / code.claude.com / github.com/anthropics) は本書ではなく [sources](./sources.md) に追記する
- 一覧が肥大化したら章立てで分割する (例: `## Academic` / `## Anthropic official` / `## 3rd-party blog`)。各セクションで「参照元 doc」を必ず併記して catalog 性を保つ

## 更新トリガー

- 新規 doc が外部参考文献を持つようになった (= 新規エントリ追加)
- 既存の参考文献の URL が変更された / 404 になった
- 参考文献の概念に対する公式の更新があった (例: Anthropic の context engineering blog 改訂)
- セクションが肥大化し分割が必要になった

## 参照

- [README](./README.md): docs/ 全体の索引と利用方針
- [sources](./sources.md): 公式 spec の鮮度確認 (本書とは責務が異なる)
