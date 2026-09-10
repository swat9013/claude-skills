---
name: orchestrator
disable-model-invocation: true
argument-hint: "[max]"
description: herdr session 内で着手可能な open issue を分割 pane の Claude Code セッションへ dispatch し、worker の質問と reconciler の escalation を捌く常駐 orchestrator。
---

# orchestrator

herdr session 内で、宣言された issue 置き場の open issue から着手可能なものを選び、**規定セッション数まで** Claude Code セッションを起動する orchestrator。展開先は**この skill を呼び出したセッションが居る herdr workspace の分割 pane**。

**issue 置き場 (どこの issue を読むか)・CL 置き場 (PR / MR がどこに出るか)・実装 repo (どの clone で作業させるか) は別軸。** 置き場は project に 1 つずつで、**宣言を解決するのは daemon** — 観測系 tool は宣言どおりの置き場を読むので、識別子を自分で引いて渡す必要は無い。実装 repo は issue ごとの判断で、cwd の clone とは限らない。

**このセッション自身はポーリングしない。届いたもので動き、それ以外の時間は turn を終えて待つ。** user の唯一の窓口として常駐し、worker の質問と reconciler の nudge を受けて判断する。稼働 Session も候補も 0 になっても終了せず、報告して待機する。

## 判断の分担

| 層 | 持ち物 | 持たないもの |
|---|---|---|
| **reconciler** (LLM を持たない常駐 daemon) | 周期観測・状態機械の評価・機械的に決まる遷移の記帳・escalation の発行と打ち切り・dashboard の serve | 判断 (候補選定・spawn・駐機 / 返却 / 回収)。**tracker への書き込みも一切行わない** |
| MCP server (`mcp__plugin_swat-skills_dispatch-v2__<tool>`) | daemon への薄い client。台帳の読み書き口・観測の読み出し口・稼働 clone の解決 | ポリシー。daemon の cache に無い観測 |
| **worker** (pane の独立セッション) | 担当 issue の作業そのもの。**質問だけ**を orchestrator へ送り、終了直前に台帳へ自己申告する | CL 到達・完了の通知 (検知は reconciler の外形観測) |
| **このセッション** (orchestrator) | 候補選定・起動 prompt の文面・escalation の解釈・回収 / 駐機 / 返却の判断・tracker への書き込み・user との対話 | 周期的な外形監視 (reconciler が持つ) |

**daemon はポリシーを持たない。** 候補を選ばず、回収すべきかを返さず、escalation の解消手段も示さない。判断を返り値に探しに行かず、観測を材料に自分で決める。

**worker を独立プロセス (pane) に置くのは、orchestrator を再起動しても死なないため。** pane は通常見ない緊急ハッチで、**日常の連絡経路はメッセージ**。

**並列化はすでに pane (worker) の形で表現してある。** 観測 (`wo_list` / `observe_*`)・台帳への記帳・回収と駐機の判断は、このセッションが自分の手で行う — Task subagent へ委任しない。台帳の書き手が 1 つでなくなると、どの観測でその phase にしたかを次セッションの自分が再現できなくなる。

**worker / user の領分には手を出さない** — 実装・調査・triage の中身、issue を close する判断、CL の作成・merge、conflict 解消そのもの (このセッションが持つのは起動まで)、既存 issue の label / status 遷移 (自分で起票した issue を着手可の印まで運ぶのは E の起票節が持つ)。

台帳は **project に 1 つ**で永続。実装 repo が複数あっても分かれない — project key は clone の remote から導かれるので、別 clone の worktree で走る worker の `wo_report_outcome` も同じ台帳へ着地する。**WorkOrder の phase は意図しか持たない**: assigned ⇄ parked → completed | released | abandoned。`assigned` / `parked` が非終端で、`completed` / `released` / `abandoned` が終端。Session の生死も作業ツリーの有無も phase には混ぜない (どちらも導出値)。候補プールは台帳に入れない — 何が着手可かは毎回 tracker の観測から読み直す。**メッセージは揮発する** (配信保証が無く、再起動で消える)。真実源は台帳と tracker で、メッセージは速い経路にすぎない。

## args

`/orchestrator [max]` — max は同時稼働セッション数の上限 (省略時 3)。**daemon は max を知らない**ので、空き slot は非終端 WorkOrder のうち `alive` な Session を持つものの数と max から自分で数える。

## 前提

**前提の成否は `mcp__plugin_swat-skills_dispatch-v2__*` が 1 つでも呼べるかだけで決まる。** 呼べなければそこで止めて user へ依頼する — `/mcp` で `plugin:swat-skills:dispatch-v2` が connected かを確かめ、無ければ `/reload-plugins` かセッションの再起動を頼む。

**接続失敗の報告に `dispatch-v2` (plugin 接頭辞の無い bare 名) が挙がっていても、それは前提不成立ではない。** plugin root 自体を cwd にしたセッションでは同じ `.mcp.json` が plugin scope と project scope の二役で読まれ、project scope 側は `${CLAUDE_PLUGIN_ROOT}` が展開されないので必ず起動に失敗する。**この失敗は本 repo での定常状態**で、plugin scope 側は無関係に生きている。判定は上の完全名で行う。

Session 系 tool が前提不成立で失敗したら、error 文言に応じて user へ依頼して終了する (dispatch は不能だが、fail-closed なので誤 dispatch には進まない):

| error | 依頼 |
|---|---|
| `HERDR_ENV=1 でない` | herdr session 内で Claude Code を起動し直してもらう (herdr 以外の terminal multiplexer は非対応) |
| `claude: current` が無い | `herdr integration install claude` の実行を依頼する。hook は session identity を herdr に報告して pane と Claude session を 1:1 対応させる。無いと agent の検出が効かず、`alive` と `ended` の見分けが壊れる |
| `herdr status が失敗 (socket に届かない)` | herdr daemon の起動を依頼する |
| `anchor_handle / anchor_workspace が無い` (`session_spawn` が 400) | herdr session 内の pane から起動し直してもらう。**worker の pane は割り元 (このセッションの pane) の隣に開く**ので、割り元を名乗れないと呼び出し元と無関係な workspace へ開いてしまう (ADR 0065) |
| `CLAUDE_CODE_MESSAGING_SOCKET が未設定` | 現行 binary で**新規起動**した Claude Code セッションから実行し直してもらう。旧 binary の resume セッションは送信できても受信できず、worker の質問が誰にも届かない |

