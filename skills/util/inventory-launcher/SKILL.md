---
name: inventory-launcher
disable-model-invocation: true
argument-hint: "[targets]"
description: herdr (AI agent 向け terminal multiplexer) session 内で、inventory 系 3 skill (inventory-permissions / inventory-claude-md / inventory-project-values) をそれぞれ独立した Claude Code セッション (分割 pane) として並列起動する launcher。起動して報告したら終了し、各 pane の進行・レポート・適用には一切関与しない。
---

# inventory-launcher

herdr session 内で inventory 系 3 skill をそれぞれ独立した Claude Code セッションとして起動する launcher。展開先は**この skill を呼び出したセッションが居る herdr workspace の分割 pane** — user は同じ workspace 内で全セッションを見渡し、pane 移動で直接介入できる。

**起動して報告したら終了する。** 監視ループ・レポートの回収と要約・候補の採否判断・稼働中 pane の回収は一切行わない。起動後の pane は user が直接見る。停滞している pane があっても user に pane 移動を案内する (pane へテキストを送るのは pane 内の AskUserQuestion に対する人間承認の迂回になる)。各 inventory skill の手順の中身 (観測 script / 候補の分類 / 適用) は各 pane の領分。

## args

`/inventory-launcher [targets]` — targets は対象 subset の指定 (space / comma 区切り、`permissions` / `claude-md` / `project-values`)。省略時は 3 target 全部。

## 手順

### 1. 起動 script を撃つ

target は user が書いたまま (空白区切りでも comma 区切りでも script が受ける) 渡す。省略時は 3 target 全部。**この 1 行を literal で撃つ** — path を変数や `${CLAUDE_SKILL_DIR}` へ置き換えると sandbox の除外指定と照合されず、script 内の herdr が socket へ届かないまま落ちる。

```
~/.claude/skills/swat-skills/skills/util/inventory-launcher/scripts/launch-inventory-panes.py [targets]
```

script は pane の分割・label 付与・セッション起動・agent の検出待ちまでを行い、結果を JSON で返す。前提 (herdr session の内側に居るか / 連携 hook の版 / daemon への疎通 / `claude` の実在) が 1 つでも欠けたら**どの target も起動せず** exit 1 で止まる。

**起動した pane の cwd は本 skill を呼び出したセッションの cwd** になる。各 inventory skill は cwd の repo を棚卸し対象にするので、対象 project の checkout で呼ぶ (worktree で呼べば worktree が対象になる)。

### 2. 返った JSON を読んで報告する

| 欄 | 意味 | 報告に載せること |
|---|---|---|
| `arg_error` | target 名が不正で 1 つも起動しなかった | 指定できる名前を添えて user に指定し直してもらう (環境の不備ではない) |
| `preflight_error` | 前提が欠けていて 1 つも起動しなかった | 文言をそのまま伝え、user に環境の修正を依頼して終了する |
| `launched[]` | 起動した pane (`target` / `label` / `pane_id`) | 一覧にする |
| `settle_error` | pane は起こしたが、agent の検出中に herdr が応答しなくなった | `launched[]` は「起こしたが未確認」の意味になる。user に pane を直接見てもらう |
| `skipped[].reason` = `already_running` | 同じ label の pane が既に稼働している | 起動済みとして扱う (再起動しない) |
| `skipped[].reason` = `self_pane_stale_label` | 自 pane に前回実行の残骸 label が付いている | 自 pane の label は本 skill から付け替えないので、herdr での rename を user に依頼する |
| `failed[].reason` = `launch_failed` | pane を起こせなかった (`detail` に herdr の文言) | 理由ごと伝える。撃ち直すかは user の判断で、本 skill からは行わない |
| `failed[].reason` = `agent_not_detected` | pane は割れたが Claude Code が立たなかった | 同上。**`label_released` が `false` なら畳めなかった pane が label を占有したまま残っている** — 次回もその target は `already_running` として飛ばされるので、herdr での回収を user に依頼する |

最後に参加方法を添える — 同じ workspace 内に展開済み。herdr の keybinding (`prefix+...` — bind は user config 依存) で pane 移動 / zoom できる。**各 pane 内の inventory skill は候補の採否を pane 内の AskUserQuestion で user に諮る — これには user 自身が当該 pane に移動して応答する**
