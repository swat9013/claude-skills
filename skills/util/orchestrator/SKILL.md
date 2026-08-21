---
name: orchestrator
disable-model-invocation: true
argument-hint: "[max]"
description: herdr (AI agent 向け terminal multiplexer) session 内で、着手可能な open issue を選んで Claude Code セッションを分割 pane として起動し、worker の質問と observer の escalation で回収・駐機・補充を回す常駐 orchestrator。周期観測は pane 常駐の observer、観測と操作 (tracker / pane / worktree / durable 台帳) は dispatch-ops MCP server が持ち、候補選定・回収・駐機・drift 解消の判断と user の窓口はこのセッション 1 つに保つ。台帳は永続で、前回セッションが駐機した issue とその後の PR merge を再入時に拾い直す。
---

# orchestrator

herdr session 内で、宣言された issue 置き場の open issue から着手可能なものを選び、**規定セッション数まで** Claude Code セッションを起動する orchestrator。展開先は**この skill を呼び出したセッションが居る herdr workspace の分割 pane**。

**issue 置き場 (どこの issue を読むか)・PR 置き場 (PR / MR がどこに出るか)・実装 repo (どの clone で作業させるか) は別軸。** 置き場は project に 1 つずつで、**宣言を解決するのは server** — tracker 系 tool は宣言の repo を既定で使うので、識別子を自分で引いて渡す必要は無い (中身を見るなら `observe_project`)。実装 repo は issue ごとの判断で、cwd の clone とは限らない。

**issue 置き場と PR 置き場が別 tracker の project** (issue = Jira / PR = GitLab 等) では、issue 側の tool 群だけが差し替わる。該当するかは `observe_project` の `issue.tracker` で判る — 判ったら A へ進む前に「cross-tracker」節を読む。**単一 tracker (GitHub / GitLab) の project では読まなくてよい。**

**このセッション自身はポーリングしない。届いたもので動き、それ以外の時間は turn を終えて待つ。** user の唯一の窓口として常駐し、worker の質問と observer の escalation を受けて判断する。active も候補も 0 になっても終了せず、報告して待機する。

## 三役と判断の分担

| 層 | 持ち物 | 持たないもの |
|---|---|---|
| MCP server (`mcp__plugin_swat-skills_dispatch-ops__<tool>`) | 観測の正規化・操作の実行・台帳への記帳・phase 遷移の合法性検証 | ポリシー (候補選定・回収可否・drift の解消手段) |
| **worker** (pane の独立セッション) | 担当 issue の作業そのもの。**質問だけ**を orchestrator へ送り、終了直前に台帳へ自己申告する | PR 到達・完了の通知 (検知は observer の外形観測) |
| **observer** (pane の常駐セッション + `/loop` dynamic) | 外部状態 (tracker / PR / pane 生死 / worktree) の周期観測。**観測から一意に決まる機械的遷移だけ**を台帳へ直接記帳し、判断が要る事象を escalation する | user・channel との対話。worker の transcript 読解。判断を要する遷移 |
| **このセッション** (orchestrator) | 候補選定・起動 prompt の文面・escalation とイベントの解釈・回収 / 駐機 / unclaim の判断・drift 解消・user との対話 | 周期的な外形監視 (observer へ移管。単発の照合は下記 3 点で行う) |

**server はポリシーを持たない。** 候補を選ばず、回収すべきかを返さず、drift の解消手段も示さない。判断を server の返り値に探しに行かず、観測を材料に自分で決める。

worker と observer を独立プロセス (pane) に置くのは、orchestrator を再起動しても死なないため。pane は通常見ない緊急ハッチで、**日常の連絡経路はメッセージ**。

**observer は加算であって置換ではない。** このセッション自身の `resolve` 実行トリガー 3 点 (再入 / イベント処理 / user の問い合わせ) はそのまま残す — observer が沈黙しても検知経路が丸ごと消えないため。

台帳 (`~/.claude/issue-dispatch/<key>/`) は **project に 1 つ**で永続。実装 repo が複数あっても分かれない — worker には `pane_spawn` が台帳ディレクトリを env で渡すので、別 clone で走る worker の `ledger_report_outcome` もここに着地する (worker の prompt に台帳の話を書く必要は無い)。台帳は**着手した後**のライフサイクル (`claimed` → `active` → `parked` → `done` → `cleaned`、ほかに終端の `released` / `spawn_failed`) だけを持つ。候補プールは台帳に入れない — 何が着手可かは毎回 tracker の生データから読み直す。**メッセージは揮発する** (配信保証が無く、再起動で消える)。真実源は台帳と tracker で、メッセージは速い経路にすぎない。

## args

`/orchestrator [max]` — max は同時稼働セッション数の上限 (省略時 3)。**server は max を知らない**ので、空き slot は `observe_panes` の結果 (`tracked` が true で `is_self` が false の pane 数) と max から自分で数える。`observe_panes` が観測失敗で丸ごと落ちているときは、台帳の `active` entry 数を使用中 slot として数えて進む (罠の表参照)。

## 前提

pane 系 tool が前提不成立で失敗したら、error 文言に応じて user へ依頼して終了する (dispatch は不能だが、fail-closed なので誤 dispatch には進まない):

| error | 依頼 |
|---|---|
| `HERDR_ENV=1 でない` | herdr session 内で Claude Code を起動し直してもらう (herdr 以外の terminal multiplexer は非対応) |
| `claude: current` が無い | `herdr integration install claude` の実行を依頼する。hook は session identity を herdr に報告して pane と Claude session を 1:1 対応させる。無いと agent field が埋まらず、起動確認と駐機判定が壊れる |
| `herdr status が失敗 (socket に届かない)` | herdr daemon の起動を依頼する |
| `CLAUDE_CODE_MESSAGING_SOCKET が未設定` | 現行 binary で**新規起動**した Claude Code セッションから実行し直してもらう。旧 binary の resume セッションは送信できても受信できず、worker の質問も observer の escalation も誰にも届かない |

表に無い error 文言は前提不成立ではない。特に `pane <ID> の応答に agent field が無い` は特定 pane の応答が壊れているだけで、hook 未インストール (agent field が「埋まらない」) とは別事象 — user に修正依頼せず罠の表を引く。

Remote Control (`/remote-control`) の有効化を user に薦める (前提ではない)。有効なら質問中継と承認を claude.ai web / mobile から返せる。無効でも herdr pane の terminal で同じ操作ができるので、断られたらそのまま進む。

model / effort はセッション起動時 (`claude --model` / `--effort`) に決まっている前提で動く。frontmatter では指定しない — skill の上書きは active な turn にしか効かず、常駐の大半を占める受信・起床で始まる turn は session の値で走る。

## プロトコル

```
起動 /orchestrator [max]
  │
  ├─ 0. 自 pane 名  observe_panes (他に orchestrator が居ないこと) → herdr pane rename
  ├─ A. 再入       ledger_list → resolve        前回の意図と現実の差を確認
  │               + observer の確保 (発見 or spawn) → 初回コンタクト
  ├─ B. drift 解消  台帳を直すか、現実を直すか
  ├─ C. 候補選定    observe_issues (宣言された置き場) label 体系と issue の実態から選ぶ
  ├─ D. 起動       実装 repo と clone を決める
  │               → issue_claim → ledger_record → pane_spawn
  │               → ledger_transition(active) → 初回コンタクト送信
  │
  └─ E. 常駐 ◀──────────────────────────────────────┐
        turn を終えて待つ (自分ではポーリングしない)   │
        worker の質問 / observer の escalation /      │
        user の指示で起床                             │
        → 冒頭で observer の死活確認 (A の手順)       │
        → resolve で現実照合 → 中継 / 回収 / 駐機     │
        → worktree_tidy (clone ごと) → C/D で補充 ────┘
        └─ active 0 かつ候補 0 でも終了しない。報告して待機する
```

### 0. 自 pane を `orchestrator` に rename する

pane が増えたときに人間が herdr 画面で役 (`orchestrator` / `observer` / `i<N>`) を見分けられるようにする。**A より先に通す。**

1. `observe_panes` で label `orchestrator` の pane を探し、**`ListAgents` も引く** — `observe_panes` が見るのは自 workspace の pane だけで、**同一マシンの別セッション (別 workspace / Remote Control / cloud) はここに映らない**。窓口の一意性は pane ではなくマシン単位で要るので、両方を見ないと 2 体目を検知できない
2. **`is_self` が false の `orchestrator` pane、または `orchestrator` を名乗る peer session が 1 つでも在れば rename せず、その pane_id / name を報告して止まる** — 窓口は 1 つで、2 体目の orchestrator を作らない。user がどちらを残すか決めるまで dispatch へ進まない。**pane に映らない peer は同じ台帳へ書き得る**ので、居るあいだは記帳の帰属 (どの観測でその phase になったか) を自分の観測から確定できない
3. **`observe_panes` が観測失敗で丸ごと落ちているときも rename しない** (「居ないから rename」と判定しない)。報告に載せてそのまま A へ進む
4. 他に居なければ Bash で `herdr pane rename $HERDR_PANE_ID orchestrator` を実行する。**`$HERDR_PANE_ID` が空なら実行しない** (引数が 1 つ足りない呼び出しになる)。1 の返り値の `is_self` が true の entry の `pane_id` が同じ値なので、そちらを literal で渡してもよい。**自 pane が既に `orchestrator` label なら rename は不要**だが、その事実を報告に書く (書かないと手順を通したのか飛ばしたのかが報告から見えない)

- 自 pane の label が前回 dispatch の残骸 `i<N>` だったときは、1 の `observe_panes` の時点で server が `dispatch` へ付け直している。その上から `orchestrator` を書く形になる (server が付け直すのは issue slug 形式の label だけなので、`orchestrator` は以後保持される)
- rename コマンド自体が失敗したときは dispatch を止めない (pane 名は人間の見分けのための印で、宛先伝達の経路ではない)。失敗を報告に載せる

### A. 再入 (ledger_list → resolve → observer の確保)

