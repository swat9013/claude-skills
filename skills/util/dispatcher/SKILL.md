---
name: dispatcher
disable-model-invocation: true
argument-hint: "[instruction-file] [decisions-file]"
description: issue → CL の自律オーケストレーションを project に導入する手順と、観測 script が書いた指示ファイルを受けて着手を判断する orchestrator の手順。
---

# dispatcher

issue から CL (PR / MR) の間の自律オーケストレーション。**機械 (観測 script) は観測と指示の導出までで、tracker には書かない。状態を書くのは LLM と人間** — 台帳を持たず、毎 tick 外部 store (tracker の label / open CL) を読み直して再構成する。script は cron から周期起動する前提で書かれている (単一実行 lock と PATH の自己解決を持つ)。指示があるときだけ script が本 skill を `claude -p` で起動し (orchestrator)、orchestrator が決定 file に書いた worker を script が detach 起動する。

```
cron ─▶ 観測 script ─指示ファイル─▶ orchestrator (本 skill) ─決定 file─▶ 観測 script ─spawn─▶ worker
        観測 + 指示導出              採否判断 + wip 付与              決定どおり起動        実装 → CL
```

orchestrator と worker は `claude -p --permission-mode auto` で走る。prompt へ落ちる経路が無いので、classifier が止めた操作は実行されずセッションは続く — 止められて進めない worker は続行不能として人へ返す (迂回しない)。

| 状態 | 表現 | 書き手 |
|---|---|---|
| 着手可 | 宣言 config の `ready_label` (triage の label) | 人間 / triage |
| 着手中 | `dispatcher:wip` label | 付与 = orchestrator、剥がし = worker の終了処理 |
| 実装済み (CL 待ち) | 紐づく open CL の存在 (label なし) | CL 作成が遷移 |
| 続行不能 (人待ち) | `ready-for-human` label + issue コメント | worker の終了処理 |
| 完了 | CL の merge / issue close | 人間 |

候補 = `ready_label` ∧ ¬`dispatcher:wip` ∧ ¬`ready-for-human` ∧ 紐づく open CL なし。並列上限 N は `dispatcher:wip` の枚数で守る。

導入 (宣言 config / label / 試運転 / crontab) と運用 (死活確認) の手順は同梱の [README.md](README.md) が持つ。本 file の以降は script と orchestrator と worker の契約。

## tick が残すもの (`~/.claude/dispatcher/<project>/`)

