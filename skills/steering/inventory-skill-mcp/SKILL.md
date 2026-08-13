---
name: inventory-skill-mcp
disable-model-invocation: true
description: 全 plugin skill + personal skill + project skill + MCP (claude.ai connectors 含む) の直近 30 日 invocation を transcript から決定的に集計し、単位別 (skill / MCP tool / MCP server / plugin) の削除/見直し/保持候補を LLM 具体化まで運ぶ棚卸し。決定的ルールは tool が評価し (bucket_candidate / rule_fired / open_predicates)、LLM は open_predicates だけを判断する。高確度候補はセッション内で AskUserQuestion 提案し承認後そのまま適用、低確度候補はレポート提示。判定は人間。汎用スキル制約 (Claude Code 標準ファイルのみ参照) で動く。Use when「skill 棚卸し」「MCP 棚卸し」「skill を削除したい」「MCP server を disconnect したい」「plugin を uninstall したい」「棚卸し」「inventory-skill-mcp」.
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

- `rule_candidates` が機械判定済みの候補。**`open_predicates` に挙がった条件だけを判断し**、満たすと判断したものだけ `bucket_candidate` を bucket として確定する
- `near_misses` は「あと 1 条件」で外れた unit と落選理由。**閾値・分母・巻き込みの当否を疑う証拠はここにしか出ない** — レポートの informational に転記する
- `units` は invocation を 1 件以上持つ unit だけ。count 0 の unit は `denominators` と `rule_candidates` にしか現れない

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
- `denominator_completeness`: **実行中セッションの available skill / MCP 一覧を思い出し**、mart の分母に無いが存在するものを `session-observed` タグ付きで report に載せる (claude.ai connectors / built-in skill が典型)。**判断ではなく転記**なので原則と矛盾しない
- `removable_independently`: 第三者 plugin 同梱の skill は `/plugin` 操作でしか触れない。単体で外せるかを手順 4 の分岐表と突き合わせて判断する

**「推測:」prefix 分離の義務**: mart の証拠 (count / share / rank / 抜粋の user_prompt / tool_input / outcome) に紐づく記述には prefix を付けない。証拠に紐づかない一般論には `推測: ` prefix を付ける。証拠ゼロで根拠を書かない選択肢もある — 埋めるために推測で埋めない。

**channels 内訳と coverage の使い方** (思想系 skill の判定を歪めないための補正):

- 「session 開始で 1 度 load → 以降 session 全体で暗黙適用」型の skill (`coding-principles` / `engineering-judgment` / `test-strategy` / `pr-quality` 等) は count 単独では実適用回数を過小評価する。`units[skill][].channels.command + .read > 0` の unit は「1 session 1 load 型」の可能性が高いと解釈する
- coverage を評価するときは `sessions[]` を絞り込む: コード編集の思想系なら `has_code_edit == true` を分母、その中で loaded_skills に対象 skill を含む session を分子とする。設計議論系なら `has_plan_mode == true` の session を分母とする
- **どの skill を「思想系」とするかは LLM 判断**。tool は語彙を持たない (skill 名の一覧を tool に持たせると新設 skill を取りこぼす)
- **coverage を出す前に「誘導機構の稼働開始日」と観測窓を突き合わせる**。invoke を促す機構 (SessionStart 注入 hook 等) が観測窓の途中で導入されていると、既定の 30 日窓は導入前後を混ぜて coverage を過小評価する。機構の導入日は repo の `git log` で確認し、ずれていれば `days` を稼働期間に合わせて再スキャンしてから判定する。狭めた窓は母数が落ちるので、割合ではなく **分子/分母を n 付きで併記**する

**token 経済 (`usage`) の使い方**: `usage.by_skill` は「呼ばれているが重い skill」を count と独立に見るための軸。count が低くても `output_tokens` / `cache_creation_input_tokens` が突出する unit は `review-candidate` の根拠になる (削除ではなく**縮小**の候補)。`by_skill` が帰属 turn だけの下限である点は mart の `contract.notes` が正本。

標準フロー外の追加検査が要るときは `mcp__plugin_swat-skills_transcript-ops__query` に read-only SQL を投げる (単発に留める)。

### 3. 高確度候補の抽出とセッション内提案

bucket 確定後、以下をすべて満たす unit だけを高確度候補として抽出する:

1. `rule_fired` に `unused_skill` / `unused_mcp_server` / `unused_plugin` のいずれかが入っている (機械判定可能な条件は tool が確認済み)
2. `open_predicates` の全条件を**決定的観測だけで**満たすと判断できた (`推測:` prefix を要しない)
3. 適用手順が単一の既定分岐で完結する

