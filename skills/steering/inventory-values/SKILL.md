---
name: inventory-values
disable-model-invocation: true
description: 標準 transcript のユーザー手入力プロンプト (直近 30 日) を決定的に観測し、121 字以上の帯から価値観候補と反映 diff 案を証拠 anchor 付きで具体化する棚卸し。反映先は engineering-judgment (正本 `skills/knowledge/engineering-judgment/references/values-source.md` → SKILL.md 蒸留の 2 段) / coding-principles (SKILL.md 直接) / 反映先未定 の 3 分類。候補ごとに AskUserQuestion で人間が採否を判定し、承認分だけを worktree + PR で反映する。判定は常に人間、無人 commit はゼロ。Use when「価値観棚卸し」「フィードバックから価値観を抽出」「却下・訂正から学ぶ」「判断規則集を更新」「engineering-judgment を更新」「coding-principles を更新」「inventory-values」「棚卸し」.
---

# inventory-values

セッション中にユーザーが出した**フィードバック性のプロンプト** (却下・訂正・方針指示) から価値観を拾い上げ、判断規則集 (`engineering-judgment` / `coding-principles`) へ反映する棚卸し skill。観測源は標準 transcript のうち**人間が手入力した prompt だけ**。

3 段階モデル (原則: **観測・集計は決定的に、判断は人間に、LLM は文章の具体化のみ**):

1. **決定的観測**: `scripts/scan-user-prompts.py` が transcript を走査し、手入力 prompt だけの mart JSON を出す (手順 1)。`scripts/select-candidates.py` が mart を長さ・repo で絞り込み、読み順を確定した slice を出す (手順 2)。両 script とも「どれがフィードバックか」「どれが価値観か」を**知らない**
2. **LLM 具体化 (このメインコンテキスト)**: slice を読み、価値観候補・反映先分類・反映 diff 案を証拠 anchor 付きで組み立てる。**判定はしない** (手順 3)
3. **人間判定**: 採否を選ぶのは常に人間。候補ごとに AskUserQuestion で提示し (手順 4)、**承認された候補だけ**を worktree + PR で反映する (手順 5)

本 skill は他の inventory 系のような高確度 / 低確度の 2 層化を**しない**。価値観の抽出は本質的に内容判断であり、閾値解釈の余地がない決定的シグナルが存在しないため、**全候補が AskUserQuestion を通る**。承認なしの write はゼロ (ADR 0011 決定 1)。

## スコープ (改変不可の境界)

- **読み対象**: `~/.claude/projects/**/*.jsonl` (標準 transcript) のみ。走査・抽出は script の責務で、LLM が transcript を直接読むことはしない
- **書き対象**: 本 repo (swat-skills) の `skills/knowledge/engineering-judgment/` と `skills/knowledge/coding-principles/` のみ、かつ **AskUserQuestion での人間承認後に worktree + PR で**行う。main の working tree を直接編集しない
- **除外**: `~/.claude/CLAUDE.md` (global) / 他 repo のファイル / `~/.claude/state/rules/` の `#rule` buffer。buffer の消費は `inventory-claude-md` の領分であり本 skill は触らない (repo 表現は同一キーなので突き合わせは可能だが、**書かない**)
- **非依存**: claude-mem / dotfiles / herdr。すべて標準 transcript と本 repo 内 file のみ

## 引数

引数なし (`/inventory-values`)。観測窓・絞り込み条件は手順 1 / 2 の script option で調整する (既定は直近 30 日 × 121 字以上)。

## 手順

### 1. 観測 script 起動 (決定的)

```
${CLAUDE_SKILL_DIR}/scripts/scan-user-prompts.py
```

- `--days N` で観測窓を上書き (省略時 30 日)
- stdout に mart JSON path (`/tmp/inventory-values/mart-<timestamp>.json`) が出る
- **mart は生データ** — bucket も発話型も持たない。`meta.excluded` に除外理由別の内訳が出るので、`no_prompt_source` が急増していたら CLI schema 変更を疑う (観測の劣化が silent zero にならない設計)

`meta.total_prompts` が 0 なら「観測不能」を報告して終了する (推測での穴埋めをしない)。

### 2. 候補の絞り込み (決定的)

**mart 全件を読まない。** 実測で mart は 1,295 prompt / 934 KB あり、LLM に全件を読ませる前提は成立しない。読む順序は script が決める:

```
${CLAUDE_SKILL_DIR}/scripts/select-candidates.py --mart <手順 1 の path>
```

- stdout に slice JSON path (`/tmp/inventory-values/candidates-<timestamp>.json`) が出る
- 読み順は **`text_chars` 降順 → timestamp → session_id → uuid** の全順序。`rank` の昇順に読む
- `--repo <repo>` で repo 単位に絞る。repo 別の候補件数は slice の `repos` に出るので、量が多いときはここを入口にする
- `--limit N` で件数を打ち切れる。削られた件数は `meta.truncated_by_limit` に出る (silent cap にしない)

