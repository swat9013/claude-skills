# dispatcher の導入と運用

本 file は人が読む手順書。仕組みと契約 (指示の種別 / orchestrator の手順 / worker の spawn prompt 契約 / tick が残す file) は [SKILL.md](SKILL.md)。

## 導入 (project ごとに 1 回)

1. `mkdir -p ~/.claude/dispatcher/<project>` して宣言 config を置く: `~/.claude/dispatcher/<project>/dispatcher-project.toml`。`<project>` は script に渡す任意の名前 (repo 名で足りる)

   ```toml
   [issue]
   # 対応 tracker は gh のみ (他は script が名指しで落とす)
   tracker = "gh"
   # issue 置き場 (owner/name)
   repo = "swat9013/swat-skills"
   # 着手可を表す triage label の綴り
   ready_label = "ready-for-agent"

   # [cl] は CL 置き場が issue 置き場と別のときだけ書く。省略すると issue 置き場を継ぐ。
   # 書くなら repo は必須 (空の [cl] は「同じ」ではなく誤り)。tracker は issue 側と同じ
   # [cl]
   # repo = "swat9013/other-repo"

   [limits]
   # 並列上限 N (dispatcher:wip の枚数がこれに達すると start 指示が出ない)
   max_wip = 2

   # [auth] は cron から gh の認証が読めない環境だけ書く (手順 4 の cron 相当の試運転で認証が落ちたとき)。
   # env に GH_TOKEN / GITHUB_TOKEN が無いときだけ tick がこの file を読み、gh と orchestrator / worker へ
   # GH_TOKEN として渡す。file は group / other から読めない mode (600 等。読めると exit 2)。~ 始まりか絶対 path
   # [auth]
   # token_file = "~/.claude/dispatcher/<project>/gh-token"
   ```

   未知の table / key は script が名指しで失敗させる (綴り違いが「宣言していない」と同じ挙動になるのを防ぐ)。

2. claim 信号の label を置き場 repo に作る: `gh label create dispatcher:wip -R <repo>` (`ready-for-human` も無ければ同様に)。script の config 検査が 2 つの存在を確かめ、無ければ名指しで exit 2 にする

3. **実装 repo の clone を cwd にして** tick を試運転 (`--dry-run`) で撃つ。**この 1 行を literal で撃つ** (path を変数へ置き換えたり `env …` / `VAR=x` を前置したりすると sandbox の除外指定と照合されず、script 内の gh が credential を読めないまま落ちる):

   ```
   ~/.claude/skills/swat-skills/skills/util/dispatcher/scripts/dispatcher-tick.py --dry-run <project>
   ```

   試運転は config の検査 → 観測 → 指示の導出までを通し、claude を起動する直前で止めて、指示の件数を stdout に JSON 1 行で出す (`"result": "ok"` と `instructions` の種別ごとの件数)。**`~/.claude/dispatcher/<project>/` 配下に何も書かない** (lock / `.config-verified` / log.jsonl / 指示ファイルのどれも) ので、実 config のまま撃ってよく、着手可の issue があっても worker は着手しない。gh と claude が PATH で見つかることも確かめる。

   exit 2 は config 起因 (無い / TOML が読めない / 未知の table・key / 置き場 repo が実在しない / `dispatcher:wip`・`ready-for-human` label が無い / `[auth].token_file` が無い・group / other から読める mode・空) で、観測を開始していない。綴りを直して撃ち直す。exit 4 (`result=auth_error`) は gh の認証が通らない失敗で、綴りでは直らない — `gh auth status` で認証を直す。失敗は stderr に `tick=-` の 1 行で出る (試運転は log.jsonl を書かないので)。

