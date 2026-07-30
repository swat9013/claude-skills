---
name: inventory-permissions
disable-model-invocation: true
description: Claude Code の permission (allow/deny/ask) / sandbox / guard hook を transcript の tool_use 実績と突合し、両軸集計 (設定 pattern × 実績) と bypass 系列を単位別に 5 bucket (revoke / promote / refine / sandbox / keep) の候補提示まで LLM に運ばせる棚卸し。高確度候補 (revoke 限定・4 条件) はセッション内で AskUserQuestion 提案し承認後そのまま適用、低確度候補はレポート提示。判定は人間。汎用スキル制約 (Claude Code 標準ファイルのみ参照) で動く。Use when「permission 棚卸し」「settings.json の allow を見直したい」「未使用 allow を削りたい」「hook / sandbox の切り分け」「bypass 系列を確認」「棚卸し」「inventory-permissions」.
---

# inventory-permissions

Claude Code の permission 3 層 — **permission** (正規表現ベースの一律許可) / **hook** (細粒度の条件判断) / **sandbox** (行動範囲の線引き) — を、`~/.claude/projects/**/*.jsonl` の tool_use 実績と突合し、単位別に **5 bucket (revoke / promote / refine / sandbox / keep)** の候補を LLM に**候補提示までさせて**、判定は人間の判断に残す棚卸し skill。

3 段階モデル (原則: **観測・集計は決定的に、判断は人間に、LLM は文章の具体化のみ**):

1. **決定的観測**: `scripts/scan-permissions.py` が transcript を **stateless 全走査**し、両軸集計 + bypass 系列 + guard 逆引きを含む mart JSON を出力する
2. **LLM 具体化 (このメインコンテキスト)**: mart JSON を読み、単位別に bucket 候補と根拠、具体 entry 案 (hook は要件文まで) を組み立てる。**判定はしない**
3. **人間判定**: 削除/昇格/絞り込み/保持を選ぶのは常に人間。提示は確度で 2 層に分ける — **高確度候補** (手順 3 の 4 条件、revoke 限定) はセッション内で AskUserQuestion 提案し、承認されたら同セッション内で適用に進む (project scope は worktree + PR、global scope は人間側操作)。**低確度候補**はレポート提示で止まる

3 段階モデルの原則は 2 層化後も**不変** — 変わるのは判定後の適用タイミングと提示 UI のみ。無人 commit は引き続き行わない (適用は必ず AskUserQuestion での人間承認を経る)。

**汎用スキル制約**: 参照するのは Claude Code 標準ファイルのみ (`~/.claude/projects/` / `~/.claude/settings.json` / `<repo>/.claude/settings.json(.local)`)。dotfiles / swat-skills 固有 hook 資産 (tool-signatures.jsonl 等) には触らない。global 変更の反映先 (chezmoi 等) は各 repo 側の指示文の責務。

## 引数

- 省略時 → `project` section (cwd の `.claude/settings.json(.local)` × 当該 repo 実績)
- `global` → `~/.claude/settings.json` × 全 repo 実績 (1.2 GB 級走査)
- `all` → 両方

## 手順

### 0. 前提: matcher 実装の確認 (best-effort)

Claude Code 本体の permission matcher 実装を確認できれば `sample_matched` を厳密化できるが、確認できなくても本 skill は動く。実装冒頭で以下を試み、確認できたら「matcher_confidence を `exact` に昇格して報告」を宣言する。確認できなくても続行 — ブロッカーにしない。

- 手元の Claude Code パッケージ内 permission 判定コードの探索 (grep)
- 公式 docs (`https://code.claude.com/docs/ja/`) の permission matcher 記述
- 実測 (deny 済み entry で似た pattern を作って発火試験)

いずれも空振りしたら本 skill は保守的近似のまま進む (`exact_tool` / `exact_command` / `prefix` を `exact`、`glob` を `approx` として提示)。

### 1. 観測 script 起動 (決定的)

```
${CLAUDE_SKILL_DIR}/scripts/scan-permissions.py --section <引数>
```