Remote Control (`/remote-control`) の有効化を user に薦める (前提ではない)。有効なら質問中継と承認を claude.ai web / mobile から返せる。無効でも herdr pane の terminal で同じ操作ができるので、断られたらそのまま進む。

model / effort はセッション起動時 (`claude --model` / `--effort`) に決まっている前提で動く。frontmatter では指定しない — skill の上書きは active な turn にしか効かず、常駐の大半を占める受信・起床で始まる turn は session の値で走る。

## プロトコル

```
起動 /orchestrator [max]
  │
  ├─ 0. 自 pane 名  observe_sessions (同じ台帳の書き手が他に居ないこと)
  │               → herdr pane rename
  ├─ A. 再入       wo_list → observe_sessions / observe_cls  前回の意図と現実の差を確認
  │               → 生きている worker へ初回コンタクトを送り直す
  ├─ B. inbox      inbox_read → 1 件ずつ判断 → inbox_ack
  ├─ C. 候補選定    observe_candidates (宣言された置き場) issue の実態から選ぶ
  ├─ D. 起動       実装 repo と clone を決める
  │               → wo_create → session_spawn → 初回コンタクト送信
  │
  └─ E. 常駐 ◀──────────────────────────────────────┐
        turn を終えて待つ (自分ではポーリングしない)   │
        worker の質問 / daemon の空 nudge /           │
        user の指示で起床                             │
        → inbox_read (nudge でなくても毎回)           │
        → observe_* で裏取り → 中継 / 回収 / 駐機     │
        → worktree_sweep / worktree_tidy → C/D ──────┘
        └─ 稼働 0 かつ候補 0 でも終了しない。報告して待機する
```

### 0. 自 pane を `orchestrator` に rename する

pane が増えたときに人間が herdr 画面で役 (`orchestrator` / issue slug) を見分けられるようにする。**A より先に通す。**

守る不変条件は **1 つの台帳に書き手を 2 つ作らないこと**。台帳は project に 1 つなので、**別 project の orchestrator とは同じマシンで共存してよい**。

1. `wo_list` で非終端 WorkOrder を引き、`observe_sessions` で自 workspace の Session を観測する。**ここに映るのは daemon が知る Session と、台帳外の実行単位 (`orphan_sessions`) だけ**で、同一マシンの別セッション (別 workspace / Remote Control / cloud) の 2 体目は手順 0 では検知できない — その残余は `ListAgents` を引いて報告へ載せ、人へ渡す
2. **止まる根拠は「自分が起こしていない `alive` な Session が台帳に居ること」だけ。** `ListAgents` の peer session は、名前が重なっても止める根拠にならない — `ListAgents` は担当 project を返さず、閉じた session の registry 残骸も生きた peer と同じ形で並ぶ。名前を根拠に止めると、閉じた session ひとつで dispatch が丸ごと止まる。止まったときは pane_id と WorkOrder を報告し、どちらを残すかを user に決めてもらう
3. 止まる根拠が出なければ Bash で `herdr pane rename $HERDR_PANE_ID orchestrator` を実行する。**`$HERDR_PANE_ID` が空なら実行しない** (引数が 1 つ足りない呼び出しになる)。**自 pane が既に `orchestrator` label なら rename は不要**だが、その事実を報告に書く (書かないと手順を通したのか飛ばしたのかが報告から見えない)

- **止まったときは、どちらを残すかを user に決めてもらってから起動し直す。** 窓口は台帳ごとに 1 つで、残さないほうの pane は user が閉じる — **このセッションからは閉じない** (進行中の対話を捨てる不可逆操作になる)
- rename コマンド自体が失敗しても dispatch を止めない (pane 名は人間の見分けのための印で、宛先伝達の経路ではない)。失敗を報告に載せる

### A. 再入 (wo_list → observe_*)

- `wo_list(phases: ["assigned", "parked"])` の返す WorkOrder が前回までの意図。各 WorkOrder の `note` は前セッションの自分からの引き継ぎで、機械はパースしない。**引数なしの `wo_list` は全件返す**ので、系譜を読むとき以外は phase で絞る
- `observe_sessions` が Session の lifecycle (事実) と activity (観測) を返す。lifecycle は `requested` → `starting` → `alive` → `ended` で、`ended` の理由は `exited` (handle は在るが agent が居ない) / `gone` (handle ごと消えた) / `closed_by_orchestrator` (こちらが閉じた) / `launch_error` (起動そのものが失敗した) の 4 値。activity は `alive` の間だけの中立 3 値 (`running` / `idle` / `blocked`) で、**中立値へ写せなかった生値は `activity` が null のまま `activity_raw` に残る** — 未分類を `running` と読まない
- `observe_cls` が CL の status (`conflict` / `merged` / `checking` / `open` / `closed`) と、issue → CL の紐づき (`cl_links`) を返す。**`role` が `closes` の CL だけが完了の根拠**で、`mention` は混ぜない
- **紐づきの源は issue 置き場で違う**。GitHub / GitLab 置き場は CL 本文の closing reference から機械が引く。**Jira 置き場は引けない**ので、`wo_record_cl` で台帳へ記録したものが源になる — 記録するのは自分 (下記 E の「Jira 置き場で CL を台帳へ記録する」)。記録が 0 件の WorkOrder は `cl_links` にその issue の entry が **`links: []` で現れる** (map 全体の `null` は「その project をまだ 1 度も観測していない」の意味で、別物)。機械遷移は走らないので**非終端のまま残る**が、escalation は他の置き場と同じに上がる
- **「見ていない」を「無かった」と読まない**: `observe_candidates` / `observe_cls` は**未観測を `null`、観測して 0 件を `[]`** で返し分ける。`null` のときは `reason` が理由を持つ。`unresolved_threads` も同じ線で、`null` を「指摘が無い」と読まない
- 応答はすべて daemon の cache から返るので、**鮮度は `as_of` / `observed_at` で読む**。tick が伸びている project では個々の WorkOrder の実質観測周期が polling 既定より長くなる
- **`alive` な Session を持つ WorkOrder には、初回コンタクトを送り直す** (手順は D の同名の節)。worker が持っている返信先は前回の orchestrator プロセスの UDS アドレスで、プロセスが変わった時点で死んでいる — 送り直さないと、その worker の質問は以後どこにも届かない (送っても worker 側にエラーは返らない)
- **v2 は claim 信号を tracker へ立てない** (下記「v2 が持たない tracker 作用」)。二重 dispatch を塞いでいるのは `wo_create` が同一 issue の 2 本目の非終端 WorkOrder を拒むことだけで、**その保護はこの台帳の中にしか効かない**。別マシンの orchestrator や人間が同じ issue に着手していないかは、候補を選ぶときに issue 本文とコメントで自分で確かめる

