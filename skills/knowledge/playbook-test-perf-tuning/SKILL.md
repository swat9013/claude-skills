---
name: playbook-test-perf-tuning
description: テスト実行時間の短縮を、原則の索引 Read から 2 軸レビュー通過までの番号付き step で通す playbook。
disable-model-invocation: true
---

# テスト時間チューニングの playbook

テストスイートの実行時間を実測し、green と短縮を実測確認できた変更だけを残すまでの手順。step の中でどの skill と原則を使うかを名前で置いてある。変更影響ベースの selective testing と CI 基盤側の最適化は提案止まりで、適用は担当外。

## 使い方

入口は 2 つあり、どちらも届くのは本文 1 枚だけ。人が `/swat-skills:playbook-test-perf-tuning` で開くか、spawn prompt から本 file の path を Read して開く。Read で開いたときは本文の `${CLAUDE_SKILL_DIR}` が literal のまま見えるので、本 file のディレクトリと読み替えて path を解決する。

- **step は逐語で todolist へ写す。** 要約・統合・並べ替えをしない
- **実行しない step も list に残し、`skip: <理由>` を 1 行付ける。** 黙って飛ばすと、飛ばしたこと自体が誰にも見えない
- 条件付きの step には条件が書いてある。条件に当たらないなら、その条件を `skip:` の理由に書く

## step

1. 原則の索引を Read する。`${CLAUDE_SKILL_DIR}/../principle-index/SKILL.md` を Read し、適用条件が今回の作業に当たる leaf を全部 Read する。本 playbook は索引を内蔵していないので、この step を飛ばすと原則が 1 つも手元に来ない。**以降の step が `principle-*` を名指ししたら、索引が示す path から Read する** — leaf は invoke せず読む。

2. 対象と基準コマンドを確定する。依頼で対象パスが指定されていればそれを、無ければテストスイート全体を対象にする。テストの実行コマンドを repo の実体 (test script / CI 設定 / runner 設定) から発見し、どれを計測の基準に選んだかを明示してから計測に入る — 人の返答は待たないが、比較の前提を後から監査できる形で先に残す。**以降の 2 点比較はすべて同じコマンド・同じ環境で行う** (条件の変わった計測は比較にならない)。完了条件は「基準時間と変更後時間が同条件の実測で対に記録され、green と短縮を実測確認できた変更だけが作業ツリーに残っていること」。

3. baseline を実測する。既存の計測データ (CI の時間ログ・junit.xml・`--durations` 出力等) があれば流用し、無ければ基準コマンドで実測する。runner の計時機能 (pytest なら `--durations` 等) で遅いテスト・遅い setup の上位を特定する。`principle-verify-the-real-artifact`: 実測の無いまま推測で手を入れ始めたら、この step へ戻る。

4. 既存設定の意図を確かめる。直列実行・並列化の除外・timeout 等の現行設定には理由がありうる — コメント・docs・commit log から意図を読み、意図が読み取れた設定は変更対象から外して「意図あり」としてレポートへ回す。変更候補に入れるのは意図の読み取れなかった設定だけ。

5. 個別最適化を適用する。条件: step 3 で遅いテストを特定できたとき。上位から、setup の重複 (fixture の生成回数・scope)、実時間待ち (sleep・実時刻依存 — `principle-test-double-boundary` の注入で置換)、不要な I/O を直す。1 変更ごとに step 7 の実測確定 (green と短縮) を通してから次の変更へ進む — 複数の変更を混ぜると、どれが効いたか・どれが壊したかを実測で切り分けられない。

6. 並列化を導入・調整する。条件: runner に並列化手段があるとき。先にテスト間の共有状態 (共有ファイル・共有 DB・共有 env — `principle-isolate-shared-writes`) を確かめ、共有の残るテストは並列化の対象から外す。導入済みの repo では worker 数・分配方式の調整だけを見る。

7. 変更を実測で確定する。本 step は step 5・6 の各変更に繰り返し適用し、todolist 上は最後の変更の確定をもって完了とする。1 変更ごとに、基準コマンドで green と時間短縮を実測する。並列化に触れた変更は直列と並列の両方で green を確認する (直列でだけ通るテストは並列化が壊した状態共有の症状)。どちらかが確認できない変更は revert する — `principle-verify-the-real-artifact`: 「たぶん速くなった」を作業ツリーに残さない。

8. レポートを書く。基準と結果の対 (コマンド・実測時間) を逐語で記録し、適用した変更と revert した変更 (revert の理由付き) を列挙する。適用範囲の外に置いたもの — selective testing、CI 基盤側の最適化 (キャッシュ・シャーディング) — は提案として列挙し、step 4 で「意図あり」とした設定は読み取った意図付きの判断材料として列挙する (変更提案ではない)。テスト設計側の問題 (レベル配分・実装詳細への結合・剪定候補) を見つけていたら、診断せず「`/swat-skills:playbook-test-optimization` の領分」として節を分けて列挙する。置き場は実行 project の doc 規約に従い、定めが無ければ gitignore 済みの作業ディレクトリへ書き、それも無ければレポート全文を応答で返す。

9. PR 到達前に 2 軸レビューを通す。条件: 変更が作業ツリーに残っているとき。全変更を revert して差分が出ないなら `skip: 差分なし`。
   - `swat-skills:two-axis-review` を Skill tool で invoke する
   - Act on を直してから 2 回目のレビューを行い、**レビューは 2 回で打ち切る** (2 回目は 1 回目の修正が新しい症状を作っていないかを見る回。3 回目以降は収束より消耗が勝つ)。2 回目は `${CLAUDE_SKILL_DIR}/../two-axis-review/SKILL.md` を Read で読み直してから、手順を反証工程まで含めて通しで実行する — compaction で本文が context から落ちていても読み直せば戻るので、落ちているかを自分で判定せずに済む
   - 打ち切り時点で残った Act on と、反証で落ちた Dismissed を**具体物ごと** PR 説明文へ載せてから PR を出す。この記録は人が PR review で読んで覆すために置くもので、自分の指摘を自分で落とす構造の唯一の歯止めになる
   - PR 説明文の節構成は `swat-skills:pr-quality` を Skill tool で invoke して決める
