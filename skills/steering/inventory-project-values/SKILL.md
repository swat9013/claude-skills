---
name: inventory-project-values
disable-model-invocation: true
description: 実行中の project の標準 transcript から、ユーザーが手入力したプロンプト (直近 30 日 / 60 字以上) を決定的に観測し、同一規範の再出現回数を判定材料にして project 規範の候補を証拠 anchor 付きで具体化する棚卸し。反映先は cwd repo の CLAUDE.md 常時ルール / `.claude/rules/<topic>.md` の 2 種と、器が決まらない保留。候補ごとに AskUserQuestion で人間が採否を判定し、承認分だけを worktree + PR で反映する。判定は常に人間、無人 commit はゼロ。Use when「project 規範の棚卸し」「フィードバックから規範を抽出」「却下・訂正から学ぶ」「CLAUDE.md を更新」「rules を更新」「inventory-project-values」「棚卸し」.
---

# inventory-project-values

**実行中の project** でユーザーが出した**フィードバック性のプロンプト** (却下・訂正・方針指示) から規範を拾い上げ、その project の `CLAUDE.md` / `.claude/rules/` へ反映する棚卸し skill。観測源は標準 transcript のうち**人間が手入力した prompt だけ**で、観測範囲は**cwd の repo に限る**。

3 段階モデル (原則: **観測・集計は決定的に、判断は人間に、LLM は文章の具体化のみ**):

1. **決定的観測**: `scan_prompts` tool が transcript を走査し、手入力 prompt だけの mart JSON を出す (手順 1)。`select_candidates` tool が mart を長さ・repo・正規形の完全一致で絞り込み、読み順を確定した slice を出す (手順 2)。どちらの tool も「どれがフィードバックか」「どれが規範か」を**知らない**
2. **LLM 具体化 (このメインコンテキスト)**: slice を読み、同一規範ごとに束ねた候補・反映 diff 案を証拠 anchor 付きで組み立てる。**判定はしない** (手順 3)
3. **人間判定**: 採否を選ぶのは常に人間。候補ごとに AskUserQuestion で提示し (手順 4)、**承認された候補だけ**を worktree + PR で反映する (手順 5)

本 skill は他の inventory 系のような高確度 / 低確度の 2 層化を**しない**。規範の抽出は本質的に内容判断であり、閾値解釈の余地がない決定的シグナルが存在しないため、**全候補が AskUserQuestion を通る**。承認なしの write はゼロ。

## スコープ (改変不可の境界)

- **読み対象**: `~/.claude/projects/**/*.jsonl` (標準 transcript) のみ。走査・抽出は tool の責務で、LLM が transcript を直接読むことはしない。**cwd の repo の prompt だけ**を候補にする (手順 2 の repo 既定解決)
- **書き対象**: **cwd repo の `CLAUDE.md` / `.claude/rules/<topic>.md`** のみ。どちらの器に置くかは**手順 4 で人間が選ぶ** (skill が器を決めるのは禁止。手順 5)。**AskUserQuestion での人間承認後に worktree + PR で**行い、main の working tree を直接編集しない
- **除外**: `~/.claude/CLAUDE.md` (global) / cwd repo 以外のファイル
- **非依存**: claude-mem / dotfiles / herdr。すべて標準 transcript と cwd repo 内 file のみ

## 引数

引数なし (`/inventory-project-values`)。観測窓・絞り込み条件は手順 1 / 2 の tool 引数で調整する (既定は直近 30 日 × 60 字以上 × cwd repo)。

## 手順

### 1. 観測 tool 起動 (決定的)

`mcp__plugin_swat-skills_transcript-ops__scan_prompts` を引数なしで呼ぶ。

- `days` で観測窓を上書き (省略時 30 日)
- 返り値の `path` に mart JSON (`/tmp/inventory-values/mart-<timestamp>.json`) が出る。**mart 本体は返らない**
- **mart は生データ** — bucket も発話型も持たない。返り値の `meta.excluded` に除外理由別の内訳が出るので、`no_prompt_source` が急増していたら CLI schema 変更を疑う (観測の劣化が silent zero にならない設計)。**「急増」の判定には前回 mart が要る**。手元に無ければ `total_prompts` との比を劣化判定の代用にせず、レポートに「前回 mart が無く判定不能」と書く
- **`meta.store` は観測の劣化シグナル**。`broken_lines` (壊れた行) / `unreadable_files` (読めなかった file) / `skipped_nested_files` (観測範囲外の subagent transcript) が 0 でなければ**分母が欠けている** — その数だけ「観測されなかった prompt」があるので、手順 6 のレポートヘッダへ転記する

