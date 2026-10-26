---
name: playbook-diagnosis
description: bug の診断を、原則の索引 Read から根本原因の記録・返却までの番号付き step で通す playbook。
disable-model-invocation: true
---

# 診断の playbook

bug の根本原因を特定し、修正の判断材料として issue に残すまでの手順。step の中でどの原則を使うかを名前で置いてある。修正・PR は担当外。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-diagnosis` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step 1 に着手する前に、todolist tool で step を逐語 todolist へ写す。** 要約・統合・並べ替えをしない (変形した分だけ必須工程が silent に欠ける)。各項目の題には step 番号と先頭文を逐語で書き、step 本文を条件節・sub-bullet ごとそのまま添える。skip する step は題の先頭に `skip: <理由>` を付ける。todolist tool が tool 一覧に無いときは、写す先を最終返答に替え、冒頭に「todolist tool なし」と 1 行書く
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない。条件付き step で条件に当たらないときは、その条件を理由に書く。spawn prompt から Read で開いたとき (人が todolist を見ない) は、skip 行を最終返答 (PR 到達時はレビュー出力とは別の 1 コメントで PR) にも写す

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、索引の読み方に従って今回の作業に当たる leaf を読む。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。以降の step が `principle-*` を名指ししたときも、索引の読み方に従う。

2. 症状を確定する。依頼の本文 (issue 等) を逐語で読み、期待した挙動と実際の挙動を 1 文ずつで言明する。完了条件は「根本原因の言明が証拠付きで issue に記録され、修正の判断が依頼元へ返っていること」— 修正コードを書き始めたら本 playbook の外に出ている。

3. 再現ループを作る。**仮説を立てる前に**、このバグで必ず失敗する (red) 検証 1 コマンドを作る。red なコマンドを 1 度も実行しないまま code を読んで理論を組み始めたら、この step へ戻る。
   - 手段は次の優先順で当てる: 失敗テスト / curl / fixture 付き CLI / headless browser / trace replay / 使い捨て harness / property loop / bisect harness / differential 実行。credential は env var 経由で受ける形で組み、コマンドや出力に直書きしない
   - 作れたら tight にする: 秒で終わり、毎回同じ判定を返し、症状そのものを assert する (「crash しなかった」では red にならない)
   - 非決定バグは clean な再現でなく**再現率を上げる**: trigger を 100 回 loop・並列化・timing を狭める
   - ループが作れないなら `principle-fail-loudly` — 試した手段を列挙して issue へ記録し、再現に必要なもの (環境アクセス / redacted な captured artifact / 一時計測の許可) を明示して停止する

4. 再現を最小化する。ループを red のまま、入力・呼び出し元・設定・データを 1 つずつ削っては再実行し、**残る要素すべてが load-bearing** (どれを外しても green に変わる) になるまで縮める。最小化した分だけ step 5 の仮説空間が狭まる。

5. 仮説を 3〜5 本立てて記録する。1 本目で止めると最初の思いつきに anchor するので、必ず複数をランク付けする。各仮説は反証可能な予測を持つ:「<X> が原因なら、<Y> を変えるとバグが消える / <Z> を変えると悪化する」。予測が書けない仮説は捨てるか研ぎ直す。**ランク付きの一覧を issue へコメントしてから検証に進む** — 人の返答は待たないが、検証過程を後から監査できる形で先に残す。

6. 計測して反証する。probe は step 5 の予測 1 つに対応させ、**変える変数は一度に 1 つ**。debugger / REPL が使える環境ならそれを優先し、使えないときだけ仮説を切り分ける境界に絞ったログを足す — ログには一意な接頭辞 (例: `[DEBUG-a4f2]`) を必ず付け、後始末を grep 1 回にする。性能バグはログでなく計測 — baseline を測ってから bisect する。仮説が確証されるまで step 5 の一覧へ戻り、`principle-root-cause-and-hierarchy-of-controls` で「症状を消せる箇所」でなく「原因の連鎖の根」まで辿れているかを確かめる。

7. 診断結果を issue へ記録する。次を 1 コメントにまとめる: 根本原因の言明 (証拠 `path:line` 付き) / 再現 1 コマンド (実行した invocation と output を逐語で。修正側が TDD の red にそのまま使える形) / 反証済み仮説とその根拠 / 回帰テストの設計案 (どの seam にどんなテストを置くべきか。**バグの実パターンを通せる seam が無いなら、無いこと自体を発見として書く**)。red なテストや再現 script は commit せず、コメント内の逐語で渡す。貼る output は秘匿情報を `<REDACTED>` に置換し、信号を運ぶ行だけを引用する。

8. 後始末する。step 6 の接頭辞を grep して計測用の一時変更をすべて除去し、作業ツリーの diff が空であることを確かめる。

9. PR 到達前にレビュー工程を通す。出力は PR を出した後にコメントで投稿する。条件: 診断の成果 (再現 harness 等) を例外的に repo へ commit するとき。issue コメントだけで終わり差分が出ないなら `skip: 差分なし`。
   - `swat-skills:review-stage` を Skill tool で invoke する
   - review-stage は commit 済みの差分だけを読むので、invoke の前に変更を commit する。発端の issue があれば `issue=<ref>` を args で渡す。出力の `再レビュー:` 行が `停止` で始まるときは、理由が `未 commit` で、`停止理由:` 行に挙がった file がすべてこの作業で入れた変更なら commit して同じ args で invoke し直す (この停止出力を `prev=` に渡すと初回へ落ちる)。それ以外の停止 (無関係な変更を含む `未 commit`・`reviewer 未起動`・`その他: <自由文>`) は、レビューを通らないまま先へ進まないよう user へ報告して止まる (spawn prompt から開いたときは最終返答に載せる)。`停止` で始まらなければ Act on を直して commit し、`再レビュー:` 行が `要` で始まるときだけ `swat-skills:review-stage` を invoke し直し、前巡の出力を保存した file を `prev=<file>` で渡す — 前の invoke の記憶から reviewer を直接立てると、invoke 先の手順が抜ける。`要` で始まらなければレビューはそこで終える
   - レビューを 1 回走らせるたびに、その出力を注記行まで含めて逐語で一時 file へ保存する — PR へ載せるのは PR を出した後なので、context に頼ると compaction で逐語が崩れる。置き場は作業ツリーの外 (中に置くと commit に混ざるか、untracked file が作業ツリーの削除を拒む)、名前はどのレビューの何巡目かが分かる形にし、投稿したら消す。最後に走らせた巡の後に修正したら、その修正を「レビュー後に修正 (未レビュー)」として最後の巡の file へ書き足す — その修正はレビューを通っていないので、通っていないことを人が読める形で残す
   - PR を出したら (既存の PR へ push する作業なら push したら)、保存した file を走らせた順に 1 回 1 コメントで**そのまま** PR へ投稿する (`gh pr comment <番号> --body-file <file>`。GitLab は `glab mr note create <番号> --resolvable=false < <file>` — 解決待ちの thread にすると merge を塞ぐ)。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる — 注記行が無いと、Act on のうちどれだけが反証を経たかを読み手が判別できない。PR 説明文はレビューを経た最終状態だけで書き、PR を初めて見た人が何を変えたかを読み取れるようにする — レビューの経過はコメントが持つ
   - レビューで直さなかった指摘と、担当範囲の外で見つけた欠陥は、作業ツリーの repo の issue の規範 (`docs/agents/issue-tracker.md` など) に従って、起票するかを決めて起票する
   - PR 説明文の節構成は `swat-skills:pr` を Skill tool で invoke し、その template を土台に決める。template の節のうち、この CL に中身の無い節は省く。呼び出し元 (spawn prompt・workflow 定義) が求める節は template の節と並べて足し、中身が無くても見出しを残して「なし」と書く (後段の作業がその節を見出しで探しに来る)。説明文の節がそろったら PR を draft で出し (既にある PR へ push する作業ならその PR のまま)、ready にするのは作業が完了したときだけにする。完了は、説明文に未確定の値が無く、PR に載せる記録 (保存したレビュー出力と、PR へ写すときの skip 行) を投稿し終えた状態で、投稿の後に commit を足したならその修正も「レビュー後に修正 (未レビュー)」としてコメントで足してある。待ちの長い値 (計測や実行の完了) は、待っている値とそれを生む手段 (コマンド・job の参照先) を書いて未確定と明記したまま draft で待ち、確定して書き換えてから ready にする — draft で先に出しておけば、待つ間に呼び出し元が課す実行時間の上限に当たっても PR と記録が残る。待ちきれずに終えるときは draft のまま終え、未確定の値と手段を最終返答に載せる。GitHub は `gh pr create --draft` / `gh pr ready <番号>`、GitLab は `glab mr create --draft` / `glab mr update <番号> --ready`
   - issue を閉じる参照は書かず、`Refs #<番号>` で参照する — 修正は担当外で、issue は修正側の CL が閉じる

10. 修正の判断を返す。修正に着手するかの判断は依頼元のフロー (人 / triage) へ返す — 着手可否・優先度・label 操作は実行 project の運用に従う。