- `ledger_list` の非終端 entry が前回までの意図。各 entry の `note` は前セッションの自分からの引き継ぎで、機械はパースしない
- `resolve` が台帳と外部 store (tracker / pane / git) を join し、食い違いを `drift` に列挙する。単発の追跡 (「この issue の PR / worktree / pane はどこか」) も同じ tool を `issue_ref` 付きで叩く
- **`resolve` の `repo` / `pr_repo` も宣言から埋まる** (tracker 系 tool と同じ)。渡さない。ただし**宣言が効いていない環境**では CLI の cwd 推論へ倒れ、置き場が cwd と別 repo なら別 repo の同番号 issue の state を読んで事実無根の `issue_closed` が出る — その drift は phase を `done` へ送る根拠なので、放置すると稼働中の worktree が回収対象になる。宣言が効いているかは `observe_project` の `repo` が埋まっているかで見る (1 サイクルに 1 度で足りる)
- **別 clone の worktree は台帳の `agent.worktree` で照合される。** `resolve` は cwd の clone の一覧に加えて、entry に記録されたパスを見に行く。**記録が無い entry の worktree は照合されない**ので、起動時に絶対パスを記帳しておく (D)。記録パスを観測できなかった entry は `stores.worktrees.errors` に載り、その entry の `checked.worktree` が false になる (「消えた」ではない)
- **PR の突合は (repo, ref)。** `repo` を書かずに記帳した PR は、別 repo に同番号の PR が居ると一意に決まらず `pr_ref_ambiguous` で返る。解消は `ledger_transition` の `prs` に `repo` 付きで記録し直すこと (どちらの PR が自分の成果かは `prs[].repo` を見て決める)
- **PR 置き場が issue 置き場と別 tracker の project では、`resolve` / `observe_prs` は宣言の PR 置き場で走る** (server が解決する。`pr_repo` を渡すのは宣言と違う置き場を 1 回だけ見るときだけ)。この構成では issue から PR を引く経路が無く、**台帳の `prs[]` に記録した PR だけ**が観測される — 記録が無い entry は `stores.prs.errors` に理由が載って `checked.prs` が false になり、merged になっても drift は出ない。PR を出した worker の entry には必ず `ref` / `role` / `repo` を記帳する
- **「見ていない」を「無かった」と読まない**: `stores.<name>.observed` が false の store 由来の drift は 1 件も出ないし、entry ごとの `checked.{issue,prs,pane,worktree}` が false の側面は判定していない。pane を観測できていないのに「pane 消失」と読むのが最も高くつく誤読
- **pane が生きていると分かった `active` entry には、初回コンタクトを送り直す** (手順は D の同名の節)。worker が持っている返信先は前回の orchestrator プロセスの UDS アドレスで、プロセスが変わった時点で死んでいる — 送り直さないと、その worker の質問は以後どこにも届かない (送っても worker 側にエラーは返らない)
- `resolve` は非終端 entry 1 件あたり tracker CLI を数回、直列で起動する。常駐が長引くほど非終端 entry は伸びるので、**引数なしの `resolve` は再入 (A) と user の全体照会だけに使い**、それ以外は `issue_ref` で 1 件に絞る。引数なし呼び出しは 2 分を超えて自動 background 化されうる (同期フローが崩れる)。あわせて終端 entry を溜めない (`done` は `worktree_tidy` で `cleaned` まで送る)

#### observer の確保

**再入 (A) と E の起床ごとに、observer を 1 つだけ確保する** (prompt の文面は D「observer の spawn prompt」、送信手順は D「初回コンタクト」)。observer は台帳にも `tracked` にも載らない label 起動なので、slot も worktree も消費しない。ここが observer の死活の唯一の検知経路 — **observer の沈黙は escalation の不在からは見分けられない**ので、届いたものに反応する形では確かめられない。

`observe_panes` で label `observer` の pane を探し、pane の有無と `agent_status` (中立 4 値) で処置を分ける:

| 観測 | 処置 |
|---|---|
| pane が無い | `pane_spawn(label: "observer", prompt: <observer の spawn prompt>)` で起動する (`worktree` / `cwd` は渡さない) |
| pane は在るが `agent_status` が `exited` / `agent` が null | **`pane_close(pane_id)` してから `pane_spawn`** — 残った pane が label を占有している間、`pane_spawn` は「既にある」で撥ね続ける (agent の生死は見ない) |
| pane が在って `running` / `idle` | 起動しない。`idle` は observer の正常形 (tick の合間) |
| `observe_panes` が観測失敗で丸ごと落ちている | **何もしない** — 有無を判定できない状態で spawn すると二重起動になる。二重起動より 1 周期観測が遅れるほうが安い。報告に載せて次の機会に確保する |

- **pane が無くても `observer` を名乗る peer session が居ることがある** (手順 0 の `ListAgents` で見える。pane を畳んでも messaging registry からは即座に消えない)。**spawn する前に `ListAgents` で確かめる** — pane だけを見て起こすと、生きている observer と並走して同じ台帳へ書き得る 2 体目になる。既に居るなら起こさず、どちらを残すかを user に返す
- **初回コンタクトは、A では確保の結果によらず必ず送る** — observer が持つ escalation 宛先は前プロセスの UDS アドレスであり、orchestrator が変わった時点で死んでいる。送り直さないと escalation は以後どこにも届かない (送っても observer 側にエラーは返らない)。**E の死活確認では、spawn し直したときだけ送る** (既に居て健全なら宛先は生きている)
- **同じ起床で spawn した observer は、その回の死活判定にかけない** — agent の検出に十数秒かかるので `agent: null` に見え、閉じて起こし直す無限往復になる。判定は次の起床から
- `pane_spawn` が「既にある」で撥ねられたのは「既に居る」であって失敗ではない (`spawn_failed` の扱いをしない)

### B. drift 解消

台帳は「意図と記録」、外部 store が「現実」。どちらを直すかを毎回決める:

- **意図がもう無い** (作業が終わった / 成果ごと消えた) → 台帳を現実に合わせる (`ledger_transition`)
- **意図は生きていて機構だけ落ちた** (`pane_missing` かつ worktree が残っている) → 現実を直す (`pane_spawn` に `cwd` を渡して駐機ツリーへ再入する。台帳の `agent.worktree` の絶対パスをそのまま渡せば別 clone のツリーにも届く。再入した worker にも初回コンタクトを送る)
- **台帳外だが自分の管理下に置くべき作業** (`worktree_untracked` で assignee が自分・branch が `i<N>` / `worktree-i<N>` 規約に合う — 台帳導入前のセッションや旧方式の遺産) → 後追い記帳する: `ledger_record` (`agent` に `worktree` / `branch` を記載) → `ledger_transition("active")`、駐機状態なら続けて `"parked"` へ (`claimed → parked` の直行は非合法)。issue が既に closed なら `record → "done"` が合法で、tidy の回収対象になる。台帳に載せない限り、conflict 解消の起動も駐機 merge の回収も、その issue には発火しない
- **それ以外の `worktree_untracked` / `pane_untracked`** (他者・出所不明) は触らない側に倒して報告する
- 解消しないと決めた drift は、その理由を `note` に残す (同じ判断をやり直さないため)。phase を動かさない決着なので `ledger_annotate` で書く

### C. 候補選定

`observe_issues` は絞らず並べ替えず生データを返す。着手可の意味は**その環境の label 体系と issue の実態から推定する**。

- **issue 置き場は server が宣言から解決する。** tracker 系 tool (`observe_issues` / `observe_prs` / `issue_claim` / `issue_unclaim` / `issue_comment` / `issue_label` / `resolve`) の `repo` は未指定なら宣言の値が入るので、置き場が cwd の repo でなくても渡さなくてよい。渡すのは**宣言と違う repo を 1 回だけ見るとき**だけ (明示引数が宣言に勝つ)
- **観測先が宣言どおりかは `issues[].url` で確かめる** — 1 サイクルに 1 度見れば足りる。返り値の `repo` は実際に使った識別子だが、宣言の綴り自体が誤っていれば同じ値が echo されるので、それだけでは検証にならない。置き場が想定と違ったら `observe_project` で宣言の中身を見る
- **`observe_issues` が「adapter は未実装」で落ちたら、issue 置き場が dispatch-ops の adapter を持たない tracker (Jira)** — 前提不成立ではないので user へ依頼せず、「cross-tracker」節の C の差分へ移る
- **issue 本文が要るなら `gh issue view <N> --json body -q .body` で引く** (`observe_issues` が返すのは title / labels / state で、本文は含まない)。**ループで回さず 1 呼び出し 1 issue** — `for` の本体に埋めた `gh` は sandbox 内で走って必ず起動失敗する (罠の表)
- **確信が持てない issue は dispatch しない側に倒す** (誤 dispatch のコストは、補充が 1 回遅れるコストより高い)
- 除外する: assignee が付いている issue (他者の作業 / 自分の駐機)、台帳に非終端 entry がある issue。**終端 entry (`cleaned` / `released` / `spawn_failed`) しか無い issue は再 dispatch 可**で、直前に `released` へ送った issue もここに戻る (`ledger_record` は非終端 entry が生きていると失敗するので、再 dispatch は必ず新規 entry になる)。**終端 entry の `outcome` は除外根拠にならない** — 「作業不要と判断した」で終わった issue も、open で label が着手可なら候補に戻る。dispatch しないと決めたなら候補として報告し、close / label 剥がしを user へ返す (放置すると毎サイクル浮上し、observer の候補通知も繰り返される)
- `blocked: null` は**未検査**であって「blocker 無し」ではない。`include_blocked` は 1 issue あたり CLI が起動するので、`labels_any` / `labels_none` / `assignee` で絞ってから、通す候補にだけ立てる。検査に失敗した issue は blocked 扱いで skip する
- `truncated` が true なら窓の外に取り残しがある。報告に載せる
- 整列も判断のうち — 完成に近い段階から slot を埋めると、同じ slot 数でも成果が出る速度が上がる

### D. 起動

**`issue_claim` → `ledger_record` → `pane_spawn` → `ledger_transition("active", agent={...})` → 初回コンタクト送信** の順で通す。claim を先に置くのは、起動前に assignee を立てて二重 dispatch を塞ぐため。

作業ツリーの隔離は `pane_spawn` の引数で表す (`worktree` と `cwd` は両方渡すと error、どちらを実行したかは返り値の `mode` で確認する):

| 渡す引数 | mode | 用途 |
|---|---|---|
| なし | `plain` | repo root でそのまま起動する |
| `worktree: "<issue slug>"` | `created` | 新しい作業ツリーへ隔離する (実装作業の既定)。名前は pane label と同じ issue slug (`i386` / jira は `swatcf-14`) にする — **`worktree_tidy` / `worktree_sweep` の保護と回収はこの綴りで照合する** |
| `cwd: <駐機ツリーの絶対パス>` | `reentered` | 既存の駐機ツリーへ再入する (conflict 解消・追加指示) |
| `repo_root: <clone の絶対パス>` | 上記と併用 | 作業ツリーを切る clone を指定する (省略時は cwd の clone) |

#### 実装 repo の選定と clone の解決

**どの clone で実装するかは issue ごとにこちらが決める。** server は repo を推測せず clone も探さない。

1. **実装 repo を読む** — issue 本文の明示・label・`docs/agents/domain.md` 等の宣言 doc から。読み取れない issue は cwd の clone で実装する (置き場がメイン repo なら通常これ)
2. **clone のローカルパスを解決する** — `ls` / `git worktree list` / ghq 配下の探索など、その環境で確かめられる手段で **実在を確認してから**渡す。`pane_spawn` は無いパスを error で撥ねるが、撥ねられてから探すより先に確かめるほうが claim の巻き戻しが要らない
3. **clone が見つからないときは dispatch を見送る** — `git clone` しない (どこに置くべきかを判断できない)。claim 済みなら `issue_unclaim` して候補へ返し、報告に「clone が無いので見送った issue」として載せる
4. **`repo_root` を渡して起動する** — `worktree: "i<N>"` と併用すると、その clone の下に作業ツリーが切られる。返り値の `repo_root` / `cwd` / `mode` で意図した clone に切れたかを確認する

