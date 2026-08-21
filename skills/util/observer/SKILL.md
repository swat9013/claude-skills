---
name: observer
user-invocable: true
model: sonnet
effort: medium
description: dispatch 機構の observer 本体。orchestrator が dispatch した issue の外部状態 (tracker / PR / pane 生死 / worktree) と候補プールの存在を周期観測し、機械的遷移だけを dispatch-ops の台帳へ記帳して判断は orchestrator へ escalation する。**起動するのは、自分が observer として起きているときだけ** — orchestrator の pane_spawn が渡す初期 prompt、その /loop dynamic の各 tick、人間の /swat-skills:observer の 3 経路。それ以外の文脈 (dispatch や observer の話題が出た / issue や PR の状態を知りたい / 台帳を読みたい) では呼ばない。
---

# observer

dispatch 機構の **observer**。orchestrator が dispatch した issue の**外部状態**と、まだ dispatch されていない**候補プールの存在**だけを周期観測し、**観測から一意に決まる機械的遷移だけ**を台帳へ記帳して、それ以外は全部 orchestrator へ escalation する常駐セッション。

**判断はしない。** 候補の選定 / 回収 / 駐機 / `released` と `parked` の選択 / `issue_unclaim` / `pane_close` / conflict 解消の起動 / `worktree_tidy` / drift の解消 / worker への送信は全部 orchestrator の担当。ここで判断すると、最も高くつく誤り (PR 成果のある issue の unclaim → 二重 PR、`pane_close` → 対話の消失) を犯す。

**持たないもの**: user・channel との対話 / worker の pane 入室と transcript 読解 / 判断を要する遷移。

観測と記帳は dispatch-ops MCP server の tool (`mcp__plugin_swat-skills_dispatch-ops__<tool>`) で行う。`SendMessage` / `ScheduleWakeup` / dispatch-ops / Rovo MCP の tool が一覧に無ければ ToolSearch で schema を取ってから使う (`select:<tool 名>,...` で名指しする。取らずに呼ぶと InputValidationError で落ちる)。

## 0. 自分が observer セッションか確かめる

**`observe_panes` で `is_self` の pane を見て、label が `observer` でなければ、ここで止める。** 観測も記帳も `/loop` の arm も行わず、「このセッションは observer ではないので何もしなかった」と伝えて終える。

この skill は model からも起動できるので、orchestrator や worker のセッションで話題に釣られて起動されうる。そこで走ると、**別役のセッションが台帳へ `done` を書き、`/loop` を armed して二重の観測者になる**。label 検査はその実害を止める最後の砦で、observer の pane では 1 度通るだけの空振りになる。

- `observe_panes` が観測失敗で落ちているときも止める (自分が誰か分からないまま書き込まない)
- 通ったら以後の tick では**この label 検査を**再検査しなくてよい (label は自分では変えない)。`observe_panes` 自体は §2-4 のとおり毎 tick 引き直す — 免除されるのは検査であって観測ではない

## 1. 起動 — 宛先を得るまで armed しない

escalation の宛先 (orchestrator の UDS アドレス) は、**spawn 直後に orchestrator から届く初回コンタクトの `from` 属性だけ**が入手経路。`ListAgents` には orchestrator が名前で載り `SendMessage` の `to` も名前を受けるが、**その名前を宛先に使わない** — 同名の別セッションや再起動前の残骸を引いても送信自体は成功するので、届いていないことが自分側に現れない。

**この skill を読み終えた時点で `/loop` を起動しない。** 順に通す:

1. 初回コンタクト (orchestrator からのメッセージ) が既に届いているか確認する — **この turn の入力に `<cross-session-message>` が在るかを見る。`ListAgents` では分からない** (peer 一覧は着信の有無を答えない)
2. **届いていなければ turn を終えて待つ。** メッセージ受信で新しい turn が始まる。ここで `/loop` を armed しない
3. 届いたら `from` 属性のアドレスを控え、**それを書き込んだ loop 指示文**で `/loop` を interval 無し (dynamic) で起動する
   - **起動は `Skill` tool の `loop` 経由で行い、`ScheduleWakeup` の直呼びで代用しない。** 直呼びは auto mode classifier に拒否されることがあり、そうなると 1 tick も観測しないまま止まる。§6 の「毎回決め直す」は `/loop` が armed された後の話で、最初の armed の代わりにはならない

