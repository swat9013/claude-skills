---
name: playbook-conflict-resolution
description: 既存 CL の merge conflict 解消を、原則の索引 Read から既存 branch への push までの番号付き step で通す playbook。
disable-model-invocation: true
---

# コンフリクト解消の playbook

既存 CL の head branch 上で base branch との merge conflict を解消し、push するまでの手順。対象 CL の番号・URL・head / base branch は呼び出し元 (spawn prompt / 依頼文) から受け取る。新規 CL は作らず、既存 branch へ push する。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-conflict-resolution` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step 1 に着手する前に、TodoWrite で step を逐語 todolist へ写す。** 要約・統合・並べ替えをしない (変形した分だけ必須工程が silent に欠ける)
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない。条件付き step で条件に当たらないときは、その条件を理由に書く。spawn prompt から Read で開いたとき (人が todolist を見ない) は、skip 行を最終返答 (PR 到達時はその説明文) にも写す
- playbook を複数渡されたときは、渡された順に本文を Read して todolist を連結する (並び順は渡す側の契約が決める)

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、適用条件が今回の作業に当たる leaf を全部 Read する。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。**以降の step が `principle-*` を名指ししたら、索引が示す path から Read する** — leaf は invoke せず読む。

2. conflict を解消する。CL の head branch から作った作業ツリーで `git merge origin/<base>` を撃つ (base は呼び出し元から受け取った CL の base branch を使う — stack した CL の base は main でない)。conflict を解消して commit し、解消の判断 (どちらの変更を採ったか) を CL 説明文の自律判断の節に追記する。解消できない conflict と、merge が何も変えない場合は、続行不能として呼び出し元のフローで人へ返す。

3. PR 到達前に 2 軸レビューを通す。既存 CL では push が PR 到達に当たり、対象差分は conflict 解消で入った変更に絞る。
   - `swat-skills:two-axis-review` を Skill tool で invoke する
   - Act on を直してから 2 回目のレビューを行い、**レビューは 2 回で打ち切る** (2 回目は 1 回目の修正が新しい症状を作っていないかを見る回。3 回目以降は収束より消耗が勝つ)。2 回目は `${CLAUDE_SKILL_DIR}/../two-axis-review/SKILL.md` を Read で読み直してから、手順を反証工程まで含めて通しで実行する — compaction で本文が context から落ちていても読み直せば戻るので、落ちているかを自分で判定せずに済む。2 回目の対象差分は 1 回目の指摘へ応えた修正差分に絞る (全差分を再走査すると新規指摘が発散し、その修正がまた未レビューの差分を生む)
   - 2 回目の指摘へ修正で応えたら、その修正は「2 回目後に修正 (未レビュー)」として PR 説明文へ列挙する — 打ち切りの構造上この修正はレビューを通らないので、通っていないことを人が読める形で残す
   - 打ち切り時点の 2 軸レビュー出力を、各節の注記行 (`reviewed:` / `反証対象:` 等) まで含めて**そのまま** PR 説明文へ載せてから PR を出す。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる — 注記行が無いと、Act on のうちどれだけが反証を経たかを読み手が判別できない
   - 載せ先は新規 PR ではなく既存 CL の説明文への追記。追記する節の構成は `swat-skills:pr-quality` を Skill tool で invoke して決める

4. 既存 branch へ push する。push が終わった時点で conflict は解消扱い — 結果の見届けは呼び出し元の観測 (dispatcher なら次 tick) が持つ。