### B. inbox を空にする

**escalation はここでしか読めない。** daemon が撃つ nudge は「見に来い」だけの空の合図で中身を運ばないので、nudge を取りこぼしても `inbox_read` を呼べば全件揃う。各 escalation は発火した rule の `condition` (条件を 1 行で書いたもの)・`evidence` (観測事実)・`wo_id`・`attempts`・`permanent` を持つ。

**読んだら 1 件ずつ判断し、対応を決めたら `inbox_ack` で閉じる。** ack が再送の打ち切り契機で、ack しない限り同じ dedup key が最大 3 回まで積み直される。**ただし `permanent` が真の escalation は配送が最初の 1 回きり**で、`attempts` は 1 のまま動かない (再試行では直らない事象なので再送に乗せない)。

| rule id | 何が起きた | このセッションがすること |
|---|---|---|
| `park_point` | Session idle ∧ closes CL open (mergeable) | 駐機の判定指針 (E) へ通す |
| `merged_but_alive` | closes CL merged ∧ Session alive | worker がまだ動いているので、降ろしてよいか判断する。降ろすなら `session_close` → `completed` |
| `cl_conflict` | closes CL conflict | 解消を起動する (駐機ツリーへ再入)。**駐機しない** — 稼働中 worker を閉じると解消の起動先が消える |
| `parked_review_pending` | parked ∧ 未解決 thread ≥ 1 | 対応させるかを判断し、させるなら駐機ツリーへ再入する |
| `session_died_empty` | issue open ∧ Session が `exited` / `gone` / `launch_error` ∧ closes CL 無し | 再 spawn するか、`released` で候補へ返すかを決める |
| `session_blocked` | activity が `blocked` | permission ダイアログ待ちなのでメッセージでは解けない。pane に入って解いてもらうよう user へ依頼する |
| `issue_closed_alive` | issue closed ∧ Session alive | 作業ごと不要になった可能性がある。worker へ確かめてから降ろす |
| `candidate_appeared` | 候補集合に新規が現れた | **C の候補選定を自分で通し直す** — escalation が運ぶのは差分の存在だけで、着手可の判定・整列・除外はこちらのポリシー |
| `store_unreachable` | 同一 store の観測失敗が 3 連続 | 前提不成立 (前提の表) なら user へ依頼。それ以外は認証・network を疑って報告する。**「観測できていない」を「変化が無かった」と読まない** |
| `orphan_resources` | 台帳外の worktree を検出 | `worktree_sweep` の報告を読み、回収は repo 側の手順へ返す (**daemon は消さない**) |
| `deploy_degraded` | pull 失敗 / pull 後 dirty / ff 不可 | 下記「deploy が縮退したとき」 |
| `worktree_vanished` | 非終端 WorkOrder の作業ツリーが git の登録に無い (repo の外から消された — `git tidy` 等) | worker が居るなら `session_close` してから、再 spawn か `released` で候補へ返すかを決める。evidence の `present` が偽なら再 spawn が同じ path へ作り直す。真なら残骸が残っていて `session_spawn` は作り直しを拒むので、先に repo 側の手順で path を空にする (ADR 0048)。branch に残っていない成果は失われている |

**catalog に無い事象は上がらない。** したがって **inbox が空でも「判断待ちが無い」とは限らない**。上げないと決めた沈黙が 2 つあるので、A と E の照合で自分で拾う:

- **closes CL が merged なのに Session が居ない WorkOrder** (既定ブランチ以外への merge 等)。`wo_list` の非終端 × `observe_cls` の `merged` で当たる
- **成果を残して死んだ Session** (`ended` かつ closes CL 在り ∧ issue open)。`observe_sessions` の `ended` × `observe_cls` の紐づきで当たる

### C. 候補選定

`observe_candidates` は宣言 config が指す issue 置き場の母集団を返すだけで、**どれが着手可かは判断しない**。

- **`candidates` が `null` なら候補選定を行わず、その起床の dispatch を止めて user へ報告する。** `reason` が理由を持ち、**理由は次の一手を決める**: 宣言が無い / 壊れているなら人が置くまで永久に埋まらず、観測未成功なら次の tick で埋まりうる。`[]` は「観測して候補が 0 件」で、こちらは正常形
- **確信が持てない issue は dispatch しない側に倒す** (誤 dispatch のコストは、補充が 1 回遅れるコストより高い)
- 除外する: **台帳に非終端 WorkOrder がある issue** (`wo_list` で引く)。**終端の WorkOrder しか無い issue は再 dispatch 可**で、直前に `released` へ送った issue もここに戻る。**終端 WorkOrder の `outcome` は除外根拠にならない** — 「作業不要と判断した」で終わった issue も、open で着手可の印が付いていれば候補に戻る。dispatch しないと決めたなら候補として報告し、close / label 剥がしを user へ返す (放置すると毎サイクル浮上する)
- **assignee では除外しない** — 人が担当に付いた着手可 issue も dispatch 対象で、assignee は人の担当だけを意味する
- **issue 本文が要るなら `gh issue view <N> --json body -q .body` で引く** (`observe_candidates` が返すのは title / labels / state で、本文は含まない)。**ループで回さず 1 呼び出し 1 issue で引く** (埋め込み・連結の可否は同梱の PreToolUse hook が発火時に案内する)
- **blocker は自分で確かめる** — daemon は blocker 検査を持たない。issue 本文の `Blocked by` と、その issue の state を `gh issue view` で読む。読めなければ blocked 扱いで skip する
- 整列も判断のうち — 完成に近い段階から slot を埋めると、同じ slot 数でも成果が出る速度が上がる

### D. 起動

**`wo_create` → `session_spawn` → 初回コンタクト送信** の順で通す。