高確度候補は候補ごとに証拠 1-2 行 (`rule_inputs` の count / sessions_presented / 分母 source) + 適用手順 (手順 4 の単位別分岐表) を添えて **AskUserQuestion で選択肢を提示する** (例:「obsidian plugin は 30 日 0 invocation (skill 3/3 未使用・同梱 MCP も 0)。uninstall しますか?」。選択肢は「適用する / 見送る (レポート記載のみ) / 保留」相当)。

- **承認されたらそのまま同セッション内で適用に進む**。適用は単位別分岐に従う: swat-skills 本体 / third 分は worktree + PR (通常フロー)、他 plugin の `/plugin` 操作・MCP config 編集・claude.ai connectors disconnect 等の人間側操作は具体的手順を提示して受け渡す
- 却下・保留された候補、および 3 条件を満たさない候補はすべて手順 4 のレポートへ回す

### 4. Markdown レポート組み立て

`/tmp/inventory-skill-mcp/report-<timestamp>.md` に mart と並置で書く。以下の**固定 schema**:

1. **ヘッダ**: 観測窓 / 総 invocation 数 / distinct sessions / 判定可能性 (`sufficient_for_relative_judgment`)
2. **候補 section** (bucket 別に列挙):
    - 対象 (unit id) / 単位 (skill / MCP server / plugin)
    - count / share / rank / percentile
    - bucket (上表の語彙) と、`rule_fired` / `open_predicates` の判断結果
    - 証拠 max 3 件 (mart から転記。session_id + timestamp anchor 付き)
    - 提案 (1-2 行、証拠に紐づく)
    - 適用手順 (単位別分岐、下表参照)
3. **informational**: MCP tool 表 (server 判断の材料)、`near_misses` (落選理由つき)、`open_predicates` を満たさないと判断した候補
4. **summary 表**: 番号 × bucket × 対象。「3 と 7 だけ採用」と言える形。**高確度候補 (手順 3) も本表に載せ**、「セッション内提案済み (承認 / 見送り / 保留)」の結果を付記する — レポートは監査証跡として単体で完結させる

**適用手順の単位別分岐** (レポートに埋め込む):

| 対象 | 適用手順 |
|---|---|
| swat-skills 本体 / third | worktree + PR (通常フロー) |
| 他 plugin | `/plugin` 操作 (uninstall / disable) — 人間が該当 marketplace 設定で実施 |
| MCP server (ローカル config) | `~/.claude.json` の `mcpServers` 該当 entry を削除、または `.mcp.json` を編集 |
| claude.ai connectors | claude.ai 側の設定画面で disconnect — 人間が実施 |

### 5. 人間判定 (確度で扱いが分かれる)

- **高確度候補**: 手順 3 で AskUserQuestion 承認済みのものは同セッション内で適用まで進んでよい (worktree + PR / 人間側操作の単位別分岐に従う)。承認なしの適用・無人 commit は行わない
- **低確度候補**: レポートを提示するところで skill の責務は終わる。承認 → 適用は次のセッション (or 別の worktree) で人間が実施する

## 出力例 (概略)

```
# skill/MCP 棚卸しレポート

観測窓: 2026-06-17 〜 2026-07-17 (30 日) / 総 invocation 245 / distinct sessions 42
判定可能性: sufficient (総数 >= 30)

## delete-candidate

### 1. <plugin-a>:<skill-x>
- rule_fired: unused_skill / count 0 / sessions_presented 38 / 分母 source config
- open_predicates: rename_or_removal_in_window ✗ (git log に改名なし) /
  denominator_completeness ○ / removable_independently ○ (`/plugin` で個別 disable 可)
- bucket: delete-candidate
- 適用手順: `/plugin` 操作で該当 plugin から...

## review-candidate

### 2. swat-skills:apm-skill-add
- count 3 / share 1.2% / rank 22 / percentile 0.72 / outcomes {success: 2, error: 1}
- 証拠: (session 069b... 2026-07-15 ...)
- 推測: apm 経由の vendoring は特定局面でしか使わないため、trigger 語彙の妥当性を...

## informational — near_misses

- <plugin-b>:<skill-y> : unused_skill の presented_at_least_once を外した
  (sessions_presented 0 = 提示されていない。不使用の証拠にならない)

## summary

| # | bucket | 対象 | 単位 | セッション内提案 |
|---|---|---|---|---|
| 1 | delete-candidate | <plugin-a>:<skill-x> | skill | 提案済み (承認 → 適用) |
| 2 | review-candidate | swat-skills:apm-skill-add | skill | - (低確度) |
```