`meta.total_prompts` が 0 なら「観測不能」を報告して終了する (推測での穴埋めをしない)。

mart は**全 project 横断**で作られる (transcript の lake は project 単位に分かれていない)。project への絞り込みは手順 2 が行う。

### 2. 候補の絞り込み (決定的)

**mart 全件を読まない。** mart は千 prompt / 数百 KB 規模になり、LLM に全件を読ませる前提は成立しない。読む順序は tool が決める:

`mcp__plugin_swat-skills_transcript-ops__select_candidates` に `mart` = 手順 1 の path を渡して呼ぶ。

- 返り値の `path` に slice JSON (`/tmp/inventory-values/candidates-<timestamp>.json`) が出るので、それを Read する
- 読み順は **`text_chars` 降順 → timestamp → session_id → uuid** の全順序。`rank` の昇順に読む
- `limit` で件数を打ち切れる。削られた件数は `meta.truncated_by_limit` に出る (silent cap にしない)
- 各候補に `steering_pattern` (`correct` / `question` / `instruct` / `steer`) が付く。**`limit` で打ち切るときは `steering_patterns.priority_order` の順に拾う** — `correct` (訂正) は規範とのずれが露出した瞬間で、長さ順の打ち切りで落とすと最も惜しい帯。`rank` 自体は動かない (読み順と提示順の分離は手順 4 と同じ)
- `steering_pattern` は表層語だけの決定的分類で、**取りこぼす**。`correct` でない候補を訂正でないと読まない (優先帯は拾い上げの補助であって分類の正解ではない)

#### 2-1. project scope (既定)

`repo` 未指定時は **cwd の git remote** に解決する (`meta.repo_scope: "cwd"`)。他 project で実行したときに無関係な repo の prompt が候補に載らないための既定であり、社内 repo での実行時に個人 repo の prompt を混ぜないのが目的。

- 解決できない cwd (git 管理外) では**全 repo に倒さず失敗する**。`repo` か `all_repos: true` を明示する
- **cwd は server プロセスのもの** (セッション起動時に固定)。棚卸し中に別 project へ移った場合は `repo_root` を明示して解決先を合わせる
- `all_repos` は「repo をまたいで再出現する規範」を見るための逃げ道。**本 skill の反映先は cwd repo なので、`all_repos` の結果をそのまま反映しない** (他 project の発話を根拠に cwd repo の規範を作ることになる)
- repo 別の内訳は slice の `repos` に出る

#### 2-2. 観測帯 (60 字以上)

**既定の入口は 60 字以上の帯** (`min_chars`、default 60)。帯ごとの中身:

| 帯 | 中身 | 既定で候補か |
|---|---|---|
| 1-10 字 | `全部` / `OK` / `A` — 承認・選択肢応答 | 対象外 |
| 11-59 字 | 短い操作指示。単体では復元できない応答が混じる | 対象外 |
| 60-120 字 | 短い方針指示・FB。**単体で意味が通る** | 候補 |
| 121-300 字 | 設計方針・FB が現れる帯 | 候補 |
| 301 字以上 | 長文の要件・対話依頼 (エラーログ貼り付けも混在) | 候補 |

**短文帯 (59 字以下) は候補源に含めない。** 理由は復元不能性 — `OK` / `全部` / `A` は直前の AskUserQuestion や提案とセットでなければ規範を復元できず、mart は prompt 単位で直前の assistant turn を持たないため、単体では証拠にならない。60-120 字帯の発話は単体で意味が通るため、この境界を入口にする。**除外は silent にしない**: slice の `meta.excluded.below_min_chars` と `meta.band_histogram` を手順 6 のレポートにそのまま転記する。

**長さは絞り込みには使えるが判定には使えない。** 301 字以上の帯にはエラーログ・調査資料の貼り付けが相当数混じる。「どれがフィードバックか」の判定は tool に持たせず、手順 3 の具体化と手順 4 の人間判定に委ねる。

#### 2-3. 定型文の除外 (決定的)

正規化 (URL → path → 数値 → 空白畳み) した**正規形が完全一致する群が 3 件以上**なら定型文として除外する。tool はハードコードした文面パターンを持たず、類似度計算もしない — 定型文は正規化後にバイト一致するのに対し、規範の再出現は表層語をほぼ共有しないため、両者は構造的に衝突しない。閾値 3〜5 で結果が変わらない (分布が二峰性) ため引数にも出さない。

