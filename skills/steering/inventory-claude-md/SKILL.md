---
name: inventory-claude-md
disable-model-invocation: true
description: project の CLAUDE.md (root + サブディレクトリ + `.claude/rules/*.md` + `~/.claude/state/rules/` の `#rule` バッファ (captured/archive)) を静的観測し、行単位で 6 bucket (keep-inline / move-to-path-scoped / move-to-skill / move-to-lint / delete / merge) の候補提示まで LLM に運ばせる棚卸し。高確度候補 (buffer entry 限定 — 完全一致 merge / cross-repo delete) はセッション内で AskUserQuestion 提案し承認後に archive 適用、低確度候補はレポート提示。判定は人間。global CLAUDE.md は読みも書きもしない。Use when「CLAUDE.md 棚卸し」「CLAUDE.md を整理」「rules/ に切り出したい」「#rule で貯めたルールを昇格」「ルールバッファ消費」「inventory-claude-md」「棚卸し」.
---

# inventory-claude-md

project 範囲の CLAUDE.md 系 (root `CLAUDE.md` + サブディレクトリ CLAUDE.md + `.claude/rules/*.md` + `~/.claude/state/rules/` の `#rule` バッファ (captured/archive)) を、静的に観測し **行単位で 6 bucket の候補**を LLM に提示させ、判定は人間に残す棚卸し skill。

3 段階モデル (原則: **観測・集計は決定的に、判断は人間に、LLM は文章の具体化のみ**):

1. **決定的観測**: `scripts/scan-claude-md.py` が対象ファイル群を走査し、observation JSON を出力する
2. **LLM 具体化 (このメインコンテキスト)**: JSON を読み、行単位で bucket 候補と根拠、提案文面を組み立てる。**判定はしない**
3. **人間判定**: 適用/却下を選ぶのは常に人間。提示は確度で 2 層に分ける — **高確度候補** (手順 3 の buffer entry 2 型) はセッション内で AskUserQuestion 提案し、承認されたら同セッション内で適用 (`archive.jsonl` append) に進む。**低確度候補**はレポート提示で止まり、承認後の CLAUDE.md 編集は人間 (worktree + PR)

3 段階モデルの原則は 2 層化後も**不変** — 変わるのは判定後の適用タイミングと提示 UI のみ。承認なしの write はゼロ (適用は必ず AskUserQuestion での人間承認を経る)。

## スコープ (改変不可の境界)

- **読み対象**: project の CLAUDE.md 系のみ (`<repo>/CLAUDE.md` + サブディレクトリ CLAUDE.md + `<repo>/.claude/rules/*.md` + `~/.claude/state/rules/{captured,archive}.jsonl`)。前 3 者は observation の対象、buffer は取り込み候補として並置 (captured.jsonl を fold し archive.jsonl 記載 id を除外した pending が候補)
- **書き対象**: `~/.claude/state/rules/archive.jsonl` のみ、かつ **AskUserQuestion での人間承認後に限る** (手順 3 の高確度候補)。CLAUDE.md / rules / captured.jsonl へは書かない。**承認なしの write はゼロ** — 編集提案は Markdown レポートまで
- **除外**: `~/.claude/CLAUDE.md` (global) は読みも書きもしない。global 昇格は本 skill の機能外 — 人間が chezmoi 側で別途行う
- **非依存**: transcript (`~/.claude/projects/`) / claude-mem / dotfiles / swat-skills 固有 gate 実装。すべて静的 repo 内 file と home 固定 buffer path のみ

**汎用スキル制約**: 参照するのは Claude Code 標準ファイルと `claude-config-review` の references のみ。repo 固有の gate 構成 (pre-commit gate script の有無) には触らない — 「lint 化可能か」の判断は LLM 段階で提案するが、配線は repo 側の別作業。

## 引数

現状 project section 固定 (cwd の CLAUDE.md 系 + buffer)。省略時に project、他 section は無し (global は仕様上除外)。

## 手順

### 1. 観測 script 起動 (決定的)

