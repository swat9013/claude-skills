---
name: issue-restructure
description: open issue を根本課題ごとのクラスタに束ねて課題型の issue へまとめ直す案を、人間の承認を通して tracker へ反映する、triage の前段の対話。
disable-model-invocation: true
---

# issue-restructure

open issue を全件読み、根本課題を共有する issue をクラスタに束ねて、課題型の issue にまとめ直す。束ね方と本文を user に承認してもらい、承認された分だけ tracker へ反映する。`ready-for-agent` の判断は triage に任せる — 本 skill が付ける label は `needs-triage`・`needs-grilling`・`tracking-issue` だけで、作った issue と書き換えた issue は (2 ラウンドで決まらず `needs-grilling` を付ける根本課題 issue を除き) `needs-triage` にして返す。形を整えてから triage が状態を裁けば、close される issue に判断を費やさずに済む。

語は次の意味で使う。

- **観測型 issue**: 起きた不具合や食い違いを書いたもの。**手段型 issue**: 解き方を先に指定したもの。**課題型 issue**: 解くべき問題と、解けたかの判定条件を書いたもの
- **根本課題**: クラスタの共通原因を課題の形で言い直したもの。解決すればクラスタ内のどの issue も不要になる (再現しなくなる・書く理由が消える) 原因のうち、最も浅いものを採る。1 件しか説明しない原因は根本課題にしない。原因が iceberg model のどの層にあるかと、どの層の原因を採るかは原則 leaf `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/references/leaves/principle-root-cause-and-hierarchy-of-controls.md` に従う

置き場は `-R` で明示する: issue は `-R <issue 置き場>`、PR は `-R <CL 置き場>`。issue 置き場は、呼び出し元が指定していればそれを使う。指定が無ければ対象 repo の `docs/agents/issue-tracker.md` の宣言に従う。宣言が別の file を正本として指すなら、その file が置き場として宣言する値を使う。指された file が無いか値を読み取れなければ、対象 repo 自身へ落とさず、止まって、指された file の path と読めなかった理由を挙げて置き場を user に問う — 正本が別 repo を宣言していると、同じ番号の別の issue を読むことになる。`docs/agents/issue-tracker.md` が無いか置き場の宣言を読み取れなければ対象 repo 自身を置き場とする。CL 置き場と CLI 規約・label の綴り (`needs-triage`・`ready-for-agent`・`needs-info`・`ready-for-human`・`needs-grilling`・`tracking-issue`) は対象 repo の `docs/agents/issue-tracker.md` / `docs/agents/triage-labels.md` を Read して決める。無ければ CL 置き場は cwd の repo、label は上に並べた既定の名前とし、その前提を報告に書く。`needs-grilling` に当たる label が無い repo では、深掘りを待つ根本課題 issue を label でなく報告で返す。`tracking-issue` に当たる label が無い repo では label を付けず、親の着手を止めるのは本文の 1 文だけになることを報告に書く。コマンド例は GitHub (`gh`) の綴りで、他の tracker なら読み替える。

## 調査

tracker に何も書かずに調べる。

- 判断の規律は `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、当たる leaf を読んで使う
- open issue を本文とコメントごと `gh issue list -R <issue 置き場> --state open --limit 1000 --json number,title,body,labels,comments` の 1 回で全件引き、出力を `mktemp` で作業ツリーの外に作った file に受ける。この file は反映を終えるまで消さない。返った件数が `--limit` に届いていたら上げて引き直す (`--limit` を超えた分は黙って打ち切られる)。読む前に件数を確かめ、全件の本文を読んで原因で束ねきれない件数なら、絞り方だけを先に user に聞く。原因の仮説で束ねるには全件の本文が要るので、要約へ落とさず自分で読む。`gh issue list` は issue ごとのコメントを先頭 100 件で黙って打ち切るので、コメントがちょうど 100 件返った issue だけ `gh issue view <N> -R <issue 置き場> --json comments` で読み直す (`gh issue view` は残りのコメントも引く)
- open CL を `gh pr list -R <CL 置き場> --state open --limit 1000 --json number,title,body` で同じく全件引き、どの issue を実装しているかを控える
- 原因の仮説で束ねる。component や題名の近さは候補を探すのに使い、原因は本文と、`git fetch` 後の `origin/<既定 branch>` にあるコード・手順・規範 (`git show` / `git grep`) で確かめ、設計文書は原則 leaf `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/references/leaves/principle-design-conformance.md` の「設計 doc の置き場の引き方」で引いたものだけを読む — 同じ component に触れていても原因が別なら別のクラスタにする。同じ指摘を持つ重複も 1 つのクラスタにする。cwd は user の checkout でありうるので HEAD は動かさない
- 根本課題は、根拠 (issue 本文・コード・過去の決定の引用) を添えた仮説として書く。実行しないと決まらないものは、再現の確認を判定条件に含める

クラスタと issue ごとに案を 1 つ決める。作る issue と書き換える issue の本文は、次の課題型の雛形で書く。

```
## 課題
<解くべき問題>