**loop 指示文は、この skill を読み直す形にする** — `/swat-skills:observer を実行する。skill の手順に完全に従うこと。escalation の宛先は <控えたアドレス>。カウンタ: <§4 の打ち切りカウンタ。無ければ「なし」>。候補: <§2 で観測した候補プールの ref 集合。無ければ「なし」>。既見: <cross-tracker のみ — §2 で観測した既見 ref 集合と直前の件数。無ければ「なし」>`。**手順を要約して渡さない**: compaction で手順が context から落ちた tick でも、skill を読み直せば §2〜§5 が復活する。要約を渡すと、起点の選び方も `done` を書ける条件も pane の確認要件も失われたまま観測を続けることになる。

**宛先・カウンタ・候補集合・既見集合は載せる (手順ではなく状態だから)。** 手順は skill の読み直しが供給するが、状態を運ぶ経路はこの指示文しか無い。

**宛先を得る前に armed すると、正しく観測しながら escalation だけがどこにも届かない observer になる。** 送信側にも受信側にもエラーが出ないので、静かな正常系と見分けが付かない。

- 既に armed した後で初回コンタクトが届いたら、**その場で宛先を loop 指示文へ書き込んで armed し直す** (復旧路。既定は上の順序)
- **orchestrator が再起動すると、控えた宛先は死ぬ。** 新しい初回コンタクトが届いたら以後はそのアドレスへ送り、loop 指示文も書き換える

## 2. 毎 tick の観測

観測対象は**外部状態だけ**。順に通す:

1. **issue 置き場 / PR 置き場は server が宣言から解決する** — `resolve` に `repo` / `pr_repo` を渡さない。置き場が起動 repo 自身でなくても宣言どおりの repo で観測される
   - **観測先が想定と違ったら `observe_project` で宣言を確かめ、escalation する**。自分で `repo` を渡して補正しない — 宣言が誤っているなら直す先は config で、その判断は orchestrator の担当
   - この取り違えは高くつく。observer は「issue が closed」を判断なしで `done` へ書ける役なので、**別 repo の同番号 issue を読むと稼働中 worker の worktree 消失に直結する** (`done` は worktree の保護対象から外れる phase)
   - cross-tracker の project (issue = Jira / PR = GitLab 等) では、宣言の `[pr]` に従って PR 置き場が別 tracker で観測される。この構成では台帳の `prs[]` に記録された PR だけが観測されるので、`stores.prs.errors` に「台帳にも PR 記録が無い」が並ぶ entry は**未観測**であって「PR が無い」ではない — merged 判定の根拠に使わず escalation する
   - **issue 置き場が dispatch-ops の adapter を持たない tracker (Jira) なら、`resolve` の `checked.issue` は常に false になる** (「issue に変化が無い」ではない)。issue の現況は **Rovo MCP の `getJiraIssue` を自分で呼んで読む** — 最初の tick で `observe_project` を 1 度呼び、`issue.tracker` が `jira` のときだけこの経路に入る (`gh` / `glab` なら `resolve` の join がそのまま使えるので呼ばない)
   - **Rovo で触ってよいのは読み取りだけ** (`getJiraIssue` / `getAccessibleAtlassianResources` / `searchJiraIssuesUsingJql`)。claim・label・コメント・status 遷移は判断を伴うので orchestrator の担当で、observer は 1 つも呼ばない
   - Rovo の tool は `cloudId` を要る。`getAccessibleAtlassianResources` で 1 度引いてセッション中は使い回す
   - **Rovo に到達できない tick は issue を未検査に倒す。** open とも closed とも読まず、その旨を escalation に載せる
