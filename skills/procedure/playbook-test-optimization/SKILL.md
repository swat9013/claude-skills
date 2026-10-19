---
name: playbook-test-optimization
description: テストスイートの軸ごとの診断を、原則の索引 Read から適用判断の返却までの番号付き step で通す playbook。
disable-model-invocation: true
---

# テスト戦略・設計最適化の playbook

既存テストスイートを軸 (レベル配分 / ダブル境界 / リファクタリング耐性 / 剪定候補 / 対象ごとの厚さ) ごとに診断し、最適化案を判断材料として渡すまでの手順。step の中でどの skill と原則を使うかを名前で置いてある。テストの書き換え・削除 (適用) は担当外。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-test-optimization` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step 1 に着手する前に、todolist tool で step を逐語 todolist へ写す。** 要約・統合・並べ替えをしない (変形した分だけ必須工程が silent に欠ける)。各項目の題には step 番号と先頭文を逐語で書き、step 本文を条件節・sub-bullet ごとそのまま添える。skip する step は題の先頭に `skip: <理由>` を付ける。todolist tool が tool 一覧に無いときは、写す先を最終返答に替え、冒頭に「todolist tool なし」と 1 行書く
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない。条件付き step で条件に当たらないときは、その条件を理由に書く。spawn prompt から Read で開いたとき (人が todolist を見ない) は、skip 行を最終返答 (PR 到達時はレビュー出力とは別の 1 コメントで PR) にも写す

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、索引の読み方に従って今回の作業に当たる leaf を読む。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。以降の step が `principle-*` を名指ししたときも、索引の読み方に従う。

2. 対象と完了条件を確定する。依頼で対象パスが指定されていればそれを、無ければ repo のテストスイート全体を対象にする。完了条件は「step 4 の全軸の診断結果と最適化案が具体物 (`path:line`) 付きでレポートに言明され、適用の判断が依頼元へ返っていること」— テストコードを書き換え始めたら本 playbook の外に出ている。

3. スイートの現状を観測する。テストの層別 (単体 / 統合 / e2e 相当) の分布、mock・patch の使用箇所、テストの実行手段、テストごとの対象 file とその対象の性質 (所有者・振る舞いの有無・決定的か推論的か・利用者の範囲・黙って壊れるか・被害の回復性) を repo の実体 (test script / CI 設定 / runner 設定) から読み取る。カバレッジの計測手段が repo にあれば計測する。`principle-fail-loudly`: 計測できなかったもの・読み取れなかったものは「できなかった」とレポートに残す (黙って落とすと、観測していない軸が観測済みに見える)。

4. 次の軸で監査する。指摘には具体物 (`path:line` と、どの規則に照らしたか) を添え、具体物の無い指摘は出さない。対象ごとの厚さの軸を先に通し、各テスト対象が判断表のどの行に当たるかを決めてから剪定候補の軸へ進む。
   - **対象ごとの厚さ**: `principle-test-thickness-by-property` — 対象の性質と厚さのずれを厚すぎ・薄すぎの両方向で見る (推論で効く文の言い回しを assert するテスト、vendor や宣言だけの設定へのテスト、開発者しか使わない対象への境界値・書式揺れの網羅、黙って壊れて取り消せない対象の薄いテスト)
   - **レベル配分**: `principle-test-level-allocation` — 投資配分のずれ (委譲だけの glue code への単体テスト集中、ユースケースを検査する自動テストの不在)
   - **ダブル境界**: `principle-test-double-boundary` — managed dependency の mock、時刻・乱数・環境変数の非注入
   - **リファクタリング耐性**: `principle-observable-behavior` — 実装詳細 (内部の呼び出し順・private 状態・中間データ構造) への assert
   - **剪定候補**: `principle-test-pruning` — 厚さの軸で行 4・5 に当たった対象にだけ当てる。候補には限界価値がゼロに見える根拠と、適用側が確定判定に使う検証手順 (剪定前後のカバレッジ 2 点比較等) を添える。確定判定そのものは適用側の作業

5. レポートを書く。軸ごとに指摘と最適化案を列挙し、指摘の無かった軸は「問題なし」と明記する (見なかったのか無かったのかを区別する)。実行時間・並列化の問題を見つけていたら、診断せず「`/swat-skills:playbook-test-perf-tuning` の領分」として節を分けて列挙する。置き場は実行 project の doc 規約に従い、定めが無ければ gitignore 済みの作業ディレクトリへ書き、それも無ければレポート全文を応答で返す。

6. PR 到達前にレビュー工程を通す。出力は PR を出した後にコメントで投稿する。条件: 診断結果を repo へ commit するとき。read-only で終わり差分が出ないなら `skip: 差分なし`。
   - `swat-skills:review-stage` を Skill tool で invoke する
   - review-stage は commit 済みの差分だけを読むので、invoke の前に変更を commit する。発端の issue があれば `issue=<ref>` を args で渡す。出力の `再レビュー:` 行が `停止` で始まるときは、理由が `未 commit` で、`停止理由:` 行に挙がった file がすべてこの作業で入れた変更なら commit して同じ args で invoke し直す (この停止出力を `prev=` に渡すと初回へ落ちる)。それ以外の停止 (無関係な変更を含む `未 commit`・`reviewer 未起動`・`その他: <自由文>`) は、レビューを通らないまま先へ進まないよう user へ報告して止まる (spawn prompt から開いたときは最終返答に載せる)。`停止` で始まらなければ Act on を直して commit し、`再レビュー:` 行が `要` で始まるときだけ `swat-skills:review-stage` を invoke し直し、前巡の出力を保存した file を `prev=<file>` で渡す — 前の invoke の記憶から reviewer を直接立てると、invoke 先の手順が抜ける。`要` で始まらなければレビューはそこで終える
   - レビューを 1 回走らせるたびに、その出力を注記行まで含めて逐語で一時 file へ保存する — PR へ載せるのは PR を出した後なので、context に頼ると compaction で逐語が崩れる。置き場は作業ツリーの外 (中に置くと commit に混ざるか、untracked file が作業ツリーの削除を拒む)、名前はどのレビューの何巡目かが分かる形にし、投稿したら消す。最後に走らせた巡の後に修正したら、その修正を「レビュー後に修正 (未レビュー)」として最後の巡の file へ書き足す — その修正はレビューを通っていないので、通っていないことを人が読める形で残す
   - PR を出したら (既存の PR へ push する作業なら push したら)、保存した file を走らせた順に 1 回 1 コメントで**そのまま** PR へ投稿する (`gh pr comment <番号> --body-file <file>`。GitLab は `glab mr note create <番号> --resolvable=false < <file>` — 解決待ちの thread にすると merge を塞ぐ)。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる — 注記行が無いと、Act on のうちどれだけが反証を経たかを読み手が判別できない。PR 説明文はレビューを経た最終状態だけで書き、PR を初めて見た人が何を変えたかを読み取れるようにする — レビューの経過はコメントが持つ
   - レビューで直さなかった指摘と、担当範囲の外で見つけた欠陥は、作業ツリーの repo の issue の規範 (`docs/agents/issue-tracker.md` など) に従って、起票するかを決めて起票する
   - PR 説明文の節構成は `swat-skills:pr` を Skill tool で invoke し、その template を土台に決める。template の節のうち、この CL に中身の無い節は省く。呼び出し元 (spawn prompt・workflow 定義) が求める節は template の節と並べて足し、中身が無くても見出しを残して「なし」と書く (後段の作業がその節を見出しで探しに来る)。説明文の節がそろったら PR を draft で出し (既にある PR へ push する作業ならその PR のまま)、ready にするのは作業が完了したときだけにする。完了は、説明文に未確定の値が無く、PR に載せる記録 (保存したレビュー出力と、PR へ写すときの skip 行) を投稿し終えた状態で、投稿の後に commit を足したならその修正も「レビュー後に修正 (未レビュー)」としてコメントで足してある。待ちの長い値 (計測や実行の完了) は、待っている値とそれを生む手段 (コマンド・job の参照先) を書いて未確定と明記したまま draft で待ち、確定して書き換えてから ready にする — draft で先に出しておけば、待つ間に呼び出し元が課す実行時間の上限に当たっても PR と記録が残る。待ちきれずに終えるときは draft のまま終え、未確定の値と手段を最終返答に載せる。GitHub は `gh pr create --draft` / `gh pr ready <番号>`、GitLab は `glab mr create --draft` / `glab mr update <番号> --ready`

7. 適用の判断を依頼元へ返す。最適化案を適用するかの判断と実行は依頼元のフロー (人 / triage) に委ねる — レポートがそのまま適用作業の判断材料になる形 (具体物と検証手順付き) で渡っていることを確かめる。
