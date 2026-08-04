---
name: issue-dispatch
disable-model-invocation: true
argument-hint: "[max]"
description: herdr (AI agent 向け terminal multiplexer) session 内で、着手可能な open issue を選んで Claude Code セッションを分割 pane として起動し、監視ループで回収・駐機・補充を回す dispatcher。観測と操作 (tracker / pane / worktree / durable 台帳) は dispatch-ops MCP server が持ち、候補選定・回収・駐機・drift 解消の判断は毎サイクルこのセッションが行う。台帳は永続なので、前回セッションが駐機した issue とその後の PR merge を再入時に拾い直す。
---

# issue-dispatch

herdr session 内で cwd repo の open issue から着手可能なものを選び、**規定セッション数まで** Claude Code セッションを起動する dispatcher。展開先は**この skill を呼び出したセッションが居る herdr workspace の分割 pane** — user は同じ workspace 内で全セッションを見渡し、pane 移動で直接介入できる。起動後は監視ループに入り、完了 pane を回収し、PR に到達して手が止まった pane は駐機 (pane を降ろして作業ツリーと assignee を温存) させ、空いた slot に次候補を補充する。

## 機構と判断の分担

| 層 | 持ち物 |
|---|---|
| MCP server (`mcp__plugin_swat-skills_dispatch-ops__<tool>`) | 観測の正規化・操作の実行・台帳への記帳・phase 遷移の合法性検証 |
| このセッション (LLM) | 候補選定・起動 prompt の文面・回収 / 駐機 / unclaim の判断・drift 解消・終了判定 |

**server はポリシーを持たない。** 候補を選ばず、回収すべきかを返さず、drift の解消手段も示さない。判断を server の返り値に探しに行かず、観測を材料に自分で決める。

台帳 (`~/.claude/issue-dispatch/<repo-key>/`) は永続で、**着手した後**のライフサイクル (`claimed` → `active` → `parked` → `done` → `cleaned`、ほかに終端の `released` / `spawn_failed`) だけを持つ。候補プールは台帳に入れない — 何が着手可かは毎サイクル tracker の生データから読み直す。

## args

`/issue-dispatch [max]` — max は同時稼働セッション数の上限 (省略時 3)。**server は max を知らない**ので、空き slot は `observe_panes` の結果 (`tracked` が true で `is_self` が false の pane 数) と max から自分で数える。`observe_panes` が観測失敗で丸ごと落ちているときは、台帳の `active` entry 数を使用中 slot として数えて進む (罠の表参照)。

## 前提

pane 系 tool が前提不成立で失敗したら、error 文言に応じて user へ依頼して終了する (dispatch は不能だが、fail-closed なので誤 dispatch には進まない):

| error | 依頼 |
|---|---|
| `HERDR_ENV=1 でない` | herdr session 内で Claude Code を起動し直してもらう (herdr 以外の terminal multiplexer は非対応) |
| `claude: current` が無い | `herdr integration install claude` の実行を依頼する。hook は session identity を herdr に報告して pane と Claude session を 1:1 対応させる。無いと agent field が埋まらず、起動確認と駐機判定が壊れる |
| `herdr status が失敗 (socket に届かない)` | herdr daemon の起動を依頼する |

表に無い error 文言は前提不成立ではない。特に `pane <ID> の応答に agent field が無い` は特定 pane の応答が壊れているだけで、hook 未インストール (agent field が「埋まらない」) とは別事象 — user に修正依頼せず罠の表を引く。

## ループプロトコル

```
起動 /issue-dispatch [max]
  │
  ├─ A. 再入       ledger_list → resolve        前回の意図と現実の差を確認
  ├─ B. drift 解消  台帳を直すか、現実を直すか
  ├─ C. 候補選定    observe_issues (生データ)     label 体系と issue の実態から選ぶ
  ├─ D. 起動       issue_claim → ledger_record
  │               → pane_spawn → ledger_transition(active)
  │
  └─ E. 監視ループ ◀────────────────────────────┐
        pane_watch (≤110s) → resolve → 回収判断  │
        → pane_close / ledger_transition(note)   │
        → worktree_tidy → C/D で補充 ────────────┘
        └─ active 0 かつ候補 0 → 最終報告して終了
```