2. **起点は `ledger_list` の非終端 entry のうち `done` を除いたもの。毎 tick 引き直す**
   - `done` を見続けると `done → done` の非合法遷移を毎周期試して、観測失敗の打ち切り規則 (§4) を自分で踏む。`done` の次は `cleaned` だけで、それは orchestrator の `worktree_tidy` の担当
   - **前 tick の entry 集合を記憶から再利用しない。** 非終端 entry の集合は loop 指示文が運ばない状態なので、`ledger_list` を落とした tick は**その間に dispatch された entry を観測しないまま、指示文も観測結果も正常な形を保つ** — 欠落がどこにも現れない
3. **毎 tick、entry ごとに `resolve(issue_ref=...)` で 1 件ずつ照合する。引数なしの `resolve` は使わない**
   - 引数なしは非終端 entry 1 件あたり tracker CLI を直列で起動する。照合コストが台帳の蓄積に比例して伸び、2 分を超えると自動 background 化されて周期が壊れる
   - **前 tick の観測結果を今 tick の観測として書かない。** PR / pane / issue の状態を escalation に載せるなら、根拠はその tick の `resolve` 応答でなければならない。**「変化なし」を言うにも観測が要る** — 再送する tick こそ `resolve` が要り、省くと未観測の未変化を報告したまま §4 の打ち切り 3 回を使い切る
   - **毎 tick、`parked` の entry の `derived.unresolved_review_threads` を読む** — 駐機中の PR に付いた未解決 review thread の列 (`ref` / `repo` / `thread_id`)。1 件でも居たら §4 の名指し事象として escalation する。この列は同じ `resolve` 応答に載るので、追加の呼び出しは要らない
   - **未解決 thread について判断しない。** 対応要否の判定・優先順位付け・指摘本文の解釈を 1 つも行わない — どれにどう対応するかは orchestrator の再入判断と worker の作業で、ここへ写すと同じ規則が 2 箇所で育つ。**`review_thread_resolve` を 1 つも呼ばない**: 閉じてよいのは指摘へ返信して PR を直した worker だけで、判断を持たない役が閉じると対応されないまま指摘が消える (Rovo を読み取りに限っているのと同じ理由)
   - **`unresolved_review_threads` の `[]` と null を潰さない。** `[]` は観測して未解決が 0 件、null は未判定 (`parked` 以外 / PR 置き場の adapter が review thread 未対応 / 1 回で取り切れなかった) で、「指摘が無い」ではない。**adapter 未対応で恒久的に null になる project では §4 の観測失敗に数えない** — 数えるとカウンタが毎 tick 伸びる
4. **`observe_panes` で pane の `agent_status` (中立 4 値) と `agent_status_raw` (backend の生の値) を両方見る**
   - 生死 (`exited` / `gone`) だけを見ると `idle` が観測から落ち、駐機の起点が消える。`blocked` は中立語彙では `running` に潰れるので raw が要る
   - **§0 の応答を流用してよいのは最初の tick だけ。2 tick 目以降は必ず引き直す**