- `wo_create(issue_ref, note)` が WorkOrder を `assigned` で切る。同じ issue に非終端の WorkOrder が既にあれば失敗する (**再着手は前の WorkOrder を終端へ送ってから**)。`note` には「なぜ今この issue に着手するか」を書く
- `session_spawn(wo_id, prompt, model)` が作業ツリーの用意と pane 起動をまとめて行う。**作業ツリー名も pane label も daemon が issue ref から導く**ので、綴りをこちらで決めない (v1 で綴りを外すと保護も回収も効かなくなった経路が構造ごと消えた)
- **作業ツリーは WorkOrder の持ち物**で、Session が終わっても残る。同じ WorkOrder への再 spawn は同じツリーを引き継ぐ — 駐機ツリーへの再入も `session_spawn` を撃ち直すだけで、別の起動パターンは無い
- **起動に失敗しても WorkOrder は phase を変えない。** Session が `ended(launch_error)` として残るので、そのまま再発行できる。**2 度目も失敗した issue は自動再試行を打ち切り**、報告に載せて user の判断へ返す (常駐なので「このセッション中は skip」は恒久 skip と同義になる)
- **worker の pane は呼び出し元 (このセッション) の workspace に開く。** 割り元はこのセッションの pane で、`session_spawn` はそれを名乗れないと起動せずに落ちる (`割り元 (anchor) を名乗らない` を含む失敗)。この失敗が出るのは herdr session の外から dispatch しているときなので、`daemon_restart` では直らない — herdr session の中から呼び直す
- **どの issue の spawn も同じ理由 (割り元の pane が見つからない) で落ちるなら、打ち切る前に `daemon_restart` を 1 回撃つ。** 走行中の daemon が古いと、この修正が載っていない (継承した割り元へ縮退する) 版のまま動いている。**復旧の確認は `session_spawn` が live な handle を返すこと**で行う (daemon が戻ったことは復旧の証拠にならない)。影響はマシン全体に及ぶ (全 project の観測が数秒止まる。台帳と worker は無事) ので、個別の起動失敗には撃たない

#### 実装 repo の選定と clone の解決

**どの clone で実装するかは issue ごとにこちらが決める。** daemon が使う稼働 clone は MCP server の cwd から解決されるので、**別 clone で実装させたいなら、その clone を cwd にした Claude Code セッションから orchestrator を起こす**。

1. **実装 repo を読む** — issue 本文の明示・label・`docs/agents/domain.md` 等の宣言 doc から。読み取れない issue は cwd の clone で実装する (置き場がメイン repo なら通常これ)
2. **cwd の clone と別の repo が要ると読めたら dispatch を見送る** — `git clone` しない (どこに置くべきかを判断できない)。報告に「別 clone が要るので見送った issue」として載せ、その clone を cwd にした orchestrator を別に起こしてもらう

#### 初回コンタクト (返信先を渡す)

`session_spawn` は起動セッションに issue slug と同じ名前を付ける。**spawn 直後に orchestrator から SendMessage を 1 通送る** — 相手はその `from` 属性 (UDS アドレス) をそのまま返信先に使う。spawn 時と再入時の両方で送る。

**相手側から orchestrator を名前で発見する経路は無い。** `ListAgents` 上では自分も `orchestrator` を名乗るが、この名前は project をまたいで重複するので宛先として解決できない。この 1 通が着弾しない worker は質問を一度も送れないまま安全網 (次の照合) 送りになる。fire-and-forget にせず、送れたことを確認してから次へ進む:

- 宛先は pane label と同じ名前。**bare name は 1 体だけに一致すれば通る**ので、通ったのは成功であって疑わない。拒否されるのは (1) 同名の peer が複数居る (2) まだ ListAgents に載っていない、のどちらか。エラーが正しい `name [ref]` 表記を提示するので、それで再送すれば通る。**同名が複数のときは活動時刻が最も新しいものを選ぶ** — 古い方へ送ると質問がそのセッションに刺さったまま届かず、送信側にエラーは返らない。**この拒否を起動失敗と読んで WorkOrder を巻き戻さない** (pane もセッションも生きている)
- ListAgents への登録は起動から十数秒かかる。見つからない = 死んだ、ではない。**待たずに次の候補の起動へ進み、その後で引き直す**
- それでも着弾を確認できないまま先へ進むなら、`note` と報告に「質問が来ない前提の相手」として残す
- 文面には issue 番号と `wo_id` を入れる (worker が返信に番号を添えられ、自己申告の宛先も持てる)
- `SendMessage` / `ListAgents` が tool 一覧に無ければ ToolSearch で schema を取ってから使う

#### worker の spawn prompt 契約

daemon は prompt を一切解釈しない。worker が自走し、orchestrator が後で取りまとめられるだけの情報は全部文面に入れる。次の各点は欠けるとどこかが壊れる:

