# 深掘りのセッションへの手順コメント

`needs-grilling` にする issue へ投稿する手順コメントの雛形。作るのは issue-triage (区分 4) と issue-restructure (根本課題 issue)、投稿するのは `needs-grilling` を付ける呼び出し元。深掘りは user が別セッションで grill 系 skill を呼んで行い、そのセッションは triage 系 skill を読まない。手順がこのコメントだけで閉じるように、雛形の `<…>` はすべて実値で埋めて出す: `<M…>` は close したメンバーの番号、`<grill-with-docs の SKILL.md の絶対 path>` と `<body-rewrite.md の絶対 path>` は、本 file を Read させた SKILL.md が渡す path を、そこに見えている絶対 path のまま書く。tracker が GitHub でなければ、コマンドもその tracker の綴りへ置き換えて埋める。

- 分類根拠のコメントとは別のコメントにする — `ready-for-agent-review` は範囲を決めたコメントを検査対象にするので、手順が混ざると検査対象との境目が曖昧になる
- 1 行目は雛形の固定の文字列のまま変えない。読んだその issue のコメントに 1 行目が同じコメントが既にあれば、手順コメントを作らない — 既に `needs-grilling` の issue へ重複して投稿しない
- 「(再構成のときだけ)」の行は、再構成でなければ行ごと消し、残りの番号を詰める
- `needs-grilling` に当たる label が無い repo では投稿せず、埋めたものを最後の報告に逐語で書く

```
深掘りのセッションへの手順 (issue の仕様ではない)

この issue は深掘りを待っています。grill 系 skill で固めるセッションは、次の手順に従ってください。

1. 決定が出るたびと、agent の推奨や判断を覆したたびに、`<grill-with-docs の SKILL.md の絶対 path>` の「決まった判断を規範へ codify する」節を Read して従う
2. (再構成のときだけ) close したメンバー #<M1>, #<M2>, … は、この issue に束ねたもの。固める中で束ね方が覆ったら、外れるメンバーを reopen して `needs-triage` を付ける (綴りは対象 repo の `docs/agents/triage-labels.md` に従う)
3. 本文を読んだ時点で、`<body-rewrite.md の絶対 path>` の「基準の保存」に従って本文を基準として保存する
4. 固まった内容 (user が承認した文面だけ) を、この issue 本文の書き換え案にまとめる。本文での決定の扱いは、手順 1 の節の「対象が issue なら」の項に従う
5. issue と書き換え案と基準の path を `swat-skills:issue-triage` に渡す。内側と判定されれば、書き戻し・検査・`ready-for-agent` の付与まで issue-triage が行う。この issue を実装している open CL があれば書き戻されずに返るので、CL の差し戻しは user に問う
```