**`boilerplate_forms` は第 2 の候補源として必ず読む。** 除外は silent にせず、検出した正規形が件数・repo 数・実例 anchor 付きで slice に出る。**逐語反復された規範が定型判定される経路は実在する** (同じ FB を何度もコピペした場合)。「同じ文を n 回コピペしている」は未ルール化の強いシグナルでもあるため、一覧を読んで拾い戻す。拾い戻した候補は通常の候補と同じ形 (3-3) で手順 4 に載せる。

### 3. 候補の具体化 (LLM)

slice の `candidates` を `rank` 順に読み、**同一規範ごとに束ねた**候補を組み立てる。**判定はしない** — 出すのは候補・証拠・反映 diff 案まで。

**読了予算の規律**: slice も全件全文読みできるとは限らない。プレビュー等で足切りする場合:

- 全文を読んだ件数と足切りした件数を **手順 6 のレポート §5 に provenance として明記する** (何件をどう読んだかが復元できる形)
- **全文を読んでいない record に個別の断定理由を書かない**。「プレビューのみで候補化しなかった」以上のことを書けば、読んでいない内容を読んだことにする

#### 3-1. 探索観点 — 規範が乗る 4 つの発話型

長文を要約するだけでは規範は出てこない。以下の型を**探すあて**として読む。型そのものは決定的に判定できないため tool には持たせておらず、ここが唯一の適用箇所:

| 型 | 見分け方 | 実例 |
|---|---|---|
| **前提の突き返し** | 提案そのものではなく、提案が立つ前提を疑う | 「なぜこの話が出てくる？ どこにこれに誘導する悪い記述がある？」 |
| **方針の明文化** | 選択と、選んだ理由がセットで書かれる | 「独自の仕組みで作ろうとしたが、作り込みすぎて柔軟性にかけていて…シンプルで安定して高品質な今のしくみのほうがよい」 |
| **成果物への FB** | `FB` / `フィードバック` で始まる箇条書き | 「README.md がこちらの事情を書きすぎている。経緯や背景は不要」 |
| **撤退の決定** | やめる判断と、代わりに残すものが併記される | 「tm 削除でよい。ただ、herdr から戻れるように歴史は残しておきたい」 |

どの型にも当てはまらない候補は**捨てずに** informational に落とし、理由を書く (手順 6 のレポート §5)。

**貼り付け境界チェック**: mart の `text` は「人間が手入力した prompt」だが、その中にユーザーが**貼り戻した assistant の応答**が含まれうる。規範文らしき文章を証拠に採る前に、貼り付け境界より前の人間記述だけで規範が復元できるかを確認する。できないものは informational へ落とす。**assistant の文章をユーザーの規範として帰属させない** — 長文帯ほど混入しやすく、エラーログの混在 (手順 2) より見分けにくい。

#### 3-2. 同一規範の束ね (頻度シグナル)

候補は 1 発話 = 1 候補ではなく、**同一規範の束ね**単位で作る。束ねるのは LLM の役割 — tool は長さと repo でしか並べられない (定型文は正規化後にバイト一致するが、規範の再出現は表層語をほぼ共有しないため、tool の定型判定とは別物)。

**再出現回数が判定材料の中心**になる。同じ規範が繰り返し指示されているのは「まだ harness に定着していない」ことの直接の証拠であり、昇格すれば再出現は自然に止む。

**束ね漏れは許容する設計である。** 別々の発話が同一規範だと気づけずに候補が分散することはあるし、逆に束ね損ねて 1 回の発話として埋もれることもある。それを欠陥として扱わない — 拾い漏れた規範は再指示されて出現回数が上がり、次回の棚卸しで拾われる。**束ね漏れは恒久的欠落ではなく遅延**であり、本 skill は「網羅」ではなく「反復の検出」を約束する。後から読んだ人が「網羅していない」を欠陥と誤読しないよう、レポートにもこの前提を書く。

#### 3-3. 候補 1 件の形

候補は以下を揃えて初めて人間が判定できる。欠けたまま手順 4 に進まない:

