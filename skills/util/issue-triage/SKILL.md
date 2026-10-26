---
name: issue-triage
description: issue を 1 件以上受けて AFK 境界で振り分け、境界の内側 (ready-for-agent・close・wontfix) は tracker へ反映し、外側の判定 (規範の文案・深掘り・人手の作業・報告者への質問) と止まったものを呼び出し元へ返す. Use when「issue を着手可 (`ready-for-agent`)・close・人待ち (`ready-for-human`) へ振り分けて tracker に反映する」「triage 待ち (`needs-triage`) の issue を処理する」.
user-invocable: true
---

# issue-triage

issue を N 件 (1 件以上) 受け、AFK 境界 (agent が人に問わず自分で決めてよい判断と、人に返す判断の境目) に従って振り分ける。境界の内側と判定したものは承認を待たずに tracker へ反映し、外側の判定と止まったものは呼び出し元へ返す — 内側の判断は人が事後に覆せるので、承認で止めずに進めて、人が見る判断を外側に絞る。repo のファイルは変更しない。人へどう届けるか (label で待たせるか、その場で問うか) は呼び出し元が決める。

args は issue 置き場 (省けば下の文書の宣言) と、issue ごとの `#<N>` の見出し。見出しの下に本文の書き換え案が逐語で添えられていたら、tracker 上の本文の代わりにそれを振り分けの対象にする。書き換え案には、案の元にした本文を保存した file の path (基準) も添えてもらう — 基準が無いと、案を作った後に入った他の書き手の編集を検知できない。基準の無い書き換え案は振り分けには使うが、tracker へは書き戻さずに返す。

置き場と CLI 規約・label 語彙は対象 repo の `docs/agents/issue-tracker.md` / `docs/agents/triage-labels.md` を Read して決める。CL 置き場は issue の変更が入る repo (cwd の repo) で、issue 置き場と別のことがある。宣言を読み取れなければ cwd の repo を両方の置き場とする。コマンドには置き場を `-R <issue 置き場>` (PR は `-R <CL 置き場>`) で明示する — cwd に任せると、別 repo の同じ番号を読み書きする。本文は label を `needs-triage`・`needs-info`・`needs-grilling`・`ready-for-agent`・`ready-for-human`・`wontfix` の名前で書き、`triage-labels.md` が別の綴りを宣言していればその綴りで付け外しする (コマンドに書いた label と、label で絞る検索も同じ)。外の仕組みが issue に付ける印 (作業中の印など) は区分にも反映の条件にも使わず、付け外しもしない — 外の仕組みの作業と triage を重ねないのは、issue を渡す前の呼び出し元の役割で、閉じた issue に印が残るのも意図した振る舞いになる。置き場や label を宣言から読めなかったときは、その前提を返す内容に書く。コマンド例は GitHub (`gh`) の綴りで、他の tracker なら読み替える。

## 1. 読む

