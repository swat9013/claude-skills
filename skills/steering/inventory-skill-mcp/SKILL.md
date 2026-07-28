---
name: inventory-skill-mcp
disable-model-invocation: true
description: 全 plugin skill + personal skill + project skill + MCP (claude.ai connectors 含む) の直近 30 日 invocation を transcript から決定的に集計し、単位別 (skill / MCP tool / MCP server / plugin) の削除/見直し/保持候補を LLM 具体化まで運ぶ棚卸し。高確度候補 (4 条件) はセッション内で AskUserQuestion 提案し承認後そのまま適用、低確度候補はレポート提示。判定は人間。汎用スキル制約 (Claude Code 標準ファイルのみ参照) で動く。Use when「skill 棚卸し」「MCP 棚卸し」「skill を削除したい」「MCP server を disconnect したい」「plugin を uninstall したい」「棚卸し」「inventory-skill-mcp」.
---

# inventory-skill-mcp

install 済みの skill / MCP を **transcript の tool_use 実績**と突合し、単位別に削除候補 / 見直し候補 / 保持を LLM に**候補提示までさせて**、判定は人間の判断に残す棚卸し skill。

3 段階モデル (原則: **観測・集計は決定的に、判断は人間に、LLM は文章の具体化のみ**):

1. **決定的観測**: `scripts/scan-invocations.py` が transcript walk + 分母列挙 + 抜粋 sampling を実行し、bucket を知らない生の mart JSON を出力する
2. **LLM 具体化 (このメインコンテキスト)**: mart JSON を読み、単位別に bucket 候補と根拠 1-2 行、および証拠 anchor 付き Markdown レポートを組み立てる。**判定はしない**
3. **人間判定**: 削除/見直し/保持を選ぶのは常に人間。提示は確度で 2 層に分ける — **高確度候補** (手順 3 の 4 条件) はセッション内で AskUserQuestion 提案し、承認されたら同セッション内で適用に進む。**低確度候補**はレポート提示で止まる。適用の実施形は単位別分岐に従う (swat-skills 分は worktree + PR、他 plugin は `/plugin` 操作、MCP は config 編集、claude.ai connectors は claude.ai 側)

3 段階モデルの原則は 2 層化後も**不変** — 変わるのは判定後の適用タイミングと提示 UI のみ。無人 commit は引き続き行わない (適用は必ず AskUserQuestion での人間承認を経る)。

汎用スキル制約: 参照するのは Claude Code 標準ファイルのみ (`~/.claude/projects/` / `~/.claude/plugins/installed_plugins.json` / 各 plugin の `.claude-plugin/plugin.json` / `~/.claude/skills/` / 現 repo `.claude/skills/` / `~/.claude.json` / 現 repo `.mcp.json`)。swat-skills 固有 hook 資産 (tool-signatures.jsonl 等) には触らない。

## 手順

### 1. 観測 script 起動 (決定的)

```
${CLAUDE_SKILL_DIR}/scripts/scan-invocations.py
```

- 引数なしで直近 30 日 (`--days 30`) を集計する。stdout に mart JSON path (`/tmp/inventory-skill-mcp/mart-<timestamp>.json`) が出る
- `--days N` で観測窓を上書き。`--repo-root PATH` で分母源 project を切替 (省略時 cwd)
- script は **bucket を出さない** (循環依存の回避)。sort 済みの count / share / rank / percentile / outcome breakdown / 抜粋 max 3 件 + 分母 (`config` / `session-observed` タグ付き) を出す
- 想定所要時間は 1.2 GB / 224 project dir で数十秒 (mtime filter + 線形 1 pass)
- **skill unit の count は 3 channel の合算**: `channels: {skill_tool, command, read}` の内訳が同 unit の下に付く (`skill_tool` = Skill tool_use / `command` = `<command-name>/xxx</command-name>` slash / `read` = SKILL.md path への Read tool_use)。built-in slash (`/model` `/compact` `/clear` 等) と非 SKILL.md path への Read は count 対象外
- **session-level 生データ**: mart 末尾に `sessions: [{session_id, loaded_skills, has_code_edit, has_plan_mode}]` が出る (skill load を 1 度以上持つ session のみ)。coverage 判定の材料 (どの skill を「思想系」として扱うかは LLM 段階で決める。script は判定語彙を持たない)

出力に失敗したら (transcripts_dir が無い / config が壊れている等) 空 mart が出るので `meta.total_invocations` を確認する。0 なら「観測不能」を報告して終了。

### 2. mart JSON を読んで単位別に bucket 候補を提示 (LLM)

mart の schema は script docstring 参照。上位から順に各 unit を評価する。

**単位別 bucket vocabulary** (script はこの語彙を知らない。ここで初めて割り当てる):

| 単位 | bucket |
|---|---|
| skill | `delete-candidate` / `review-candidate` / `keep` / `insufficient-data` |
| MCP server | `disconnect-candidate` / `keep` / `insufficient-data` |
| MCP tool | informational のみ (個別 on/off は Claude Code に無い、server 判断の材料) |
| plugin | `uninstall-candidate` / `keep` / `insufficient-data` |

