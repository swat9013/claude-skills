---
name: inventory-claude-md
disable-model-invocation: true
description: project の CLAUDE.md 系 (root / `CLAUDE.local.md` / サブディレクトリ / `.claude/rules/`) を静的観測し、行単位の bucket の見直し候補と規範の届き方の数を前後で示すレポートを作る棚卸し。判定は人間で、候補ごとに承認された分だけを write-for-harness で反映する。
---

# inventory-claude-md

project 範囲の CLAUDE.md 系 (root `CLAUDE.md` + project-local `CLAUDE.local.md` + サブディレクトリ CLAUDE.md + `.claude/rules/**/*.md`) を、静的に観測し **行単位の bucket の候補**を LLM に提示させ、判定は人間に残す棚卸し skill。CLAUDE.md の行は外へ出すのが既定で、残す行にだけ根拠を求める。

4 段階モデル (原則: **観測・集計は決定的に、判断は人間に、LLM は文章の具体化のみ、反映は承認分だけ**。本 skill の観測は `open_predicates` を出さないので、`open_predicates` の解釈の工程は無く、bucket の判定基準を当てはめる解釈は LLM が担う):

1. **決定的観測**: `scripts/scan-claude-md.py` が対象ファイル群を走査し、observation JSON を出力する
2. **LLM 具体化 (このメインコンテキスト)**: JSON を読み、行単位で bucket 候補と根拠、提案文面を組み立てる。**判定はしない**
3. **人間判定**: 採否を選ぶのは常に人間。候補ごとに問う
4. **反映**: 承認された候補だけを、1 件ずつ `write-for-harness` に書かせる。**人間承認を得ていない write はゼロ**で、書き終えたら diff を提示する

**確度による 2 層化は持たない** — 静的トークンコスト・注入実績・届き方の観測 (`reach`)・Read の実績は、候補に載せる行と提示の順を決めるのと、証拠と §7 の報告に載せるのに使い、bucket の決定には使わない。反映するものがある bucket の候補は、すべて同じ「候補ごとの承認 → 反映」の経路を通す。

## スコープ (改変不可の境界)

- **読み対象**: project の CLAUDE.md 系 (`<repo>/CLAUDE.md` + `<repo>/CLAUDE.local.md` + サブディレクトリ CLAUDE.md + `<repo>/.claude/rules/**/*.md`)。届き方の観測に限り、`<repo>/.claude/skills/**/SKILL.md` と、それらから link を辿って届く repo 内の文書と、索引行が指す文書と同じ dir の `*.md` も script が読む (書かない)
- **`CLAUDE.local.md` の位置づけ**: memory 階層 4 層目の project-local (個人 override)。本 skill は **root 直下 (`<repo>/CLAUDE.local.md`) のみ**を対象にする (reference `claude-md.md` の memory 階層表が project-local として挙げるのはこの 1 path。project 層と違いサブディレクトリへの言及が無い)。observation JSON では `sources.claude_md.local` (`label: "project-local"`) に独立キーで出るので、**source class は path の文字列判定でなく JSON のキーから読む**
- **書き対象**: 手順 5 で人間が承認した候補の反映先 (`<repo>/CLAUDE.md` / `<repo>/CLAUDE.local.md` / `<repo>/.claude/rules/**/*.md` / repo 内の既存 skill の SKILL.md / move-to-doc の移動先の repo 内の文書) だけ。書くのは `write-for-harness` で、本 skill は反映先と候補を渡す。レポートは `/tmp/inventory-claude-md/` に置く
- **除外**: `~/.claude/CLAUDE.md` (global) は読みも書きもしない。global 昇格は本 skill の機能外 — 人間が chezmoi 側で別途行う
- **transcript の扱い**: bucket 判定の材料 (行・section・参照) はすべて静的 repo 内 file から取る。transcript は**注入実績 (どの file が何 session に載ったか) と compaction 実害**を named query `overhead` 経由で (手順 1b)、**索引行が指す文書を Read した session の数**を named query `read-sessions` 経由で (手順 1c) 読むだけにする。行単位の実績は transcript に存在しないため、行の内容判断が transcript に依存することはない
- **非依存**: claude-mem / dotfiles / swat-skills 固有 gate 実装。**git の tracked/untracked も観測しない** (script は git に依存しない)