## 責務

- 削除/見直し/保持の**判定は常に人間** (3 段階モデル不変)
- **決定的ルールは tool 側**。LLM が判断するのは `open_predicates` に挙がった条件だけで、機械判定可能な条件を再導出しない
- **高確度候補** (手順 3): セッション内 AskUserQuestion 提案 → 人間承認 → 同セッション内で適用まで進んでよい。適用形は単位別分岐に従う。無人 commit は行わない
- **低確度候補**: 観測 → 候補提示 → 適用手順の明示までで止まる
- SKILL.md 本文の診断 (trigger 語彙見直し・記述強化) は本 skill の観測範囲外 — 別 skill の領分

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| user-reject が error に混ざる | `is_error=true` の tool_result content から user-reject 文言を best-effort で判定するが、Claude Code の [#29499](https://github.com/anthropics/claude-code/issues/29499) の false positive バグを完全に無効化できない | outcome breakdown を「参考値」として扱い、bucket 判定の主根拠にしない (count が主根拠) |
| outcome が unknown | `toolUseResult` も `is_error` も無い tool_result。判定不能を success に丸めない設計 | unknown は「観測できなかった」であり失敗ではない。bucket 判定は success / error の実数で行う |
| claude.ai connectors が分母に出ない | ローカル config に出現しない | 手順 2 の `denominator_completeness` で LLM が `session-observed` タグ付きで転記 |
| 他 project scoped の skill/MCP が分母不明 | 現 project 以外の `.claude/skills` / `.mcp.json` は tool が読まない | `denominator-unknown` として報告し、棚卸しを project ごとに回す運用でカバー |
| 総 invocation が閾値未満 | 新規 install / 長期休止後の初回等 | 全 unit を `insufficient-data` としてヘッダ宣言、informational 提示のみ (rule も `relative_judgment_available` で外れる) |
| 思想系 skill の coverage が異常に低い | 観測窓が invoke 誘導機構 (SessionStart 注入 hook 等) の導入日をまたぎ、導入前の期間が分母を膨らませている | 機構の導入日を `git log` で確認し `days` を稼働期間に合わせて再スキャンして比較する。両方の窓の値を n 付きで併記し、旧窓の値は破棄しない |
| 実体の無い skill id が「参照元に旧名が残っている」ように見える | 観測窓が skill の rename / 削除日をまたぎ、旧名での**正常だった**呼び出しが窓内に残っているだけ | 手順 2 の `rename_or_removal_in_window` を判断する |
| 抜粋の user_prompt が空 | tool_use が assistant turn 開始直後で先行 user turn が meta tag のみ | 空文字を許容。抜粋 anchor (session_id + timestamp) で生 transcript を読めば復元可 |
| Skill 呼出が二重カウントに見える | queue-operation record と user turn record への二重記録は本 tool では**発生しない** (session id + tool_use.id で dedupe 済み) | 単位 (count) は tool_use.id 単一化済み。session 内複数呼び出しは正しく累計される |
| bucket 判定を tool に確定させたくなる | 「候補まで出せるなら bucket も出せる」と感じる | 意味判断 (思想系か / rare-by-design か) の機械化には skill 名の allowlist が要り、新設 skill を取りこぼす ([ADR 0032](https://github.com/swat9013/swat-skills/blob/main/docs/adr/0032-policy-free-refinement-deterministic-rules.md) が却下した形) |
| 提示すらされていない unit を delete-candidate にする | `rule_fired` を確認せず count 0 だけで判断した | `sessions_presented == 0` の unit は rule が発火せず `near_misses` に落ちる。delete の根拠にしない |
| 重い skill を token だけで削除候補にする | `usage.by_skill` を実消費の総量と読んだ | by_skill は帰属 turn だけの下限。重さは削除でなく**縮小** (review-candidate) の根拠として扱う |
| 高確度基準を満たさない候補をセッション内提案したくなる | 「count 1 だしほぼ確実」等の緩和誘惑 | 手順 3 の 3 条件を満たさないものは必ずレポート側に落とす (基準の緩和は rule 実装か本 SKILL.md の改訂として行う) |

## 参照

- 関連 skill (相互参照はしない): skill-usage-audit (SKILL.md の実装と実挙動の乖離を監査) は別軸の監査。棚卸しは「使われているか」、audit は「書かれた仕様どおり動いたか」
