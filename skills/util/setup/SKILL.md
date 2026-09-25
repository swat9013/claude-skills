---
name: setup
disable-model-invocation: true
description: >-
  plugin 利用者の repo に swat-skills の初期セットアップ項目 (settings 適用を含む) の
  充足を検査し、不足だけを理由付きで提案する doctor。
---

# setup

cwd の project を対象に、swat-skills を使うためのセットアップ項目の充足を検査し、**不足だけを理由付きで提案する** doctor。再実行 = 再検査で、既存ファイル・既存設定は差分提示 + 対話 merge にする (黙って上書きしない)。完了報告には全項目の結果を漏れなく載せる (区分は手順 8)。

**idempotency の定義**: 整合済みの project に再実行したとき、**提案がゼロになること**。ファイルのバイト一致ではない。

## スコープ (改変不可の境界)

| 対象 | 扱い |
|---|---|
| cwd project の `.claude/settings.local.json` | settings step の**既定の書き先** (個人スコープ、gitignored) |
| cwd project の `.claude/settings.json` | 共有したい旨の回答があったときのみ書き先にする |
| cwd project の `CONTRIBUTING.md` / `CLAUDE.local.md` | **無い場合のみ**生成する (ある場合は内容を監査しない) |
| `~/.claude/settings.json` (global) | **読むだけ**。上位 scope の deny / ask が project の allow を殺していないかの衝突検知に使う |
| cwd 以外の repo | 常に対象外。読みも書きもしない |
| swat-skills repo 自身 | cwd が swat-skills 本体なら手順 0 で停止する (配布元は導入対象でない) |

script は持たない。判断はすべて本文の基準に委ね、書き込みは人間の承認を経た分だけ行う。

**実行環境の属性 (隔離の有無 / OS / proxy 構成) を判断材料にしない。** Claude はこれらを正確に観測できず、推測で分岐すると誤った提案になる。環境に依存する選択はユーザーに尋ね、repo の内容 (依存ファイル・tracker・開発フロー) から判断できるものだけを調べて提案する。

## 手順

### 0. 前提の確認

cwd が **swat-skills repo 本体** (plugin の配布元) なら停止する。判定は cwd の `.claude-plugin/plugin.json` の `name` が `swat-skills` か (remote 名や directory 名は clone / fork で変わるので使わない)。

### 1. 全項目の充足検査

先に全項目を検査し、不足の一覧を作ってから提案に入る (項目ごとに提案 → 承認を往復すると、後続項目の不足が最後まで見えない)。

| 項目 | 充足の判定 | 不足時に進む手順 |
|---|---|---|
| tracker 設定 | `docs/agents/issue-tracker.md` が存在する | 手順 2 |
| worktree 設定 | `.claude/settings.json` の `worktree` キー / `.worktreeinclude` / worktree 初期化 script のいずれかが存在する (3 点すべてが要る repo は少ないため、1 つでも在れば導入済みと扱う) | 手順 3 |
| settings | 手順 4-4 の群がすべて入っていて、手順 4 の突合で変更提案がゼロ。入っていない群があれば手順 4 で尋ね直す (見送った群は手順 8 で対象外として載せる) | 手順 4 |
| CONTRIBUTING.md | ファイルが存在する | 手順 5 |
| CLAUDE.local.md | `CLAUDE.local.md` (または `CLAUDE.md`) に隔離環境の記載がある。**この項目だけは機械検査で確定しない** — 隔離の有無は手順 6 の対話で確定する | 手順 6 |
| dispatcher | `~/.claude/dispatcher/<project>/dispatcher-project.toml` が存在する。**導入するか否かは機械検査で確定しない** — 手順 7 の対話で確定し、導入しないなら充足・不足のどちらでもなく対象外 | 手順 7 |

### 2. tracker 設定 (外部 plugin へ委譲)

委譲先 `/setup-matt-pocock-skills` は外部 plugin (mattpocock/skills) の skill で、user-invoked 専用のため Skill tool では起動できない。

1. **導入検知**: この step が不足のときだけ、`Skill(mattpocock-skills:writing-for-agents)` の invoke 成否で plugin の導入を検知する (同 plugin の model-invocable な skill。invoke が成功すれば導入済み)
2. **未導入なら**: この step だけ停止し、`/plugin install mattpocock-skills@claude-plugins-official` を案内して残りの step を続行する。停止した step は手順 8 の未完了一覧に必ず載せる
3. **導入済みなら**: ユーザーに `/setup-matt-pocock-skills` の実行を提案する。セッション内で実行されなければ未完了として報告に載せる

### 3. worktree 設定 (既存 skill へ委譲)

