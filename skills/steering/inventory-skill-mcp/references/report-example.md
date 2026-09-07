# 棚卸しレポートの記入例

手順 4 の固定 schema (ヘッダ / 候補 section / informational / summary 表) を埋めた形の概略。

```
# skill/MCP 棚卸しレポート

観測窓: 2026-06-17 〜 2026-07-17 (30 日) / 総 invocation 245 / distinct sessions 42
判定可能性: sufficient (総数 >= 30)

## delete-candidate

### 1. <plugin-a>:<skill-x>
- rule_fired: unused_skill / count 0 / sessions_presented 38 / 分母 source config
- open_predicates: rename_or_removal_in_window ✗ (git log に改名なし) /
  denominator_completeness ○ / removable_independently ○ (`/plugin` で個別 disable 可)
- bucket: delete-candidate
- 適用手順: `/plugin` 操作で該当 plugin から...

## review-candidate

### 2. swat-skills:apm-skill-add
- count 3 / share 1.2% / rank 22 / percentile 0.72 / outcomes {success: 2, error: 1}
- 証拠: (session 069b... 2026-07-15 ...)
- 推測: apm 経由の vendoring は特定局面でしか使わないため、trigger 語彙の妥当性を...

## informational — near_misses

- <plugin-b>:<skill-y> : unused_skill の presented_at_least_once を外した
  (sessions_presented 0 = 提示されていない。不使用の証拠にならない)

## summary

| # | bucket | 対象 | 単位 | セッション内提案 |
|---|---|---|---|---|
| 1 | delete-candidate | <plugin-a>:<skill-x> | skill | 提案済み (承認 → 適用) |
| 2 | review-candidate | swat-skills:apm-skill-add | skill | - (低確度) |
```
