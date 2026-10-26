---
name: inventory-claude-md
disable-model-invocation: true
description: project の CLAUDE.md 系 (root / `CLAUDE.local.md` / サブディレクトリ / `.claude/rules/`) を静的観測し、行単位で 7 bucket の見直し候補レポートを作る棚卸し。判定は人間で、候補ごとに承認された分だけを write-for-harness で反映する。
---

# inventory-claude-md

project 範囲の CLAUDE.md 系 (root `CLAUDE.md` + project-local `CLAUDE.local.md` + サブディレクトリ CLAUDE.md + `.claude/rules/*.md`) を、静的に観測し **行単位で 7 bucket の候補**を LLM に提示させ、判定は人間に残す棚卸し skill。

4 段階モデル (原則: **観測・集計は決定的に、判断は人間に、LLM は文章の具体化のみ、反映は承認分だけ**。本 skill の観測は `open_predicates` を出さないので、`open_predicates` の解釈の工程は無く、bucket の判定基準を当てはめる解釈は LLM が担う):

1. **決定的観測**: `scripts/scan-claude-md.py` が対象ファイル群を走査し、observation JSON を出力する
2. **LLM 具体化 (このメインコンテキスト)**: JSON を読み、行単位で bucket 候補と根拠、提案文面を組み立てる。**判定はしない**
3. **人間判定**: 採否を選ぶのは常に人間。候補ごとに問う
4. **反映**: 承認された候補だけを、1 件ずつ `write-for-harness` に書かせる。**人間承認を得ていない write はゼロ**で、書き終えたら diff を提示する

**確度による 2 層化は持たない** — 静的トークンコストと注入実績は**候補の重み付け**に使い、bucket の決定には使わない。反映するものがある bucket の候補は、すべて同じ「候補ごとの承認 → 反映」の経路を通す。

## スコープ (改変不可の境界)

- **読み対象**: project の CLAUDE.md 系のみ (`<repo>/CLAUDE.md` + `<repo>/CLAUDE.local.md` + サブディレクトリ CLAUDE.md + `<repo>/.claude/rules/*.md`)
- **`CLAUDE.local.md` の位置づけ**: memory 階層 4 層目の project-local (個人 override)。本 skill は **root 直下 (`<repo>/CLAUDE.local.md`) のみ**を対象にする (reference `claude-md.md` の memory 階層表が project-local として挙げるのはこの 1 path。project 層と違いサブディレクトリへの言及が無い)。observation JSON では `sources.claude_md.local` (`label: "project-local"`) に独立キーで出るので、**source class は path の文字列判定でなく JSON のキーから読む**
- **書き対象**: 手順 5 で人間が承認した候補の反映先 (`<repo>/CLAUDE.md` / `<repo>/CLAUDE.local.md` / `<repo>/.claude/rules/*.md` / repo 内の既存 skill の SKILL.md) だけ。書くのは `write-for-harness` で、本 skill は反映先と候補を渡す。レポートは `/tmp/inventory-claude-md/` に置く
- **除外**: `~/.claude/CLAUDE.md` (global) は読みも書きもしない。global 昇格は本 skill の機能外 — 人間が chezmoi 側で別途行う
- **transcript の扱い**: bucket 判定の材料 (行・section・参照) はすべて静的 repo 内 file から取る。transcript は**注入実績 (どの file が何 session に載ったか) と compaction 実害だけ**を `scan_overhead` 経由で読む (手順 1b)。行単位の実績は transcript に存在しないため、行の内容判断が transcript に依存することはない
- **非依存**: claude-mem / dotfiles / swat-skills 固有 gate 実装。**git の tracked/untracked も観測しない** (script は git に依存しない)

**汎用スキル制約**: 参照するのは Claude Code 標準ファイルと `write-for-harness` (references の Read と、反映段での invoke) のみ。repo 固有の gate 構成 (pre-commit gate script の有無) には触らない — 「lint 化可能か」の判断は LLM 段階で提案するが、配線は repo 側の別作業。