不足なら、ユーザーに `/swat-skills:worktree-setup` の実行を提案する (user-invoked 専用のため Skill tool では起動できない)。セッション内で実行されなければ未完了として報告に載せる。

### 4. settings 適用

**最小集合** (swat-skills を使う全環境に共通して推奨する entry) と sandbox block は群ごとに入れるかをユーザーに尋ね、選ばれた群だけを提案する。便利系は repo 実態を見たアレンジ提案として区別する。

1. **install 形態の判定**: `~/.claude/skills/swat-skills/` が読めれば symlink install、読めなければ marketplace install。判定は skill script 群の列挙の起点 (「最小集合」節) にだけ使う — 提案する entry の正本は install 形態によらず本文の表だけ。`~/.claude/skills/swat-skills/` 配下は**読むだけ**で書き換えない
2. **書き先の決定**: 既定は `.claude/settings.local.json` のまま進む。依頼文に共有意図 (「チームで」「commit したい」「CI でも」「fresh worktree でも」) が読めるときだけ `.claude/settings.json` にするかを尋ねる。既定側は gitignored なので fresh worktree / fresh clone には存在しない — 共有意図はこの形でも現れる。共有側は個人スコープの許可を他者へ配ることになるので既定にしない
3. **現状の読み取り**: cwd の `.claude/settings.json` / `.claude/settings.local.json` (両方無ければ最小集合の全体が「追加」= 新規作成。片方だけあるときも書き先でない側を読んで重複提案しない)。`~/.claude/settings.json` は衝突検知のためだけに読む — permission は **`deny` > `ask` > `allow`** かつ上位 scope が勝つ形で評価されるため、global の deny / ask に当たる entry は project 側の allow が一度も機能しない
4. **群の確認と提案**: 「最小集合」節の 3 群 (skill script / MCP tool / deny) と「sandbox block」節の 1 群を現状と突き合わせ、中身がすべて入っている群を除いて、入れるかを AskUserQuestion (複数選択) で尋ねる。選択肢には群ごとに入れない場合の影響を書く:
   - skill script / MCP tool: swat-skills の script や tool を呼ぶたびに permission ask が出る
   - deny: 取り返しがつかない操作を permission 層で止める entry が無くなる
   - sandbox: Bash が Claude Code 内蔵 sandbox の OS 境界なしで動く。入れる場合の前提も添える — macOS は追加不要、Linux / WSL2 は `bubblewrap` と `socat` が要り、無いまま入れると `failIfUnavailable: true` で起動に失敗する (前提の充足は setup が検査せず、ユーザーの判断に委ねる)

   選ばれた群の entry のうち無いものを追加として提案する。確認は質問であって提案ではないので、idempotency の「提案ゼロ」とは別勘定。適用先の project 固有 entry と既存の `sandbox` block は保持する — 表に無いこと・選ばれなかったことは削除理由にならない。既存記述が本文の原則と矛盾するときは 現状 / 提案 / なぜ矛盾か の 3 点で**理由付きの変更提案**にする
5. **アレンジ提案**: repo の実態 (使っている tracker / 言語 ecosystem / 開発フロー) を読み、「entry を足す / 足さない基準」節に照らして便利系 entry を提案する。sandbox 群が選ばれたら、依存ファイル (lock ファイル / manifest) から分かる registry の domain を `network.allowedDomains` へ足す提案もここで出す。**手順 4-4 の提案と区別して提示する** — アレンジ提案は見送っても skill は動く
6. **承認と書き込み**: 提案ゼロなら「整合済み。提案なし」で終わる。承認された分だけ書く。既存の key 順・entry 順は保ち、追加は配列の末尾へ。書いた後に `jq . <file>` で構文確認する。書き先が `.claude/settings.json` (共有) なら、変更理由を repo の運用に沿って記録するよう伝える (届け方は実行 project の運用に従う)
7. **事後確認**: 適用した allow entry のうち副作用のない read 系のものを 1 つ実際に走らせ、permission ask が消えたことを確かめる。消えないときは 上位 scope の deny / ask (評価順は 3) → entry の綴りと token 境界 (「entry を足す / 足さない基準」節の deny 項) → path 中 `*` の parse (「path 解決」節) の順で辿る。この順で消えなければ [references/troubleshooting.md](references/troubleshooting.md) の確かめ順へ進む

setup は「plugin が動く最小を入れる」まで。以後の permission 改善 (実測で締める / 緩める) は、完了報告でユーザーに `/swat-skills:inventory-permissions` の実行を提案する。

### 5. CONTRIBUTING.md 生成 (無い場合のみ)

