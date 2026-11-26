# 付け替えより先に branch が消えて closed になった CL の戻し方

SKILL.md 手順 7 の「merged の CL を target にしていて closed になった CL」に当たったときの手順。

merged の CL の head の commit を引き (`gh pr view <番号> -R <CL の repo> --json headRefOid` / `glab mr view <番号> -R <CL の repo>` の SHA)、その commit で merged の CL の source branch を push し直す (`git push <remote> <SHA>:refs/heads/<source branch>`)。push の hook が、push する commit が HEAD でないことを理由に拒んだら、その commit を HEAD にした一時の worktree から push する (`git worktree add --detach <作業ツリーの外の一時の path> <SHA>` → `cd <一時の path> && git push <remote> HEAD:refs/heads/<source branch>` → `git worktree remove <一時の path>`) — 親の作業ツリーで checkout すると親の HEAD が動く。どちらの push も Bash の timeout を上限にして呼ぶ — hook が test を走らせる repo では、足りないと push が途中で打ち切られる。push が通ったら closed の CL を reopen して (`gh pr reopen <番号> -R <CL の repo>` / `glab mr reopen <番号> -R <CL の repo>`)、SKILL.md 手順 7 の「merged の CL を target にしている open の CL」の項の付け替えに回す。push (`git worktree add` を含む) か reopen が失敗したら、作った一時の worktree を消し、失敗したコマンドと出力を添えてユーザーに問う。