## 引数

現状 project section 固定 (cwd の CLAUDE.md 系)。省略時に project、他 section は無し (global は仕様上除外)。

## 手順

### 1. 観測 script 起動 (決定的)

```
${CLAUDE_SKILL_DIR}/scripts/scan-claude-md.py --repo-root .
```

- `--repo-root` 省略時 cwd
- stdout に observation JSON path (`/tmp/inventory-claude-md/observation-<timestamp>.json`) が出る
- **script は bucket を出さない**。決定的観測 3 項目だけを出す — 本 domain には「count 0 / 完全一致」のような機械判定可能な述語が無く、機械判定できる述語から bucket を確定させる層 (決定的ルール層) の対象外だから (rule 層を持つのは permissions / invocations / engineering-values の 3 系統のみ)

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

着手前に、手順 4 が「常に」とする 2 本 (`patches.md` と `components/claude-md.md`) を Read する — 行の分類に診断表を使う。

observation の schema は script docstring 参照。以下の順に組み立てる。

**bucket vocabulary** (script はこの語彙を知らない。ここで初めて割り当てる):

| bucket | 判定基準 (要旨) | 提案先 |
|---|---|---|
| **keep-inline** | 常時参照される汎用規範 (asking policy / 言語 / 責務分離原則等) | CLAUDE.md 本文の残置 |
| **move-to-path-scoped** | 特定ディレクトリ配下の作業でのみ必要 | `.claude/rules/<topic>.md` + `paths:` frontmatter |
| **move-to-skill** | 特定タスク・状況でのみ必要な手順 | 既存 skill 追記 / 新規 skill |
| **move-to-lint** | 決定的検査に置換できる規則 (行数 / path / 重複 / 参照実在等) | repo の gate/hook (配線は repo 側の仕事) |
| **delete** | 参照切れ / 陳腐化 / 解決済み問題の残骸 / 既定どおりの行 (no-op) / 環境を見れば分かる事実 | 削除 |
| **merge** | 他行と重複 / 散らばった定義 | 統合先の行番号を提示 |
| **rewrite** | 置き場は今のままで、文面を書き換える (下の対応表で rewrite に当たる行) | 対象行の書き換え案 (診断表の行が示す方向) |

**行の分類は診断表で行う**: 手順 4 で Read する `write-for-harness` の patches.md の診断表で各行を分類し、次の対応で bucket に移す。

| 診断表の行 | bucket |
|---|---|
| 既定どおりの行 (no-op) / 事実で、環境を見れば分かる | delete |
| 同じ概念の言い直し / 散らばった定義 | merge |
| 事実で、一部の分岐でしか要らない | move-to-path-scoped / move-to-skill |
| 分割 | move-to-skill |
| 弱い表記・弱い語 / 禁止の形 / 手順の列挙 | rewrite |

- 「同じ概念の言い直し / 散らばった定義」は、診断表ではパッチが「残す」の行の但し書き (統合) に書かれている。但し書きに当たる行を先に merge にし、残りの「残す」の行を keep-inline にする。上の対応に無いそれ以外の行は、bucket を自分で割り当てず informational に理由付きで落とす
- move-to-lint と、参照切れ・陳腐化による delete は診断表の外 (決定的検査への置換と、参照の実在) で判定する
- **no-op の delete 候補は micro-test で確かめたものだけを出す**: 候補の行ごとに手順 2-1 の micro-test にかける。振る舞いが変わらなかった行だけを delete に載せ、変わった行は診断表の他の行で分類し直す。micro-test を回せなかった行と手順 2-1 の上限から外れた行は、delete に載せず informational に「no-op の疑い・未検証」と書く

**bucket 割当ての制約 (script との責務分担)**:

