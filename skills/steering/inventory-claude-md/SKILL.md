---
name: inventory-claude-md
disable-model-invocation: true
description: project の CLAUDE.md (root + project-local `CLAUDE.local.md` + サブディレクトリ + `.claude/rules/*.md`) を静的観測し、行単位で 6 bucket (keep-inline / move-to-path-scoped / move-to-skill / move-to-lint / delete / merge) の候補提示まで LLM に運ばせる棚卸し。全候補を候補レポートに載せ、判定と適用は人間 (worktree + PR)。global CLAUDE.md は読みも書きもしない。Use when「CLAUDE.md 棚卸し」「CLAUDE.md を整理」「CLAUDE.local.md を整理」「rules/ に切り出したい」「inventory-claude-md」「棚卸し」.
---

# inventory-claude-md

project 範囲の CLAUDE.md 系 (root `CLAUDE.md` + project-local `CLAUDE.local.md` + サブディレクトリ CLAUDE.md + `.claude/rules/*.md`) を、静的に観測し **行単位で 6 bucket の候補**を LLM に提示させ、判定は人間に残す棚卸し skill。

3 段階モデル (原則: **観測・集計は決定的に、判断は人間に、LLM は文章の具体化のみ**):

1. **決定的観測**: `scripts/scan-claude-md.py` が対象ファイル群を走査し、observation JSON を出力する
2. **LLM 具体化 (このメインコンテキスト)**: JSON を読み、行単位で bucket 候補と根拠、提案文面を組み立てる。**判定はしない**
3. **人間判定**: 適用/却下を選ぶのは常に人間。本 skill はレポート提示で止まり、承認後の CLAUDE.md 編集は人間 (worktree + PR)

**確度による 2 層化は持たない** — bucket 判定はすべて内容判断 (常時参照か・汎用か等) を伴う。静的トークンコストと注入実績は決定的に観測できるが、それは**候補の重み付け**であって bucket の決定ではない。加えて本 skill は write 対象がゼロなので、2 層化が分岐させる先 (セッション内で適用に進むか) 自体が存在しない。全候補が同じレポート経路を通る。

## スコープ (改変不可の境界)

