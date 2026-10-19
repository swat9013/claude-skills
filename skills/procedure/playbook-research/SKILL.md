---
name: playbook-research
description: 調査作業を、原則の索引 Read から成果物の受け渡しまでの番号付き step で通す playbook。
disable-model-invocation: true
---

# 調査の playbook

問いを立てて一次ソースに当たり、判断材料として渡すまでの手順。step の中でどの skill と原則を使うかを名前で置いてある。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-research` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step 1 に着手する前に、todolist tool で step を逐語 todolist へ写す。** 要約・統合・並べ替えをしない (変形した分だけ必須工程が silent に欠ける)。各項目の題には step 番号と先頭文を逐語で書き、step 本文を条件節・sub-bullet ごとそのまま添える。skip する step は題の先頭に `skip: <理由>` を付ける。todolist tool が tool 一覧に無いときは、写す先を最終返答に替え、冒頭に「todolist tool なし」と 1 行書く
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない。条件付き step で条件に当たらないときは、その条件を理由に書く。spawn prompt から Read で開いたとき (人が todolist を見ない) は、skip 行を最終返答 (PR 到達時はレビュー出力とは別の 1 コメントで PR) にも写す

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、索引の読み方に従って今回の作業に当たる leaf を読む。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。以降の step が `principle-*` を名指ししたときも、索引の読み方に従う。

2. 問いと完了条件を確定する。何が分かれば調査を終えてよいかを 1 文で書く。答えの形 (決定の材料 / 事実の確認 / 選択肢の比較) を先に決める — 形が決まらないまま集めた事実は、どれだけ集まっても終われない。

3. 一次ソースに当たる。公式 doc・実装コード・実測を根拠に採り、二次情報は一次ソースへ辿り直す。複数ソースの横断や最新性の確認 (バージョン・非推奨化・破壊的変更) は `swat-skills:web-research` subagent へ回す。単一 URL の単発 fetch は自分で取る。

4. 実測と推測を分けて記録する。断定には出典 (`path:line` / URL / 実行したコマンドと出力) を添え、出典が付かないものは推測として区別する。`principle-fail-loudly`: 到達できなかったソースは「到達できなかった」と書いて残す (黙って落とすと、調べていない範囲が調べた範囲に見える)。

5. 成果物を書く。`principle-artifact-register` で成果物の役割 (調査レポート / 決定の材料 / 蒸留版) を先に決めてから書式を決める — 役割ごとに規範は逆になる。置き場は対象 repo の doc 規約に従う。

6. 判断を依頼元へ返す。拮抗した選択肢は自分で確定せず、トレードオフを添えて返す。人手でしか解けない項目が残ったら `principle-ai-delegation-boundary` に従い、停止理由・そこまでの成果・次に人が実行する手順を添える (無言で終わらせない)。

7. PR 到達前にレビュー工程を通す。出力は PR を出した後にコメントで投稿する。条件: 調査結果を repo へ commit するとき。read-only で終わり差分が出ないなら `skip: 差分なし`。
   - `swat-skills:review-stage` を Skill tool で invoke する
   - review-stage は commit 済みの差分だけを読むので、invoke の前に変更を commit する。発端の issue があれば `issue=<ref>` を args で渡す。出力の `再レビュー:` 行が `停止` で始まるときは、理由が `未 commit` で、`停止理由:` 行に挙がった file がすべてこの作業で入れた変更なら commit して同じ args で invoke し直す (この停止出力を `prev=` に渡すと初回へ落ちる)。それ以外の停止 (無関係な変更を含む `未 commit`・`reviewer 未起動`・`その他: <自由文>`) は、レビューを通らないまま先へ進まないよう user へ報告して止まる (spawn prompt から開いたときは最終返答に載せる)。`停止` で始まらなければ Act on を直して commit し、`再レビュー:` 行が `要` で始まるときだけ `swat-skills:review-stage` を invoke し直し、前巡の出力を保存した file を `prev=<file>` で渡す — 前の invoke の記憶から reviewer を直接立てると、invoke 先の手順が抜ける。`要` で始まらなければレビューはそこで終える
   - レビューを 1 回走らせるたびに、その出力を注記行まで含めて逐語で一時 file へ保存する — PR へ載せるのは PR を出した後なので、context に頼ると compaction で逐語が崩れる。置き場は作業ツリーの外 (中に置くと commit に混ざるか、untracked file が作業ツリーの削除を拒む)、名前はどのレビューの何巡目かが分かる形にし、投稿したら消す。最後に走らせた巡の後に修正したら、その修正を「レビュー後に修正 (未レビュー)」として最後の巡の file へ書き足す — その修正はレビューを通っていないので、通っていないことを人が読める形で残す
   - PR を出したら (既存の PR へ push する作業なら push したら)、保存した file を走らせた順に 1 回 1 コメントで**そのまま** PR へ投稿する (`gh pr comment <番号> --body-file <file>`。GitLab は `glab mr note create <番号> --resolvable=false < <file>` — 解決待ちの thread にすると merge を塞ぐ)。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる — 注記行が無いと、Act on のうちどれだけが反証を経たかを読み手が判別できない。PR 説明文はレビューを経た最終状態だけで書き、PR を初めて見た人が何を変えたかを読み取れるようにする — レビューの経過はコメントが持つ
   - レビューで直さなかった指摘と、担当範囲の外で見つけた欠陥は、作業ツリーの repo の issue の規範 (`docs/agents/issue-tracker.md` など) に従って、起票するかを決めて起票する
   - PR 説明文の節構成は `swat-skills:pr` を Skill tool で invoke し、その template を土台に決める。template の節のうち、この CL に中身の無い節は省く。呼び出し元 (spawn prompt・workflow 定義) が求める節は template の節と並べて足し、中身が無くても見出しを残して「なし」と書く (後段の作業がその節を見出しで探しに来る)。説明文の節がそろったら PR を draft で出し (既にある PR へ push する作業ならその PR のまま)、ready にするのは作業が完了したときだけにする。完了は、説明文に未確定の値が無く、PR に載せる記録 (保存したレビュー出力と、PR へ写すときの skip 行) を投稿し終えた状態で、投稿の後に commit を足したならその修正も「レビュー後に修正 (未レビュー)」としてコメントで足してある。待ちの長い値 (計測や実行の完了) は、待っている値とそれを生む手段 (コマンド・job の参照先) を書いて未確定と明記したまま draft で待ち、確定して書き換えてから ready にする — draft で先に出しておけば、待つ間に呼び出し元が課す実行時間の上限に当たっても PR と記録が残る。待ちきれずに終えるときは draft のまま終え、未確定の値と手段を最終返答に載せる。GitHub は `gh pr create --draft` / `gh pr ready <番号>`、GitLab は `glab mr create --draft` / `glab mr update <番号> --ready`