| file | 中身 |
|---|---|
| `log.jsonl` | 毎 tick 1 行 + orchestrator の判断 1 行 (決定 file から script が写す)。script 行で読むのは `ts` (最終行が cron 自体の死活の手掛かり) / `result` (`ok` = 観測して指示を導出した、`error` = 観測できなかった・指示を導出できなかった (playbook の frontmatter が読めない / `deliverable: cl` なのに `dispatch-when` が無い / `start` で選べる playbook が 0 本)・orchestrator が正常終了しなかった (timeout / 異常終了。決定 file は読まない)・claude を起動できなかった・決定 file が無い / 読めない / prompt に未展開の `${` が残っている・`playbooks` の path が prompt に無い / 実在しない / start で指示の `playbooks` に無い / reenter で指示の条件と食い違う・指示に対する採否が欠けている・想定外の例外で止まった (この行には `instruction_file` / `orchestrator` / `spawned` が載らず、claude や worker を起動済みかは行から読めない)、`config_error` = config 起因で観測していない、`auth_error` = gh の認証が通らず観測していない (未認証 / token の失効・権限不足。綴りを直しても直らない)、`locked` = 前 tick が走っていた) / `instruction_file` / `orchestrator` (exit code・所要秒・timeout・`session_id`。この key があれば claude を起動した tick) / `spawned` (起動した worker の issue・pid・log path・`session_id`。`error` の行でも起動済みの分は載る)。`session_id` は script が起動ごとに発行して `claude -p --session-id` で渡した値で、標準 transcript へ辿る鍵 (引き方は README「死活確認」)。orchestrator が wip を付けた後に死ぬと wip が残る — 機械は剥がさないので、`error` 行を見た人が triage で回収する。orchestrator 行 (`actor: orchestrator`) は指示ごとの採否と理由。正本ではなく、消えても運用は続く |
| `decisions/<ts>.json` | orchestrator が書き、script が読む唯一の入力。指示ごとの採否 (`decisions`) と起動する worker の列 (`spawn`)。`decisions/<ts>.orchestrator.log` に orchestrator の stdout / stderr、`decisions/<ts>.handoff-<issue>.md` に人へ返したときの引き渡し本文 |
| `workers/<issue>-<ts>.log` | detach 起動した worker の stdout / stderr。判断過程の全体は `spawned[].session_id` で引く標準 transcript に残る |
| `instructions/<ts>.json` | 指示があった tick だけ書かれる。`snapshot` (候補 — issue 本文 `body` 付き。playbook 選定の信号 — / wip / `ready-for-human` / open issue → open CL の紐づき / open CL の状態 = 紐づく issue・head / base branch 名・`mergeable`・`checks`・`unresolved_threads`) と `instructions` の列 |
| `tick.lock` | 単一実行 lock。前 tick が走っていれば新しい tick は `result: locked` の log 1 行だけ残して exit 3 で終わる |
| `.config-verified` | 実在検査に通った config の hash。config を書き換えると次 tick で再検査される |
| `cron.log` | crontab の 1 行が append する script の stdout / stderr。script が起動できなかった失敗 (uv が無い / clone が無い) が出る唯一の場所で、失敗した tick の error 文もここに `<時刻 (UTC)> [<project>] tick=<log.jsonl の ts か -> result=<log.jsonl の result> <error>` の 1 行で並ぶ (複数行の error は ` / ` で畳む。`tick=-` は log.jsonl を書けなかった tick。想定外の例外で止まった tick も log.jsonl に書ければ `result: error` の行を残し、cron.log では traceback の後ろに `<error>` が `想定外の例外で止まった: <例外の型>: <内容>` の 1 行を置く)。前置の無い行は script 以外 (shell / uv) が出したものか、前置の付いた行の直前の traceback。正常な tick は何も書かない。project dir ごと無ければこの file も書けない |

指示の種別は 3 つ (並びは reenter → start → anomaly):

- `reenter` — 紐づく open CL がちょうど 1 本あり、その head が worker の branch 規約 `worktree-issue-<N>` に従う issue (wip / `ready-for-human` の付いたものを除く) の、その CL に条件が立っている。人が開いた CL には出さない (worker が他人の branch へ push しない)。`issue` / `cl` (`number` / `url` / `branch` = head / `base`) / `conditions` (立った条件ごとの `name` と対応 playbook の絶対 path `playbook`) を添える。条件の語彙・並び・snapshot 上の述語・対応 playbook は script の `REENTER_CONDITIONS` が持つ。同じ CL に複数の条件が立てば 1 指示にその並びで併記し、worker はこの順に対応する。空き slot の分だけ出し、残りの空きが `start` の `free_slots` になる
- `start` — 候補があり空き slot がある。`free_slots` と候補一覧と、`start` で選べる playbook の一覧 (`playbooks` — 新しい CL を作る playbook ごとの絶対 path `path` と選定条件 `dispatch_when`。script が playbook の frontmatter `metadata` から tick ごとに走査する) を添える。**候補の選定は指示を受けた LLM の判断で、script は順位を付けない**
- `anomaly` — 分類できない観測。`reason` は `wip_over_limit` (wip 枚数 > N) / `wip_and_ready_for_human` (同居) / `multiple_open_cls` (1 issue に open CL が複数 = 二重着手の徴候)。指示 0 件の沈黙とは区別して必ず上げる。**wip かつ open CL なしは anomaly にしない** — 着手直後の正常状態と stale wip を snapshot から区別できないため (stale wip の回収は triage の人間判断)

観測に失敗した tick は `result: error` で log に残り、指示ファイルは書かれない (指示 0 件と混同しない)。取得上限 (script の定数) に達した tick も同じ扱い — 切り詰めた像から指示を出すと、窓の外の open CL を持つ issue が候補へ戻って二重着手になるため。

## 指示ファイルを受けたとき (orchestrator)

`/swat-skills:dispatcher <instruction-file> <decisions-file>` で起動される。入力は instruction-file、出力は decisions-file (どちらも絶対 path で渡される)。tick を跨いで何も持たない (前回の判断は log に残るだけで、毎 tick 現実から判断し直す)。

