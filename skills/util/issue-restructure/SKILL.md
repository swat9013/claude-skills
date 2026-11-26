---
name: issue-restructure
description: open issue を根本課題ごとのクラスタに束ねて課題型の issue へまとめ直す案を、人間の承認を通して tracker へ反映し、続けて backlog-triage の手順へ進む対話。
disable-model-invocation: true
---

# issue-restructure

open issue を全件読み、根本課題を共有する issue をクラスタに束ねて、課題型の issue にまとめ直す。束ね方と本文を user に承認してもらい、承認された分だけ tracker へ反映する。`ready-for-agent` の判断は triage に任せる — 本 skill が付ける label は `needs-triage`・`needs-grilling`・`ready-for-human`・`tracking-issue` だけで、作った issue は `needs-triage`、書き換えた issue は下の「書き換えた後の状態」が決める label にして返す (どちらも、2 ラウンドで決まらず `needs-grilling` を付ける根本課題 issue を除く)。形を整えてから triage が状態を裁けば、close される issue に判断を費やさずに済む。

語は次の意味で使う。

- **観測型 issue**: 起きた不具合や食い違いを書いたもの。**手段型 issue**: 解き方を先に指定したもの。**課題型 issue**: 解くべき問題と、解けたかの受け入れ条件を書いたもの
- **根本課題**: クラスタの共通原因を課題の形で言い直したもの。解決すればクラスタ内のどの issue も不要になる (再現しなくなる・書く理由が消える) 原因のうち、最も浅いものを採る。1 件しか説明しない原因は根本課題にしない。原因が iceberg model のどの層にあるかと、どの層の原因を採るかは原則 leaf `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/references/leaves/principle-root-cause-and-hierarchy-of-controls.md` に従う
- **上流の課題**: 同じ種類の issue を繰り返し生んでいる原因を、課題の形で言い直したもの。解決すると同じ種類の issue が新しく生まれなくなるが、既にある issue は不要にならない。structures 層か mental models 層にあり、close 済みを含めて 2 件以上の issue を説明する原因だけを採る

issue 置き場と CL 置き場は `${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/references/placement.md` を Read して決め、コマンドには同書のとおり `-R` で明示する。同書が止まるとしたときは調査に入らずに止まり、同書が渡すとしたものを挙げて置き場を user に問う。CLI 規約・label の綴り (`needs-triage`・`ready-for-agent`・`needs-info`・`ready-for-human`・`needs-grilling`・`tracking-issue`) は対象 repo の `docs/agents/issue-tracker.md` / `docs/agents/triage-labels.md` を Read して決める。無ければ label は上に並べた既定の名前とし、置き場を同書の既定で決めたときと同じく、その前提を報告に書く。`needs-grilling` に当たる label が無い repo では、深掘りを待つ根本課題 issue を label でなく報告で返す。`tracking-issue` に当たる label が無い repo では label を付けず、親の着手を止めるのは本文の 1 文だけになることを報告に書く。コマンド例は GitHub (`gh`) の綴りで、他の tracker なら読み替える。

## 調査

tracker に何も書かずに調べる。