- **再出現**: 出現回数 / 期間 (最初と最後の timestamp) / repo 数 / **各出現の証拠 anchor**。1 回しか出ていない候補は「1 回」と書く (書かないと束ねたのか単発なのか区別できない)
- **証拠 anchor**: slice の `session_id` / `timestamp` / `repo` + 原文引用 (200 字超は truncate)。束ねた候補では代表 1 件の全文 + 残りの anchor 一覧
- **規範の言い換え**: 原文から復元した規範文を 1-2 行。**原文の意味を拡張しない**
- **反映先分類**: 3-4 の 3 種のいずれか
- **反映 diff 案**: 反映先ファイルの**どの節に何を足すか**を具体化する。既存記述と重複・矛盾する場合はその行を引用して指摘する
- **推測の分離**: 観測 (slice の値・原文引用) を超える解釈には `推測:` prefix を必ず付ける

#### 3-4. 反映先の分類 (3 種)

候補ごとに器の候補を分類する。**決めるのは人間** (手順 4) で、ここで出すのは選択肢と含意まで:

| 分類 | 対象 | 反映経路 |
|---|---|---|
| **CLAUDE.md 常時ルール** | 全セッションで効く不変の規範 (作業方式 / 前提確認 / 禁止事項) | cwd repo の `CLAUDE.md` の該当節に追記 |
| **`.claude/rules/<topic>.md`** | 特定ファイル群の編集時だけ効く細則 (`paths:` で発火条件を絞れるもの) | 既存 rules への追記、または `paths:` を宣言した新規 rules |
| **器が決まらない** | 上 2 つのどちらでもない / 判断材料が足りない (例: 応答規範・skill 化した方がよいもの) | **反映しない**。レポートに器の候補と含意を残し、必要なら issue 起票を提案するところで止まる |

分類の軸は「発火条件で絞れるか」の 1 点。絞れる規約を CLAUDE.md 直書きにすると無関係セッションで毎回ロードされ high-signal が薄まり、逆に `paths:` の無い rules は CLAUDE.md と等価になる。

`器が決まらない` は消化しきれなかった残りではなく、**正規の分類**として AskUserQuestion に載せる。

#### 3-5. 具体化の前に必ず Read する reference

反映先が harness ファイルなので、diff 案を書く前に同一 plugin 内の以下を Read する。**手順 4 の選択肢を組み立てるより前に読み終える** — 器の選択肢に載せた後で読むと、既存 checklist との衝突が PR 作成後に発覚して事後裁定になる:

- 常に**両方**: `${CLAUDE_SKILL_DIR}/../../knowledge/claude-config-review/references/claude-md.md` と同 `rules.md` (器の 2 択がこの 2 つなので、片方だけ読んで両方を選択肢に並べない)
- `器が決まらない` の候補で**新規 skill を選択肢に挙げるなら、挙げる前に**同 `skill.md` も読む
- 反映先の現状把握: cwd repo の `CLAUDE.md` と `.claude/rules/*.md` (既存記述との重複・矛盾を diff 案で指摘するため)

**`${CLAUDE_SKILL_DIR}` 展開後の構造** (相対 path の暗算ミスで File not found を誘発しないよう明示。plugin root の実 path は install 形態で変わるので、ここでは書かない):

- `${CLAUDE_SKILL_DIR}` = `<plugin root>/skills/steering/inventory-project-values`
- 展開後 = `<plugin root>/skills/knowledge/claude-config-review/references/<name>.md`
- **`..` は 2 回**で category 階層を抜けて `skills/` に戻る。1 回だと `skills/steering/knowledge/...` を指し File not found

reference の checklist を提案文面に**引用しない**。用語と判断軸を借りるのみ。

### 4. 人間判定 (候補ごとの AskUserQuestion)

**候補 1 件 = 1 問**で提示する。選択肢は器の選択 (CLAUDE.md 常時ルール / `.claude/rules/<topic>.md` / 見送る) を並べる。

**提示順は出現回数の降順** (同数なら最終出現が新しい順)。slice の `rank` (文字数降順) は**読み順**であって提示順ではない — 長さは規範圧の強さを表さないが、再出現は表す。同数のときは `steering_pattern: correct` を含む束を先に出す (訂正はずれが露出した瞬間で、規範圧が最も高い出方)。

**バッチ規約**: AskUserQuestion は 1 回あたり 4 問までなので、候補は **4 件ずつ**提示し、必要な回数だけ繰り返す。候補が多くて全件は回せないと判断した場合、**打ち切った件数と出現回数の範囲をレポートに明記する** (silent に切らない)。

- 各問には手順 3-3 の再出現欄・証拠 anchor・反映 diff 案を添える。原文引用のない候補を提示しない
- 採用された候補だけが手順 5 に進む。見送り・保留はレポートに残す
- **判定を LLM が代行しない**。「明らかに採用でしょう」と読める候補も必ず問う (3 段階モデルの不変条件)

