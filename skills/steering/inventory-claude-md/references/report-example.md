# レポート出力例

`inventory-claude-md` が組み立てる棚卸しレポートの記入例 (概略)。**固定 schema の正本は SKILL.md 手順 3** で、本 file はその具体化 — 各 section を実際にどう書き下すかが掴めないときに Read する。

```
# CLAUDE.md 棚卸しレポート

観測時刻: 2026-07-18T00:12:34Z
対象: CLAUDE.md (156 lines) / CLAUDE.local.md (14 lines) / .claude/CLAUDE.md (無し) / .claude/rules/ (2 files)
meta 観測 (root): 見出し H2 x 12 / H3 x 8 / @import 展開 3 / 参照実在 fail 1
meta 観測 (project-local): 見出し H2 x 2 / @import 展開 0 / 参照実在 fail 0

## keep-inline

### 1. CLAUDE.md:14-22  「## 責務」
- 対象: (抜粋) skill / hook を独立 git repo として管理し...
- bucket: keep-inline
- 証拠: 先頭 2 section 以内 / 他 skill 本文から参照される (根拠: observation.cross_refs)
- 提案: 現状維持

### 2. CLAUDE.local.md:8  「ローカル DB は `~/.local/share/<app>/dev.sqlite` を使う」
- bucket: keep-inline (残置先は `CLAUDE.local.md`)
- 証拠: sources.claude_md.local (label: project-local) の行。マシン固有 path を含む
- 提案: 共有化せず local 残置。この行が project-local に置かれている理由が内容 (個人環境の path) から読み取れる

## move-to-path-scoped

### 3. CLAUDE.md:78-84  「skill script path 変更時は...」
- bucket: move-to-path-scoped
- 証拠: skill/hook 編集時のみ発火する規則。他 file 編集では無関係
- 提案: `.claude/rules/skill-script-permissions.md` に切り出し、`paths: "skills/**/SKILL.md, settings/settings.local.json"` を宣言
- 適用手順: 新 rules file を作成 + CLAUDE.md の対象行を削除 (or 索引 stub 残置)
- 推測: `settings/settings.local.json` は `.claude/settings.local.json` とは別 file だが glob 上両方を包含できる

### 4. CLAUDE.local.md:11-13  「migration script を流す前に必ず dry-run する」
- bucket: move-to-path-scoped
- 証拠: sources.claude_md.local (label: project-local) の行。個人環境固有の要素を含まず、`db/migrate/**` 編集時のみ発火する規則
- 提案: **共有化**: `.claude/rules/migration-dry-run.md` に切り出し `paths: "db/migrate/**"` を宣言。移動先は tracked なので**他の作業者・他マシンにも配布される** — この内容を共有してよいか人間に確認する
- 適用手順: rules file の新設は実行 project の運用に従って反映。`CLAUDE.local.md` 側の行削除は `.gitignore` 済みなら version control に乗らないため別作業 (2 経路)

## move-to-lint

### 5. CLAUDE.md:56  「個人パスのハードコード禁止」
- bucket: move-to-lint
- 証拠: 決定的検査可能 (regex `/Users/[^s-watanabe]`)。実際 `.githooks/pre-commit` に `lint-personal-paths.py` 実装済
- 提案: CLAUDE.md 行は残しつつ「(gate で自動検査)」注記を追加 or 完全に削除 (gate が実 enforcer)

## delete

### 6. CLAUDE.md:120  「// TODO: xxx (2025-11 対応)」
- bucket: delete
- 証拠: 参照先 issue が close 済 (observation.stale_refs)
- 提案: 削除

## merge

### 7. CLAUDE.md:92 / CLAUDE.md:140  「file paths を quote」
- bucket: merge
- 証拠: 双方が同義の規範を重複記述 (observation の該当 2 行を引用)
- 提案: CLAUDE.md:92 に統合し 140 行目を削除

## informational

- CLAUDE.md:130  「Revision Policy」— 決定的観測では bucket 割当てなし。用途不明のため人間確認

## 観測範囲外 (人間確認へ回す)

- CLAUDE.md:135  「詳細は `docs/agents/foo.md` を参照」— inline code span の path は script の link_targets に出ない。参照実在の判断は本 skill の範囲外
- CLAUDE.md:88  参照実在 fail 1 件 (link_targets[3]) — check_mode=relative-path で解決先が見つからないが、書き手誤植 vs 陳腐化を区別できない

## summary

| # | bucket | 対象 | scope |
|---|---|---|---|
| 1 | keep-inline | CLAUDE.md:14-22 | project |
| 2 | keep-inline | CLAUDE.local.md:8 | project-local |
| 3 | move-to-path-scoped | CLAUDE.md:78-84 | project |
| 4 | move-to-path-scoped (共有化) | CLAUDE.local.md:11-13 | project-local → project |
| 5 | move-to-lint | CLAUDE.md:56 | project |
| 6 | delete | CLAUDE.md:120 | project |
| 7 | merge | CLAUDE.md:92 / 140 | project |
```