**判定可能性の分岐**: `distribution.sufficient_for_relative_judgment == false` (mart header) なら**全 unit を `insufficient-data`** としてレポートヘッダで宣言し、以下は informational として並べる。個別 0-1 件でも全体母数が十分なら `delete-candidate` として提示可。`review-candidate` の見直し方向 (広げる/狭める) はここでは決めない — SKILL.md 本文の診断は本 skill の観測範囲外。

**「推測:」prefix 分離の義務**: mart の証拠 (count / share / rank / 抜粋の user_prompt / tool_input / outcome) に紐づく記述には prefix を付けない。証拠に紐づかない一般論 (「この skill は typically ...」等) は `推測: ` prefix を付ける。証拠ゼロで根拠を書かない選択肢もある — 埋めるために推測で埋めない。

**分母補完 (`session-observed` タグ) の使い方**: mart の `denominators.skills[].source` が `config` でなく `session-observed` の unit は、ローカル config に出ないが実行時 session には現れたもの (claude.ai connectors / built-in skill が典型)。**LLM 段階でここに追加補完**する — 実行中セッションの available skill / MCP 一覧を思い出し、mart の分母に無いが存在するものを `session-observed` タグ (「LLM による転記」の意味) で report に載せる。**判断ではなく転記**なので原則と矛盾しない (script が config を読めない領域を補うだけ)。

**channels 内訳と coverage の使い方** (思想系 skill の判定を歪めないための補正):

- 「session 開始で 1 度 load → 以降 session 全体で暗黙適用」型の skill (`coding-principles` / `engineering-judgment` / `test-strategy` / `pr-quality` 等) は count 単独では実適用回数を過小評価する。`units[skill][].channels.command + .read > 0` の unit は「1 session 1 load 型」の可能性が高いと解釈する
- coverage を評価するときは `sessions[]` を絞り込む: コード編集の思想系なら `has_code_edit == true` を分母、その中で loaded_skills に対象 skill を含む session を分子とする。設計議論系 (engineering-judgment 等) なら `has_plan_mode == true` あるいは brainstorming skill を loaded_skills に含む session を分母とする
- どの skill を「思想系」とし coverage を評価するかは LLM 判断。script は語彙を持たない (bucket 判定を script に埋めないのと同型の circular 回避)
- **coverage を出す前に「誘導機構の稼働開始日」と観測窓を突き合わせる**。invoke を促す機構 (SessionStart 注入 hook 等) が観測窓の途中で導入されていると、既定の 30 日窓は導入前後を混ぜて coverage を過小評価する。機構の導入日は repo の `git log` で確認し、ずれていれば `--days N` で稼働期間に合わせて再スキャンしてから判定する。狭めた窓は母数が落ちるので、割合ではなく **分子/分母を n 付きで併記**する (実例: `hooks/harness/inject-skill-guide.py` の導入は 2026-07-24。30 日窓では `coding-principles` の coverage 5.2% (13/248) だが、導入後 4 日窓で再スキャンすると 42.1% (8/19) で、注入は効いていた)

### 3. 高確度候補の抽出とセッション内提案

bucket 候補割り当て後、以下の **4 条件をすべて満たす** unit だけを高確度候補として抽出する:

1. mart header の `distribution.sufficient_for_relative_judgment == true`
2. 該当 unit の count 0 (観測窓内 invocation ゼロ)
3. 分母 source が `config` (session-observed 補完でなく config から確実に列挙されたもの)
4. 依存関係の巻き込みなし (例: plugin の skill が 3/3 未使用でも、同 plugin の MCP tool が使われているケース〈claude-mem が典型〉は除外。plugin 単位なら配下 skill と MCP 両方が count 0 のときのみ)

高確度候補は候補ごとに証拠 1-2 行 (count / 観測窓 / 分母 source) + 適用手順 (手順 4 の単位別分岐表) を添えて **AskUserQuestion で選択肢を提示する** (例:「obsidian plugin は 30 日 0 invocation (skill 3/3 未使用)。uninstall しますか?」。選択肢は「適用する / 見送る (レポート記載のみ) / 保留」相当)。

- **承認されたらそのまま同セッション内で適用に進む**。適用は単位別分岐に従う: swat-skills 本体 / third 分は worktree + PR (通常フロー)、他 plugin の `/plugin` 操作・MCP config 編集・claude.ai connectors disconnect 等の人間側操作は具体的手順を提示して受け渡す
- 却下・保留された候補はレポートの従来 bucket section に残す
- 4 条件を満たさない候補 (review-candidate / insufficient-data / informational を含む) はすべて手順 4 のレポートへ回す

### 4. Markdown レポート組み立て

`/tmp/inventory-skill-mcp/report-<timestamp>.md` に mart と並置で書く。以下の**固定 schema**:

1. **ヘッダ**: 観測窓 / 総 invocation 数 / distinct sessions / 判定可能性 (`sufficient_for_relative_judgment`)
2. **候補 section** (bucket 別に列挙):
    - 対象 (unit id) / 単位 (skill / MCP server / plugin)
    - count / share / rank / percentile
    - bucket 候補 (上表の語彙)
    - 証拠 max 3 件 (mart から転記。session_id + timestamp anchor 付き)
    - 提案 (1-2 行、証拠に紐づく)
    - 適用手順 (単位別分岐、下表参照)