**既定の入口は 121 字以上の帯** (`--min-chars`、default 121)。実測 1,295 件の分布:

| 帯 | 件数 | 中身 | 既定で候補か |
|---|---|---|---|
| 1-10 字 | 518 | `全部` / `OK` / `A` — 承認・選択肢応答 | 対象外 |
| 11-60 字 | 401 | 短い操作指示 | 対象外 |
| 61-120 字 | 237 | 中間 | 対象外 |
| 121-300 字 | 84 | 設計方針・FB が現れる帯 | 候補 |
| 301 字以上 | 55 | 長文の要件・対話依頼 (エラーログ貼り付けも混在) | 候補 |

**短文帯 (60 字以下・919 件) は候補源に含めない。** 理由は復元不能性 — `OK` / `全部` / `A` は直前の AskUserQuestion や提案とセットでなければ価値観を復元できず、mart は prompt 単位で直前の assistant turn を持たないため、単体では証拠にならない。**除外は silent にしない**: slice の `meta.excluded.below_min_chars` と `meta.band_histogram` を手順 6 のレポートにそのまま転記する。

**長さは絞り込みには使えるが判定には使えない。** 301 字以上の帯にはエラーログ・調査資料の貼り付けが相当数混じる。「どれがフィードバックか」の判定は script に持たせず、手順 3 の具体化と手順 4 の人間判定に委ねる。

### 3. 候補の具体化 (LLM)

slice の `candidates` を `rank` 順に読み、価値観候補を組み立てる。**判定はしない** — 出すのは候補・証拠・反映 diff 案まで。

#### 3-1. 探索観点 — 価値観が乗る 4 つの発話型

長文を要約するだけでは価値観は出てこない。以下の型を**探すあて**として読む。型そのものは決定的に判定できないため script には持たせておらず、ここが唯一の適用箇所:

| 型 | 見分け方 | 実例 |
|---|---|---|
| **前提の突き返し** | 提案そのものではなく、提案が立つ前提を疑う | 「なぜこの話が出てくる？ どこにこれに誘導する悪い記述がある？」 |
| **方針の明文化** | 選択と、選んだ理由がセットで書かれる | 「独自の仕組みで作ろうとしたが、作り込みすぎて柔軟性にかけていて…シンプルで安定して高品質な今のしくみのほうがよい」 |
| **成果物への FB** | `FB` / `フィードバック` で始まる箇条書き | 「README.md がこちらの事情を書きすぎている。経緯や背景は不要」 |
| **撤退の決定** | やめる判断と、代わりに残すものが併記される | 「tm 削除でよい。ただ、herdr から戻れるように歴史は残しておきたい」 |

どの型にも当てはまらない候補は**捨てずに** informational に落とし、理由を書く (手順 6 のレポート §4)。

#### 3-2. 反映先の分類 (3 種)

候補ごとに反映先を分類する。**既存 2 経路に押し込まない・黙って捨てない**が本節の不変条件:

| 分類 | 対象 | 反映経路 |
|---|---|---|
| **engineering-judgment** | コード行より上の判断 (設計 / 技術選定 / テスト戦略 / 品質と速度 / リリース) | 正本 `skills/knowledge/engineering-judgment/references/values-source.md` に追記 → SKILL.md へ蒸留 の **2 段** (手順 5-1) |
| **coding-principles** | コード行レベル (命名 / 実装指針 / 構造設計 / 成果物ごとの表現 / 品質基準) | SKILL.md を**直接**更新 (正本 references なし。手順 5-2) |
| **反映先未定** | 上 2 つのどちらにも属さない (例: 応答規範 — 「シカファンシー禁止」「確信がない場合は一次情報を取得しにいって」) | **器の選択を人間に委ねる** (手順 5-3)。skill は置き場所を決めない |

`反映先未定` は消化しきれなかった残りではなく、**正規の分類**として AskUserQuestion に載せる。実測では応答規範だけで 11 件あり、2 分類のままだと候補提示の時点で消える。

#### 3-3. 候補 1 件の形

候補は以下を揃えて初めて人間が判定できる。欠けたまま手順 4 に進まない:

- **証拠 anchor**: slice の `session_id` / `timestamp` / `repo` + 原文引用 (200 字超は truncate)
- **価値観の言い換え**: 原文から復元した規範文を 1-2 行。**原文の意味を拡張しない** (`#rule` の refine と同じ制約)
- **反映先分類**: 3-2 の 3 種のいずれか
- **反映 diff 案**: 反映先ファイルの**どの節に何を足すか**を具体化する。既存記述と重複・矛盾する場合はその行を引用して指摘する
- **推測の分離**: 観測 (slice の値・原文引用) を超える解釈には `推測:` prefix を必ず付ける

