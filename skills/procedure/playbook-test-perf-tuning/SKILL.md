---
name: playbook-test-perf-tuning
description: テスト実行時間の短縮を、原則の索引 Read からレビュー工程の通過までの番号付き step で通す playbook。
disable-model-invocation: true
metadata:
  deliverable: cl
  dispatch-when: テストスイートの実行時間短縮を主目的とする issue だけ (テストの追加・修正やテスト対象の実装変更は対象外)。
---

# テスト時間チューニングの playbook

テストスイートの実行時間を実測し、green と短縮を実測確認できた変更だけを残すまでの手順。step の中でどの skill と原則を使うかを名前で置いてある。変更影響ベースの selective testing と CI 基盤側の最適化は提案止まりで、適用は担当外。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-test-perf-tuning` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step 1 に着手する前に、todolist tool で step を逐語 todolist へ写す。** 要約・統合・並べ替えをしない (変形した分だけ必須工程が silent に欠ける)。各項目の題には step 番号と先頭文を逐語で書き、step 本文を条件節・sub-bullet ごとそのまま添える。skip する step は題の先頭に `skip: <理由>` を付ける。todolist tool が tool 一覧に無いときは、写す先を最終返答に替え、冒頭に「todolist tool なし」と 1 行書く
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない。条件付き step で条件に当たらないときは、その条件を理由に書く。spawn prompt から Read で開いたとき (人が todolist を見ない) は、skip 行を最終返答 (PR 到達時はレビュー出力とは別の 1 コメントで PR) にも写す

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、索引の読み方に従って今回の作業に当たる leaf を読む。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。以降の step が `principle-*` を名指ししたときも、索引の読み方に従う。

2. 対象と基準コマンドを確定する。依頼で対象パスが指定されていればそれを、無ければテストスイート全体を対象にする。テストの実行コマンドを repo の実体 (test script / CI 設定 / runner 設定) から発見し、どれを計測の基準に選んだかを明示してから計測に入る — 人の返答は待たないが、比較の前提を後から監査できる形で先に残す。**以降の 2 点比較はすべて同じコマンド・同じ環境で行う** (条件の変わった計測は比較にならない)。完了条件は「基準時間と変更後時間が同条件の実測で対に記録され、green と短縮を実測確認できた変更だけが作業ツリーに残っていること」。

3. baseline を実測する。既存の計測データ (CI の時間ログ・junit.xml・`--durations` 出力等) があれば流用し、無ければ基準コマンドで実測する。runner の計時機能 (pytest なら `--durations` 等) で遅いテスト・遅い setup の上位を特定する。`principle-verify-the-real-artifact`: 実測の無いまま推測で手を入れ始めたら、この step へ戻る。

4. 既存設定の意図を確かめる。直列実行・並列化の除外・timeout 等の現行設定には理由がありうる — コメント・docs・commit log から意図を読み、意図が読み取れた設定は変更対象から外して「意図あり」としてレポートへ回す。変更候補に入れるのは意図の読み取れなかった設定だけ。

5. 個別最適化を適用する。条件: step 3 で遅いテストを特定できたとき。上位から、setup の重複 (fixture の生成回数・scope)、実時間待ち (sleep・実時刻依存 — `principle-test-double-boundary` の注入で置換)、不要な I/O を直す。1 変更ごとに step 7 の実測確定 (green と短縮) を通してから次の変更へ進む — 複数の変更を混ぜると、どれが効いたか・どれが壊したかを実測で切り分けられない。

6. 並列化を導入・調整する。条件: runner に並列化手段があるとき。先にテスト間の共有状態 (共有ファイル・共有 DB・共有 env — `principle-isolate-shared-writes`) を確かめ、共有の残るテストは並列化の対象から外す。導入済みの repo では worker 数・分配方式の調整だけを見る。

7. 変更を実測で確定する。本 step は step 5・6 の各変更に繰り返し適用し、todolist 上は最後の変更の確定をもって完了とする。1 変更ごとに、基準コマンドで green と時間短縮を実測する。並列化に触れた変更は直列と並列の両方で green を確認する (直列でだけ通るテストは並列化が壊した状態共有の症状)。どちらかが確認できない変更は revert する — `principle-verify-the-real-artifact`: 「たぶん速くなった」を作業ツリーに残さない。

8. レポートを書く。基準と結果の対 (コマンド・実測時間) を逐語で記録し、適用した変更と revert した変更 (revert の理由付き) を列挙する。適用範囲の外に置いたもの — selective testing、CI 基盤側の最適化 (キャッシュ・シャーディング) — は提案として列挙し、step 4 で「意図あり」とした設定は読み取った意図付きの判断材料として列挙する (変更提案ではない)。テスト設計側の問題 (レベル配分・実装詳細への結合・剪定候補・対象の性質に合わない厚さ) を見つけていたら、診断せず「`/swat-skills:playbook-test-optimization` の領分」として節を分けて列挙する。置き場は実行 project の doc 規約に従い、定めが無ければ gitignore 済みの作業ディレクトリへ書き、それも無ければレポート全文を応答で返す。

9. PR 到達前にレビュー工程を通す。出力は PR を出した後にコメントで投稿する。条件: 変更が作業ツリーに残っているとき。全変更を revert して差分が出ないなら `skip: 差分なし`。
   - `swat-skills:review-stage` を Skill tool で invoke する
   - review-stage は commit 済みの差分だけを読むので、invoke の前に変更を commit する。発端の issue があれば `issue=<ref>` を args で渡す。出力の `再レビュー:` 行が `停止` で始まるときは、理由が `未 commit` で、`停止理由:` 行に挙がった file がすべてこの作業で入れた変更なら commit して同じ args で invoke し直す (この停止出力を `prev=` に渡すと初回へ落ちる)。それ以外の停止 (無関係な変更を含む `未 commit`・`reviewer 未起動`・`その他: <自由文>`) は、レビューを通らないまま先へ進まないよう user へ報告して止まる (spawn prompt から開いたときは最終返答に載せる)。`停止` で始まらなければ Act on を直して commit し、`再レビュー:` 行が `要` で始まるときだけ `swat-skills:review-stage` を invoke し直し、前巡の出力を保存した file を `prev=<file>` で渡す — 前の invoke の記憶から reviewer を直接立てると、invoke 先の手順が抜ける。`要` で始まらなければレビューはそこで終える
   - レビューを 1 回走らせるたびに、その出力を注記行まで含めて逐語で一時 file へ保存する — PR へ載せるのは PR を出した後なので、context に頼ると compaction で逐語が崩れる。置き場は作業ツリーの外 (中に置くと commit に混ざるか、untracked file が作業ツリーの削除を拒む)、名前はどのレビューの何巡目かが分かる形にし、投稿したら消す。最後に走らせた巡の後に修正したら、その修正を「レビュー後に修正 (未レビュー)」として最後の巡の file へ書き足す — その修正はレビューを通っていないので、通っていないことを人が読める形で残す
   - PR を出したら (既存の PR へ push する作業なら push したら)、保存した file を走らせた順に 1 回 1 コメントで**そのまま** PR へ投稿する (`gh pr comment <番号> --body-file <file>`。GitLab は `glab mr note create <番号> --resolvable=false < <file>` — 解決待ちの thread にすると merge を塞ぐ)。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる — 注記行が無いと、Act on のうちどれだけが反証を経たかを読み手が判別できない。PR 説明文はレビューを経た最終状態だけで書き、PR を初めて見た人が何を変えたかを読み取れるようにする — レビューの経過はコメントが持つ
   - PR 説明文の節構成は `swat-skills:pr` を Skill tool で invoke し、その template を土台に決める。template の節のうち、この CL に中身の無い節は省く。呼び出し元 (spawn prompt・workflow 定義) が求める節は template の節と並べて足し、中身が無くても見出しを残して「なし」と書く (後段の作業がその節を見出しで探しに来る)。説明文の節がそろったら PR を draft で出し (既にある PR へ push する作業ならその PR のまま)、ready にするのは作業が完了したときだけにする。完了は、説明文に未確定の値が無く、PR に載せる記録 (保存したレビュー出力と、PR へ写すときの skip 行) を投稿し終えた状態で、投稿の後に commit を足したならその修正も「レビュー後に修正 (未レビュー)」としてコメントで足してある。待ちの長い値 (計測や実行の完了) は、待っている値とそれを生む手段 (コマンド・job の参照先) を書いて未確定と明記したまま draft で待ち、確定して書き換えてから ready にする — draft で先に出しておけば、待つ間に呼び出し元が課す実行時間の上限に当たっても PR と記録が残る。待ちきれずに終えるときは draft のまま終え、未確定の値と手段を最終返答に載せる。GitHub は `gh pr create --draft` / `gh pr ready <番号>`、GitLab は `glab mr create --draft` / `glab mr update <番号> --ready`