**台帳へ実装先を記録する** (`ledger_record` / `ledger_transition`)。次セッションの再入と worktree 回収がここだけを頼りにする:

- `repo` に**実装 repo** の識別子 (issue 置き場ではない。置き場は宣言が正本なので entry に書く意味が無い)。**綴りは `prs[]` の `repo` と同じ体系で揃える** — closes の帰属判定は `entry の repo == prs[].repo` の文字列一致なので、体系が違うと自分の worker の PR が必ず `closes_other_repo` へ落ち、駐機・回収の根拠 (下記) が構造的に空になる。**glab は `prs[].repo` が数値 project id の文字列で返る**ので、entry の `repo` にも同じ id を書く (人が読める full path を書くと突合が外れる)
- `agent.worktree` に作業ツリーの**絶対パス**。`worktree: "i<N>"` 起動の作業ツリーは `<clone root>/.claude/worktrees/i<N>` に切られるので、**`pane_spawn` の返り値の `repo_root` に `/.claude/worktrees/<label>` を継いで書く** (渡した値ではなく返り値を使う — server が main worktree root へ正規化するので両者は一致しないことがある)。`cwd` をそのまま書かない — 新規隔離 (`mode: created`) の `cwd` は clone root であって作業ツリーではなく、これを記録すると `resolve` が clone root の実在を見て「worktree は在る」と読み、消失も残置も永久に検出されない。再入 (`cwd` 起動) はこの絶対パスをそのまま渡す。**回収 (E) はここから clone root を復元する**ので、記録が無い entry の worktree は照合も回収もされない
- `prs[]` を記帳するときは `repo` も入れる — **`observe_prs` の `prs[].repo` をそのまま写す** (issue の repo でも URL でもない。突合は文字列一致なので綴りが違うと status の食い違いを捏造する)。ref は repo をまたぐと一意でないので、無いと突合が `pr_ref_ambiguous` に落ちる

失敗の扱い:

- claim 失敗 → 起動せず次候補へ
- clone が見つからない → 上記 3
- `pane_spawn` が `ok: false` (agent 未検出) → **pane が残って label を占有し、以後その issue の起動が撥ね続ける**。`pane_close` で畳んでから `ledger_transition(spawn_failed)` + `issue_unclaim` する (assignee 残留は候補から永久に外れる)。再試行は新規 entry で 1 度だけ。**2 度目も失敗した issue は自動再試行を打ち切り**、報告に載せて user の判断へ返す (常駐なので「このセッション中は skip」は恒久 skip と同義になる)

#### 初回コンタクト (返信先を渡す)

`pane_spawn` は起動セッションに pane label と同じ名前 (worker は `i<N>`、observer は `observer`) を付ける。**spawn 直後に orchestrator から SendMessage を 1 通送る** — 相手はその `from` 属性 (UDS アドレス) をそのまま返信先に使う。worker にも observer にも、spawn 時と再入時の両方で送る。

**相手側から orchestrator を名前で発見する経路は無い** (orchestrator 自身に名前は無く、送信元として表示されるラベルはアドレスとして解決できない)。この 1 通が着弾しない worker は質問を一度も送れず、observer は escalation を一度も送れないまま安全網 (再入時の `resolve`) 送りになる。fire-and-forget にせず、送れたことを確認してから次へ進む:

- 宛先は pane label と同じ名前。**bare name は 1 体だけに一致すれば通る**ので、通ったのは成功であって疑わない。拒否されるのは (1) 同名の peer が複数居る (`N agents are named ...`) (2) まだ ListAgents に載っていない、のどちらか。エラーが正しい `name [ref]` 表記を提示するので、それで再送すれば通る。**同名が複数のときは活動時刻が最も新しいものを選ぶ** — 古い方へ送ると質問も escalation もそのセッションに刺さったまま届かず、送信側にエラーは返らない。**この拒否を起動失敗と読んで `spawn_failed` / `issue_unclaim` へ巻き戻さない** (pane もセッションも生きている)
- ListAgents への登録は起動から十数秒かかる。見つからない = 死んだ、ではない。**待たずに次の候補の起動へ進み、その後で引き直す** (待ちを入れずに時間を稼ぐ)
- それでも着弾を確認できないまま先へ進むなら、`note` と報告に「質問 / escalation が来ない前提の相手」として残す
- worker 宛の文面には issue 番号を入れる (worker が返信に番号を添えられる)。observer 宛は escalation の宛先を渡すのが目的なので、**この 1 通が届いたら以後の loop 指示文にこのアドレスを書き込んで持ち回れ**と明記する
- `SendMessage` / `ListAgents` が tool 一覧に無ければ ToolSearch で schema を取ってから使う

#### worker の spawn prompt 契約

server は prompt を一切解釈しない。worker が自走し、orchestrator が後で取りまとめられるだけの情報は全部文面に入れる。次の各点は欠けるとどこかが壊れる:

| 文面に入れるもの | 欠けたときに壊れるもの |
|---|---|
| issue 番号 (と何をする作業か) | worker が自分の担当を特定できない |
| **質問だけを SendMessage で orchestrator へ送る契約** — 人間の判断が要る質問が出たら送って turn を終える。宛先は最初に届いたメッセージの `from` をそのまま使う。**PR 到達・完了は送らせない** (外形観測で拾うので、送らせると「申告があったから終わったはず」という読みを誘発する) | 質問が誰にも届かず、worker が pane 内で止まる |
| **AskUserQuestion を使わないという禁止** — 質問は SendMessage で送って turn を終え、idle で回答を待つ | worker が pane 内で応答待ちに入り、pane を見ていない user には質問の存在が見えない (`blocked` のまま slot を塞ぐ) |
| PR 本文に closing reference を必ず含めるという要求。**PR を出す repo と issue 置き場が別なら `Closes <owner>/<置き場 repo>#<N>` の cross-repo 表記を逐語で指定する** (同 repo なら `Closes #<N>`)。**issue 置き場と PR 置き場が別 tracker なら closing reference は成立しないので、この行を「cross-tracker」節の 2 行で置き換える** | 紐づきが `mention` 止まりになり、駐機判定と observer の merge 検知が「自分の PR を持つ issue」を見分けられない。cross-repo で `Closes #<N>` と書くと**その関連 repo 内の同番号 issue**を指し、無関係な issue を巻き込みつつ本来の issue は紐づかない |
| 終了直前に `ledger_report_outcome` (`issue_ref` / `outcome` / `summary`) で自己申告する契約 | 「なぜ終わったか」が台帳に残らず、正常終了と途中死を外形観測で区別できない。**申告は入力であって真実源ではない** — 完了判定は観測で行う |
| 再入 (`cwd` 起動) では新規 PR を作らず既存 PR の branch へ push する、という明示 | 再入セッションが 2 本目の PR を作る |
| **レビュー指摘への対応で再入させるときの 3 点** — 指摘へ返信し、対応を PR へ反映したうえで、その thread を `review_thread_resolve` で**自分で閉じる**。**同意できない指摘は PR 上で反論せず**、SendMessage で orchestrator へ上げる (質問と同じ経路) | 閉じないと同じ thread を毎 tick 検知し続ける (未対応かの判定を tracker 側の `isResolved` に置いているので、機構の内側に閉じる主体が要る)。PR 上で反論すると user の窓口が 2 つになり、user が知らないまま PR 上で議論が進む |
| 着手前に **`git pull --no-rebase origin main`** で最新を取り込む、という明示 (コマンドごと書く)。**別 clone で実装させるときは「作業ツリーの repo の main」であることも書く** (issue 置き場の repo ではない) | worker が古い main を土台に作業し、merge 時に conflict する |

- **作業ツリーの外にある実体を触る acceptance criteria は worker に渡さない。** `worktree` 起動 (`mode: created`) の worker の sandbox は clone root への書き込みを構造的に拒否する (Edit は隔離エラー、Bash 経由も `PermissionError`)。該当する AC を含む issue は、その項目だけ user の領分として切り分けてから dispatch し、報告に「user に残る作業」として載せる
- `--no-rebase` にするのは worker が既に commit していると `--ff-only` が成立しないから (merge commit ができる点は許容する)
- `outcome` の語彙を server は検証しない。orchestrator が読んで判断する材料なので、**この session が読み分けられる語彙を prompt 側で指定する** (最低限「PR に到達した」「作業不要と判断した」「人手が要って停止した」の 3 系統 + 理由)
- 作業種別 (実装 / 調査 / triage / 計画検証) と `model` / `effort` は issue の label 体系から読んで選ぶ。server は段階を知らないので、対応付けはこの session が毎回決める

#### observer の spawn prompt

**観測の契約は observer skill 本文が正本で、ここには置かない** (二重化すると直す先が分からなくなる)。渡すのは静的 1 行だけ:

```
pane_spawn(label: "observer", prompt: "/swat-skills:observer を実行する。skill の手順に完全に従うこと",
           model: "sonnet", effort: "medium")
```

- **`model` / `effort` はこの 2 値で固定する。** observer は「観測して、機械的遷移だけ書いて、残りは上げる」役で、判断を持たないので上位モデルに上げる用途が無い。下げないのは、毎 tick が「entry ごとに `resolve` → `observe_panes` → 記帳の 2 条件を検査 → escalation → 次の起床を armed」の多段手順で、1 段の分類ではないため
- **`effort` を `low` にしない。** 記帳の前提検査 (pane が居ないこと / PR の `repo` が entry と一致すること) を飛ばした `done` は、稼働中 worker の作業ツリーを回収させる。取り返しがつかない側の誤りなので、検査を省く余地を残さない
- **動的情報を文面に載せない。** escalation の宛先は初回コンタクトの `from` で渡り、issue 置き場の repo 識別子は server が宣言から解決する (observer は識別子を知らなくても宣言どおりの置き場を観測する)
- 契約が守られていない兆候を観測したら、直す先は spawn prompt ではなく observer skill 本文 (罠の表を参照)

### E. 常駐 (イベント処理)

**このセッションでポーリングループを組まない。タイマーを置かない。** 周期観測は observer の担当で、こちらは届いたものを処理する。補充と照合を済ませたら turn を終えて待つ — idle なら受信で新しい turn が始まり、tool 実行中なら tool call の合間に読まれる。キューは会話そのものなので、届いた順に 1 件ずつ処理すれば足りる (同時処理は要らない)。

**起床したら、届いたものを処理する前に observer の死活を確認する** (手順は A の「observer の確保」。不在 / `exited` / `agent` が null なら確保し直し、spawn し直したときだけ初回コンタクトを送る)。これはタイマーではなく、起床したときにだけ通る 1 手順 — `observe_panes` が 1 回増えるだけで、observer が健全なら空振りする。狙う不変条件は **orchestrator さえ生きていれば dispatch 系が自己回復すること**で、observer の `/loop` が aged out しても無言死しても次の起床で戻る。**observer を監視する役は作らない** (再帰する)。

