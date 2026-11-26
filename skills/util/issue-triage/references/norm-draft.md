# 規範の文案 (区分 2)

issue-triage の SKILL.md §4 から、区分 2 の issue があるときに読む。

先に、同じ規範の文案を待つ issue を探す。`gh issue list -R <issue 置き場> --state open --label ready-for-human --limit 1000 --json number,comments` の出力を file に受ける。issue-triage の dir の下の `scripts/issue-list.py norm-draft <受けた file> --path <置き場の path> --limit 1000` で、行頭が `規範の文案: <置き場の path>` のコメントを持つ issue を拾う (検索 qualifier は日本語の語句を取りこぼすので、コメントを照らす)。exit 2 の stderr の理由が件数なら `--limit` を上げて引き直し、ほかの理由なら同じコマンドで 1 回取り直し、また返れば文案を書かずに停止として返す。gh 以外の tracker では、script を通さずに受けた file のコメントを同じ規則で照らす。番号が返れば文案を書き直さず、その issue の番号を返す。同じ実行の中で複数の issue が同じ path の文案を要するときも、文案は args に並んだ順で最初の issue のブロックに 1 本だけ書き、その 1 本に、同じ path を要する issue それぞれに足りない一般則をすべて入れる。残りの issue のブロックには最初の issue の番号を書く — 別々に書くと、人が 1 本を採った後に残りの文案が食い違ったまま残る。

どちらにも当たらなければ、足りない一般則を書き込む先の path と、規範の文面を逐語で書く。返すときは 1 行目を `規範の文案: <置き場の path>` にし、path の後ろには何も書かない (書き込む節の見出しや説明は 2 行目から書く) — 呼び出し元がこの行を `ready-for-human` の issue へ投稿すると、次の triage がこの file の前段の検索で見つける。
