---
name: apply-swat-settings
disable-model-invocation: true
description: swat-skills の正本 settings (permission / sandbox) を、原則に照らして cwd の project へ適用するインストーラ。普遍的な entry は既定で提案し、環境依存チューニングは症状つき opt-in、適用先の既存記述と矛盾する箇所は理由付きの変更提案にする。project 固有 entry は保持し、承認された分だけ書く。既定の書き先は `.claude/settings.local.json`。Use when「swat-skills の settings を適用」「permission を入れて」「この project に sandbox 設定を持ち込む」「settings.local.json を作って」「apply-swat-settings」.
---

# apply-swat-settings

swat-skills が settings 運用で学んだ**原則**を、cwd の project の settings へ適用する skill。環境差 (path 解決 / 環境依存チューニング / 適用先の既存記述との衝突) を吸収するインストーラとして働く。

出力は **追加 / 変更 (理由付き) / 保留** に分類した変更提案。**判定は人間**で、承認された分だけ書く。無人 commit はしない。

**idempotency の定義**: 原則と整合済みの settings に再適用したとき、**変更提案がゼロになること**。ファイルのバイト一致ではない。

## スコープ (改変不可の境界)

| 対象 | 扱い |
|---|---|
| cwd project の `.claude/settings.local.json` | **既定の書き先** (個人スコープ、gitignored) |
| cwd project の `.claude/settings.json` | 共有したい旨の回答があったときのみ書き先にする |
| `~/.claude/settings.json` (global) | **読むだけ**。衝突検知 (上位 scope の deny / ask が project の allow を殺していないか) に使う。**書かない** |
| cwd 以外の repo | 常に対象外。読みも書きもしない |
| swat-skills repo 自身の `settings/settings.local.json` | **対象外**。cwd が swat-skills 本体なら手順 0 で停止する |

script は持たない。merge の判断は本文の原則に委ねる。

## 手順

### 0. 前提の確認

1. cwd が git repo の root か確認する。repo でなければ「どの project に適用するか」を尋ねて止まる
2. cwd が **swat-skills repo 本体**なら停止する。本 skill は他 project 向け専用で、正本自身の保守は別 skill の領分
3. install 形態を判定する — `~/.claude/skills/swat-skills/settings/settings.local.json` が読めれば symlink install (チャネル A)、読めなければ marketplace install かそれ以外
4. 書き先を決める。**既定は `.claude/settings.local.json`** で、そのまま進む。起動時の依頼文に共有意図 (「チームで」「commit したい」「CI でも効かせたい」) が読めるときだけ、`.claude/settings.json` にするかを尋ねる。共有側は個人スコープの許可を repo の共有設定として他者へ配ることになるので、既定にはしない

### 1. 正本の取得

- **読めた場合 (symlink install)**: そのファイルを一次ソースにする。本文の baseline 表は「どの entry がどちらの分類か」の判断基準として使う (正本の側が新しい)
- **読めなかった場合**: 本文の baseline 表が唯一のソース。正本 JSON は公開ツリーに載らないため、marketplace install では実行時に読めない

いずれの場合も **`~/.claude/skills/swat-skills/` 配下は読むだけ**で、書き換えない。

### 2. 現状の読み取り

- cwd の `.claude/settings.json` / `.claude/settings.local.json`。**両方無い場合は baseline 全体が「追加」になる** (新規作成)。片方だけある場合も、書き先でないほうを読んで既存 entry を重複提案しない
- `~/.claude/settings.json` — 衝突検知のためだけに読む

### 3. 分類と突合

baseline の各 entry を、適用先の現状と突き合わせて 3 分類に振る。

| 分類 | 条件 | 出力 |
|---|---|---|
| **追加** | baseline にあり適用先に無く、原則と矛盾しない | entry と根拠を 1 行で |
| **変更 (理由付き)** | 適用先の既存記述が baseline の原則と矛盾する | 現状 / 提案 / **なぜ矛盾か** の 3 点。黙って上書きしない |
| **保留** | 環境依存チューニング、適用先で使わない skill の script entry、判断材料が足りないもの | 症状 or 判断に要る情報とセットで |

**適用先の project 固有 entry は保持する。** baseline に無いことは削除理由にならない (build / test runner、project 固有 path など)。

### 4. 提示と承認

3 分類のレポートを提示し、AskUserQuestion で承認を取る。**追加が 1 件も無ければ「原則と整合済み。提案なし」で終わる** (idempotency の成立形)。

### 5. 書き込み

承認された分だけ書く。

- 既存の key 順・entry 順は保つ。追加は配列の末尾へ
- JSON として壊さない (書いた後に `jq . <file>` で構文確認する)
- 書き先が `.claude/settings.json` (共有) の場合は、変更理由を commit message に残すよう伝える

