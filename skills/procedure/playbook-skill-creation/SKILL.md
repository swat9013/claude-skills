---
name: playbook-skill-creation
description: 新規 skill 作成を、skill 化の要否判定からレビュー工程の通過までの番号付き step で通す playbook。
disable-model-invocation: true
metadata:
  deliverable: cl
  dispatch-when: skill を新規に作る issue だけ (既存 skill の改修は、description や本文の手直しを含めて対象外)。
---

# skill 作成の playbook

新規 skill を作って PR へ出すまでの手順。step の中でどの skill と原則を使うかを名前で置いてある。既存 skill の改修は対象外で、呼び出し元のフローに委ねる。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-skill-creation` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step 1 に着手する前に、todolist tool で step を逐語 todolist へ写す。** 要約・統合・並べ替えをしない (変形した分だけ必須工程が silent に欠ける)。各項目の題には step 番号と先頭文を逐語で書き、step 本文を条件節・sub-bullet ごとそのまま添える。skip する step は題の先頭に `skip: <理由>` を付ける。todolist tool が tool 一覧に無いときは、写す先を最終返答に替え、冒頭に「todolist tool なし」と 1 行書く
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない。条件付き step で条件に当たらないときは、その条件を理由に書く。spawn prompt から Read で開いたとき (人が todolist を見ない) は、skip 行を最終返答 (PR 到達時はレビュー出力とは別の 1 コメントで PR) にも写す

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、索引の読み方に従って今回の作業に当たる leaf を読む。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。以降の step が `principle-*` を名指ししたときも、索引の読み方に従う。

2. skill 化の要否を判定する。`principle-automate-when-it-hurts` (自動化は痛みの実測後) で、その手順・知識が反復して要ることを依頼の根拠から確かめる。1 回限りのタスク、source や `--help` を読めば分かる情報は skill 化せず、その判断を理由付きで依頼元へ返し、残りの step に `skip: skill 化しない` を付けて終える。作る場合は、導入済み skill の一覧から同じ責務・同じ trigger 語彙の skill が既に無いことを確かめる — 既にあるなら新設ではなくその skill の改修を提案する。

3. 責務と境界を確定する。この skill が「何を書くか / 何をするか」を 1 文で書き、対象外 (隣接するが扱わない作業) を添えて書き留める — description と本文冒頭でそのまま使う。1 文に収まらないなら責務が複数混ざっているので、skill を分ける。

4. 配置と invocation を決める。配置 (plugin 配布 / project-local / global) と登録先は対象 repo の skill 規約 (CLAUDE.md / rules / docs) に従い、規約が無ければ project-local (`.claude/skills/`) に置く。invocation は用途で選ぶ: model にも自動起動させる (user + model 両用) / 他 skill・spawn prompt からだけ呼ぶ (model 専用) / 人が名指しでだけ呼ぶ (user 専用)。`principle-context-budget`: model が読む description は全セッションの context に常時載るので、自動起動が要らないなら user 専用にして context 負担を 0 にする。

5. 文面を書く。`swat-skills:write-for-harness` を Skill tool で args `新規: skill <目的>` を添えて invoke する。frontmatter・description の書式・本文の文面規範の Read から、検証 subagent と構造 reviewer の指摘反映までを同 skill の手順が持つ — 本 playbook 側で文面規範を重複して当てない。step 3 の責務 1 文と step 4 の invocation を入力として渡す。

6. 起動導線で受け入れ確認する。`principle-verify-the-real-artifact`: file が置けたことではなく、step 4 で決めた入口 (Skill tool invoke / `/` メニュー / description trigger) から本文が実際に届くことを確かめる。新規 skill は起動中のセッションの一覧に載らないので、セッション再起動を挟む確認は残作業として依頼元へ明示する。対象 repo に skill の機械検証 (gate / lint) があれば、その規約に従い実行して pass させる。

7. PR 到達前にレビュー工程を通す。出力は PR を出した後にコメントで投稿する。条件: skill を repo へ commit するとき。
   - `swat-skills:review-stage` を Skill tool で invoke する
   - review-stage は commit 済みの差分だけを読むので、invoke の前に変更を commit する。発端の issue があれば `issue=<ref>` を args で渡す。出力の `再レビュー:` 行が `停止` で始まるときは、理由が `未 commit` で、`停止理由:` 行に挙がった file がすべてこの作業で入れた変更なら commit して同じ args で invoke し直す (この停止出力を `prev=` に渡すと初回へ落ちる)。それ以外の停止 (無関係な変更を含む `未 commit`・`reviewer 未起動`・`その他: <自由文>`) は、レビューを通らないまま先へ進まないよう user へ報告して止まる (spawn prompt から開いたときは最終返答に載せる)。`停止` で始まらなければ Act on を直して commit し、`再レビュー:` 行が `要` で始まるときだけ `swat-skills:review-stage` を invoke し直し、前巡の出力を保存した file を `prev=<file>` で渡す — 前の invoke の記憶から reviewer を直接立てると、invoke 先の手順が抜ける。`要` で始まらなければレビューはそこで終える
   - レビューを 1 回走らせるたびに、その出力を注記行まで含めて逐語で一時 file へ保存する — PR へ載せるのは PR を出した後なので、context に頼ると compaction で逐語が崩れる。置き場は作業ツリーの外 (中に置くと commit に混ざるか、untracked file が作業ツリーの削除を拒む)、名前はどのレビューの何巡目かが分かる形にし、投稿したら消す。最後に走らせた巡の後に修正したら、その修正を「レビュー後に修正 (未レビュー)」として最後の巡の file へ書き足す — その修正はレビューを通っていないので、通っていないことを人が読める形で残す
   - PR を出したら (既存の PR へ push する作業なら push したら)、保存した file を走らせた順に 1 回 1 コメントで**そのまま** PR へ投稿する (`gh pr comment <番号> --body-file <file>`。GitLab は `glab mr note create <番号> --resolvable=false < <file>` — 解決待ちの thread にすると merge を塞ぐ)。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる — 注記行が無いと、Act on のうちどれだけが反証を経たかを読み手が判別できない。PR 説明文はレビューを経た最終状態だけで書き、PR を初めて見た人が何を変えたかを読み取れるようにする — レビューの経過はコメントが持つ
   - PR 説明文の節構成は `swat-skills:pr` を Skill tool で invoke し、その template を土台に決める。template の節のうち、この CL に中身の無い節は省く。呼び出し元 (spawn prompt・workflow 定義) が求める節は template の節と並べて足し、中身が無くても見出しを残して「なし」と書く (後段の作業がその節を見出しで探しに来る)。説明文の節がそろったら PR を draft で出し (既にある PR へ push する作業ならその PR のまま)、ready にするのは作業が完了したときだけにする。完了は、説明文に未確定の値が無く、PR に載せる記録 (保存したレビュー出力と、PR へ写すときの skip 行) を投稿し終えた状態で、投稿の後に commit を足したならその修正も「レビュー後に修正 (未レビュー)」としてコメントで足してある。待ちの長い値 (計測や実行の完了) は、待っている値とそれを生む手段 (コマンド・job の参照先) を書いて未確定と明記したまま draft で待ち、確定して書き換えてから ready にする — draft で先に出しておけば、待つ間に呼び出し元が課す実行時間の上限に当たっても PR と記録が残る。待ちきれずに終えるときは draft のまま終え、未確定の値と手段を最終返答に載せる。GitHub は `gh pr create --draft` / `gh pr ready <番号>`、GitLab は `glab mr create --draft` / `glab mr update <番号> --ready`