3. **informational**: MCP tool 表 (server 判断の材料。個別 bucket は出さない)
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

### 1. superpowers:handoff
- count 0 / share 0% / rank 47 / percentile 0.0
- bucket: delete-candidate
- 証拠: なし (0 呼び出し)
- 提案: 30 日で 1 度も呼ばれていない。install 除去を検討
- 適用手順: `/plugin` 操作で superpowers plugin から skills 配列を...

## review-candidate

### 2. swat-skills:apm-skill-add
- count 3 / share 1.2% / rank 22 / percentile 0.72
- outcomes: {success: 2, error: 1}
- 証拠: (session 069b... 2026-07-15 ...)
- 推測: apm 経由の vendoring は特定局面でしか使わないため、trigger 語彙の妥当性を...

## summary

| # | bucket | 対象 | 単位 | セッション内提案 |
|---|---|---|---|---|
| 1 | delete-candidate | superpowers:handoff | skill | 提案済み (承認 → 適用) |
| 2 | review-candidate | swat-skills:apm-skill-add | skill | - (低確度) |
| ... |
```

## 責務

- 削除/見直し/保持の**判定は常に人間** (3 段階モデル不変)
- **高確度候補** (手順 3 の 4 条件): セッション内 AskUserQuestion 提案 → 人間承認 → 同セッション内で適用まで進んでよい。適用形は単位別分岐に従う (swat-skills 分は worktree + PR、他は人間側操作の手順提示)。無人 commit は行わない
- **低確度候補**: 観測 → 候補提示 → 適用手順の明示までで止まる。適用はレポート提示外の作業として skill から切り離し、人間が該当 side で実施
- SKILL.md 本文の診断 (trigger 語彙見直し・記述強化) は本 skill の観測範囲外 — 別 skill (skill-usage-audit 等) の領分

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| user-reject が error に混ざる | `is_error=true` の tool_result content から user-reject 文言を best-effort で判定するが、Claude Code の [#29499](https://github.com/anthropics/claude-code/issues/29499) の false positive バグを完全に無効化できない | outcome breakdown を「参考値」として扱い、bucket 判定の主根拠にしない (count が主根拠) |
| claude.ai connectors が分母に出ない | ローカル config に出現しない | 手順 2 の「分母補完」で LLM が `session-observed` タグ付きで転記 |
| 他 project scoped の skill/MCP が分母不明 | 現 project 以外の `.claude/skills` / `.mcp.json` は script が読まない | `denominator-unknown` として報告し、棚卸しを project ごとに回す運用でカバー |
| 総 invocation が閾値未満 | 新規 install / 長期休止後の初回等 | 全 unit を `insufficient-data` としてヘッダ宣言、informational 提示のみ |
| 思想系 skill の coverage が異常に低い | 観測窓が invoke 誘導機構 (SessionStart 注入 hook 等) の導入日をまたぎ、導入前の期間が分母を膨らませている | 機構の導入日を `git log` で確認し `--days N` で稼働期間に再スキャンして比較する。両方の窓の値を n 付きで併記し、旧窓の値は破棄しない |
| 実体の無い skill id が「参照元に旧名が残っている」ように見える | 観測窓が skill の rename / 削除日をまたぎ、旧名での**正常だった**呼び出しが窓内に残っているだけ | rename / 削除の commit 日 (`git log --diff-filter=D`) と invocation の timestamp を突き合わせる。invocation が rename 前なら参照元の修正は不要 (ADR / plan / 変更履歴の旧名は記録であり書き換えない) |
| 抜粋の user_prompt が空 | tool_use が assistant turn 開始直後で先行 user turn が meta tag のみ | 空文字を許容。抜粋 anchor (session_id + timestamp) で生 transcript を読めば復元可 |
| Skill 呼出が二重カウントに見える | queue-operation record と user turn record への二重記録は本 script では**発生しない** (session id + tool_use.id で dedupe されている) | 単位 (count) は tool_use.id 単一化済み。session 内複数呼び出しは正しく累計される |
| bucket を script に埋め込みたくなる | 循環依存 (script が bucket を知ると LLM が再判定できなくなる) | script は生集計のみ。bucket 判定は LLM 段階で mart を読んでから |
| 高確度基準を満たさない候補をセッション内提案したくなる | 「count 1 だしほぼ確実」等の緩和誘惑 | 手順 3 の 4 条件を満たさないものは必ずレポート側に落とす (基準の緩和は本 SKILL.md の改訂として行う) |

## 参照

- 仕様確定: [issue #215 コメント](https://github.com/swat9013/swat-skills/issues/215#issuecomment-4998006482)
- 上位 map: [issue #209](https://github.com/swat9013/swat-skills/issues/209)
- 関連 skill (相互参照はしない): skill-usage-audit (SKILL.md の実装と実挙動の乖離を監査) は別軸の監査。棚卸しは「使われているか」、audit は「書かれた仕様どおり動いたか」
