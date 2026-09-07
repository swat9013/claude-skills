---
name: inventory-skill-mcp
disable-model-invocation: true
description: install 済みの skill / MCP を直近 30 日の transcript invocation と突合し、単位別 (skill / MCP tool / MCP server / plugin) の削除・見直し・保持候補を証拠付きで提示する棚卸し (判定は人間)。
---

# inventory-skill-mcp

install 済みの skill / MCP を **transcript の tool_use 実績**と突合し、単位別に削除候補 / 見直し候補 / 保持を LLM に**候補提示までさせて**、判定は人間の判断に残す棚卸し skill。

3 段階モデル (原則: **決定的にできる推論は tool へ、意味判断だけを LLM へ、判定は人間に**):

1. **決定的観測 + 決定的ルール**: `scan_invocations` tool が分母列挙 + 抜粋 sampling + 提示分母 + token 経済を出し、機械判定可能な条件を評価して `rule_candidates` (`bucket_candidate` / `rule_fired` / `rule_inputs` / `open_predicates` / `near_misses`) を mart に書く。**tool は bucket を確定しない**
2. **LLM 具体化 (このメインコンテキスト)**: `open_predicates` に挙がった条件だけを判断して bucket を確定し、証拠 anchor 付き Markdown レポートを組み立てる。**採否は決めない**
3. **人間判定**: 削除/見直し/保持を選ぶのは常に人間。**高確度候補** (手順 3) はセッション内で AskUserQuestion 提案し、承認されたら同セッション内で適用に進む。**低確度候補**はレポート提示で止まる

無人 commit は行わない (適用は必ず AskUserQuestion での人間承認を経る)。

汎用スキル制約: 参照するのは Claude Code 標準ファイルのみ (`~/.claude/projects/` / `~/.claude/plugins/installed_plugins.json` / 各 plugin の `.claude-plugin/plugin.json` / `~/.claude/skills/` / 現 repo `.claude/skills/` / `~/.claude.json` / 現 repo `.mcp.json`)。swat-skills 固有 hook 資産 (tool-signatures.jsonl 等) には触らない。

## 手順

### 1. 観測 tool 起動 (決定的)

`mcp__plugin_swat-skills_transcript-ops__scan_invocations` を引数なしで呼ぶ。

- 既定で直近 30 日を集計する。返り値の `path` に mart JSON (`/tmp/inventory-skill-mcp/mart-<timestamp>.json`) が出るので、それを Read する (**mart 本体は返らない**)
- `days` で観測窓を上書き。`repo_root` で分母源 project を切替 (省略時は server プロセスの cwd)
- 想定所要時間: 初回のみ transcript の取り込みに十数秒かかる。2 回目以降は差分だけを取り込むので 1 秒未満 + 集計時間

返り値の `meta.total_invocations` が 0 なら (transcript lake が無い / config が壊れている等) 「観測不能」を報告して終了。

### 2. mart を読んで bucket を確定する (LLM)

mart の読み方の注記と **rule カタログ**は mart の `contract` が正本 (schema と規則を本書に再記述しない)。