## 判定条件
<解けたと判断できる観測可能な条件>

## 観測
<元の issue が書いていた事実。束ねたときはメンバーの番号と、再現条件などメンバー固有の事実>

## 候補の手段
<元の issue が指定していた解き方。必須ではなく候補>
```

- **根本課題 issue を作る**: 題名は課題の形にし、雛形で本文を書き、`needs-triage` を付ける (2 ラウンドで決まらず `needs-grilling` を付ける根本課題 issue を除く)。メンバーは根本課題 issue へのリンクをコメントしてから close する
- **既存の issue を使い回す**: コメント欄が既に根本課題そのものの議論になっている issue があれば、新しく作らずその issue を雛形で書き換えて根本課題 issue にする
- **寄せ集めを振り分ける**: 複数の根本課題にまたがる指摘を寄せ集めた issue は、中身を各クラスタの根本課題 issue へ振り分け、振り分け先のリンクをコメントしてから close する
- **子 issue に分ける**: 1 つの CL に収まらない根本課題は課題型の子 issue に分け、子同士の依存を tracker の依存リンクで表す。親には `tracking-issue` を付け、本文に「子 issue の追跡用で、直接は実装しない。子がすべて close されたら close する」と書く — label は機械が親を着手対象から外すための印で、本文は triage で読む人に理由を伝える。作った親にも、使い回して親にした既存の issue にも付ける
- **単発を書き換える**: どのクラスタにも入らない観測型・手段型の issue のうち、課題か判定条件が欠けているものは、番号を変えずに題名と本文を雛形で書き換える — 手段を仕様のまま残すと、課題を解かずに手段だけが実装される
- **変更なし**: 上のどれにも当たらない issue

本文を書き換える案は、書き換え後の本文全体で作る。元の本文に、前の本文より優先すると宣言した追記の節 (`## 確定事項` 等) があれば、この段でその内容を本文の該当箇所へ溶かし込み、節を消す — 書き換えた本文の中に優先順位の規則を残さない。

本文を書き換える issue は、状態を表す label (`ready-for-agent`・`needs-info`・`ready-for-human` など) を外して `needs-triage` を付ける案にする (2 ラウンドで決まらず `needs-grilling` を付ける根本課題 issue を除く) — 書き換えた本文で状態を裁き直すのは triage で、特に `ready-for-agent` は本文をそのまま実装してよいという保証なので、本文が変われば失効する。open CL があるメンバーは、close や書き換えの選択肢に CL の差し戻しを入れる — その CL は観測ごとの修正を実装している。

## 承認のラウンド

user には番号付きの設問をラウンドで出す。最初のラウンドの冒頭にクラスタの一覧表 (クラスタ / メンバー / 根本課題の仮説 / 層 (events・structures・mental models のどれか) / 根拠) を置き、クラスタごとに 1 つの Q を立てる。単発の書き換えはまとめて 1 つの Q にする。作る issue の本文・書き換え後の本文・投稿するコメントは、その Q の本文に逐語で並べる — user が読んでいない文面を tracker に載せない。まだ番号の無い issue への参照は `#<根本課題: 題名>` のような placeholder で書き、反映で置き換えてよいのはこの placeholder だけにする。

```
❓ **Q1** - **<見出し>**: <本文。作る issue・書き換え後の本文・コメントの文面は逐語で>

➡️ <推奨する回答 (選択肢なら 1 つを名指す) と根拠>

---

❓ **Q2** - ...
```

根本課題をめぐって問答を重ねてよい。同じラウンドの他の Q の答えに依存する Q と、回答で新たに生まれた判断は、次のラウンドに出す。2 ラウンドを経ても根本課題が決まらないクラスタは、決まった範囲で根本課題 issue を作るか既存の issue を使い回して `needs-grilling` を付け、メンバーは通常どおりリンクをコメントして close する — open のまま残すと、次の triage や深掘りがメンバーを観測ごとに `ready-for-agent` にしうる。束ね方が覆ったら、深掘りのセッションがメンバーを reopen する。`needs-grilling` にする根本課題 issue には、`${CLAUDE_SKILL_DIR}/../issue-triage/references/grilling-handoff.md` を Read し、その雛形の値 (close するメンバーの番号を含む) を埋めて手順コメントを作り、そのクラスタの Q に逐語で出す。雛形の二つの path には `${CLAUDE_SKILL_DIR}/../../design/grill-with-docs/SKILL.md` と `${CLAUDE_SKILL_DIR}/../issue-triage/references/body-rewrite.md` を、ここに見えている絶対 path のまま書く。「推奨でOK」のような一括の回答は、そのラウンドの全 Q を推奨どおりに承認する。読み取れない回答は聞き直す。