1. 指示ファイルを Read し、`snapshot.issue_repo` / `snapshot.cl_repo` / `snapshot.limits.max_wip` / `instructions` を取る
2. 指示ごとに判断する。`reenter` を `start` より先に扱う (新規着手より既存 CL の完了が近く、同じ wip 枠を使う):
   - **`reenter`** — 現実を読み直す: `gh pr view <cl.number> -R <cl_repo> --json state` で CL がまだ open であること、`gh issue view <issue> -R <issue_repo> --json labels` で wip / `ready-for-human` が付いていないことを確かめる (外れていれば `skip`。tick と今の間に人か worker が動いた)。次に `conditions` を 1 つずつ読み直し、1 つでも立ったままなら採り、外れた条件は spawn prompt の `conditions` から落とす (全部外れていれば `skip`):
     - `conflict`: `gh pr view <cl.number> -R <cl_repo> --json mergeable` が `CONFLICTING`
     - `review`: worker 契約と同じ `reviewThreads` の query を `isResolved` だけで引いて未解決が残っている (`gh pr view --json` には thread の解決状態が無い。100 件を超える thread は script の観測が error にするので指示に乗らない)
     - `ci`: `gh api graphql` で `pullRequest(number:) { commits(last: 1) { nodes { commit { statusCheckRollup { state } } } } }` を引き、`state` が `FAILURE` / `ERROR` (script の `REENTER_CONDITIONS` の `ci` と同じ述語。それ以外は外れたとみなす)
     wip 枚数を `start` と同じ方法で数え直し、空きが無ければ `skip` (理由 `空き slot なし`。script は tick 時点の空きで絞っているので、前 tick の worker が付けた wip の分だけ食い違う)。採るなら step 3 で wip を付け、決定 file に `action: reenter` と、下記契約の**再入版** (着手形態 `reenter`・head / base branch・`conditions`・条件別 playbook の path) で組んだ spawn prompt を `kind: reenter` で書く。条件別 playbook は立ったままの条件の分だけ、`conditions` の並び順で全部 prompt に載せる (順序の正本は下記契約表の再入行) — path は各条件の `playbook` の値をそのまま写す (script が決定 file の `playbooks` を指示の条件と照合し、指示に無い path・条件の順と違う並びなら worker を起動しない)
   - **`start`** — 現実を読み直してから選ぶ。`gh issue list -R <issue_repo> --label dispatcher:wip --state open --json number` で wip 枚数を数え直し、空き = `max_wip - wip 枚数` とする (指示の `free_slots` は tick 時点の値で、前 tick の worker が付けた wip を含まないことがある)。空きが無ければ見送る。候補から空きの枚数まで選ぶ — 選ぶのは本文が自己完結し (実装対象と受け入れ条件が読める)、`Blocked by` の相手が open でなく、受け入れ条件が作業ツリーの中で閉じる issue。順位は issue 番号の昇順を既定にし、**候補の 1 件ごとに採否を決定 file に書く** (飛ばした候補は `skip` と理由。script が網羅を検査する)。採る issue ごとに、指示の `playbooks` から 1 本選ぶ: 候補に添えられた issue 本文 (`body`) を各 playbook の `dispatch_when` に突き合わせ、`playbook-implementation` 以外の playbook は `dispatch_when` に明示的に当てはまるときだけ選び、どれにも当てはまらないか迷ったら `playbook-implementation` (既定。誤って狭い playbook を渡すと無関係な step が worker の todolist を占める)。推測した種別と根拠はその issue の decision の `reason` に書く (label は付けない)
   - **`anomaly`** — 外部 store を読み直して状況を確かめ、様子見 (見送り) を既定にする。人へ返すのは、放置すると誤った着手や二重作業が起きると読めたときだけ (例: 1 issue に open CL が 2 本)。**`issues` の 1 件ごとに採否 (`skip` か `ready-for-human`) を決定 file に書く** — script が網羅を検査し、欠けた issue があると error にする (判断不能を黙って落とせない)。**見送り (`skip`) のときは wip を剥がさない** (stale に見えても回収は triage の人間判断 — 誤回収で稼働中の作業を潰さない)
     - 人へ返すとき: 引き渡し (下記「引き渡しの形式」) を Write tool で `<decisions-file の .json を .handoff-<N>.md に替えた path>` に書き → `gh issue comment <N> -R <issue_repo> --body-file <その path>` → `gh issue edit <N> -R <issue_repo> --add-label ready-for-human --remove-label dispatcher:wip` (wip も剥がす。`ready-for-human` が再着手を塞ぐ)。以後の tick はこの issue に `start` も reenter も出さない (人が label を外すまで)
