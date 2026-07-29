---
name: inventory-claude-md
disable-model-invocation: true
description: project の CLAUDE.md (root + サブディレクトリ + `.claude/rules/*.md`) を静的観測し、行単位で 6 bucket (keep-inline / move-to-path-scoped / move-to-skill / move-to-lint / delete / merge) の候補提示まで LLM に運ばせる棚卸し。全候補を候補レポートに載せ、判定と適用は人間 (worktree + PR)。global CLAUDE.md は読みも書きもしない。Use when「CLAUDE.md 棚卸し」「CLAUDE.md を整理」「rules/ に切り出したい」「inventory-claude-md」「棚卸し」.
---

# inventory-claude-md

project 範囲の CLAUDE.md 系 (root `CLAUDE.md` + サブディレクトリ CLAUDE.md + `.claude/rules/*.md`) を、静的に観測し **行単位で 6 bucket の候補**を LLM に提示させ、判定は人間に残す棚卸し skill。

3 段階モデル (原則: **観測・集計は決定的に、判断は人間に、LLM は文章の具体化のみ**):

1. **決定的観測**: `scripts/scan-claude-md.py` が対象ファイル群を走査し、observation JSON を出力する
2. **LLM 具体化 (このメインコンテキスト)**: JSON を読み、行単位で bucket 候補と根拠、提案文面を組み立てる。**判定はしない**
3. **人間判定**: 適用/却下を選ぶのは常に人間。本 skill はレポート提示で止まり、承認後の CLAUDE.md 編集は人間 (worktree + PR)