```
${CLAUDE_SKILL_DIR}/scripts/scan-claude-md.py --repo-root . --buffer-dir ~/.claude/state/rules
```

- `--repo-root` 省略時 cwd
- `--buffer-dir` 省略時 `~/.claude/state/rules` (#212 の仕様。存在しなくても script は続行し `buffer.status = "missing"` を返す)
- stdout に observation JSON path (`/tmp/inventory-claude-md/observation-<timestamp>.json`) が出る
- **script は bucket を出さない** (循環依存の回避)。決定的観測 3 項目 + buffer 取り込みだけを出す

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

**buffer entry の初期扱い**: observation の `buffer.pending.entries` (captured.jsonl を fold し archive.jsonl 済みを除いた残り) は必ず 6 bucket のいずれかに割り当てる。既存 CLAUDE.md 行と重複するなら `merge`、path-scoped が適切なら `move-to-path-scoped`、汎用なら `keep-inline` として CLAUDE.md への挿入位置を提案。**buffer 側を残置する第 7 の bucket は作らない** (次回再登場は却下 semantics で扱う、下記 §6)。

**buffer entry の repo mismatch 扱い**: `buffer.pending.entries[].repo` が `--repo-root` の git remote (or basename) と一致しない cross-repo entry は、本 project では規範として反映できない (別 repo 向けの内容)。この場合は **`delete` bucket に割り当てる** — 「本 project では適用不能」意の却下と読む。適用手順は「対象 repo で棚卸しする」案内が既定 (cwd を切り替えて対象 repo で再度本 skill を回すのが正規経路)。archive.jsonl への id append は明示承認があった時のみ (手順 3 の (b) / §6 の却下 semantics)。§4 の「観測範囲外」に逃さない (buffer は必ず 6 bucket に割り当てる原則を優先する)。

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

### 3. 高確度候補の抽出とセッション内提案

bucket 候補割り当て後、以下の **共通 4 条件をすべて満たす** 候補だけを高確度として抽出する:

1. 判定材料が決定的観測のみで完結する (`推測:` prefix を要しない)
2. count 0 / 完全一致など、閾値解釈の余地がない証拠
3. 巻き込み・連動なし (依存 / pair 規約 / sandbox 連動 / 複数行 section への波及なし)
4. 適用手順が単一の既定分岐で完結する

本 skill での具体化は **buffer entry 限定**。既存 CLAUDE.md 行の bucket (keep-inline / move-to-* / delete / merge) は内容判断 (常時参照か・汎用か等) を伴うため**常にレポート側** — 静的観測のみで count 軸が無く、inventory-skill-mcp 級の決定的シグナルが存在しない。層 1 に出せるのは次の 2 型のみ:

- **(a) 完全一致 merge**: buffer entry の text (refined 優先、無ければ raw) が既存 CLAUDE.md / rules 行と文字列完全一致する (証拠: 双方の引用で一致を示せる)。適用 = CLAUDE.md 編集なし、`~/.claude/state/rules/archive.jsonl` への id append のみ
- **(b) cross-repo delete**: `buffer.pending.entries[].repo` が現 repo と不一致 (決定的証拠)。適用 = 「対象 repo で棚卸しする」案内 + 明示承認時のみ archive.jsonl append (§6 の却下 semantics に従う)

高確度候補は候補ごとに証拠 + 適用内容を添えて **AskUserQuestion で選択肢を提示する** (選択肢は「適用する / 見送る (レポート記載のみ) / 保留」相当)。

- **承認されたらそのまま同セッション内で適用する**。2 型とも CLAUDE.md 変更を伴わないため、適用は archive.jsonl への id append のみで完結する (§6)
- 却下・保留された候補はレポートの従来 bucket section に残す
- 4 条件を満たさない候補 (既存 CLAUDE.md 行の全 bucket を含む) はすべて手順 4 のレポートへ回す

### 4. Markdown レポート組み立て

`/tmp/inventory-claude-md/report-<timestamp>.md` に observation JSON と並置で書く。以下の**固定 schema**:

1. **ヘッダ**: 観測時刻 / 対象 file 一覧 / buffer status (present / empty / missing) / 総行数 / @import 展開数 / 参照実在 fail 数
2. **候補 section** (bucket 別に列挙):
    - 単位: `file:line-range` (既存 CLAUDE.md 行) or `buffer:<id>` (取り込み候補)
    - 対象テキスト (抜粋 or 全文、200 char 超は truncate)
    - bucket 候補 (上表の語彙)
    - 証拠 (observation JSON の観測項目を転記)
    - 提案 (1-2 行、証拠に紐づく)
    - 適用手順 (単位別分岐、下表参照)
3. **informational**: 6 bucket の**どれにも該当しない**行の一覧と理由 (bucket vocabulary の適用先が不明な行 / 用途不明な行)
4. **観測範囲外 (人間確認へ回す)**: 参照実在検査が fail-safe で判断不能だった行 / inline code span の path 参照 / observation JSON の穴。**bucket 判定の証拠として使わない**行をここに集める (罠表参照)
5. **meta 観測**: CLAUDE.md 自体の物理計測 (行数 / 見出し階層 / @import 数)。閾値 enforce はしない — 「参考値」として提示のみ
6. **summary 表**: 番号 × bucket × 対象 × scope。「3 と 7 だけ採用」と言える形。**高確度候補 (手順 3) も本表に載せ**、「セッション内提案済み (承認 / 見送り / 保留)」の結果を付記する — レポートは監査証跡として単体で完結させる

**観測由来の事実と LLM 推測の分離**: レポートの証拠欄には observation JSON の値を**そのまま転記**する。LLM の解釈を混ぜる場合は `推測:` prefix を必ず付けて 1 行に留める。

**適用手順の単位別分岐** (レポートに埋め込む):

| 対象 | 適用手順 |
|---|---|
| CLAUDE.md 行 (keep-inline / delete / merge) | worktree + PR で `<repo>/CLAUDE.md` を編集 |
| CLAUDE.md 行 (move-to-path-scoped) | 新 `.claude/rules/<slug>.md` を作成し `paths:` frontmatter を宣言 + CLAUDE.md の対象行を削除 (or 索引 stub 残置)。詳細は `claude-config-review/references/rules.md` を参照 |
| CLAUDE.md 行 (move-to-skill) | 対象 skill の SKILL.md に追記 (新規 skill なら別 skill として起票)。詳細は `claude-config-review/references/skill.md` を参照 |
| CLAUDE.md 行 (move-to-lint) | gate/hook の要件文まで書き、実装は別セッション。本 skill は配線しない |
| buffer entry (keep-inline / move-to-*) | CLAUDE.md or rules/skill に反映する worktree PR。適用 commit と同時に `~/.claude/state/rules/archive.jsonl` に該当 id を append して注入対象から外す (append-only。captured.jsonl は削除しない。下記 §6) |
| buffer entry (delete / merge) | CLAUDE.md 編集無し。却下 semantics に従い archive.jsonl に id を記録するか放置するかを人間が指示 |

### 5. LLM 段階で必ず Read する reference

bucket 提案文面を作る際は同一 plugin 内の以下を **判定前に Read** する (汎用スキル制約下で許容される参照):

- 常に: `${CLAUDE_SKILL_DIR}/../../knowledge/claude-config-review/references/claude-md.md`
- `move-to-path-scoped` 提案時: 同 `rules.md`
- `move-to-skill` 提案時: 同 `skill.md`

**`${CLAUDE_SKILL_DIR}` 展開後のフルパス例** (相対 path の暗算ミスで File not found を誘発しないよう明示):

- `${CLAUDE_SKILL_DIR}` = `~/.claude/skills/swat-skills/skills/steering/inventory-claude-md`
- 展開後 = `~/.claude/skills/swat-skills/skills/knowledge/claude-config-review/references/<name>.md`
- **`skills/` セグメントが 2 回現れる** (`swat-skills/skills/knowledge/`) 点に注意。`swat-skills/knowledge/` は File not found

reference の checklist を提案文面に**引用しない**。用語と判断軸を借りるのみ。references はレビュー観点の正本なので **references 側の変更は本 skill を追従改修**する。

### 6. buffer 消費 semantics (承認なしの write ゼロ)

- 本 skill は buffer を **AskUserQuestion での人間承認なしには一切 write しない**。層 1 (手順 3 の 2 型) 承認後の `archive.jsonl` への id append (および完全一致 merge のような CLAUDE.md 変更を伴わない適用) は同セッション内で実施してよい。captured.jsonl は常に読取専用 (レポート生成で失敗しても捕捉は失われない)
- captured.jsonl は append-only の混在ログ (`event:"capture"` / `event:"refine"`)。**行削除では消費しない** (append-only 不変 — 層 1 承認後も削除しない)。層 2 の採用 entry は**適用 commit と同時**に、人間が worktree PR で CLAUDE.md 変更と一緒に `~/.claude/state/rules/archive.jsonl` へ該当 id を append する (append-only)。archive.jsonl 記載 id は inject-rule.py が注入から除外するため、以降のセッションに出なくなる
- 却下 entry も同様に archive.jsonl へ id を記録すれば再登場しない。ただし記録は**明示指示があった時のみ**。放置は残留 (次回棚卸しで再登場)
- archive.jsonl は home 配下 (untracked / worktree 非追従) なので直接 append で良い — PR フロー外の作業として許容
- レポートに「buffer 適用手順」section を置き、archive.jsonl へ append する対象 entry の `id` (capture-rule.sh が付与する uuid) を明示する

### 7. 人間判定 (確度で扱いが分かれる)

- **高確度候補** (手順 3 の buffer entry 2 型): AskUserQuestion 承認済みのものは同セッション内で適用 (archive.jsonl append) まで進んでよい。承認なしの write は一切行わない
- **低確度候補** (既存 CLAUDE.md 行の全 bucket と上記以外の buffer entry): レポートを提示するところで skill の責務は終わる。承認 → 適用は次のセッション (or 別の worktree) で人間が実施する。CLAUDE.md への write は層を問わず本 skill からは行わない

## 出力例 (概略)

```
# CLAUDE.md 棚卸しレポート

観測時刻: 2026-07-18T00:12:34Z
対象: CLAUDE.md (156 lines) / .claude/CLAUDE.md (無し) / .claude/rules/ (2 files) / buffer (pending 4 entry, archive 記載 2 id)
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

### 5. buffer:3f9a1c2e-...  「Bash コマンドは quote を...」
- bucket: merge
- 証拠: CLAUDE.md:92 の「file paths を quote」と同義
- 提案: CLAUDE.md:92 に buffer 表現を統合し、適用時に archive.jsonl へ当該 id を append

## informational

- CLAUDE.md:130  「Revision Policy」— 決定的観測では bucket 割当てなし。用途不明のため人間確認

## 観測範囲外 (人間確認へ回す)

- CLAUDE.md:135  「詳細は `docs/agents/foo.md` を参照」— inline code span の path は script の link_targets に出ない。参照実在の判断は本 skill の範囲外
- CLAUDE.md:88  参照実在 fail 1 件 (link_targets[3]) — check_mode=relative-path で解決先が見つからないが、書き手誤植 vs 陳腐化を区別できない

## summary

| # | bucket | 対象 | scope | セッション内提案 |
|---|---|---|---|---|
| 1 | keep-inline | CLAUDE.md:14-22 | project | - (低確度) |
| 2 | move-to-path-scoped | CLAUDE.md:78-84 | project | - (低確度) |
| 3 | move-to-lint | CLAUDE.md:56 | project | - (低確度) |
| 4 | delete | CLAUDE.md:120 | project | - (低確度) |
| 5 | merge | buffer:3f9a1c2e-... → CLAUDE.md:92 | project + buffer | - (低確度: 同義だが完全一致でない) |
```

## 責務

- 6 bucket の**判定は常に人間** (3 段階モデル不変)
- **高確度候補** (手順 3 の buffer entry 2 型): セッション内 AskUserQuestion 提案 → 人間承認 → 同セッション内で適用 (archive.jsonl append) まで進んでよい。承認なしの write はゼロ
- **低確度候補**: 観測 → 候補提示 → 適用手順の明示までで止まる。適用 (CLAUDE.md 編集 / rules 新設 / skill 追記 / gate 配線 / buffer の archive.jsonl 記録) はレポート提示外の作業として skill から切り離す
- global CLAUDE.md への波及は skill の機能外

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| CLAUDE.md が観測不能 | root にファイルが無い | `sources.claude_md.root.status = "missing"` を確認し「観測不能」を報告して終了 (推測での穴埋めをしない) |
| buffer が空だが棚卸しは走る | captured.jsonl が未生成 or pending が全て archive.jsonl 済み | `buffer.status = "empty"` として既存 CLAUDE.md の整理を単独で回す。エラーにしない |
| 参照実在 fail が過剰 | git worktree 内でルート相対 path が解決できない / 動的展開文字列を path として拾った | `observation.link_targets.check_mode` を確認し `fail-safe` 分は削除の主根拠にしない。レポート §4「観測範囲外」に集約 |
| inline code span の path が参照実在に出ない | script の link 抽出は `[text](target)` と `@import` のみ。バッククォート内の path は拾わない (`docs/foo.md` 等) | 「参照実在検査の範囲外」を明記し、削除の主根拠にしない。レポート §4「観測範囲外」に集約 |
| サブディレクトリ CLAUDE.md を root と混同 | root と subdir で規範が競合しても物理観測では区別できない | subdir CLAUDE.md は**読取対象に含めるが提案先には推奨しない** (rules/skill への昇華を提案する方向に倒す) |
| bucket を script に埋め込みたくなる | 循環依存 (script が bucket を知ると LLM が再判定できなくなる) | script は生観測のみ。bucket 判定は LLM 段階で observation を読んでから |
| 推測と観測の混同 | LLM が観測 JSON に無い解釈を根拠として書く | すべての解釈行に `推測:` prefix を付ける。prefix 無しの主張は observation JSON からの直接引用のみ |
| global 昇格を持ち出しそうになる | 「これはグローバル価値観に見える」と判断が擦り抜ける | 本 skill の機能外。レポートに「(参考) 汎用性が高い可能性 — 判定は chezmoi 側で人間実施」と informational に落とすまで |
| buffer を skill から消費したくなる | 適用 commit と同時に archive されないと再登場して noise になる | archive.jsonl への id append は必ず人間の承認に紐付ける — 層 2 は人間の適用 commit と同時、層 1 (手順 3 の 2 型) のみ AskUserQuestion 承認後に同セッションで実施してよい。captured.jsonl は削除しない (append-only 不変) |
| 高確度基準を満たさない候補をセッション内提案したくなる | 「証拠が濃く見える」等の緩和誘惑 (既存 CLAUDE.md 行を層 1 に出したくなる誘惑を含む) | 既存 CLAUDE.md 行は内容判断を伴うため常にレポート側。手順 3 の 2 型以外は必ずレポートへ落とす (基準の緩和は本 SKILL.md の改訂として行う) |
| buffer entry の repo が cwd と別 project | cross-repo buffer は 6 bucket 語彙 (keep-inline / move-to-* / merge / delete) に自然には嵌らない | §2「buffer entry の repo mismatch 扱い」に従い `delete` (本 project では適用不能を意味する却下) に割り当てる。§4「観測範囲外」に逃さない。archive.jsonl append は明示承認時のみ (手順 3 の (b))、それ以外は当該 repo の棚卸しに委ねる |

## 参照

- 仕様確定: [issue #214 コメント](https://github.com/swat9013/swat-skills/issues/214)
- 上位 map: [issue #209](https://github.com/swat9013/swat-skills/issues/209)
- 関連 skill: `inventory-permissions` (別軸: permission / sandbox / hook) / `inventory-skill-mcp` (別軸: skill / MCP 実績集計)
- reference (Read 対象): `${CLAUDE_SKILL_DIR}/../../knowledge/claude-config-review/references/{claude-md,rules,skill}.md`