4. 同じ試運転を cron と同じ最小の環境変数で撃つ。`--cron-env` を足すと、script が `HOME` と `PATH=<uv の dir>:/usr/bin:/bin` だけを残した環境で自分を撃ち直す (`<uv の dir>` は今の PATH で引いた uv の dir。親 shell の `GH_TOKEN` 等は継がない):

   ```
   ~/.claude/skills/swat-skills/skills/util/dispatcher/scripts/dispatcher-tick.py --dry-run --cron-env <project>
   ```

   手順 3 は通るのにここで exit 4 (`result=auth_error`) が出るなら、gh の認証が親 shell の環境変数にしか無い。手順 1 の `[auth].token_file` に token を置く (下記「死活確認」の gh 認証の項)。手順 5 の tick 行の `<uv の dir>` は、ここで使われた dir (`dirname "$(command -v uv)"` の値) と同じにする。この試運転は cron を完全には再現しない — macOS Keychain 等の keyring は環境変数でなくログイン session に従うので、対話 session から撃つと cron では読めない keyring を読めてしまう。`gh auth status` が `(keyring)` と出る環境は、ここが通っても手順 6 で落ちうるので先に `[auth].token_file` を置いておく

5. crontab に 1 行足す (`crontab -e`。`/swat-skills:dispatcher-setup` は手順 4 が通った後、承認を得てこの行を登録する)。周期は 5 分が目安 (orchestrator の上限 15 分より短くても lock で重ならない):

   ```
   */5 * * * * cd <実装 repo の clone> && PATH=<uv の dir>:$PATH ~/.claude/skills/swat-skills/skills/util/dispatcher/scripts/dispatcher-tick.py <project> >> ~/.claude/dispatcher/<project>/cron.log 2>&1
   ```

   `cd <clone>` が orchestrator / worker の cwd を決める。`PATH=<uv の dir>` の前置は script の shebang が `uv` を PATH で引くために要る (cron の既定 PATH に無く、無いと script が 1 行も走らない)。`>> cron.log` の中身は SKILL.md「tick が残すもの」の表。`~/.claude/dispatcher/<project>/` ごと無いと cron.log も書けず、失敗は cron の mail にしか残らない (手順 1 で作った dir を消さない)

6. 周期の 2 倍待って `tail -1 ~/.claude/dispatcher/<project>/log.jsonl` の `ts` が進んでいれば導入完了。静止した project なら `result: ok` / `instructions: {}` の行だけが並び、claude は起動しない

## 運用

### 実行状況を見る

tick の状態と、走っている / wip の残った worker の一覧を 1 コマンドで出す (読み取り専用で、何も書かない)。tick と同じく**実装 repo の clone を cwd にして、この行を literal で撃つ** (BRANCH 列は cwd の clone の作業ツリーを引く):

```
~/.claude/skills/swat-skills/skills/util/dispatcher/scripts/dispatcher-status.py ps swat-skills
```

```
~/.claude/skills/swat-skills/skills/util/dispatcher/scripts/dispatcher-status.py watch swat-skills
```

- `ps` は 1 回出して終わる。`--json` で同じ内容を JSON で出す。project を省略すると `~/.claude/dispatcher/` 配下の全 project を順に出す
- `watch` は `ps` を 5 秒ごとに描き直す (`--interval <秒>` で変える)。Ctrl-C で終わる
- 先頭行は tick の状態 (`tick.lock` が取られていれば `tick 実行中`、取られていなければ `待機`) と log.jsonl 最終 tick 行の `ts` / `result`。worker が 0 本ならこの 1 行だけ。lock は shared lock を一瞬取って確かめるので、その一瞬に cron の tick が重なるとその tick は `result: locked` の 1 行を残して見送られる (次の周期で普通に回る)
- worker の行は log.jsonl の `spawned` の起動記録のうち、issue に `dispatcher:wip` が付いているか process が生きているものを載せる (同じ issue を再入で何度か起動していれば起動ごとに 1 行)。STATE は process の生死 (`running` / `exited`。pid の再利用を拾わないよう、tick が渡した session id が command 行にあるかで見る)、SESSION は `claude agents --json` の同じ session の行 (id / status / state)、BRANCH は `worktree-issue-<N>` の作業ツリーの `origin/HEAD` からの ahead commit 数、WIP / CL は gh で毎回読む
- 外部コマンド (gh / git / claude / ps) が失敗した列は `?` になり、表は出る。理由は先頭行の下に `!` 行で出る。wip を読めないときは process の生きている worker だけを、process の一覧を読めないときは wip の付いた worker だけを載せる。cwd の clone が CL 置き場の repo でないとき (project を省略して別 repo の project も出すとき) はその project の BRANCH が `?` になる
- 個々の worker をリアルタイムに見るなら SESSION 列の id で `claude attach <id>` する (id は background の session だけが持つ)
- 短縮名 (`dispatcher ps` 等) が欲しければ自分の shell の alias で付ける