- 原則 leaf `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/references/leaves/principle-ai-delegation-boundary.md` を Read する — AFK 境界の中身 (人に返す判断の種類) の正本。要るのはこの leaf 1 本なので、索引は引かない
- issue ごとに `gh issue view <N> -R <issue 置き場> --json title,body,labels,comments,closedByPullRequestsReferences` で本文と全コメントを、各コメントの中身を末尾まで省かずに読む (`--comments` は非 TTY で本文が抜ける。件数や先頭の数文字に縮めると、§2 の過去の triage のコメントの区分と、その後の新しい情報の有無を決められない)。1 回で引ける `gh issue list` は番号で絞れず、受けた N 件のために使うと読まない issue の本文まで context に載る。本文は書き換えうるので、`${CLAUDE_SKILL_DIR}/references/body-rewrite.md` を Read し、その「基準の保存」に従う (一時 file は呼び出し元が置き場を決めていなければ `mktemp` で作業ツリーの外に作る)。呼び出し元が書き換え案と基準を添えた issue は、添えられた基準を使う
- issue を実装している open CL を控える。候補は、上で読んだ `closedByPullRequestsReferences` の各 CL (要素の url で `gh pr view <url> --json state` を撃ち、open のものだけを残す) と、`gh pr list -R <CL 置き場> --state open --search "<N> in:body" --json number,body` のうち本文がこの issue を参照する CL (CL 置き場と issue 置き場が同じなら `#<N>`、違えば `<owner>/<repo>#<N>` か issue の URL。どちらも番号の直後が数字でないもの) — open CL を全件引くと、1 件の issue のために repo の CL を全部読むことになる
- open issue の題名を `gh issue list -R <issue 置き場> --state open --limit 1000 --json number,title` で 1 回だけ引いて file に受け (件数が `--limit` に届いたら上げて引き直す)、close の確かめと §5 の follow-up issue の重複探しで使い回す
- close の 3 種 (重複・既に直っている・前提が変わった) は、この題名の一覧と、`git fetch` 後の `origin/<既定 branch>` で本文が名指す file・識別子を `git show` / `git grep` した結果の 2 つだけで確かめる。どちらにも当たらなければ、3 種のどれでもないとみなす — 決定記録や過去 PR を広く読みに行くと、1 件の triage の費用が上限なく増える (この限りは close の確かめに当てる。close 以外の区分の判定では、§2 が定義する規範 (決定記録を含む) をこの限りに縛らずに読む。決定記録は対象 repo の `docs/agents/domain.md` が案内する置き場から引き、案内が無ければ読まない — 規約の path や既定の名前で探すと、置き場を宣言で入れ替えた repo では別の場所を読む)。cwd は人の checkout でありうるので HEAD は動かさない
- `wontfix` は、本文の中身を `docs/agents/issue-tracker.md` の「follow-up issue の起票」節の「単位の外」の箇条と、その節に repo が足した補足に照らして決め、節の外は読みに行かない — 補足を外すと、起票する側だけが補足を当て、閉じる判定と起票の判定の基準が割れる
- 同じ session で呼ばれ直したとき、この skill の手順で Read する plugin 内の file (`${CLAUDE_SKILL_DIR}` から辿る leaf と `references/`) は、前の実行で読んだ結果が context に見えていれば読み直さない (compaction で要約されていれば読み直す) — plugin 内の file は実行の間で変わらない。対象 repo の宣言 (`docs/agents/*.md`)・tracker の状態 (本文・コメント・label・open CL・題名の一覧)・`git fetch` は実行ごとに引き直す — 前の実行の後に、人や呼び出し元が cwd を更新し、issue を起票し、label を付け替え、CL を出しうるので、前の結果を使うと古い状態で振り分ける。基準は `body-rewrite.md` の「基準の保存」に従って実行ごとに決める。前の実行で会話へ返した文面も持ち越さない — §6 のブロックには同じ文面でも逐語で書き直し、§6 の「既にある」コメントにも数えない (呼び出し元が投稿するのは今回返したブロックの文面で、会話へ返しただけの文面は tracker に無い)

## 2. 振り分ける

issue ごとに 1 つの区分に決める。規範とは対象 repo の CLAUDE.md・rules・決定記録・原則 leaf・docs を指す。

| 区分 | 当てはまるもの | 境界 |
|---|---|---|
| 1 | 判断が要らない、または既存の規範で決まる | 内側 |
| 3 | 規範はまだ決めていないが、判断がこの issue の中で閉じる。自分で決め、決めたことと退けた案を本文に書く | 内側 |
| close | 重複している・既に直っている・前提が変わった | 内側 (reopen で戻せる) |
| `wontfix` | 対象 repo の `docs/agents/issue-tracker.md`「follow-up issue の起票」節が起票しないとした種類 (欠陥を伴わない改善など) に当たる。起票元のレビューが付けた区分ではなく、本文の中身で決める | 内側 (reopen で戻せる) |
| 2 | 判断がこの issue の外へ一般化する (同じ種類の判断が次の issue でも来る) のに、決めるための一般則が規範に無い。一般則を決めることは規範を作ることなので、人に返す | 外側 |
| 4 (判断) | 原則 leaf が人に返すとした種類の判断 (人と決める論点) が残る。人手でしか進まない作業も残っていてよい — 論点が決まれば、作業は本文に残る | 外側 |
| 4 (人手) | 決める論点は残らず、人手でしか進まない作業 (権限で塞がれた操作・人の資格情報が要る操作・物理的な手作業) だけが残る | 外側 |
| `needs-info` | 報告者しか持たない事実が要る | 外側 |