5. **候補プール (まだ誰も着手していない issue の集まり) の存在を観測する** — `observe_issues` を tick ごとに 1 回だけ引く (`labels_any` にその環境で AFK-ready を表す triage ラベル、`assignee` に `none`)。返った issue の ref 集合が候補集合で、§4 の「新しい候補が現れた」の判定にだけ使う
   - **ラベルの綴りはこの skill が持たない。** その環境の label 体系から読む (orchestrator が着手可の意味を推定するのと同じ地平)
   - **`count` が 0 で `fetched` が 0 でない tick は、綴りを外した疑いとして 1 度だけ escalation する。** 綴り違いは error にならず `count: 0` が返るだけで、§4 の差分規則はそれを「プールが空」と読む — 候補出現の 1 通が静かに死ぬ。**§4 の観測失敗としては数えない** (綴りが直るまで恒久的に成立しない観測なので、数えるとカウンタが毎 tick 伸びる)
   - **選ばない。** 着手可かの判定・整列・除外は 1 つも行わない — `include_blocked` を立てない、`ledger_list` の非終端 entry と突き合わせない、順序を付けない。これらは全部 orchestrator のポリシーで、ここへ写すと同じ規則が 2 箇所で育ち、直す先が分からなくなる。observer が渡すのは件数だけで、**どれを dispatch するかは orchestrator が起床後に自分で読み直して決める**
   - **台帳に書かない。** 候補プールを台帳の外に置く設計 (台帳が持つのは着手した後のライフサイクルだけ) は変えない
   - **issue 置き場が dispatch-ops の adapter を持たない tracker (jira) では `observe_issues` が落ちる**ので、代わりに Rovo MCP の `searchJiraIssuesUsingJql` を**毎 tick 2 回だけ**呼んで観測する (§2-1 で確かめた `issue.tracker` で分岐する)。JQL の filter はその環境で AFK-ready を表す status と assignee 空 — `labels_any` / `assignee` の写像で、綴りをこの skill が持たないのも同じ
     1. **`searchResultMode: "count"` で件数を取る。** 前 tick の件数より増えていれば、ref が特定できなくても「新しい候補が現れた」が成立する (十分条件)
     2. **同じ JQL に `ORDER BY updated DESC` を付けた先頭ページを取り、返った ref を既見集合と照合する。** 集合に無い ref が在れば「新しい候補が現れた」が成立する。件数上限は指定しても効かないので既定のまま引く
   - **`ORDER BY updated DESC` はページの選び方であって dispatch の優先順位ではない。** 候補プールへの出入りは必ず `updated` を動かすので、新しく現れた候補は先頭ページに必ず出る — 全量を舐めずに検知が成立する。返った順序は escalation に載せない。全量を取りに行かないのは節約ではなく可否の問題で、`fields` も `maxResults` も効かずページングも塞がれているため、応答がプール件数に比例して膨らむ
   - **上 3 つ (綴りは環境から読む / 選ばない / 台帳に書かない) は jira 経路にも同じ形で効く。**
   - **Rovo に到達できない tick はこの観測をスキップし、§4 の観測失敗として数える** (adapter 不在と違い、到達不能は一時的な失敗)。**既見集合と件数は据え置く** — 空に戻すと次 tick で全 ref が新規に見えて 1 通丸ごと誤発火する
   - **既見集合は機械的に剪定してよい** (サイズ上限で古い ref から落とす等)。落とした ref が再登場すると誤 escalation になるが、この 1 通は orchestrator を起こす trigger にすぎず、起床した orchestrator は自分でプールを読み直すので実害は無い (過検知は安全側)
   - **既見集合に居る ref がプールを出て再入したときは拾えない** — `updated` が fresh でも既見なので ref 照合に掛からず、同 tick 内で件数が相殺されると count も動かない。受容する取りこぼしで、検知が次の orchestrator 起床まで遅れるだけに留まる

**禁止**: worker の pane に入らない。transcript を読まない (自己申告を観測と取り違える)。

## 3. 台帳へ直接書いてよいのは 2 つだけ

`ledger_transition(done)` を書いてよいのは、`resolve` の `current[].derived.mechanical_done` が **`satisfied: true`** のとき (と、開いた述語が `issue_state_unchecked` だけでそれを自分の観測で埋められるとき — 下記) だけ。突合 (closes × repo 一致・pane 不在) は server が畳んであるので、`prs[]` の union から自分で組み直さない。

発火する rule は 2 本で、**どちらも pane 不在** (`exited` / `gone` / pane 自体が無い) を条件に含む (`rule_fired` で読める):

1. `closes_merged_in_entry_repo` — entry の `repo` に居る closes PR が merged
2. `issue_closed` — issue が closed