### 6. 事後確認

適用した entry のうち 1 つを実際に走らせ、permission ask が消えたことを確かめる。消えなければ「よくある落とし穴」の 1 / 4 / 6 を疑う。

## baseline: 普遍的な entry (既定で提案する)

**前提**: 適用先で swat-skills を使うこと。skill script の entry はこの前提の下で「全環境に共通」であり、環境依存チューニング (次節) とは別の分類に置く。

| 群 | entry | 根拠 |
|---|---|---|
| skill script | install 済み swat-skills の `skills/<category>/<name>/scripts/` 配下で、SKILL.md 本文から直接起動される script すべて。列挙の起点は手順 0-3 で判定した install 形態で決まる — symlink なら `~/.claude/skills/swat-skills/`、marketplace なら次節の cache glob を `ls` で実 path まで解決してから辿る | swat-skills の skill が直接呼ぶ script の事前承認。path 解決は次節の 2 本立てで書く。**正本 `permissions.allow` を列挙元にしない** — marketplace install では正本が読めず (手順 1)、この群だけが空になる |
| MCP tool | 同梱 MCP server が提供する tool すべて。entry 形は `mcp__plugin_swat-skills_<server>__<tool>` で、`<server>` の列挙元は plugin の `.mcp.json`、`<tool>` はその server が実際に公開している tool (現行は `dispatch-ops` の 1 server) | skill script 行と同じく「swat-skills を使う前提」で全環境共通。**本表に列挙は書かない** — server が tool を増やすたびに表を更新することになり、server と無関係な差分まで止まる |
| git (読み取り) | `Bash(git status:*)` `Bash(git log:*)` `Bash(git diff:*)` `Bash(git show:*)` `Bash(git branch:*)` `Bash(git rev-parse:*)` `Bash(git remote:*)` `Bash(git ls-files:*)` `Bash(git ls-tree:*)` `Bash(git cat-file:*)` | 全 project で同じ用途の read-only |
| git (書き込み / 統合) | `Bash(git add:*)` `Bash(git commit:*)` `Bash(git switch:*)` `Bash(git checkout -b:*)` `Bash(git worktree:*)` `Bash(git merge:*)` `Bash(git push:*)` `Bash(git pull:*)` `Bash(git fetch:*)` `Bash(git ls-remote:*)` | issue 駆動フロー (実装 → commit → PR) で毎回出る。**履歴書き換え / working tree 破棄 (`git rebase` / `git restore`) は入れない** — フローに現れず、事故時の損失が大きいので ask を通す |
| issue tracker | `Bash(gh issue view:*)` `Bash(gh issue list:*)` `Bash(gh issue comment:*)` `Bash(gh issue edit:*)` `Bash(gh pr view:*)` `Bash(gh pr list:*)` `Bash(gh pr diff:*)` `Bash(gh pr checks:*)` `Bash(gh pr create:*)` `Bash(gh pr comment:*)` `Bash(gh repo view:*)` `Bash(gh api:*)` (GitLab の project なら `glab` の対応 entry) | tracker は project ごとに違うので、使う側だけ入れる。**issue の state 変更 (`gh issue close`) は入れない** — 実際の close は PR merge の auto-close で足りる |
| read-only 調査 | `Bash(ls:*)` `Bash(grep:*)` `Bash(rg:*)` `Bash(wc:*)` `Bash(jq:*)` `Bash(yq:*)` `Bash(cat:*)` `Bash(head:*)` `Bash(tail:*)` `Bash(sort:*)` `Bash(uniq:*)` `Bash(comm:*)` `Bash(diff:*)` `Bash(test:*)` `Bash(echo:*)` `Bash(sleep:*)` `Bash(mkdir:*)` `Bash(chmod +x:*)` `Bash(pwd)` `Bash(date:*)` `Bash(tree:*)` | 自律的なコード理解を妨げないための最小セット。**`find` は入れない** (落とし穴 9) |
| deny | `Bash(rm -rf /)` `Bash(rm -rf /*)` `Bash(rm -rf ~)` `Bash(rm -rf ~/)` `Bash(rm -rf $HOME)` `Bash(git push --force:*)` `Bash(git push -f:*)` `Bash(git reset --hard:*)` および pipe-to-shell 4 形 `Bash(curl * \| sh)` `Bash(curl * \| bash)` `Bash(wget * \| sh)` `Bash(wget * \| bash)` | 取り返しがつかない操作 / 明確なセキュリティリスクの最後の砦 |
| sandbox 本体 | `enabled: true` / `failIfUnavailable: true` / `autoAllowBashIfSandboxed: true` / `allowUnsandboxedCommands: false` | 起動不能時に unsandboxed へ黙って fallback させない。`dangerouslyDisableSandbox` による脱出を全面禁止 |
| sandbox 脱出 (普遍) | `excludedCommands` に `gh:*` / `glab:*` / `herdr:*` と、subprocess で gh・herdr を起動する skill script の tilde path | **sandbox の設計由来**の制約。`gh` / `glab` は credential (`~/.config/gh` 等) が sandbox の credential 保護で読めず起動失敗、`herdr` は socket connect が遮断される。script 経由の起動も同じ理由で除外が要る |
| filesystem 書込 | `allowWrite` に `~/.claude/metrics` / `~/.cache/uv` / `~/.claude/issue-dispatch` | 順に permission 記録・uv 実行の cache・同梱 MCP server の台帳置き場。いずれも cwd 外なので既定では書けない |
| network | `allowedDomains` に `github.com` / `api.github.com` / `*.githubusercontent.com` / `codeload.github.com` / `api.anthropic.com` / `statsig.anthropic.com` | ここまでが実依存の最小。言語 ecosystem の domain (`pypi.org` / registry 系) は**適用先の依存に合わせて足し引きする** |