- 再現や計測の実行が要る issue は自分で実行せず、再現確認を受け入れ条件に足して区分 1 にする (再現や計測は 4 (人手) の作業に数えない)
- 本文が前提とする変更 (別の issue の実装や open CL が足す file・規範) が既定 branch にまだ無い issue は、区分を決めても §3・§5 に進めず、停止として返す。有無は、本文が名指す file・識別子を close の確かめと同じ `origin/<既定 branch>` への `git show` / `git grep` で見て、名指された issue / CL は state だけを引く — それより広く読むと 1 件の費用が上限なく増える。停止理由に前提の issue / CL の番号 (番号が無ければ既定 branch に無い file・規範の名) を書き、人が次にやることは「前提が既定 branch に入ったら triage をやり直す」とする。label はこの skill では動かさないので、呼び出し元が queue から外さなければ次の triage でも同じ停止に戻る — 前提の無い本文を `ready-for-agent` にすると、worker が実在しない file・規範を相手に作業する
- 範囲や受け入れ条件を変えるときは、書き換え後の本文全体を書き換え案にする — 無人で実装する側は本文しか読まない。元の本文に、前の本文より優先すると宣言した追記の節 (`## 確定事項` 等) があれば、その内容を本文の該当箇所へ溶かし込んで節を消す (書き換えた本文に優先順位の規則を残さない)
- open CL がある issue は、区分を決めても tracker に書かない — CL は書き換える前の本文を実装している。判定と書き換え案を添えて呼び出し元へ返す (CL を差し戻すかは人が決める)
- 自分で下した判断は、規範へ書き起こす (codify) 候補にしない。codify は人の決定を規範にするもので、承認する人がいない場で文案を積まない
- 過去の triage のコメントが外側 (`needs-grilling`・`ready-for-human`・`needs-info`) と判定した issue は、label が `needs-triage` に戻っていても、そのコメント以降に新しい情報 (報告者・人の回答、人手の作業を終えた旨の人のコメント、決定を書き込んだ本文の書き換え、論点を決めた規範の追加) が無ければ、前回と同じ外側の区分で返す。区分 1・3・close・`wontfix` にしない — 前回人に返すとした判断を、agent が後から自分で下さない(前回の判定の後に、重複・既に直っている・前提が変わったことを §1 の確かめ方で確かめた issue は close してよい)。前回のコメントが 4 を下位の区分なしで書いていれば、4 (判断) と 4 (人手) のどちらかに分けて返してよい (分けるのは区分の変更に数えない)

## 3. `ready-for-agent` にする前の検査 (区分 1・3)

1. 区分 1・3 の issue をまとめて `swat-skills:ready-for-agent-review` を Skill tool で invoke する。args は issue 置き場と `#<N>` の見出しで、書き換え案がある issue は見出しの下に逐語で置く
2. 返った issue ごとのブロックで分ける。`### 指摘` に指摘がある issue は、修正案を書き換え案に当て、その issue だけで invoke し直す。修正案の行の代わりに `深掘り` の行を持つ指摘だけが残った issue は、invoke し直さずに区分 4 (判断) へ振り直す — 書き換え案に当てる文面が無いので、invoke し直しても同じ指摘が返る。`停止:` 行の無いブロックで未反証の節が残った issue も、そのまま invoke し直す。invoke は 1 issue につき 2 回まで (手順 1 のまとめての invoke を 1 回目と数える) にし、invoke し直すときも Skill tool で invoke する — Agent に手順を読ませて代わりに回すと、その回が上限に数えられない。2 回目でも指摘が残れば、残った論点に応じて区分 2・4 (判断)・4 (人手)・`needs-info` へ振り直す。次の issue は、どの回でも invoke し直さずに、`ready-for-agent` にせず停止として返す: `停止:` 行を持つブロックが返った issue (ブロックの停止理由と次の手順を §6 の停止に写す) / `## ready-for-agent-review: #<N>` のブロックが返らなかった issue (要約だけが返った・手順を実行せずに終わった) / 2 回目の invoke の後も未反証の節が残った issue — 指摘が 0 件だったことは `### 指摘` の「該当なし」でしか確かめられず、要約や自分の見立てで代えると、検査を通っていない本文に `ready-for-agent` が付く
3. 指摘が follow-up issue の文案 (規範の衝突を裁定したときの、負けた規範を直す issue) を含んでいたら、最後に返った文案を控える
4. `### 指摘` が「該当なし」で未反証の節も無い issue を `ready-for-agent` にする

