---
name: inventory-permissions
disable-model-invocation: true
description: Claude Code の permission (allow/deny/ask) / sandbox / guard hook を transcript の tool_use 実績と突合し、両軸集計 (設定 pattern × 実績) と bypass 系列を単位別に 5 bucket (revoke / promote / refine / sandbox / keep) の候補提示まで LLM に運ばせる棚卸し。決定的ルールは tool が評価し (bucket_candidate / rule_fired / open_predicates)、LLM は open_predicates だけを判断する。高確度候補 (revoke 限定) はセッション内で AskUserQuestion 提案し承認後そのまま適用、低確度候補はレポート提示。判定は人間。汎用スキル制約 (Claude Code 標準ファイルのみ参照) で動く。Use when「permission 棚卸し」「settings.json の allow を見直したい」「未使用 allow を削りたい」「hook / sandbox の切り分け」「bypass 系列を確認」「棚卸し」「inventory-permissions」.
---

# inventory-permissions

Claude Code の permission 3 層 — **permission** (正規表現ベースの一律許可) / **hook** (細粒度の条件判断) / **sandbox** (行動範囲の線引き) — を、`~/.claude/projects/**/*.jsonl` の tool_use 実績と突合し、単位別に **5 bucket (revoke / promote / refine / sandbox / keep)** の候補を LLM に**候補提示までさせて**、判定は人間の判断に残す棚卸し skill。

3 段階モデル (原則: **決定的にできる推論は tool へ、意味判断だけを LLM へ、判定は人間に**):

1. **決定的観測 + 決定的ルール**: `scan_permissions` tool が両軸集計 + bypass 系列 + guard 逆引きを出し、機械判定可能な条件を評価して `rule_candidates` (`bucket_candidate` / `rule_fired` / `rule_inputs` / `open_predicates` / `near_misses`) を書く。**tool は bucket を確定しない**
2. **LLM 具体化 (このメインコンテキスト)**: `open_predicates` に挙がった条件だけを判断し、bucket を確定して具体 entry 案 (hook は要件文まで) を組み立てる。**採否は決めない**
3. **人間判定**: 削除/昇格/絞り込み/保持を選ぶのは常に人間。提示は確度で 2 層に分ける — **高確度候補** (手順 3) はセッション内で AskUserQuestion 提案し、承認されたら同セッション内で適用に進む (project scope は worktree + PR、global scope は人間側操作)。**低確度候補**はレポート提示で止まる

無人 commit は行わない (適用は必ず AskUserQuestion での人間承認を経る)。

**汎用スキル制約**: 参照するのは Claude Code 標準ファイルのみ (`~/.claude/projects/` / `~/.claude/settings.json` / `<repo>/.claude/settings.json(.local)`)。dotfiles / swat-skills 固有 hook 資産 (tool-signatures.jsonl 等) には触らない。global 変更の反映先 (chezmoi 等) は各 repo 側の指示文の責務。

## 引数

- 省略時 → `project` section (cwd の `.claude/settings.json(.local)` × 当該 repo 実績)
- `global` → `~/.claude/settings.json` × 全 repo 実績
- `all` → 両方

## 手順

### 0. 前提: matcher 実装の確認 (best-effort)

Claude Code 本体の permission matcher 実装を確認できれば `sample_matched` を厳密化できるが、確認できなくても本 skill は動く。実装冒頭で以下を試み、確認できたら「matcher_confidence を `exact` に昇格して報告」を宣言する。確認できなくても続行 — ブロッカーにしない。

- 手元の Claude Code パッケージ内 permission 判定コードの探索 (grep)
- 公式 docs (`https://code.claude.com/docs/ja/`) の permission matcher 記述
- 実測 (deny 済み entry で似た pattern を作って発火試験)

いずれも空振りしたら本 skill は保守的近似のまま進む (`exact_tool` / `exact_command` / `prefix` を `exact`、`glob` を `approx` として提示)。

### 1. 観測 tool 起動 (決定的)

`mcp__plugin_swat-skills_transcript-ops__scan_permissions` を `section` に引数を渡して呼ぶ。