- `--section` 省略時 `project`。`global` / `all` は上表の通り
- 直近 30 日 (`--days 30`) を集計 — 変更したければ `--days N`
- stdout に **読む順の分割ファイル path 一覧** (`/tmp/inventory-permissions/run-<timestamp>/` 配下) が出る。ファイルの読む順・用途は `00-meta.json` の `contract.files` に従う
- **script は bucket を出さない** (循環依存の回避)。sort 済みの match_count / outcome_breakdown / sample_matched / bypass 系列 / guard 逆引きを出す
- 想定所要時間: project section 数秒 / global section 数十秒〜数分 (1.2 GB / 数百 project dir を線形 1 pass)

出力に失敗したら (transcripts_dir が無い / settings が壊れている等) 空 mart が出るので `00-meta.json` の `meta.total_events` を確認する。0 なら「観測不能」を報告して終了。

### 2. 分割ファイルを読む順に Read して bucket 候補を提示 (LLM)

ファイルの読む順・用途・derived view の意味論は `00-meta.json` の `contract` (`files` / `views`) に従う (schema の正本は script 発の contract 一本)。**script の stdout が列挙した順にファイルを Read するだけで標準フローが完結する** (jq / inline python 不要)。読みながら bucket 候補に落とすときの視点:

- `meta.total_events` で判定可能性を分岐する (下記「判定可能性の分岐」)
- `20-axis-a.json` が keep を含む全 entry の母集団。bucket 割当てはここを起点にする
- `30-bypass-samples.json` の代表系列は `refine` の証拠としてレポートに転記する

`standard_flow: false` の `90-mart.json` は全量 (bypass_sequences / axis_b_actual_usage 含む) — 標準フローでは読まない。想定外の追加検査にだけ jq で単発参照し、恒常的に必要になった集計は script の分割出力拡張として提案する (inline python は環境によって hook で禁止される)。

**判定可能性の分岐**: `meta.sufficient_for_relative_judgment == false` (総 event < 30) なら**全単位を `insufficient-data`** としてレポートヘッダで宣言し、以下は informational として並べる。個別 0 件でも全体母数が十分なら `revoke` として提示可。

**bucket vocabulary** (script はこの語彙を知らない。ここで初めて割り当てる):

| bucket | 証拠源 | 具体度 |
|---|---|---|
| **revoke** | `axis_a_pattern_matches` で `match_count == 0` の allow / ask entry (観測窓内) | コピペ可能な削除対象 entry を提示 |
| **promote** | `axis_b_actual_usage` で `config_matches == []` かつ success 頻発 / ask に対する success 頻発 | 追加すべき allow entry を提示 (例: `Bash(gh pr view:*)`) |
| **refine** | `axis_a` で match_count 巨大 & deny_permission-rule/deny_automode 混在 / `bypass_sequences` に該当系列 | 分割 entry 案 (広い pattern を絞る、または hook に移す要件文) |
| **sandbox** | 到達範囲を制限すべき系列 (例: shell が広く許可されているが実行内容は限定的) | `sandbox.excludedCommands` の具体 entry 案 or hook 要件 |
| **keep** | `axis_a` で match_count > 0 かつ deny 少数 / 明示的に整合が取れている | 「保持」を明示的に記録 (次回の revoke 誤判定を防ぐ) |

**bucket 割当ての制約 (script との責務分担)**:
- LLM は上表の**証拠源**に沿って割り当てる。証拠に紐づかない bucket 割当てはしない
- bypass 系列は独立 bucket にしない — `refine` の証拠として扱う
- guard 逆引き (`guard_reverse_lookup`) は refine / sandbox の証拠として使う (実装 hook の追加要件を書く)
- hook の実装案は**要件文まで** (script は生成しない、実装しない)
- deny / allow / sandbox は**コピペ可能な具体 entry 案**まで書く

**revoke の絞り込み (match_count == 0 だけでは revoke にしない)**:
- revoke に出すのは「未使用 **かつ** 副作用能力あり (remote 書き込み / process 起動 / 破壊的操作等)」の entry のみ。read-only で無害な未使用 entry は削除しても attack surface が減らず将来の permission ask 反復コストだけ増える — keep 側に「未使用だが無害」と明示する
- 観測窓内に追加された entry / rare-by-design の entry (setup script・手動同期 script 等) は露出不足 — informational に **hold** として分離する。settings が git 管理下なら `git log -S '<entry>'` で追加時期を確認する手順をレポートに書く
- 同一 script の複数呼び出し形 (直接実行 / `python3 <path>` prefix 等) は **1 unit** として扱い、片側だけの revoke を提示しない。repo 側 README / commit 履歴に pair 規約の意図が残っていないか確認する
- allow entry と対になる `sandbox.excludedCommands` entry があれば、連動削除の要否を候補に明記する