**汎用スキル制約**: 参照するのは Claude Code 標準ファイルと `write-for-harness` (references の Read と、反映段での invoke) と `transcript-query` (手順 1b・1c の invoke) のみ。repo 固有の gate 構成 (pre-commit gate script の有無) には触らない — 「lint 化可能か」の判断は LLM 段階で提案するが、配線は repo 側の別作業。

## 引数

引数を取らない。対象は cwd の repo の CLAUDE.md 系に固定する。

## 手順

### 1. 観測 script 起動 (決定的)

```
${CLAUDE_SKILL_DIR}/scripts/scan-claude-md.py --repo-root .
```

- `--repo-root` 省略時 cwd
- stdout に observation JSON path (`/tmp/inventory-claude-md/observation-<timestamp>.json`) が出る
- **script は bucket を出さない**。決定的観測だけを出す (項目は script の docstring) — 本 domain には「count 0 / 完全一致」のような機械判定可能な述語が無く、機械判定できる述語から bucket を確定させる層 (決定的ルール層) の対象外だから

出力に失敗したら (repo に CLAUDE.md が無い等) `sources.claude_md.root.status = "missing"` が入る。**root と local (`sources.claude_md.local.status`) の両方が `missing` のときだけ**「観測不能」を報告して終了する。片方でも `present` なら棚卸し対象があるので続行する (`CLAUDE.local.md` だけが存在する repo は成立する)。

各 file の `token_cost` に **行単位の概算 token** が出る (`per_line_est_tokens` は index 0 = 1 行目の配列、`sections` は見出し別合計と share)。CLAUDE.md 系は session 開始で無条件に載るので、実コストは行数ではなく token — 行数が同じでも表・code block・日本語で数倍違う。

`reach` に規範の届き方の材料が出る (場面か path を絞る語を持つ CLAUDE.md の行・索引行が指す file・どこからも辿れない agent 向け文書・実在しない参照先・写し・常時載る量。数え方は script の docstring)。手順 3 の §7 の前の値と、手順 1c に渡す文書の一覧はここから取る。

### 1b. 注入実績の観測 (決定的)

`swat-skills:transcript-query` を Skill tool で invoke し、named query `overhead` を直近 30 日の観測窓と出力 dir の名前 `inventory-claude-md` で呼ぶ。数える session は cwd の repo のものに絞られる。

- 返り値の `path` に成果物の JSON が出るので Read する (**本体は返らない**)。中身は `rows` の 1 行で、列の読み方は同じ JSON の `contract` が持つ
- `memory_files[]` が **file 別の注入 session 数**
- `sessions_unresolved` が repo を解決できずに数から落ちた session の cause 別の数 (手順 2 の差分の読み方を分ける)
- `compaction` が **捨てられた token** (`dropped_tokens`)
- `static_context.sources` は「何が重いか」の内訳 (`memory_file` / `skill_listing` / `mcp_instructions` / ...)。root `CLAUDE.md` の量は手順 1 の `token_cost` で見る

この観測が取れなくても (道具の取得の失敗・呼び直しても残る `stopped`・`sessions_observed` か `memory_files[]` が 0 件) 手順 2 以降は成立する — 静的観測だけで bucket 候補は組める。取れなかったら手順 2 の `memory_files[]` との突き合わせを止め、取れなかったことと理由をレポート §4「観測範囲外」に明記する。`empty_window` は窓を広げずに取れなかった扱いにする。transcript-query の「返り値の読み方」が 0 件を証拠に使えないとする値が返り値にあれば、その値を同じ節に書き、`memory_files[]` の行の不在を注入 0 session の証拠に使わない。