**`observe_panes` を見たついでに concierge の残骸を畳んでよい** (裁量であって義務ではない)。対象は `untracked` × label が `concierge-*` × `agent_status: exited` の 3 条件が揃った pane だけで、`pane_close` する。concierge は自律終了しても pane が残り、台帳外なので回収の担い手が他に居ない — 3 条件が揃わない pane は対話中かもしれないので触らない。**この掃除のために `observe_panes` を増やさない** (ポーリングの再導入になる)。

**この `observe_panes` を実行していない起床では、報告に「observer 健在」と書かない。** 書けるのは「この起床では死活を確認していない (前回確認は HH:MM)」だけ。escalation が届いたことは生存の証拠にならない — 届いたものはその観測時点のもので、以後に死んでいても同じ見え方をする。**手順の義務だけでは省略が報告から見えず、省いたことに誰も気づけない**ので、書ける文言の側で縛る。

**user への確認は AskUserQuestion で行わない。** 設問は報告文に書いて turn を終える。AskUserQuestion は tool 実行中の扱いになり、**回答が返るまで worker の質問も observer の escalation も配送されない** (回答後に一括で届く)。その間に observer は打ち切り上限まで再送を消費し、以後その事象を上げなくなる — 配送が止まっているだけなのに、observer 側は「返事が無い」と読む。同じ理由で、**判断を外部へ出して長く待つ tool 呼び出しも起床の処理中は避ける**。

このセッションが `resolve` を走らせるのは次の 3 点だけ (observer が居ても減らさない):

1. セッション起動時の再入 (A)
2. 届いたものを 1 件処理するとき (対象を `issue_ref` で絞る)
3. user が状況を尋ねたとき

届いたものごとの処理:

| 届くもの | すること |
|---|---|
| worker からの質問 | **issue 番号を冠して user へ逐語で中継する** (並走する worker が複数居るので、番号が無いと user はどの作業の話か分からない)。どの issue のどのアドレスから来たかの対応を自分で保持し、user の回答を逐語でそのアドレスへ返す。返信が worker を起こす |
| observer の「機械的遷移を記帳した」 | 台帳は既に更新済み。`resolve` (`issue_ref`) で裏を取ってから `worktree_tidy` と補充 (C / D) へ通す。**observer の記帳も観測の報告であって観測そのものではない** — 食い違ったら自分で観測し直した結果を採る |
| observer の「判断が要る」 | `resolve` (`issue_ref`) で観測してから回収 / 駐機 / conflict 解消の判定指針に通す。判断はこちらが持つ |
| observer の「判断が要る」のうち**駐機中の未解決 review thread** | `resolve` (`issue_ref`) の `derived.unresolved_review_threads` で裏を取り、対応させるかを判断する。**null は「指摘が無い」ではない** (未判定) ので、空と読んで閉じない。対応させるなら**駐機ツリーへ再入する** — D の起動表の `cwd: <駐機ツリーの絶対パス>` (`reentered`) をそのまま使い、初回コンタクトを送り、`ledger_transition("active")` で戻す。**新しい起動パターンも新しい phase も作らない**。再入 prompt には上記「worker の spawn prompt 契約」のレビュー指摘対応の行を必ず載せる (thread を閉じるのは worker だけで、observer も自分も閉じない)。対応が終わって PR が更新された後の駐機は、判定指針表の既存行 (agent が `idle`・`derived.closes_same_repo` に `open` の PR) がそのまま拾う |
| observer の「新しい候補が現れた」 | 候補プールの件数だけが届く (どれが候補かは書かれていない)。**C の候補選定を自分で通して読み直す** — 届いた件数を候補の一覧として使わない。着手可の判定・整列・除外はこちらのポリシーで、observer は持っていない (存在を観測して起こすだけの役) |
| observer の「観測できなかった」 | 前提不成立 (前提の表) なら user へ依頼。それ以外は observer の状態を `observe_panes` で確かめ、死んでいれば A の手順で確保し直す。**「観測できていない」を「変化が無かった」と読まない** |
| user からの指示 | 該当 worker への送信 (下記の規範) / 状況照会 / max の変更 / 終了 |
| user からの「人間との多ターン対話が本体である作業」の依頼 | **自分で処理しない。** concierge として別 pane へ spawn し、セッション名を返信で案内する (下記「concierge の spawn」)。spawn できなくても自セッションで代替しない |

**同じ事象が observer 経由と自分の照合の両方から来る。** どちらでも判定手順は同じ (観測が正) なので、二重に処理しないよう台帳の phase を先に見る。

イベントを 1 件処理したら、続けて `worktree_tidy` と補充 (C / D) を回してから待ちに戻る。**`active` が max 未満なら `observe_issues` は必須** — 「前回から変わっていないはず」を理由に飛ばさない。飛ばすと、その間に着手可になった issue は user が声をかけるまで拾われず、slot が空のまま遊ぶ。`done` の entry が 0 件で `worktree_tidy` に回収対象が無いときは省いてよいが、**省いた事実と理由を報告に書く** (呼ばなければ `ledger.unmappable` = 保護されていない entry の有無を報告できない)。`worktree_tidy` の保護対象 (`active` / `parked`) と回収対象 (`done`) は台帳から**issue slug で**自動導出される (番号を持たない tracker の entry も同じ集合に入る)。照合は作業ツリー名 / branch 名との突き合わせなので、**ツリー名が issue slug でないと保護も回収も効かない** — 綴りの規約は上の `pane_spawn` の表。**駐機したまま merge されたツリーは回収されない** — merge を観測したら先に `done` へ遷移させる。回収できた `done` は `cleaned` へ自動遷移する。dirty なツリーは消えず `skipped` に載る。

**`worktree_tidy` は clone root ごとに呼ぶ。** 1 回の呼び出しが掃除するのは渡した clone 1 つだけで、**どの clone を回るかは server が持たない**:

1. `ledger_list` の entry から clone root を列挙する — `agent.worktree` の絶対パスから `.claude/worktrees/i<N>` を除いた部分が root (`repo` はどの repo かの目印で、パスではない)
2. 重複を潰し、**cwd の clone は `repo_root` 無しで**、他は `repo_root: <clone root>` で呼ぶ
3. 回収した entry はその場で `cleaned` になるので、**同じ台帳を見る次の root の呼び出しが二重に遷移させることはない**。順序は問わない
4. 実在しない root は error で返る (server は clone しない)。その entry は報告に載せて次へ進む
5. **回収対象のツリーが既に消えている `done` は `cleaned` へ自動遷移しない** — `reclaimable_slugs` には載るが `ledger.cleaned` は空で返る。その entry は `ledger_transition("cleaned")` を手で書いて台帳を伸ばさない (非終端のまま残すと `resolve` が毎回 tracker CLI を叩き続ける)
6. 回収が効くのは **`agent.worktree` と同じパスのツリーだけ**。別 clone に同番号の残骸があっても消さず `excluded` (`recorded-path-mismatch`) に載る。`ledger.unattributed` は「回収したが記録と一致しないので entry を動かさなかった」もので、記録が間違っている印 — `agent.worktree` を直してから呼び直す

`worktree_sweep` (台帳の外の木の回収 = E2BIG 予防) も同じで、clone ごとに呼ばないとその clone では効かない。

**終了しない。** active が 0 かつ候補が 0 でも、その旨を報告して待機する。終了するのは user がそう言ったときだけ。

#### 沈黙した worker / 沈黙した observer

worker は質問しか送ってこない。**PR 到達・完了・無言死の検知は observer の周期観測が主経路**で、observer が沈黙している間は次の照合 (届いたものの処理・user の問い合わせ・次セッションの再入) まで遅れる。

疑いを確かめるときは `observe_panes` + `resolve` (`issue_ref`) の**単発観測**を使う。`pane_watch` は変化を待ち受ける tool なので、このセッションの主経路では使わない — 反復して呼べばポーリングの再導入になる。

#### 回収 / 駐機の判定指針

判断の入力は issue state / agent status (中立値と `agent_status_raw`) / PR status。**closes × repo 一致の突合は `resolve` が畳んで返す** (`current[].derived`) ので、`prs[]` の union から自分で絞り直さない。**`status` だけで駐機・回収を決めない** — 集約 `status` は closes 限定だが repo では絞られない (cross-repo の closing reference を落とさないため):

- `derived.closes_same_repo` = 台帳 entry の `repo` (dispatch した実装 repo) に居る closes PR = 自分の worker の成果。**駐機・回収の根拠はこれだけ**
- `derived.closes_other_repo` = repo が一致しない closes PR (fork や第三者の関連 repo からの closing reference)。集約 `status` には効いているが自分の worker の状態ではない。**根拠に採らず、PR 番号と repo を報告に添えて user 判断へ残す** (これを根拠に駐機すると、まだ作業中の worker を降ろす)
  - ただし**綴り体系のずれでここへ落ちているだけ**のことがある (entry が full path・観測が数値 project id 等)。`prs[].url` の repo path が entry の実装 repo と同じなら fork ではなく記録の誤りなので、`ledger_transition` の `prs` と entry の `repo` を観測側の綴りへ直してから判定し直す。**直さずに「同一 repo とみなす」判断を `note` に書いて運用しない** — 台帳に残ると次セッションが検証なしに引き継ぎ、禁止が恒久的に上書きされる
- どちらも `null` なら**未検査** (`[]` = 「無い」とは別物)。理由は `derived.mechanical_done.open_predicates` に出る — `entry_repo_unrecorded` (台帳導入前 / 記録漏れで entry に `repo` が無い) なら、issue 置き場と同じ repo の closes を自分のものと見なすかを判断し、その旨を `note` に残す

先に通す原則: **PR 成果 (`derived.closes_same_repo` に `open` / `checking` / `conflict` / `merged` の PR) のある issue は unclaim しない。** assignee がその issue の駐機 marker であり、外すと同じ issue に 2 本目の PR を作る dispatch が走る。PR 状態を観測できていない (`checked.prs` が false) issue も「不明」として据え置く。