- LLM は observation JSON の証拠に**紐付けて**割り当てる。「常時参照される」を主張するなら「先頭 N section 以内 / 他 skill から参照される / 反復失敗の記録がある」等の**可視な根拠**を書く
- 決定的観測を超える推論には `推測:` prefix を必ず付ける (例: `推測: このルールは dispatch 実行時のみ必要と読める`)
- lint 化可否は LLM の判断で、根拠に**適用可能な判定形式** (regex / AST / 静的解析) を 1 行明示する。実 gate 配線は書かない
- 参照実在 fail は `move-to-lint` の証拠にできる。数える前に `link_targets[].check_mode` を見て、**実際に検査されて解決しなかった行 (`exists: false`) だけを fail に数える** — `url` / `anchor` / `dynamic` / `empty` は fail-safe で未検査 (`exists: null`) なので、レポート §4「観測範囲外」へ回す。fail 一件で削除を主張しない (書き手の誤植 vs 陳腐化を区別できない)
- **inline code span (backtick 内) の path は script の `link_targets` に出ない**。`[text](target)` markdown link と `@path` import のみが検査対象。バッククォート内で書かれた `docs/foo.md` は observation JSON に無いため、参照実在の主張はしない (「link 抽出仕様の範囲外」を明記)
- **サブディレクトリ CLAUDE.md の行は読取対象に含めるが、提案先には推奨しない** — root と subdir で規範が競合しても物理観測では区別できないので、`.claude/rules/` / skill への昇華 (`move-to-path-scoped` / `move-to-skill`) に倒す
- bucket vocabulary の**どれにも該当しないなら informational に落として理由を書く** (握り潰さない)

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

#### 2-1. no-op の micro-test

手順 2 の着手前に Read した patches.md の「no-op の micro-test」の手順で比べる。かける行は 1 回の棚卸しで **est_tokens 降順の上位 5 行まで**とする。

- **対象が CLAUDE.md 系なので、正本の subagent の種類を CLAUDE.md 系を自動で読む種類に替えると、「なし」側にも対象の行が cwd から載り、比べる対照が成り立たない**
- 「変わらない」と判定した行は、場面・両側の答えの要旨・試行数 (「1 場面・各 1 回」など) をレポートの証拠欄に書く

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
3. **informational**: bucket vocabulary の**どれにも該当しない**行の一覧と理由 (bucket vocabulary の適用先が不明な行 / 用途不明な行)。**汎用性が高く見える行もここに落とす** — 「(参考) global 昇格の余地あり — 判定は chezmoi 側で人間実施」と書き、bucket 候補としては出さない (global は本 skill の機能外)
4. **観測範囲外 (人間確認へ回す)**: 参照実在検査が fail-safe で判断不能だった行 (`exists: null`) / inline code span の path 参照 / observation JSON の穴 / **観測境界の外にある file** (サブディレクトリの `CLAUDE.local.md` 等、存在しても JSON に出ないもの)。**bucket 判定の証拠として使わない**行をここに集める
5. **meta 観測**: CLAUDE.md / `CLAUDE.local.md` 自体の物理計測 (行数 / 見出し階層 / @import 数)。observation JSON の `meta.root_meta` と `meta.local_meta` を**それぞれ転記**する (`meta` は root と local の 2 本立てで、subdirs / rules は含まない)。**`status = "missing"` の側は数値を転記せず「(無し)」と書く** — `summarize_meta` は不在時に全項目 0 を返すので、そのまま載せると「存在するが 0 行」と読めてしまう。閾値 enforce はしない — 「参考値」として提示のみ
6. **summary 表**: 番号 × bucket × 対象 × scope × 採否。「3 と 7 だけ採用」と言える形。scope 列で `project` と `project-local` を区別する (適用手順が分岐するため)。採否列は手順 5・6 の結果 (反映済み / 見送り / 承認済み・未反映 / 未判定 (打ち切り) / 問わない bucket は `-`) を、手順 6 を終えた時点で書き込む。承認済み・未反映の候補は、候補 section に「停止理由」の行を足し、`write-for-harness` が返した文を逐語で書く。手順 6 の照合で止めた候補には、対象外の path の一覧と次に人間が行う手順も書く

