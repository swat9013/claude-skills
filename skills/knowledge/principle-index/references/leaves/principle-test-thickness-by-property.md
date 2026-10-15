---
name: principle-test-thickness-by-property
description: 対象ごとにテストを書くか、どれだけ厚くするか、薄く保つ対象のテストを何本残すかを決めるときに適用する。所有者・振る舞いの有無・決定的か推論的か・黙って壊れるか・被害の回復性・利用者の範囲で厚さを引く。
---

# テストの厚さは対象の性質で決める

テストの厚さは対象の種類 (skill / hook / script) ではなく性質で決める。性質は限界価値 ÷ 生涯コスト (`principle-test-pruning`) の両方を動かす — 欠陥が黙って起きるほど、他に気づく手段が無いほど、被害が取り消せないほど、そのテストだけが検出手段になって分子が増える。逆に、欠陥が次の commit ですぐ表に出る対象や読めば正しさがわかる対象では、実行と保守のコストが分母を占める。

**上から順に当てはめ、最初に当たった行で決める。**

| # | 対象の性質 | 厚さ |
|---|---|---|
| 1 | 第三者が所有する (vendor したもの) | 書かない。取り込み時の検査 (悪性の兆候・依存の閉包) で守る — テストは upstream の責務 |
| 2 | 宣言だけの設定 (manifest・hook 登録・server 定義の JSON 等) | 書かない。構文・参照・整合の gate か公式 validator で守る — 振る舞いを持たない。設定を読む script のテストは、その script の行で決める |
| 3 | 推論で効く文 (skill 指示文・agent 定義・hook 注入文・MCP tool の名前と説明文) | 言い回しを assert する決定的テストは書かない。挙動を見るなら eval で見る (下の eval の項) |
| 4 | 決定的で、利用者の環境で動き、壊れても黙っており、被害が取り消せないか第三者に届く (破壊的操作を止める検査の素通し・秘匿情報の出力) | 厚く。不変条件・境界値・分類の優先順まで書き、mutation を当てる |
| 5 | 決定的で、利用者の環境で動く (行 4・7 に当たらないもの) | 標準。剪定は `principle-test-pruning` の 3 条件 |
| 6 | 決定的で、開発者しか使わず、欠陥が次の commit / push で表に出る (gate・git hook・開発用 script) | 薄く。ハッピーパス 1 本 + 不変条件ごとの拒否 1 本。誤った値を黙って出す出力にだけ、その値を見るテストを 1 本足す |
| 7 | 決定的で単純 (標準コマンドだけで完結、100 行以内程度)、壊れれば目に見える。開発者しか使わない対象は除く (行 6 へ) | 書かない — 読めば正しさがわかり、保守コストが限界価値を上回る |

- **どの行でも、取り消せない被害を防ぐ不変条件 (安全性の回帰) は残す** — 開発者しか使わない道具の不変条件 (非公開 file を公開しない・作業中の git index を壊さない等) も行 4 ではなくここで残す
- **迷ったら厚い側の行に倒す**。「黙っているか」「取り消せるか」の当てはめは判断が入る
- **行 6 の拒否 1 本は、検査の空回りを捕まえるために要る** — 正のケースだけでは、検査が壊れて全部通しても気づけない
- **重い子プロセス (JVM・本物の git repo・LLM 呼び出し) を起動するテストは、必要な本数までに抑える** — 遅いガードレールは守るはずの fast-feedback を削る

## 推論で効く文の eval

句があることは、LLM がその句どおりに動くことを示さない。言い回しの assert は直すたびに落ちる change-detector になる。

- **下限は skill ごとに代表シナリオ 3 件** (Anthropic の skill authoring best practices の checklist "At least three evaluations created")。それより厚くするかは呼ばれる頻度 × 誤ったときの被害で決める
- **毎変更で回す分は judge を呼ばない grader に限る** — skill が起動したか・どの tool を呼んだか・file ができたか (Claude Code の plugin-evals docs: "give quick every-change suites only graders that don't call a judge")。judge を呼ぶ grader は課金と非決定性を持ち込む
- **1 case は複数回走らせ、skill 無しの baseline と比べる** — 1 回の実行は非決定性で判定できない (同 docs: "One run of a non-deterministic agent tells you little")。skill 無しでも通る case は、その skill が効いていないことを示す

行 4・5 の対象の既存テストを消すかは `principle-test-pruning`、新しく書くテストのレベル配分は `principle-test-level-allocation` が扱う。