聞くことが無くなったら、反映する操作の一覧を出して「この一覧どおりに反映するか」を確かめてから書く。修正が返ったら該当する Q を次のラウンドへ戻し、一覧を出し直す。

## 反映と報告

承認された操作だけを、承認された文面のまま反映する — 機械が勝手に tracker を動かさないことが、他セッションからの信頼になる。文面は `mktemp` で作業ツリーの外に作った file に書き、`--body-file` で渡す。他の文面が新しい issue の番号を指すので、issue の作成を最初に行い、参照される側から順に作る。作るたびに、決まった番号で後続の文面の placeholder を置き換える。新しい issue 同士が互いを参照するなら、先に作った issue の placeholder を、相手を作った後に `gh issue edit <N> -R <issue 置き場> --body-file <file>` で埋める — 置き換えるのは placeholder だけで、作ったばかりの本文なので旧本文の記録は要らない。

書き込みが 1 つでも失敗したら、その issue の番号を参照する残りの操作を止める。報告には、成功した操作・失敗した操作とその理由・反映していない承認済みの操作を並べる — user が残りから再開できるようにする。

- 作成: `gh issue create -R <issue 置き場> --title <題名> --body-file <file> --label <needs-triage か needs-grilling。子 issue に分けた親には、そのどちらかに加えて、label のある repo なら tracking-issue も>`
- 依存リンク・子 issue の紐付け: 対象 repo の `docs/agents/issue-tracker.md` の手順に従う。無ければ、承認した子の本文に `Blocked by: #<N>` の行を含めておく
- label: `gh issue edit <N> -R <issue 置き場> --add-label <label> --remove-label <label>`。使い回して子 issue の親にした既存の issue には、ここで `tracking-issue` を足す。使い回して `needs-grilling` にする根本課題 issue も、ここで付け替える
- コメント: `gh issue comment <N> -R <issue 置き場> --body-file <file>`
- close: リンクを書いたコメントを投稿してから `gh issue close <N> -R <issue 置き場>`
- 手順コメント: `needs-grilling` にした根本課題 issue へ、作成とメンバーの close を済ませた後に、承認された手順コメントを `gh issue comment <N> -R <issue 置き場> --body-file <file>` で 1 件投稿する — 雛形はメンバーを close 済みと述べるので、close に失敗したメンバーがあれば投稿しない
- CL の差し戻し: 承認された文面のコメントを `gh pr comment <N> -R <CL 置き場> --body-file <file>` で投稿する。CL の close は、承認されたときだけ `gh pr close <N> -R <CL 置き場>` で行う
- 本文の書き換え: `${CLAUDE_SKILL_DIR}/../issue-triage/references/body-rewrite.md` を Read し、その「書き戻し」の順で書き戻す。基準は、調査で受けた file にあるその issue の body を切り出したもの。承認された状態の label の付け替え (`ready-for-agent` などを外し、`needs-triage` か `needs-grilling` を付ける) は、同書の手順 1 で 1 回の操作として済ませる (上の label の操作と同じもので、二度は撃たない)。同書が人へ報告するとした内容の宛先は user。同書の手順 2 か 5 で止めたら、上の「書き込みが失敗した」ときと同じく、その issue の番号を参照する残りの操作 (メンバーの close・手順コメント) も止める

最後に、反映した操作・承認されず据え置いた issue・`needs-grilling` (今回 `needs-grilling` にした根本課題 issue の一覧と、`needs-grilling` の残数)・user に残る作業を報告する。user に残る作業には、作った issue と書き換えた issue が (2 ラウンドで決まらず `needs-grilling` を付けた根本課題 issue を除き) `needs-triage` になっているので、次に `/swat-skills:backlog-triage` を回すことを含める。深掘りのセッションへの手順は手順コメントが持つので、報告には展開しない。`needs-grilling` に当たる label が無い repo では、手順コメントを投稿しない代わりに、根本課題 issue ごとに手順コメントを逐語で報告に書く。メンバーの close に失敗して手順コメントを投稿しなかった根本課題 issue は、close に失敗したメンバーの番号と埋めた手順コメントの逐語を user に残る作業に書く — 深掘りのセッションへ手順が届く経路が他に無い。