### 死活確認

- **tick が回っているか**: `log.jsonl` 最終行の `ts` が周期の 2 倍より新しい。古ければ tick が走っていない
- **走っていないとき**: `ls -l ~/.claude/dispatcher/<project>/cron.log` の更新時刻を見る。log.jsonl 最終行より新しければ script 手前で落ちている — 末尾の行を読む (`env: uv: No such file` なら tick 行の `PATH=<uv の dir>` 前置、`cd: … No such file` なら clone の path)。cron.log も古ければ cron 自体が撃っていない (`crontab -l` で登録を確かめる / マシンのスリープ中は走らない — 取りこぼした tick は追い掛けず次の周期から普通に再開する)。cron.log には失敗 tick の error 文も溜まるので「空か」では判定しない
- **tick が失敗しているとき**: `tail -n 3 ~/.claude/dispatcher/<project>/cron.log` を読む。script が出す行は前置付きの 1 行で、いつ・どの tick が・何で・なぜ落ちたかが読める (書式は SKILL.md「tick が残すもの」の `cron.log` 行。`tick=` の値で log.jsonl の行と突き合わせる)。script 手前の起動失敗 (uv が無い / clone が無い) は script が前置できないので前置の無い行になり、いつ起きたかは上の cron.log の更新時刻で読む
- **回っているが期待どおりでないとき**: 最終行の `result` を読む (`error` / `config_error` / `auth_error` / `locked` の意味は SKILL.md「tick が残すもの」の表)
- **orchestrator / worker の判断過程を読むとき**: tick 行の `orchestrator.session_id` / `spawned[].session_id` で標準 transcript を引く — `ls ~/.claude/projects/*/<session_id>.jsonl` が 1 件返る (dir 名は cwd から Claude Code が作るので glob で引く)。claude を起動した最初の tick の後にこれで 1 件返ることを確かめておく (返らなければ `--session-id` を受け付けない claude の版で、log から transcript へ辿れない)
- **`result: auth_error` (gh の認証失敗) が出る**: gh の認証が OS の keyring 保存 (`gh auth status` に `(keyring)`) だと cron から読めないことがある。宣言 config の `[auth].token_file` に token を置く: issue 置き場と CL 置き場の repo (別 repo なら両方) だけに絞った fine-grained PAT (Repository permissions の Issues / Pull requests / Contents を Read and write) を発行し、`(umask 077; cat > ~/.claude/dispatcher/<project>/gh-token)` で貼り付けて保存してから、config に `[auth]` / `token_file = "~/.claude/dispatcher/<project>/gh-token"` を足す。tick は env に `GH_TOKEN` / `GITHUB_TOKEN` が無いときだけこの file を読み、gh と orchestrator / worker へ `GH_TOKEN` として渡す。token は平文の file として残る (mode 600 で user 以外読めないが、漏洩時は失効させる)。worker の `git push` がこの token で通るのは git の credential helper が gh のとき (`gh auth setup-git`) だけで、ssh や osxkeychain で push している環境はその経路の認証に依る