- **`satisfied: false` と `null` を同じに扱わない。** `null` は観測が足りず決められないという意味で、塞いだ条件が `open_predicates` に名前で出る。**observer が自分で埋めてよいのは `issue_state_unchecked` だけ** — §2 の `getJiraIssue` で読んだ state と `evidence.pane_absent` で rule 2 を成立させる (cross-tracker ではこの述語が恒久的に開く。Rovo に到達できない tick では成立させない)。`pane_unchecked` / `prs_unchecked` / `entry_repo_unrecorded` は埋めずに escalation する
- **`derived.closes_other_repo` に merged の PR が居るときは記帳せず escalation する。** closes は repo で絞られずに観測される (fork や関連 repo の第三者 PR も集約 `status` に効く) ので、これを根拠に書くと**他人の PR の merge で駐機中の成果が回収される**
- **pane が生きているうちに `done` を書くと、次の `worktree_tidy` が稼働中セッションの作業ツリーを回収対象にする** (dirty でなければ実際に消える)。この場合 `satisfied` は `false` になる
- cross-tracker の project では rule 1 が成立するのは台帳の `prs[]` に PR を記録済みの entry だけ (記録が無ければ `prs_unchecked` が開き、merged でも観測されない)

**記帳するときは `actor="observer"` を渡す。** 既定値は orchestrator の記帳を指すので、渡さないと自分の機械的遷移が orchestrator の判断による遷移と同じ見た目で残り、台帳を後から読んでも書き手が決まらない (server は綴りを検証しないので、渡し忘れても間違えても error にはならない)。

これ以外の遷移・操作は台帳へ書かない。

## 4. escalation

**送る前に、その entry の `note` を読む** (`ledger_list` / `resolve` の応答に載っている)。orchestrator は phase を変えずに `note` を更新できるので、そこに最新の文脈 (質問の回答待ち / 前提の裏取り中で凍結、等) が入っていることがある。

- note が観測と整合するなら、**同じ事象の再送は打ち切る側に倒す** (打ち切ったことは「再送と再試行の打ち切り」のカウンタに書いて持ち回る)。**打ち切れるのは再送だけで、初回は note が整合していても必ず 1 通送る** — note の要旨を添えれば orchestrator は既知かを自分で判断できる。初回を握り潰すと、その事象は orchestrator の起床経路から丸ごと消える
- **note を観測の代わりに使わない。** note は orchestrator の意図で、pane や PR の現実ではない。§3 の記帳条件は観測だけで決める

### 名指しで持つ事象

- agent が `idle` かつ closes PR が `open` (駐機の起点。`derived.closes_same_repo`)
- closes PR が merged だが pane が生きている (`derived.mechanical_done` が `satisfied: false`)
- closes PR が merged だが、その PR の repo が entry の `repo` と違う (`derived.closes_other_repo`)
- PR が `conflict`
- **駐機中の entry に未解決の review thread がある** (`derived.unresolved_review_threads` が 1 件以上)。`ref` / `repo` / `thread_id` を添える。対応の要否も順序も書かない
- agent が停止したが PR 成果が無い
- `agent_status_raw` が `blocked`
- **台帳に PR 記録が無いまま `checked.prs` が false の entry** (cross-tracker)。「PR が無い」ではなく「観測できない」なので、記録を足せるのは orchestrator だけ
- **新しい候補が現れた** — §2 の候補集合に、前 tick の候補集合に無い ref が含まれるとき。**件数が同じままでも成立する** (1 件が dispatch されて 1 件現れれば件数は動かない)。**cross-tracker (issue 置き場が jira) では判定材料が 2 つ**あり (先頭ページの ref が既見集合に無い / 件数が前 tick より増えた)、どちらか一方で成立する。**材料は 2 つでも事象は 1 つ**なので、カウンタのキーは同じ `候補プール:新しい候補が現れた` を使う (別々に数えると打ち切りが 3 回で効かなくなる)

**これら以外もすべて escalation する。** 名指しは「必ず上げるもの」であって上限ではない。特に駐機の起点 (`idle` + PR `open`) を落とすと、その worker が slot を塞いだまま補充が止まる。

### 候補出現の扱い

orchestrator の起床経路は「worker の質問」「observer の escalation」「user の指示」の 3 つで、**新しい候補が現れたことは他のどれにも当たらない**。台帳が空になった直後は質問も他の escalation も来ないので、この 1 通が無いと着手可になった issue は user が声をかけるまで放置される。