| 観測 | 判断 |
|---|---|
| `derived.mechanical_done` が `satisfied: true` (`issue_closed` / `closes_merged_in_entry_repo` の発火。**同一 repo の `Closes` が merge されると 2 本とも発火する**) | pane が在れば `pane_close`。`done` へ遷移させ、`worktree_tidy` に回収させる — これが駐機ツリーの回収経路。**observer が先に記帳していることがある** (既に `done` なら遷移は不要で `worktree_tidy` から先を進める)。`rule_fired` に `closes_merged_in_entry_repo` があったら、続けて**下記「merge した変更を runtime へ載せる」を通す** (既に `done` でも通す — 記帳済みでも pull 済みとは限らない)。issue が open のままなら報告する: **PR と issue が別 repo なら自動 close されない**ので open のままが既定で、assignee が残って候補から外れ続ける。close 要否は user へ返す |
| agent 停止 (`exited` / `gone`)・PR 成果なし・**自己申告も無い** (途中死候補) | `released` へ遷移 + `issue_unclaim` してキューへ返す |
| worker が「作業不要」「検証のみ完了」を申告・`derived` の closes 2 列が空・worktree clean | `pane_close` → `done`。**`released` + `issue_unclaim` は採らない** (成果の無い未着手として候補プールへ戻り、同じ作業が再 dispatch される)。issue は open のまま残るので、**close 要否は `note` と報告に書いて user へ返す**。**申告を完了の証拠として扱う唯一の経路**なので、申告の逐語と worktree clean の観測を `note` に両方残す |
| agent 停止・PR 成果あり | **駐機**: `parked` へ遷移 (`agent` の `pane_id` を null に)。worktree と assignee は残す |
| agent が `idle`・`derived.closes_same_repo` に `open` の PR | **駐機**: `pane_close` → `parked`。pane close はそこまでの対話を捨てる不可逆操作なので、この条件は狭く取る |
| PR が `conflict` | 解消を起動する (下記)。**駐機しない** — 稼働中 worker を閉じると解消の起動先が消える |
| `agent_status_raw` が `blocked` / `unknown` | 触らない。契約どおりなら質問はメッセージで届くので、残る `blocked` は permission ダイアログ待ち (メッセージでは答えられない)。`unknown` は判定不能 |
| PR が `checking` | 次に触るときに再観測する (「conflict 無し」と読み替えない) |
| `derived` の closes 2 列がどちらも空で `prs[]` に PR が居る (mention だけ) | 別 issue の PR が本文で番号に言及しただけでありうる。駐機・conflict 対応の根拠にせず、PR 番号を報告に添えて user 判断へ残す |
| `derived.closes_other_repo` に merged の PR | **自分の worker の成果ではない** (fork や第三者の関連 repo からの closing reference)。`done` へ送らず、PR 番号と repo を報告に添えて user 判断へ残す — 送ると駐機中のツリーが保護対象から外れて回収される |

`note` には**何を観測してその phase にしたか / 次に何を待っているか**を書く。次のイベント処理・次セッションの自分が判断を再現できることが唯一の基準。

**phase が動かないまま状況だけが動いたら `ledger_annotate` で `note` を更新する** (遷移を伴う更新は従来どおり `ledger_transition` の `note`)。`active` のまま質問の回答を待っている / 前提の裏取り中で停止ではない、といった文脈は observer の判定材料でもある — 台帳に書かないと、SendMessage で伝えた分は自分の再起動で消え、observer は `idle` を停止と読んで escalation を繰り返す。同一 phase への遷移 (`active → active`) は非合法のままで、phase を往復させて代用しない (`parked` は「pane を降ろした」の意味を失う)。

駐機した issue には worker がもう居ない。**merge の検知は observer の周期観測が主経路**で、observer が止まっている間は「次にこのセッションが動いたとき」— 届いたものの処理・user の問い合わせ・次セッションの再入 — に落ちる。止まった observer はその起床の冒頭で確保し直されるので、遅れは次の起床までで頭打ちになる。台帳が永続なので取りこぼしても失われはしない。

#### merge した変更を runtime へ載せる

**merge は deploy ではない。** plugin 実体は cache コピーを持たず main チェックアウトの working tree を in-place で読むので、`origin/main` が正しくても**ローカル main が古ければ runtime は古いまま動き続ける**。merge 成功・CI 緑・issue closed・PR "Merged" と「有効になった」と読める材料だけが揃い、無効であることを示す信号は一つも出ない。

**`closes_merged_in_entry_repo` を観測したら、その場で通す。** 起動時にまとめて引くのでは足りない — このセッションは常駐なので、事故は常駐中の merge で起きる。

1. **clone root を復元し、pull 前の HEAD を控える** — clone root は台帳 entry の `agent.worktree` の絶対パスから `.claude/worktrees/<slug>` を除いた部分 (`worktree_tidy` と同じ導出)。**`git -C <clone root> rev-parse HEAD` の値を控えてから次へ進む** — 手順 4 の diff の基点で、pull した後では取り戻せない
2. **`git -C <clone root> pull --ff-only origin main`** — `--no-rebase` を使わない。main に merge commit を作らせず、ff できない状況では大きく失敗させる
3. **着地を検証する** — `git -C <clone root> rev-parse HEAD origin/main` が同じ sha を 2 行返すこと **かつ** `git -C <clone root> status --porcelain` が空であること。**両方見る** (片方だけでは半適用を見逃す)
4. **diff を分類して報告する** — `git -C <clone root> diff --name-only <手順 1 で控えた sha> HEAD`

| 触った path | 報告 |
|---|---|
| `hooks/**` | 載った (呼び出しごとに exec されるため) |
| `skills/**/SKILL.md` | 次の invoke から載る。**このセッションの orchestrator 本文は文脈済みなので載らない** |
| `mcp/**` `hooks.json` `plugin.json` `.mcp.json` `settings/**` | **載っていない。「再起動が要る」と明示して user へ報告する** — 登録と server プロセスは session 起動時に確定する |

**fail posture は「止めて返す」。リトライしない**:

- **pull 後にツリーが dirty なら止める。** sandbox の自己改変保護は `hooks/` への write を拒み、**HEAD 据え置き + working tree だけ書き換わった中途半端な状態**を作りうる (同ディレクトリの `README.md` の `sandbox.excludedCommands` の節)。**半適用の deploy は綺麗に古いままより悪い**ので、その上で再試行しない。逐語で報告して user の判断へ返す
- **pull 自体が失敗しても止める。** main が dirty なのは異常 (worktree 必須ルールが clean を保つ建て付け)。原因を推測して `--force` や `stash` へ逃げない
- 止めた場合も `done` 遷移と `worktree_tidy` は通してよい (deploy の失敗と dispatch の完了は別事象)。ずれたままであることを `ledger_annotate` の `note` に残す

**引くのは merge が着地した clone 1 つだけ** (手順 1 で復元したもの)。他の clone は今回の観測の対象外なので触らない — 別の entry の merge を観測したときにその clone が引かれる。**runtime が直るのは、着地先が plugin 実体の clone だったときだけ**である点に注意する (他 repo の merge を引いても hook / skill は変わらない。それでも次の worker が古い土台から始めるのを防ぐので引く価値はある)。

**稼働中 worker の足元で hook script が差し替わる**ことは避けられない。merge 後 / CI 緑のコードなので確率は低いが、fail-closed guard が壊れた版に入ると全 worker が同時に止まる。pull の直後に worker の沈黙が揃ったら、まずこれを疑う。

worker 側の `git pull --no-rebase origin main` (spawn prompt 契約) はこれを代替しない。あれは**worktree の中**で走って `origin/main` を worker の branch へ merge するもので、main チェックアウトの `main` も working tree も動かさない。

#### worker へ送るときの規範

主経路は **SendMessage** (初回コンタクトで確立したアドレス宛)。`pane_send` はアドレスを失った worker への fallback。送る内容の規範は経路によらず同じ:

- 送るのは「放置すると作業自体が無駄になる」ものに限る (conflict 解消が典型 — 放置すると push が詰む)。**追加指示は割り込まない** — 数分遅れても結果は変わらず、元タスクとの混線リスクだけが残る。駐機を待ってから `cwd` 再入で渡す
- **`agent_status_raw` が `blocked` の worker へ送らない** — 人間宛の permission ダイアログはどちらの経路でも答えられず、送ってもキューに積まれるだけ。送る前に `observe_panes` で raw status を見る (中立語彙では `running` に潰れる)
- user から預かった指示は逐語で渡す。orchestrator が指示を創作しない
- **同じ指示を再送しない** (混線を増やすだけ)。効かなければ user に渡す
- `pane_send` は `issue_ref` を添えると events.jsonl に残る。SendMessage は残らないので、記録を残したい送信は `ledger_annotate` で `note` に書く (phase は動かない)

#### concierge の spawn (人間との対話が本体の依頼)

**「人間との多ターン対話が本体である作業」の依頼は自セッションで処理しない。** remote-control 付きの独立セッションを別 pane に spawn し、人間とはそのセッションが直接対話する (ADR 0044)。自分で受けると observer の escalation と worker の質問が対話に割り込み、対話の全文が配車判断のコンテキストを希釈する。worker として dispatch する経路も使えない — worker 契約は AskUserQuestion 禁止 + 質問の中継なので、対話が本体の作業とは構造が逆立ちする。

- **対象判定は性質基準** —「人間との多ターン対話が本体か」で判断する (例: `/grill-with-docs` のような対話型 skill の依頼)。**skill の allowlist にしない** (対話型 skill が増えるたびに更新が要る drift 源になる)。一問一答で済む照会・状況確認は対象外で、その場で答える
- 起動は `pane_spawn(label: "concierge-<話題 slug>", prompt: <下の規定で組んだ初期 prompt>, model: "fable", remote_control: true)`。`remote_control` が組む `--remote-control` のセッション名は server が label から与えるので、**セッション名を prompt 側で書かない**
- **台帳に載せない。** `issue_claim` / `ledger_record` を通さず、`active` の slot も消費しない — label 起動の pane は `tracked` にも台帳にも載らず、observer の観測対象にもならない (この免除は既存規定がそのまま与えるので、新しい仕掛けは要らない)
- **label は `concierge-<話題 slug>`** (英小文字・数字・ハイフン。issue slug 表記 `i<N>` / `<project>-<N>` は予約で server が弾く)。slug は依頼内容からこちらが採る
- **`model` は既定 `fable`**。依頼時に user が別のモデルを指定したらそれで上書きする
- **初期 prompt は依頼文 + skill の invoke 指示 + 終了条件だけ。** orchestrator 側の経緯・台帳の状況・他 issue の話は渡さない (逆向きの汚染を避ける)。**worker 契約と違い AskUserQuestion を許可し、orchestrator への SendMessage 義務も課さない** — 相手は人間で、対話がその pane に閉じるのが目的
- **成功・失敗とも必ず一言返す。** 成功したら**セッション名 (= label) を user へ案内する** (remote-control 一覧から拾えるようにする)。`ok: false` や error なら `pane_close` で畳んでから断って報告する。**silent fallback 禁止** — 起動できないからといって自セッションで代わりに対話しない (コンテキスト汚染を避けるという目的の自己否定になる)
- **成果は fire-and-forget。** orchestrator への報告経路は作らない (成果は tracker 等の外部 store に落ちる)。途中死も誰も検知しない — 対話の相手が人間なので、沈黙は人間が直接気づく前提で受容する

### 報告

起動直後・イベントを 1 件処理したとき・user に尋ねられたときに出す。

**出し方** — user は「今どの issue がどうなっていて、自分は何をすればよいか」を読み取るために報告を読む:

- **起床したら、最初の tool 呼び出しの前に、これから何を処理するかを 1 文で言う。** そこから報告までの間は、判断が変わったとき (回収した / 駐機した / 見送った / 前提が崩れた) だけ短く出す。`resolve` や `observe_panes` を今から叩くといった実況はしない
- 報告は結論から始める (1 文目が「この起床で何が動いたか」に答える)。分量は下の列挙と判断の根拠に使い、前置きと注意書きは短くする。観測の生データを貼らず、判断とその根拠になった観測だけを書く
- 前回の報告から変わらない項目は 1 行にまとめてよい。**ただし放置すると悪化する項目 (観測が止まっている = 候補プールも観測されないので、新しく着手可になった issue は user が声をかけるまで拾われない / 回収できていない / assignee が残り続けている / 着弾していない相手が居る) は、変化が無くても毎回明示する** (黙ると静かな正常系と見分けが付かない)
- **観測していない項目を現況として書かない。** 候補プールの件数と observer の状態は、**その起床で `observe_issues` / `observe_panes` を実行したときだけ**現況として書く。していなければ「未確認 (最終確認 HH:MM)」と書く — 「観測して 0 件」と「見ていない」が同じ文言になると、読者にも次セッションの自分にも区別が付かず、手順を省いたことが誰にも見えなくなる

少なくとも次を含める:

- 起動した worker (issue / pane_id / **実装 repo と worktree の絶対パス**)、skip した候補と理由、未検査で残した候補数 (`truncated` を含む)
- **clone が見つからずに見送った issue** (どの repo の clone が要るか。user が clone するまで着手できない)
- **自 pane の rename 結果** (`orchestrator` になった / 既に `orchestrator` だった / 他に `orchestrator` pane や peer session が居て止まった / 観測失敗や rename 失敗で付けられなかった)
- **observer の状態** (居る / 起動した / 起こし直した / 確保できなかった / この起床では未確認)。確保できていない間は外形観測が止まっている旨を明示する — 黙ると「静かで平穏」に見える
- 初回コンタクトが着弾していない worker / observer (質問も escalation も来ない前提で扱う旨)
- 駐機した issue とその PR (番号 / status / URL)。**merged なのに issue が open のもの**は assignee が残り続けるので明示する
- 台帳の `outcome` — 人手が要ると自己申告したものは理由付きで、申告が無く PR 成果も無いものは途中死候補として
- 観測した drift と、それをどう解消したか (解消しなかったものは理由)
- `worktree_tidy` の結果を **clone root ごとに** (回収したもの / dirty で残したもの / issue slug を起こせず保護できなかった台帳 entry = `unmappable`。黙殺すると保護したつもりの worktree が消える)。**掃除できなかった clone** (root が実在しない / `agent.worktree` が未記録で root を復元できない) も名指しする — 黙ると worktree が溜まり続け、その clone で E2BIG (worktree 蓄積で全 Bash が起動不能になる障害) が起きる
- 待機に戻ることと、user が今できること (worker への指示・pane 移動での直接介入・終了指示)

## cross-tracker (issue 置き場と PR 置き場が別 tracker)

**読むのは `observe_project` の `issue.tracker` が `jira` のときだけ。** GitHub 単独 / GitLab 単独、および issue と PR が同じ tracker の project では A〜E をそのまま通す (この節は該当しない)。

dispatch-ops が adapter を持つのは `gh` / `glab` の 2 つだけで、**issue 置き場が Jira の project では issue 側の tool が全部 error で落ちる** (`observe_issues` / `issue_claim` / `issue_unclaim` / `issue_comment` / `issue_label`)。PR / pane / worktree / 台帳は落ちない — PR 置き場は宣言の `[pr]` で GitLab に解決され、台帳は中立 ref (`jira:<KEY>-<N>`) をそのまま持つ。

**分岐するのは「どの tool で issue を触るか」だけで、A〜E の順序も台帳と pane の手順も変わらない。**

### tool 群の切り分け

| 何を触るか | issue 置き場が gh / glab | issue 置き場が jira |
|---|---|---|
| issue の観測 (候補選定) | `observe_issues` | Rovo MCP `searchJiraIssuesUsingJql` |
| issue 1 件の現況 | `resolve` の join が見る | Rovo MCP `getJiraIssue` (`resolve` の `checked.issue` は false のまま) |
| claim / unclaim | `issue_claim` / `issue_unclaim` | Rovo MCP `editJiraIssue` (assignee) |
| label / stage 遷移 | `issue_label` | Rovo MCP `editJiraIssue` / `transitionJiraIssue` |
| issue へのコメント | `issue_comment` | Rovo MCP `addCommentToJiraIssue` |
| MR の一覧 (発見) | `observe_prs` (issue_ref なし) | **経路が無い** — glab adapter が明示 repo scope 未対応なので error。番号は worker の `outcome` から得る (下記) |
| **記録済み MR の追跡** | `observe_prs` / `resolve` | **同じ** (宣言の `[pr]` へ解決される。台帳の `prs[]` に記録した MR だけ) |
| **pane / worktree / 台帳** | `pane_*` / `worktree_*` / `ledger_*` | **同じ** (issue_ref に `jira:<KEY>-<N>` を渡す) |

- **Rovo の tool は deferred** — 一覧に出ていないので `ToolSearch` で schema を取ってから呼ぶ (`select:mcp__claude_ai_Atlassian_Rovo__<name>,...` で名指し)。取らずに呼ぶと InputValidationError で落ちる
- Rovo の tool は `cloudId` を要る。`getAccessibleAtlassianResources` で 1 度引いてセッション中は使い回す
- **Rovo に到達できないときは issue 側を「未検査」に倒す** — 候補選定を行わず (dispatch しない)、既存 entry の issue state は「不明」として据え置く。open と読んで dispatch すると、既に closed の issue に worker を張る
- **glab adapter は明示 repo scope 未対応** — 宣言に `repo` が入っている GitLab の置き場では、識別子を受け取った tracker 系 tool が CLI を起動する前に落ちる (`明示 repo scope 未実装`)。cross-tracker に限った話ではないが、この構成で当たるのは MR の一覧だけ (issue 側は Rovo へ回り、記録済み MR の追跡は識別子を受ける別経路を通る)

### C の差分 (候補選定)

`searchJiraIssuesUsingJql` で置き場の project を引く (宣言の `repo` が project key)。**着手可の意味を label / status 体系から推定するのも、除外規則 (assignee 付き / 台帳に非終端 entry) も gh / glab と同じ。**

- 中立 ref は **`jira:<KEY>-<N>`** (key は大文字)。台帳と pane へはこの形で渡す
- blocker は `getJiraIssue` の issue link から読む。読めなければ blocked 扱いで skip する (`blocked: null` と同じ倒し方)

### D の差分 (起動)

順序は変わらない (**claim → `ledger_record` → `pane_spawn` → `ledger_transition("active")` → 初回コンタクト**)。差分は 3 点:

1. **claim は `editJiraIssue` で assignee を自分に設定する** (accountId は `lookupJiraAccountId` で引く)。失敗したら起動せず次候補へ、は同じ
2. **pane label / 作業ツリー名は key を小文字化した slug** — `jira:SWATCF-14` なら `swatcf-14`。`pane_spawn(issue_ref: "jira:SWATCF-14", worktree: "swatcf-14")` で、作られる branch は `worktree-swatcf-14`
3. **`agent.branch` を記帳しておく** — 自己申告が届かなかった worker の MR を `glab mr list` から拾うときの照合鍵になる (下記)。`pane_spawn` は branch 名を返さないので規約 (`worktree-<worktree 名>`) で書く。**`observe_worktrees` では確かめられない** — 一覧に載るのは `i<N>` 規約の worktree だけで、`resolve` が記録パスを見に行く経路も `branch` は null 固定。確かめるなら Bash で `git -C <agent.worktree> rev-parse --abbrev-ref HEAD` を読む。`agent.worktree` の絶対パスも従来どおり要る

**実装 repo の clone がローカルに実在することの確認は、この構成では毎回通る検査になる。** 実装先は必ず PR 置き場 (GitLab) 側の clone で、cwd の clone とは限らないため — `repo_root` にその絶対パスを渡す。無ければ D の 3 (dispatch を見送って報告) へ倒す。

worker の spawn prompt は、closing reference の行を次の 3 行で置き換える:

| 文面に入れるもの | 欠けたときに壊れるもの |
|---|---|
| **`ledger_report_outcome` に渡す中立 ref を逐語で書く** (`jira:<KEY>-<N>`) | worker は Jira key から中立 ref の綴りを導けず、自己申告が別 ref の新規 entry に落ちる |
| **`outcome` に MR 番号を必ず含めさせる** (「PR に到達した: `<MR 番号>`」)。単一 tracker では推奨だが、cross-tracker では**唯一の発見経路** | MR がどこにも観測されず、merge も conflict も永久に検知されない (下記) |
| **MR 本文に closing reference を書かせない。** 代わりに MR の説明へ Jira key を書かせ、**branch 名を変えない**ことを要求する | GitLab の `Closes #<N>` は **GitLab 側の同番号 issue** を指し、無関係な issue を閉じる。branch 名が変わると、申告が届かなかったときの拾い直しができなくなる |

### MR を台帳へ結び付ける (この構成の要)

**dispatch-ops には MR を発見する経路が無い。** closing reference が tracker をまたげないので `observe_prs(issue_ref=...)` では引けず、`observe_prs()` (引数なし) も **glab adapter が明示 repo scope 未対応**なので、宣言の `[pr].repo` が既定で入った時点で `明示 repo scope 未実装` の error になる。

**記録さえすれば追跡は成立する** — `resolve` の PR 観測は台帳の `(repo, ref)` から直接引く経路を通り、そちらは明示 repo を受ける。だから手順は「MR 番号をどこから得るか」に尽きる:

1. **主経路は worker の自己申告。** spawn prompt が `ledger_report_outcome` の `outcome` に「PR に到達した: `<MR 番号>`」の綴りを要求しているので、`ledger_list` の `outcome.status` から番号を読む。**これは観測ではなく申告だが、ここでは観測する対象を特定するためだけに使う** (status は記帳した後に `resolve` が観測する)
2. `ledger_transition` の `prs` へ **`ref` (`glab!<番号>`) / `role: "closes"` / `repo`** を 3 つ揃えて記帳する。`repo` は **`observe_prs` / `resolve` が返す `prs[].repo` (glab では数値 project id) を逐語で写す** — 宣言 (`observe_project` の `pr.repo`) が full path だと綴りが揃わず、突合がその差を吸収しない。entry 側の `repo` も同じ id に揃える (D の記帳)
3. 以後 `resolve(issue_ref=...)` がその MR の status を観測する。**記帳しない限り、merge されても drift は出ない**

- **申告が届かないまま停止した worker の MR は、dispatch-ops からは一生見えない。** GitLab の clone で `glab mr list` を自分で読み、`agent.branch` と一致する MR の番号を拾って 2 を行う。それもできなければ「PR の有無が不明」として user へ報告し、**`released` へ送って unclaim しない** (成果ごと候補へ戻すことになる)
- `role` は台帳の記録がそのまま信じられる — **`mention` を `closes` と記帳すると、無関係な MR の merge で駐機ツリーが回収される**
- observer が「台帳に PR 記録が無い」で escalation してきた entry は、その場で 1〜2 を回す

