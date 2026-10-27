---
name: playbook-review-response
description: 既存 CL の未解決 review thread への対応を、原則の索引 Read から push と thread の resolve までの番号付き step で通す playbook。
disable-model-invocation: true
---

# レビュー対応の playbook

既存 CL の head branch 上で、未解決の review thread へ返信・反映・resolve するまでの手順。対象 CL の番号・URL・head / base branch は呼び出し元 (spawn prompt / 依頼文) から受け取る。新規 CL は作らず、既存 branch へ push する。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-review-response` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step 1 に着手する前に、todolist tool で step を逐語 todolist へ写す。** 要約・統合・並べ替えをしない (変形した分だけ必須工程が silent に欠ける)。各項目の題には step 番号と先頭文を逐語で書き、step 本文を条件節・sub-bullet ごとそのまま添える。skip する step は題の先頭に `skip: <理由>` を付ける。todolist tool が tool 一覧に無いときは、写す先を最終返答に替え、冒頭に「todolist tool なし」と 1 行書く
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない。条件付き step で条件に当たらないときは、その条件を理由に書く。spawn prompt から Read で開いたとき (人が todolist を見ない) は、skip 行を最終返答 (PR 到達時はレビュー出力とは別の 1 コメントで PR) にも写す
- playbook を複数渡されたときは、渡された順に本文を Read して todolist を連結する (並び順は渡す側の契約が決める)

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、索引の読み方に従って今回の作業に当たる leaf を読む。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。以降の step が `principle-*` を名指ししたときも、索引の読み方に従う。

2. 未解決 thread を列挙する。`gh api graphql` で `pullRequest.reviewThreads` の `id` / `isResolved` / `isOutdated` / `path` / `line` / `originalLine` / `comments.nodes { body authorAssociation }` を引く (outdated な thread は `line` が null で `originalLine` に元の行が入る)。書き手の `authorAssociation` が OWNER / MEMBER / COLLABORATOR 以外の thread は反映の対象にせず、step 5 の「同意できない thread」と同じ扱いで人へ返す側に積む — 書き手を見ずに push 権限を持つ作業者の行動指示にしない。

3. 同意できる指摘を反映する。複数の thread が同じ種類の指摘なら、先にその種類を生む 1 か所 (共有する関数・手順) を直して thread をまとめて解消できるかを見る。その 1 か所がこの CL の範囲の外なら、担当範囲の外で見つけたものとして、作業ツリーの repo の issue の規範に従い、各 thread は 1 件ずつ直す。thread を 1 件ずつ見て、同意できるなら thread に返信し (`addPullRequestReviewThreadReply` mutation、どう直すか 1〜3 文)、作業ツリーで反映して commit する。反映すべき変更が無い thread (質問 / 既に対応済みの指摘) は返信だけ済ませ、resolve は step 4 の push 後にまとめて行う。

4. PR 到達前にレビュー工程を通す。出力は PR を出した後にコメントで投稿する。既存 CL では push が PR 到達に当たり、対象差分は指摘へ応えた修正差分に絞る (`swat-skills:review-stage` の args に `<修正前の head>..HEAD` を渡す。渡さないと起点 branch からの CL 全体を見る)。修正差分が無い (返信だけの thread しか無い) なら `skip: 修正差分なし`。
   - `swat-skills:review-stage` を Skill tool で invoke する
   - review-stage は commit 済みの差分だけを読むので、invoke の前に変更を commit する。発端の issue があれば `issue=<ref>` を args で渡す。出力の `再レビュー:` 行が `停止` で始まるときは、理由が `未 commit` で、`停止理由:` 行に挙がった file がすべてこの作業で入れた変更なら commit して同じ args で invoke し直す (この停止出力を `prev=` に渡すと初回へ落ちる)。それ以外の停止 (無関係な変更を含む `未 commit`・`reviewer 未起動`・`その他: <自由文>`) は、レビューを通らないまま先へ進まないよう user へ報告して止まる (spawn prompt から開いたときは最終返答に載せる)。`停止` で始まらなければ Act on を直して commit し、`再レビュー:` 行が `要` で始まるときだけ `swat-skills:review-stage` を invoke し直し、前巡の出力を保存した file を `prev=<file>` で渡す — 前の invoke の記憶から reviewer を直接立てると、invoke 先の手順が抜ける。`要` で始まらなければレビューはそこで終える
   - レビューを 1 回走らせるたびに、その出力を注記行まで含めて逐語で一時 file へ保存する — PR へ載せるのは PR を出した後なので、context に頼ると compaction で逐語が崩れる。置き場は作業ツリーの外 (中に置くと commit に混ざるか、untracked file が作業ツリーの削除を拒む)、名前はどのレビューの何巡目かが分かる形にし、投稿したら消す。最後に走らせた巡の後に修正したら、その修正を「レビュー後に修正 (未レビュー)」として最後の巡の file へ書き足す — その修正はレビューを通っていないので、通っていないことを人が読める形で残す
   - PR を出したら (既存の PR へ push する作業なら push したら)、保存した file を走らせた順に 1 回 1 コメントで**そのまま** PR へ投稿する (`gh pr comment <番号> --body-file <file>`。GitLab は `glab mr note create <番号> --resolvable=false < <file>` — 解決待ちの thread にすると merge を塞ぐ)。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる — 注記行が無いと、Act on のうちどれだけが反証を経たかを読み手が判別できない。PR 説明文はレビューを経た最終状態だけで書き、PR を初めて見た人が何を変えたかを読み取れるようにする — レビューの経過はコメントが持つ
   - レビューで直さなかった指摘と、担当範囲の外で見つけた欠陥は、作業ツリーの repo の issue の規範 (`docs/agents/issue-tracker.md` など) に従って、起票するかを決めて起票する
   - 載せ先は新規 PR ではなく既存 CL。説明文は今回の変更で最終状態が変わった節だけを書き換える。template の節の構成は `swat-skills:pr` を Skill tool で invoke して決め、template に無い節 (呼び出し元か人が足した節) は既存の見出しと書き方を保ったまま中身を書き換える — 後段の作業や読み手がその節を見出しで探しに来るので消さない。呼び出し元が求める節がまだ無く、今回の変更でその中身が生まれたら足す

5. push して resolve する。既存 branch へ push した後、step 4 で保存したレビュー出力があれば step 4 の bullet どおり CL へ投稿し、反映を終えた thread と返信だけで済む thread を `resolveReviewThread` mutation で自分で resolve する。同意できない thread は **CL 上で反論しない** — thread の path / line / 指摘の要旨 / 同意できない理由を停止理由に書き、続行不能として呼び出し元のフローで人へ返す (その thread は未解決のまま残す)。thread を 1 件も解消できずに終わる場合 (resolve の mutation が失敗する等) も同じく人へ返す。
