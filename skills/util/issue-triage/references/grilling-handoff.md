# 深掘りのセッションへの手順コメント

`needs-grilling` にする issue へ投稿する手順コメントの雛形。作るのは issue-triage (区分 4 (判断)。4 (人手) には作らない) と issue-restructure (根本課題 issue)、投稿するのは `needs-grilling` を付ける呼び出し元。深掘りは user が別セッションで `/swat-skills:grill-with-docs` を起動して行う。雛形の `<M…>` は close したメンバーの番号で埋めて出す。

- 分類根拠のコメントとは別のコメントにする — `ready-for-agent-review` は範囲を決めたコメントを検査対象にするので、手順が混ざると検査対象との境目が曖昧になる
- 1 行目は雛形の固定の文字列のまま変えない。読んだその issue のコメントに 1 行目が同じコメントが既にあれば、手順コメントを作らない — 既に `needs-grilling` の issue へ重複して投稿しない
- 「(再構成のときだけ)」の行は、再構成でなければ行ごと消し、再構成なら接頭辞だけを消す
- `needs-grilling` に当たる label が無い repo では投稿せず、埋めたものを最後の報告に逐語で書く

```
深掘りのセッションへの手順 (issue の仕様ではない)

この issue は深掘りを待っています。`/swat-skills:grill-with-docs` を起動し、この issue を渡して固めてください。

(再構成のときだけ) close したメンバー #<M1>, #<M2>, … は、この issue に束ねたもの。固める中で束ね方が覆ったら、外れるメンバーを reopen して `needs-triage` を付ける (綴りは対象 repo の `docs/agents/triage-labels.md` に従う)
```
