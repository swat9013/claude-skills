# settings 適用後の確かめ順

手順 4-7 の辿り順で permission ask が消えないとき、または sandbox 群を入れた適用先で Bash が失敗するときに、上から順に確かめる。本文の節で扱っている規則はその節を指すだけにし、ここでは本文に無い手掛かりだけを持つ。

## permission ask が消えないとき

手順 4-7 の 3 点 (上位 scope の deny / ask、綴りと token 境界、path 中 `*` の parse) を確かめた後に、次の順で確かめる。

1. 同じコマンドに match する `ask` entry が、どの scope にも無いことを確かめる。PreToolUse hook が `allow` を返しても、match する ask entry があればプロンプトは出る (hook の deny は permission rule より先に効くが、allow は ask を越えない)
2. 確認に使った呼び出しが、`cd` と相対 path の読み取りを同居させていないことを確かめる。`Read()` deny rule が 1 つでもある環境では、`cd` の後に相対 path を読む呼び出しは読み取り先を決められないとして承認を求め、allow を足しても消えない。読み取り先を session cwd 基準の path か絶対 path で書き直して確かめ直す
3. セッションの作業ツリーに書き先の settings があることを確かめる。`.claude/settings.local.json` は gitignored なので、fresh worktree / fresh clone には無い (手順 4-2)

## sandbox 内で Bash が失敗するとき

1. 失敗したコマンドが「環境依存チューニング」節の症状に当たるかを先に見る。当たれば同節の設定を足す
2. `excludedCommands` に入れたコマンドが、Bash 呼び出しの top-level の断片の先頭にあることを確かめる。照合は `;` / `&&` / `||` / `|` で切った各断片の先頭 token だけを見て、`for` / `while` / `if` の本体や `N=$(gh …)` の中は照合しない。除外対象コマンドは 1 呼び出しにつき top-level の 1 断片として、他のコマンドと同居させずに書く (1 つでも top-level にあると呼び出し全体が sandbox 外へ出るため)
3. 除外したコマンドへ渡す path が、sandbox の内と外で同じ場所に解決される絶対 path であることを確かめる。sandbox 外へ出た呼び出しの `$TMPDIR` は、sandbox 内の `$TMPDIR` と別のディレクトリを指す
4. unix socket の失敗は errno で切り分ける。`EPERM` なら sandbox が塞いでいるので、socket のあるディレクトリを `network.allowUnixSockets` に名指しで並べる。`ECONNREFUSED` なら sandbox ではなく、相手のプロセスが居ない