- **読み対象**: project の CLAUDE.md 系のみ (`<repo>/CLAUDE.md` + `<repo>/CLAUDE.local.md` + サブディレクトリ CLAUDE.md + `<repo>/.claude/rules/*.md`)
- **`CLAUDE.local.md` の位置づけ**: memory 階層 4 層目の project-local (個人 override)。本 skill は **root 直下 (`<repo>/CLAUDE.local.md`) のみ**を対象にする (reference `claude-md.md` の memory 階層表が project-local として挙げるのはこの 1 path。project 層と違いサブディレクトリへの言及が無い)。observation JSON では `sources.claude_md.local` (`label: "project-local"`) に独立キーで出るので、**source class は path の文字列判定でなく JSON のキーから読む**
- **書き対象**: **なし**。本 skill からの write はゼロ — 成果物は `/tmp/inventory-claude-md/` のレポートのみで、CLAUDE.md / `CLAUDE.local.md` / rules への反映は人間が行う
- **除外**: `~/.claude/CLAUDE.md` (global) は読みも書きもしない。global 昇格は本 skill の機能外 — 人間が chezmoi 側で別途行う
- **transcript の扱い**: bucket 判定の材料 (行・section・参照) はすべて静的 repo 内 file から取る。transcript は**注入実績 (どの file が何 session に載ったか) と compaction 実害だけ**を `scan_overhead` 経由で読む (手順 1b)。行単位の実績は transcript に存在しないため、行の内容判断が transcript に依存することはない
- **非依存**: claude-mem / dotfiles / swat-skills 固有 gate 実装。**git の tracked/untracked も観測しない** (script は git に依存しない)

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
- **script は bucket を出さない**。決定的観測 3 項目だけを出す — 本 domain には「count 0 / 完全一致」のような機械判定可能な述語が無く、[ADR 0032](https://github.com/swat9013/swat-skills/blob/main/docs/adr/0032-policy-free-refinement-deterministic-rules.md) の決定的ルール層の対象外だから (rule 層を持つのは permissions / invocations / engineering-values の 3 系統のみ)

出力に失敗したら (repo に CLAUDE.md が無い等) `sources.claude_md.root.status = "missing"` が入る。**root と local (`sources.claude_md.local.status`) の両方が `missing` のときだけ**「観測不能」を報告して終了する。片方でも `present` なら棚卸し対象があるので続行する (`CLAUDE.local.md` だけが存在する repo は成立する)。

各 file の `token_cost` に **行単位の概算 token** が出る (`per_line_est_tokens` は index 0 = 1 行目の配列、`sections` は見出し別合計と share)。CLAUDE.md 系は session 開始で無条件に載るので、実コストは行数ではなく token — 行数が同じでも表・code block・日本語で数倍違う。

### 1b. 注入実績の観測 (決定的)

`mcp__plugin_swat-skills_transcript-ops__scan_overhead` を引数なしで呼ぶ (repo は cwd に既定解決する。解決できないときは失敗するので、別 project を見るなら `repo_root` を明示する)。

- 返り値の `path` に mart JSON が出るので Read する (**本体は返らない**)
- `memory_files[]` が **file 別の注入 session 数**。path は repo 相対で、worktree・複数 checkout に散った同一 file は 1 行に畳まれている (`absolute_paths_folded` が畳んだ数)
- `compaction` が **捨てられた token** (`dropped_tokens`)。`cumulative_dropped_tokens` は session ごとの最大値で、boundary をまたいで足した値ではない
- `static_context.sources` は「何が重いか」の内訳 (`memory_file` / `skill_listing` / `mcp_instructions` / ...)。CLAUDE.md 系が静的コストのどれくらいを占めるかはここで見る
- **tool は bucket を出さない**。実績は file 粒度までで、行粒度の実績は transcript に存在しない

この観測が取れなくても (mart が空・観測窓に session が無い等) 手順 2 以降は成立する — 静的観測だけで bucket 候補は組める。取れなかったことをレポート §4「観測範囲外」に明記する。

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

**コスト × 実績の使い方 (候補の重み付け。bucket の決定ではない)**:

- 候補の行群には `token_cost.per_line_est_tokens` を合算した **est_tokens を必ず添える**。「毎 session この行群に払っている概算 token」が、同じ bucket の候補間の優先順位になる
- `.claude/rules/<topic>.md` は `memory_files[]` と突き合わせる。**注入 0 session の file は `memory_files[]` に行として現れない** (mart は観測した注入だけを載せ、repo の file 一覧を知らない)。判定は**差分で行う** — 手順 1 の `sources.rules[]` が列挙した file のうち `memory_files[]` に無いものが「窓内で 1 度も注入されていない」。それは規範が届いていない証拠なので、`move-to-path-scoped` の glob 見直し (or `keep-inline` への差し戻し) を提案する
- 逆に `sessions_injected` が観測 session のほぼ全数に届いている path-scoped rule は、実質常時ロードなので CLAUDE.md 本体と同じコスト扱いで読む (`meta.sessions_observed` が分母)
- `compaction.dropped_tokens` が大きい環境では、静的コストの削減提案 (`move-to-path-scoped` / `delete`) に「compaction 実害」を根拠として添えられる。**逆は言えない** — compaction は静的コストだけで起きるものではないので、「この行のせいで compaction した」とは書かない
- **token 数は概算** (`token_cost.estimator` を転記する)。桁の比較にだけ使い、「N token を超えたら移設」のような閾値判定はしない

**`CLAUDE.local.md` 由来の行の追加制約** (`sources.claude_md.local` の行にのみ適用):

- **`.claude/rules/` / skill / project `CLAUDE.md` への移動提案は「移設」でなく「共有化」**。project-local は個人 override の器なので、移すと**他の作業者・他マシンにも配布される**。提案文に `共有化:` を明示し、「この内容は共有してよいか」を人間への確認事項として 1 行添える。無印の移設として書かない
- **共有化を提案しない側に倒す条件**: マシン固有 path / 個人ホスト名 / 個人の作業習慣・好み・実験中の設定など、**その行が local に置かれている理由が内容から読み取れる**場合。この場合は `keep-inline` (local 残置) を提案し、根拠に「project-local に置く理由が内容にある」と書く
- **提案先の器を書き分ける**: `keep-inline` の残置先は `CLAUDE.md` でなく `CLAUDE.local.md`。レポートの「対象」は必ず `CLAUDE.local.md:<line>` 形式で書き、root 由来と混ぜない
- **`merge` は器をまたいで提案してよいが方向を明示する** (local → project の重複なら「local 側を削除して project に一本化」か「project 側の個人 override として意図的」かを人間判断に残す。`推測:` で片寄せしない)

**bucket 割当ての粒度規約**:

- 単位は**行 or 行群**。1 section 内に異質な bucket 候補が混在する場合、section 全体を informational にせず、**連続する同 bucket 候補の行群を 1 単位で切り出す**。単一行の bucket 分割も可
- 見出し (H2 / H3) 自体は section 境界の情報として扱い、単独では bucket 割当ての対象にしない。見出しを含む行群を単位にする場合は「対象」の line-range に見出し行番号を含める

### 3. Markdown レポート組み立て

`/tmp/inventory-claude-md/report-<timestamp>.md` に observation JSON と並置で書く。以下の**固定 schema**:

1. **ヘッダ**: 観測時刻 / 対象 file 一覧 / 総行数 / @import 展開数 / 参照実在 fail 数。**対象 file 一覧には `status = "missing"` の file も「(無し)」付きで載せる** — 「走査して空だった」と「そもそも走査していない」を人間が区別できるようにする (`CLAUDE.local.md` は無い repo の方が多い)。総行数は root と local を**別行で出す** (合算しない — 器が違う)
2. **候補 section** (bucket 別に列挙):
    - 単位: `file:line-range`
    - 対象テキスト (抜粋 or 全文、200 char 超は truncate)
    - bucket 候補 (上表の語彙)
    - 証拠 (observation JSON の観測項目を転記)
    - 提案 (1-2 行、証拠に紐づく)
    - 適用手順 (bucket 別分岐、下表参照)
3. **informational**: 6 bucket の**どれにも該当しない**行の一覧と理由 (bucket vocabulary の適用先が不明な行 / 用途不明な行)
4. **観測範囲外 (人間確認へ回す)**: 参照実在検査が fail-safe で判断不能だった行 / inline code span の path 参照 / observation JSON の穴 / **観測境界の外にある file** (サブディレクトリの `CLAUDE.local.md` 等、存在しても JSON に出ないもの)。**bucket 判定の証拠として使わない**行をここに集める (罠表参照)
5. **meta 観測**: CLAUDE.md / `CLAUDE.local.md` 自体の物理計測 (行数 / 見出し階層 / @import 数)。observation JSON の `meta.root_meta` と `meta.local_meta` を**それぞれ転記**する (`meta` は root と local の 2 本立てで、subdirs / rules は含まない)。**`status = "missing"` の側は数値を転記せず「(無し)」と書く** — `summarize_meta` は不在時に全項目 0 を返すので、そのまま載せると「存在するが 0 行」と読めてしまう。閾値 enforce はしない — 「参考値」として提示のみ
6. **summary 表**: 番号 × bucket × 対象 × scope。「3 と 7 だけ採用」と言える形。scope 列で `project` と `project-local` を区別する (適用手順が分岐するため)

**観測由来の事実と LLM 推測の分離**: レポートの証拠欄には observation JSON の値を**そのまま転記**する。LLM の解釈を混ぜる場合は `推測:` prefix を必ず付けて 1 行に留める。

**適用手順の bucket 別分岐** (レポートに埋め込む):

| bucket | 適用手順 |
|---|---|
| keep-inline / delete / merge | worktree + PR で対象 file (`<repo>/CLAUDE.md` or `<repo>/CLAUDE.local.md`) を編集 |
| move-to-path-scoped | 新 `.claude/rules/<slug>.md` を作成し `paths:` frontmatter を宣言 + 元 file の対象行を削除 (or 索引 stub 残置)。詳細は `claude-config-review/references/rules.md` を参照 |
| move-to-skill | 対象 skill の SKILL.md に追記 (新規 skill なら別 skill として起票)。詳細は `claude-config-review/references/skill.md` を参照 |
| move-to-lint | gate/hook の要件文まで書き、実装は別セッション。本 skill は配線しない |

**source による適用経路の分岐** (bucket 分岐と直交。両方をレポートに書く):

- `CLAUDE.local.md` が `.gitignore` 済みなら**その file への編集は PR に乗らない** — 「worktree + PR」でなく「人間が直接編集」と書く。gitignore 済みかは observation JSON に無いので、レポートには条件形 (`.gitignore` 済みなら〜) で書くか、人間に確認を促す 1 行を添える
- 共有化 (local → project `CLAUDE.md` / `.claude/rules/` / skill) は**移動先が tracked**なので worktree + PR が要る。local 側の削除は PR に乗らない可能性があり、**2 経路の作業になる**ことを適用手順に明記する

### 4. LLM 段階で必ず Read する reference

bucket 提案文面を作る際は同一 plugin 内の以下を **判定前に Read** する (汎用スキル制約下で許容される参照):

- 常に: `${CLAUDE_SKILL_DIR}/../../knowledge/claude-config-review/references/claude-md.md`
- `move-to-path-scoped` 提案時: 同 `rules.md`
- `move-to-skill` 提案時: 同 `skill.md`

**`${CLAUDE_SKILL_DIR}` 展開後の構造** (相対 path の暗算ミスで File not found を誘発しないよう明示。plugin root の実 path は install 形態で変わるので、ここでは書かない):

- `${CLAUDE_SKILL_DIR}` = `<plugin root>/skills/steering/inventory-claude-md`
- 展開後 = `<plugin root>/skills/knowledge/claude-config-review/references/<name>.md`
- **`..` は 2 回**で category 階層を抜けて `skills/` に戻る。1 回だと `skills/steering/knowledge/...` を指し File not found

reference の checklist を提案文面に**引用しない**。用語と判断軸を借りるのみ。references はレビュー観点の正本なので **references 側の変更は本 skill を追従改修**する。

### 5. 人間判定

レポートを提示するところで skill の責務は終わる。承認 → 適用は次のセッション (or 別の worktree) で人間が実施する。CLAUDE.md / rules への write は本 skill からは行わない。

## 出力例 (概略)

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
- 適用手順: rules file の新設は worktree + PR。`CLAUDE.local.md` 側の行削除は `.gitignore` 済みなら PR に乗らないため別作業 (2 経路)

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

## 責務

- 6 bucket の**判定は常に人間** (3 段階モデル不変)
- 観測 → 候補提示 → 適用手順の明示までで止まる。適用 (`CLAUDE.md` / `CLAUDE.local.md` 編集 / rules 新設 / skill 追記 / gate 配線) はレポート提示外の作業として skill から切り離す
- **`CLAUDE.local.md` の内容を共有してよいかの判断も常に人間**。skill は共有化候補として提示するところまで
- global CLAUDE.md への波及は skill の機能外

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| CLAUDE.md が観測不能 | root にファイルが無い | `sources.claude_md.root.status` と `sources.claude_md.local.status` の**両方が `missing`** のときだけ「観測不能」を報告して終了 (推測での穴埋めをしない)。root が無くても local があれば棚卸し対象は存在する |
| `CLAUDE.local.md` の行を無印で rules / skill へ「移設」提案 | project-local が個人 override の器であることを失念 | 提案文に `共有化:` を明示し、他の作業者・他マシンへ配布される変更であることと、共有可否の人間確認を 1 行添える |
| `CLAUDE.local.md` の適用手順を「worktree + PR」と書く | 全 bucket 共通の適用手順表を無条件に転記した | `.gitignore` 済みなら PR に乗らない。条件形で書くか人間に確認を促す。共有化は「移動先の PR」+「local 側の直接編集」の 2 経路になる |
| サブディレクトリの `CLAUDE.local.md` を観測結果に期待する | 本 skill の観測対象は root 直下の `CLAUDE.local.md` のみ (reference `claude-md.md` の memory 階層表に沿う) | 観測対象外。存在しても JSON に出ない (subdir 探索の glob は完全一致 `CLAUDE.md`)。レポート §4「観測範囲外」に落とす |
| root と local の行数を合算して「総行数」に出す | ヘッダ schema の 1 項目に見えた | 器が違うので別行で出す。`meta.root_meta` / `meta.local_meta` をそれぞれ転記する |
| 参照実在 fail が過剰 | git worktree 内でルート相対 path が解決できない / 動的展開文字列を path として拾った | `observation.link_targets.check_mode` を確認し `fail-safe` 分は削除の主根拠にしない。レポート §4「観測範囲外」に集約 |
| inline code span の path が参照実在に出ない | script の link 抽出は `[text](target)` と `@import` のみ。バッククォート内の path は拾わない (`docs/foo.md` 等) | 「参照実在検査の範囲外」を明記し、削除の主根拠にしない。レポート §4「観測範囲外」に集約 |
| サブディレクトリ CLAUDE.md を root と混同 | root と subdir で規範が競合しても物理観測では区別できない | subdir CLAUDE.md は**読取対象に含めるが提案先には推奨しない** (rules/skill への昇華を提案する方向に倒す) |
| bucket を script に埋め込みたくなる | 他 skill (permissions / skill-mcp) の tool が rule 層を持つのを見て対称化したくなる | 本 domain の bucket 判定はすべて内容判断で、機械判定可能な述語が存在しない。決定的シグナルが現れたら ADR 0032 の移行判定基準に当てて判断する (script に直接足さない) |
| token コストで bucket を決める | 「重い行 = 移設」と閾値判定した | コストは同 bucket 内の**優先順位**の材料。bucket は内容判断で決める。概算である旨 (`estimator`) をレポートに転記する |
| 行ごとの注入実績を期待する | `memory_files[]` を行単位の証拠と読んだ | 実績は file 粒度まで。行粒度の実績は transcript に存在しない (レポート §4「観測範囲外」に落とす) |
| 注入 0 の rules file を `memory_files[]` から探す | mart に `sessions_injected: 0` の行があると思った | mart は観測した注入だけを載せる。0 件は**行の不在**として現れるので、`sources.rules[]` との差分で検出する |
| compaction を特定行の責任にする | `dropped_tokens` を静的コストの直接の結果と読んだ | compaction は静的コスト以外でも起きる。削減提案の傍証には使えるが、因果は書かない |
| 推測と観測の混同 | LLM が観測 JSON に無い解釈を根拠として書く | すべての解釈行に `推測:` prefix を付ける。prefix 無しの主張は observation JSON からの直接引用のみ |
| global 昇格を持ち出しそうになる | 「これはグローバル価値観に見える」と判断が擦り抜ける | 本 skill の機能外。レポートに「(参考) 汎用性が高い可能性 — 判定は chezmoi 側で人間実施」と informational に落とすまで |
| セッション内で CLAUDE.md を直接編集したくなる | 「証拠が濃く、承認も取れそう」に見える誘惑 | 本 skill の write 対象はゼロ。適用は必ずレポート提示外の人間作業 (worktree + PR) に落とす |

## 参照

- 関連 skill: `inventory-permissions` (別軸: permission / sandbox / hook) / `inventory-skill-mcp` (別軸: skill / MCP 実績集計)
- reference (Read 対象): `${CLAUDE_SKILL_DIR}/../../knowledge/claude-config-review/references/{claude-md,rules,skill}.md`