### E の差分 (worktree 名を key slug で切る)

**保護 / 回収は番号ではなく issue slug で導出されるので、jira entry も gh / glab と同じように tidy / sweep の両方で守られる**。差分は 1 点だけ — **`pane_spawn` の `worktree` に渡す名前を key slug (`swatcf-14`) にする**こと。

- 照合は「台帳が持っている slug の集合に、branch 名か worktree ディレクトリ名が入っているか」で行う。`pane_spawn` の `worktree` に別の綴り (`impl-1` 等) を渡すと集合に当たらず、駐機中の保護も `done` の回収も効かない
- `agent.worktree` には従来どおり `<clone root>/.claude/worktrees/swatcf-14` の絶対パスを記帳する (回収の突合はパスで行うため)
- **key slug のツリーは `observe_worktrees` の一覧に載らない** (掃除の照合とは別経路で、一覧に出るのは `i<N>` 規約の木だけ)。ツリー名を確かめるなら `agent.worktree` のパスか Bash の `git -C <clone root> worktree list` を読む。`resolve` は記録パスの probe で見に行くので、消失 / 残置の検知は従来どおり効く
- `worktree_tidy` / `worktree_sweep` を呼んだら **`ledger.unmappable` を毎回報告に載せる**。正常な台帳では空で、載るのは slug を起こせなかった entry (手で編集された台帳 / 未知の ref 語彙) — 保護されていない entry の一覧なので黙殺しない
- `done` へ送った jira entry は gh / glab と同じく `worktree_tidy` が回収して `cleaned` へ自動遷移させる。**tidy が回収できたときは** `cleaned` を手で書かない (書くと二重遷移で `transition_errors` に落ちる)。回収対象のツリーが既に消えていて `ledger.cleaned` が空だったときだけ手で書く (E)

### observer との分担

observer も同じ切り分けで動くが、**issue 側は Rovo の読み取りだけ** (`getJiraIssue` / `getAccessibleAtlassianResources` / `searchJiraIssuesUsingJql`) で、claim / label / コメント / transition はこのセッションが持つ。**候補プールは observer が毎 tick 観測する** — `searchJiraIssuesUsingJql` の 2 呼び出し (件数 + `ORDER BY updated DESC` の先頭ページ) で**存在**だけを見る役で、着手可の判定・整列・除外は C の候補選定としてこのセッションが持つ (上の切り分け表の「issue の観測 (候補選定)」は jira でもこのセッションのまま)。届いた「新しい候補が現れた」の扱いは gh / glab と同じで、件数だけを受け取って C を自分で通し直す。cross-tracker で observer が `done` を書ける根拠は「記帳済み MR が merged」と「Rovo で読んだ issue が closed」の 2 つで、**MR を記帳していない entry では前者が成立しない** — `stores.prs.errors` を根拠にした escalation が来るので、上の「MR を台帳へ結び付ける」を回す。

## 責務境界

orchestrator の責務は配車と取りまとめ、そして user と worker の間の中継。conflict は解消作業の**起動まで**。周期的な外形観測は observer の領分で、observer が上げてくる判断はこちらが持つ。**merge を観測した後の deploy (着地先 clone の main を最新にする) もこちらの責務** — 環境を変える操作は observer に持たせない (E の「merge した変更を runtime へ載せる」)。

**並列化はすでに pane (worker / observer / concierge) の形で表現してある。** 観測 (`resolve` / `observe_*`)・台帳への記帳・回収と駐機の判断は、このセッションが自分の手で行う — Task subagent へ委任しない。台帳の書き手が 1 つでなくなると、どの観測でその phase にしたかを次セッションの自分が再現できなくなる。

**この禁止の射程は台帳に触る仕事**で、禁止理由は書き手を 1 つに保つこと。**台帳へ一切触れない concierge の spawn (E) は対象外** — 別 pane へ出すのがむしろ正しい形で、自セッションで抱えるほうが規約違反。

以下は worker / observer / user の領分:

- 実装・調査・triage の中身、issue を close する判断、PR の作成・merge、conflict 解消そのもの
- outcome の自己申告 (各 worker が `ledger_report_outcome` で残す。**代理申告も推測での補完もしない** — 埋めると「途中死を検知できる」という契約が壊れる)
- 周期的な外部状態の観測と、そこから一意に決まる `done` 記帳 (observer。**その 2 つ以外を observer に判断させない**)
- 駐機 worktree の破棄。orchestrator が回収するのは `done` へ送れたものだけで、dirty なツリーと未 merge branch は報告して user に返す (`branch -d` の `-D` 昇格は server も skill も行わない)
- permission 待ち (`blocked`) の解除 — ダイアログはメッセージで答えられないので user が pane に入って解く
- **clone root (作業ツリーの外) にある実体の編集。worker の sandbox が拒否した書き込みを肩代わりしない** — user の承認があってもやらない。その clone に外部の自動 commit 機構 (backup / sync) が居ると変更が main へ載り、同じ file を触る駐機中の PR を delete/modify conflict にする。台帳 `note` に「単体で commit しない」と書いても、commit する主体は orchestrator ではないので効かない。**deploy の `pull --ff-only` (上記) は別** — あれは main を上流に追いつかせるだけで、自分の変更を持ち込まない
- label 遷移
- 外部タスク管理サービスとの連携は行わない (入出力は issue tracker と台帳に閉じる)