## 環境依存チューニング (症状つきの opt-in。既定では入れない)

個人環境の実測に依存し、他環境で再現するとは限らない設定。**症状が出てから足す**。既定に入れると「なぜこの exclude があるのか分からない entry」が適用先に増える。

| 設定 | 症状 (これが出たら足す) | 補足 |
|---|---|---|
| `excludedCommands` に `git push:*` / `git fetch:*` / `git pull:*` / `git ls-remote:*` | sandbox 内の git remote 操作が `nc: authentication method negotiation failed` で失敗する | sandbox が注入する `nc` ProxyCommand が、認証必須 SOCKS5 proxy と非互換なため。proxy 構成に依存し、同じマシンでもセッションによって変わった実績がある。**足したら force push の deny (`Bash(git push --force:*)` / `Bash(git push -f:*)`) と対で扱う** — sandbox 外実行になるので、止めているのは permission 層だけになる |
| `excludedCommands` に `git merge:*` | sandbox 内の `git merge` が `Operation not permitted` で落ち、**HEAD 据え置き + working tree だけ書き換わった中途半端な状態**になる | 適用先が `hooks/` / `.claude/hooks` / `.claude/skills` / `.claude/agents` を in-tree で管理していると、sandbox 組み込みの自己改変保護 (設定では解除できない) が merge の write を拒む。**repo の形に依存する** — これらを持たない project では症状が出ないので足さない。`git pull` (= fetch + merge) を既に除外しているなら一貫性の範囲で、権限の新規拡大にはならない |
| `sandbox.enableWeakerNetworkIsolation: true` | `gh` (Go binary) が sandbox 内で TLS 検証に失敗する | セキュリティ低下とのトレードオフ。まず `gh api user` を sandbox 内で走らせて要否を確認する |
| `network.allowedDomains` の追加 | sandbox 内の fetch / install が domain 拒否で失敗する | 適用先の実依存 (npm / rubygems / 社内 registry 等) で決まる。baseline をそのまま増やさない |

## path 解決: symlink 環境と marketplace 環境の 2 本立て

swat-skills の script を指す entry は、環境ごとに 2 本併記する。環境判定はしない — 実在しない側は解決しない path を指すだけで害が無く、判定を持たない分だけ壊れる余地が減る。

| 環境 | 書き方 |
|---|---|
| symlink (チャネル A) | `Bash(~/.claude/skills/swat-skills/skills/<category>/<name>/scripts/<script>:*)` |
| marketplace plugin (チャネル C) | `Bash(~/.claude/plugins/cache/*/swat-skills/*/skills/<category>/<name>/scripts/<script>*)` |

**marketplace 側で `:*` を使わない。これが最大の罠。** rule content が `:*` で終わると wildcard より先に prefix として parse され、`*` が literal 文字として比較される → 永久に match しない死に entry になる。`:` を外して `*` を script 名へ直付けすると wildcard 型になり、引数の有無を問わず一致する。

**先頭 wildcard (`Bash(*/skills/<name>/scripts/<script>*)`) は書かない。** `*` はコマンド名自体を跨いで一致するため、「末尾がこの文字列に一致する任意のコマンド」を許可することになり、prefix anchor の無い bypass クラスを作る。version 吸収のために path 中央へ `*` を置くのは、先頭が絶対 path で固定されている限り許容範囲。

