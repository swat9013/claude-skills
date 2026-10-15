---
name: playbook-plan-verification
description: 計画・spec の検証を、原則の索引 Read から判定の差し戻しまでの番号付き step で通す playbook。
disable-model-invocation: true
---

# 計画検証の playbook

計画・spec・ADR 案が実装可能で正本と整合しているかを検証し、判定を返すまでの手順。step の中でどの skill と原則を使うかを名前で置いてある。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-plan-verification` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step 1 に着手する前に、todolist tool で step を逐語 todolist へ写す。** 要約・統合・並べ替えをしない (変形した分だけ必須工程が silent に欠ける)。各項目の題には step 番号と先頭文を逐語で書き、step 本文を条件節・sub-bullet ごとそのまま添える。skip する step は題の先頭に `skip: <理由>` を付ける。todolist tool が tool 一覧に無いときは、写す先を最終返答に替え、冒頭に「todolist tool なし」と 1 行書く
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない。条件付き step で条件に当たらないときは、その条件を理由に書く。spawn prompt から Read で開いたとき (人が todolist を見ない) は、skip 行を最終返答 (PR 到達時はレビュー出力とは別の 1 コメントで PR) にも写す

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、索引の読み方に従って今回の作業に当たる leaf を読む。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。以降の step が `principle-*` を名指ししたときも、索引の読み方に従う。

2. 検証対象と正本を確定する。計画本文を逐語で読み、それが依拠している正本 (ADR・仕様 doc・既存実装) を列挙する。正本を特定できない主張は、この時点で検証不能として記録する。

3. 前提の実在を確かめる。計画が名指しした path・skill 名・tool・設定・節番号を 1 件ずつ `ls` / Read / 検索で確かめる。実在しない前提は計画側の欠陥であって、実行者が現場で埋める余地ではない。引用された決定記録の status が現行か (Superseded / Deprecated を根拠にしていないか) もここで見る。

4. 受け入れ条件が観測可能か確かめる。`principle-observable-behavior`: 「〜を考慮する」形の条件は、何を見れば満たしたと言えるかへ書き直せるかを問う。実行者の作業ツリーの外にある実体を触る条件は実行者が満たせないので、担当を分けて書き出す。

5. 反証を当てる。計画が「できる」としている step を 1 つずつ取り、できない具体物 (`path:line` / 実行結果 / 矛盾する正本の引用) を探す。**具体物の無い疑義は反証として採らない** — 「うまくいかない気がする」は差し戻しの根拠にならない。

6. 判定を書いて返す。「通す」「直してから通す」「差し戻す」の 3 分類にし、分類ごとに根拠となった具体物を添える。拮抗する設計判断は自分で確定せず、トレードオフを添えて依頼元へ返す (`principle-ai-delegation-boundary`: 停止理由・そこまでの成果・次に人が実行する手順を添える)。

7. PR 到達前にレビュー工程を通す。出力は PR を出した後にコメントで投稿する。条件: 計画 doc の修正や検証レポートを repo へ commit するとき。判定を返すだけで差分が出ないなら `skip: 差分なし`。
   - `swat-skills:review-stage` を Skill tool で invoke する
   - review-stage は commit 済みの差分だけを読むので、invoke の前に変更を commit する。発端の issue があれば `issue=<ref>` を args で渡す。出力の `再レビュー:` 行が `停止` で始まるときは、理由が `未 commit` で、`停止理由:` 行に挙がった file がすべてこの作業で入れた変更なら commit して同じ args で invoke し直す (この停止出力を `prev=` に渡すと初回へ落ちる)。それ以外の停止 (無関係な変更を含む `未 commit`・`reviewer 未起動`・`その他: <自由文>`) は、レビューを通らないまま先へ進まないよう user へ報告して止まる (spawn prompt から開いたときは最終返答に載せる)。`停止` で始まらなければ Act on を直して commit し、`再レビュー:` 行が `要` で始まるときだけ `swat-skills:review-stage` を invoke し直し、前巡の出力を保存した file を `prev=<file>` で渡す — 前の invoke の記憶から reviewer を直接立てると、invoke 先の手順が抜ける。`要` で始まらなければレビューはそこで終える
   - レビューを 1 回走らせるたびに、その出力を注記行まで含めて逐語で一時 file へ保存する — PR へ載せるのは PR を出した後なので、context に頼ると compaction で逐語が崩れる。置き場は作業ツリーの外 (中に置くと commit に混ざるか、untracked file が作業ツリーの削除を拒む)、名前はどのレビューの何巡目かが分かる形にし、投稿したら消す。最後に走らせた巡の後に修正したら、その修正を「レビュー後に修正 (未レビュー)」として最後の巡の file へ書き足す — その修正はレビューを通っていないので、通っていないことを人が読める形で残す
   - PR を出したら (既存の PR へ push する作業なら push したら)、保存した file を走らせた順に 1 回 1 コメントで**そのまま** PR へ投稿する (`gh pr comment <番号> --body-file <file>`。GitLab は `glab mr note create <番号> --resolvable=false < <file>` — 解決待ちの thread にすると merge を塞ぐ)。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる — 注記行が無いと、Act on のうちどれだけが反証を経たかを読み手が判別できない。PR 説明文はレビューを経た最終状態だけで書き、PR を初めて見た人が何を変えたかを読み取れるようにする — レビューの経過はコメントが持つ
   - PR 説明文の節構成は `swat-skills:pr` を Skill tool で invoke し、その template を土台に決める。template の節のうち、この CL に中身の無い節は省く。呼び出し元 (spawn prompt・workflow 定義) が求める節は template の節と並べて足し、中身が無くても見出しを残して「なし」と書く (後段の作業がその節を見出しで探しに来る)。説明文の節がそろったら PR を draft で出し (既にある PR へ push する作業ならその PR のまま)、ready にするのは作業が完了したときだけにする。完了は、説明文に未確定の値が無く、PR に載せる記録 (保存したレビュー出力と、PR へ写すときの skip 行) を投稿し終えた状態で、投稿の後に commit を足したならその修正も「レビュー後に修正 (未レビュー)」としてコメントで足してある。待ちの長い値 (計測や実行の完了) は、待っている値とそれを生む手段 (コマンド・job の参照先) を書いて未確定と明記したまま draft で待ち、確定して書き換えてから ready にする — draft で先に出しておけば、待つ間に呼び出し元が課す実行時間の上限に当たっても PR と記録が残る。待ちきれずに終えるときは draft のまま終え、未確定の値と手段を最終返答に載せる。GitHub は `gh pr create --draft` / `gh pr ready <番号>`、GitLab は `glab mr create --draft` / `glab mr update <番号> --ready`