- `rule_candidates` が機械判定済みの候補。**`open_predicates` に挙がった条件だけを判断し**、満たすと判断したものだけ `bucket_candidate` を bucket として確定する。delete 系 bucket の根拠は `rule_fired` に置く — `sessions_presented == 0` の unit は「提示されていない」だけなので rule が発火せず `near_misses` に落ちる
- `near_misses` は「あと 1 条件」で外れた unit と落選理由。**閾値・分母・巻き込みの当否を疑う証拠はここにしか出ない** — レポートの informational に転記する
- `units` は invocation を 1 件以上持つ unit だけ。count 0 の unit は `denominators` と `rule_candidates` にしか現れない。count は session id + `tool_use.id` で dedupe 済みなので、session 内の複数回呼び出しはそのまま累計として読む
- outcome breakdown は参考値として読み、**bucket 判定は count と success / error の実数で行う**。`is_error` からの user-reject 判定は best-effort で Claude Code の [#29499](https://github.com/anthropics/claude-code/issues/29499) の false positive を除ききれず、`unknown` は「観測できなかった」であって失敗ではない

**単位別 bucket vocabulary** (tool は候補ラベル `*-pending` までしか出さない。確定はここ):

| 単位 | bucket |
|---|---|
| skill | `delete-candidate` / `review-candidate` / `keep` / `insufficient-data` |
| MCP server | `disconnect-candidate` / `keep` / `insufficient-data` |
| MCP tool | informational のみ (個別 on/off は Claude Code に無い、server 判断の材料) |
| plugin | `uninstall-candidate` / `keep` / `insufficient-data` |

`review-candidate` の見直し方向 (広げる/狭める) はここでは決めない — SKILL.md 本文の診断は本 skill の観測範囲外。

**open_predicates の判断方法** (述語文そのものは contract の rule カタログが正本。ここは判断の**手順**だけ):

- `rename_or_removal_in_window`: rename / 削除の commit 日 (`git log --diff-filter=D`) と invocation の timestamp を突き合わせる。invocation が rename 前なら参照元の修正は不要 (ADR / plan / 変更履歴の旧名は記録であり書き換えない)
- `denominator_completeness`: **実行中セッションの available skill / MCP 一覧を思い出し**、mart の分母に無いが存在するものを `session-observed` タグ付きで report に載せる (claude.ai connectors / built-in skill が典型)。**判断ではなく転記**なので原則と矛盾しない。現 project 以外の `.claude/skills` / `.mcp.json` は tool が読まないので、他 project scoped の unit は `denominator-unknown` として報告し、棚卸しを project ごとに回してカバーする
- `removable_independently`: 第三者 plugin 同梱の skill は `/plugin` 操作でしか触れない。単体で外せるかを手順 4 の分岐表と突き合わせて判断する

**「推測:」prefix 分離の義務**: mart の証拠 (count / share / rank / 抜粋の user_prompt / tool_input / outcome) に紐づく記述には prefix を付けない。証拠に紐づかない一般論には `推測: ` prefix を付ける。証拠ゼロで根拠を書かない選択肢もある — 埋めるために推測で埋めない。

**channels 内訳と coverage の使い方** (思想系 skill の判定を歪めないための補正):

- 「session 開始で 1 度 load → 以降 session 全体で暗黙適用」型の skill (`coding-principles` / `engineering-judgment` / `test-strategy` / `pr-quality` 等) は count 単独では実適用回数を過小評価する。`units[skill][].channels.command + .read > 0` の unit は「1 session 1 load 型」の可能性が高いと解釈する
- coverage を評価するときは `sessions[]` を絞り込む: コード編集の思想系なら `has_code_edit == true` を分母、その中で loaded_skills に対象 skill を含む session を分子とする。設計議論系なら `has_plan_mode == true` の session を分母とする
- **「編集前に呼んだか」を問うときは `first_skill_invoke_ts < first_code_edit_ts` の session だけを分子に取る** (session 中いつでも呼べば分子に入る coverage とは別の指標)。`first_skill_invoke_ts` は session 内の最初の skill invoke なので、`loaded_skills` が 2 件以上ある session では対象 skill 自身の順序が確定しない — その session は `query` の ad-hoc SQL で確かめるか、確定不能として分子から外したことを併記する
- **どの skill を「思想系」とするかは LLM 判断**。tool は語彙を持たない (skill 名の一覧を tool に持たせると新設 skill を取りこぼす)
- **coverage を出す前に「invoke を促す機構の稼働期間」と観測窓を突き合わせる**。機構 (SessionStart 注入 hook 等) の導入日 / 撤去日が観測窓の内側にあると、既定の 30 日窓は稼働前後を混ぜて coverage を歪める。導入日・撤去日は repo の `git log` で確認し、ずれていれば `days` を稼働期間に合わせて再スキャンしてから判定する。狭めた窓は母数が落ちるので、割合ではなく **分子/分母を n 付きで併記**する

**token 経済 (`usage`) の使い方**: `usage.by_skill` は「呼ばれているが重い skill」を count と独立に見るための軸。count が低くても `output_tokens` / `cache_creation_input_tokens` が突出する unit は `review-candidate` の根拠になる (削除ではなく**縮小**の候補)。`by_skill` が帰属 turn だけの下限である点は mart の `contract.notes` が正本。

標準フロー外の追加検査が要るときは `mcp__plugin_swat-skills_transcript-ops__query` に read-only SQL を投げる (単発に留める)。

### 3. 高確度候補の抽出とセッション内提案

bucket 確定後、以下をすべて満たす unit だけを高確度候補として抽出する:

1. `rule_fired` に `unused_skill` / `unused_mcp_server` / `unused_plugin` のいずれかが入っている (機械判定可能な条件は tool が確認済み)
2. `open_predicates` の全条件を**決定的観測だけで**満たすと判断できた (`推測:` prefix を要しない)
3. 適用手順が単一の既定分岐で完結する

高確度候補は候補ごとに証拠 1-2 行 (`rule_inputs` の count / sessions_presented / 分母 source) + 適用手順 (手順 4 の単位別分岐表) を添えて **AskUserQuestion で選択肢を提示する** (例:「obsidian plugin は 30 日 0 invocation (skill 3/3 未使用・同梱 MCP も 0)。uninstall しますか?」。選択肢は「適用する / 見送る (レポート記載のみ) / 保留」相当)。

- **承認されたらそのまま同セッション内で適用に進む**。適用は単位別分岐に従う: swat-skills 本体 / third 分は swat-skills repo の運用に従って反映、他 plugin の `/plugin` 操作・MCP config 編集・claude.ai connectors disconnect 等の人間側操作は具体的手順を提示して受け渡す
- 却下・保留された候補、および 3 条件を満たさない候補はすべて手順 4 のレポートへ回す。「count 1 だしほぼ確実」のように基準を緩めたくなったものもレポート側に落とし、緩和は rule 実装か本 SKILL.md の改訂として行う

### 4. Markdown レポート組み立て

`/tmp/inventory-skill-mcp/report-<timestamp>.md` に mart と並置で書く。以下の**固定 schema**:

1. **ヘッダ**: 観測窓 / 総 invocation 数 / distinct sessions / 判定可能性 (`sufficient_for_relative_judgment`)。総 invocation が閾値未満のとき (新規 install / 長期休止後の初回等) は全 unit を `insufficient-data` としてヘッダで宣言し、informational 提示に留める (`relative_judgment_available` が外れて rule も発火しない)
2. **候補 section** (bucket 別に列挙):
    - 対象 (unit id) / 単位 (skill / MCP server / plugin)
    - count / share / rank / percentile
    - bucket (上表の語彙) と、`rule_fired` / `open_predicates` の判断結果
    - 証拠 max 3 件 (mart から転記。session_id + timestamp anchor 付き)。抜粋の `user_prompt` は空文字のことがある (tool_use が assistant turn 開始直後で、先行 user turn が meta tag のみ) — 空のまま転記し、中身が要るなら anchor から生 transcript を読む
    - 提案 (1-2 行、証拠に紐づく)
    - 適用手順 (単位別分岐、下表参照)
3. **informational**: MCP tool 表 (server 判断の材料)、`near_misses` (落選理由つき)、`open_predicates` を満たさないと判断した候補
4. **summary 表**: 番号 × bucket × 対象。「3 と 7 だけ採用」と言える形。**高確度候補 (手順 3) も本表に載せ**、「セッション内提案済み (承認 / 見送り / 保留)」の結果を付記する — レポートは監査証跡として単体で完結させる

schema を埋めた形の記入例が要るときは [references/report-example.md](references/report-example.md) を Read する (bucket 別 section / informational / summary 表の書き方)。

**適用手順の単位別分岐** (レポートに埋め込む):

| 対象 | 適用手順 |
|---|---|
| swat-skills 本体 / third | swat-skills repo の運用に従って反映 |
| 他 plugin | `/plugin` 操作 (uninstall / disable) — 人間が該当 marketplace 設定で実施 |
| MCP server (ローカル config) | `~/.claude.json` の `mcpServers` 該当 entry を削除、または `.mcp.json` を編集 |
| claude.ai connectors | claude.ai 側の設定画面で disconnect — 人間が実施 |

### 5. 人間判定 (確度で扱いが分かれる)

- **高確度候補**: 手順 3 で AskUserQuestion 承認済みのものは同セッション内で適用まで進んでよい (手順 4 の単位別分岐に従う)。承認なしの適用・無人 commit は行わない
- **低確度候補**: レポートを提示するところで skill の責務は終わる。承認 → 適用は次のセッションで人間が実施する