## 4. 規範の文案 (区分 2)

先に、同じ規範の文案を待つ issue を探す。`gh issue list -R <issue 置き場> --state open --label ready-for-human --limit 1000 --json number,comments` の出力を file に受け (返った件数が `--limit` に届いたら上げて引き直す)、行頭が `規範の文案: <置き場の path>` のコメントを拾う (検索 qualifier は日本語の語句を取りこぼすので、コメントを読んで照らす)。同じ path があれば文案を書き直さず、その issue の番号を返す。同じ実行の中で複数の issue が同じ path の文案を要するときも、文案は args に並んだ順で最初の issue のブロックに 1 本だけ書き、その 1 本に、同じ path を要する issue それぞれに足りない一般則をすべて入れる。残りの issue のブロックには最初の issue の番号を書く — 別々に書くと、人が 1 本を採った後に残りの文案が食い違ったまま残る。

どちらにも当たらなければ、足りない一般則を書き込む先の path と、規範の文面を逐語で書く。返すときは 1 行目を `規範の文案: <置き場の path>` にし、path の後ろには何も書かない (書き込む節の見出しや説明は 2 行目から書く) — 呼び出し元がこの行を `ready-for-human` の issue へ投稿すると、次の triage がこの節の検索で見つける。

## 5. 反映 (`ready-for-agent` にする issue・close・`wontfix`)

§3 で `ready-for-agent` にすると決まった issue と close・`wontfix` の issue のうち、open CL が無く、書き換え案があるなら基準もあるものだけ、次の順で反映する。停止・振り直しになった issue には何も書かない。文面は file に書いて `--body-file` で渡す。

1. **本文**: 書き換え案があれば、`${CLAUDE_SKILL_DIR}/references/body-rewrite.md` の「書き戻し」の順で書き戻す。手順 1 で外す label は、既に付いている `ready-for-agent` だけ (検査を通る前の本文に `ready-for-agent` を残さない。ほかの状態の label は下の 4 で外す)。ほかの状態の label が付いていなければ、同じ操作で `needs-triage` を付ける — 外すだけだと、手順 2 か 5 で止めたときに状態の無い issue が残る。手順 2 か 5 で止めたら、その issue の以降の操作をせず、同書「停止の扱い」の 4 項目を停止として返す。旧本文の記録の投稿に失敗したら、基準の file の中身を逐語で添えて返す
2. **follow-up issue** (§3 で文案を控えたとき): この起票は repo の issue の規範の起票の節ではなく、ここの手順に従う。題名は `<負けた規範の path>: <文案の題名>` にする — 文案の題名は呼ぶたびに書かれて揺れるので、重複は path で照らす。§1 で受けた題名の一覧から、同じ `<負けた規範の path>:` で始まる issue を探す。あれば起票せずに発端 (この issue の番号) をコメントで足す。無ければ `gh issue create -R <issue 置き場> --title <題名> --body-file <file> --label needs-triage` で起票し、起票した番号と題名を一覧の file に追記する (同じ実行の続く issue で二重に起票しない)。本文には文案どおり `## 範囲` の節を置き (範囲の無い issue は次の検査で差し戻される)、その節の直前に独立した 1 行 `発端: #<この issue の番号>` を置く
3. **根拠**: 区分と根拠をコメントにする。区分 3 は決めたことと退けた案、close は重複先・直した変更・変わった前提、`wontfix` は当てた規範の path と見出し、起票しない種類と判定した理由を 1 行で書く — label だけでは理由が次に読む人へ残らない。既に `ready-for-agent` だけが付き、本文を書き換えず、根拠が直近の triage のコメントと変わらない issue にはコメントを足さない (同じ根拠が積み重なるだけになる)
4. **状態**: `ready-for-agent` は `gh issue edit <N> -R <issue 置き場> --add-label ready-for-agent --remove-label <付いている状態の label>` の 1 回で付け替える (状態の label は `needs-triage`・`needs-info`・`needs-grilling`・`ready-for-human`。本文を書き換えず、既に `ready-for-agent` だけが付いているなら付け替えない)。2 回に分けると、間で止まったときに状態の無い issue か、`ready-for-agent` と別の状態が併存する issue が残る。close は先に `gh issue close <N> -R <issue 置き場>` で閉じ、その後で状態の label を外す — 間で止まっても、閉じた issue に古い label が残るだけで済む。`wontfix` は閉じた後の 1 回の `gh issue edit` で、`--add-label wontfix` と状態の label の `--remove-label` をまとめる。この edit が失敗したら停止として返す — `wontfix` の無い closed issue は、次の起票の重複探しから漏れる