### 1c. 索引行が指す文書の Read の実績 (決定的)

手順 1 の `reach.index_targets` が空でなければ、`swat-skills:transcript-query` を Skill tool で invoke し、named query `read-sessions` を、`paths` に `index_targets` をカンマで繋いだ値、直近 30 日の観測窓、出力 dir の名前 `inventory-claude-md` で呼ぶ。

- 1b と同じく返り値の `path` の成果物を Read する。`rows` が文書ごとの `sessions_read`、`meta` の `sessions_observed` が分母。列の読み方は成果物の `contract` が持つ
- 成果物の `contract` の `notes` が「数えない」とする経路 (repo の subdirectory で始めた session・symlink 経由の Read など) を、レポート §4「観測範囲外」に転記する。値が 0 の文書があるときは、その行の証拠にも「数えていない経路あり」と添える
- 文書ごとの値を、その文書を指す索引行の証拠に添える。0 の索引行は、効いていないか、その場面が起きていないかのどちらかで、どちらかは人が決める — bucket の決め手にしない
- 取れなかったかの判定と扱いは手順 1b と同じにし (`sessions_observed` が 0 のときと `empty_window` を含む)、取れなかったらどの文書の 0 も「読まれていない」の証拠に使わない

### 2. observation JSON を読んで bucket 候補を提示 (LLM)

着手前に、手順 4 が「常に」とする 2 本 (`patches.md` と `components/claude-md.md`) を Read する — 行の分類に診断表を使う。

observation の schema は script docstring 参照。以下の順に組み立てる。

**bucket vocabulary** (script はこの語彙を知らない。ここで初めて割り当てる):

| bucket | 判定基準 (要旨) | 提案先 |
|---|---|---|
| **keep-inline** | 触る file も作業の種類も問わず判断を変える規則と、索引行 | CLAUDE.md 本文の残置 |
| **move-to-path-scoped** | その path の file を tool で触る場面でしか要らず、compaction の後に次にその file を触るまで消えていても困らない規範。候補には 2 つの条件それぞれの根拠を書く | `.claude/rules/<topic>.md` + `paths:` frontmatter |
| **move-to-doc** | 一部の場面でしか要らないが、move-to-path-scoped の条件を満たさない規範 (file を触らない設計・計画・レビューの議論でも要る、compaction の後に消えると困る) | repo 内の文書へ本文を移し、要る場面ごとに索引行 (CLAUDE.md) か Read させる行 (rules) で指す |
| **move-to-skill** | 特定タスク・状況でのみ必要な手順 | 既存 skill 追記 / 新規 skill |
| **move-to-lint** | 決定的検査に置換できる規則 (行数 / path / 重複 / 参照実在等) | repo の gate/hook (配線は repo 側の仕事) |
| **delete** | 参照切れ / 陳腐化 / 解決済み問題の残骸 / 下の対応表で delete に当たる行 | 削除 |
| **merge** | 他行と重複 / 下の対応表で merge に当たる行 | 統合先の行番号を提示 |
| **rewrite** | 置き場は今のままで、文面を書き換える (下の対応表で rewrite に当たる行) | 対象行の書き換え案 (診断表の行が示す方向) |

**行の分類は診断表で行う**: 手順 4 で Read する `write-for-harness` の patches.md の診断表で各行を分類し、次の対応で bucket に移す。