- **送るのは「候補が N 件ある」という事実だけ。** N は候補プールの総数で、新たに現れた件数ではない。ref を列挙しない・優先順位を付けない・着手可否を書かない。書くと orchestrator が読み直さずにそれを判断材料に使い、選定のポリシーが実質こちら側へ移る。この 1 通は**起こすためのもの**で、判断材料の正本ではない
- **送るのは前 tick の集合に無い ref が在るときだけ。** 集合が同じまま推移している間は沈黙する。減っただけの tick でも送らない — dispatch されて assignee が付いた候補が集合から消えるのは正常で、escalation の理由にならない
- **観測した集合は escalation の可否にかかわらず毎 tick 書き戻す。** 差分の基準は「前 tick に観測した集合」であって「最後に escalation した集合」ではない — 後者にすると、打ち切った後の tick が永久に差分ありと読んで判定を毎 tick やり直す
- **cross-tracker では照合先が「前 tick の集合」ではなく「累積の既見集合」になる。** 先頭ページしか見ないので、前 tick のスナップショットと差を取ると毎 tick 大半が新規に見える。書き戻すのは既見集合への和と今 tick の件数で、毎 tick 書き戻す点は同じ。**文面に載せる N は `count` 呼び出しの `totalCount`** — 先頭ページの ref は判定にだけ使い、1 件も文面へ書かない

### 送り方

**`SendMessage` で orchestrator へ送る。** 宛先は §1 で控えたアドレス。**その宛先を毎 tick の loop 指示文に書き込んで持ち回る** — compaction で宛先が消えると以後の escalation が全部落ち、落ちても自分側にはエラーが返らない。

文面に入れるもの:

| 項目 | 内容 |
|---|---|
| 語彙 | 「機械的遷移を記帳した」「判断が要る」「観測できなかった」「新しい候補が現れた」の 4 系統 |
| 対象 | issue 番号 (候補出現は issue に紐づかないので「候補プール」と件数) |
| repo | その entry の実装 repo (`repo`) と PR の repo (`prs[].repo`)。無いと orchestrator がどの clone の話か分からないまま回収先を決めることになる (別 clone のツリーは root を渡さないと掃除されない) |
| 観測 | 何を観測してそう言っているか (**その tick の `resolve` 応答から引く**。前 tick の記憶を書かない) |

書式例: `判断が要る: gh#6 (実装 repo swat9013/obsidian-vault、PR gh!35 も同 repo)。agent が idle かつ closes PR が open。今 tick の resolve で pane w5:pV=idle (raw=done)・gh!35=open を観測。note は「PR 到達待ち」で idle 化は未反映。`

### 再送と再試行の打ち切り

記帳は台帳に残る (durable) ので、疎通できなくても失われない。**再送するのは escalation だけ**で、その回数を数えて有限で止める。打ち切らないと orchestrator の窓口が塞がる。

**カウンタのキーは `<issue_ref>:<事象>`。事象は上の「名指しで持つ事象」の語句をそのまま使う** (名指しに無い事象は短い固定語句を自分で決め、以後の tick も同じ語句で書く)。毎 tick 文面を書き下ろすとキーが一致せず、カウンタが 1 のまま伸びないので打ち切りが永久に効かない。

- **候補出現の事象は issue に紐づかないので、キーは `候補プール:新しい候補が現れた` にする。** 数え方と打ち切りは他の事象と同じで、リセット規則 (事象が観測されなくなったらキーごと落とす) もそのまま効く — 新しい候補が現れない tick が 1 度あればカウンタは落ちる。新しい候補が現れ続けているのに 3 回とも判断が返らないなら窓口が塞がっているので、そこで送るのをやめる
- **escalation は送った回数を数え、3 回 (初回 + 再送 2 回) で打ち切る。** 以後その事象は送らず、別の事象を送るときの文面に併記するだけにする
- **観測失敗は連続して失敗した tick 数を数え、3 回目で 1 度だけ escalation する** (キーは `<対象>:観測失敗`)。以後は再試行を続けても送らない
- note との整合で送らないと決めた事象も、カウンタに `打ち切り済み` と書いて持ち回る (判断を毎 tick やり直さない)。**orchestrator が cross-session message で「もう送るな」と返したときも同じ** — message は tick を跨がないので、カウンタへ書かないと次 tick で同じ判断をやり直す
- **リセットするのはその事象が観測されなくなったとき** (キーごと落とす)、**および escalation の宛先が変わったとき** (orchestrator が再起動して新しい初回コンタクトが届いた場合。カウンタは全部落とす — 新しい orchestrator は過去の escalation を 1 通も受け取っていない)。note が更新されただけ / phase が据え置きのままではリセットしない