**marketplace 環境で 2 本立てでも足りないことがある。** 照合は両辺とも文字列そのままで、rule 側の `~` はホームへ展開されない。skill が `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_SKILL_DIR}` 経由で script を起動して絶対 path に解決される環境では、tilde entry に当たらない。**この症状 (marketplace install で ask が出続ける) が出たら**、`$HOME` を展開した絶対 path 版 (version segment のみ `*`) を足す。適用先の settings は個別環境のファイルなので絶対 path を書いてよい。

`sandbox.excludedCommands` にも同じ parse 規則が効く。script を除外するときは同じ 2 形式で書く。

## よくある落とし穴 (TIPS)

適用先で「entry を足したのに効かない」となる原因は、ほぼこの 10 個のどれか。

1. **評価順と scope**: `deny` > `ask` > `allow`、かつ上位 scope が勝つ。global (`~/.claude/settings.json`) の deny / ask は project の allow を必ず殺す。実例: global に `Bash(find:*)` deny があると、project 側に `find` の allow を置いても一度も許可として機能しない
2. **`autoAllowBashIfSandboxed: true` は「sandbox 内 Bash が全部 ask レス」ではない**。auto-allow mode でも素通りしないものが 3 つある — ① 明示的な `permissions.deny`、② **content-scoped な `permissions.ask` entry (`Bash(rm:*)` 等) は sandbox 内でもプロンプトを強制する**、③ `/`・home・重要 system path を対象にした `rm` / `rmdir`。ask を丸ごとスキップできるのは裸の `Bash` / `Bash(*)` ask のみ
3. **PreToolUse hook の `allow` は permission rule を越えない**。同じコマンドに match する `ask` がある間、許可専用 hook はプロンプトを消せない。逆方向 (hook の deny / exit 2) は permission rule より先に効く
4. **照合はコマンドの綴りに依存する**。`Bash(rm:*)` は先頭 token が `rm` のときだけ当たる。`command rm` は wrapper stripping (`command` / `builtin` / `timeout` / `nice` / `nohup` / `stdbuf` / `noglob` / フラグなし `xargs`) の対象なので当たるが、**`/bin/rm` のような path 接頭辞つきの綴りは当たらず ask を無言で通過する**。破壊的操作の防御を ask 層だけに頼らない
5. **prefix 照合は token 境界を見る**。`Bash(git push --force:*)` は `git push --force-with-lease` に当たらない (一致条件が「完全一致」か「直後が空白」のため)
6. **`:*` サフィックスは wildcard を無効化する** — 前節のとおり。path 中に `*` を置きたいなら `:*` を外す
7. **`excludedCommands` に出したコマンドには sandbox の制約が一切かからない**。守っているのは permission 層だけになるので、`git push:*` を出すなら force push の deny と対で扱う
8. **`excludedCommands` に登録した script は literal な tilde path の単文で起動する**。`${CLAUDE_SKILL_DIR}` 展開 (絶対 path) / 変数間接 / 複合コマンド (`for` ループ・`VAR=x` 前置) 経由の起動は照合されず、script が sandbox 内に落ちて subprocess の gh / herdr が即死する
9. **ファイル探索の正規手段は `rg --files` / `ls` / Glob tool**。`find` の allow を足さない — 適用先の global に `Bash(find:*)` deny があれば allow は機能せず (落とし穴 1 の実例)、無くても `rg --files` / `ls` で同じ探索が通る。`find` 固有の危険は列挙ではなく `-exec` / `-delete` 系の述語なので、塞ぐなら allow/deny ではなく hook 側で述語だけを見る
10. **`.claude/settings.local.json` は Claude Code の既定で gitignored**。fresh worktree / fresh clone には存在せず、そこで動くセッションには適用されない。共有が要るなら `.claude/settings.json` へ書く

## entry を足す / 足さない基準

適用先で新しい entry を提案するかの判断基準。

**allow に足す**: swat-skills の skill / hook が直接呼ぶ script (絶対 path 完全一致) / 全 project で同じ用途の read-only コマンド / issue 駆動の標準フローで毎回出るコマンド。

**allow に足さない (適用先の判断に返す)**: 単一 project 固有の build / test runner / credential・環境変数を扱うコマンド / network 外向きコマンド (`curl` / `wget` / `rsync`)。

**deny に足す**: 取り返しがつかない操作 (`rm -rf /` / `git reset --hard` / force push) / セキュリティリスクが明確なもの (`curl | sh`)。

## 責務外

- 正本 `settings/settings.local.json` そのものの変更 (本 skill は読むだけ)
- global `~/.claude/settings.json` への書き込み (人間の操作、または dotfiles の責務)
- 適用先の transcript 走査 — 本 skill は正本の原則を**入れる**側で、実績から減らす / 直す側ではない
- 承認ステップの自動化。書き込みは必ず人間の承認を経る
