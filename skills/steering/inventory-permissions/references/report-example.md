# 棚卸しレポートの記入例

手順 4 の固定 schema (ヘッダ / 候補 section / informational / summary 表) を埋めた形の概略。候補 section の証拠 (mart からの転記) と提案の行は省いてある。

```
# permission / sandbox / hook 棚卸しレポート

観測窓: 2026-06-17 〜 2026-07-17 (30 日) / 総 event 14,449 / distinct sessions 580 / section project
判定可能性: sufficient (総数 >= 30)
matcher confidence: exact 105 / approx 14
数えた transcript: main
観測の劣化: 事故由来の劣化なし / absent_optional_paths: なし
分母の欠落: なし

## revoke

### 1. Bash(some-unused-remote-write-cmd:*)  [allow, project_local]
- rule_fired: revoke_candidate / match_count 0 / observation 30 days
- open_predicates: side_effect_capability ○ (remote 書き込み) / exposure_opportunity △
  (推測: 窓内に同 repo での作業 42 session — 機会の実在は反実仮想) / alias_still_in_use ✗ / invocation_form_pair ✗
- bucket: revoke (低確度 — レポート提示まで)
- 編集可能性: `.claude/settings.local.json` は untracked (`git ls-files --error-unmatch` exit 1) — version control 追跡なし
- 適用手順: 本 skill は書き込まない。`<repo 実パス>/.claude/settings.local.json` の `permissions.allow` から
  `"Bash(some-unused-remote-write-cmd:*)"` の行を人間が直接削除する

## refine

### 2. Bash(grep:*)  [allow, project_local]
- rule_fired: compound_line_deny_miscount / hard_deny 26 / compound_command_deny 24 / window_split shifted false (窓内で一様)
- open_predicates: deny_attributable_to_entry ✗ (deny の 24/26 が `grep ... || <deny 対象>`)
- bucket: refine — 対象は entry ではなく複合行の組み立て方。entry 変更は不要

## keep

### 3. Bash(some-readonly-cmd:*)  [allow, project_local]
- rule_fired: revoke_candidate / match_count 0
- open_predicates: side_effect_capability ✗ (read-only)
- bucket: keep (未使用だが無害 — 削除は ask 反復コストだけ増える)

## informational — near_misses

- Read(**/*.env) : revoke_candidate の matcher_exact を外した (approx)
- Bash(gh:*) : revoke_candidate の no_sandbox_pair を外した (excludedCommands と連動)

## informational — PreToolUse hook の所要 / tool 呼び出し (Bash, 12,480 calls)

- non_global: p50 210ms / p90 480ms / max 6,100ms
- all: p50 230ms / p90 520ms / max 6,100ms

## summary

| # | bucket | 対象 | scope | セッション内提案 |
|---|---|---|---|---|
| 1 | revoke | Bash(some-unused-remote-write-cmd:*) | project_local | - (低確度: exposure_opportunity が推測) |
| 2 | refine | Bash(grep:*) | project_local | - (低確度) |
| 3 | keep | Bash(some-readonly-cmd:*) | project_local | - (低確度) |
```
