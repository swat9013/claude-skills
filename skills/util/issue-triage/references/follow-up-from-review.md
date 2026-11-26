# レビューの文案からの follow-up issue

issue-triage の SKILL.md §5 の 2 から、§3 で follow-up issue の文案を控えたときに読む。

この起票は repo の issue の規範の起票の節ではなく、ここの手順に従う。題名は `<負けた規範の path>: <文案の題名>` にする — 文案の題名は呼ぶたびに書かれて揺れるので、重複は path で照らす。SKILL.md §1 で受けた題名の一覧の file に `scripts/issue-list.py title-prefix <一覧の file> --path <負けた規範の path>` を当て、同じ `<負けた規範の path>:` で始まる issue を探す。あれば起票せずに発端 (この issue の番号) をコメントで足す。無ければ本文を文案どおりに file に書き (受け入れ条件の無い issue は次の検査で差し戻される)、`scripts/issue-list.py origin-line <本文の file> --issue <この issue の番号>` で、文案に `発端:` の行が無いときだけ末尾に独立した 1 行 `発端: #<この issue の番号>` を足す (`body-rewrite.md` の「書き換え案に残す行」の置き方)。`gh issue create -R <issue 置き場> --title <題名> --body-file <file> --label needs-triage` で起票し、`scripts/issue-list.py append <一覧の file> --number <起票した番号> --title <題名>` で一覧の file に追記する (同じ実行の続く issue で二重に起票しない)。`scripts/` は issue-triage の dir の下にある。script はどれも gh の形の file を読み、exit 2 で終えたら起票せず、stderr の理由を添えて停止として返す。gh 以外の tracker では、script を通さずに一覧の file と文案を同じ規則で照らし、手で追記する