| 診断表の行 | bucket |
|---|---|
| [既定どおりの行 (no-op)](../write-for-harness/references/patches.md#diag-no-op) / [事実で、環境を見れば分かる](../write-for-harness/references/patches.md#diag-fact-from-environment) | delete |
| [同じ概念の言い直し](../write-for-harness/references/patches.md#diag-restated-concept) / [散らばった定義](../write-for-harness/references/patches.md#diag-scattered-definition) | merge |
| [事実で、一部の分岐でしか要らない](../write-for-harness/references/patches.md#diag-fact-some-branches) | move-to-path-scoped / move-to-doc / move-to-skill |
| [独立した trigger 語を持つ規律、またはほかの skill からも要る規律](../write-for-harness/references/patches.md#diag-independent-discipline) | move-to-skill |
| [機械が判定でき例外の無い規範・一意に決まる手順](../write-for-harness/references/patches.md#diag-computational) | move-to-lint |
| [弱い表記・弱い語](../write-for-harness/references/patches.md#diag-weak-wording) / [禁止の形](../write-for-harness/references/patches.md#diag-prohibition) / [手順の列挙](../write-for-harness/references/patches.md#diag-procedure-list) | rewrite |

- 上の対応の merge の 2 つは、診断表ではパッチが「残す」の行の但し書き (統合) に書かれている。但し書きに当たる行を先に merge にする。残りの「残す」の行のうち、keep-inline の根拠を書ける CLAUDE.md の行と索引行だけを keep-inline にし、書けない行は要る場面に合わせて move-to-path-scoped / move-to-doc / move-to-skill の候補にする — 常時載る行は全 session の費用になる。`paths:` の無い rules の行も常時載るので、CLAUDE.md の行と同じに扱う。`paths:` を持つ rules の行は、move-to-path-scoped の条件を満たせば keep-inline (その rules に残置し、根拠に 2 つの条件を書く)、触る file も作業の種類も問わず効くなら informational に「常時の規則が path-scoped の rules にある」と書き、それ以外は move-to-doc の候補にする。上の対応に無いそれ以外の行は、bucket を自分で割り当てず informational に理由付きで落とす
- 参照切れ・陳腐化による delete は診断表の外 (参照の実在) で判定する
- **no-op の delete 候補**: 診断表の no-op の行の判定に従って delete に載せる。判定の根拠を証拠欄に書く

**bucket 割当ての制約 (script との責務分担)**:

- LLM は observation JSON の証拠に**紐付けて**割り当てる。keep-inline の根拠には、その規則が判断を変える場面が触る file にも作業の種類にもよらないことを、行の内容から**可視に**書く。`reach.scoped_lines` に載った行を keep-inline にするなら、載った語があっても場面を絞らない理由も書く。`CLAUDE.local.md` の行は、下の「project-local に置く理由が内容にある」も keep-inline の根拠に数える
- move-to-doc の候補には、移動先の文書の path (既存か新規)・索引行か Read させる行の文面・その行を置く file を逐語で書く — 手順 6 はこの 3 つを候補から写すので、どれかが無ければ no-placement になる
- 決定的観測を超える推論には `推測:` prefix を必ず付ける (例: `推測: このルールは dispatch 実行時のみ必要と読める`)
- lint 化可否は LLM の判断で、根拠に**適用可能な判定形式** (regex / AST / 静的解析) を 1 行明示する。実 gate 配線は書かない
- 参照実在 fail は `move-to-lint` の証拠にできる。数える前に `link_targets[].check_mode` を見て、**実際に検査されて解決しなかった行 (`exists: false`) だけを fail に数える** — `url` / `anchor` / `dynamic` / `empty` は fail-safe で未検査 (`exists: null`) なので、レポート §4「観測範囲外」へ回す。fail 一件で削除を主張しない (書き手の誤植 vs 陳腐化を区別できない)
- **inline code span (backtick 内) の path は script の `link_targets` に出ない**。`[text](target)` markdown link と `@path` import のみが検査対象。バッククォート内で書かれた path のうち `reach.missing_targets` が検査するものの範囲は script の docstring が持つ。検査しない backtick 内の path は、参照実在の主張をせずに §4 に回す (「link 抽出仕様の範囲外」を明記)
- **サブディレクトリ CLAUDE.md の行は読取対象に含めるが、提案先には推奨しない** — root と subdir で規範が競合しても物理観測では区別できないので、置き場を移す候補に倒す。どの bucket にするかは root の行と同じ表で決める
- bucket vocabulary の**どれにも該当しないなら informational に落として理由を書く** (握り潰さない)

**コスト × 実績の使い方 (候補の重み付け。bucket の決定ではない)**:

- 候補の行群には `token_cost.per_line_est_tokens` を合算した **est_tokens を必ず添える**。「毎 session この行群に払っている概算 token」が、同じ bucket の候補間の優先順位になる
- `.claude/rules/<topic>.md` は `memory_files[]` と突き合わせる。**注入 0 session の file は `memory_files[]` に行として現れない**。判定は**差分で行う** — 手順 1 の `sources.rules[]` が列挙した file のうち `memory_files[]` に無いものが差分で、読み方は `sessions_unresolved` (repo を解決できなかった session の cause 別の数) の合計 N で分かれる
    - N が 0 なら、差分の file は「窓内で 1 度も注入されていない」。それは規範が届いていない証拠なので、`move-to-path-scoped` の glob 見直しを提案する。file を触らない場面でも要る規範なら move-to-doc を、keep-inline の根拠を書けるときだけ CLAUDE.md への差し戻しを提案する
    - N が 1 以上なら、差分の file はレポート §4「観測範囲外」に「未確定: 解決できなかった session N 件の注入が数に入っていない」と `sessions_unresolved` の値 ({cause: 数}) を添えて並べ、N が 0 のときの提案をしない — 理由は成果物の `contract` の `notes` が持つ。その file に別の根拠で候補が立ったときは、候補の証拠にも同じ文を添える
- 逆に `sessions_injected` が観測 session のほぼ全数に届いている path-scoped rule は、実質常時ロードなので CLAUDE.md 本体と同じコスト扱いで読む (行の直下の `sessions_observed` が分母)
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
    - 提案 (1-2 行、証拠に紐づく)。新しい `.claude/rules/<topic>.md` を起こす move-to-path-scoped の候補は、作成先 path と `paths:` の glob も逐語で書く — 手順 6 はこの 2 つを候補から写すので、無ければ no-placement になる
    - 適用手順 (bucket 別分岐、下表参照)
    - write-for-harness の報告 (手順 6 で渡した候補だけ): 契約の「返答を呼び出し元の報告へ運ぶ」節が載せさせるものを逐語で
3. **informational**: bucket vocabulary の**どれにも該当しない**行の一覧と理由 (bucket vocabulary の適用先が不明な行 / 用途不明な行)。**汎用性が高く見える行もここに落とす** — 「(参考) global 昇格の余地あり — 判定は chezmoi 側で人間実施」と書き、bucket 候補としては出さない (global は本 skill の機能外)
4. **観測範囲外 (人間確認へ回す)**: 参照実在検査が fail-safe で判断不能だった行 (`exists: null`) / `reach.missing_targets` が検査しない inline code span の path 参照 / observation JSON の穴 / **観測境界の外にある file** (サブディレクトリの `CLAUDE.local.md` 等、存在しても JSON に出ないもの) / 手順 1b・1c が取れなかった理由と、証拠に使えない返り値の値 / 手順 1c の `read-sessions` が数えていない経路 (`notes` の転記)。**bucket 判定の証拠として使わない**行をここに集める
5. **meta 観測**: CLAUDE.md / `CLAUDE.local.md` 自体の物理計測 (行数 / 見出し階層 / @import 数)。observation JSON の `meta.root_meta` と `meta.local_meta` を**それぞれ転記**する (`meta` は root と local の 2 本立てで、subdirs / rules は含まない)。**`status = "missing"` の側は数値を転記せず「(無し)」と書く** — `summarize_meta` は不在時に全項目 0 を返すので、そのまま載せると「存在するが 0 行」と読めてしまう。閾値 enforce はしない — 「参考値」として提示のみ
6. **summary 表**: 番号 × bucket × 対象 × scope × 採否。「3 と 7 だけ採用」と言える形。scope 列で `project` と `project-local` を区別する (適用手順が分岐するため)。採否列は手順 5・6 の結果 (反映済み / 見送り / 承認済み・未反映 / no-placement / `undecided` / 問わない bucket は `-`) を、手順 6 を終えた時点で書き込む。承認済み・未反映・no-placement の候補は、手順 6 がレポートに残すとしたものを候補 section に書く。`undecided` があれば、表の下に件数と番号の一覧を 1 行書く
7. **届き方の数**: 次の 5 つを、実行の前と後の 2 列の表で示す。上の 4 つは 0 を合格とし、合否と 0 でない数の該当の一覧は後の列 (後の列が空なら前の列) について表の下に書く。常時載る量には合否を付けない — 量だけでは行の要否が決まらない
    - 移す候補にも根拠つきの keep-inline にも、人が承認した書き換えにもなっていない、場面か path を絞る語を持つ行の数 (`reach.counts.uncovered_scoped_lines`)。手順 2 を終えたら、移す候補 (move-to-path-scoped / move-to-doc / move-to-skill) と根拠つきの keep-inline になった `reach.scoped_lines` の行の `text` を 1 行 1 件で `/tmp/inventory-claude-md/covered-before-<timestamp>.txt` に書き、手順 1 の script に `--covered <その file>` を足して起動し直した値を前の値にする (この起動の observation JSON が、後の値の `--before` になる)
    - どこからも辿れない agent 向け文書の数 (`reach.counts.unpointed_docs`)
    - 索引行と Read させる行が指す先が実在しない数 (`reach.counts.missing_targets`。検査する範囲は script の docstring)
    - 同じ本文が 2 か所以上にある数 (`reach.counts.duplicates`)
    - 常時載る量 (`reach.always_loaded` の est_tokens と chars)

    後の値は、手順 6 を終えた後に手順 1 の script を `--covered` と `--before` つきで起動し直した観測から取る。前の値と同じ定義で数える (前の時点で承認した書き換えは無い)。次の 2 つを揃える。反映が 0 件でも同じ手順で取る
    - 後の `--covered` の file は (本 skill の単独の実行では `/tmp/inventory-claude-md/covered-after-<timestamp>.txt` に)、前の file の行に、move-to-doc の反映で足した索引行と Read させる行の `text` と、承認して反映した rewrite・merge の行の反映後の本文 (1 行 1 件) を足して書く。`inventory-harness` の自律で直す一覧から、人が外さずに反映した rewrite・merge も承認に数える (趣旨の承認)。足した行は `uncovered_scoped_lines` に数えない。反映された移す候補の行は元 file から消えており、反映されなかった移す候補の行はこの実行の候補として覆われたまま残る
    - `--before` には前の値を取った観測の observation JSON を渡す。母集団はその `reach.population_docs` (文書の集合) を使うので、索引行を消す候補を反映しても、前の母集団の文書は数から外れない

    表の下に、索引行が指す文書ごとの Read session 数 (手順 1c。分母の `sessions_observed` を添える) を並べる。`reach.index_targets` が空なら「索引行が指す文書なし」、手順 1c が取れなかったときは「観測なし」と書いて §4 を指す

各 section の書き下し方が掴めないときは `${CLAUDE_SKILL_DIR}/references/report-example.md` を Read する (schema の正本は上の 7 項で、reference はその記入例)。

**観測由来の事実と LLM 推測の分離**: レポートの証拠欄には observation JSON の値を**そのまま転記**する。LLM の解釈を混ぜる場合は `推測:` prefix を必ず付けて 1 行に留める。

**適用手順の bucket 別分岐** (レポートに埋め込む。手順 6 の反映はこの表に従う。括弧内は各 file に当てる patches.md のパッチ種別):

| bucket | 適用手順 |
|---|---|
| keep-inline | 変更なし |
| rewrite / delete / merge | 対象 file (`<repo>/CLAUDE.md` / `<repo>/CLAUDE.local.md` / `<repo>/.claude/rules/**/*.md`) を編集 (書き換え / 削除 / 統合)。merge が file をまたぐなら、統合先 (書き換え) と削除側 (削除) の file を別々に編集する |
| move-to-path-scoped | 置き先を候補化の時点で `write-for-harness/references/components/rules.md` のチェックリストの「分ける順」で決めて提案に書き、承認後は提案に書いた置き先に書く — 同じ `paths:` で同じ関心の既存 rules を置き先にしたならそこへ追記 (追加)、新 `.claude/rules/<topic>.md` を置き先にしたなら作成して `paths:` frontmatter を宣言 (追加)。反映の段で置き先を選び直さない (承認していない file に書くことになる)。どちらでも元 file の対象行を削除 (削除) |
| move-to-doc | 移動先の文書へ本文を書く (追加。文書が無ければ作成)。提案に書いた file に索引行か Read させる行を足し (追加)、元 file の対象行を削除 (削除)。元 file と索引行を置く file が同じなら、対象行を索引行へ書き換える (書き換え) 1 回で済む |
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

reference の checklist を提案文面に**引用しない**。用語と判断軸を借りるのみ。

### 5. 人間判定 (候補ごとの AskUserQuestion)

レポートを提示したうえで、**反映の対象になる候補を 1 件 = 1 問**で問う。対象は rewrite / delete / merge / move-to-path-scoped / move-to-doc / repo 内の既存 skill への move-to-skill の候補。keep-inline・informational・move-to-lint・新規 skill への move-to-skill は反映するものが本 skill に無いので問わず、レポートに残す。

- 選択肢は「採用」と「見送る」を並べ、各問にはレポートの対象テキスト・証拠・提案を添える。`共有化:` の候補には、共有してよいかの確認を問いの文面に含める
- 提示順は bucket ごとに est_tokens の降順 (手順 2 の「コスト × 実績の使い方」の優先順位)
- **バッチ規約**: AskUserQuestion は 1 回あたり 4 問までなので、候補は **4 件ずつ**提示し、全候補を問い終えるまで繰り返す — 候補の多さを理由に本 skill が打ち切ると、問わなかった候補の採否を LLM が決めたことになる。人が提示を打ち切ったら、答えの出ていない候補 (示して答えが無い候補と、まだ示していない候補) を `undecided` とし、件数と一覧をレポートに残して、承認された候補だけで手順 6 へ進む
- **判定を LLM が代行しない**。採用が明らかに見える候補も必ず問う

### 6. 承認分の反映

採用された候補だけを、手順 3 の適用手順に従って反映する。**変更の届け方は実行 project の運用に従う。**

**文面を書く工程は `write-for-harness` に委ねる。** 本 skill が持つのは候補・証拠と反映先の指定で、harness ファイルの文面規範は持たない。invoke の前に `${CLAUDE_SKILL_DIR}/../write-for-harness/references/caller-contract.md` (呼び出し元向けの契約) を Read し、契約が定める事項は契約に従う。順序は次の 2 段で固定する:

1. **採用された候補ごとに `Skill(swat-skills:write-for-harness)` を invoke する。**
    - 契約の「2 file にまたがる変更」と同じく file ごとに invoke する候補は、move-to-path-scoped と move-to-skill (移動先 + 元 file の行削除)、move-to-doc (移動先の文書 + 索引行を置く file + 元 file。後の 2 つが同じなら 2 file)、file をまたぐ merge (統合先 + 削除側)、共有化 (移動先 + `CLAUDE.local.md` の行削除)
    - どの候補を反映するかと 6-1 の制約を宣言する。2 file にまたがる候補では、その invoke の file に当てるパッチ (手順 3 の適用手順表の括弧内の種別) も宣言する — 候補全体を渡すと、書き手は他 file にまたがる削除・統合として報告だけにし、`未反映:` を返す。候補の場所は対象テキストの引用で示す — 先の反映で行番号がずれるので、行番号はレポート上の識別子に留める
    - invoke ごとに、契約の「委譲後の変更 path の照合」節の手順で照合する。許可 path は、args が既存 file の path ならその 1 本 (2 file にまたがる候補も invoke ごとに、その invoke の file だけ。契約に従って rules の索引行を足す invoke の `CLAUDE.md` を含む)、`新規: rules` で起こす候補なら作成先 `.claude/rules/<topic>.md` の 1 本。作成先と `paths:` の glob は、候補の提案に逐語で載っているものを契約の「渡す args」のとおりに渡し、どちらかが載っていなければ契約のとおり invoke せずに no-placement とし、欠けたもの (作成先 / glob) をレポートの候補 section と報告に残す。止めたときは、契約が残させるものをレポートの候補 section と報告に残す
    - move-to-doc の移動先の文書は CLAUDE.md・rules・SKILL.md のどれでもないので、その invoke の直前の同じ turn で component を `claude-md` と宣言する — 文書は索引行か Read させる行が Read させる先で、置き場の規範を claude-md の reference が持つ。文書がまだ無ければ `新規: claude-md <目的>` を渡し (`<目的>` は候補の索引行か Read させる行の文面)、作成先 path を同じ turn で宣言する。許可 path は作成先の 1 本
2. **全候補の反映が済んだら (照合で止めたときはそこまでに) diff と、別立てで invoke ごとの委譲先の返答のうち契約の「返答を呼び出し元の報告へ運ぶ」節が載せさせるもの (レポートの候補 section にも残す) を提示し、手順 3 の §7 の手順で後の値を書き、summary 表の採否列を書き込む**

- 契約の「反映を終えられなかったとき」の承認済み・未反映に当たった候補は、同節が載せさせるものと一緒にレポートと報告に残す (承認が黙って消えないようにする)

#### 6-1. invoke 時に文面で添える制約

`write-for-harness` は棚卸し側の制約を知らないので、invoke のたびに渡す:

- **反映先ファイルには規範文だけを書き、観測の証拠 (est_tokens / 注入 session 数 / Read session 数 / 行番号) は書かない。** 証拠はレポートに置く
- **承認された bucket と提案の範囲で書く。** 元の行の意味を拡張しない。rewrite は、診断表の行が示す書き換えの方向を提案として添える

## 返却 (統合の棚卸しから開かれたとき)

`inventory-harness` の subagent として Read で開かれたときは、人に問わず、repo の file を書き換えない。手順 1〜4 を進め、手順 3 のレポートを採否の列と §7 の後の値の列を空けて書き、手順 5・6 は行わずに、最終報告で次を返す。

- レポートの path と、§4「観測範囲外」の要旨と、§7 の前の値と、後の値を取るのに要るもの (起動し直す script の command と引数、前の値に使った `--covered` の file の path と、その観測の observation JSON の path (後の値の `--before`)。後の値は、手順 3 の項目 7 の後の値の段落に従って取る)
- 手順 5 が問う対象の候補の一覧 (手順 5 の提示順)。1 件ごとに: レポートの番号 / bucket / `file:line-range` と対象テキストの冒頭 / est_tokens / scope (`project` / `project-local`) / 反映 (invoke ごとの反映先 file と当てるパッチの種別 — 手順 3 の適用手順の表。新しい rules は作成先と `paths:` の glob、move-to-doc は移動先の文書・索引行か Read させる行の文面・それを置く file と、移動先の文書を書く invoke では component を `claude-md` と宣言すること (新しい文書なら `新規: claude-md <目的>` と作成先)) / 削除の根拠 (delete の候補だけ: no-op / 環境を見れば分かる事実 / 参照切れ・陳腐化) / 添える制約 (6-1 の各項) / `共有化:` の候補は人への確認事項
- 手順 5 が問わない候補のうち、move-to-lint・新規 skill への move-to-skill・informational は「反映しない」と付け、レポートの番号・bucket・対象・証拠の要旨 1 行を返す。keep-inline は件数だけを返す
- 手順 1 で「観測不能」になったときは、その理由を言い換えずに返す

## 参照

- 関連 skill: `inventory-permissions` (別軸: permission / sandbox / hook) / `inventory-skill-mcp` (別軸: skill / MCP 実績集計)
