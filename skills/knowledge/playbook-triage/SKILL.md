---
name: playbook-triage
description: issue の triage を、原則の索引 Read から tracker への記入までの番号付き step で通す playbook。
disable-model-invocation: true
---

# triage の playbook

issue を読んで分類し、着手可否と担当の境界を決めて tracker へ書き戻すまでの手順。step の中でどの skill と原則を使うかを名前で置いてある。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-triage` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step は逐語で todolist へ写す。** 要約・統合・並べ替えをしない
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない
- 条件付きの step には条件が書いてある。条件に当たらないなら、その条件を `skip:` の理由に書く

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../principle-index/SKILL.md` を Read し、適用条件が今回の作業に当たる leaf を全部 Read する。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。**以降の step が `principle-*` を名指ししたら、索引が示す path から Read する** — leaf は invoke せず読む。

2. 対象と分類規約を確定する。対象 issue の本文を逐語で読む。label 体系と triage 規約は対象 repo の `docs/agents/triage-labels.md` を Read する。その doc が無い repo では tracker の既存 label の使われ方から読み取り、読み取った規約を判断の前提として書き出す (推測した規約で分類すると、根拠が誰にも再現できない)。

3. 重複と既知を潰す。既存 issue・ADR・過去の PR を検索し、同じ問題が既に記録されていないか確かめる。重複していたら分類ではなく統合先を示す。

4. 根本原因の当たりを付ける。`principle-fix-root-causes`: 症状だけを消す回避策を「対応済み」に数えない。再現条件と、症状が出ている層より下に原因がありうるかを書く。

5. 分類と着手可否を決める。判断 1 つにつき根拠を 1 行添える。負債として受けると決めたら `principle-debt-quadrant` に従い所在・悪化条件・返済トリガーを書く (記録の無い負債は許容範囲の外)。人手でしか解けない項目は `principle-ai-delegation-boundary` に従って「人へ返す」と明示し、停止理由・そこまでの成果・次に人が実行する手順を添える。

6. tracker へ書き戻す。label と comment の両方を書く。根拠は comment に残す — label だけでは、なぜその分類になったかが次に読む人に残らない。

7. PR 到達前に 2 軸レビューを通す。条件: triage の過程で repo に差分が出たとき (規約 doc の更新など)。label と comment だけで終わったなら `skip: 差分なし` (既定はこちら)。
   - `swat-skills:two-axis-review` を Skill tool で invoke する
   - Act on を直してから 2 回目のレビューを行い、**レビューは 2 回で打ち切る** (2 回目は 1 回目の修正が新しい症状を作っていないかを見る回。3 回目以降は収束より消耗が勝つ)。2 回目は `${CLAUDE_SKILL_DIR}/../two-axis-review/SKILL.md` を Read で読み直してから、手順を反証工程まで含めて通しで実行する — compaction で本文が context から落ちていても読み直せば戻るので、落ちているかを自分で判定せずに済む
   - 打ち切り時点で残った Act on と、反証で落ちた Dismissed を**具体物ごと** PR 説明文へ載せてから PR を出す。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる
   - PR 説明文の節構成は `swat-skills:pr-quality` を Skill tool で invoke して決める