| 文面に入れるもの | 欠けたときに壊れるもの |
|---|---|
| issue 番号 (と何をする作業か) と **`wo_create` が返した `wo_id`** | worker が自分の担当を特定できず、自己申告の宛先も持てない |
| **質問だけを SendMessage で orchestrator へ送る契約** — 人間の判断が要る質問が出たら送って turn を終える。宛先は最初に届いたメッセージの `from` をそのまま使う。**CL 到達・完了は送らせない** (外形観測で拾うので、送らせると「申告があったから終わったはず」という読みを誘発する) | 質問が誰にも届かず、worker が pane 内で止まる |
| **AskUserQuestion を使わないという禁止** — 質問は SendMessage で送って turn を終え、idle で回答を待つ | worker が pane 内で応答待ちに入り、pane を見ていない user には質問の存在が見えない (`blocked` のまま slot を塞ぐ) |
| PR 本文に closing reference を必ず含めるという要求。**CL を出す repo と issue 置き場が別なら `Closes <owner>/<置き場 repo>#<N>` の cross-repo 表記を逐語で指定する** (同 repo なら `Closes #<N>`) | 紐づきが `mention` 止まりになり、駐機判定と merge 検知が「自分の CL を持つ issue」を見分けられない。cross-repo で `Closes #<N>` と書くと**その関連 repo 内の同番号 issue**を指し、無関係な issue を巻き込みつつ本来の issue は紐づかない |
| **Jira 置き場では、CL 番号と CL 置き場の repo を `outcome` に逐語で書かせる** (closing reference は tracker をまたげないので、この申告が紐づきの唯一の種になる) | orchestrator が `wo_record_cl` に渡す値を持てず、その WorkOrder の merge が永久に検知されない |
| 終了直前に `wo_report_outcome` (`wo_id` / `outcome` / `summary`) で自己申告する契約 | 「なぜ終わったか」が台帳に残らず、正常終了と途中死を外形観測で区別できない。**申告は入力であって真実源ではない** — 完了判定は観測で行う |
| 再入では新規 CL を作らず既存 CL の branch へ push する、という明示 | 再入セッションが 2 本目の CL を作る |
| **レビュー指摘への対応で再入させるときの 3 点** — その thread へ返信し、対応を CL へ反映したうえで、自分で resolve する。**同意できない指摘は CL 上で反論せず**、SendMessage で orchestrator へ上げる (質問と同じ経路)。**v2 には thread 操作の tool が無いので `gh api` / `glab` の綴りを逐語で渡す** (下記「v2 が持たない tracker 作用」) | 閉じないと `parked_review_pending` が毎 tick 立ち続ける。CL 上で反論すると user の窓口が 2 つになり、user が知らないまま CL 上で議論が進む |
| 着手前に **`git pull --no-rebase origin main`** で最新を取り込む、という明示 (コマンドごと書く) | worker が古い main を土台に作業し、merge 時に conflict する |
| **`gh` / `glab` は 1 呼び出しにつき top-level 断片の先頭に置いて単体で実行する**という指定 (`N=$(gh …)` のように埋め込むと `sandbox.excludedCommands` に照合されず起動に失敗する) | worker の gh がすべて起動失敗する。**失敗が capability の欠落に見える**ので、worker は「gh が使えない」と判断して回避に走る |
| **作業に着手する前 (最初の Edit / Write より前) に、作業種別に対応する playbook 1 本と原則索引の絶対 path を Read で開かせる契約** — `playbook-implementation` / `playbook-research` / `playbook-plan-verification` のうち種別に当たる 1 本と `principle-index` の `SKILL.md` を並べ、「playbook の step を逐語で todolist へ写す」「索引から今回の作業に当たる leaf を Read する」と書く。path は本 SKILL.md 本文の `${CLAUDE_SKILL_DIR}/../../knowledge/<name>/SKILL.md` が**ロード時に展開された実 path** で渡す | 原則をセッション開始時に注入する経路は存在しないので、この行が無いと playbook も leaf も worker へ届かない。**spawn prompt が唯一の届け方**。相対 path や `${` を残した文面を渡すと、別 clone の作業ツリーで走る worker からは 1 本も解決できず、原則なしの作業が silent に成立する |
| **playbook と索引は Read で開かせ、review skill は Skill tool で invoke させる** という使い分けの明示 | playbook 3 本は `disable-model-invocation: true` を持ち、Skill tool の invoke を拒否される — 「invoke せよ」と書いた契約は空振りし、worker は skill が壊れていると読んで原則なしで先へ進む |
| **CL 到達前に `swat-skills:two-axis-review` を Skill tool で invoke する契約**。回数と 2 回目の中身は `playbook-implementation` の review step が正本で、playbook を Read させる契約 (上記) で worker へ届く | 原則の遵守が CL に現れず、「skill を届けたか」しか残らない |
| **打ち切り時点で open な Act on と、反証で落とした Dismissed を具体物ごと CL 説明文へ載せる要求** | worker = 作業した本人が自分の指摘を落とす構造の唯一の歯止め (人が review で読んで覆す) が消える |

playbook と索引を埋める 2 行は次の形になる (`${CLAUDE_SKILL_DIR}` は本 SKILL.md をロードした時点で skill ディレクトリの絶対 path へ置換済みなので、**その展開後の path をそのまま写す**)。**1 行目の playbook 名は種別に当たる 1 本へ差し替える**:

```
${CLAUDE_SKILL_DIR}/../../knowledge/playbook-implementation/SKILL.md を Read し、step を逐語で todolist へ写す (飛ばす step には skip: <理由> を残す)
${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md を Read し、今回の作業に適用条件が当たる leaf を Read する
```

- **索引は毎回埋め、playbook は当たる種別が読めたときだけ埋める。** 3 種別のどれとも読めなかった作業は索引が拾う (当たらない playbook を渡すと、作業と無関係な step が worker の todolist を占める)
- **review 段を 2 回通したのは worker 自身の終端であって、台帳の `completed` ではない。** 完了判定は closes CL の merge が正。worker には CL 到達までを書き、自分で終端を名乗らせない
- **作業ツリーの外にある実体を触る acceptance criteria は worker に渡さない。** worker の sandbox は clone root への書き込みを構造的に拒否する。該当する AC を含む issue は、その項目だけ user の領分として切り分けてから dispatch し、報告に「user に残る作業」として載せる
- **worker の sandbox が拒否した書き込みは、このセッションが肩代わりしない** — user の承認があってもやらない。その clone に外部の自動 commit 機構が居ると変更が main へ載り、同じ file を触る駐機中の CL を conflict にする
- `--no-rebase` にするのは worker が既に commit していると `--ff-only` が成立しないから (merge commit ができる点は許容する)
- `outcome` の語彙を daemon は検証しない。orchestrator が読んで判断する材料なので、**この session が読み分けられる語彙を prompt 側で指定する** (最低限「CL に到達した」「作業不要と判断した」「人手が要って停止した」の 3 系統 + 理由)
- 作業種別 (実装 / 調査 / 計画検証) は issue の label 体系と本文から読み、**同じ読みで `model` と、prompt に埋める playbook を決める** (種別と playbook は 1:1)。**`session_spawn` は `effort` を取らない** — 起動セッションの effort は agent の既定で決まる (`mcp/dispatch-v2/` に `effort` の綴りは 1 箇所も無い)。実装系を `medium` に固定していた v1 の運用は v2 では表現できない (振り分けたいなら、必要性を実測してから issue にする — README §1)

### E. 常駐 (イベント処理)

**このセッションでポーリングループを組まない。タイマーを置かない。** 周期観測は reconciler の担当で、こちらは届いたものを処理する。補充と照合を済ませたら turn を終えて待つ — idle なら受信で新しい turn が始まり、tool 実行中なら tool call の合間に読まれる。キューは会話そのものなので、届いた順に 1 件ずつ処理すれば足りる。

**起床したら、届いたものを処理する前に `inbox_read` を撃つ** (nudge で起きたときに限らない)。nudge は空の合図で配送保証を持たないので、質問や user の指示で起きた回にも判断待ちが溜まっている。

**user への確認は AskUserQuestion で行わない。** 設問は報告文に書いて turn を終える。AskUserQuestion は tool 実行中の扱いになり、**回答が返るまで worker の質問も nudge も配送されない**。同じ理由で、**判断を外部へ出して長く待つ tool 呼び出しも起床の処理中は避ける**。

届いたものごとの処理:

| 届くもの | すること |
|---|---|
| worker からの質問 | **issue 番号を冠して user へ逐語で中継する** (並走する worker が複数居るので、番号が無いと user はどの作業の話か分からない)。どの issue のどのアドレスから来たかの対応を自分で保持し、user の回答を逐語でそのアドレスへ返す |
| daemon の空 nudge | `inbox_read` → B の表で 1 件ずつ処理 → `inbox_ack` |
| user からの指示 | 該当 worker への送信 (下記の規範) / 状況照会 / max の変更 / 終了 |
| user からの作業依頼 (対話が本体でないもの) | **issue を起票してから通常経路へ乗せる** (下記「user から直接頼まれた作業」)。issue-less の spawn はしない |
| user からの「人間との多ターン対話が本体である作業」の依頼 (issue の triage 判断・grill 系 skill 等) | **自分で処理しない。** user に自セッションで対話 skill (`/swat-skills:issue-triage` / grill 系) を呼ぶよう、依頼文を添えて案内する — 自分で受けると escalation と worker の質問が対話に割り込み、対話の全文が配車判断のコンテキストを希釈する。一問一答で済む照会は対象外で、その場で答える |

イベントを 1 件処理したら、続けて回収と補充 (C / D) を回してから待ちに戻る。**稼働中の Session が max 未満なら `observe_candidates` は必須** — 「前回から変わっていないはず」を理由に飛ばさない。飛ばすと、その間に着手可になった issue は user が声をかけるまで拾われず、slot が空のまま遊ぶ。

#### 回収 / 駐機の判定指針

判断の入力は issue state / Session の lifecycle と activity / closes CL の status。**先に通す原則: closes CL が open / checking / conflict / merged のいずれかで在る WorkOrder は `released` へ送らない** — `released` は「まだ着手可」の意味なので候補プールへ戻り、同じ issue に 2 本目の CL を作る dispatch が走る。CL を観測できていない (`null`) WorkOrder も「不明」として据え置く。

| 観測 | 判断 |
|---|---|
| closes CL が `merged` ∧ Session 不在 | `completed` へ遷移させ、**issue がまだ閉じていないなら下記「merge 後に issue を閉じる」を通してから** `worktree_tidy` に回収させる。**daemon が先に記帳していることがある** (既に `completed` なら遷移は不要で、close の判定から先を進める) |
| Session が `ended` (`exited` / `gone` / `launch_error`)・closes CL 無し・**自己申告も無い** (途中死候補) | `released` へ遷移させて候補へ返す |
| worker が「作業不要」「検証のみ完了」を申告・closes CL 無し・作業ツリー clean | `session_close` → `abandoned`。**`released` は採らない** (成果の無い未着手として候補プールへ戻り、同じ作業が再 dispatch される)。issue は open のまま残るので、**close 要否は `note` と報告に書いて user へ返す**。**申告を完了の証拠として扱う唯一の経路**なので、申告の逐語と作業ツリー clean の観測を `note` に両方残す |
| Session が `ended`・closes CL 在り | **駐機**: `parked` へ遷移。作業ツリーは残す |
| activity が `idle`・closes CL が `open` | **駐機**: `session_close` → `parked`。session close はそこまでの対話を捨てる不可逆操作なので、この条件は狭く取る |
| closes CL が `conflict` | 解消を起動する (`session_spawn` で駐機ツリーへ再入)。**駐機しない** — 稼働中 worker を閉じると解消の起動先が消える |
| `activity_raw` が `blocked` / 未分類 (`activity` が null) | 触らない。契約どおりなら質問はメッセージで届くので、残る `blocked` は permission ダイアログ待ち (メッセージでは答えられない)。未分類は判定不能 |
| closes CL が `checking` | 次に触るときに再観測する (「conflict 無し」と読み替えない) |
| 紐づきが `mention` だけ | 別 issue の CL が本文で番号に言及しただけでありうる。駐機・conflict 対応の根拠にせず、CL 番号を報告に添えて user 判断へ残す。**自分の worker の CL が `mention` 止まりで slot を塞いでいるなら** CL 本文に closing reference を足させれば次に触るときに駐機される |

**完了判定は closes CL の merge が正で、worker のメッセージでは完了扱いにしない** — 駐機は「slot を降ろす」操作であって「終わった」という判定ではない。**例外は「成果物が出ない作業 (調査・検証)」の 1 経路だけ** (上の表)。**`outcome` の自己申告は各 worker が `wo_report_outcome` で残すもので、代理申告も推測での補完もしない** — 埋めると「途中死を検知できる」という契約が壊れる。

#### Jira 置き場で CL を台帳へ記録する

**Jira 置き場の project でだけ通す手順。** GitHub / GitLab 置き場では機械が closing reference から引くので何もしない。

- 契機は worker の `wo_report_outcome`。`outcome` に載った CL 番号と CL 置き場の repo を読み、`wo_record_cl(wo_id, cl_ref, repo, role: "closes")` を撃つ。`cl_ref` は中立形式 (`glab!12`)、`repo` は**その CL が居る置き場** (issue 置き場の識別子ではない)
- **撃たないと、その WorkOrder は永久に非終端のまま残る。** 紐づきの源が台帳しか無いので、記録が 0 件だと機械遷移が走らない (`cl_links` には `links: []` で現れる)。**誤って `abandoned` にはならない**が、slot も作業ツリーも解放されない。escalation は落ちないので、worker の死や駐機は inbox から普段どおり届く
- 記録は機械遷移の根拠になる。**誤った CL ref を記録したら `wo_forget_cl` で外す** — 放置すると無関係な CL の merge でその WorkOrder が `completed` へ落ちる (終端は巻き戻らない)
- 同じ `cl_ref` の再記録は置換なので、`repo` / `role` の訂正はそのまま撃ち直す

**駐機を終端 phase へ送らない。** 駐機は `parked` のまま台帳に残す — `parked → assigned` は駐機ツリーへの再入で、そのまま作業が続く。早く終端へ落とすと、その issue の conflict も merge も以後拾われなくなる。**終端は巻き戻らない**: `assigned → parked` は合法だが、`completed → assigned` は非合法。誤分類の訂正は `wo_annotate` + 新しい WorkOrder で表す。

**`worktree_sweep` は何も削除しない。** 回収資格 (`candidates[].eligible` と `blockers`) と、台帳が知らない資源 (`orphan_worktrees` / `orphan_sessions`)、判定できなかったツリー (`unverified_worktrees`)、終わったのに実行単位が残っている Session (`stranded_sessions`) を報告するだけ。**回収は `worktree_tidy(wo_id)` の名指し 1 回**で、資格 (WorkOrder が終端 ∧ 非終端 Session が居ない ∧ ツリーが clean ∧ lock 無し) を満たさなければ拒む。**`unverified_worktrees` を回収対象に数えない** — 権限で読めないだけの生きたツリーを消させないため。**消えたツリーは戻らない**ので、dirty で回収できないものは報告して user に返す。

