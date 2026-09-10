---
name: playbook-test-optimization
description: テストスイートの 4 軸診断を、原則の索引 Read から適用判断の返却までの番号付き step で通す playbook。
disable-model-invocation: true
---

# テスト戦略・設計最適化の playbook

既存テストスイートを 4 軸 (レベル配分 / ダブル境界 / リファクタリング耐性 / 剪定候補) で診断し、最適化案を判断材料として渡すまでの手順。step の中でどの skill と原則を使うかを名前で置いてある。テストの書き換え・削除 (適用) は担当外。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-test-optimization` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step は逐語で todolist へ写す。** 要約・統合・並べ替えをしない
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない
- 条件付きの step には条件が書いてある。条件に当たらないなら、その条件を `skip:` の理由に書く

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../principle-index/SKILL.md` を Read し、適用条件が今回の作業に当たる leaf を全部 Read する。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。**以降の step が `principle-*` を名指ししたら、索引が示す path から Read する** — leaf は invoke せず読む。

2. 対象と完了条件を確定する。依頼で対象パスが指定されていればそれを、無ければ repo のテストスイート全体を対象にする。完了条件は「4 軸すべての診断結果と最適化案が具体物 (`path:line`) 付きでレポートに言明され、適用の判断が依頼元へ返っていること」— テストコードを書き換え始めたら本 playbook の外に出ている。

3. 判断基準を手元に置く。`swat-skills:test-strategy` を Skill tool で invoke する。以降の監査はこの規則集と step 1 の leaf に照らして判定する — 本 playbook は基準を内蔵していないので、この step を飛ばすと各軸の判定線が手元に来ない。

4. スイートの現状を観測する。テストの層別 (単体 / 統合 / e2e 相当) の分布、mock・patch の使用箇所、テストの実行手段を repo の実体 (test script / CI 設定 / runner 設定) から読み取る。カバレッジの計測手段が repo にあれば計測する。`principle-fail-loudly`: 計測できなかったもの・読み取れなかったものは「できなかった」とレポートに残す (黙って落とすと、観測していない軸が観測済みに見える)。

5. 4 軸で監査する。指摘には具体物 (`path:line` と、どの規則に照らしたか) を添え、具体物の無い指摘は出さない。
   - **レベル配分**: `principle-test-level-allocation` — 投資配分のずれ (委譲だけの glue code への単体テスト集中、ユースケースを検査する自動テストの不在)
   - **ダブル境界**: `principle-test-double-boundary` — managed dependency の mock、時刻・乱数・環境変数の非注入
   - **リファクタリング耐性**: `principle-observable-behavior` — 実装詳細 (内部の呼び出し順・private 状態・中間データ構造) への assert
   - **剪定候補**: `principle-test-pruning` — 候補には限界価値がゼロに見える根拠と、適用側が確定判定に使う検証手順 (剪定前後のカバレッジ 2 点比較等) を添える。確定判定そのものは適用側の作業

6. レポートを書く。軸ごとに指摘と最適化案を列挙し、指摘の無かった軸は「問題なし」と明記する (見なかったのか無かったのかを区別する)。実行時間・並列化の問題を見つけていたら、診断せず「`/swat-skills:playbook-test-perf-tuning` の領分」として節を分けて列挙する。置き場は実行 project の doc 規約に従い、定めが無ければ gitignore 済みの作業ディレクトリへ書き、それも無ければレポート全文を応答で返す。

7. PR 到達前に 2 軸レビューを通す。条件: 診断結果を repo へ commit するとき。read-only で終わり差分が出ないなら `skip: 差分なし`。
   - `swat-skills:two-axis-review` を Skill tool で invoke する
   - Act on を直してから 2 回目のレビューを行い、**レビューは 2 回で打ち切る** (2 回目は 1 回目の修正が新しい症状を作っていないかを見る回。3 回目以降は収束より消耗が勝つ)。2 回目は `${CLAUDE_SKILL_DIR}/../two-axis-review/SKILL.md` を Read で読み直してから、手順を反証工程まで含めて通しで実行する — compaction で本文が context から落ちていても読み直せば戻るので、落ちているかを自分で判定せずに済む
   - 打ち切り時点で残った Act on と、反証で落ちた Dismissed を**具体物ごと** PR 説明文へ載せてから PR を出す。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる
   - PR 説明文の節構成は `swat-skills:pr-quality` を Skill tool で invoke して決める

8. 適用の判断を依頼元へ返す。最適化案を適用するかの判断と実行は依頼元のフロー (人 / triage) に委ねる — レポートがそのまま適用作業の判断材料になる形 (具体物と検証手順付き) で渡っていることを確かめる。