### 5. 承認分の反映 (worktree + PR)

採用された候補のみ、**cwd repo の** 1 つの worktree で反映して PR にする。**main の working tree は直接編集しない。無人 commit はゼロ** (この時点で人間承認は済んでいるが、commit 前に diff を提示する)。

- **CLAUDE.md**: 常時ルールの該当節に追記する。既存規範との重複は追記せず「既に defend 済み」としてレポートに落とす
- **`.claude/rules/<topic>.md`**: 既存 rules に足すか、`paths:` を宣言した新規 rules を作る。新規作成時は `paths:` の glob が対象ファイルを正確に捉えているかを確認し、その repo の CLAUDE.md が rules 索引を持つなら索引更新も同じ diff に含める
- **器が決まらなかった候補は反映しない**。「近いから CLAUDE.md に入れておく」は本 skill の最も起きやすい逸脱であり、規範の所在を歪める
- **`session_id` を反映先ファイルに書かない。** transcript は消えるため、session ID を証拠に据えると規範が単独で完結しなくなる。証拠 anchor (session_id / timestamp / repo) を残すのは**手順 6 のレポートと PR body だけ**
- **原文から読み取れない事実を断定形で書かない。** 手順 3-3 の「原文の意味を拡張しない」は反映時にも効き続ける。補足が要るなら `推測:` を付けるか、書かない
- worktree を作り (`EnterWorktree` があればそれを使う。なければ `git worktree add`)、反映 → commit → PR 作成まで進む
- PR body には「どの候補を採用したか」を再出現回数と証拠 anchor 付きで書く。手順 6 のレポート summary 表をそのまま貼れる形にする

### 6. Markdown レポート組み立て

`/tmp/inventory-values/report-<timestamp>.md` に mart / slice と並置で書く。以下の**固定 schema**:

1. **ヘッダ**: 観測時刻 / 観測窓 / mart の総 prompt 数 / slice の `min_chars` / `repo_scope` と対象 repo / 候補件数 / 観測の劣化 (`meta.store` の 0 でない項目。すべて 0 なら「劣化なし」と明記)
2. **除外の明示**: slice の `meta.excluded` (`below_min_chars` / `other_repo` / `boilerplate`) と `meta.band_histogram` を転記し、「短文帯 N 件・他 repo N 件・定型 N 件を意図的に対象外にした」ことを明記する。`meta.truncated_by_limit` が非 0 ならその件数も出す
3. **定型一覧**: `boilerplate_forms` を件数付きで転記し、拾い戻した候補があればどれかを書く (拾い戻しゼロならその旨を書く。読んだことが復元できる形にする)
4. **候補 section** (器の分類別): 候補ごとに 再出現 (回数 / 期間 / repo 数) / 発話型 / 証拠 anchor / 原文引用 / 規範の言い換え / 反映 diff 案 / AskUserQuestion の結果 (採用 / 見送り / 保留)
5. **informational**: 4 発話型のどれにも当てはまらず候補にしなかった slice record と、その理由 + 読了 provenance
6. **summary 表**: 番号 × 出現回数 × 器の分類 × 発話型 × 判定結果 × 反映先ファイル。「3 と 7 だけ採用」と言える形

冒頭に**束ね漏れ許容の前提** (3-2) を 1 行入れる。「網羅した一覧」と誤読させない。

**観測由来の事実と LLM 推測の分離**: 証拠欄には slice の値と原文をそのまま転記する。解釈には `推測:` prefix を付けて 1 行に留める。

## 責務