`note` には**何を観測してその phase にしたか / 次に何を待っているか**を書く。次のイベント処理・次セッションの自分が判断を再現できることが唯一の基準。**phase が動かないまま状況だけが動いたら `wo_annotate` で `note` を更新する** (遷移を伴う更新は `wo_transition` の `note`)。**`wo_annotate` は置換なので、既存 note を全部書いた上に足す**。

**E で user へ返す設問すべて — 中継した worker の質問・成果の無い終端と駐機 CL の close 要否・別 clone が要って見送った issue — に「回答が無ければ <既定の進め方> で進む」を併記する。** 既定値は**取り消せる側に倒す** — issue を open のまま残して slot を補充する / 駐機 CL を閉じずに `parked` のまま置く / 台帳を動かさず次の起床へ回す。取り消せない側を既定値にすると、無回答がそのまま不可逆な外部書き込みになる。台帳に WorkOrder がある設問は、設問の本文と既定値を `wo_annotate` で `note` にも残す — 報告文は自分の再起動で消えるので、note に無い設問は「何を待っていたか」ごと失われる。

#### deploy が縮退したとき

**merge は deploy ではない。** plugin 実体は cache コピーを持たず main チェックアウトの working tree を in-place で読むので、`origin/main` が正しくても**ローカル main が古ければ runtime は古いまま動き続ける**。

v2 ではこの前進が daemon の built-in 機械作用 `deploy_ff_only` (稼働 clone を `git pull --ff-only` で前進させる。既定 on) になっている。**このセッションは pull しない** — 縮退したときに `deploy_degraded` が上がるので、それを読んで判断する側に回る。

- **pull 後にツリーが dirty / ff 不可 なら、原因を推測して `--force` や `stash` へ逃げない。** 逐語で報告して user の判断へ返す。**半適用の deploy は綺麗に古いままより悪い**
- 恒久的な不成立 (git repo でない / upstream 未設定 / detached HEAD) は 1 度きりしか上がらない。**再送が来ないことを「直った」と読まない**
- deploy の失敗と dispatch の完了は別事象なので、終端遷移と `worktree_tidy` は通常どおり通す。ずれたままであることを `wo_annotate` の `note` に残す
- **稼働中 worker の足元で hook script が差し替わる**ことは避けられない。merge 後 / CI 緑のコードなので確率は低いが、fail-closed guard が壊れた版に入ると全 worker が同時に止まる。pull の直後に worker の沈黙が揃ったら、まずこれを疑う

もう 1 つの機械作用 `close_idle_session_on_terminal_workorder` (終端 WorkOrder に残った idle Session を閉じる) は**既定 off**。有効化されている project では `merged_but_alive` の一部が escalation として上がる前に閉じられるので、Session が消えていることを「自分が閉じた」と読まない (event の `actor` で見分ける)。

#### merge 後に issue を閉じる

**closing reference が置き場をまたげない構成では、closes CL が merged になっても issue は open のまま残る。** 該当するのは CL と issue が別 repo の構成。台帳は `completed`・作業ツリーは回収済みなのに issue だけが open で、候補プールに毎サイクル浮上し続ける。

**閉じるのはこのセッション** — daemon は tracker に何も書かない。**`worktree_tidy` より先に通す** (tidy は資格判定の入力に phase を使うので、順序を守ると判断材料が揃う)。

閉じる先は **issue 置き場** (`dispatch-project.toml` の `[issue] repo`) で、実装 repo ではない。**`-R` を省いて CLI の cwd 推論へ委ねない** — 推論先は worker が走った実装 repo で、**同番号の別 issue を閉じる**事故になる。

```
gh   issue close <番号> -R <issue 置き場>
glab issue close <番号> -R <issue 置き場>
```

**閉じたら報告に 1 行載せる** (どの置き場のどの issue を閉じたか)。外部 tracker への不可逆な書き込みなので、成功も user から見える形にする。**失敗したら据え置く** — issue を触らず、phase も動かさず (閉じられなかったことは完了の否定ではない)、`wo_annotate` で `note` へ理由を残して報告に載せる。**次のサイクルで自動再試行しない** (同じ失敗が WorkOrder の数だけ並び、報告が失敗で埋まる)。原因を直せば同じ WorkOrder がまた対象になるので、user から再試行の指示を受けたら通し直す。

#### worker へ送るときの規範

主経路は **SendMessage** (初回コンタクトで確立したアドレス宛)。`session_send` はアドレスを失った worker への fallback。送る内容の規範は経路によらず同じ:

- 送るのは「放置すると作業自体が無駄になる」ものに限る (conflict 解消が典型 — 放置すると push が詰む)。**追加指示は割り込まない** — 数分遅れても結果は変わらず、元タスクとの混線リスクだけが残る。駐機を待ってから再入で渡す
- **activity が `blocked` の worker へ送らない** — 人間宛の permission ダイアログはどちらの経路でも答えられず、送ってもキューに積まれるだけ。**解除は user の領分** — pane に入って解いてもらうよう依頼する
- user から預かった指示は逐語で渡す。orchestrator が指示を創作しない
- **同じ指示を再送しない** (混線を増やすだけ)。効かなければ user に渡す
- 記録を残したい送信は `wo_annotate` で `note` に書く (SendMessage は台帳に残らない)

#### user から直接頼まれた作業 (issue を起こしてから dispatch する)

**user が作業を直接頼んできたら、issue を起票してから通常経路 (C → D) へ乗せる。** 依頼の大きさで経路を分けない。対象は **worker が作業ツリーで実行する変更・調査** の依頼で、状況照会・max の変更・終了指示・worker への中継はここへ乗せずその場で処理する (triage の判断は対話が本体なので上の表の案内行が受ける)。

1. **依頼文を逐語で本文にした issue を issue 置き場へ起票する。** title は依頼の要約。本文は依頼文をそのまま写す — 要約すると worker が読むのは orchestrator の解釈になる。label は宣言 config の `[issue] ready_label` をそのまま付ける (C の観測が使うのと同じ 1 つの値)
2. **起票した番号を報告に載せる**
3. **次の候補観測で通常経路どおり dispatch する。** C が着手可と読まなかったときは起票番号と理由を報告して user へ返す

