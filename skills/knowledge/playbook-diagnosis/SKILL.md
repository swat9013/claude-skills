---
name: playbook-diagnosis
description: bug の診断を、原則の索引 Read から根本原因の記録・返却までの番号付き step で通す playbook。
disable-model-invocation: true
---

# 診断の playbook

bug の根本原因を特定し、修正の判断材料として issue に残すまでの手順。step の中でどの原則を使うかを名前で置いてある。修正・PR は担当外。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-diagnosis` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step は逐語で todolist へ写す。** 要約・統合・並べ替えをしない
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない
- 条件付きの step には条件が書いてある。条件に当たらないなら、その条件を `skip:` の理由に書く

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../principle-index/SKILL.md` を Read し、適用条件が今回の作業に当たる leaf を全部 Read する。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。**以降の step が `principle-*` を名指ししたら、索引が示す path から Read する** — leaf は invoke せず読む。

2. 症状を確定する。依頼の本文 (issue 等) を逐語で読み、期待した挙動と実際の挙動を 1 文ずつで言明する。完了条件は「根本原因の言明が証拠付きで issue に記録され、修正の判断が依頼元へ返っていること」— 修正コードを書き始めたら本 playbook の外に出ている。

3. 再現ループを作る。**仮説を立てる前に**、このバグで必ず失敗する (red) 検証 1 コマンドを作る。red なコマンドを 1 度も実行しないまま code を読んで理論を組み始めたら、この step へ戻る。
   - 手段は次の優先順で当てる: 失敗テスト / curl / fixture 付き CLI / headless browser / trace replay / 使い捨て harness / property loop / bisect harness / differential 実行。credential は env var 経由で受ける形で組み、コマンドや出力に直書きしない
   - 作れたら tight にする: 秒で終わり、毎回同じ判定を返し、症状そのものを assert する (「crash しなかった」では red にならない)
   - 非決定バグは clean な再現でなく**再現率を上げる**: trigger を 100 回 loop・並列化・timing を狭める
   - ループが作れないなら `principle-fail-loudly` — 試した手段を列挙して issue へ記録し、再現に必要なもの (環境アクセス / redacted な captured artifact / 一時計測の許可) を明示して停止する

4. 再現を最小化する。ループを red のまま、入力・呼び出し元・設定・データを 1 つずつ削っては再実行し、**残る要素すべてが load-bearing** (どれを外しても green に変わる) になるまで縮める。最小化した分だけ step 5 の仮説空間が狭まる。

5. 仮説を 3〜5 本立てて記録する。1 本目で止めると最初の思いつきに anchor するので、必ず複数をランク付けする。各仮説は反証可能な予測を持つ:「<X> が原因なら、<Y> を変えるとバグが消える / <Z> を変えると悪化する」。予測が書けない仮説は捨てるか研ぎ直す。**ランク付きの一覧を issue へコメントしてから検証に進む** — 人の返答は待たないが、検証過程を後から監査できる形で先に残す。

6. 計測して反証する。probe は step 5 の予測 1 つに対応させ、**変える変数は一度に 1 つ**。debugger / REPL が使える環境ならそれを優先し、使えないときだけ仮説を切り分ける境界に絞ったログを足す — ログには一意な接頭辞 (例: `[DEBUG-a4f2]`) を必ず付け、後始末を grep 1 回にする。性能バグはログでなく計測 — baseline を測ってから bisect する。仮説が確証されるまで step 5 の一覧へ戻り、`principle-fix-root-causes` で「症状を消せる箇所」でなく「原因の連鎖の根」まで辿れているかを確かめる。

7. 診断結果を issue へ記録する。次を 1 コメントにまとめる: 根本原因の言明 (証拠 `path:line` 付き) / 再現 1 コマンド (実行した invocation と output を逐語で。修正側が TDD の red にそのまま使える形) / 反証済み仮説とその根拠 / 回帰テストの設計案 (どの seam にどんなテストを置くべきか。**バグの実パターンを通せる seam が無いなら、無いこと自体を発見として書く**)。red なテストや再現 script は commit せず、コメント内の逐語で渡す。貼る output は秘匿情報を `<REDACTED>` に置換し、信号を運ぶ行だけを引用する。

8. 後始末する。step 6 の接頭辞を grep して計測用の一時変更をすべて除去し、作業ツリーの diff が空であることを確かめる。

9. PR 到達前に 2 軸レビューを通す。条件: 診断の成果 (再現 harness 等) を例外的に repo へ commit するとき。issue コメントだけで終わり差分が出ないなら `skip: 差分なし`。
   - `swat-skills:two-axis-review` を Skill tool で invoke する
   - Act on を直してから 2 回目のレビューを行い、**レビューは 2 回で打ち切る** (2 回目は 1 回目の修正が新しい症状を作っていないかを見る回。3 回目以降は収束より消耗が勝つ)。2 回目は `${CLAUDE_SKILL_DIR}/../two-axis-review/SKILL.md` を Read で読み直してから、手順を反証工程まで含めて通しで実行する — compaction で本文が context から落ちていても読み直せば戻るので、落ちているかを自分で判定せずに済む
   - 打ち切り時点で残った Act on と、反証で落ちた Dismissed を**具体物ごと** PR 説明文へ載せてから PR を出す。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる
   - PR 説明文の節構成は `swat-skills:pr-quality` を Skill tool で invoke して決める

10. 修正の判断を返す。修正に着手するかの判断は依頼元のフロー (人 / triage) へ返す — 着手可否・優先度・label 操作は実行 project の運用に従う。