agent (worker) 向けの開発フロー宣言として、repo を調査 + 対話で確定して生成する。**固定テンプレの丸写しをしない** — repo ごとの gate コマンドが合わないまま載ると、読んだ agent に silent に嘘をつく。

1. repo を調査する: build / test / lint コマンド (lock ファイル・`package.json` scripts・`Makefile`・CI 設定から)、branch 運用の痕跡 (default branch、PR の有無)
2. 骨格は 4 節: **セットアップ / branch・worktree 運用 / gate (品質チェック) / commit・PR 規約**。各節の中身は調査結果を対話で確定してから書く
3. **gate 節に載せるコマンドは実在確認したものだけ** — 実行して通るか、少なくとも script / 設定ファイルの実在を確認する。確認できないコマンドは載せず、対話で確定するか節ごと省く

### 6. CLAUDE.local.md 生成 (隔離環境の場合のみ)

実行環境が隔離下 (sandbox / microVM / コンテナ等) かをユーザーに尋ねる。隔離でなければファイルを作らない。

隔離下なら、**その事実だけを蒸留して記載する**: 「この repo のセッションは隔離環境で動く。通信できる domain・失敗するコマンドの詳細は settings (sandbox 設定) を参照」の形。規範や settings 内容の複製は書かない — 規範の注入は plugin 側の責務で、複製は settings 変更のたびに腐る。

- 既定の書き先は `CLAUDE.local.md`。**repo が全環境で sandbox と明言された場合のみ** `CLAUDE.md` への記載を提案する
- `CLAUDE.local.md` が gitignore されていない repo では、誤 commit のリスクを案内してから書く

### 7. dispatcher 初期設定 (既存 skill へ委譲)

`~/.claude/dispatcher/<project>/dispatcher-project.toml` (`<project>` の既定は cwd の repo 名) が無ければ、cwd の repo で dispatcher (issue から CL までの自律オーケストレーション) を回すかを **AskUserQuestion で尋ねる** — dispatcher は opt-in で、入れない repo の方が多い。確認は質問であって提案ではないので、手順 4-4 の群の確認と同じく idempotency の「提案ゼロ」とは別勘定にする。回さないなら完了報告に「対象外」で載せて終える。

config が既にある (= 導入済み) か、回すと答えたなら、ユーザーに `/swat-skills:dispatcher-setup` の実行を提案する (user-invoked 専用のため Skill tool では起動できない)。セッション内で実行されなければ未完了として報告に載せる。

dispatcher の常駐環境 (宣言 config / claim label / crontab はいずれも cwd の外) の充足は同 skill が正本で、setup はその入口だけを持つ。sandbox block を入れた repo では `filesystem.allowWrite` の `~/.claude/dispatcher` がその環境の前提になるので、同 skill から戻されたらこの step ではなく手順 4 で足す。

### 8. 完了報告

全項目を表で報告する: 項目 / 結果 (**充足** = 検査を素通り / **適用** = 提案が承認され書き込んだ / **提案のみ** = 承認待ちや見送り / **未完了** = 停止・保留 / **対象外** = 手順 4-4 で見送った settings の群と、手順 7 で導入しないと決めた dispatcher) / 次アクション。外部 plugin 未導入で停止した step、セッション内で実行されなかった委譲先も未完了として必ず載せる。

## 最小集合 (手順 4-4 で群ごとに入れるかを尋ねる)

**前提**: 適用先で swat-skills を使うこと。この前提の下で「全環境に共通」な entry だけを置く。

| 群 | entry | 根拠 |
|---|---|---|
| skill script | install 済み swat-skills の `skills/<category>/<name>/scripts/` 配下で、SKILL.md 本文から直接起動される script すべて。列挙の起点は手順 4-1 で判定した install 形態で決まる — symlink なら `~/.claude/skills/swat-skills/`、marketplace なら「path 解決」節の cache glob を `ls` で実 path まで解決してから辿る | swat-skills の skill が直接呼ぶ script の事前承認。書き方は「path 解決」節の 2 本立て。列挙は実行のたびに SKILL.md 本文から行い、固定の一覧を持たない |
| MCP tool | 同梱 MCP server が提供する tool すべて。entry 形は `mcp__plugin_swat-skills_<server>__<tool>` で、`<server>` の列挙元は plugin の `.mcp.json`、`<tool>` はその server が実際に公開している tool | skill script 行と同じく「swat-skills を使う前提」で全環境共通。**本表に列挙は書かない** — server が tool を増やすたびに表の更新が要ることになる |
| deny | `Bash(rm -rf /)` `Bash(rm -rf /*)` `Bash(rm -rf ~)` `Bash(rm -rf ~/)` `Bash(rm -rf $HOME)` `Bash(git push --force:*)` `Bash(git push -f:*)` `Bash(git reset --hard:*)` および pipe-to-shell 4 形 `Bash(curl * \| sh)` `Bash(curl * \| bash)` `Bash(wget * \| sh)` `Bash(wget * \| bash)` | 取り返しがつかない操作 / 明確なセキュリティリスクの最後の砦 |