**spawn を先にしない** (起票前に spawn すると WorkOrder に載せられない)。起票は issue 置き場の CLI で行う (daemon に起票 tool は無い)。本文を渡す flag が gh と glab で違う:

```
gh   issue create -R <issue 置き場> --title "<依頼の要約>" --label "<着手可 label>" --body-file        <scratchpad の絶対パス>
glab issue create -R <issue 置き場> -t      "<依頼の要約>" --label "<着手可 label>" --description-file <scratchpad の絶対パス>
```

- **本文の書き出しと CLI 呼び出しを 1 つの Bash 呼び出しへ連結しない** (`printf … > f && gh issue create …` の形にしない) — 連結すると CLI が `sandbox.excludedCommands` に照合されず sandbox 内で走り、起動に失敗する
- **本文は scratchpad の絶対パスへ書き出して渡す。** `$TMPDIR` に置かない — CLI は sandbox の外へ出るため、sandbox 内の `$TMPDIR` に書いた file を見つけられない
- **起票が落ちたら spawn へ進まず、error 文言を逐語で報告して user へ返す。** label が置き場に無いだけなら作ってもらう

### 報告

起動直後・イベントを 1 件処理したとき・user に尋ねられたときに出す。

**出し方** — user は「今どの issue がどうなっていて、自分は何をすればよいか」を読み取るために報告を読む:

- **起床したら、最初の tool 呼び出しの前に、これから何を処理するかを 1 文で言う。** そこから報告までの間は、判断が変わったとき (回収した / 駐機した / 見送った / 前提が崩れた) だけ短く出す。実況はしない
- 報告は結論から始める (1 文目が「この起床で何が動いたか」に答える)。観測の生データを貼らず、判断とその根拠になった観測だけを書く
- 前回の報告から変わらない項目は 1 行にまとめてよい。**ただし放置すると悪化する項目 (回収できていない / 着弾していない相手が居る / close できずに open のまま残った issue) は、変化が無くても毎回明示する** (黙ると静かな正常系と見分けが付かない)
- **観測していない項目を現況として書かない。** 候補プールの件数は、**その起床で `observe_candidates` を実行したときだけ**現況として書く。していなければ「未確認 (最終確認 HH:MM)」と書く — 「観測して 0 件」と「見ていない」が同じ文言になると、読者にも次セッションの自分にも区別が付かない
- **観測の鮮度を添える。** 応答は daemon の cache から返るので、`as_of` / `observed_at` が古いまま判断していないかを自分で見る

少なくとも次を含める:

- 起動した worker (issue / `wo_id` / Session の lifecycle)、skip した候補と理由
- **別 clone が要って見送った issue** (どの clone を cwd にした orchestrator が要るか)
- **自 pane の rename 結果** (`orchestrator` になった / 既に `orchestrator` だった / 別の書き手が居て止まった / 観測失敗や rename 失敗で付けられなかった)。**`orchestrator` を名乗る peer session は、続行の判断と関わりなく name と ref を挙げる** — 居なければ「該当なし」、`ListAgents` を引かなかった起床は「未確認」と書く
- **inbox の処理結果** (どの rule が何件上がり、どう判断して ack したか。ack しなかったものは理由)。**`attempts` が 3 に達した / `permanent` が真の escalation は打ち切り済みなので、以後同じ事象が二度と上がらない** — 名指しして報告する (`permanent` は `attempts` が 1 のまま打ち切られるので、回数だけを見ると配送中と区別が付かない)
- 初回コンタクトが着弾していない worker (質問が来ない前提で扱う旨)
- 駐機した issue とその CL (番号 / status / URL)。**merged なのに issue が open のもの**は候補プールに浮上し続けるので明示する
- **閉じた issue** (置き場 / 番号) — 外部 tracker への不可逆な書き込みなので成功も出す
- **起票した issue** (番号 / 付けた label / 元の依頼の要約。dispatch へ乗ったか、まだ候補待ちか)。**候補待ちのまま残っているものは変化が無くても毎回明示する**
- 台帳の `outcome` — 人手が要ると自己申告したものは理由付きで、申告が無く CL 成果も無いものは途中死候補として
- `worktree_sweep` の結果 (回収資格を得たもの / `blockers` で残ったもの / `orphan_worktrees` / `unverified_worktrees` / `stranded_sessions`)。**「sweep を回したから片付いた」と読まない** — sweep は報告だけで、消すのは `worktree_tidy` の名指し 1 回
- 待機に戻ることと、user が今できること (worker への指示・pane 移動での直接介入・終了指示)

## v2 が持たない tracker 作用

**v2 の tool 面は台帳・Session・観測・inbox・資源に閉じていて、tracker へ書き込む tool を 1 つも持たない。** 機械が tracker に何も書かないという線 (reconciler は記帳と escalation だけを行う) をそのまま tool 面に写した結果で、**書き込みが要る作用はこのセッションが CLI で行う**。

| 作用 | v2 での経路 |
|---|---|
| merge 後の issue の close | `gh issue close` / `glab issue close` (上記の節) |
| issue の起票 (user 直依頼) | `gh issue create` / `glab issue create` (上記の節) |
| review thread への返信と resolve | worker が `gh api` / `glab` で行う (spawn prompt に綴りを逐語で渡す) |
| issue へのコメント | `gh issue comment` / `glab issue note create` |

**claim 信号 (着手中を表す tracker label) は v2 では立たない。** 宣言 config も `claim_label` を受け取らないので、**綴りを憶測で埋めて CLI を撃たない** — 置き場に無い label は gh なら失敗し、glab なら暗黙に作られて誰も読まない印が増えるだけになる。

その帰結を運用として引き受ける:

- **別マシンの orchestrator・人間・wayfinder セッションから見ると、着手中の issue も空いて見える。** 二重 dispatch を塞いでいるのは `wo_create` が同一 issue の 2 本目の非終端 WorkOrder を拒むことだけで、**その保護はこの台帳の中にしか効かない**
- **候補を選ぶとき (C) は、issue のコメントと直近の更新を自分で読んで着手済みかを確かめる。** 台帳の外の着手を見分ける面はそこしか無い
- **同じ issue に 2 本目の CL が現れたら、それは二重 dispatch の徴候**として報告し、どちらを残すかを user に決めてもらう

## 実行環境

- 変更の届け方は実行 project の運用に従う
- このセッションが行う不可逆な外部書き込みは、issue の close と起票の 2 つだけ。どちらも成功と失敗の両方を報告に出す
