---
name: playbook-implementation
description: 実装作業を、原則の索引 Read から 2 軸レビュー通過までの番号付き step で通す playbook. Use when 呼び出し元 (user / skill / spawn prompt) が本 playbook を名指しで通すよう指示したとき。名指しの無い実装依頼では起動しない.
user-invocable: true
metadata:
  deliverable: cl
  dispatch-when: コード・設定・docs の変更で受け入れ条件が閉じる issue 全般 (既存 skill の改修を含む)。
---

# 実装の playbook

コードを書き換えて PR へ出すまでの手順。step の中でどの skill と原則を使うかを名前で置いてある。

## 使い方

入口は 3 つあり、どれも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-implementation` で開くか、skill / セッションが Skill tool で invoke するか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step 1 に着手する前に、TodoWrite で step を逐語 todolist へ写す。** 要約・統合・並べ替えをしない (変形した分だけ必須工程が silent に欠ける)
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない。条件付き step で条件に当たらないときは、その条件を理由に書く。spawn prompt から Read で開いたとき (人が todolist を見ない) は、skip 行を最終返答 (PR 到達時はその説明文) にも写す
- **複数の issue / PR に続けて適用するときは、issue ごとに本文を読み直してから step 1 から通す** (todolist も issue ごとに作り直す)。記憶で工程を再現すると、context が薄れた分だけ必須工程 (反証・読み直し) が silent に欠ける

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、適用条件が今回の作業に当たる leaf を全部 Read する。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。**以降の step が `principle-*` を名指ししたら、索引が示す path から Read する** — leaf は invoke せず読む。

2. 受け入れ条件を確定する。依頼の本文 (issue 等) を逐語で読み、何を満たせば完了かを列挙する。作業ツリーの外にある実体を触る条件は自分の担当から外し、誰の担当として残るかを書き出す (自分の sandbox が書き込みを拒否するので、抱えたままだと完了できない)。

3. 土台を最新にする。既定ブランチから分岐した作業ブランチにいることを確かめてから、作業ツリーの repo で `git pull --no-rebase <remote> <既定ブランチ>` を実行する (既定ブランチ上にいるなら先に作業ブランチを作る)。古い土台の上に積んだ差分は merge のときにやり直しになる。

4. 変更の形を決める。選択肢が複数あるなら `principle-design-derivation-order` (制約 → アーキテクチャ特性 → 構造の順で導く) と `principle-localize-change-impact` (原則同士が競合したら波及を抑える側) で選ぶ。自前実装と既製品が拮抗したら `principle-build-vs-buy`。決めた理由と退けた案を書き留める — step 8 の PR 説明文でそのまま使う。

5. テストを先に書く。条件: 振る舞いを追加・修正するとき。`principle-tdd-rhythm` で失敗するテストを先に置き、`principle-test-level-allocation` でレベルを決め、`principle-test-as-spec` で名前と構造を決める。振る舞いが変わらない作業 (文書・設定・構造だけのリファクタリング) は `skip: 振る舞いの変更なし`。

6. 実装する。`principle-structure-simplicity` (責務の置き場と共通化の有無) / `principle-ubiquitous-naming` (識別子と概念名) / `principle-signature-intent` (引数とシグネチャ) / `principle-fail-loudly` (エラー処理とフォールバック) を当てる。

7. 欠陥を探す。条件: コードの差分 (script / hook / テスト等、実行されるもの) を出したとき。指示文・docs だけの差分は `skip: コード差分なし`。標準の `code-review` skill (prefix なし) を、既定ブランチとの merge-base からの差分を対象に指定して Skill tool で invoke する (既定の「現在の diff」は commit 済みの作業ツリーでは空になり、欠陥探しが無言で空振りする)。対象は commit range で渡す — 例: `$(git merge-base <remote>/<既定ブランチ> HEAD)..HEAD`。ブランチ名や単一 commit を渡すとそれ自体の差分と解釈され、別の差分へのレビューが 1 回分空振りする。crash・データ破損・到達可能性は step 8 の 2 軸レビューの担当外なので、ここで見なければどこでも見ない。
   - effort は `medium` か `high` から選び、invoke の args で渡す (渡さないと選定と無関係な level で走る): 重要領域 (決済・金銭計算 / 認証・認可 / 個人情報・秘匿情報 / 削除・migration 等の不可逆操作 / 並行処理・共有状態 / 外部入力の境界) に触れる、または差分が大きい (目安 +300 行超) なら `high`、それ以外は `medium`。`low` は候補ごとの検証工程 (verify) が走らない 1-pass のため選ばない

8. PR 到達前に 2 軸レビューを通す。`swat-skills:two-axis-review` を Skill tool で invoke する。
   - Act on を直してから 2 回目のレビューを行い、**レビューは 2 回で打ち切る** (2 回目は 1 回目の修正が新しい症状を作っていないかを見る回。3 回目以降は収束より消耗が勝つ)。2 回目は `${CLAUDE_SKILL_DIR}/../two-axis-review/SKILL.md` を Read で読み直してから、手順を反証工程まで含めて通しで実行する — compaction で本文が context から落ちていても読み直せば戻るので、落ちているかを自分で判定せずに済む。2 回目の対象差分は 1 回目の指摘へ応えた修正差分に絞る (全差分を再走査すると新規指摘が発散し、その修正がまた未レビューの差分を生む)
   - 2 回目の指摘へ修正で応えたら、その修正は「2 回目後に修正 (未レビュー)」として PR 説明文へ列挙する — 打ち切りの構造上この修正はレビューを通らないので、通っていないことを人が読める形で残す
   - 打ち切り時点の 2 軸レビュー出力を、各節の注記行 (`reviewed:` / `反証対象:` 等) まで含めて**そのまま** PR 説明文へ載せてから PR を出す。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる — 注記行が無いと、Act on のうちどれだけが反証を経たかを読み手が判別できない
   - PR 説明文の節構成は `swat-skills:pr-quality` を Skill tool で invoke して決め、step 4 で書き留めた理由と退けた案も載せる
