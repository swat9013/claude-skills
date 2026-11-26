# レポート出力例

`inventory-claude-md` が組み立てる棚卸しレポートの記入例 (概略)。**固定 schema の正本は SKILL.md 手順 3** で、本 file はその具体化。

候補の番号は識別子で、本文での出現順ではない。例の証拠欄が引く診断表の行: [既定どおりの行 (no-op)](../../write-for-harness/references/patches.md#diag-no-op) / [禁止の形で書かれた規範](../../write-for-harness/references/patches.md#diag-prohibition)

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
- 証拠: reach.scoped_lines に載らない
- 根拠: 責務の分け方はどの file を触るときも、設計・レビューの議論でも判断を変える (触る file も作業の種類も問わず効く)
- 提案: 現状維持

### 11. CLAUDE.md:30  「レビューの前に `docs/agents/review.md` を Read する」
- bucket: keep-inline
- 証拠: reach.scoped_lines に載る (source: CLAUDE.md:30, matched: 「前に」) / read-sessions: docs/agents/review.md 0 (sessions_observed: 42)
- 根拠: 索引行
- 提案: 現状維持。Read が 0 なのは、効いていないか、レビューの場面が起きていないかのどちらか — 人が決める

### 2. CLAUDE.local.md:8  「ローカル DB は `~/.local/share/<app>/dev.sqlite` を使う」
- bucket: keep-inline (残置先は `CLAUDE.local.md`)
- 証拠: sources.claude_md.local (label: project-local) の行。マシン固有 path を含む
- 提案: 共有化せず local 残置。この行が project-local に置かれている理由が内容 (個人環境の path) から読み取れる

## move-to-path-scoped

### 3. CLAUDE.md:78-84  「skill script path 変更時は...」
- bucket: move-to-path-scoped
- 証拠: reach.scoped_lines に載る (source: CLAUDE.md:79, matched: `skills/**/SKILL.md`) / est_tokens: 64 (estimator: <token_cost.estimator を転記>)
- 根拠: SKILL.md か settings を tool で書き換える場面でしか要らない。compaction の後に消えても、次にその file を触れば載り直す
- 提案: `.claude/rules/skill-script-permissions.md` に切り出し、`paths: "skills/**/SKILL.md, .claude/settings.local.json"` を宣言
- 適用手順: 新 rules file を作成 + CLAUDE.md の対象行を削除 (or 索引 stub 残置)
- 推測: script path の変更は SKILL.md と settings の allow entry を同時に触るので、paths に両方を並べればどちらの編集でも規則が読み込まれる

### 4. CLAUDE.local.md:11-13  「migration script を流す前に必ず dry-run する」
- bucket: move-to-path-scoped
- 証拠: sources.claude_md.local (label: project-local) の行。個人環境固有の要素を含まず、`db/migrate/**` 編集時のみ発火する規則
- 提案: **共有化**: `.claude/rules/migration-dry-run.md` に切り出し `paths: "db/migrate/**"` を宣言。移動先は tracked なので**他の作業者・他マシンにも配布される** — この内容を共有してよいか人間に確認する
- 適用手順: rules file の新設は実行 project の運用に従って反映。`CLAUDE.local.md` 側の行削除は `.gitignore` 済みなら version control に乗らないため別作業 (2 経路)

## move-to-doc

### 10. CLAUDE.md:96-104  「## ADR の書き方」
- bucket: move-to-doc
- 証拠: reach.scoped_lines に載る (source: CLAUDE.md:97, matched: 「とき」) / est_tokens: 210 (estimator: <token_cost.estimator を転記>)
- 根拠: ADR を書く場面でしか要らない。書く前の設計の議論 (file を触らない工程) でも要るので、path-scoped の rules では届かない
- 提案: 本文を `docs/how-to/adr.md` (新規) に移し、CLAUDE.md の対象行を索引行「ADR を書く・直す前に `docs/how-to/adr.md` を Read する」に置き換える
- 適用手順: 新しい文書を作成 (追加) + CLAUDE.md の対象行を索引行へ書き換え (書き換え)

## move-to-lint

### 5. CLAUDE.md:56  「個人パスのハードコード禁止」
- bucket: move-to-lint
- 証拠: est_tokens: 15 (estimator: <token_cost.estimator を転記>)
- 根拠: 診断表「機械が判定でき例外の無い規範」に当たる。判定形式: regex `/Users/[^/]+/`。移し先: lint
- 提案: 束「lint」(行: #5) の設問で問い、採られたら依頼文で起票する

## delete

### 6. CLAUDE.md:120  「// TODO: xxx (2025-11 対応)」
- bucket: delete
- 証拠: 参照先 issue が close 済 (observation.stale_refs)
- 提案: 削除

### 7. CLAUDE.md:31  「エラーメッセージは読んでから対処する」
- bucket: delete
- 証拠: 診断表「既定どおりの行 (no-op)」に当たる / 判定: この行が無くても、失敗出力を読んでから修正する / est_tokens: 12 (estimator: <token_cost.estimator を転記>)
- 提案: 削除
- 適用手順: 対象 file (CLAUDE.md) の該当行を編集

## merge

### 8. CLAUDE.md:92 / CLAUDE.md:140  「file paths を quote」
- bucket: merge
- 証拠: 双方が同義の規範を重複記述 (observation の該当 2 行を引用)
- 提案: CLAUDE.md:92 に統合し 140 行目を削除
- 止まった操作: write-for-harness
- 返した文: <write-for-harness が返した `未反映:` 行と `復旧:` 行を逐語で転記。文が無ければ「文を返さずに終わった」>
- 書かれた file: なし (反映先は CLAUDE.md の 1 本)

## rewrite

### 9. CLAUDE.md:48  「テストを書かずに PR を出さないこと」
- bucket: rewrite
- 証拠: 診断表「禁止の形で書かれた規範」に当たる / est_tokens: 18 (estimator: <token_cost.estimator を転記>)
- 提案: 肯定の形へ書き換え (例: 「PR を出す前に、変更した振る舞いのテストを足す」)
- 適用手順: 対象 file (CLAUDE.md) の該当行を編集

## informational

- CLAUDE.md:130  「Revision Policy」— 決定的観測では bucket 割当てなし。用途不明のため人間確認

## 観測範囲外 (人間確認へ回す)

- CLAUDE.md:135  「手順は `scripts/dev/gate.sh` を参照」— code span の `*.md` 以外の path は実在を検査しない。参照実在の判断は本 skill の範囲外
- read-sessions: 数えていない経路 — repo の subdirectory で始めた session と、symlink 経由など cwd の外の path で読んだ Read は sessions_read に入らない。0 の索引行 (docs/agents/review.md) は読まれていないことの証拠にしない
- CLAUDE.md:88  参照実在 fail 1 件 (link_targets[3]) — check_mode=relative-path で解決先が見つからないが、書き手誤植 vs 陳腐化を区別できない

## summary

| # | bucket | 対象 | scope | 採否 |
|---|---|---|---|---|
| 1 | keep-inline | CLAUDE.md:14-22 | project | - |
| 2 | keep-inline | CLAUDE.local.md:8 | project-local | - |
| 3 | move-to-path-scoped | CLAUDE.md:78-84 | project | 反映済み |
| 4 | move-to-path-scoped (共有化) | CLAUDE.local.md:11-13 | project-local → project | 見送り |
| 5 | move-to-lint | CLAUDE.md:56 | project | 起票済み (`<issue 置き場>#<N>`) |
| 6 | delete | CLAUDE.md:120 | project | 反映済み |
| 7 | delete | CLAUDE.md:31 | project | `undecided` |
| 8 | merge | CLAUDE.md:92 / 140 | project | 承認済み・未反映 |
| 9 | rewrite | CLAUDE.md:48 | project | 反映済み |
| 10 | move-to-doc | CLAUDE.md:96-104 | project | 反映済み |
| 11 | keep-inline | CLAUDE.md:30 | project | - |

undecided: 1 件 (#7)

## 届き方の数

| 数 | 前 | 後 |
|---|---|---|
| 移す候補にも根拠つきの keep-inline にもなっていない、場面か path を絞る語を持つ行 | 1 | 2 |
| どこからも辿れない agent 向け文書 | 1 | 1 |
| 索引行と Read させる行の指す先が実在しない | 1 | 1 |
| 同じ本文が 2 か所以上にある | 1 | 1 |
| 常時載る量 (est_tokens / chars) | 4,120 / 9,870 | 3,560 / 8,240 |

- 合否 (後): 不合格 — 4 つとも 0 でない
- 0 でない数 (後): 場面か path を絞る語を持つ行 = CLAUDE.md:135 (どの候補にもならず §4 に回した) / CLAUDE.md:48 (#9 の書き換えで「前に」を持った。#4 は見送ったが、この実行の候補として覆われる) ・辿れない文書 = docs/agents/old-flow.md ・実在しない参照 = CLAUDE.md:88 → docs/gone.md ・写し = CLAUDE.md:92 / 140 (#8 は承認済み・未反映)
- 索引行が指す文書の Read session 数 (分母 sessions_observed: 42): docs/architecture.md 17 / docs/agents/review.md 0 (効いていないか、レビューの場面が起きていないか — 人が決める)
```