#### 3-4. 具体化の前に必ず Read する reference

反映先が skill ファイルなので、diff 案を書く前に同一 plugin 内の以下を Read する:

- 常に: `${CLAUDE_SKILL_DIR}/../../knowledge/claude-config-review/references/skill.md`
- 反映先の現状把握: `skills/knowledge/engineering-judgment/references/values-source.md` (正本の項目構成) と対応する `SKILL.md`、または `skills/knowledge/coding-principles/SKILL.md`
- `反映先未定` の器として `.claude/rules/` / CLAUDE.md を候補に挙げる場合: 同 `rules.md` / `claude-md.md`

**`${CLAUDE_SKILL_DIR}` 展開後のフルパス例** (相対 path の暗算ミスで File not found を誘発しないよう明示):

- `${CLAUDE_SKILL_DIR}` = `~/.claude/skills/swat-skills/skills/steering/inventory-values`
- 展開後 = `~/.claude/skills/swat-skills/skills/knowledge/claude-config-review/references/<name>.md`
- **`skills/` セグメントが 2 回現れる** (`swat-skills/skills/knowledge/`) 点に注意。`swat-skills/knowledge/` は File not found

reference の checklist を提案文面に**引用しない**。用語と判断軸を借りるのみ。

### 4. 人間判定 (候補ごとの AskUserQuestion)

**候補 1 件 = 1 問**で提示する。選択肢は「採用する / 見送る (レポート記載のみ) / 保留」相当に、`反映先未定` の候補では器の選択肢 (CLAUDE.md 常時ルール / `.claude/rules/<topic>.md` / 新規 skill) を並べる。

**バッチ規約**: AskUserQuestion は 1 回あたり 4 問までなので、候補は `rank` 順に **4 件ずつ**提示し、必要な回数だけ繰り返す。候補が多くて全件は回せないと判断した場合、**打ち切った件数と rank 範囲をレポートに明記する** (silent に切らない)。

- 各問には手順 3-3 の証拠 anchor と反映 diff 案を添える。原文引用のない候補を提示しない
- 採用された候補だけが手順 5 に進む。見送り・保留はレポートに残す
- **判定を LLM が代行しない**。「明らかに採用でしょう」と読める候補も必ず問う (3 段階モデルの不変条件)

### 5. 承認分の反映 (worktree + PR)

採用された候補のみ、1 つの worktree で反映して PR にする。**main の working tree は直接編集しない。無人 commit はゼロ** (この時点で人間承認は済んでいるが、commit 前に diff を提示する)。

#### 5-1. engineering-judgment — 正本 → 蒸留の 2 段

1. **正本を先に更新**: `skills/knowledge/engineering-judgment/references/values-source.md` の該当節に、既存項目と同じ構成 (**価値** / **採用概念** / **検討メモ**) で追記する。由来として証拠 anchor (session_id / timestamp / repo) を残す
2. **SKILL.md へ蒸留**: `skills/knowledge/engineering-judgment/SKILL.md` の対応節へ、決定規則の形 (「競合したらどちらを選ぶか」) に圧縮して反映する

順序は逆にしない。values-source.md には「SKILL.md と本書が食い違ったら本書が勝つ」という更新規約があり、SKILL.md だけを先に書くと正本が負けた状態が生まれる。

#### 5-2. coding-principles — SKILL.md 直接

`skills/knowledge/coding-principles/SKILL.md` の該当節を直接更新する。この skill は正本 references を持たないため 2 段にしない (持たないものを作らない — 経路の非対称は意図的)。

#### 5-3. 反映先未定 — 器の決定は人間

**skill は置き場所を決めない。** 手順 4 で人間が器を選んだ場合はその指示に従って反映し、器が決まらなかった場合は**反映しない**。レポートに器の候補 (CLAUDE.md 常時ルール / `.claude/rules/<topic>.md` / 新規 skill) と各案の含意を残し、必要なら issue 起票を提案するところで止まる。

既存 2 経路への押し込みは**禁止**。「engineering-judgment に近いから入れておく」は本 skill の最も起きやすい逸脱であり、価値観の所在を歪める。

#### 5-4. PR

- worktree を作り (`EnterWorktree` があればそれを使う。なければ `git worktree add`)、反映 → commit → PR 作成まで進む
- PR body には「どの候補を採用したか」を証拠 anchor 付きで書く。手順 6 のレポート summary 表をそのまま貼れる形にする

### 6. Markdown レポート組み立て

`/tmp/inventory-values/report-<timestamp>.md` に mart / slice と並置で書く。以下の**固定 schema**:

1. **ヘッダ**: 観測時刻 / 観測窓 / mart の総 prompt 数 / slice の `min_chars` / 候補件数 / 対象 repo 内訳
2. **除外の明示**: slice の `meta.excluded.below_min_chars` と `meta.band_histogram` を転記し、「短文帯 N 件を意図的に対象外にした」ことを明記する。`meta.truncated_by_limit` が非 0 ならその件数も出す
3. **候補 section** (反映先分類別): 候補ごとに 発話型 / 証拠 anchor / 原文引用 / 価値観の言い換え / 反映 diff 案 / AskUserQuestion の結果 (採用 / 見送り / 保留)
4. **informational**: 4 発話型のどれにも当てはまらず候補にしなかった slice record と、その理由
5. **summary 表**: 番号 × 反映先分類 × 発話型 × 判定結果 × 反映先ファイル。「3 と 7 だけ採用」と言える形

**観測由来の事実と LLM 推測の分離**: 証拠欄には slice の値と原文をそのまま転記する。解釈には `推測:` prefix を付けて 1 行に留める。

## 責務

- 価値観の**判定は常に人間** (候補ごとの AskUserQuestion。3 段階モデル不変)
- script は絞り込みと整列まで。bucket / 発話型 / 反映先分類を script に持たせない
- LLM は候補・証拠・反映 diff 案の具体化まで。承認なしの write はゼロ
- 承認後の反映先は本 repo の判断規則集 2 つのみ。`反映先未定` の器の決定は人間に返す
- global CLAUDE.md / 他 repo / `#rule` buffer への波及は機能外

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| mart 全件を読もうとする | mart は 934 KB / 1,295 件。全部読めば漏れないという直感 | 手順 2 の slice を読む。読み順は script が決める (`rank` 昇順)。全件走破が要るなら `--repo` で分割して回す |
| 長文を要約しただけの候補が並ぶ | 探すあてがないまま読んだ | 手順 3-1 の 4 発話型を探索観点として明示的に当てる。型に当たらない record は informational へ |
| 301 字以上を無条件に価値観扱いする | 長さを判定に使った | 長さは絞り込みの入口まで。この帯にはエラーログ・調査資料の貼り付けが混じる。判定は人間 |
| `反映先未定` を engineering-judgment に押し込む | 「近いから」で分類が擦り抜ける | 手順 5-3。器の決定は人間に返し、決まらなければ反映しない。押し込みは価値観の所在を歪める |
| `反映先未定` を候補から落とす | 2 経路に載らない候補を「対象外」と読んだ | 3-2 の正規分類。実測で応答規範だけで 11 件ある。必ず AskUserQuestion に載せる |
| 短文帯の除外が report に出ない | script が絞ったので気づかない | 手順 6 §2。`meta.band_histogram` の転記を省かない。silent な取りこぼしにしない |
| engineering-judgment の SKILL.md だけ更新する | 正本 references の存在を忘れる | 手順 5-1 の 2 段。values-source.md の更新規約 (本書が勝つ) と矛盾する状態を作らない |
| coding-principles にも正本を作りたくなる | 経路の対称性が気持ちよく見える | 持たないものを作らない。非対称は意図的 (手順 5-2) |
| 「明らかに採用」の候補を問わずに反映する | 承認コストを節約したくなる | 全候補が AskUserQuestion を通る。本 skill は高確度層を持たない (ADR 0011 決定 1) |
| 候補が多すぎて途中で止める | AskUserQuestion は 1 回 4 問まで | 4 件ずつ繰り返す。打ち切る場合は件数と rank 範囲をレポートに明記する |
| main で反映してしまう | 承認後の勢いで編集する | 手順 5-4。必ず worktree + PR。CLAUDE.md の常時ルール (worktree 必須) が優先する |

## 参照

- 仕様: [issue #295](https://github.com/swat9013/swat-skills/issues/295) (skill 本体 + 統治配線) / [issue #294](https://github.com/swat9013/swat-skills/issues/294) (観測 script。実測分布の出典)
- 運用正本: [`docs/steering.md`](https://github.com/swat9013/swat-skills/blob/main/docs/steering.md) §1 (3 段階モデル) / §2 (統治対象マップ ツール 7) / §3 (責務境界)
- 決定: [ADR 0011](https://github.com/swat9013/swat-skills/blob/main/docs/adr/0011-human-triggered-inventory-tools.md) (無人 commit の構造的排除)
- 反映先: `skills/knowledge/engineering-judgment/` (正本 `skills/knowledge/engineering-judgment/references/values-source.md` + SKILL.md) / `skills/knowledge/coding-principles/SKILL.md`
- 関連 skill: `inventory-claude-md` (別軸: CLAUDE.md と `#rule` buffer) / `inventory-permissions` (別軸: permission) / `inventory-skill-mcp` (別軸: skill / MCP 実績)
- reference (Read 対象): `${CLAUDE_SKILL_DIR}/../../knowledge/claude-config-review/references/{skill,rules,claude-md}.md`
