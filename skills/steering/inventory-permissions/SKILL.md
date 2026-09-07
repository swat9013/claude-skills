---
name: inventory-permissions
disable-model-invocation: true
description: Claude Code の permission (allow/deny/ask) / sandbox / guard hook を transcript の tool_use 実績と突合し、5 bucket (revoke / promote / refine / sandbox / keep) の候補を人間の判定へ差し出す棚卸し。
---

# inventory-permissions

Claude Code の permission 3 層 — **permission** (正規表現ベースの一律許可) / **hook** (細粒度の条件判断) / **sandbox** (行動範囲の線引き) — を、`~/.claude/projects/**/*.jsonl` の tool_use 実績と突合し、単位別に **5 bucket (revoke / promote / refine / sandbox / keep)** の候補を LLM に**候補提示までさせて**、判定は人間の判断に残す棚卸し skill。

3 段階モデル (原則: **決定的にできる推論は tool へ、意味判断だけを LLM へ、判定は人間に**):

1. **決定的観測 + 決定的ルール**: `scan_permissions` tool が両軸集計 + bypass 系列 + guard 逆引きを出し、機械判定可能な条件を評価して `rule_candidates` (`bucket_candidate` / `rule_fired` / `rule_inputs` / `open_predicates` / `near_misses`) を書く。**tool は bucket を確定しない**
2. **LLM 具体化 (このメインコンテキスト)**: `open_predicates` に挙がった条件だけを判断し、bucket を確定して具体 entry 案 (hook は要件文まで) を組み立てる。**採否は決めない**
3. **人間判定**: 削除/昇格/絞り込み/保持を選ぶのは常に人間。提示は確度で 2 層に分ける — **高確度候補** (手順 3) はセッション内で AskUserQuestion 提案し、承認されたら同セッション内で適用に進む (project scope は skill が編集、global scope は人間側操作)。**低確度候補**はレポート提示で止まる

無人 commit は行わない (適用は必ず AskUserQuestion での人間承認を経る)。

**汎用スキル制約**: 参照するのは Claude Code 標準ファイルのみ (`~/.claude/projects/` / `~/.claude/settings.json` / `<repo>/.claude/settings.json(.local)`)。dotfiles / swat-skills 固有 hook 資産 (tool-signatures.jsonl 等) には触らない。global 変更の反映先 (chezmoi 等) は各 repo 側の指示文の責務。

## 引数

- 省略時 → `project` section (cwd の `.claude/settings.json(.local)` × 当該 repo 実績)
- `global` → `~/.claude/settings.json` × 全 repo 実績
- `all` → 両方

## 手順

### 0. 前提: matcher 実装の確認 (best-effort)

Claude Code 本体の permission matcher 実装を確認できれば `sample_matched` を厳密化できるが、確認できなくても本 skill は動く。実装冒頭で以下を試み、`approx` (glob 近似) の解釈揺れが無いと確認できたら「該当 entry を `exact` 相当として扱って報告」を宣言する。確認できなくても続行 — ブロッカーにしない。

- 手元の Claude Code パッケージ内 permission 判定コードの探索 (grep)
- 公式 docs (`https://code.claude.com/docs/ja/`) の permission matcher 記述
- 実測 (deny 済み entry で似た pattern を作って発火試験)

