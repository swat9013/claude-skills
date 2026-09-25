---
name: playbook-skill-creation
description: 新規 skill 作成を、skill 化の要否判定から 2 軸レビュー通過までの番号付き step で通す playbook。
disable-model-invocation: true
metadata:
  deliverable: cl
  dispatch-when: skill を新規に作る issue だけ (既存 skill の改修は、description や本文の手直しを含めて対象外)。
---

# skill 作成の playbook

新規 skill を作って PR へ出すまでの手順。step の中でどの skill と原則を使うかを名前で置いてある。既存 skill の改修は対象外で、呼び出し元のフローに委ねる。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-skill-creation` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step 1 に着手する前に、TodoWrite で step を逐語 todolist へ写す。** 要約・統合・並べ替えをしない (変形した分だけ必須工程が silent に欠ける)
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない。条件付き step で条件に当たらないときは、その条件を理由に書く。spawn prompt から Read で開いたとき (人が todolist を見ない) は、skip 行を最終返答 (PR 到達時はその説明文) にも写す

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、適用条件が今回の作業に当たる leaf を全部 Read する。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。**以降の step が `principle-*` を名指ししたら、索引が示す path から Read する** — leaf は invoke せず読む。

2. skill 化の要否を判定する。`principle-automate-when-it-hurts` (自動化は痛みの実測後) で、その手順・知識が反復して要ることを依頼の根拠から確かめる。1 回限りのタスク、source や `--help` を読めば分かる情報は skill 化せず、その判断を理由付きで依頼元へ返し、残りの step に `skip: skill 化しない` を付けて終える。作る場合は、導入済み skill の一覧から同じ責務・同じ trigger 語彙の skill が既に無いことを確かめる — 既にあるなら新設ではなくその skill の改修を提案する。

3. 責務と境界を確定する。この skill が「何を書くか / 何をするか」を 1 文で書き、対象外 (隣接するが扱わない作業) を添えて書き留める — description と本文冒頭でそのまま使う。1 文に収まらないなら責務が複数混ざっているので、skill を分ける。

4. 配置と invocation を決める。配置 (plugin 配布 / project-local / global) と登録先は対象 repo の skill 規約 (CLAUDE.md / rules / docs) に従い、規約が無ければ project-local (`.claude/skills/`) に置く。invocation は用途で選ぶ: model にも自動起動させる (user + model 両用) / 他 skill・spawn prompt からだけ呼ぶ (model 専用) / 人が名指しでだけ呼ぶ (user 専用)。`principle-context-budget`: model が読む description は全セッションの context に常時載るので、自動起動が要らないなら user 専用にして context 負担を 0 にする。

5. 文面を書く。`swat-skills:write-for-harness` を Skill tool で args `新規: skill <目的>` を添えて invoke する。frontmatter・description の書式・本文の文面規範の Read から、検証 subagent と構造 reviewer の指摘反映までを同 skill の手順が持つ — 本 playbook 側で文面規範を重複して当てない。step 3 の責務 1 文と step 4 の invocation を入力として渡す。

6. 起動導線で受け入れ確認する。`principle-verify-the-real-artifact`: file が置けたことではなく、step 4 で決めた入口 (Skill tool invoke / `/` メニュー / description trigger) から本文が実際に届くことを確かめる。新規 skill は起動中のセッションの一覧に載らないので、セッション再起動を挟む確認は残作業として依頼元へ明示する。対象 repo に skill の機械検証 (gate / lint) があれば、その規約に従い実行して pass させる。

7. PR 到達前に 2 軸レビューを通す。条件: skill を repo へ commit するとき。
   - `swat-skills:two-axis-review` を Skill tool で invoke する
   - Act on を直してから 2 回目のレビューを行い、**レビューは 2 回で打ち切る** (2 回目は 1 回目の修正が新しい症状を作っていないかを見る回。3 回目以降は収束より消耗が勝つ)。2 回目は `${CLAUDE_SKILL_DIR}/../two-axis-review/SKILL.md` を Read で読み直してから、手順を反証工程まで含めて通しで実行する — compaction で本文が context から落ちていても読み直せば戻るので、落ちているかを自分で判定せずに済む。2 回目の対象差分は 1 回目の指摘へ応えた修正差分に絞る (全差分を再走査すると新規指摘が発散し、その修正がまた未レビューの差分を生む)
   - 2 回目の指摘へ修正で応えたら、その修正は「2 回目後に修正 (未レビュー)」として PR 説明文へ列挙する — 打ち切りの構造上この修正はレビューを通らないので、通っていないことを人が読める形で残す
   - 打ち切り時点の 2 軸レビュー出力を、各節の注記行 (`reviewed:` / `反証対象:` 等) まで含めて**そのまま** PR 説明文へ載せてから PR を出す。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる — 注記行が無いと、Act on のうちどれだけが反証を経たかを読み手が判別できない
   - PR 説明文の節構成は `swat-skills:pr-quality` を Skill tool で invoke して決める