### A. 再入 (ledger_list → resolve)

- `ledger_list` の非終端 entry が前回までの意図。各 entry の `note` は前セッションの自分からの引き継ぎで、機械はパースしない
- `resolve` が台帳と外部 store (tracker / pane / git) を join し、食い違いを `drift` に列挙する。単発の追跡 (「この issue の PR / worktree / pane はどこか」) も同じ tool を `issue_ref` 付きで叩く
- **「見ていない」を「無かった」と読まない**: `stores.<name>.observed` が false の store 由来の drift は 1 件も出ないし、entry ごとの `checked.{issue,prs,pane,worktree}` が false の側面は判定していない。pane を観測できていないのに「pane 消失」と読むのが最も高くつく誤読
- `resolve` は非終端 entry 1 件あたり tracker CLI を数回、直列で起動する。追跡数が増えた状態の引数なし呼び出しは 2 分を超えて自動 background 化されうる (同期フローが崩れる) → 終端 entry を溜めない (`done` は `worktree_tidy` で `cleaned` まで送る)、個別確認は `issue_ref` で絞る

### B. drift 解消

台帳は「意図と記録」、外部 store が「現実」。どちらを直すかを毎回決める:

- **意図がもう無い** (作業が終わった / 成果ごと消えた) → 台帳を現実に合わせる (`ledger_transition`)
- **意図は生きていて機構だけ落ちた** (`pane_missing` かつ worktree が残っている) → 現実を直す (`pane_spawn` に `cwd` を渡して駐機ツリーへ再入する)
- **台帳外だが自分の管理下に置くべき作業** (`worktree_untracked` で assignee が自分・branch が `i<N>` / `worktree-i<N>` 規約に合う — 台帳導入前のセッションや旧方式の遺産) → 後追い記帳する: `ledger_record` (`agent` に `worktree` / `branch` を記載) → `ledger_transition("active")`、駐機状態なら続けて `"parked"` へ (`claimed → parked` の直行は非合法)。issue が既に closed なら `record → "done"` が合法で、tidy の回収対象になる。台帳に載せない限り、conflict 解消の起動も駐機 merge の回収も、その issue には発火しない
- **それ以外の `worktree_untracked` / `pane_untracked`** (他者・出所不明) は触らない側に倒して報告する
- 解消しないと決めた drift は、その理由を `note` に残す (次サイクルで同じ判断をやり直さないため)

### C. 候補選定

`observe_issues` は絞らず並べ替えず生データを返す。着手可の意味は**その環境の label 体系と issue の実態から推定する**。

- **確信が持てない issue は dispatch しない側に倒す** (誤 dispatch のコストは、1 サイクル遅れるコストより高い)
- 除外する: assignee が付いている issue (他者の作業 / 自分の駐機)、台帳に非終端 entry がある issue。**終端 entry (`cleaned` / `released` / `spawn_failed`) しか無い issue は再 dispatch 可**で、同じサイクル内で `released` へ送った issue もここに戻る (`ledger_record` は非終端 entry が生きていると失敗するので、再 dispatch は必ず新規 entry になる)
- `blocked: null` は**未検査**であって「blocker 無し」ではない。`include_blocked` は 1 issue あたり CLI が起動するので、`labels_any` / `labels_none` / `assignee` で絞ってから、通す候補にだけ立てる。検査に失敗した issue は blocked 扱いで skip する
- `truncated` が true なら窓の外に取り残しがある。報告に載せる
- 整列も判断のうち — 完成に近い段階から slot を埋めると、同じ slot 数でも成果が出る速度が上がる

### D. 起動

**`issue_claim` → `ledger_record` → `pane_spawn` → `ledger_transition("active", agent={...})`** の順で通す。claim を先に置くのは、起動前に assignee を立てて二重 dispatch を塞ぐため。

作業ツリーの隔離は `pane_spawn` の引数で表す (両方渡すと error、どちらを実行したかは返り値の `mode` で確認する):

| 渡す引数 | mode | 用途 |
|---|---|---|
| なし | `plain` | repo root でそのまま起動する |
| `worktree: "i<N>"` | `created` | 新しい作業ツリーへ隔離する (実装作業の既定) |
| `cwd: <駐機ツリーのパス>` | `reentered` | 既存の駐機ツリーへ再入する (conflict 解消・追加指示) |

