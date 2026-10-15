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
- 提案: `.claude/rules/skill-script-permissions.md` に切り出し、`paths: "skills/**/SKILL.md, .claude/settings.local.json"` を宣言
- 適用手順: 新 rules file を作成 + CLAUDE.md の対象行を削除 (or 索引 stub 残置)
- 推測: script path の変更は SKILL.md と settings の allow entry を同時に触るので、paths に両方を並べればどちらの編集でも規則が読み込まれる

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

### 7. CLAUDE.md:31  「エラーメッセージは読んでから対処する」
- bucket: delete
- 証拠: 診断表「既定どおりの行 (no-op)」に当たる / micro-test (1 場面・各 1 回): 場面「テストが失敗した直後に次の手を決める」/ あり: 失敗出力を読んで原因箇所を特定してから修正 / なし: 同じく失敗出力を読んでから修正 → 取る行動が一致 / est_tokens: 12
- 提案: 削除
- 適用手順: 対象 file (CLAUDE.md) の該当行を編集

## merge

### 8. CLAUDE.md:92 / CLAUDE.md:140  「file paths を quote」
- bucket: merge
- 証拠: 双方が同義の規範を重複記述 (observation の該当 2 行を引用)
- 提案: CLAUDE.md:92 に統合し 140 行目を削除
- 停止理由: <write-for-harness が返した文を逐語で転記>

## rewrite

### 9. CLAUDE.md:48  「テストを書かずに PR を出さないこと」
- bucket: rewrite
- 証拠: 診断表「禁止の形で書かれた規範」に当たる / est_tokens: 18 (estimator: <token_cost.estimator を転記>)
- 提案: 肯定の形へ書き換え (例: 「PR を出す前に、変更した振る舞いのテストを足す」)
- 適用手順: 対象 file (CLAUDE.md) の該当行を編集

## informational

- CLAUDE.md:130  「Revision Policy」— 決定的観測では bucket 割当てなし。用途不明のため人間確認
- CLAUDE.md:66  「変更は小さく保つ」— no-op の疑い・未検証 (micro-test の上限から外れた)

## 観測範囲外 (人間確認へ回す)

- CLAUDE.md:135  「詳細は `docs/agents/foo.md` を参照」— inline code span の path は script の link_targets に出ない。参照実在の判断は本 skill の範囲外
- CLAUDE.md:88  参照実在 fail 1 件 (link_targets[3]) — check_mode=relative-path で解決先が見つからないが、書き手誤植 vs 陳腐化を区別できない

## summary

| # | bucket | 対象 | scope | 採否 |
|---|---|---|---|---|
| 1 | keep-inline | CLAUDE.md:14-22 | project | - |
| 2 | keep-inline | CLAUDE.local.md:8 | project-local | - |
| 3 | move-to-path-scoped | CLAUDE.md:78-84 | project | 反映済み |
| 4 | move-to-path-scoped (共有化) | CLAUDE.local.md:11-13 | project-local → project | 見送り |
| 5 | move-to-lint | CLAUDE.md:56 | project | - |
| 6 | delete | CLAUDE.md:120 | project | 反映済み |
| 7 | delete | CLAUDE.md:31 | project | 未判定 (打ち切り) |
| 8 | merge | CLAUDE.md:92 / 140 | project | 承認済み・未反映 |
| 9 | rewrite | CLAUDE.md:48 | project | 反映済み |
```