**promote の絞り込み (axis_b の全 unit を候補にしない)**:
- permission entry でゲートされない built-in tool (Read / Edit / Write / Task* / Agent 等) の unit は promote 対象外 — `derived_views.axis_b_unlisted_frequent` は Bash / `mcp__*` / deny_permission-rule 実績ありに絞り済み (除外数は `omitted_non_permission_units`)
- built-in tool で deny_permission-rule 実績を持つ unit は path 系 deny rule の反射の可能性 — promote ではなく refine の証拠として扱う

**確度注記の義務**:
- `matcher_confidence: approx` の entry は「近似マッチ (glob) — 実 matcher と揺れる可能性」を **entry 表示に注記**する
- outcome の `deny_user-rejected` は Claude Code の [#29499](https://github.com/anthropics/claude-code/issues/29499) の false positive バグ影響下 — bucket 判定の**主根拠にしない** (count が主根拠)
- `guard_reverse_lookup` に hook-deny (Claude Code の PreToolUse permissionDecision: deny) は原則含まれない (現状 `toolDenialKind` に emit されない)。automode-blocked / automode-unavailable のみを対象とする — 「hook 由来の deny は本 skill の観測範囲外」と明記する

### 3. 高確度候補の抽出とセッション内提案

bucket 候補割り当て後、以下の **共通 4 条件をすべて満たす** 候補だけを高確度として抽出する:

1. 判定材料が決定的観測のみで完結する (`推測:` prefix を要しない)
2. count 0 / 完全一致など、閾値解釈の余地がない証拠
3. 巻き込み・連動なし (依存 / pair 規約 / sandbox 連動 / 複数行 section への波及なし)
4. 適用手順が単一の既定分岐で完結する

本 skill での具体化は **`revoke` bucket 限定** (promote / refine / sandbox / keep は entry 設計の判断を伴うため常にレポート側)。revoke のうち以下をすべて満たす entry のみ:

- `meta.sufficient_for_relative_judgment == true` かつ `match_count == 0`
- `matcher_confidence: exact` (approx は除外 — 実 matcher と揺れる余地がある)
- 手順 2 の revoke 絞り込み (「未使用 かつ 副作用能力あり」) を満たし、hold 対象 (窓内追加・rare-by-design) でない
- pair 規約・`sandbox.excludedCommands` 連動のない単独 entry

高確度候補は候補ごとに証拠 1-2 行 (match_count / 観測窓 / scope) + 適用手順 (手順 4 の単位別分岐表) を添えて **AskUserQuestion で選択肢を提示する** (選択肢は「適用する / 見送る (レポート記載のみ) / 保留」相当)。

- **承認されたら scope で分岐する**: project scope entry は同セッション内で worktree + PR (通常フロー)。global scope (`~/.claude/settings.json`) は従来どおり人間側操作 — 削除対象 entry をコピペ可能形で示した具体的手順を提示して受け渡す
- 却下・保留された候補はレポートの従来 bucket section に残す
- 4 条件を満たさない候補 (approx entry / hold / 他 bucket を含む) はすべて手順 4 のレポートへ回す

### 4. Markdown レポート組み立て

`/tmp/inventory-permissions/report-<timestamp>.md` に mart と並置で書く。以下の**固定 schema**:

1. **ヘッダ**: 観測窓 / 総 event 数 / distinct sessions / section / 判定可能性 / matcher confidence 内訳
2. **候補 section** (bucket 別に列挙):
    - 単位: `entry` (A 軸候補) or `tool + command_head` (B 軸候補) or `session_id + denied_at` (bypass)
    - match_count / outcome_breakdown (A 軸) or count / outcomes (B 軸)
    - bucket 候補 (上表の語彙)
    - 証拠 (`sample_matched` / `samples` / `follow_ups` を mart から転記)
    - 提案 (1-2 行、証拠に紐づく)
    - 適用手順 (単位別分岐、下表参照)
3. **informational**: 今回未分類 (母数不足 / 証拠不十分) の一覧、hold (窓内追加・rare-by-design で露出不足 — 追加時期の確認手順つき) の一覧、matcher confidence 別集計
4. **summary 表**: 番号 × bucket × 対象。「3 と 7 だけ採用」と言える形。**高確度候補 (手順 3) も本表に載せ**、「セッション内提案済み (承認 / 見送り / 保留)」の結果を付記する — レポートは監査証跡として単体で完結させる

**適用手順の単位別分岐** (レポートに埋め込む):

| 対象 | 適用手順 |
|---|---|
| project scope の allow / deny / ask entry | worktree + PR で `.claude/settings.json(.local)` を編集 |
| global scope の allow / deny / ask entry | 人間が `~/.claude/settings.json` (or dotfiles 側) を直接編集 — 本 skill は書き込まない |
| sandbox 追加 | 該当 section の `permissions.sandbox.excludedCommands` に追加 (project or global) |
| hook 新設 / 改修 | 要件文まで書き、実装は別セッションで別途 (skill から実装せず) |

### 5. 人間判定 (確度で扱いが分かれる)

- **高確度候補** (revoke 限定): 手順 3 で AskUserQuestion 承認済みのものは同セッション内で適用まで進んでよい (project scope は worktree + PR、global scope は人間側操作の手順提示)。承認なしの適用・無人 commit は行わない
- **低確度候補**: レポートを提示するところで skill の責務は終わる。承認 → 適用は次のセッション (or 別の worktree) で人間が実施する

## 出力例 (概略)

```
# permission / sandbox / hook 棚卸しレポート

観測窓: 2026-06-17 〜 2026-07-17 (30 日) / 総 event 14,449 / distinct sessions 580 / section project
判定可能性: sufficient (総数 >= 30)
matcher confidence: exact 105 / approx 14

## revoke

### 1. Bash(some-unused-remote-write-cmd:*)  [allow, project_local]
- match_count 0 / observation 30 days
- bucket: revoke (未使用 かつ remote 書き込みの副作用能力あり)
- 提案: 30 日で 1 度も発火していない。削除して良い (read-only 無害なら keep 側へ)
- 適用手順: worktree で `.claude/settings.local.json` から本 entry を削除

## promote

### 2. Bash × gh pr view
- count 42 / outcomes {success: 40, deny_user-rejected: 2} / config_matches []
- bucket: promote
- 提案: 頻用されるが allow 未収載。`Bash(gh pr view:*)` を追加

## refine

### 3. Bash(grep:*)  [allow, project_local]
- match_count 986 / outcomes {success:940, deny_permission-rule:26, deny_user-rejected:15, error:4}
- sample_matched: grep -n (615) / grep -rn (144) / grep -rln (42)
- bucket: refine
- 提案: 広い prefix。deny_permission-rule=26 は path 系 deny (Read(**/.env) 等) の反射?
  実際に走った command_head を allow に個別追加し `Bash(grep:*)` は削除、または hook 化を検討

### 4. bypass 系列 (session 7a90... 2026-07-17T05:42:32Z, Bash)
- denied: `which claude 2>&1 && find ...` (permission-rule)
- follow_ups (gap ≤ 300s、同 tool):
  - `ls -la $(readlink -f ...)` — success (gap 1s)
- bucket: refine (deny 直後に別コマンドで成功 = 代替経路の存在)
- 提案: 該当領域 (~/.claude 探索) を hook で明示 deny するか、find の path 制限を allow に足す

## keep

### 5. Bash(git status:*)  [allow, project_local]
- match_count 274 / success 265 / deny 6
- bucket: keep (整合。deny は git status の path 制限系で許容範囲)

### 6. Bash(some-readonly-cmd:*)  [allow, project_local]
- match_count 0
- bucket: keep (未使用だが read-only 無害 — 削除は ask 反復コストだけ増える)

## summary

| # | bucket | 対象 | scope | セッション内提案 |
|---|---|---|---|---|
| 1 | revoke | Bash(some-unused-remote-write-cmd:*) | project_local | 提案済み (承認 → 適用) |
| 2 | promote | Bash × gh pr view | (未収載) | - (低確度) |
| 3 | refine | Bash(grep:*) | project_local | - (低確度) |
| 4 | refine | bypass session 7a90... | (系列) | - (低確度) |
| 5 | keep | Bash(git status:*) | project_local | - (低確度) |
| 6 | keep | Bash(some-readonly-cmd:*) | project_local | - (低確度) |
```

## 責務

- revoke / promote / refine / sandbox / keep の**判定は常に人間** (3 段階モデル不変)
- **高確度候補** (手順 3 の 4 条件、revoke 限定): セッション内 AskUserQuestion 提案 → 人間承認 → 同セッション内で適用まで進んでよい (project scope は worktree + PR、global scope は人間側操作の手順提示)。無人 commit は行わない
- **低確度候補**: 観測 → 候補提示 → 適用手順の明示までで止まる。適用 (settings 編集 / hook 実装 / sandbox 設定) はレポート提示外の作業として skill から切り離す

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| unknown が多い | tool_result が transcript 末尾で truncate / 別セッションに分割された、または未完了 | mart の `outcome_totals.unknown` を「参考値」として扱い、bucket 判定は明示 outcome を主にする |
| deny_user-rejected が過大 | Claude Code の [#29499](https://github.com/anthropics/claude-code/issues/29499) の false positive バグ | user-reject の count は bucket 判定の主根拠にしない (permission-rule / automode / success が主) |
| deny_hook が 0 | Claude Code が PreToolUse hook deny に `toolDenialKind: hook` を emit しないため、本 skill は明示 kind のみ信頼する保守設計 | hook 由来の deny は本 skill の観測範囲外。hook 追加要件は refine / sandbox の bucket に記述する |
| bypass 系列が過大 | 同 tool の後続 call を全て follow_up にするため、無関係な reuse も混入する | LLM 段階で「first follow_up が success かつ input が似ている」ものだけ refine 候補にする。低 gap の系列を優先 |
| project section で event_count が 0 | 現 cwd と event.cwd が別 (worktree 内で実行、transcript は親 repo path で保存等) | `--repo-root <parent>` で親を指定するか、`--section all` で対象範囲を広げる |
| matcher_confidence: approx の entry で match_count がぶれる | `**/*.env` 等の glob を fnmatch で近似しているため、本体 matcher と揺れる余地あり | approx の entry は entry 表示に「近似マッチ」注記を付ける。判断は人間に |
| bucket を script に埋め込みたくなる | 循環依存 (script が bucket を知ると LLM が再判定できなくなる) | script は生集計のみ。bucket 判定は LLM 段階で mart を読んでから |
| revoke 候補が過大になる | match_count == 0 を機械的に revoke へ割り当てた | 「未使用 かつ 副作用能力あり」に絞る (手順 2 の絞り込み)。実例: 2026-07-18 の初回棚卸しで 23 entry 提示 → 人間判定で 4 entry に縮小された |
| pair 規約・sandbox 連動の見落とし | entry 単位で独立判定した | 同一 script の複数呼び出し形と対応する `sandbox.excludedCommands` を 1 unit として提示する |
| 分割ファイル以外の集計が欲しくなる | 標準フロー外の検査 (90-mart.json は数 MB 級、inline python は hook 禁止の環境あり) | 90-mart.json への jq は単発に留める。恒常的に必要なら script の分割出力拡張 (split_outputs / derived_views) を提案 — LLM 段階の手集計を既定にしない |
| 高確度基準を満たさない候補をセッション内提案したくなる | 「approx でもほぼ確実」「count 1 だし」等の緩和誘惑 | 手順 3 の 4 条件を満たさないもの (approx entry / hold / revoke 以外の bucket) は必ずレポート側に落とす (基準の緩和は本 SKILL.md の改訂として行う) |

## 参照

- 仕様確定: [issue #213 コメント](https://github.com/swat9013/swat-skills/issues/213#issuecomment-4998014561)
- 上位 map: [issue #209](https://github.com/swat9013/swat-skills/issues/209)
- 関連 skill: inventory-skill-mcp (別軸: skill / MCP の実績集計 — 本 skill は permission 3 層)
