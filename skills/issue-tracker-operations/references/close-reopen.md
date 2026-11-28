# close と reopen

issue を close / reopen する手順。理由のコメントの文面・付ける状態の label・止めたときの宛先は呼び出し元の手順が決め、本書は撃つ順だけを持つ。状態の label の定義と付け外しの綴りは `state-label.md` に従う。コマンドは対象 repo の `docs/agents/issue-tracker.md` の CLI 規約に従い、規約が無ければ GitHub (`gh`) の綴りの例を tracker の綴りへ読み替える。

## close

1. 理由やリンクのコメントを投稿するなら、閉じる前に `gh issue comment <N> -R <issue 置き場> --body-file <file>` で投稿する — 閉じた後に投稿が失敗すると、理由の無い closed issue が残る
2. `gh issue close <N> -R <issue 置き場>` で閉じる
3. `gh issue view <N> -R <issue 置き場> --json labels` で付いている状態の label を読み、それを外す。`wontfix` で閉じるなら、`--add-label wontfix` と状態の label の `--remove-label` を 1 回の `gh issue edit <N> -R <issue 置き場>` にまとめる。閉じる前に外すと、間で止まったときに状態の無い open issue が残る。閉じた後なら、止まっても closed issue に古い label が残るだけで済む

`wontfix` を付ける edit が失敗したら、止めて呼び出し元の手順が決めた宛先へ渡す — `wontfix` の無い closed issue は、起票の前の重複探し (open の issue と `wontfix` の closed issue を探す) から漏れる。

## reopen

1. 付ける状態の label を先に付ける (呼び出し元が決めていなければ `needs-triage`)。`gh issue view <N> -R <issue 置き場> --json labels` で付いている label を読み、ほかの状態の label (close の途中で止まって残ったもの) と `wontfix` が付いていれば、同じ `gh issue edit <N> -R <issue 置き場>` で外す — 残すと、開いた issue が状態を 2 つ持つ
2. `gh issue reopen <N> -R <issue 置き場>` で開く

この順にすると、間で止まっても closed issue に状態の label が残るだけで済む。開いてから付けると、間で止まったときに状態の無い open issue が残る。

## 失敗したとき

どの手順の操作 (コメントの投稿・label の edit・close / reopen) が失敗しても、その issue の以降の操作 (コメントの投稿か reopen の手順 1 の失敗なら、閉じることや開くことも) を行わず、`gh issue view <N> -R <issue 置き場> --json state,labels` で読んだ現状と、まだ反映していない操作を、呼び出し元の手順が決めた宛先へ渡す。
