---
name: playbook-plan-verification
description: 計画・spec の検証を、原則の索引 Read から判定の差し戻しまでの番号付き step で通す playbook。
disable-model-invocation: true
---

# 計画検証の playbook

計画・spec・ADR 案が実装可能で正本と整合しているかを検証し、判定を返すまでの手順。step の中でどの skill と原則を使うかを名前で置いてある。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-plan-verification` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step は逐語で todolist へ写す。** 要約・統合・並べ替えをしない
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない
- 条件付きの step には条件が書いてある。条件に当たらないなら、その条件を `skip:` の理由に書く

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../principle-index/SKILL.md` を Read し、適用条件が今回の作業に当たる leaf を全部 Read する。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。**以降の step が `principle-*` を名指ししたら、索引が示す path から Read する** — leaf は invoke せず読む。

2. 検証対象と正本を確定する。計画本文を逐語で読み、それが依拠している正本 (ADR・仕様 doc・既存実装) を列挙する。正本を特定できない主張は、この時点で検証不能として記録する。

3. 前提の実在を確かめる。計画が名指しした path・skill 名・tool・設定・節番号を 1 件ずつ `ls` / Read / 検索で確かめる。実在しない前提は計画側の欠陥であって、実行者が現場で埋める余地ではない。引用された決定記録の status が現行か (Superseded / Deprecated を根拠にしていないか) もここで見る。

4. 受け入れ条件が観測可能か確かめる。`principle-observable-behavior`: 「〜を考慮する」形の条件は、何を見れば満たしたと言えるかへ書き直せるかを問う。実行者の作業ツリーの外にある実体を触る条件は実行者が満たせないので、担当を分けて書き出す。

5. 反証を当てる。計画が「できる」としている step を 1 つずつ取り、できない具体物 (`path:line` / 実行結果 / 矛盾する正本の引用) を探す。**具体物の無い疑義は反証として採らない** — 「うまくいかない気がする」は差し戻しの根拠にならない。

6. 判定を書いて返す。「通す」「直してから通す」「差し戻す」の 3 分類にし、分類ごとに根拠となった具体物を添える。拮抗する設計判断は自分で確定せず、トレードオフを添えて依頼元へ返す (`principle-ai-delegation-boundary`: 停止理由・そこまでの成果・次に人が実行する手順を添える)。

7. PR 到達前に 2 軸レビューを通す。条件: 計画 doc の修正や検証レポートを repo へ commit するとき。判定を返すだけで差分が出ないなら `skip: 差分なし`。
   - `swat-skills:two-axis-review` を Skill tool で invoke する
   - Act on を直してから 2 回目のレビューを行い、**レビューは 2 回で打ち切る** (2 回目は 1 回目の修正が新しい症状を作っていないかを見る回。3 回目以降は収束より消耗が勝つ)。2 回目は `${CLAUDE_SKILL_DIR}/../two-axis-review/SKILL.md` を Read で読み直してから、手順を反証工程まで含めて通しで実行する — compaction で本文が context から落ちていても読み直せば戻るので、落ちているかを自分で判定せずに済む
   - 打ち切り時点で残った Act on と、反証で落ちた Dismissed を**具体物ごと** PR 説明文へ載せてから PR を出す。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる
   - PR 説明文の節構成は `swat-skills:pr-quality` を Skill tool で invoke して決める
