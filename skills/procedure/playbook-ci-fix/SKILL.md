---
name: playbook-ci-fix
description: 既存 CL の CI 失敗修正を、原則の索引 Read から既存 branch への push までの番号付き step で通す playbook。
disable-model-invocation: true
---

# CI 修正の playbook

既存 CL の head branch 上で、失敗した check の原因を直して push するまでの手順。対象 CL の番号・URL・head / base branch は呼び出し元 (spawn prompt / 依頼文) から受け取る。新規 CL は作らず、既存 branch へ push する。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-ci-fix` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step 1 に着手する前に、todolist tool で step を逐語 todolist へ写す。** 要約・統合・並べ替えをしない (変形した分だけ必須工程が silent に欠ける)。各項目の題には step 番号と先頭文を逐語で書き、step 本文を条件節・sub-bullet ごとそのまま添える。skip する step は題の先頭に `skip: <理由>` を付ける。todolist tool が tool 一覧に無いときは、写す先を最終返答に替え、冒頭に「todolist tool なし」と 1 行書く
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない。条件付き step で条件に当たらないときは、その条件を理由に書く。spawn prompt から Read で開いたとき (人が todolist を見ない) は、skip 行を最終返答 (PR 到達時はレビュー出力とは別の 1 コメントで PR) にも写す
- playbook を複数渡されたときは、渡された順に本文を Read して todolist を連結する (並び順は渡す側の契約が決める)

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、索引の読み方に従って今回の作業に当たる leaf を読む。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。以降の step が `principle-*` を名指ししたときも、索引の読み方に従う。

2. 失敗した check を読む。`gh pr checks <CL 番号> -R <CL repo> --json name,state,link` で失敗した check を列挙する (exit code は読まない — 失敗があると 1 で終わるのが正常)。GitHub Actions なら `gh run view <run-id> -R <CL repo> --log-failed` で失敗 log を読む。

3. 失敗を作業ツリーで再現する。手元で再現できない失敗は推測で push せず、続行不能として呼び出し元のフローで人へ返す。原因が自分の変更の外にある失敗 (base branch でも落ちる / 外部サービス起因) も同じく人へ返す。

4. 原因を直す。手元で失敗が通ることを確かめてから commit する — テストを消して通すのは修正ではない。

5. PR 到達前にレビュー工程を通す。出力は PR を出した後にコメントで投稿する。既存 CL では push が PR 到達に当たり、対象差分は原因を直した修正差分に絞る (`swat-skills:review-stage` の args に `<修正前の head>..HEAD` を渡す。渡さないと起点 branch からの CL 全体を見る)。
   - `swat-skills:review-stage` を Skill tool で invoke する
   - review-stage は commit 済みの差分だけを読むので、invoke の前に変更を commit する。発端の issue があれば `issue=<ref>` を args で渡す。出力の `再レビュー:` 行が `停止` で始まるときは、理由が `未 commit` で、`停止理由:` 行に挙がった file がすべてこの作業で入れた変更なら commit して同じ args で invoke し直す (この停止出力を `prev=` に渡すと初回へ落ちる)。それ以外の停止 (無関係な変更を含む `未 commit`・`reviewer 未起動`・`その他: <自由文>`) は、レビューを通らないまま先へ進まないよう user へ報告して止まる (spawn prompt から開いたときは最終返答に載せる)。`停止` で始まらなければ Act on を直して commit し、`再レビュー:` 行が `要` で始まるときだけ `swat-skills:review-stage` を invoke し直し、前巡の出力を保存した file を `prev=<file>` で渡す — 前の invoke の記憶から reviewer を直接立てると、invoke 先の手順が抜ける。`要` で始まらなければレビューはそこで終える
   - レビューを 1 回走らせるたびに、その出力を注記行まで含めて逐語で一時 file へ保存する — PR へ載せるのは PR を出した後なので、context に頼ると compaction で逐語が崩れる。置き場は作業ツリーの外 (中に置くと commit に混ざるか、untracked file が作業ツリーの削除を拒む)、名前はどのレビューの何巡目かが分かる形にし、投稿したら消す。最後に走らせた巡の後に修正したら、その修正を「レビュー後に修正 (未レビュー)」として最後の巡の file へ書き足す — その修正はレビューを通っていないので、通っていないことを人が読める形で残す
   - PR を出したら (既存の PR へ push する作業なら push したら)、保存した file を走らせた順に 1 回 1 コメントで**そのまま** PR へ投稿する (`gh pr comment <番号> --body-file <file>`。GitLab は `glab mr note create <番号> --resolvable=false < <file>` — 解決待ちの thread にすると merge を塞ぐ)。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる — 注記行が無いと、Act on のうちどれだけが反証を経たかを読み手が判別できない。PR 説明文はレビューを経た最終状態だけで書き、PR を初めて見た人が何を変えたかを読み取れるようにする — レビューの経過はコメントが持つ
   - レビューで直さなかった指摘と、担当範囲の外で見つけた欠陥は、作業ツリーの repo の issue の規範 (`docs/agents/issue-tracker.md` など) に従って、起票するかを決めて起票する
   - 載せ先は新規 PR ではなく既存 CL。説明文は今回の変更で最終状態が変わった節だけを書き換える。template の節の構成は `swat-skills:pr` を Skill tool で invoke して決め、template に無い節 (呼び出し元か人が足した節) は既存の見出しと書き方を保ったまま中身を書き換える — 後段の作業や読み手がその節を見出しで探しに来るので消さない。呼び出し元が求める節がまだ無く、今回の変更でその中身が生まれたら足す

6. 既存 branch へ push し、step 5 で保存したレビュー出力を step 5 の bullet どおり CL へ投稿する。原因を直した commit を push した時点で解消扱いとし、**push 後の check の結果は待たない** — 合否は呼び出し元の観測 (claude-dispatcher なら次 tick) が持ち、再失敗はそちらが拾う。