各 section の書き下し方が掴めないときは `${CLAUDE_SKILL_DIR}/references/report-example.md` を Read する (schema の正本は上の 6 項で、reference はその記入例)。

**観測由来の事実と LLM 推測の分離**: レポートの証拠欄には observation JSON の値を**そのまま転記**する。LLM の解釈を混ぜる場合は `推測:` prefix を必ず付けて 1 行に留める。

**適用手順の bucket 別分岐** (レポートに埋め込む。手順 6 の反映はこの表に従う。括弧内は各 file に当てる patches.md のパッチ種別):

| bucket | 適用手順 |
|---|---|
| keep-inline | 変更なし |
| rewrite / delete / merge | 対象 file (`<repo>/CLAUDE.md` / `<repo>/CLAUDE.local.md` / `.claude/rules/<topic>.md`) を編集 (書き換え / 削除 / 統合)。merge が file をまたぐなら、統合先 (書き換え) と削除側 (削除) の file を別々に編集する |
| move-to-path-scoped | 置き先を `write-for-harness/references/components/rules.md` のチェックリストの「分ける順」で決め、同じ `paths:` で同じ関心の既存 rules があればそこへ追記 (追加)、無ければ新 `.claude/rules/<topic>.md` を作成して `paths:` frontmatter を宣言 (追加)。どちらでも元 file の対象行を削除 (削除) |
| move-to-skill | repo 内の既存 skill なら、対象 skill の SKILL.md へ移す (パッチ種別は `write-for-harness` が選ぶ) + 元 file の対象行を削除 (削除)。新規 skill (と repo の外の skill) なら別 skill として起票し、本 skill では反映しない。詳細は `write-for-harness/references/components/skill.md` を参照 |
| move-to-lint | gate/hook の要件文まで書き、実装は別セッション。本 skill は配線しない |

**source による適用経路の分岐** (bucket 分岐と直交。両方をレポートに書く):

- `CLAUDE.local.md` が `.gitignore` 済みなら**その file への編集は version control に乗らない** — レポートの適用手順に「反映しても version control には残らない」と書く。gitignore 済みかは observation JSON に無いので、レポートには条件形 (`.gitignore` 済みなら〜) で書くか、人間に確認を促す 1 行を添える
- 共有化 (local → project `CLAUDE.md` / `.claude/rules/` / skill) は**移動先が tracked**なので version control に載る。local 側の削除は載らない可能性があり、**2 経路の作業になる**ことを適用手順に明記する

### 4. LLM 段階で必ず Read する reference

bucket 提案文面を作る際は同一 plugin 内の以下を **判定前に Read** する (汎用スキル制約下で許容される参照):

- 常に: `${CLAUDE_SKILL_DIR}/../write-for-harness/references/patches.md` (手順 2 の診断表)
- 常に: `${CLAUDE_SKILL_DIR}/../write-for-harness/references/components/claude-md.md`
- `move-to-path-scoped` 提案時: 同 `components/rules.md`
- `move-to-skill` 提案時: 同 `components/skill.md`

**`${CLAUDE_SKILL_DIR}` 展開後の構造** (相対 path の暗算ミスで File not found を誘発しないよう明示。plugin root の実 path は install 形態で変わるので、ここでは書かない):

- `${CLAUDE_SKILL_DIR}` = `<plugin root>/skills/steering/inventory-claude-md`
- 展開後 = `<plugin root>/skills/steering/write-for-harness/references/patches.md` と `<plugin root>/skills/steering/write-for-harness/references/components/<name>.md` (`patches.md` は `components/` の 1 階層上)
- **`..` は 1 回**で `skills/steering/` に戻る (参照先が同じ category なので category 階層は抜けない)

reference の checklist を提案文面に**引用しない**。用語と判断軸を借りるのみ。references はレビュー観点の正本なので **references 側の変更は本 skill を追従改修**する。

