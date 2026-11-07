# 呼び出し口

transcript の観測を呼ぶ手順を書くときの約束。呼び出し元が持つのは次の 3 つだけで、内部の変更はこの 3 つに波及しない。

- **名前**: named query の名前
- **引数**: 観測窓 (`--since` / `--until`)・問いが定める条件・出力 dir の名前 (`--consumer`)
- **返り値**: 成果物の path・行数・`degradation`・`absent_optional_paths`・問いが持つときの `meta`、止まったときの `stopped`・`next_action`・`missing_paths`・`error_type`。ad-hoc の問いは止まらずに `missing_paths` か `snapshot_not_checked` を返す

手順からは Skill tool で `swat-skills:transcript-query` を invoke し、named query の名前と引数を渡す。invoke が返す本文の起動の形に従って問いを実行し、返り値は本文の「返り値の読み方」に従って読む。

手順の本文と `references/` には、問いの名前と返り値の列の名前を書く。script の path・view の名前・SQL・transcript の生の key 名は、書くと形式や view の組み替えのたびに全呼び出し元を直すことになるので、観測の skill の中に置く。

出力 dir の名前は呼び出し元の skill の名前にする。別の呼び出し元の成果物と混ざらず、`/tmp/<consumer>/` が 0700 で作られる。

## 読み方の責務

- 観測は bucket (棚卸しの判定の区分) を確定しない。決定的ルール (機械で評価できる条件) の出力は候補・導出過程・未判定の条件 (`open_predicates`) までで、bucket を確定して採否を決めるのは呼び出し元の側
- 成果物の列の意味と読み方の注記は、成果物の `contract` key が持つ。手順はそこを参照し、読み方を本文へ書き直さない
- 道具の取得に失敗すると返り値の JSON が無いまま終わる。観測を使う分岐だけを止めて理由を報告へ載せ、観測を使わない分岐は続ける。transcript を自前で読む代わりは持たない — 読むと内部の形式に依存する
