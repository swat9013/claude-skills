# issue の取得結果の鍵

issue を本文とコメントまで JSON で取得したとき、読む値がどの鍵にあるかの対応。鍵の名前と形は tracker の CLI ごとに違うので、取得結果を読む・検査する・切り出すときは、その形の列で鍵を引く。

取得のコマンドは、対象 repo の `docs/agents/issue-tracker.md` の CLI 規約にある issue を読むコマンドを使い、下の表の「取得に要る指定」が欠けていれば足して撃つ — 表の鍵は、この指定で出した JSON にだけある。規約が無ければ、GitHub (`gh`) の綴りの例 (`gh issue view <N> -R <issue 置き場>`) を tracker の綴りへ読み替え、その tracker の列の指定を足す。取得結果の file がどの形かは鍵で見分ける: `number` があれば gh の形、`iid` があれば glab の形。

| 読む値 | gh の形 | glab の形 |
|---|---|---|
| 取得に要る指定 | `--json number,title,body,labels,comments` | `--comments --output json` |
| 番号 | `number` | `iid` (`id` は instance 全体の通し番号で、issue の番号ではない) |
| 題名 | `title` | `title` |
| 本文 | `body` | `description` |
| label | `labels[].name` | `labels[]` (label 名の文字列の配列) |
| コメント | `comments[]` | `Notes[]` のうち `system` が `false` のもの |
| コメントの本文 | `comments[].body` | `Notes[].body` |
| コメントの author | `comments[].author.login` | `Notes[].author.username` |
| コメントの日時 | `comments[].createdAt` | `Notes[].created_at` |
| issue を閉じる CL | `closedByPullRequestsReferences` (`--json` に足したとき) | 鍵なし (閉じる CL の見つけ方は `body-rewrite.md` の「open CL の確かめ」に従う) |

- gh はコメントを `--json` の `comments` で取る — `--comments` を付けると、非 TTY 実行ではコメントだけを出力し、本文が黙って抜ける
- glab は `--comments` を付けたときだけ `Notes` を出す。付けずに取得した結果はコメントを欠くので、取得し直す。`Notes` が `null` か空の配列なら、コメントは 0 件。note は `--per-page` の件数ずつ全 page を引いて返るので、`Notes` は途中で切れない
- `system` が `true` の note は、label の付け替えや状態の変更を tracker が書いた記録で、コメントではない — コメントに数えると、コメントの区分や新しい情報の有無を tracker の記録で判定する
- 表に無い tracker は、CLI 規約のコマンドの出力から表の行の値を読む
