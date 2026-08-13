---
name: inventory-dispatch
disable-model-invocation: true
argument-hint: "[targets]"
description: herdr (AI agent 向け terminal multiplexer) session 内で、inventory 系 3 skill (inventory-permissions / inventory-claude-md / inventory-project-values) をそれぞれ独立した Claude Code セッション (分割 pane) として並列起動する launcher。起動して報告したら終了し、各 pane の進行・レポート・適用には一切関与しない。Use when「棚卸しを一括」「inventory をまとめて実行」「全部まとめて棚卸し」「一括棚卸し」「inventory-dispatch」.
---

# inventory-dispatch

herdr session 内で inventory 系 3 skill をそれぞれ独立した Claude Code セッションとして起動する launcher。展開先は**この skill を呼び出したセッションが居る herdr workspace の分割 pane** — user は同じ workspace 内で全セッションを見渡し、pane 移動で直接介入できる。

**起動して報告したら終了する。** 監視ループ・レポートの回収と要約・候補の承認仲介・適用指示の送信・pane の回収は一切行わない。起動後の pane は user が直接見る。

## args

`/inventory-dispatch [targets]` — targets は対象 subset の指定 (space / comma 区切り、`permissions` / `claude-md` / `project-values`)。省略時は 3 target 全部。

## target 表

| target | 起動する skill | pane label |
|---|---|---|
| `permissions` | `inventory-permissions` | `inv-permissions` |
| `claude-md` | `inventory-claude-md` | `inv-claude-md` |
| `project-values` | `inventory-project-values` | `inv-project-values` |

## 手順

### 1. 起動

target ごとに `pane_spawn` を 1 回ずつ呼ぶ (dispatch-ops MCP server)。issue に紐づかない起動なので `issue_ref` は渡さず `label` を渡す:

```
pane_spawn(label: "inv-permissions", prompt: "/swat-skills:inventory-permissions を実行する。skill の手順に完全に従うこと")
```

- 3 target とも同じ形。label と skill 名は上の表から取る
- `worktree` / `cwd` は渡さない — 各 inventory skill が repo 規約に従って自分で worktree を切る
- 呼び出しが失敗したときの読み分け:

| 症状 | 意味 | 対応 |
|---|---|---|
| `label ... の pane が既にある` | 稼働中か、**自 pane に残骸 label が付いている**かのどちらか | `observe_panes` で当該 label の `is_self` を見る。`false` なら起動済みとして次の target へ。`true` なら前回実行の残骸が自分に付いた状態で起動済みではない — herdr での rename を user に依頼して報告する (自 pane の label は本 skill から変更できない) |
| herdr 由来のエラー (session 外 / hook 未導入 / socket 不通) | pane backend が使えない | user に環境の修正を依頼して終了する |
| `ok: false` (`pane_id` は返る) | pane は割れたが agent を検出できなかった | **`pane_close(pane_id)` で畳んでから**起動失敗を報告する。畳まないと label を占有したままになり、以後その target の起動が永久に「既にある」で撥ねられる。再試行は user 判断 |

### 2. 報告して終了

以下を提示して終了する:

1. 起動した pane 一覧 (target / pane_id / 新規・既存稼働の別)
2. 起動失敗した target とその理由
3. 参加方法 — 同じ workspace 内に展開済み。herdr の keybinding (`prefix+...` — bind は user config 依存) で pane 移動 / zoom できる。**各 pane 内の inventory skill は候補の採否を pane 内の AskUserQuestion で user に諮る — これには user 自身が当該 pane に移動して応答する**

## 責務境界

起動と報告で止まる。以下を**構造的に守る**:

- **pane へテキストを送らない** (`pane_send` を使わない)。pane 内の AskUserQuestion への代答は人間承認の迂回になる。停滞している pane があっても、user に pane 移動を案内する
- **レポートを読まない・要約しない・候補の採否を判断しない**。判定は各 pane 内で人間が行う
- **稼働中の pane を回収しない**。起動後の pane の寿命は user の領分。`pane_close` を使ってよいのは `ok: false` (agent 未検出) で残った pane を畳むときだけ — 起動しなかった pane の後始末は起動の一部で、セッションの応答を取りまとめる行為ではない
- inventory 各 skill の手順の中身 (観測 script / 候補の分類 / 適用と PR 作成) は各 pane の領分
