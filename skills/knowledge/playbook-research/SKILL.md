---
name: playbook-research
description: 調査作業を、原則の索引 Read から成果物の受け渡しまでの番号付き step で通す playbook。
disable-model-invocation: true
---

# 調査の playbook

問いを立てて一次ソースに当たり、判断材料として渡すまでの手順。step の中でどの skill と原則を使うかを名前で置いてある。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-research` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step は逐語で todolist へ写す。** 要約・統合・並べ替えをしない
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない
- 条件付きの step には条件が書いてある。条件に当たらないなら、その条件を `skip:` の理由に書く

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../principle-index/SKILL.md` を Read し、適用条件が今回の作業に当たる leaf を全部 Read する。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。**以降の step が `principle-*` を名指ししたら、索引が示す path から Read する** — leaf は invoke せず読む。

2. 問いと完了条件を確定する。何が分かれば調査を終えてよいかを 1 文で書く。答えの形 (決定の材料 / 事実の確認 / 選択肢の比較) を先に決める — 形が決まらないまま集めた事実は、どれだけ集まっても終われない。

3. 一次ソースに当たる。公式 doc・実装コード・実測を根拠に採り、二次情報は一次ソースへ辿り直す。複数ソースの横断や最新性の確認 (バージョン・非推奨化・破壊的変更) は `swat-skills:web-research` subagent へ回す。単一 URL の単発 fetch は自分で取る。

4. 実測と推測を分けて記録する。断定には出典 (`path:line` / URL / 実行したコマンドと出力) を添え、出典が付かないものは推測として区別する。`principle-fail-loudly`: 到達できなかったソースは「到達できなかった」と書いて残す (黙って落とすと、調べていない範囲が調べた範囲に見える)。

5. 成果物を書く。`principle-artifact-register` で成果物の役割 (調査レポート / 決定の材料 / 蒸留版) を先に決めてから書式を決める — 役割ごとに規範は逆になる。置き場は対象 repo の doc 規約に従う。

6. 判断を依頼元へ返す。拮抗した選択肢は自分で確定せず、トレードオフを添えて返す。人手でしか解けない項目が残ったら `principle-ai-delegation-boundary` に従い、停止理由・そこまでの成果・次に人が実行する手順を添える (無言で終わらせない)。

7. PR 到達前に 2 軸レビューを通す。条件: 調査結果を repo へ commit するとき。read-only で終わり差分が出ないなら `skip: 差分なし`。
   - `swat-skills:two-axis-review` を Skill tool で invoke する
   - Act on を直してから 2 回目のレビューを行い、**レビューは 2 回で打ち切る** (2 回目は 1 回目の修正が新しい症状を作っていないかを見る回。3 回目以降は収束より消耗が勝つ)。2 回目は `${CLAUDE_SKILL_DIR}/../two-axis-review/SKILL.md` を Read で読み直してから、手順を反証工程まで含めて通しで実行する — compaction で本文が context から落ちていても読み直せば戻るので、落ちているかを自分で判定せずに済む
   - 打ち切り時点で残った Act on と、反証で落ちた Dismissed を**具体物ごと** PR 説明文へ載せてから PR を出す。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる
   - PR 説明文の節構成は `swat-skills:pr-quality` を Skill tool で invoke して決める