- 規範の**判定は常に人間** (候補ごとの AskUserQuestion。3 段階モデル不変)
- tool は絞り込み (長さ / repo / 正規形の完全一致) と整列まで。bucket / 発話型 / 器の分類を tool に持たせない
- LLM は束ね・候補・証拠・反映 diff 案の具体化まで。承認なしの write はゼロ
- 承認後の反映先は **cwd repo の CLAUDE.md / `.claude/rules/`** のみ。器の決定は人間に返し、決まらなければ反映しない
- global CLAUDE.md / 他 repo への波及は機能外
- 網羅は約束しない (束ね漏れ許容。3-2)

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| mart 全件を読もうとする | 全部読めば漏れないという直感 | 手順 2 の slice を読む。読み順は tool が決める (`rank` 昇順) |
| 他 project の prompt を候補にする | mart は全 project 横断で作られるので、絞らないと混ざる | 手順 2-1。`repo` は既定で cwd に解決される。`all_repos` の結果を cwd repo へ反映しない |
| cwd が git 管理外で失敗する | project 外で起動した | `repo` か `all_repos: true` を明示する。黙って全 repo に倒す実装にはしない (失敗が正しい) |
| 定型除外に隠れた規範を見落とす | 除外を tool 任せにして `boilerplate_forms` を読まない | 手順 2-3。定型一覧は第 2 の候補源。逐語反復された規範が定型判定される経路は実在する |
| 読んでいない候補に断定的な除外理由を書く | slice も全件全文読みできず、足切りしたことがレポート上で見えなくなる | 手順 3 の読了予算。全文読み / 足切りの内訳をレポート §5 に provenance として出し、未読 record には理由を書かない |
| assistant の文章をユーザーの規範として採る | 手入力 prompt に、ユーザーが貼り戻した assistant 応答が混じる | 手順 3-1 の貼り付け境界チェック。人間記述だけで復元できないものは informational へ |
| 候補を 1 発話 = 1 件で出す | slice が発話単位なので、そのまま候補にしてしまう | 手順 3-2 の束ね。再出現回数が判定材料の中心で、束ねないとその情報が消える |
| 束ね漏れを欠陥として報告する / 網羅しようとして止まる | 「棚卸し = 全件網羅」と読んだ | 手順 3-2。拾い漏れは再指示で頻度が上がり次回拾われる。網羅ではなく反復の検出を約束する skill |
| 文字数降順のまま AskUserQuestion に出す | slice の `rank` を提示順と読んだ | 手順 4。`rank` は読み順。提示順は出現回数降順 (長さは規範圧を表さない) |
| 反映先ファイルに session ID を書く | 手順 3-3 の証拠 anchor をそのまま反映先へ持ち込む | 手順 5。transcript は消えるため規範が単独完結しなくなる。session ID はレポートと PR body だけに残す |
| 原文にない事実を断定形で書く | 散文で書き切るうちに、原文から復元できない詳細が混じる | 手順 5。手順 3-3 の「意味を拡張しない」は反映時にも効く。裏が取れないなら `推測:` を付けるか書かない |
| reference を反映直前に読み、PR 後に衝突が出る | 3-5 の Read を「編集の直前」と読む | 手順 3-5。**手順 4 の選択肢を組む前**に `claude-md.md` / `rules.md` の両方を読み終える |
| 長文を要約しただけの候補が並ぶ | 探すあてがないまま読んだ | 手順 3-1 の 4 発話型を探索観点として明示的に当てる。型に当たらない record は informational へ |
| 301 字以上を無条件に規範扱いする | 長さを判定に使った | 長さは絞り込みの入口まで。この帯にはエラーログ・調査資料の貼り付けが混じる。判定は人間 |
| `paths:` の無い rules を作る | rules を CLAUDE.md の分割先としか見ていない | `paths:` が無い rules は CLAUDE.md と等価で常時ロードされる。発火条件で絞れないなら CLAUDE.md 側の器を選ぶ |
| 器が決まらない候補を CLAUDE.md に押し込む | 「近いから」で分類が擦り抜ける | 手順 5。器の決定は人間に返し、決まらなければ反映しない。押し込みは規範の所在を歪める |
| 短文帯・定型の除外が report に出ない | tool が絞ったので気づかない | 手順 6 §2 / §3。`meta.band_histogram` と `boilerplate_forms` の転記を省かない |
| 「明らかに採用」の候補を問わずに反映する | 承認コストを節約したくなる | 全候補が AskUserQuestion を通る。本 skill は高確度層を持たない |
| 候補が多すぎて途中で止める | AskUserQuestion は 1 回 4 問まで | 4 件ずつ繰り返す。打ち切る場合は件数と出現回数の範囲をレポートに明記する |
| main で反映してしまう | 承認後の勢いで編集する | 手順 5。必ず worktree + PR。CLAUDE.md の常時ルール (worktree 必須) が優先する |

## 参照

- 関連 skill: `inventory-claude-md` (別軸: CLAUDE.md / `.claude/rules/` の静的観測) / `inventory-permissions` (別軸: permission) / `inventory-skill-mcp` (別軸: skill / MCP 実績)
- reference (Read 対象): `${CLAUDE_SKILL_DIR}/../../knowledge/claude-config-review/references/{claude-md,rules,skill}.md`