## 6. 返す

issue ごとに次の形で返す。呼び出し元が同じ agent で、あとで表などにまとめ直すときも、先にこのブロックを全 issue 分出す — まとめ直した表には基準や手順コメントの欄が無い。呼び出し元はこれを読んで人へ渡すので、操作の文面と文案は逐語で書く (同じ session で呼ばれ直したときは §1 の最後の項に従う)。区分 4 (判断) に `needs-grilling` を付けて人へ渡す呼び出し元は、返った手順コメントをそのまま投稿する。区分 4 (人手) に `ready-for-human` を付けて人へ渡す呼び出し元は、返った人手の作業のコメントをそのまま投稿する — 4 (人手) には決める論点が無いので、深掘りの手順コメントを付けると、人を論点の無い深掘りへ誘導する。

```
### #<N> <題名>
- 区分: <1 | 3 | close | wontfix | 2 | 4 (判断) | 4 (人手) | needs-info>
- 結果: <反映済み | 返却 (外側) | 停止 | 書き込まず (open CL #<M> / 基準なし)>
- 反映した操作: <本文の書き戻し・コメント・起票した follow-up issue の番号・label。無ければ「なし」>
- 根拠: <区分の根拠。区分 3 は決めたことと退けた案>
- 基準: <この issue で保存した基準の file の path>
- 呼び出し元に渡すもの (下に当たる区分・結果が無ければ「なし」):
  <区分 2: 「規範の文案: <path>」の行と逐語の文面、または同じ規範を待つ issue の番号>
  <区分 4 (判断): 論点と選択肢 (推奨を 1 つ名指す)。続けて深掘りのセッションへの手順コメントを、`${CLAUDE_SKILL_DIR}/references/grilling-handoff.md` を Read して値まで埋めて逐語で書く (雛形の二つの path には `${CLAUDE_SKILL_DIR}/../../design/grill-with-docs/SKILL.md` と `${CLAUDE_SKILL_DIR}/references/body-rewrite.md` を、ここに見えている絶対 path のまま書く)。読んだコメントに 1 行目が同じコメントが既にあれば、手順コメントは書かず「手順コメントあり」とする>
  <区分 4 (人手): 手順コメントは作らず、`ready-for-human` の issue へ投稿する人手の作業のコメントを逐語で書く。中身は停止理由 (どの操作がなぜ人手でしか進まないか)・そこまでに残した成果・人が次に実行する手順の checklist (`- [ ]` の行。最後の行は「作業を終えたら、その旨と結果を新しいコメントに書き、`ready-for-human` を外して `needs-triage` を付ける」で、label は対象 repo の綴りで書く — label を外さないと作業を終えた issue がどの queue にも戻らず、コメントが無いと次の triage が新しい情報を見つけられず同じ 4 (人手) で返す)>
  <needs-info: 報告者へ聞くことの逐語>
  <停止: 停止理由・label の現状・まだ反映していない操作・人が次にやること。旧本文の記録の投稿に失敗したときは基準の file の中身の逐語>
  <書き込まず: 判定と書き換え案の逐語、反映しなかった理由>
```

置き場や label を宣言から読めなかったときは、その前提を末尾に 1 行添える。
