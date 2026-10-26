---
name: playbook-implementation
description: 実装作業を、原則の索引 Read から PR を ready にするまでの番号付き step で通す. Use when 呼び出し元 (user / skill / spawn prompt) が実装の playbook を名指しで通すよう指示したとき.
user-invocable: true
metadata:
  deliverable: cl
  dispatch-when: コード・設定・docs の変更で受け入れ条件が閉じる issue 全般 (既存 skill の改修を含む)。
---

# 実装の playbook

コードを書き換えて PR へ出し、PR を ready にして作業を終えるまでの手順。ready は実装者の作業が完了し、PR の内容がもう変わらないことを示す。step の中でどの skill と原則を使うかを名前で置いてある。

## 使い方

入口は 3 つあり、どれも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-implementation` で開くか、skill / セッションが Skill tool で invoke するか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step 1 に着手する前に、todolist tool で step を逐語 todolist へ写す。** 要約・統合・並べ替えをしない (変形した分だけ必須工程が silent に欠ける)。各項目の題には step 番号と先頭文を逐語で書き、step 本文を条件節・sub-bullet ごとそのまま添える。skip する step は題の先頭に `skip: <理由>` を付ける。todolist tool が tool 一覧に無いときは、写す先を最終返答に替え、冒頭に「todolist tool なし」と 1 行書く
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない。条件付き step で条件に当たらないときは、その条件を理由に書く。spawn prompt から Read で開いたとき (人が todolist を見ない) は、skip 行を最終返答 (PR 到達時はレビュー出力とは別の 1 コメントで PR) にも写す
- **複数の issue / PR に続けて適用するときは、issue ごとに本文を読み直してから step 1 から通す** (todolist も issue ごとに作り直す)。記憶で工程を再現すると、context が薄れた分だけ必須工程 (反証・読み直し・原則 leaf の Read) が silent に欠ける。skill を invoke する step は issue ごとに invoke し直す — 前 issue の記憶から reviewer の subagent を直接立てると、invoke 先 skill の手順が抜ける

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、索引の読み方に従って今回の作業に当たる leaf を読む。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。以降の step が `principle-*` を名指ししたときも、索引の読み方に従う。

2. 受け入れ条件を確定する。依頼の本文 (issue 等) を逐語で読み、何を満たせば完了かを列挙する。作業ツリーの外にある実体を触る条件は自分の担当から外し、誰の担当として残るかを書き出す (作業ツリーの外の変更は CL の差分にもレビューにも載らないので、抱えたままだと完了を示せない)。

3. 土台を最新にする。既定ブランチから分岐した作業ブランチにいることを確かめてから、作業ツリーの repo で `git pull --no-rebase <remote> <既定ブランチ>` を実行する (既定ブランチ上にいるなら先に作業ブランチを作る)。古い土台の上に積んだ差分は merge のときにやり直しになる。

4. 変更の形を決める。選択肢が複数あるなら `principle-design-derivation-order` (制約 → アーキテクチャ特性 → 構造の順で導く) と `principle-state-coupling-complexity-code` (状態 > 結合 > 複雑性 > コード量の順に比べ、波及を抑える案を取る) で選ぶ。自前実装と既製品が拮抗したら `principle-build-vs-buy`。決めた理由と退けた案を書き留める — step 7 の PR 説明文で使う。

5. テストを先に書く。条件: 振る舞いを追加・修正し、その対象にテストを書くと `principle-test-thickness-by-property` が決めるとき。`principle-tdd-rhythm` で失敗するテストを先に置き、`principle-test-level-allocation` でレベルを決め、`principle-test-as-spec` で名前と構造を決める。振る舞いが変わらない作業 (文書・設定・構造だけのリファクタリング) は `skip: 振る舞いの変更なし`、テストを書かない性質の対象 (推論で効く文を含む) は `skip: テストを書かない対象` — 両方に当たるときは先に挙げたほうを書く。

6. 実装する。`principle-structure-simplicity` (責務の置き場と共通化の有無) / `principle-intention-revealing-names` (識別子と概念名) / `principle-signature-intent` (引数とシグネチャ) / `principle-fail-loudly` (エラー処理とフォールバック) を当てる。step 5 で書いたテストと変更が触れる範囲のテストは手で走らせる。対象 repo の commit / push の hook が走らせる検査は hook に任せて commit し (commit の前に手で走らせると、hook が同じ検査をもう一度走らせる)、どの hook も走らせない検査は commit の前に手で走らせる。hook が落ちたら、落ちた検査が指摘した箇所を直して commit し直す。

7. PR 到達前にレビュー工程を通す。通ったら PR を draft で出してレビューの出力をコメントで投稿し、作業が完了したら ready にする。`swat-skills:review-stage` を Skill tool で invoke する。
   - review-stage は commit 済みの差分だけを読むので、invoke の前に変更を commit する。発端の issue があれば `issue=<ref>` を args で渡す。出力の `再レビュー:` 行が `停止` で始まるときは、理由が `未 commit` で、`停止理由:` 行に挙がった file がすべてこの作業で入れた変更なら commit して同じ args で invoke し直す (この停止出力を `prev=` に渡すと初回へ落ちる)。それ以外の停止 (無関係な変更を含む `未 commit`・`reviewer 未起動`・`その他: <自由文>`) は、レビューを通らないまま先へ進まないよう user へ報告して止まる (spawn prompt から開いたときは最終返答に載せる)。`停止` で始まらなければ Act on を直して commit し、`再レビュー:` 行が `要` で始まるときだけ `swat-skills:review-stage` を invoke し直し、前巡の出力を保存した file を `prev=<file>` で渡す — 前の invoke の記憶から reviewer を直接立てると、invoke 先の手順が抜ける。`要` で始まらなければレビューはそこで終える
   - レビューを 1 回走らせるたびに、その出力を注記行まで含めて逐語で一時 file へ保存する — PR へ載せるのは PR を出した後なので、context に頼ると compaction で逐語が崩れる。置き場は作業ツリーの外 (中に置くと commit に混ざるか、untracked file が作業ツリーの削除を拒む)、名前はどのレビューの何巡目かが分かる形にし、投稿したら消す。最後に走らせた巡の後に修正したら、その修正を「レビュー後に修正 (未レビュー)」として最後の巡の file へ書き足す — その修正はレビューを通っていないので、通っていないことを人が読める形で残す
   - PR を出したら (既存の PR へ push する作業なら push したら)、保存した file を走らせた順に 1 回 1 コメントで**そのまま** PR へ投稿する (`gh pr comment <番号> --body-file <file>`。GitLab は `glab mr note create <番号> --resolvable=false < <file>` — 解決待ちの thread にすると merge を塞ぐ)。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる — 注記行が無いと、Act on のうちどれだけが反証を経たかを読み手が判別できない。PR 説明文はレビューを経た最終状態だけで書き、PR を初めて見た人が何を変えたかを読み取れるようにする — レビューの経過はコメントが持つ
   - レビューで直さなかった指摘と、担当範囲の外で見つけた欠陥は、作業ツリーの repo の issue の規範 (`docs/agents/issue-tracker.md` など) に従って、起票するかを決めて起票する
   - PR 説明文の節構成は `swat-skills:pr` を Skill tool で invoke し、その template を土台に決める。template の節のうち、この CL に中身の無い節は省く。呼び出し元 (spawn prompt・workflow 定義) が求める節は template の節と並べて足し、中身が無くても見出しを残して「なし」と書く (後段の作業がその節を見出しで探しに来る)。説明文の節がそろったら PR を draft で出し (既にある PR へ push する作業ならその PR のまま)、ready にするのは作業が完了したときだけにする。完了は、説明文に未確定の値が無く、PR に載せる記録 (保存したレビュー出力と、PR へ写すときの skip 行) を投稿し終えた状態で、投稿の後に commit を足したならその修正も「レビュー後に修正 (未レビュー)」としてコメントで足してある。待ちの長い値 (計測や実行の完了) は、待っている値とそれを生む手段 (コマンド・job の参照先) を書いて未確定と明記したまま draft で待ち、確定して書き換えてから ready にする — draft で先に出しておけば、待つ間に呼び出し元が課す実行時間の上限に当たっても PR と記録が残る。待ちきれずに終えるときは draft のまま終え、未確定の値と手段を最終返答に載せる。GitHub は `gh pr create --draft` / `gh pr ready <番号>`、GitLab は `glab mr create --draft` / `glab mr update <番号> --ready`
   - step 4 で書き留めた理由と退けた案は、呼び出し元が判断を書く節を求めればその節に書く。求めなければ、`## 判断` の節を template の節と並べて足して短く載せる (中身が無ければ省く)