### 5. 人間判定 (候補ごとの AskUserQuestion)

レポートを提示したうえで、**反映の対象になる候補を 1 件 = 1 問**で問う。対象は rewrite / delete / merge / move-to-path-scoped / repo 内の既存 skill への move-to-skill の候補。keep-inline・informational・move-to-lint・新規 skill への move-to-skill は反映するものが本 skill に無いので問わず、レポートに残す。

- 選択肢は「採用」と「見送る」を並べ、各問にはレポートの対象テキスト・証拠・提案を添える。`共有化:` の候補には、共有してよいかの確認を問いの文面に含める
- 提示順は bucket ごとに est_tokens の降順 (手順 2 の「コスト × 実績の使い方」の優先順位)
- **バッチ規約**: AskUserQuestion は 1 回あたり 4 問までなので、候補は **4 件ずつ**提示し、必要な回数だけ繰り返す。全件を回さずに打ち切るなら、打ち切った件数をレポートに明記する (silent に切らない)
- **判定を LLM が代行しない**。採用が明らかに見える候補も必ず問う

### 6. 承認分の反映

採用された候補だけを、手順 3 の適用手順に従って反映する。**変更の届け方は実行 project の運用に従う。**

**文面を書く工程は `write-for-harness` に委ねる。** 本 skill が持つのは候補・証拠と反映先の指定で、harness ファイルの文面規範は持たない。invoke の前に `${CLAUDE_SKILL_DIR}/../write-for-harness/references/caller-contract.md` (呼び出し元向けの契約) を Read し、契約が定める事項は契約に従う。順序は次の 2 段で固定する:

1. **採用された候補ごとに `Skill(swat-skills:write-for-harness)` を invoke する。**
    - 契約の「2 file にまたがる変更」に当たる候補は、move-to-path-scoped と move-to-skill (移動先 + 元 file の行削除)、file をまたぐ merge (統合先 + 削除側)、共有化 (移動先 + `CLAUDE.local.md` の行削除)
    - どの候補を反映するかと 6-1 の制約を宣言する。候補の場所は対象テキストの引用で示す — 先の反映で行番号がずれるので、行番号はレポート上の識別子に留める
    - invoke ごとに、契約の「委譲後の変更 path の照合」節の手順で照合する。許可 path は、args が既存 file の path ならその 1 本 (2 file にまたがる候補も invoke ごとに、その invoke の file だけ。契約に従って rules の索引行を足す invoke の `CLAUDE.md` を含む)、`新規: rules` で起こす候補なら作成先 `.claude/rules/<topic>.md` の 1 本で、作成先は invoke 直前の同じ turn の文面で渡す (`新規:` の args は作成先 path を取らない)。止めたときは、契約が残させるものをレポートの候補 section と報告に残す2. **全候補の反映が済んだら (照合で止めたときはそこまでに) diff を提示し、レポートの summary 表の採否列を書き込む**

- `write-for-harness` が反映を完了せずに終わった候補は、理由を問わず**承認済み・未反映**としてレポートと報告に残す (承認が黙って消えないようにする)

#### 6-1. invoke 時に文面で添える制約

`write-for-harness` は棚卸し側の制約を知らないので、invoke のたびに渡す:

- **反映先ファイルには規範文だけを書き、観測の証拠 (est_tokens / 注入 session 数 / 行番号 / micro-test の結果) は書かない。** 証拠はレポートに置く
- **承認された bucket と提案の範囲で書く。** 元の行の意味を拡張しない。rewrite は、診断表の行が示す書き換えの方向を提案として添える
- **no-op の delete は、手順 2-1 の micro-test を通したと宣言する** (場面と、取る行動が一致したことを添える)。宣言が無いと、`write-for-harness` は確認を通していない no-op の削除として書かずに報告へ回す

## 参照

- 関連 skill: `inventory-permissions` (別軸: permission / sandbox / hook) / `inventory-skill-mcp` (別軸: skill / MCP 実績集計)