失敗の扱い:

- claim 失敗 → 起動せず次候補へ
- `pane_spawn` が `ok: false` (agent 未検出) → **pane が残って label を占有し、以後その issue の起動が撥ね続ける**。`pane_close` で畳んでから `ledger_transition(spawn_failed)` + `issue_unclaim` する (assignee 残留は候補から永久に外れる)。再試行は新規 entry で、同一 issue の再失敗はそのセッション中 skip する

#### spawn prompt の契約

server は prompt を一切解釈しない。pane が自走し、dispatcher が後で回収できるだけの情報は全部文面に入れる。次の 4 点は欠けるとループの別の場所が壊れる:

| 文面に入れるもの | 欠けたときに壊れるもの |
|---|---|
| issue 番号 (と何をする作業か) | pane が自分の担当を特定できない |
| PR 本文に `Closes #<N>` を必ず含めるという要求 | 紐づきが `mention` 止まりになり、駐機判定が「自分の PR を持つ issue」を見分けられない |
| 終了直前に `ledger_report_outcome` (`issue_ref` / `outcome` / `summary`) で自己申告する契約 | 「なぜ終わったか」が台帳に残らず、正常終了と途中死を外形観測で区別できない |
| 再入 (`cwd` 起動) では新規 PR を作らず既存 PR の branch へ push する、という明示 | 再入セッションが 2 本目の PR を作る |
| 着手前に **`git pull --no-rebase origin main`** で最新を取り込む、という明示 (コマンドごと書く) | pane が古い main を土台に作業し、merge 時に conflict する。かつ pane が `git merge` を選ぶと sandbox が repo 直下の `hooks/` / `.claude/hooks` / `.claude/skills` / `.claude/agents` への write を deny するため、これらを含む commit で `Operation not permitted` に落ちて **HEAD 据え置き + working tree だけ書き換わった中途半端な状態**になる |

- `merge` ではなく `pull` を書くのは、`git pull` が sandbox の `excludedCommands` に登録済みで sandbox 外を走るため。`--no-rebase` にするのは pane が既に commit していると `--ff-only` が成立しないから (merge commit ができる点は許容する)
- `outcome` の語彙を server は検証しない。dispatcher が読んで判断する材料なので、**この session が読み分けられる語彙を prompt 側で指定する** (最低限「PR に到達した」「作業不要と判断した」「人手が要って停止した」の 3 系統 + 理由)
- 作業種別 (実装 / 調査 / triage / 計画検証) と `model` / `effort` は issue の label 体系から読んで選ぶ。server は段階を知らないので、対応付けはこの session が毎回決める

### E. 監視ループ

1. **`pane_watch`** (既定 90 秒 / 上限 110 秒)。**2 分を超える MCP 呼び出しは自動 background 化され、「結果を見て次を決める」同期フローが崩れる**ので上限を上げない。長い監視は反復で組む
   - event (`agent_exited` / `pane_gone` / `agent_idle` / `timeout` / `no_panes`) は報告用の参考情報で、**どの event でも次の照合手順は同じ**。完了判定は issue / PR の観測が正
   - `agent_idle` は baseline からの遷移でのみ返る (止まったままの pane で毎サイクル即 return して tracker を叩き続けないため)。取りこぼしは次サイクルの `resolve` が拾う
2. **`resolve`** で現況 (`current[].observed`) と `drift` を取る
3. **回収判断** (下記の指針)。遷移には必ず `note` を付ける
4. **`worktree_tidy`** (引数なし)。保護対象 (`active` / `parked`) と回収対象 (`done`) は台帳から自動導出される。**駐機したまま merge されたツリーは回収されない** — merge を観測したら先に `done` へ遷移させる。回収できた `done` は `cleaned` へ自動遷移する。dirty なツリーは消えず `skipped` に載る
5. **補充** (C / D を再実行して空き slot を埋める)
6. **終了判定**: `active` が 0 かつ候補が 0 → 最終報告して終了。`parked` が残っていても待たない (merge を待つのは dispatcher の仕事ではない — 台帳が永続なので次のセッションが拾う)

#### 回収 / 駐機の判定指針

