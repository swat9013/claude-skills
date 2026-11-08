# Spec 源の issue の置き場を決める

issue 置き場は `${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/references/placement.md` を Read して ref ごとに決める — 置き場を決めずに番号だけで引くと、CLI が clone の remote から repo を推すので、置き場が別 repo のとき同じ番号の別 issue を読む。commit message と PR 本文の ref は、同 file の「置き場をまたぐ参照の書き方」の読み方で読む (commit message はレビュー対象 repo に、PR 本文はその PR が載った置き場に載った文面)。`issue=` の ref はどの置き場にも載らないので、置き場付きならその置き場、素の `#N` なら同 file の解決順で決めた issue 置き場の番号として読む。

レビュー対象 repo の `docs/agents/issue-tracker.md` が本文の取得手順を定めているときは、その手順で本文を取得し、issue の body だけを手順 1 の `dir` の file に置いて `prepare-inputs.py prompts` の `--issue-file <ref>=<file>` で渡す。取得できなかった ref は `--issue-missing <ref>=<理由>` で渡す (注記は script が返す)。

定めが無いときは remote の host で決め、`--issue` へ GitHub なら `github:<置き場>#<番号>`、GitLab なら `gitlab:<置き場>#<番号>` の形で渡す。script が CLI で本文を取得する。sandbox の下では script の子の CLI が資格情報を読めず失敗することがあるので、`notes` に取得の失敗が出たら、`gh issue view <番号> -R <置き場> --json body --jq .body` / `glab issue view <番号> -R <置き場> --output json --jq .description` を直接実行して、その出力 (issue の body だけ) を手順 1 の `dir` の file へ redirect で書き、`--issue-file` で `prompts` を呼び直す。