- `section` 省略時 `project`。`global` / `all` は上表の通り
- 直近 30 日を集計 — 変更したければ `days` を渡す
- 返り値の `paths` が **読む順の分割ファイル一覧** (`/tmp/inventory-permissions/run-<timestamp>/` 配下)
- **mart 本体は返らない**。返るのは path と件数 meta だけなので、中身は `paths` を Read する
- 想定所要時間: 初回のみ transcript の取り込みに十数秒かかる。2 回目以降は差分だけを取り込むので 1 秒未満 + 集計時間

`meta.total_events` が 0 なら (transcript lake が無い / settings が壊れている等) 「観測不能」を報告して終了。

### 2. 分割ファイルを読んで bucket を確定する (LLM)

ファイルの読む順・用途・derived view の意味論・**rule カタログ**・読み方の注記は `00-meta.json` の `contract` に従う (schema と規則の正本は tool 発の contract 一本で、本書は再記述しない)。**`paths` の順に Read するだけで標準フローが完結する** (jq / inline python 不要)。

読みながら bucket に落とすときの視点:

- `15-rule-candidates.json` が機械判定済みの候補。**`open_predicates` に挙がった条件だけを判断し**、満たすと判断したものだけ `bucket_candidate` を bucket として確定する。満たさないなら informational へ落とし、判断の根拠を書く
- `near_misses` は「あと 1 条件」で外れた entry と落選理由。**閾値・近似・連動の当否を疑う証拠はここにしか出ない** — レポートの informational に転記する
- `20-axis-a.json` が keep を含む全 entry の母集団。rule に載らなかった entry (promote / refine / sandbox / keep) はここを起点に割り当てる
- `30-bypass-samples.json` の代表系列は `refine` の証拠としてレポートに転記する
- `40-hooks.json` は **hook 軸** (下記「hook 観測の読み方」)。permission entry の bucket とは別枠で扱う

標準フロー外の追加検査が要るときは `mcp__plugin_swat-skills_transcript-ops__query` に read-only SQL を投げる (単発に留める。恒常的に必要になった集計は tool の分割出力拡張として提案する)。

**判定可能性の分岐**: `meta.sufficient_for_relative_judgment == false` なら**全単位を `insufficient-data`** としてレポートヘッダで宣言し、以下は informational として並べる (rule も同条件で発火しない)。

**bucket vocabulary** (tool は候補ラベル `*-pending` までしか出さない。確定はここ):

| bucket | 証拠源 | 具体度 |
|---|---|---|
| **revoke** | `rule_fired: revoke_candidate` かつ `open_predicates` を満たすと判断したもの | コピペ可能な削除対象 entry を提示 |
| **promote** | `10-derived-views.json` の `axis_b_unlisted_frequent.units` で `config_matches` / `global_config_matches` の**どちらにも `category: allow` の match が無く**、success 頻発 / ask に対する success 頻発 | 追加すべき allow entry を提示 (例: `Bash(gh pr view:*)`) |
| **refine** | `rule_fired: compound_line_deny_miscount` / `axis_a_high_deny_share` / `bypass_sequences` に該当系列 | 分割 entry 案 (広い pattern を絞る、または hook に移す要件文) |
| **sandbox** | 到達範囲を制限すべき系列 (例: shell が広く許可されているが実行内容は限定的) | `sandbox.excludedCommands` の具体 entry 案 or hook 要件 |
| **keep** | `axis_a` で match_count > 0 かつ deny 少数 / 未使用だが open_predicates を満たさないと判断したもの | 「保持」を明示的に記録 (次回の revoke 誤判定を防ぐ) |

**bucket 割当ての制約**:
- 上表の**証拠源**に沿って割り当てる。証拠に紐づかない bucket 割当てはしない
- bypass 系列は独立 bucket にしない — `refine` の証拠として扱う
- guard 逆引き (`guard_reverse_lookup`) は refine / sandbox の証拠として使う
- hook の実装案は**要件文まで** (実装はしない)
- deny / allow / sandbox は**コピペ可能な具体 entry 案**まで書く

**open_predicates の判断方法** (述語文そのものは contract の rule カタログが正本。ここは判断の**手順**だけ):