**確度による 2 層化は持たない** — 既存 CLAUDE.md 行の bucket 判定はすべて内容判断 (常時参照か・汎用か等) を伴い、count 0 / 完全一致のような閾値解釈の余地がない決定的シグナルが存在しないため。全候補が同じレポート経路を通る ([ADR 0018](https://github.com/swat9013/swat-skills/blob/main/docs/adr/0018-rule-pipeline-decommission.md) — 層 1 の唯一の供給源だった `#rule` buffer の撤去に伴う)。

## スコープ (改変不可の境界)

- **読み対象**: project の CLAUDE.md 系のみ (`<repo>/CLAUDE.md` + サブディレクトリ CLAUDE.md + `<repo>/.claude/rules/*.md`)
- **書き対象**: **なし**。本 skill からの write はゼロ — 成果物は `/tmp/inventory-claude-md/` のレポートのみで、CLAUDE.md / rules への反映は人間が worktree + PR で行う
- **除外**: `~/.claude/CLAUDE.md` (global) は読みも書きもしない。global 昇格は本 skill の機能外 — 人間が chezmoi 側で別途行う
- **非依存**: transcript (`~/.claude/projects/`) / claude-mem / dotfiles / swat-skills 固有 gate 実装。すべて静的 repo 内 file のみ

**汎用スキル制約**: 参照するのは Claude Code 標準ファイルと `claude-config-review` の references のみ。repo 固有の gate 構成 (pre-commit gate script の有無) には触らない — 「lint 化可能か」の判断は LLM 段階で提案するが、配線は repo 側の別作業。

## 引数

現状 project section 固定 (cwd の CLAUDE.md 系)。省略時に project、他 section は無し (global は仕様上除外)。

## 手順

### 1. 観測 script 起動 (決定的)

```
${CLAUDE_SKILL_DIR}/scripts/scan-claude-md.py --repo-root .
```

- `--repo-root` 省略時 cwd
- stdout に observation JSON path (`/tmp/inventory-claude-md/observation-<timestamp>.json`) が出る
- **script は bucket を出さない** (循環依存の回避)。決定的観測 3 項目だけを出す

出力に失敗したら (repo に CLAUDE.md が無い等) `sources.claude_md.root.status = "missing"` が入る。root すら無ければ「観測不能」を報告して終了する。

### 2. observation JSON を読んで bucket 候補を提示 (LLM)

observation の schema は script docstring 参照。以下の順に組み立てる。

**bucket vocabulary** (script はこの語彙を知らない。ここで初めて割り当てる):

| bucket | 判定基準 (要旨) | 提案先 |
|---|---|---|
| **keep-inline** | 常時参照される汎用規範 (asking policy / 言語 / 責務分離原則等) | CLAUDE.md 本文の残置 |
| **move-to-path-scoped** | 特定ディレクトリ配下の作業でのみ必要 | `.claude/rules/<topic>.md` + `paths:` frontmatter |
| **move-to-skill** | 特定タスク・状況でのみ必要な手順 | 既存 skill 追記 / 新規 skill |
| **move-to-lint** | 決定的検査に置換できる規則 (行数 / path / 重複 / 参照実在等) | repo の gate/hook (配線は repo 側の仕事) |
| **delete** | 参照切れ / 陳腐化 / 解決済み問題の残骸 | 削除 |
| **merge** | 他行と重複 | 統合先の行番号を提示 |

**bucket 割当ての制約 (script との責務分担)**:

- LLM は observation JSON の証拠に**紐付けて**割り当てる。「常時参照される」を主張するなら「先頭 N section 以内 / 他 skill から参照される / 反復失敗の記録がある」等の**可視な根拠**を書く
- 決定的観測を超える推論には `推測:` prefix を必ず付ける (例: `推測: このルールは issue-dispatch 実行時のみ必要と読める`)
- lint 化可否は LLM の判断で、根拠に**適用可能な判定形式** (regex / AST / 静的解析) を 1 行明示する。実 gate 配線は書かない
- 参照実在 fail は `move-to-lint` の証拠にできる。ただし fail 一件で削除を主張しない (書き手の誤植 vs 陳腐化を区別できない)
- **inline code span (backtick 内) の path は script の `link_targets` に出ない**。`[text](target)` markdown link と `@path` import のみが検査対象。バッククォート内で書かれた `docs/foo.md` は observation JSON に無いため、参照実在の主張はしない (「link 抽出仕様の範囲外」を明記)
- 6 bucket の**どれにも該当しないなら informational に落として理由を書く** (握り潰さない)

**bucket 割当ての粒度規約**:

- 単位は**行 or 行群**。1 section 内に異質な bucket 候補が混在する場合、section 全体を informational にせず、**連続する同 bucket 候補の行群を 1 単位で切り出す**。単一行の bucket 分割も可
- 見出し (H2 / H3) 自体は section 境界の情報として扱い、単独では bucket 割当ての対象にしない。見出しを含む行群を単位にする場合は「対象」の line-range に見出し行番号を含める

### 3. Markdown レポート組み立て

`/tmp/inventory-claude-md/report-<timestamp>.md` に observation JSON と並置で書く。以下の**固定 schema**:

1. **ヘッダ**: 観測時刻 / 対象 file 一覧 / 総行数 / @import 展開数 / 参照実在 fail 数
2. **候補 section** (bucket 別に列挙):
    - 単位: `file:line-range`
    - 対象テキスト (抜粋 or 全文、200 char 超は truncate)
    - bucket 候補 (上表の語彙)
    - 証拠 (observation JSON の観測項目を転記)
    - 提案 (1-2 行、証拠に紐づく)
    - 適用手順 (bucket 別分岐、下表参照)
3. **informational**: 6 bucket の**どれにも該当しない**行の一覧と理由 (bucket vocabulary の適用先が不明な行 / 用途不明な行)
4. **観測範囲外 (人間確認へ回す)**: 参照実在検査が fail-safe で判断不能だった行 / inline code span の path 参照 / observation JSON の穴。**bucket 判定の証拠として使わない**行をここに集める (罠表参照)
5. **meta 観測**: CLAUDE.md 自体の物理計測 (行数 / 見出し階層 / @import 数)。閾値 enforce はしない — 「参考値」として提示のみ
6. **summary 表**: 番号 × bucket × 対象 × scope。「3 と 7 だけ採用」と言える形

**観測由来の事実と LLM 推測の分離**: レポートの証拠欄には observation JSON の値を**そのまま転記**する。LLM の解釈を混ぜる場合は `推測:` prefix を必ず付けて 1 行に留める。

**適用手順の bucket 別分岐** (レポートに埋め込む):

| bucket | 適用手順 |
|---|---|
| keep-inline / delete / merge | worktree + PR で `<repo>/CLAUDE.md` を編集 |
| move-to-path-scoped | 新 `.claude/rules/<slug>.md` を作成し `paths:` frontmatter を宣言 + CLAUDE.md の対象行を削除 (or 索引 stub 残置)。詳細は `claude-config-review/references/rules.md` を参照 |
| move-to-skill | 対象 skill の SKILL.md に追記 (新規 skill なら別 skill として起票)。詳細は `claude-config-review/references/skill.md` を参照 |
| move-to-lint | gate/hook の要件文まで書き、実装は別セッション。本 skill は配線しない |

### 4. LLM 段階で必ず Read する reference

bucket 提案文面を作る際は同一 plugin 内の以下を **判定前に Read** する (汎用スキル制約下で許容される参照):

- 常に: `${CLAUDE_SKILL_DIR}/../../knowledge/claude-config-review/references/claude-md.md`
- `move-to-path-scoped` 提案時: 同 `rules.md`
- `move-to-skill` 提案時: 同 `skill.md`

**`${CLAUDE_SKILL_DIR}` 展開後のフルパス例** (相対 path の暗算ミスで File not found を誘発しないよう明示):

- `${CLAUDE_SKILL_DIR}` = `~/.claude/skills/swat-skills/skills/steering/inventory-claude-md`
- 展開後 = `~/.claude/skills/swat-skills/skills/knowledge/claude-config-review/references/<name>.md`
- **`skills/` セグメントが 2 回現れる** (`swat-skills/skills/knowledge/`) 点に注意。`swat-skills/knowledge/` は File not found

reference の checklist を提案文面に**引用しない**。用語と判断軸を借りるのみ。references はレビュー観点の正本なので **references 側の変更は本 skill を追従改修**する。

### 5. 人間判定

レポートを提示するところで skill の責務は終わる。承認 → 適用は次のセッション (or 別の worktree) で人間が実施する。CLAUDE.md / rules への write は本 skill からは行わない。

## 出力例 (概略)

```
# CLAUDE.md 棚卸しレポート

観測時刻: 2026-07-18T00:12:34Z
対象: CLAUDE.md (156 lines) / .claude/CLAUDE.md (無し) / .claude/rules/ (2 files)
meta 観測: 見出し H2 x 12 / H3 x 8 / @import 展開 3 / 参照実在 fail 1

## keep-inline

### 1. CLAUDE.md:14-22  「## 責務」
- 対象: (抜粋) skill / hook を独立 git repo として管理し...
- bucket: keep-inline
- 証拠: 先頭 2 section 以内 / 他 skill 本文から参照される (根拠: observation.cross_refs)
- 提案: 現状維持

## move-to-path-scoped

### 2. CLAUDE.md:78-84  「skill script path 変更時は...」
- bucket: move-to-path-scoped
- 証拠: skill/hook 編集時のみ発火する規則。他 file 編集では無関係
- 提案: `.claude/rules/skill-script-permissions.md` に切り出し、`paths: "skills/**/SKILL.md, settings/settings.local.json"` を宣言
- 適用手順: 新 rules file を作成 + CLAUDE.md の対象行を削除 (or 索引 stub 残置)
- 推測: `settings/settings.local.json` は `.claude/settings.local.json` とは別 file だが glob 上両方を包含できる

## move-to-lint

### 3. CLAUDE.md:56  「個人パスのハードコード禁止」
- bucket: move-to-lint
- 証拠: 決定的検査可能 (regex `/Users/[^s-watanabe]`)。実際 `.githooks/pre-commit` に `lint-personal-paths.py` 実装済
- 提案: CLAUDE.md 行は残しつつ「(gate で自動検査)」注記を追加 or 完全に削除 (gate が実 enforcer)

## delete

### 4. CLAUDE.md:120  「// TODO: xxx (2025-11 対応)」
- bucket: delete
- 証拠: 参照先 issue が close 済 (observation.stale_refs)
- 提案: 削除

## merge

### 5. CLAUDE.md:92 / CLAUDE.md:140  「file paths を quote」
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
| 2 | move-to-path-scoped | CLAUDE.md:78-84 | project |
| 3 | move-to-lint | CLAUDE.md:56 | project |
| 4 | delete | CLAUDE.md:120 | project |
| 5 | merge | CLAUDE.md:92 / 140 | project |
```

## 責務

- 6 bucket の**判定は常に人間** (3 段階モデル不変)
- 観測 → 候補提示 → 適用手順の明示までで止まる。適用 (CLAUDE.md 編集 / rules 新設 / skill 追記 / gate 配線) はレポート提示外の作業として skill から切り離す
- global CLAUDE.md への波及は skill の機能外

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| CLAUDE.md が観測不能 | root にファイルが無い | `sources.claude_md.root.status = "missing"` を確認し「観測不能」を報告して終了 (推測での穴埋めをしない) |
| 参照実在 fail が過剰 | git worktree 内でルート相対 path が解決できない / 動的展開文字列を path として拾った | `observation.link_targets.check_mode` を確認し `fail-safe` 分は削除の主根拠にしない。レポート §4「観測範囲外」に集約 |
| inline code span の path が参照実在に出ない | script の link 抽出は `[text](target)` と `@import` のみ。バッククォート内の path は拾わない (`docs/foo.md` 等) | 「参照実在検査の範囲外」を明記し、削除の主根拠にしない。レポート §4「観測範囲外」に集約 |
| サブディレクトリ CLAUDE.md を root と混同 | root と subdir で規範が競合しても物理観測では区別できない | subdir CLAUDE.md は**読取対象に含めるが提案先には推奨しない** (rules/skill への昇華を提案する方向に倒す) |
| bucket を script に埋め込みたくなる | 循環依存 (script が bucket を知ると LLM が再判定できなくなる) | script は生観測のみ。bucket 判定は LLM 段階で observation を読んでから |
| 推測と観測の混同 | LLM が観測 JSON に無い解釈を根拠として書く | すべての解釈行に `推測:` prefix を付ける。prefix 無しの主張は observation JSON からの直接引用のみ |
| global 昇格を持ち出しそうになる | 「これはグローバル価値観に見える」と判断が擦り抜ける | 本 skill の機能外。レポートに「(参考) 汎用性が高い可能性 — 判定は chezmoi 側で人間実施」と informational に落とすまで |
| セッション内で CLAUDE.md を直接編集したくなる | 「証拠が濃く、承認も取れそう」に見える誘惑 | 本 skill の write 対象はゼロ。適用は必ずレポート提示外の人間作業 (worktree + PR) に落とす |

## 参照

- 仕様確定: [issue #214 コメント](https://github.com/swat9013/swat-skills/issues/214)
- 上位 map: [issue #209](https://github.com/swat9013/swat-skills/issues/209)
- buffer 依存の撤去: [ADR 0018](https://github.com/swat9013/swat-skills/blob/main/docs/adr/0018-rule-pipeline-decommission.md)
- 関連 skill: `inventory-permissions` (別軸: permission / sandbox / hook) / `inventory-skill-mcp` (別軸: skill / MCP 実績集計)
- reference (Read 対象): `${CLAUDE_SKILL_DIR}/../../knowledge/claude-config-review/references/{claude-md,rules,skill}.md`