**完了判定は issue state が正で、PR 状態でも worker のメッセージでも完了扱いにしない** — 駐機は「slot を降ろす」操作であって「終わった」という判定ではなく、メッセージは観測ではなく自己申告。**例外は「成果物が出ない作業 (調査・検証)」の 1 経路だけ** (判定指針の表)。ここでも `done` は台帳の締めであって issue の close ではなく、close 判断は user に残る。

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| tool が見えない | plugin 未有効化 / server 設定後にセッションを起動していない | `/mcp` で `plugin:swat-skills:dispatch-ops` が connected か確認する。無ければ `/reload-plugins` かセッション再起動 |
| merge したはずの hook / skill の挙動が変わっていない | **ローカル main が古い。merge は deploy ではない** (plugin 実体は main チェックアウトを in-place で読む)。信号が一つも出ないので「有効になった」と読める材料だけが揃う | E の「merge した変更を runtime へ載せる」を通す。`mcp/**` や `hooks.json` を含む変更は pull しても載らないので、再起動を user へ依頼する |
| `pull --ff-only` の後にツリーが dirty | sandbox の自己改変保護が `hooks/` への write を拒み、**HEAD 据え置き + working tree だけ書き換わった**半適用状態 (`git pull:*` が `sandbox.excludedCommands` に無い環境で起きる) | **止めて user へ返す。リトライしない** — 半適用の上での再試行が最も壊す。settings の不足なら同ディレクトリの `README.md` の `sandbox.excludedCommands` の節を案内する |
| 初回コンタクトが `No agent named ... is reachable` で撥ねられる | ListAgents 登録前 (起動から十数秒) | 数秒おいて引き直し、`name [ref]` 表記が出たらそれで送り直す。**起動失敗として巻き戻さない** |
| 初回コンタクトが `N agents are named 'observer'` で撥ねられる / 送信の返り値に `other live sessions are also named` が付く | pane に映らない同名の peer session が生存している (pane を畳んでも messaging registry からは即座に消えない) | エラーが列挙する候補のうち**活動時刻が最も新しいもの**へ ref 付きで再送する。**live な同名 observer が複数居る間は、escalation の帰属 (どの observer の記帳か) を自分の観測なしに断定しない** — 余分なセッションは user に閉じてもらう |
| worker から何も届かない | **正常** — worker の契約は質問のみで、PR 到達も完了も送ってこない | 何もしない。PR / 完了は observer の escalation か、届いたものを処理するついでの `resolve` で拾う |
| observer から何も届かない | 静かな正常系と、observer の死 (loop の aged out / 契約不履行 / 初回コンタクト未着弾) が同じ見え方をする | E の起床冒頭の死活確認が拾う。疑うならその場で `observe_panes` を引き、A の手順で確保し直す。**「escalation が来ない」を「変化が無い」と読まない** |
| 起動時に「他に `orchestrator` pane が居る」で止まる | 前回の orchestrator セッションの pane が残っている / 2 体目を起こそうとしている | **窓口は 1 つ** — どちらを残すか user に決めてもらう。残さないほうの pane を user が閉じてから起動し直す (このセッションから勝手に閉じない — 進行中の対話を捨てる) |
| observer が同じ escalation を送り続ける / 同じ事象の escalation が数通まとめて届いた後ぱたりと止む | 打ち切りカウンタ (送信回数。3 回で打ち切り) が loop 指示文へ書き戻されていない。tick を跨ぐ保存先はそこしか無く、落ちるとカウンタ 0 から数え直しになる。**または orchestrator が AskUserQuestion 等で長くブロックし、escalation が配送されていない** — この場合カウンタは正常動作しており、observer skill を直しても意味が無い (原因は受信側。打ち切り後は同じ事象を二度と上げない) | 判断を返して閉じる。返せない (user 待ち) なら `ledger_annotate` で `note` に「保留中」と書く (observer は送信前に note を読み、観測と整合すれば打ち切る)。それでも続くなら observer skill 本文のカウンタ持ち回り規定を直す |
| observer が判断を要する遷移を勝手に記帳した | observer skill 本文の「機械的遷移だけ」が効いていない | 台帳を現実に合わせ直す (B の判断)。observer skill 本文の許可する 2 遷移を書き直す。**PR 成果のある issue が unclaim されていたら二重 dispatch を先に塞ぐ** |
| pane が生きている entry が `done` になっている | observer が pane を確かめずに merged / closed を記帳した | **`done` は巻き戻せない** (`done → cleaned` だけが合法)。`done` は worktree の保護対象から外れるので、**その worker が終わるまで `worktree_tidy` を呼ばない**。降ろしてよいと判断したなら `pane_close` してから tidy する。予防が唯一の対策なので、observer skill 本文の「pane が居ないことを確かめてから書く」を書き直す |
| `pane_spawn(label: "observer")` が「既にある」で撥ねられる | label が占有されている。`pane_spawn` は label の空きだけを見て agent の生死を見ないので、**生きている observer と、agent が死んだ残骸 pane の両方**でこう返る | `agent_status` を見て分ける (A の確保手順の表)。生きていれば失敗ではない (初回コンタクトだけ送り直す)。`exited` / `agent` が null なら `pane_close(pane_id)` してから spawn し直す — 畳まない限りその label は永久に塞がる |
| orchestrator を再起動したら未読が消えた / 再起動後に live worker から何も来なくなった | メッセージは揮発する (保留は上限 100 件 / 5 分で drop、再起動後の復元は無い)。加えて worker が持つ返信先は前プロセスの UDS アドレスで、再起動で死ぬ | 起動時の A (再入) が台帳と tracker から拾い直し、**pane が生きている `active` entry には初回コンタクトを送り直す**。メッセージを真実源にしない |
| 人間との長い対話の途中に worker の質問と observer の escalation が割り込む / 配車判断のコンテキストが対話の全文で薄まる | 「人間との多ターン対話が本体」の性質基準を判定し損ね、依頼を自セッションで処理した。または spawn に失敗して黙って自分で引き受けた (silent fallback) | concierge として別 pane へ出し直す (E の「concierge の spawn」)。**出せないなら断って user へ報告する** — 自セッションでの代替は目的の自己否定。残った `concierge-*` × `exited` の pane は `observe_panes` を見たついでに畳む |
| worker が pane 内で質問を出したまま止まる | spawn prompt の AskUserQuestion 禁止が効いていない | `blocked` へは送らない。user に pane 直接介入を依頼し、以後の起動 prompt で契約を明示し直す |
| 自分の pane が slot を 1 消費している / dispatch した覚えのない issue が稼働中に見える | orchestrator 自身の pane label が前回の残骸 `i<N>` のまま | 起動手順 0 が `orchestrator` へ rename する (残骸 `i<N>` は最初の pane 操作で server が `dispatch` へ付け直し、その上から 0 が `orchestrator` を書く)。slot は `is_self` を除いて数える |
| `pane_spawn` が `ok: false` を返す | agent 検出の poll 内に起動が間に合わない / prompt の崩れで pane の shell が誤解釈した | pane が label を占有したまま残るので `pane_close` → `spawn_failed`。再試行は 1 度だけで、2 度目の失敗は user へ返す |
| `observe_panes` が `pane <ID> の応答に agent field が無い` で丸ごと失敗する | **追跡 pane (`i<N>` label)** の応答が壊れている。欠落を「セッション終了」と誤読しないよう fail-closed で raise する。`resolve` は同じ失敗を `stores.panes.observed: false` へ降格して返す。追跡外の pane (agent を起動していない素の shell 等) の欠落では失敗せず、`agent: null` として観測結果に載る | slot は台帳の `active` entry 数で数えて進む。raw status を観測できない間は worker へ送らない。該当 issue は user に状況を確認して、要らなければ `pane_close` で降ろす |
| 集約 `status` が `none` なのに `count` が 1 以上 | `status` は `closes` 限定、`count` / `prs[]` は `mention` 込みの union。closes PR がまだ無い状態 | 矛盾ではない (`derived` の closes 2 列が空なのが根拠)。駐機・回収の根拠にはせず、`prs[]` の mention PR を番号付きで報告に添える |
| 同一 issue に 2 セッション | claim 前に spawn した / 別マシンから同時 dispatch した | claim → record → spawn の順を守る (同 label の pane を server が撥ねるのが二重の防波堤) |
| PR を出したのに駐機されず slot が塞がる | PR 本文に closing reference が無く、`role` が `mention` 止まり | PR 本文に closing reference を足せば次に触るときに駐機される。急ぐなら user が pane を閉じる |
| 実装 repo の clone がローカルに無い | project の関連 repo をまだ clone していない | **dispatch を見送る** (`git clone` しない)。claim 済みなら `issue_unclaim` して候補へ返し、報告に載せて user に clone を依頼する。`pane_spawn` に無いパスを渡しても error で撥ねられるだけで、clone はされない |
| 関連 repo に PR を出したのに、その repo 内の無関係な issue が巻き込まれた / 本来の issue が `mention` 止まり | worker の PR 本文が `Closes #<N>` (非修飾) で、番号が PR の repo 内で解決された | PR 本文を `Closes <owner>/<置き場 repo>#<N>` へ直させ、巻き込んだ issue は user へ報告する。以後の spawn prompt で cross-repo 表記を逐語指定し直す |
| 観測・claim・label がよその repo へ向いた | project の宣言が置かれていない (server が cwd 推論へ落ちた) / 宣言の repo の綴りが違う | `observe_project` で宣言を確かめる (`repo` が null なら宣言が効いていない。`config_path` が効いた file / null = 未設置) 。誤って claim / label した issue は元へ戻す。直す先は**台帳ディレクトリの `dispatch-project.toml`** (version 管理外・書式は `docs/agents/issue-tracker.md`) で、tool 引数ではない。直したら server の再起動が要る |
| 別 clone の作業ツリーが `worktree_tidy` で回収されない | `repo_root` を渡していない (1 回の呼び出しが掃除するのは渡した clone 1 つだけ)。または `agent.worktree` が未記録で clone root を復元できない | 台帳から clone root を列挙して root ごとに呼び直す (E)。記録が無い entry は回収の当てが無いので、パスを user に確かめてから `ledger_transition` の `agent.worktree` に絶対パスで足す |
| `resolve` が「worktree が無い」と言うが実際は別 clone に在る | entry に `agent.worktree` が無い (記録パスが無ければ照合先も無い) | `worktree_missing` を成果消失と読まない。`observe_worktrees` にその clone の `repo_root` を渡して実在を確かめ、絶対パスを記帳し直す |
| 別 clone の entry に worktree の drift が一度も出ない / 終端 entry に `worktree_present` が出続ける | `agent.worktree` に clone root (新規隔離時の `cwd`) を記録した。root は常に実在するので照合が必ず「在る」と答える | `agent.worktree` を `<clone root>/.claude/worktrees/i<N>` へ直す。tidy の回収対象になるのは実ツリーのパスを持つ entry だけ |
| `worktree_path_mismatch` が出る | 同じ `i<N>` のツリーが記録と別のパスに在る (別 clone の残骸か、記録が作業ツリー以外を指している) | どちらが自分の worker のツリーかを `observe_worktrees` に各 clone の `repo_root` を渡して確かめる。記録が誤りなら `agent.worktree` を直し、残骸なら user へ報告する (server は残骸を消さない — 記録と一致しないツリーは回収対象にならない) |
| `worktree_tidy` の `ledger.unattributed` に entry が載る | 回収したツリーのパスが `agent.worktree` と違う (別 clone の同番号 / merged branch 経路の巻き添え) | その entry は `done` のまま残っている (取り違えて `cleaned` にすると本物のツリーが二度と回収されない)。記録パスを直してから、そのツリーが在る clone の `repo_root` で呼び直す |
| `pr_repo_mismatch` が出る | 台帳の `prs[].repo` の綴りが観測と違う (`observe_prs` の `prs[].repo` を逐語で写していない) | 観測側の綴りへ `ledger_transition` の `prs` で直す。**status が変わったのではない** — 直すまでその PR の merge 検知は効かない |
| `pr_ref_ambiguous` が出る | 台帳の `prs[]` に `repo` が無く、別 repo に同番号の PR が居る | どちらが自分の worker の成果かを `prs[].repo` と entry の `repo` で決め、`ledger_transition` の `prs` に `repo` 付きで記録し直す。**当てずっぽうで片方を採らない** (status の読み違いは駐機と回収の両方を誤らせる) |
| 駐機 worktree が回収されない | phase が `parked` のまま (tidy の保護対象) | merge を観測したら `done` へ遷移させてから `worktree_tidy` を呼ぶ |
| 駐機したはずの作業ツリーが消えた / `done` の entry が `cleaned` へ進まない | 作業ツリーの名前が issue slug (`i<N>` / `swatcf-14`) と違い、台帳から導出した slug 集合に当たらない。または entry 自体が `ledger.unmappable` に載っている (slug を起こせなかった) ツリー名を確かめてから、`pane_spawn` の `worktree` に issue slug を渡し直す (「cross-tracker」節の E の差分)。**確かめ方は tracker で分かれる** — gh / glab は `observe_worktrees`、key slug (jira) は一覧に載らないので `agent.worktree` のパスか Bash の `git -C <clone root> worktree list` を読む (「cross-tracker」節の E)。**消えたツリーは戻らない** — 未 push の成果は失われている前提で user へ報告する |
| `observe_issues` / `issue_claim` / `issue_label` が「adapter は未実装」で落ちる | issue 置き場が Jira で、dispatch-ops に adapter が無い | 前提不成立ではない (pane 系の表を引かない)。「cross-tracker」節の tool 切り分けに従い、issue 側だけ Rovo MCP へ回す |
| Bash から叩いた `gh` が「`~/.config/gh/config.yml: operation not permitted`」で毎回落ちる | `gh` を `for` / `while` / `if` の本体に埋めた呼び出しは `sandbox.excludedCommands` の照合単位 (top-level segment) に当たらず、呼び出し全体が sandbox 内で走る | `gh` を **1 呼び出しにつき top-level の 1 断片**として書き直す (ループで回さず 1 issue ずつ引く)。dispatch-ops の tool 経由の観測は server が sandbox 外なので影響を受けない |
| cross-tracker で MR が merged になっても drift が出ない | 台帳 entry の `prs[]` に MR を記帳していない (closing reference は tracker をまたげないので、記帳が唯一の観測の種) | worker の `outcome.status` から MR 番号を読み、`ledger_transition` の `prs` へ `ref` / `role` / `repo` 付きで記帳する (「cross-tracker」節) |
| tracker 系 tool が「明示 repo scope 未実装」で落ちる | 置き場が GitLab で、宣言に `repo` が入っている。glab adapter は識別子を受けられないのに、server が宣言を既定値として注入する | **渡し方の誤りではないので `repo` を外しても直らない** (外すと宣言が入る)。cross-tracker で当たるのは MR の一覧だけで、番号は worker の `outcome` か `glab mr list` から得る。GitLab を issue 置き場にしている project では tool 側の対応が要るので user へ報告する |
| `resolve` が返らない / background 化する | 非終端 entry が増え、entry ごとに tracker CLI が直列で走る。常駐が長引くほど効く | 終端 entry を `cleaned` まで送って台帳を伸ばさない。引数なしは再入と全体照会だけにし、個別は `issue_ref` で絞る (`include_prs: false` は「PR を見ていない」であって「変化なし」ではない) |
| `blocked` / `dirty` が null | 検査していない (`include_blocked` off / `dirty_error`) | 「blocker 無し」「clean」と読まない。必要なら検査を立てて取り直す |
| merged なのに branch が残る | squash merge で `branch --merged` に現れない | worktree は台帳の `done` 経由で回収される。branch が邪魔なら user が消す |
| 台帳と現実の食い違いが増え続ける | drift を報告するだけで解消していない | イベントを処理するたびに B の判断を通す。解消しないと決めたなら理由を `note` に残す |
| 駐機 issue の conflict や merge が拾われない | 台帳から早く落としすぎている (終端 phase へ送った) | 駐機は `parked` のまま台帳に残す — `resolve` が tracker へ問い合わせるのは非終端 entry だけ |

**user へ出す文章は結論から始めて程よく簡潔に。** 分量は判断とその根拠になった観測に使い、実況と前置きには使わない。