- `exposure_opportunity`: settings が git 管理下なら `git log -S '<entry>'` で追加時期を確認する。**追加時期が窓外でも露出不足はありうる** — 窓の作業内容が偏っていれば capability を使う機会自体が発生していない。窓内の cwd 分布 (`~/.claude/projects/` の project ディレクトリ、bypass sample の `cwd`) を見て機会の実在を判定する
- `alias_still_in_use`: `axis_b_actual_usage` を当該 tool 名・`mcp__` で引いて別名の実績を確かめる。あれば revoke ではなく refine (pattern の書き換え)
- `invocation_form_pair`: repo 側 README / commit 履歴に pair 規約の意図が残っていないか確認する
- `deny_attributable_to_entry`: `sample_matched` と `bypass_sequences` の入力コマンドを読む。実因が複合行の混在なら refine の対象は entry ではなく「複合行の組み立て方」で、entry 変更は不要

**確度注記の義務**:
- `matcher_confidence: approx` の entry は「近似マッチ (glob) — 実 matcher と揺れる可能性」を **entry 表示に注記**し、revoke / keep の判定対象から外して informational へ置く (rule 側でも除外され `near_misses` に出る)
- outcome の `deny_user-rejected` は Claude Code の [#29499](https://github.com/anthropics/claude-code/issues/29499) の false positive バグ影響下 — bucket 判定の**主根拠にしない** (count が主根拠)
- `guard_reverse_lookup` に hook-deny (Claude Code の PreToolUse permissionDecision: deny) は原則含まれない (現状 `toolDenialKind` に emit されない)。automode-blocked / automode-unavailable のみを対象とし、「hook 由来の deny は本 skill の観測範囲外」と明記する

**hook 観測の読み方 (`40-hooks.json`)**:

統治対象の 3 本目 (permission / sandbox / **guard hook**) に対する観測。観測限界は同ファイルの `observability` が正本で、本書は再記述しない。読むときの判断:

- `fire_count` が `null` (未観測) の hook は **informational**。permission entry の revoke と違い、hook は「発火条件を満たす操作が窓内に無かっただけ」が常にありうる (null は「発火なし」と「発火したが観測に残らなかった」を区別しない) — 窓内の作業内容 (cwd 分布・tool 実績) と突き合わせて**機会が実在したか**を確かめてから所見を書く
- 遅い hook は `duration_ms` の `p95` / `max` / `total` で見る。全 hook の `total` は 1 セッションあたりの待ち時間そのものなので、体感の遅さを裏づける証拠として使える
- **hook の反映先は本 skill の write 対象外**。settings の `hooks` 登録も plugin の `hooks.json` も entry 案までで、実装・配線は別作業として要件文で渡す

### 3. 高確度候補の抽出とセッション内提案

`revoke` に確定した bucket のうち、以下をすべて満たすものだけを高確度として抽出する:

1. `rule_fired` に `revoke_candidate` が入っている (機械判定可能な条件は tool が確認済み)
2. `open_predicates` の全条件を**決定的観測だけで**満たすと判断できた (`推測:` prefix を要しない)
3. 適用手順が単一の既定分岐で完結する

**該当 0 件が既定の結果**: `revoke_candidate` の `open_predicates` には `exposure_opportunity` (機会の実在) が必ず含まれ、その証明は「機会があったのに使われなかった」という反実仮想の推論にしかならないため、条件 2 を決定的観測だけで満たすことは構造的にできない。本手順は稀にしか発火しない安全弁であり、0 件は異常でも観測の失敗でもない。0 件でも条件を緩めず、revoke 候補は手順 4 のレポート (低確度) へ回す。

高確度候補は候補ごとに証拠 1-2 行 (`rule_inputs` の match_count / 観測窓 / scope) + 適用手順 (手順 4 の単位別分岐表) を添えて **AskUserQuestion で選択肢を提示する** (選択肢は「適用する / 見送る (レポート記載のみ) / 保留」相当)。

- **承認されたら scope で分岐する**: project scope entry は同セッション内で worktree + PR (通常フロー)。global scope (`~/.claude/settings.json`) は従来どおり人間側操作 — 削除対象 entry をコピペ可能形で示した具体的手順を提示して受け渡す
- 却下・保留された候補、および 3 条件を満たさない候補はすべて手順 4 のレポートへ回す

### 4. Markdown レポート組み立て

`/tmp/inventory-permissions/report-<timestamp>.md` に mart と並置で書く。以下の**固定 schema**:

1. **ヘッダ**: 観測窓 / 総 event 数 / distinct sessions / section / 判定可能性 / matcher confidence 内訳 / 観測の劣化 (`meta.store` の 0 でない項目を事故由来 (`broken_lines` / `unreadable_files`) と設計由来 (`skipped_nested_files` — subagent transcript を ingest しない設計のため恒久的に非 0) に分けて宣言する。`skipped_nested_files` は必ず件数を書き、事故由来がすべて 0 なら「事故由来の劣化なし」と明記)
2. **候補 section** (bucket 別に列挙):
    - 単位: `entry` (A 軸候補) or `tool + command_head` (B 軸候補) or `session_id + denied_at` (bypass)
    - match_count / outcome_breakdown (A 軸) or count / outcomes (B 軸)
    - bucket (上表の語彙) と、`rule_fired` / `open_predicates` の判断結果
    - 証拠 (`sample_matched` / `samples` / `follow_ups` を mart から転記)
    - 提案 (1-2 行、証拠に紐づく)
    - 適用手順 (単位別分岐、下表参照)
3. **informational**: 今回未分類 (母数不足 / 証拠不十分) の一覧、`near_misses` (落選理由つき)、`open_predicates` を満たさないと判断した候補、matcher confidence 別集計
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
観測の劣化: 事故由来の劣化なし / skipped_nested_files 754 (設計由来 — subagent transcript は観測範囲外)

## revoke

### 1. Bash(some-unused-remote-write-cmd:*)  [allow, project_local]
- rule_fired: revoke_candidate / match_count 0 / observation 30 days
- open_predicates: side_effect_capability ○ (remote 書き込み) / exposure_opportunity △
  (推測: 窓内に同 repo での作業 42 session — 機会の実在は反実仮想) / alias_still_in_use ✗ / invocation_form_pair ✗
- bucket: revoke (低確度 — レポート提示まで)
- 適用手順: worktree で `.claude/settings.local.json` から本 entry を削除

## refine

### 2. Bash(grep:*)  [allow, project_local]
- rule_fired: compound_line_deny_miscount / hard_deny 26 / compound_command_deny 24
- open_predicates: deny_attributable_to_entry ✗ (deny の 24/26 が `grep ... || <deny 対象>`)
- bucket: refine — 対象は entry ではなく複合行の組み立て方。entry 変更は不要

## keep

### 3. Bash(some-readonly-cmd:*)  [allow, project_local]
- rule_fired: revoke_candidate / match_count 0
- open_predicates: side_effect_capability ✗ (read-only)
- bucket: keep (未使用だが無害 — 削除は ask 反復コストだけ増える)

## informational — near_misses

- Bash(**/*.env) : revoke_candidate の matcher_exact を外した (approx)
- Bash(gh:*) : revoke_candidate の no_sandbox_pair を外した (excludedCommands と連動)

## summary

| # | bucket | 対象 | scope | セッション内提案 |
|---|---|---|---|---|
| 1 | revoke | Bash(some-unused-remote-write-cmd:*) | project_local | - (低確度: exposure_opportunity が推測) |
| 2 | refine | Bash(grep:*) | project_local | - (低確度) |
| 3 | keep | Bash(some-readonly-cmd:*) | project_local | - (低確度) |
```

## 責務

- revoke / promote / refine / sandbox / keep の**判定は常に人間** (3 段階モデル不変)
- **決定的ルールは tool 側**。LLM が判断するのは `open_predicates` に挙がった条件だけで、機械判定可能な条件を再導出しない
- **高確度候補** (手順 3、revoke 限定): セッション内 AskUserQuestion 提案 → 人間承認 → 同セッション内で適用まで進んでよい (project scope は worktree + PR、global scope は人間側操作の手順提示)。無人 commit は行わない
- **低確度候補**: 観測 → 候補提示 → 適用手順の明示までで止まる

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| unknown が多い | tool_result が transcript 末尾で truncate / 別セッションに分割された、または未完了 | mart の `outcome_totals.unknown` を「参考値」として扱い、bucket 判定は明示 outcome を主にする |
| deny_user-rejected が過大 | Claude Code の [#29499](https://github.com/anthropics/claude-code/issues/29499) の false positive バグ | user-reject の count は bucket 判定の主根拠にしない (permission-rule / automode / success が主) |
| deny_hook が 0 | Claude Code が PreToolUse hook deny に `toolDenialKind: hook` を emit しないため、本 skill は明示 kind のみ信頼する保守設計 | hook 由来の deny は本 skill の観測範囲外。hook 追加要件は refine / sandbox の bucket に記述する |
| fire_count null の hook を「死んでいる」と断じる | 発火条件を満たす操作が窓内に無かっただけ (または発火が観測に残らなかっただけ) の可能性を潰していない | 窓内の作業内容と突き合わせて機会の実在を確かめる。fire_count null は informational 止まり |
| hook の fire 回数を unit ごとに断定する | `key_collision: true` の共有値を単独実績と読んだ | 共有 key の unit は「fire していない」だけを主張する。回数は共有値である旨を明記する |
| bypass 系列が過大 | 同 tool の後続 call を全て follow_up にするため、無関係な reuse も混入する | 「first follow_up が success かつ input が似ている」ものだけ refine 候補にする。低 gap の系列を優先 |
| project section で event_count が 0 | 現 cwd と event.cwd が別 (worktree 内で実行、transcript は親 repo path で保存等) | `repo_root` に親を渡すか、`section: "all"` で対象範囲を広げる |
| 未使用に見える entry が実は使われている | `meta.store` の劣化シグナルを読まずに `rule_fired` を鵜呑みにした | 事故由来の `broken_lines` / `unreadable_files` が 0 でなければ observation が欠けている — 0 でない項目があるうちは revoke ではなく hold。`skipped_nested_files` は subagent transcript を ingest しない設計由来で恒久的に非 0 — hold の条件にせず、件数をレポートヘッダで宣言する (subagent 内だけで使われた entry が match_count 0 に見えるリスクは受容済み) |
| 現役の capability を revoke に出す | tool / MCP server の改名で pattern だけが古くなり「未使用」に見える | `alias_still_in_use` を判断する (手順 2)。別名の実績があれば revoke ではなく refine |
| global で既に許可されている entry を promote 候補に出す | `config_matches: []` を「どこにも収載されていない」と読んだ (section `project` の config は project + project_local だけ) | `global_config_matches` を見る。`category: allow` の match があれば promote 不要。`deny` だけの match は refine の証拠 (複合行由来の deny を疑う) |
| promote 候補の対象が既に存在しない | 頻出実績だけを見て promote した | script path を含む unit は対象の実在を確認する。窓内に使われていた script が窓の後半で削除されている場合がある |
| bucket 判定を tool に確定させたくなる | 「候補まで出せるなら bucket も出せる」と感じる | 意味判断 (副作用能力 / rare-by-design) の機械化には allowlist が要り、陳腐化リスクが恒常化する。人間が候補を覆す余地も壊れる ([ADR 0032](https://github.com/swat9013/swat-skills/blob/main/docs/adr/0032-policy-free-refinement-deterministic-rules.md) が却下した形) |
| revoke 候補が過大になる | `rule_fired` を bucket の確定と読み、`open_predicates` を判断しなかった | `bucket_candidate` は候補であって確定ではない。open_predicates を 1 つずつ判断してから bucket に落とす |
| 分割ファイル以外の集計が欲しくなる | 標準フロー外の検査 | `query` tool への read-only SQL を単発で使う。恒常的に必要なら tool の分割出力拡張 (split_outputs / derived_views) を提案 — LLM 段階の手集計を既定にしない |
| 高確度基準を満たさない候補をセッション内提案したくなる | 「approx でもほぼ確実」「count 1 だし」等の緩和誘惑 | 手順 3 の 3 条件を満たさないものは必ずレポート側に落とす (基準の緩和は rule 実装か本 SKILL.md の改訂として行う) |

## 参照

- 関連 skill: inventory-skill-mcp (別軸: skill / MCP の実績集計 — 本 skill は permission 3 層)