判断の入力は issue state / agent status (中立値と `agent_status_raw`) / PR status。**PR は `role` が `closes` のものだけを根拠にする** — `observe_prs` のトップレベル `status` も `closes` 限定で算出されるのでそのまま使える。`count` / `prs[]` は `mention` 込みの union なので、件数と `status` は一致しない (mention しか無い issue は `status: "none"` で `count: 1`)。

先に通す原則: **PR 成果 (`open` / `checking` / `conflict` / `merged`) のある issue は unclaim しない。** assignee がその issue の駐機 marker であり、外すと同じ issue に 2 本目の PR を作る dispatch が走る。PR 状態を観測できていない (`checked.prs` が false) issue も「不明」として据え置く。

| 観測 | 判断 |
|---|---|
| issue が closed | pane が在れば `pane_close`。`done` へ遷移させ、`worktree_tidy` に回収させる |
| closes PR が merged | `done` へ遷移 (issue が open のままなら close 漏れとして報告する)。これが駐機ツリーの回収経路 |
| agent 停止 (`exited` / `gone`)・PR 成果なし | `released` へ遷移 + `issue_unclaim` してキューへ返す |
| agent 停止・PR 成果あり | **駐機**: `parked` へ遷移 (`agent` の `pane_id` を null に)。worktree と assignee は残す |
| agent が `idle`・closes PR が `open` | **駐機**: `pane_close` → `parked`。pane close はそこまでの対話を捨てる不可逆操作なので、この条件は狭く取る |
| PR が `conflict` | 解消を起動する (下記)。**駐機しない** — 稼働中 pane を閉じると解消の起動先が消える |
| `agent_status_raw` が `blocked` / `unknown` | 触らない。`blocked` は人間宛の問い合わせ待ち (閉じると待っている判断が消える)、`unknown` は判定不能 |
| PR が `checking` | 次サイクルで再観測する (「conflict 無し」と読み替えない) |
| `role` が `mention` だけの PR | 別 issue の PR が本文で番号に言及しただけでありうる。駐機・conflict 対応の根拠にせず、PR 番号を報告に添えて user 判断へ残す |

`note` には**何を観測してその phase にしたか / 次に何を待っているか**を書く。次サイクル・次セッションの自分が判断を再現できることが唯一の基準。

#### 割り込み (pane_send) の規範

- 稼働中 pane への送信は「放置すると作業自体が無駄になる」ものに限る (conflict 解消が典型 — 放置すると push が詰む)。**追加指示は割り込まない** — 数分遅れても結果は変わらず、元タスクとの混線リスクだけが残る。駐機を待ってから `cwd` 再入で渡す
- **`agent_status_raw` が `blocked` の pane へ送らない** — 人間宛の問い合わせに代答することになる。送る前に `observe_panes` で raw status を見る (中立語彙では `running` に潰れる)
- user から預かった指示は逐語で渡す。dispatcher が指示を創作しない
- `issue_ref` を添えて送ると events.jsonl に残る。**同じ pane へ同じ指示を再送しない** (混線を増やすだけ)。効かなければ user に渡す

### 報告

起動直後・サイクルごと・最終に user へ出す。少なくとも次を含める:

- 起動した pane (issue / pane_id / worktree)、skip した候補と理由、未検査で残した候補数 (`truncated` を含む)
- 駐機した issue とその PR (番号 / status / URL)。**merged なのに issue が open のもの**は assignee が残り続けるので明示する
- 台帳の `outcome` — 人手が要ると自己申告したものは理由付きで、申告が無く PR 成果も無いものは途中死候補として
- 観測した drift と、それをどう解消したか (解消しなかったものは理由)
- `worktree_tidy` の結果 (回収したもの / dirty で残したもの / `i<N>` 規約へ写せず保護できなかった台帳 entry = `unmappable`。黙殺すると保護したつもりの worktree が消える)
- 参加方法 — 同じ workspace の pane なので、user は pane 移動 / zoom で直接介入できる

## 責務境界

dispatch → 監視ループ → 全回収・駐機で止まる。conflict は解消作業の**起動まで**が dispatcher の責務。以下は各セッション / user の領分:

- 実装・調査・triage の中身、issue を close する判断、PR の作成・merge、conflict 解消そのもの
- outcome の自己申告 (各セッションが `ledger_report_outcome` で残す。**代理申告も推測での補完もしない** — 埋めると「途中死を検知できる」という契約が壊れる)
- 駐機 worktree の破棄。dispatcher が回収するのは `done` へ送れたものだけで、dirty なツリーと未 merge branch は報告して user に返す (`branch -d` の `-D` 昇格は server も skill も行わない)
- `blocked` な pane の解除 (permission 待ち・質問待ちは user が pane に入って解く)
- label 遷移
- 外部タスク管理サービスとの連携は行わない (入出力は issue tracker と台帳に閉じる)

**完了判定は issue state が正で、PR 状態で完了扱いにしない** — 駐機は「slot を降ろす」操作であって「終わった」という判定ではない。

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| tool が見えない | plugin 未有効化 / server 設定後にセッションを起動していない | `/mcp` で `plugin:swat-skills:dispatch-ops` が connected か確認する。無ければ `/reload-plugins` かセッション再起動 |
| 自分の pane が slot を 1 消費している / dispatch した覚えのない issue が稼働中に見える | dispatcher 自身の pane label が前回の残骸 `i<N>` のまま | 最初の pane 操作で `dispatch` へ自動 rename される。slot は `is_self` を除いて数える |
| `pane_spawn` が `ok: false` を返す | agent 検出の poll 内に起動が間に合わない / prompt の崩れで pane の shell が誤解釈した | pane が label を占有したまま残るので `pane_close` → `spawn_failed`。同一 issue の再失敗はそのセッション中 skip する |
| `observe_panes` が `pane <ID> の応答に agent field が無い` で丸ごと失敗する | **追跡 pane (`i<N>` label)** の応答が壊れている。欠落を「セッション終了」と誤読しないよう fail-closed で raise する。`resolve` は同じ失敗を `stores.panes.observed: false` へ降格して返す。追跡外の pane (agent を起動していない素の shell 等) の欠落では失敗せず、`agent: null` として観測結果に載る | slot は台帳の `active` entry 数で数えて進む。raw status を観測できない間は `pane_send` しない。`pane_watch` も同じ pane を見るので同時に失敗する — 該当 pane の issue は user に状況を確認して、要らなければ `pane_close` で降ろす |
| 集約 `status` が `none` なのに `count` が 1 以上 | `status` は `closes` 限定、`count` / `prs[]` は `mention` 込みの union。closes PR がまだ無い状態 | 矛盾ではない。駐機・回収の根拠にはせず、`prs[]` の mention PR を番号付きで報告に添える |
| 同一 issue に 2 セッション | claim 前に spawn した / 別マシンから同時 dispatch した | claim → record → spawn の順を守る (同 label の pane を server が撥ねるのが二重の防波堤) |
| PR を出したのに駐機されず slot が塞がる | PR 本文に `Closes #<N>` が無く、`role` が `mention` 止まり | PR 本文に closing reference を足せば次サイクルで駐機される。急ぐなら user が pane を閉じる |
| 駐機 worktree が回収されない | phase が `parked` のまま (tidy の保護対象) | merge を観測したら `done` へ遷移させてから `worktree_tidy` を呼ぶ |
| `resolve` が返らない / background 化する | 非終端 entry が増え、entry ごとに tracker CLI が直列で走る | 終端 entry を `cleaned` まで送って台帳を伸ばさない。個別確認は `issue_ref` で絞る (`include_prs: false` は「PR を見ていない」であって「変化なし」ではない) |
| `blocked` / `dirty` が null | 検査していない (`include_blocked` off / `dirty_error`) | 「blocker 無し」「clean」と読まない。必要なら検査を立てて取り直す |
| merged なのに branch が残る | squash merge で `branch --merged` に現れない | worktree は台帳の `done` 経由で回収される。branch が邪魔なら user が消す |
| 台帳と現実の食い違いが増え続ける | drift を報告するだけで解消していない | 毎サイクル B の判断を通す。解消しないと決めたなら理由を `note` に残す |
| 駐機 issue の conflict や merge が拾われない | 台帳から早く落としすぎている (終端 phase へ送った) | 駐機は `parked` のまま台帳に残す — `resolve` が毎サイクル tracker へ問い合わせるのは非終端 entry だけ |