- 判断の規律は `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、当たる leaf を読んで使う
- open issue を本文とコメントごと `gh issue list -R <issue 置き場> --state open --limit 1000 --json number,title,body,labels,comments` の 1 回で全件引き、出力を `mktemp` で作業ツリーの外に作った file に受ける。この file は反映を終えるまで消さない。返った件数が `--limit` に届いていたら上げて引き直す (`--limit` を超えた分は黙って打ち切られる)。読む前に件数を確かめ、全件の本文を読んで原因で束ねきれない件数なら、絞り方だけを先に user に聞く。原因の仮説で束ねるには全件の本文が要るので、要約へ落とさず自分で読む。`gh issue list` は issue ごとのコメントを先頭 100 件で黙って打ち切るので、コメントがちょうど 100 件返った issue だけ `gh issue view <N> -R <issue 置き場> --json comments` で読み直す (`gh issue view` は残りのコメントも引く)
- open CL を `gh pr list -R <CL 置き場> --state open --limit 1000 --json number,title,body` で同じく全件引き、どの issue を実装しているかを控える
- close 済みの issue は、題名だけを `gh issue list -R <issue 置き場> --state closed --limit 200 --json number,title,closedAt,stateReason,labels` で引く。同じ種類の issue が繰り返し生まれているかは、open issue だけでは数えられない。重複として閉じられた issue と、束ね先へ閉じられたメンバーは、同じ報告の数え直しになるので繰り返しに数えない。本文は、繰り返しの候補に当たった題名のものだけ読む。引けた範囲 (最も古い日付) は最初のラウンドに書く
- `tracking-issue` に当たる label の付いた open issue は、`${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/references/parent-child.md` の「結び方」が記録する先 (tracker の親子の関連、関連を持たない tracker では親の本文の task list の行) から子を引く。子の引き方は対象 repo の `docs/agents/issue-tracker.md` の CLI 規約に従い、規約が無ければ GitHub の sub-issue は `gh api repos/<issue 置き場>/issues/<N>/sub_issues --paginate --jq '.[] | [.number, .state]'` で引く。子がすべて close された親は、下の「親を閉じる」の案にする — 親は triage にかからないので、ここで閉じなければ open のまま残る。子を引けなかった親は案にせず、引けなかった理由を最初のラウンドに書く
- 原因の候補を挙げ、候補ごとに説明できる issue を照らして比べる。候補は設計文書の保証・設計規則・境界から探す。設計文書は、原則 leaf `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/references/leaves/principle-design-conformance.md` の「設計 doc の置き場の引き方」で引いたものだけを読む。保証・設計規則・境界を行に並べ、issue ごとに、反している行か、当たる行が無いことを置く。同じ行に集まった issue が候補になる。当たる行が無い issue と、引ける設計文書が無い repo の issue は、component や題名の近さから探す。原因は本文と、`git fetch` 後の `origin/<既定 branch>` にあるコード・手順・規範 (`git show` / `git grep`) で確かめる — 同じ component に触れていても原因が別なら別のクラスタにする。同じ指摘を持つ重複も 1 つのクラスタにする。cwd は user の checkout でありうるので HEAD は動かさない
- 候補は、解決すれば説明する issue が不要になるなら根本課題 (その issue がクラスタ)、不要にならず同じ種類が生まれなくなるだけなら上流の課題に分ける。説明する issue が重なる候補は、同じ種類の候補どうしで比べ、採る候補は上の原則 leaf の、繰り返しがあるときに直す層の既定で決める。すべてを説明する候補が無ければ、説明の範囲ごとに候補を分け、1 つへまとめない。根本課題と上流の課題は、同じ issue を説明していても両方を残す。同じ file を変えるという理由だけでは束ねない — 原因が別の issue を 1 件にすると、片方の課題が解けたかを受け入れ条件で見分けられなくなる
- 根本課題と上流の課題は、根拠 (issue 本文・コード・過去の決定の引用) を添えた仮説として書く。実行しないと決まらないものは、再現の確認を受け入れ条件に含める
- クラスタと上流の課題は、次の表の違いを除いて同じ手順で確かめる

  | | クラスタ | 上流の課題 |
  |---|---|---|
  | 通す検査 | 下の 2 つ | 下の 2 つ目だけ |
  | 反証の軸 | 根本課題の共有 | 同じ種類の issue を生む原因の共有 |
  | 保つ定義 | 根本課題 | 上流の課題 |
  | やめる条件 | メンバーが 1 件になったら解く | 説明する issue が close 済みを含めて 1 件になったら取り下げる |

- 次の 2 つを通してから承認のラウンドへ出す
  - メンバーの受け入れ条件 (無ければ本文の観測) が 1 行ずつ根本課題の受け入れ条件へ写り、メンバーが範囲に書いた制約と矛盾しない
  - 根本課題か上流の課題の主張が反証を通る。クラスタと上流の課題 1 つにつき subagent を 1 体、`subagent_type: swat-skills:refuter` で同じ message に立て、`model` / `effort` を渡さない (深さは定義が固定する)。prompt には、表の反証の軸を軸として課題の主張を指摘に、メンバーの本文とコメントと根拠を一次資料に、file:line か issue 本文の引用を具体物として認めるものに置く。返す項目は定義の項目に、崩れたメンバーの番号と成り立つ言い方を足す。メンバーの本文とコメントは `mktemp` で作業ツリーの外に作った dir に切り出し、`origin/<既定 branch>` の tree は `git archive` で同じ dir の下へ展開して、その dir を repo の root として渡す — 反証者は `git show` を実行できず、作業ツリーの同じ path は別の版でありうる。作者が選んだ file だけを渡すと、選ばなかった file からの反証が消える。渡すのは引用と出典までで、それらをつなぐ推論の文は渡さない — 案の作者の見方を渡すと、主張を壊す読み方が消える
- 具体物のある反証は受け入れ、語を弱めれば成り立つなら成り立つ最も強い言い方へ直す。直した言い方でも表の定義を保てるメンバーだけを残し、保てないメンバーはクラスタから外す。直した点と外したメンバーは最初のラウンドに書く — 直した形は人が Q で読むので、反証し直さない。表のやめる条件に当たったら、そのとおりにする。指名が解決しないときは `general-purpose` で代えない — 代えると model・effort が定義から黙って外れ、指名した深さで反証したように見える。指名が解決しないか、定義の形の結果を得られなかったら、そのクラスタと理由を user へ報告し、承認のラウンドへ出さない

クラスタと issue ごとに案を 1 つ決める。作る issue と書き換える issue の本文は、次の課題型の雛形で書く。

```
## 課題
<解くべき問題>

## 受け入れ条件
<解けたと判断できる観測可能な条件>

## 観測
<元の issue が書いていた事実。束ねたときはメンバーの番号と、再現条件などメンバー固有の事実>

## 候補の手段
<元の issue が指定していた解き方。必須ではなく候補>
```

- **根本課題 issue を作る**: 題名は課題の形にし、雛形で本文を書き、`needs-triage` を付ける (2 ラウンドで決まらず `needs-grilling` を付ける根本課題 issue を除く)。メンバーは根本課題 issue へのリンクをコメントしてから close する
- **既存の issue を使い回す**: コメント欄が既に根本課題そのものの議論になっている issue があれば、新しく作らずその issue を雛形で書き換えて根本課題 issue にする
- **寄せ集めを振り分ける**: 複数の根本課題にまたがる指摘を寄せ集めた issue は、中身を各クラスタの根本課題 issue へ振り分け、振り分け先のリンクをコメントしてから close する
- **上流の課題 issue を作る**: この項の起票は、対象 repo の `docs/agents/issue-tracker.md` の「follow-up issue の起票」節の手順ではなく、本項に従う。起票するかの基準だけは同節に合わせる。cwd を対象 repo にして `${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/scripts/follow-up-criteria.py --defaults ${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/references/filing-criteria-defaults.md` で「単位の外」の箇条を引く。その箇条と節の補足が起票しないとする種類に当たる上流の課題は案にせず、最初のラウンドの一覧表に、起票しないことと当てた箇条を添えて載せる — 続く triage が同じ基準で `wontfix` にするので、作っても閉じられる。script が箇条を出さずに終わったら、上流の課題は案にせず、一覧表に script の stderr を添えて載せる。案にするものは、題名は課題の形にし、雛形で本文を書き、`needs-triage` を付ける。「観測」には、説明する issue の番号を close 済みも含めて並べる。open のメンバーは close せず、上流の課題 issue へのリンクをコメントする — メンバーが書く不具合は上流を直しても残るので、個別に直す issue として triage へ渡す。同じ課題の open issue が既にあれば作らず、その issue にメンバーの番号をコメントで足す。同じ課題の issue が `wontfix` で閉じられていれば案にせず、一覧表にその番号を添えて載せる。2 ラウンドを経ても課題が決まらなければ取り下げる (メンバーは open のまま残るので、取り下げても失うものが無い)
- **子 issue に分ける**: 1 つの CL に収まらない根本課題は課題型の子 issue に分け、子同士の依存を tracker の依存リンクで表す。対象 repo の `docs/agents/issue-tracker.md` が依存リンクの手順を持たないなら、依存は子の本文の `Blocked by: #<N>` の行で表し、その行をこの段で子の本文の案に入れる。親には `${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/references/parent-child.md` の「issue を子に分けたとき」の時点で `tracking-issue` を付け、本文に「子 issue の追跡用で、直接は実装しない。子がすべて close されたら close する」と書く — label は機械が親を着手対象から外すための印で、本文は triage で読む人に理由を伝える。作った親にも、使い回して親にした既存の issue にも付ける
- **親を閉じる**: 調査で子がすべて close されたと分かった `tracking-issue` の親は、閉じた子の番号を挙げたコメントを投稿して close する
- **単発を書き換える**: どのクラスタにも入らない観測型・手段型の issue のうち、課題か受け入れ条件が欠けているものは、番号を変えずに題名と本文を雛形で書き換える — 手段を仕様のまま残すと、課題を解かずに手段だけが実装される
- **変更なし**: 上のどれにも当たらない issue

本文を書き換える案は、書き換え後の本文全体で作る。元の本文に、前の本文より優先すると宣言した追記の節 (`## 確定事項` 等) があれば、この段でその内容を本文の該当箇所へ溶かし込み、節を消す — 書き換えた本文の中に優先順位の規則を残さない。

本文を書き換える issue は、`${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/references/body-rewrite.md` の「書き換えた後の状態」が決める label へ付け替える案にする (2 ラウンドで決まらず `needs-grilling` を付ける根本課題 issue と、子に分けて親にする issue を除く。親にする issue は、同節の `tracking-issue` の項と同じく状態の label を変えない) — 書き換えた本文で状態を裁き直すのは triage で、特に `ready-for-agent` は本文をそのまま実装してよいという保証なので、本文が変われば失効する。open CL があるメンバーは、close や書き換えの選択肢に CL の差し戻しを入れる — その CL は観測ごとの修正を実装している。issue を閉じる open CL (数え方は `body-rewrite.md` の「open CL の確かめ」) があるメンバーの書き換えは、その CL を先に close する選択肢にだけ入れる — 閉じる CL が open のままだと、同書の書き戻しが止まる。元の本文の `発端:` の行は、同書の「書き換え案に残す行」に従って書き換え案に残す (雛形に `発端:` の節は作らず、行のまま残す)。

## 承認のラウンド

user には番号付きの設問をラウンドで出す。最初のラウンドの冒頭にクラスタと上流の課題の一覧表 (種類 (クラスタ・上流の課題のどちらか) / メンバー / 課題の仮説 / 層 (events・structures・mental models のどれか) / 根拠) を置き、表の行ごとに 1 つの Q を立てる。単発の書き換えはまとめて 1 つの Q にし、「親を閉じる」案もまとめて 1 つの Q にする (閉じる親ごとに、閉じた子の番号を挙げたコメントを逐語で並べる)。作る issue の本文・書き換え後の本文・投稿するコメントは、その Q の本文に逐語で並べ、その Q で実行する操作 (close するメンバー・付け外す label・依存リンク・CL の差し戻しと close) と、本文を書き換える Q では旧本文の記録に書く理由の文も並べる — user が読んでいない文面と操作を tracker に載せない。まだ番号の無い issue への参照は `#<根本課題: 題名>` のような placeholder で書き、反映で置き換えてよいのはこの placeholder だけにする。CL に投稿する文面が issue を指すときは、`${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/references/placement.md` の「置き場をまたぐ参照の書き方」を Read し、その綴りの `<issue 置き場>#<N>` (placeholder なら `<issue 置き場>#<根本課題: 題名>`) で書く。反映で置き換えるのは、この形でも `#<根本課題: 題名>` の部分だけになる。

```
❓ **Q1** - **<見出し>**: <本文。作る issue・書き換え後の本文・コメント・旧本文の記録の理由の文面は逐語で、実行する操作も並べて>

➡️ <推奨する回答 (選択肢なら 1 つを名指す) と根拠>

---

❓ **Q2** - ...
```

根本課題をめぐって問答を重ねてよい。同じラウンドの他の Q の答えに依存する Q と、回答で新たに生まれた判断は、次のラウンドに出す。2 ラウンドを経ても根本課題が決まらないクラスタは、決まった範囲で根本課題 issue を作るか既存の issue を使い回して `needs-grilling` を付け、メンバーは通常どおりリンクをコメントして close する — open のまま残すと、次の triage や深掘りがメンバーを観測ごとに `ready-for-agent` にしうる。束ね方が覆ったら、深掘りのセッションがメンバーを reopen する。`needs-grilling` にする根本課題 issue には、`${CLAUDE_SKILL_DIR}/../issue-triage/references/grilling-handoff.md` を Read し、その雛形の値 (close するメンバーの番号を含む) を埋めて手順コメントを作り、そのクラスタの Q に逐語で出す。「推奨でOK」のような一括の回答は、そのラウンドの全 Q を推奨どおりに承認する。読み取れない回答は聞き直す。

聞くことが無くなったら反映へ進む。反映する操作がすべて、承認された Q に書かれた形のままなら、そのまま書く — 承認は Q の回答で済んでいる。それ以外の操作があれば、反映する操作の一覧を出して「この一覧どおりに反映するか」を確かめてから書く。修正が返ったら該当する Q を次のラウンドへ戻し、一覧を出し直す。

## 反映と報告

承認された操作だけを、承認された文面のまま反映する — 機械が勝手に tracker を動かさないことが、他セッションからの信頼になる。文面は `mktemp` で作業ツリーの外に作った file に書き、`--body-file` で渡す。他の文面が新しい issue の番号を指すので、issue の作成を最初に行い、参照される側から順に作る。作るたびに、決まった番号で後続の文面の placeholder を置き換える。新しい issue 同士が互いを参照するなら、先に作った issue の placeholder を、相手を作った後に `gh issue edit <N> -R <issue 置き場> --body-file <file>` で埋める — 置き換えるのは placeholder だけで、作ったばかりの本文なので旧本文の記録は要らない。

書き込みが 1 つでも失敗したら、その issue の番号を参照する残りの操作を止める。報告には、成功した操作・失敗した操作とその理由・反映していない承認済みの操作を並べる — user が残りから再開できるようにする。

- 作成: `gh issue create -R <issue 置き場> --title <題名> --body-file <file> --label <needs-triage か needs-grilling>`
- 子 issue の紐付けと `tracking-issue` の付与: `${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/references/parent-child.md` を Read し、その手順で行う (作った親にも、使い回して親にした既存の issue にも。止めたら上の「書き込みが失敗した」ときと同じく扱う)
- 依存リンク: 対象 repo の `docs/agents/issue-tracker.md` の手順に従う。手順が無ければ、「子 issue に分ける」の案で子の本文に入れた `Blocked by:` の行の placeholder を置き換えるだけにし、承認された本文に行を足さない
- 状態の label: `${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/references/state-label.md` を Read し、その付け替え方で付け替える (止めたら上の「書き込みが失敗した」ときと同じく扱う)。使い回して `needs-grilling` にする根本課題 issue も、ここで付け替える
- コメント: `gh issue comment <N> -R <issue 置き場> --body-file <file>`
- close (メンバー・寄せ集め・子がすべて close された親): `${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/references/close-reopen.md` を Read し、その「close」の順で、承認されたコメントを投稿してから閉じる (止めたら上の「書き込みが失敗した」ときと同じく扱う)
- 手順コメント: `needs-grilling` にした根本課題 issue へ、作成とメンバーの close を済ませた後に、承認された手順コメントを `gh issue comment <N> -R <issue 置き場> --body-file <file>` で 1 件投稿する — 雛形はメンバーを close 済みと述べるので、close に失敗したメンバーがあれば投稿しない
- CL の差し戻し: 承認された文面のコメントを `gh pr comment <N> -R <CL 置き場> --body-file <file>` で投稿する。CL の close は、承認されたときだけ `gh pr close <N> -R <CL 置き場>` で行う。承認された CL の close は、その CL が閉じる issue の本文の書き換えより先に撃つ
- 本文の書き換え: `${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/references/body-rewrite.md` を Read し、その「書き戻し」の順で書き戻す。基準は、調査で受けた file にあるその issue の body を切り出したもの。承認された状態の label の付け替え (同書の「書き換えた後の状態」が決める label か `needs-grilling`) は、同書の手順 1 で 1 回の操作として済ませる (上の label の操作と同じもので、二度は撃たない)。`ready-for-human` にした issue には、同節のコメントを手順 5 の後に投稿する。文面はその Q に逐語で出す。同書が人へ報告するとした内容の宛先は user。同書の「open CL の確かめ」・「書き換え案に残す行」の確かめ・手順 2・5 のどれかで止めたら、上の「書き込みが失敗した」ときと同じく、その issue の番号を参照する残りの操作 (メンバーの close・手順コメント) も止める

最後に、反映した操作・承認されず据え置いた issue・`needs-grilling` (今回 `needs-grilling` にした根本課題 issue の一覧と、`needs-grilling` の残数)・user に残る作業を報告する。user に残る作業には、`ready-for-human` の issue は規範の merge を待っていることを含める。深掘りのセッションへの手順は手順コメントが持つので、報告には展開しない。`needs-grilling` に当たる label が無い repo では、手順コメントを投稿しない代わりに、根本課題 issue ごとに手順コメントを逐語で報告に書く。メンバーの close に失敗して手順コメントを投稿しなかった根本課題 issue は、close に失敗したメンバーの番号と埋めた手順コメントの逐語を user に残る作業に書く — 深掘りのセッションへ手順が届く経路が他に無い。

報告の後、反映で失敗した操作も止めた操作も無ければ、`${CLAUDE_SKILL_DIR}/../backlog-triage/SKILL.md` を Read し、その手順を同じ session で続ける。その本文の path に展開されていない変数が残っていれば、その変数を `${CLAUDE_SKILL_DIR}/../backlog-triage` と読み替える。反映する操作が無かったときも続ける。作った issue と書き換えた issue は `ready-for-agent` を持たず、triage が裁くまで着手されない。失敗したか止めた操作があれば続けない。そのときは user に残る作業に、残りを片付けてから `/swat-skills:backlog-triage` を回すことを書く。途中の backlog を triage が裁くと、close するはずのメンバーが `ready-for-agent` になりうる。