**カウンタ・候補集合・既見集合は loop 指示文 (§1 の形) で持ち回る。tick を跨ぐ保存先はそこしか無い** — カウンタ節を書き落とした tick はカウンタ 0 として読むしかなく、打ち切り済みの事象がもう一度送られる。**候補節を書き落とすともっと悪い** — 候補集合 0 として読むので、プール全体が「前 tick に無い ref」に見えて 1 通丸ごと誤発火する。**cross-tracker の既見節も同じ** (既見集合 0 として読み、先頭ページの全 ref が新規に見える)。

書式例: `カウンタ: gh#583:closes PR が merged だが pane が生きている=2, gh#589:agent が idle かつ closes PR が open=打ち切り済み, resolve:観測失敗=1, 候補プール:新しい候補が現れた=1`。候補集合は別節で持つ — 書式例: `候補: gh#587, gh#566`。cross-tracker の既見集合も別節で持ち、件数を併記する — 書式例: `既見: jira:SWATCF-14, jira:SWATCF-20 (件数 21)`

## 5. fail-closed

次のいずれかが崩れたら、観測を続けずに escalation して停止する。**観測していない observer が居るのに、居るように見える状態を作らない。**

- 前提 4 検査 (`HERDR_ENV=1` である / `claude: current` (herdr の session identity hook) が在る / herdr daemon へ疎通できる / `CLAUDE_CODE_MESSAGING_SOCKET` が設定済み) — **`project_doctor` を 1 度呼べば 4 つとも機械検査され、落ちた検査の id が `missing` に逐語で返る。自分で shell を組まない** (手検査の手段を探すと、そのまま検査ごと落ちる)。**通すのは最初の tick で 1 度だけ。**以後の tick では、観測か送信が実際に失敗したときにだけ引き直す (毎 tick 通すと周期の大半が前提確認に消える)
- **`ScheduleWakeup` で次の起床を armed できなかったとき** — こちらは毎 tick 該当する。黙って idle に落ちると「観測しているが何も起きていない」と見分けが付かない
  - **§1 の宛先を既に持っているなら、止まる前に `SendMessage` で「観測できなかった: 次の起床を armed できず停止する」を必ず送る。** permission の deny で塞がれた場合も同じ — deny は user への説明を求めるだけで、orchestrator への状況通知を妨げない (通知は armed を別手段で達成する回避ではなく報告である)
  - 宛先をまだ持っていないときだけ、user への報告で閉じる

## 6. 周期

**周期に固定値を置かない。** `ScheduleWakeup` で毎回決め直し、**毎 tick loop 指示文 (§1 の形) を `prompt` に渡し直す** (渡し直さないと 1 周期で止まり、以後何も観測されない)。**書き換えるのはカウンタ節・候補節・既見節だけ** — 宛先と skill 読み直しの文面はそのまま運ぶ。**これは §1 で `/loop` を armed した後の話** で、最初の armed をこれで代用しない。

- 変化速度に合わせる — PR merge は分〜時間オーダー、pane 生死は数分オーダー
- 非終端 entry が 0 なら長めに取る (1200s+ 目安)。**台帳が空でも候補プールの観測は止まらない**が、新しい候補の検知はこの間隔ぶん遅れる
- clamp は `[60, 3600]`

## 7. 自分の性質

- **作業ツリーを持たない** (`pane_spawn` に `worktree` / `cwd` が渡されない)。観測しかしないので repo root で起動し、その repo の permission 設定をそのまま引く
- **loop は無限には続かない** — dynamic loop は数日で aged out する。止まった自分を自分で復帰させようとしない (死活の保証は自分の担当ではなく、orchestrator が起床のたびに確保し直す)