## sandbox block (手順 4-4 で選ばれたときに提案する)

Claude Code 内蔵 sandbox の設定。macOS / Linux / WSL2 で動く。

| 群 | entry | 根拠 |
|---|---|---|
| sandbox 本体 | `enabled: true` / `failIfUnavailable: true` / `autoAllowBashIfSandboxed: true` / `allowUnsandboxedCommands: false` | 起動不能時に unsandboxed へ黙って fallback させない。`dangerouslyDisableSandbox` による脱出を全面禁止 |
| sandbox 脱出 (普遍) | `excludedCommands` に `gh:*` / `glab:*` / `herdr:*` と、subprocess で gh・herdr を起動する skill script の tilde path | **sandbox の設計由来**の制約。`gh` / `glab` は credential (`~/.config/gh` 等) が sandbox の credential 保護で読めず起動失敗、`herdr` は socket connect が遮断される。**除外したコマンドには sandbox の制約が一切かからない**ので、守りは permission 層だけになる — 破壊的な subcommand を含むものは deny と対で扱う |
| filesystem 書込 | `allowWrite` に `~/.claude/metrics` / `~/.cache/uv` / `~/.claude/dispatcher` | 順に permission 記録・uv 実行の cache・dispatcher の宣言 config と log の置き場。いずれも cwd 外なので既定では書けない |
| network | `allowedDomains` に `github.com` / `api.github.com` / `*.githubusercontent.com` / `codeload.github.com` / `api.anthropic.com` / `statsig.anthropic.com` | ここまでが実依存の最小。言語 ecosystem の domain (`pypi.org` / registry 系) は**適用先の依存に合わせてアレンジ提案で足す** |

## 環境依存チューニング

個人環境の実測に依存し、他環境で再現するとは限らない設定。setup の実行時点では症状を観測できないので提案に使わず、sandbox を入れた後に**症状が出てから足す**。既定に入れると「なぜこの exclude があるのか分からない entry」が適用先に増える。**macOS** と印を付けた行は macOS での実測で、他の OS で同じ症状が出るとは限らない。sandbox 内で Bash が失敗したときの確かめ順は [references/troubleshooting.md](references/troubleshooting.md)。

| 設定 | 症状 (これが出たら足す) | 補足 |
|---|---|---|
| `excludedCommands` に `git push:*` / `git fetch:*` / `git pull:*` / `git ls-remote:*` | **macOS**: sandbox 内の git remote 操作が `nc: authentication method negotiation failed` で失敗する | sandbox が注入する `nc` ProxyCommand が、認証必須 SOCKS5 proxy と非互換なため。proxy 構成に依存し、同じマシンでもセッションによって変わった実績がある。**足したら force push の deny (`Bash(git push --force:*)` / `Bash(git push -f:*)`) と対で扱う** — sandbox 外実行になるので、止めているのは permission 層だけになる |
| `excludedCommands` に `git merge:*` | sandbox 内の `git merge` が `Operation not permitted` で落ち、**HEAD 据え置き + working tree だけ書き換わった中途半端な状態**になる | 適用先が `hooks/` / `.claude/hooks` / `.claude/skills` / `.claude/agents` を in-tree で管理していると、sandbox 組み込みの自己改変保護 (設定では解除できない) が merge の write を拒む。**repo の形に依存する** — これらを持たない project では症状が出ないので足さない。`git pull` (= fetch + merge) を既に除外しているなら一貫性の範囲で、権限の新規拡大にはならない |
| `sandbox.enableWeakerNetworkIsolation: true` | `gh` (Go binary) が sandbox 内で TLS 検証に失敗する | セキュリティ低下とのトレードオフ。まず `gh api user` を sandbox 内で走らせて要否を確認する |
| `network.allowedDomains` の追加 | sandbox 内の fetch / install が domain 拒否で失敗する | 適用先の実依存 (npm / rubygems / 社内 registry 等) で決まる。sandbox block の network 行をそのまま増やさない |
| `network.allowUnixSockets` に `/private/tmp` | **macOS**: sandbox 内のテスト / ツールが unix socket の `bind` / `connect` で `Operation not permitted` (`EPERM`) になる | `filesystem.allowWrite` では開かない — bind / connect は `network-bind` / `network-outbound` という file write とは別の権限クラス。**ディレクトリ単位**で並べ、その subpath 配下だけが通る (`/tmp` は `/private/tmp` へ解決されるので 1 本でよい)。**適用先が socket を張るテスト / 常駐 daemon を持つときだけ足す**。`allowAllUnixSockets: true` は全 socket 開放で、公式 docs が `/var/run/docker.sock` 経由の host 奪取を警告しているので選ばない。広いディレクトリ (`/private/tmp` 等) を許すとその配下の**他人の socket も到達可能**になるため、名指しできるならより狭い方を選ぶ |