3. 選んだ issue (start / reenter とも) に claim を付ける: `gh issue edit <N> -R <issue_repo> --add-label dispatcher:wip` — **gh は 1 呼び出しにつき top-level 断片の先頭に置いて単体で実行する** (`N=$(gh …)` のように埋め込むと sandbox の除外指定と照合されず起動に失敗する)。失敗したらその issue は見送り、理由 (label が repo に無い等) を log に残す
4. 決定 file を Write tool で書いて終わる。**spawn が 0 件でも必ず書く** (無いと script は「書けなかった」として error にする):

   ```json
   {
     "decisions": [{"issue": <N>, "action": "start" | "reenter" | "skip" | "ready-for-human", "reason": "<1 文>"}],
     "spawn": [{"issue": <N>, "kind": "start" | "reenter", "prompt": "<下記契約で組んだ spawn prompt の全文>", "playbooks": ["<prompt に載せた playbook の絶対 path>", …]}]
   }
   ```

   `decisions` には候補・reenter の issue・anomaly の issue を 1 件ずつ載せる (見送りは `skip`。script が網羅を検査して log へ写す)。`spawn` の `kind` は同じ issue の `action` と一致させる (script が照合する)。`playbooks` は prompt に載せた playbook の絶対 path の列 (`start` は指示の `playbooks` から選んだ 1 本、`reenter` は条件数) — script が各 path の「prompt 本文に含まれる」「file として実在する」「指示に載っている」を検査し、外れていれば worker を起動しない。script は orchestrator の終了後にこの file を読み、`claude -p "<prompt>" --permission-mode auto` を実装 repo の clone (= 自分の cwd) で detach 起動する。**worker を自分で起動しない** (理由は script 側にある。起動は script の責務)

wip の付与に失敗したり決定 file を書けなかった issue は、付けた wip を `--remove-label dispatcher:wip` で剥がしてから終わる (付いたままだと候補に戻らず、着手もされない)。

### 引き渡しの形式 (人へ返す issue コメント)

orchestrator が anomaly を人へ返すときも、worker が続行不能で止まるときも、issue コメントは次の 3 節で書く。読むのは文脈を持たない人なので、log や CL を開かずに次の一手が分かる形にする。**file に書いて `--body-file` で渡す** (`--body "…"` に埋めると本文の backtick や `$` が shell に展開されて壊れる):

```
## 停止理由
<何が分からない / 何に止められたか。permission に止められたなら止められた操作>

## ここまでの成果
<push 済み branch / draft CL の URL / 無ければ「なし」>

## 人が次にやること
- [ ] <解決に要る入力・判断・作業を 1 項目ずつ>
- [ ] 解決したら `ready-for-human` label を外す。open CL が無ければ次の tick から候補に戻る。open CL (draft 含む) が残っていれば候補には戻らない — CL を閉じるか人が引き継ぐ
```

### worker の spawn prompt 契約

script は prompt を解釈しない。worker が自走して CL に到達し、人が後で取りまとめられるだけの情報を全部文面に入れる。次を欠かすと壊れる:

| 文面に入れるもの | 欠けたときに壊れるもの |
|---|---|
| issue 番号・issue 置き場と CL 置き場 (`<owner>/<repo>`)・着手形態 (`start` = 新規実装 / `reenter` = 既存 CL への再入)・実装 repo の clone (cwd) | worker が担当と置き場を特定できない / CL 置き場が別 repo のとき closing reference と CL 本文の追記 (「残りを issue にする手順」) の `-R` を組めない |
| 作業ツリーの作り方。`start`: `git fetch origin main` → `git worktree add -b worktree-issue-<N> .claude/worktrees/issue-<N> origin/main`。`reenter`: `git fetch origin` → `git worktree add -B <cl.branch> .claude/worktrees/issue-<N> origin/<cl.branch>` (head が別の worktree で checkout 済みで作れなければ続行不能)。**cwd は clone root のまま**、Edit / Write には worktree 側の絶対 path を渡す | main の作業ツリーを直接編集する / 終了時に自分の worktree を消せない (cwd の中は消せない) / 再入が main から分岐して既存の成果を捨てる |
| **最初の Edit / Write より前に** playbook (`start` は step 2 で選んだ 1 本、`reenter` は条件別 playbook を `conditions` 順に全部) と `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read する契約。「playbook の step を逐語で todolist へ写す。複数の playbook は Read した順に todolist を連結する (飛ばす step には skip: 理由)」「索引から今回の作業に当たる leaf を Read する」と書く。path は**絶対 path をそのまま写し** (索引は本 SKILL.md をロードした時点で展開済みの path、playbook は指示が持つ絶対 path — `start` は `playbooks` から選んだ 1 本の `path`、`reenter` は各条件の `playbook`)、playbook の path は決定 file の `playbooks` にも同じ綴りで書く (`${` を残すと worker は 1 本も解決できず、原則なしの作業が silent に成立する) | 原則をセッション開始時に注入する経路が無く、spawn prompt が唯一の届け方 / script の検査 (prompt 含有・実在・指示に載っている) と突合できない |
| playbook と索引は Read で開き、review skill (`swat-skills:two-axis-review`) は Skill tool で invoke する、という使い分け | 「invoke せよ」と書かれた playbook は `disable-model-invocation` で拒否され、worker は skill が壊れていると読んで原則なしで進む |
| CL 到達前に `swat-skills:two-axis-review` を invoke し、**打ち切り時点の出力 (各節の注記行・Act on・Dismissed) をそのまま CL 説明文へ載せる**。回数と 2 回目の中身は playbook の review step が正本 | worker = 作業した本人が自分の指摘を落とす構造の唯一の歯止め (人が CL で読んで覆す) が消える |
| CL 本文に closing reference を必ず書く: 同 repo なら `Closes #<N>`、CL 置き場が issue 置き場と別なら `Closes <owner>/<issue repo>#<N>` を逐語で | 紐づきが mention 止まりになり、次 tick で「実装済み」と観測されず候補へ戻る (二重着手) |
| **自律判断で進んだ点を CL 説明文の独立した節に書く** (何を決め、何を退けたか) | 往復チャネルを無くした代償を成果物側で払う場所が無い |
| `gh` / `glab` は 1 呼び出しにつき top-level 断片の先頭に置いて単体で実行する。`git push` は Bash の timeout を 600000 で明示する (pre-push の全テストが既定の 120 秒を超える) | gh が全部起動失敗し「使えない」と読んで回避に走る / push が SIGTERM で死ぬ |
| 終了処理: どの終わり方でも `gh issue edit <N> --remove-label dispatcher:wip` を撃ち、`git worktree remove .claude/worktrees/issue-<N>` で作業ツリーを消す (branch は remote に残る)。**再入では新規 CL を作らず既存 branch へ push する** | wip が残って issue が塞がる / 作業ツリーが残骸になる / 2 本目の CL |
| (`reenter` のみ) CL 番号・URL・`conditions` と、条件別 playbook の path (`conditions` の並び順で全部)。**条件ごとの対応手順・続行不能の条件は各 playbook が正本**で、本契約には持たない — worker は渡された playbook を順に Read して従う。**条件を 1 つも解消できずに終わる再入は続行不能として人へ返す** — wip を剥がしただけで終わると次 tick が同じ再入を出し続ける | worker が何を直せばよいか分からない / 条件と違う順に触って conflict の上へ review 反映を積む / 解消不能を黙って諦める |
| 続行不能 (人しか出せない入力が要る / 作業ツリーの外の実体を触る受け入れ条件しか残らない / permission に止められて進めない) のときの終わり方を、順序と綴りごと書く: (1) 途中成果があれば commit して push し、下記「残りを issue にする手順」の対象があれば issue にする (2) 引き渡し (上記の形式) を clone root の `.claude/worktrees/issue-<N>.handoff.md` に Write し `gh issue comment <N> -R <issue_repo> --body-file <その path>` を撃ってからその file を消す (worktree の中に書かない — untracked file が残ると (4) が拒否される) (3) `gh issue edit <N> -R <issue_repo> --add-label ready-for-human --remove-label dispatcher:wip` (4) `git worktree remove .claude/worktrees/issue-<N>` (作れていなければ飛ばす)。**permission の deny を迂回しない** | 無言の終了は失敗扱い。人に返す印が無いと次 tick で候補にも戻らず塞がったまま。順序が違うと (label を先に外す等) 成果の所在を書く前に人が動く |
| 作業ツリーの外にある実体 (settings の適用・label 作成・稼働 clone) を触る受け入れ条件は自分の担当から外し、CL 本文に「user に残る作業」として書く (下記「残りを issue にする手順」の対象にも当たるかを見る) | worker の sandbox が clone root 外への書き込みを拒むので、抱えたままだと完了できない |
| 下記「残りを issue にする手順」を綴りごと写す。issue が正本で、CL 本文の「user に残る作業」節は issue 番号を並べる索引 | CL 本文は merge 後に読まれず残りが散逸する / 着手可 label が付くと次 tick の `start` が worker 自身の起票を拾い、dispatcher が自分の作業を自己増殖させる (人の triage を経て初めて候補に入る) / 同じ残りが重複して起票される / 現行本文を取らずに本文を更新すると closing reference と 2 軸レビューの記録が消え、次 tick で実装済みと観測されず二重着手になる |

#### 残りを issue にする手順

- 対象: 実装が要る残タスク / 担当 issue の範囲外で見つけた欠陥 / 2 軸レビューの Act on に残して直さなかった指摘。人の 1 回の操作で済むもの (label 作成・settings 適用) は CL 本文だけに書き、issue にしない。続行不能のときは担当 issue 自身の残りを引き渡しが持つので、範囲外の欠陥と直さなかった Act on だけを対象にする (CL が無ければ発端は担当 issue の番号だけ)
- 時機: `start` は CL 作成の後、`reenter` は既存 CL への push の後で、どちらも終了処理の前。続行不能のときは引き渡しコメントの前

1 件ごとに次を行う。本文は file に書いて `--body-file` で渡す (引き渡しと同じ理由)。file は clone root の `.claude/worktrees/issue-<N>.followup.md` に置き、撃った後に消す (worktree の中に置くと untracked file が worktree の削除を拒ませる)。

1. 重複を探す: `gh issue list -R <issue_repo> --state open --search "<title> in:title" --json number,title`。search は語単位の部分一致で返るので、返った `title` が起票する title と完全一致するものだけを同じとみなす。あれば発端 (担当 issue と CL の番号) を本文 file に書き、`gh issue comment <その番号> -R <issue_repo> --body-file <その path>` を撃って step 4 へ進む
2. triage label を決める: clone の `docs/agents/triage-labels.md` の `needs-triage` 行の右列が綴り。綴りが決まったら `gh label list -R <issue_repo> --search <綴り> --json name` の `name` に完全一致があるときだけ付ける。file か行か label が無ければ label なしで作り、step 4 で番号の横に「label なし (<無かったもの>)」と書く — label の無い issue は triage の一覧から漏れうるので、索引を読む人に拾わせる。**付けるのはこの 1 枚だけ** — 着手可 label も `dispatcher:wip` も `ready-for-human` も付けない
3. 起票する: 本文 (発端 = 担当 issue と CL の番号 / 残したこと・見つけたこと / 再現手順か所在 file:line / 自分が直さなかった理由) を本文 file に書き、`gh issue create -R <issue_repo> --title "<title>" --body-file <その path>` を撃つ (step 2 で綴りが決まっていれば `--label <綴り>` を足す)
4. 番号を索引へ足す: 続行不能のときは引き渡しの「人が次にやること」に書いて終わる。それ以外は CL 本文の「user に残る作業」節へ足す — 本文の更新は丸ごと置き換えなので、`gh pr view <CL 番号> -R <cl_repo> --json body` で現行本文を取り、番号を追記した全文を本文 file に書いてから `gh api -X PATCH repos/<cl_repo>/pulls/<CL 番号> -F body=@<その path>` を撃つ (`gh pr edit` は Projects (classic) の GraphQL エラーで落ちる版がある)

本文中の `${CLAUDE_SKILL_DIR}` を含む path (原則索引) は、本 SKILL.md をロードした時点で skill ディレクトリの絶対 path に置換済みなので、その展開後の文字列をそのまま prompt に写す。playbook の path は指示 (`start` の `playbooks` / `reenter` の `conditions`) が絶対 path で持つので、そのまま prompt と決定 file の `playbooks` に写す。