**昇格できるのは `approx` だけで、`unmatchable` は対象外。** `approx` は照合対象を持ったうえで pattern の解釈が揺れる状態なので、本体仕様が判明すれば解消する。`unmatchable` は照合対象そのものが観測に無い状態で、本体仕様が判明しても照合対象は生えない (#873)。両者を混ぜて昇格させると、照合できていない entry を「使われていない」として revoke 候補へ戻すことになる。

いずれも空振りしたら本 skill は保守的近似のまま進む (`exact_tool` / `exact_command` / `prefix` / wildcard 無しの `domain` を `exact`、`glob` と wildcard 入りの `domain` を `approx` として提示)。

### 1. 観測 tool 起動 (決定的)

`mcp__plugin_swat-skills_transcript-ops__scan_permissions` を `section` に引数を渡して呼ぶ。

- `section` 省略時 `project`。`global` / `all` は上表の通り
- 直近 30 日を集計 — 変更したければ `days` を渡す
- 返り値の `paths` が **読む順の分割ファイル一覧** (`/tmp/inventory-permissions/run-<timestamp>/` 配下)
- **mart 本体は返らない**。返るのは path と件数 meta だけなので、中身は `paths` を Read する
- 想定所要時間: 初回のみ transcript の取り込みに十数秒かかる。2 回目以降は差分だけを取り込むので 1 秒未満 + 集計時間

`meta.total_events` が 0 なら、まず現 cwd と event の cwd の乖離を疑う (worktree 内で実行し transcript は親 repo path で保存されている等) — `repo_root` に親を渡すか `section: "all"` で対象範囲を広げて再実行する。それでも 0 なら (transcript lake が無い / settings が壊れている等) 「観測不能」を報告して終了。

### 2. 分割ファイルを読んで bucket を確定する (LLM)

ファイルの読む順・用途・derived view の意味論・**rule カタログ**・読み方の注記は `00-meta.json` の `contract` に従う (schema と規則の正本は tool 発の contract 一本で、本書は再記述しない)。**`paths` の順に Read するだけで標準フローが完結する** (jq / inline python 不要)。

読みながら bucket に落とすときの視点:

- `15-rule-candidates.json` が機械判定済みの候補。**`open_predicates` に挙がった条件だけを判断し**、満たすと判断したものだけ `bucket_candidate` を bucket として確定する。満たさないなら informational へ落とし、判断の根拠を書く
- `near_misses` は「あと 1 条件」で外れた entry と落選理由。**閾値・近似・連動の当否を疑う証拠はここにしか出ない** — レポートの informational に転記する
- `20-axis-a.json` が keep を含む全 entry の母集団。rule に載らなかった entry (promote / refine / sandbox / keep) はここを起点に割り当てる。entry の `scope` でどの層 (`project` / `project_local` / `project_local_main_clone`) 由来かを確かめる — worktree では親 clone の `settings.local.json` も分母に入るので、`settings_sources` の `project_local_main_clone` 行を見てから「未収載」を判断する
- `30-bypass-samples.json` の代表系列は `refine` の証拠としてレポートに転記する。follow_up は同 tool の後続 call を全て拾うので、**first follow_up が success かつ input が似ている**系列だけを候補にし、低 gap の系列を優先する
- `40-hooks.json` は **hook 軸** (下記「hook 観測の読み方」)。permission entry の bucket とは別枠で扱う

標準フロー外の追加検査が要るときは `mcp__plugin_swat-skills_transcript-ops__query` に read-only SQL を投げる (単発に留める。恒常的に必要になった集計は tool の分割出力拡張として提案する)。

**判定可能性の分岐**: `meta.sufficient_for_relative_judgment == false` なら**全単位を `insufficient-data`** としてレポートヘッダで宣言し、以下は informational として並べる (rule も同条件で発火しない)。

**分母の確認**: 突合結果 (`config_matches` / `global_config_matches`) より先に `meta.settings_denominator` を見て、`read: false` の行を reason ごとに把握する。`unparsed` がある間は promote / revoke を確定しない (層が抜けたまま「未収載」と読むことになる)。global 側に既に allow があるかは `~/.claude/settings.json` の match で確かめる — `~/.claude/settings.local.json` は Claude Code の settings 層ではなく `not_a_settings_layer` として列挙されるだけで効いていないので、反証根拠にはせず必要なら `~/.claude/settings.json` への移動を提案する (例外: cwd が `$HOME` のセッションでは同 path が local scope の解決先になって効き、`project_local` として分母に入る)。

**bucket vocabulary** (tool は候補ラベル `*-pending` までしか出さない。確定はここ):

| bucket | 証拠源 | 具体度 |
|---|---|---|
| **revoke** | `rule_fired: revoke_candidate` かつ `open_predicates` を満たすと判断したもの | コピペ可能な削除対象 entry を提示 |
| **promote** | `10-derived-views.json` の `axis_b_unlisted_frequent.units` で `config_matches` / `global_config_matches` の**どちらにも `category: allow` の match が無く**、success 頻発 / ask に対する success 頻発 | 追加すべき allow entry を提示 (例: `Bash(gh pr view:*)`) |
| **refine** | `rule_fired: compound_line_deny_miscount` / `axis_a_high_deny_share` (`window_split` を先に見る。下記「確度注記の義務」) / `bypass_sequences` に該当系列 | 分割 entry 案 (広い pattern を絞る、または hook に移す要件文) |
| **sandbox** | 到達範囲を制限すべき系列 (例: shell が広く許可されているが実行内容は限定的) | `sandbox.excludedCommands` の具体 entry 案 or hook 要件 |
| **keep** | `axis_a` で match_count > 0 かつ deny 少数 / 未使用だが open_predicates を満たさないと判断したもの | 「保持」を明示的に記録 (次回の revoke 誤判定を防ぐ) |

**bucket 割当ての制約**:
- 上表の**証拠源**に沿って割り当てる。証拠に紐づかない bucket 割当てはしない
- bypass 系列は独立 bucket にしない — `refine` の証拠として扱う
- guard 逆引き (`guard_reverse_lookup`) は refine / sandbox の証拠として使う
- promote は `global_config_matches` まで見てから割り当てる。`category: allow` の match があれば promote 不要、`deny` だけの match は refine の証拠として扱う (複合行由来の deny を疑う)
- **この view の unit は定義上すべて未被覆 >= 収載床なので、`config_matches` に match がある unit は例外なく部分被覆** (既存 entry が unit の一部しか覆っていない)。`uncovered_event_count` の非 0 は判別条件にならない。global 側で覆われているかを `global_uncovered_event_count == 0` で見られるのは **section project のときだけ** — section global では両者が同値なので判別材料が無く、`query` tool で B 軸を直接引く。部分被覆に当たったら promote (entry の追加) ではなく **pattern を広げる refine** を検討し、未被覆の実行が何かを `query` tool で当該 tool の実行から確かめる (B 軸の unit key は引数を持たず、A 軸の `sample_matched` も match した実行しか持たないので、どちらも未被覆側の実体には答えない)
- **未被覆には照合不能な実行が混ざる**。matcher が読む列を持たない実行は必ず未被覆に落ちる (引数が任意の tool では正当な省略もこの数に入る)。**A 軸の `unobserved_input_count` では差し引けない** — A 軸の行は config entry を列挙して作るので、その tool の entry が section の config に 1 件も無ければ数が出力に現れず、`config_matches: []` の unit (= promote 候補そのもの) では引けない。純粋な未収載件数が要るなら `query` tool で当該 tool の実行を直接見る
- promote 候補の対象が script path を含むなら、その path の実在を確認してから提示する (窓内に使われていた script が窓の後半で削除されていることがある)
- hook の実装案は**要件文まで** (実装はしない)
- deny / allow / sandbox は**コピペ可能な具体 entry 案**まで書く

**open_predicates の判断方法** (述語文そのものは contract の rule カタログが正本。ここは判断の**手順**だけ):

- `exposure_opportunity`: settings が git 管理下なら `git log -S '<entry>'` で追加時期を確認する。**追加時期が窓外でも露出不足はありうる** — 窓の作業内容が偏っていれば capability を使う機会自体が発生していない。窓内の cwd 分布 (`~/.claude/projects/` の project ディレクトリ、bypass sample の `cwd`) を見て機会の実在を判定する
    - settings が git 管理外 (`git ls-files --error-unmatch <path>` が非 0) なら**追加時期は履歴から詰められない**。根拠を窓内の cwd 分布だけに置き、entry 表示に「追加時期不明 (git 管理外)」と書く。cwd 分布から機会が読めるなら `推測:` prefix つきで満たすと判断し (手順 3 の条件 2 は満たさない)、読めないなら未充足として informational へ落とす
    - `project_local` の entry は `settings_denominator` の `resolved_path` を見る。repo 内の**配布用ディレクトリ** (他 project へ配る原本) への symlink を指しているなら、その entry の本来の利用者は**観測範囲外の他 repo**で、当該 repo の `match_count 0` は不使用の証拠にならない — `exposure_opportunity` は当該 repo の実績だけでは判定できず、**revoke ではなく keep** とする (原本の剪定基準は repo 側の規約が正本)
- `alias_still_in_use`: `axis_b_actual_usage` を当該 tool 名・`mcp__` で引いて別名の実績を確かめる。あれば revoke ではなく refine (pattern の書き換え)
- `invocation_form_pair`: repo 側 README / commit 履歴に pair 規約の意図が残っていないか確認する
- `deny_attributable_to_entry`: `sample_matched` と `bypass_sequences` の入力コマンドを読む。実因が複合行の混在なら refine の対象は entry ではなく「複合行の組み立て方」で、entry 変更は不要

**確度注記の義務**:
- `hard_deny_share` を bucket 判定の根拠にする前に、その deny が **permission entry 由来か**を確かめる。guard hook 由来の deny も `deny_permission-rule` にラベルされ (`denial_kind` 空 + `outcome_base: error` に落ちる経路もある)、guard の deny メッセージは安定文字列なので `result_text` の署名で帰属が引ける。入力が汚染されているときは `axis_a_high_deny_share` / `compound_line_deny_miscount` の 0 件を「候補が無い」ではなく「入力が汚れている」と読む
- outcome の `unknown` は**参考値**として扱い、bucket 判定は明示 outcome を主根拠にする (tool_result が transcript 末尾で truncate された / 別セッションに分割された / 未完了、のいずれでも unknown に落ちる)
- `meta.store` の**事故由来**の劣化 (`broken_lines` / `unreadable_files`) が 0 でないうちは、revoke ではなく hold にする — observation が欠けており、未使用に見える entry が実際には使われていることがある。`skipped_nested_files` は subagent transcript を ingest しない設計由来で恒久的に非 0 なので hold の条件にせず、件数をレポートヘッダで宣言する (subagent 内だけで使われた entry が `match_count 0` に見えるリスクは受容済み)
- `axis_a_high_deny_share` の `window_split.shifted: true` の entry は、**窓全体の `hard_deny_share` を bucket 判定の根拠にしない** (変更前後を混ぜた平均で、どちらの期間も表していない)。前半 / 後半の `hard_deny_share` を entry 表示に併記し、判定は変化後 (後半) の値で行う。`shifted: null` は判定不能 (どちらかの半分が薄い) で「窓内で一様」の証拠ではない — 前後半の件数を注記して informational へ落とす
- `matcher_confidence: approx` の entry は「近似マッチ (glob / domain の wildcard) — 実 matcher と揺れる可能性」を **entry 表示に注記**し、revoke / keep の判定対象から外して informational へ置く (rule 側でも除外され `near_misses` に出る)
- `matcher_confidence: unmatchable` の entry は **`match_count 0` を「未使用」と読まない** (読み方は mart の `contract.notes` が正本)。approx と同じく revoke / keep の判定対象から外し、**`unmatchable_reason` をそのまま**理由に添えて informational へ置く。所見には「どうすれば判定できるようになるか」を併記する — `input_column_empty` なら store に照合対象の列を足す、`param_rule` (`Tool(param:value)` 形で server が param の値を持たない) なら transcript を直接読む。**`param_rule` に `query` tool を使わない** — 値を持つ列が無く、`input_excerpt` は 200 字上限で Bash は `command` が先に入るため、長い command の呼び出しでは param が切り落とされて実在する呼び出しを 0 件と読む (この 0 を未使用と読むと #888 の偽陽性が戻る)
- `unobserved_input_count` が 0 でない entry を revoke 候補として採る前に、その数を所見に出す。**`match_count` は確認できた範囲の下限**なので、0 件でも「その pattern を使っていない」とは言い切れない。差の実体が `__unparsedToolInput` (実行されていない呼び出し) なら 0 を実績として読んでよく、引数が任意の tool (`Grep` の `path` 等) の正当な省略なら読めない — どちらかは `query` tool で当該 tool の入力を見て確かめる
- outcome の `deny_user-rejected` は Claude Code の [#29499](https://github.com/anthropics/claude-code/issues/29499) の false positive バグ影響下 — bucket 判定の**主根拠にしない** (count が主根拠)
- `guard_reverse_lookup` に hook-deny (Claude Code の PreToolUse permissionDecision: deny) は原則含まれない (現状 `toolDenialKind` に emit されない)。automode-blocked / automode-unavailable のみを対象とし、「hook 由来の deny は本 skill の観測範囲外」と明記する

**hook 観測の読み方 (`40-hooks.json`)**:

統治対象の 3 本目 (permission / sandbox / **guard hook**) に対する観測。観測限界は同ファイルの `observability` が正本で、本書は再記述しない。読むときの判断:

- `fire_count` が `null` (未観測) の hook は **informational**。permission entry の revoke と違い、hook は「発火条件を満たす操作が窓内に無かっただけ」が常にありうる (null は「発火なし」と「発火したが観測に残らなかった」を区別しない) — 窓内の作業内容 (cwd 分布・tool 実績) と突き合わせて**機会が実在したか**を確かめてから所見を書く
- `key_collision: true` の unit は fire 回数を**共有値**として扱い、所見にその旨を明記する。単独実績として断定できるのは「fire していない」ことだけ
- 遅い hook は `duration_ms` の `p95` / `max` / `total` で見る。全 hook の `total` は 1 セッションあたりの待ち時間そのものなので、体感の遅さを裏づける証拠として使える
- **hook の反映先は本 skill の write 対象外**。settings の `hooks` 登録も plugin の `hooks.json` も entry 案までで、実装・配線は別作業として要件文で渡す

### 3. 高確度候補の抽出とセッション内提案

`revoke` に確定した bucket のうち、以下をすべて満たすものだけを高確度として抽出する:

1. `rule_fired` に `revoke_candidate` が入っている (機械判定可能な条件は tool が確認済み)
2. `open_predicates` の全条件を**決定的観測だけで**満たすと判断できた (`推測:` prefix を要しない)
3. 対象 settings file が agent から編集可能で (下記)、適用手順が単一の既定分岐で完結する

**該当 0 件が既定の結果**: `revoke_candidate` の `open_predicates` には `exposure_opportunity` (機会の実在) が必ず含まれ、その証明は「機会があったのに使われなかった」という反実仮想の推論にしかならないため、条件 2 を決定的観測だけで満たすことは構造的にできない。本手順は稀にしか発火しない安全弁であり、0 件は異常でも観測の失敗でもない。0 件でも条件を緩めず、revoke 候補は手順 4 のレポート (低確度) へ回す。

**条件 3 の編集可能性は AskUserQuestion より前に確かめる** — 承認を得てから適用不能と判明すると、人間に無駄な判断をさせる。候補の対象 file (`<repo>/.claude/settings.json` / `<repo>/.claude/settings.local.json` / `~/.claude/settings.json`) ごとに次を実行し、1 つでも該当したら編集不能と確定して手順 4 のレポートへ回す:

| 確認 | 実行 | 編集不能と読む結果 |
|---|---|---|
| version control 追跡の有無 | `git ls-files --error-unmatch <path>` | exit != 0 — untracked。version control が追跡しないので、編集しても差分が残らず取り消せない |
| 編集が当該 repo に着地するか | `realpath <path>` が `git rev-parse --show-toplevel` の配下か | 配下でない — 別 repo / repo 外への symlink。書き込みは実体側の repo にしか差分を出さない (tracked な symlink は前行を通過するので、この行を独立に見る) |
| 自己編集の deny | `20-axis-a.json` と `~/.claude/settings.json` の `permissions.deny` から `Edit` / `Write` の entry を引き、対象 file と照合する | 一致する entry がある |

deny の照合では、対象 file を指す**すべての表記** (実パス / `~/` 表記 / symlink 経由の表記) を照合対象にし、1 つでも一致したら編集不能とする。**一致しない別表記を探して書き込みに回さない** — matcher は表記を realpath 解決しないので別表記なら通ってしまうが、この deny は「agent に自分の permission を書き換えさせない」意図の表明であり、表記を替えて通すのはその意図を破る。

高確度候補は候補ごとに証拠 1-2 行 (`rule_inputs` の match_count / 観測窓 / scope) + 適用手順 (手順 4 の単位別分岐表) を添えて **AskUserQuestion で選択肢を提示する** (選択肢は「適用する / 見送る (レポート記載のみ) / 保留」相当)。

- **承認されたら scope で分岐する**: project scope entry は同セッション内で編集し、変更の届け方は実行 project の運用に従う。global scope (`~/.claude/settings.json`) は従来どおり人間側操作 — 削除対象 entry をコピペ可能形で示した具体的手順を提示して受け渡す
- 却下・保留された候補、および 3 条件を満たさない候補はすべて手順 4 のレポートへ回す

### 4. Markdown レポート組み立て

`/tmp/inventory-permissions/report-<timestamp>.md` に mart と並置で書く。以下の**固定 schema**:

1. **ヘッダ**: 観測窓 / 総 event 数 / distinct sessions / section / 判定可能性 / matcher confidence 内訳 / 観測の劣化 (`meta.store` の 0 でない項目を事故由来 (`broken_lines` / `unreadable_files`) と設計由来 (`skipped_nested_files` — subagent transcript を ingest しない設計のため恒久的に非 0) に分けて宣言する。`skipped_nested_files` は必ず件数を書き、事故由来がすべて 0 なら「事故由来の劣化なし」と明記) / 分母の欠落 (`meta.settings_denominator` の `read: false` 行を reason ごとに宣言する。`unparsed` / `not_a_settings_layer` が在れば必ず書く)
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
| **編集不能な settings** — 対象 file が untracked / worktree 外への symlink / `Edit()`・`Write()` deny のいずれかに該当 (**scope 別の行より優先する**) | 本 skill は書き込まない。対象 file の**実パス**と削除対象 entry をコピペ可能形で提示して止め、人間が直接編集する |
| project scope の allow / deny / ask entry | `.claude/settings.json(.local)` を編集 |
| global scope の allow / deny / ask entry | 人間が `~/.claude/settings.json` (or dotfiles 側) を直接編集 — 本 skill は書き込まない |
| sandbox 追加 | 該当 section の `permissions.sandbox.excludedCommands` に追加 (project or global) |
| hook 新設 / 改修 | 要件文まで書き、実装は別セッションで別途 (skill から実装せず) |

### 5. 人間判定 (確度で扱いが分かれる)

- **高確度候補** (revoke 限定): 手順 3 で AskUserQuestion 承認済みのものは同セッション内で適用まで進んでよい (project scope は skill が編集、global scope は人間側操作の手順提示)。承認なしの適用・無人 commit は行わない
- **低確度候補**: レポートを提示するところで skill の責務は終わる。承認 → 適用は次のセッションで人間が実施する

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
- 編集可能性: `.claude/settings.local.json` は untracked (`git ls-files --error-unmatch` exit 1) — version control 追跡なし
- 適用手順: 本 skill は書き込まない。`<repo 実パス>/.claude/settings.local.json` の `permissions.allow` から
  `"Bash(some-unused-remote-write-cmd:*)"` の行を人間が直接削除する

## refine

### 2. Bash(grep:*)  [allow, project_local]
- rule_fired: compound_line_deny_miscount / hard_deny 26 / compound_command_deny 24 / window_split shifted false (窓内で一様)
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

## 参照

- 関連 skill: inventory-skill-mcp (別軸: skill / MCP の実績集計 — 本 skill は permission 3 層)
