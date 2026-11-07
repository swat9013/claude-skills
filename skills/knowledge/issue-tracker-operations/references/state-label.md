# 状態の label の付け替え

issue の状態を表す label (状態の label) を付け替える手順。裁いた行き先の label と付け替えで外す label・止めたときの宛先は呼び出し元の手順が決め、本書は付け替え方だけを持つ。label は名前で書いてあり、対象 repo の `docs/agents/triage-labels.md` が別の綴りを宣言していればその綴りで付け外しする。コマンドは対象 repo の `docs/agents/issue-tracker.md` の CLI 規約に従い、規約が無ければ GitHub (`gh`) の綴りの例を tracker の綴りへ読み替える。

- **状態の label** は `needs-triage`・`needs-info`・`needs-grilling`・`ready-for-agent`・`ready-for-human` で (`needs-grilling` に当たる label が無い repo では残りの 4 つ)、open の issue はちょうど 1 つを持つ。例外は issue を閉じる open CL があり、`ready-for-agent` を外した後の issue で、状態の label を持たないのが正しい (状態は open CL で表す) — ここに状態の label を付け足すと、実装中の issue が triage に戻る。種別の label (`tracking-issue` など) は状態の label ではないので、本書の付け替えでは付け外ししない
- **付け替えは 1 回の操作で行う。** 外す label と付ける label を `gh issue edit <N> -R <issue 置き場> --add-label <付ける label> --remove-label <外す label>` の 1 回にまとめる — 2 回に分けると、間で止まったときに状態の無い issue か、状態を 2 つ持つ issue が残る。付ける label が既に唯一の状態の label なら撃たない
- **`ready-for-agent` を付けるのは、規範に照らした検査を通した本文に限る。** `ready-for-agent` は「本文をそのまま実装してよい」という保証で、実装する側は本文を検査し直さずに着手する。検査の後に本文を書き換えたら、書き換えた本文で検査を通すまで付けない
- **本文を書き換えずに `ready-for-agent` で終えるときは、付け替えの前に本文を比べる。** 付けるときも、既に唯一の状態の label で撃たないときも同じ。基準は呼び出し元が保存した、検査した本文の取得で、それに `body-rewrite.md`「書き戻し」の手順 2 (比べる) を当てる。食い違えば `ready-for-agent` を付けずに止め、呼び出し元の手順が決めた宛先へ返す。既に付いていたなら、止める前に、`ready-for-agent` を外して呼び出し元の手順が決めた状態の label を付ける付け替えを 1 回で撃つ — 検査の後に他の書き手が本文を書き換えていれば、検査を通っていない本文に `ready-for-agent` が付くか、付いたまま残る。本文を書き換えるときは、同書の手順 2 と手順 5 がこの比較に当たる
- **本文の書き換えと一緒に付け替えるときは、`body-rewrite.md` の「書き戻し」が決める位置で行う。** 外す label は手順 1 で外し、`ready-for-agent` は手順 5 の読み直しの後に付ける
- **付け替えが失敗したら**、`gh issue view <N> -R <issue 置き場> --json labels` で label の現状を読み、現状と意図した付け替えを呼び出し元の手順が決めた宛先へ渡す — 呼び出し元は付け替えが済んだものとして先へ進まない。状態の label がちょうど 1 つでなければ、そのことも添える