## path 解決: symlink 環境と marketplace 環境の 2 本立て

swat-skills の script を指す entry は、環境ごとに 2 本併記する。entry の書き分けに環境判定は使わない (判定は手順 4-1 の script 列挙の起点にだけ使う) — 実在しない側は解決しない path を指すだけで害が無く、判定を持たない分だけ壊れる余地が減る。

| 環境 | 書き方 |
|---|---|
| symlink | `Bash(~/.claude/skills/swat-skills/skills/<category>/<name>/scripts/<script>:*)` |
| marketplace plugin | `Bash(~/.claude/plugins/cache/*/swat-skills/*/skills/<category>/<name>/scripts/<script>*)` |

**marketplace 側で `:*` を使わない。これが最大の罠。** rule content が `:*` で終わると wildcard より先に prefix として parse され、`*` が literal 文字として比較される → 永久に match しない死に entry になる。`:` を外して `*` を script 名へ直付けすると wildcard 型になり、引数の有無を問わず一致する。

**先頭 wildcard (`Bash(*/skills/<name>/scripts/<script>*)`) は書かない。** `*` はコマンド名自体を跨いで一致するため、「末尾がこの文字列に一致する任意のコマンド」を許可することになり、prefix anchor の無い bypass クラスを作る。version 吸収のために path 中央へ `*` を置くのは、先頭が絶対 path で固定されている限り許容範囲。

**marketplace 環境で 2 本立てでも足りないことがある。** 照合は両辺とも文字列そのままで、rule 側の `~` はホームへ展開されない。skill が `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_SKILL_DIR}` 経由で script を起動して絶対 path に解決される環境では、tilde entry に当たらない。**この症状 (marketplace install で ask が出続ける) が出たら**、`$HOME` を展開した絶対 path 版 (version segment のみ `*`) を足す。適用先の settings は個別環境のファイルなので絶対 path を書いてよい。

`sandbox.excludedCommands` にも同じ parse 規則が効く。script を除外するときは同じ 2 形式で書き、**literal な tilde path の単文で起動する** — `${CLAUDE_SKILL_DIR}` 展開 (絶対 path) / 変数間接 / 複合コマンド (`for` ループ・`VAR=x` 前置) 経由の起動は照合されず、script が sandbox 内に落ちて subprocess の gh / herdr が即死する。

## entry を足す / 足さない基準 (手順 4-5 アレンジ提案の判断軸)

**allow に足す**: 全 project で同じ用途の read-only コマンド (`git log` / `rg` / `jq` 等) / その repo の issue 駆動フローで毎回出るコマンド (git の commit・push 系、使っている tracker の `gh` / `glab` read 系と `pr create` 等)。

- tracker は repo が使っている側だけ入れる。**issue の state 変更 (`gh issue close` / `glab issue close`) を足すのは、issue 置き場が gh / glab で PR がそれと別 repo に出る project だけ** — closing reference が届かず、merge しても issue が open のまま残るため。同 repo なら PR merge の auto-close で足りる
- ファイル探索の正規手段は `rg --files` / `ls` / Glob tool。`find` は入れない — 適用先の global に `Bash(find:*)` deny があれば allow は機能せず、無くても同じ探索が通る
- **履歴書き換え / working tree 破棄 (`git rebase` / `git restore`) は入れない** — フローに現れず、事故時の損失が大きいので ask を通す

**allow に足さない (適用先の判断に返す)**: 単一 project 固有の build / test runner / credential・環境変数を扱うコマンド / network 外向きコマンド (`curl` / `wget` / `rsync`)。

**deny は綴りと token 境界で照合される**ので、破壊的操作の防御は deny 単独で完結させず、sandbox の OS 境界と hook 層を重ねる。`Bash(rm:*)` が当たるのは先頭 token が `rm` のときだけで、`command rm` は wrapper stripping の対象なので当たるが、**`/bin/rm` のような path 接頭辞つきの綴りは当たらず無言で通過する**。prefix 照合も token 境界を見るため、`Bash(git push --force:*)` は `git push --force-with-lease` に当たらない。
