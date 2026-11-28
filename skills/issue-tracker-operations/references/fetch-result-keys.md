# issue の取得結果の鍵

issue を本文とコメントまで JSON で取得したとき、読む値がどの鍵にあるかの対応。鍵の名前と形は tracker の CLI ごとに違うので、取得結果を読む・検査する・切り出すときは、その形の列で鍵を引く。

gh と glab では、取得コマンドの組み立て・取得結果の file の検査・共通の鍵への正規化・本文の切り出しを `../scripts/issue-fetch.py` が担い、この表はその鍵の対応の正本になる。表の鍵は「取得に要る指定」で出した JSON にだけある。script の `--tracker` に渡す tracker は、対象 repo の `docs/agents/issue-tracker.md` の CLI 規約で決める。規約が無ければ issue 置き場の host で決める (GitHub なら gh、GitLab なら glab)。取得結果の file がどの形かは鍵で見分ける: `number` があれば gh の形、`iid` があれば glab の形。

| 読む値 | gh の形 | glab の形 |
|---|---|---|
| 取得に要る指定 | `--json number,title,body,labels,comments,closedByPullRequestsReferences,state,url` (依存先が要るときは `blockedBy` も足す — script では `command --with-blocked-by`。古い gh は `blockedBy` を知らず取得が落ちる) | `--comments --output json` |
| 番号 | `number` | `iid` (`id` は instance 全体の通し番号で、issue の番号ではない) |
| 題名 | `title` | `title` |
| 本文 | `body` | `description` |
| label | `labels[].name` | `labels[]` (label 名の文字列の配列) |
| コメント | `comments[]` | `Notes[]` のうち `system` が `false` のもの |
| コメントの本文 | `comments[].body` | `Notes[].body` |
| コメントの author | `comments[].author.login` | `Notes[].author.username` |
| コメントの日時 | `comments[].createdAt` | `Notes[].created_at` |
| 状態 | `state` (`OPEN` / `CLOSED`。`MERGED` は番号が PR を指したことを示し、script は `closed` と読む。ほかの値は null) | `state` (`opened` / `closed`) |
| URL | `url` (番号が PR を指したときは `/pull/` を含む) | `web_url` |
| 依存先 (この issue を塞ぐ issue) | `blockedBy.nodes[].url` / `blockedBy.nodes[].state` (`blockedBy` を足して取得したときだけ) | 鍵なし (依存先が無いことを示さない) |
| issue を閉じる CL | `closedByPullRequestsReferences` | 鍵なし (閉じる CL の見つけ方は `body-rewrite.md` の「open CL の確かめ」に従う) |

- gh はコメントを `--json` の `comments` で取る — `--comments` を付けると、非 TTY 実行ではコメントだけを出力し、本文が黙って抜ける
- glab は `--comments` を付けたときだけ `Notes` を出す。付けずに取得した結果はコメントを欠くので、取得し直す。`Notes` が `null` か空の配列なら、コメントは 0 件。note は `--per-page` の件数ずつ全 page を引いて返るので、`Notes` は途中で切れない
- `system` が `true` の note は、label の付け替えや状態の変更を tracker が書いた記録で、コメントではない — コメントに数えると、コメントの区分や新しい情報の有無を tracker の記録で判定する
- 表に無い tracker は、対象 repo の `docs/agents/issue-tracker.md` の CLI 規約にある issue を読むコマンドで取得し、その出力から表の行の値を読む
