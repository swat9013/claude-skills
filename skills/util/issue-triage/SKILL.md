---
name: issue-triage
description: open issue を読み取りで調査し、label・コメント・close の推奨案を人間の承認を通して tracker へ反映する対話 triage。
disable-model-invocation: true
---

# issue-triage

open issue を全件読み取りで調査し、issue ごとの推奨 (label / コメント / close / 深掘り待ち) を組み立て、人間の承認を通ったものだけを tracker へ反映する。tracker と user の認識を同期させる対話セッションで、深掘りそのもの (grilling) はここでは行わず、深掘り待ち label で可視化して終える — 複数 issue の深掘りを 1 セッションに積むと triage が終わらなくなるため。dispatch の orchestrator が対話系依頼を受けたときの案内先でもある (dispatch 未導入でも単独で動く)。

本文のコマンド例は GitHub 置き場 (`gh`) の綴り。issue 置き場の tracker が別 (GitLab / Jira 等) なら、手順 1 で読んだ CLI 規約の同等コマンドに読み替える。書き込み・読み取りとも issue 置き場を `-R <issue 置き場>` で明示する — cwd 推論に委ねると、cwd と置き場が別 repo の構成で同番号の別 issue に刺さる。

## 前提と読み込み

1. 対象 repo の `docs/agents/issue-tracker.md` (issue 置き場と CLI 規約) と `docs/agents/triage-labels.md` (label 語彙) を Read する。どちらも無い repo では tracker の既存 label の使われ方から規約を読み取り、読み取った前提を報告に書き出す (推測した規約で分類すると、根拠が誰にも再現できない)。label の綴りは表から引く — 表に無い label は発明しない。深掘り待ちの label が宣言されていない repo では、深掘り待ちは label でなく報告で user へ返す (label 新設は user の判断)
2. `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、適用条件が今回の triage に当たる leaf を全部 Read する。以降の手順が `principle-*` を名指ししたら、索引が示す path から Read する — leaf は invoke せず読む

## 調査 (読み取りのみ)

3. open issue を全件引く (`gh issue list --state open --json number,title,labels,updatedAt`)。1 セッションで捌けない件数なら、最初の AskUserQuestion で対象の絞り方 (label / 更新日 / 件数上限) を user に確認してから進む
4. issue ごとに調査する。この段階では tracker に何も書かない:
   - 本文とコメントを逐語で読む (`gh issue view <N> --comments`)
   - 既存 issue・ADR・過去 PR を検索し、重複と既知を潰す。重複していたら分類ではなく統合先を示す
   - 現 main のコードと突き合わせ、既に直っている / 前提が変わっている (stale) かを確かめる
   - `principle-fix-root-causes`: 症状が出ている層より下に原因がありうるかの当たりを付ける。再現・計測の実行が要ると判明したら自分では実行せず、「再現確認タスク」を手順としてコメント案に書き、着手可 label を推奨へ倒す (実行は AFK agent に委ねる — triage は user の対話時間を予約しているので、機械で済む長い作業を混ぜない)
   - 負債として受ける判断には `principle-debt-quadrant` (所在・悪化条件・返済トリガー) を添え、人手でしか解けない項目は `principle-ai-delegation-boundary` に従い「人へ返す」と明示する

## 推奨案の組み立てと承認

5. issue ごとに推奨を 1 つ決め、根拠を 1 行添える。推奨は label 変更 / close / コメントのみ / 深掘り待ち label / 変更なし のいずれか。選択肢を 2〜4 個に絞れない論点を抱えた issue は、選択肢を捏造せず深掘り待ちを推奨にする
6. AskUserQuestion で全 issue 分の承認を取る (1 回 4 問まで)。各設問に推奨と根拠を載せ、user が Other で深掘りを求めた issue は深掘り待ちへ倒す。tracker への書き込み (label / close / コメント) は承認された分だけ行う — 全変更が承認制なのは、機械が勝手に label を動かさないという他セッションからの信頼を守るため

## 反映と報告

7. 承認された操作を反映する: `gh issue edit <N> --add-label/--remove-label` / `gh issue close <N> --comment` / `gh issue comment <N>`。決定と根拠は issue コメントに残す — label だけでは、なぜその分類になったかが次に読む人へ残らない。issue 本文は編集しない
8. 報告して終える。含めるもの: 反映した操作と根拠 / 承認されず据え置いた issue / 深掘り待ち (今回付けた分と既存の残数) / user に残る作業。深掘り待ちの消化はこの skill の外 — user が任意のセッションで grill 系 skill を呼び、固まった内容の issue への反映 (コメント追記・着手可 label 化・深掘り待ち label の除去) をそのセッションに指示する

## 実行環境

- tracker への書き込みは AskUserQuestion の承認を通った分だけ。issue 本文は編集しない
- repo への write は行わないので、変更の届け方の判断は発生しない
