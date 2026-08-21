# dispatch 機構の前提条件

`orchestrator` / `observer` skill と `dispatch-ops` MCP server からなる **dispatch 機構** (issue を選んで Claude Code セッションへ配車し、PR まで自走させる仕組み) を、**この plugin を入れた環境で動かせるか**を導入前に自力で判定するための文書。

対象読者は導入者 (人間)。実行時の手順は `SKILL.md` が正本で、本書は重複させない。

- **skill を使うだけなら本書は不要。** dispatch 以外の skill は追加の前提を持たない
- 前提を 1 つでも欠くと dispatch は動かない。ただし**黙って壊れるもの**と**起動時に止まるもの**があり、本書はその差を明示する (下表の「不成立時の見え方」列)
- **判定を手で行う前に `/swat-skills:dispatch-setup` を実行する** — §6 のチェックリストを機械検査し、宣言 config (§4) を生成する。本書は「なぜその前提が要るか」と「doctor が見られない項目はどれか」を持つ側で、実行手順は skill が持つ

## 前提の層

```
  導入者が用意するもの                     環境で保証されるもの
  ────────────────────                     ──────────────────────
  herdr (必須)  ─┐                        ┌─ permissions.allow
  uv             │                        │   ├ Bash(herdr …) 10
  gh / glab      ├─► dispatch 機構 ◄──────┤   ├ Bash(git …) / Bash(gh …)
  dispatch-      │   (orchestrator /      │   └ mcp__plugin_swat-skills_
    project.toml │    observer /          │        dispatch-ops__* 21
  plugin 名      │    dispatch-ops)       ├─ sandbox.excludedCommands 8
    = swat-skills┘            ▲           └─ sandbox.filesystem.allowWrite
                              │                ~/.claude/issue-dispatch
                   Claude Code harness 機能
                   (受け入れた依存 — 代替実装を持たない)
```

**「環境で保証されるもの」は plugin では配れない。** Claude Code は plugin から `settings` を配信する手段を持たないので、この列は導入者が適用先 project の `.claude/settings.local.json` へ**自分で写す**必要がある (本書 §3 に全 entry を逐語で載せてあるのはこのため)。「保証される」と呼べるのは写し終えた後だけで、写す作業自体は導入者の仕事になる。

## 1. Claude Code harness 機能 (受け入れた依存)

dispatch 機構は次の harness 機能に**直接依存している**。無い環境で代替する経路は用意していない (fallback を持たない = 受け入れた依存)。

| 機能 | 使い方 | 無いとどうなるか |
|---|---|---|
| **cross-session messaging** (`SendMessage` / `ListAgents`) | orchestrator ↔ worker の質問中継、orchestrator ↔ observer の escalation | worker の質問と observer の escalation がどこにも届かない。**送信側にエラーは返らない** |
| `CLAUDE_CODE_MESSAGING_SOCKET` | 上記の受信可否の判定に使う。server が起動時に検査する | 未設定なら `pane_spawn` が fail-closed で止まる (起動しない) |
| **`/loop` dynamic** (interval 無し) + `ScheduleWakeup` | observer の周期観測。毎 tick 自分で次の起床を決め直す | observer が 1 周期で止まり、以後 PR merge も pane 死も検知されない |
| `claude --worktree <名前>` | worker の作業ツリー隔離。`<clone root>/.claude/worktrees/<名前>` に切られる | 複数 worker が同じ working tree を奪い合う |
| `claude --name <名前>` | 起動セッションの表示名。`ListAgents` の宛先解決に使う | 相手を名前で見つけられない |
| `claude --model` / `--effort` | worker / observer の段階に応じた割り当て | 段階に関わらず既定モデルで走る |
| **plugin 同梱 MCP server** (`.mcp.json` + `${CLAUDE_PLUGIN_ROOT}`) | `dispatch-ops` の起動 | tool が 1 つも見えない |

**version 依存の落とし穴**: 旧 binary の resume セッションは messaging を**送信できても受信できない**。この差は `CLAUDE_CODE_MESSAGING_SOCKET` の有無に出るので、server は起動前に検査して止める。**現行 binary で新規起動したセッション**から orchestrator を起こすこと。

Remote Control (`--remote-control`) は**前提ではない**。有効にすると worker の質問と承認を claude.ai web / mobile から返せる。

## 2. herdr が必須 (tmux 非対応)

pane の起動・観測・rename は [herdr](https://github.com/HerdrHQ/herdr) (AI agent 向け terminal multiplexer) の CLI 経由でのみ行う。**tmux backend は実装していない** — port 境界は設計してあるが adapter が無く、`dispatch-ops` の pane 系 tool は herdr しか持たない。

導入者が用意するもの:

| 前提 | 検査方法 | 不成立時の見え方 |
|---|---|---|
| `herdr` が PATH に在る | `herdr status` | pane 系 tool が `herdr status が失敗` で落ちる |
| **herdr session 内で Claude Code を起動している** | `echo $HERDR_ENV` が `1` | `HERDR_ENV=1 でない (herdr session の外で server が起動している)` |
| **herdr の Claude 連携 hook が現行版** | `herdr integration status` の出力に `claude: current` の行 | `herdr integration status に \`claude: current\` が無い (hook が古い / 未導入)`。導入は `herdr integration install claude` |
| herdr daemon へ疎通できる | `herdr status` が exit 0 | `herdr status が失敗 (socket に届かない)` |
| `HERDR_PANE_ID` / `HERDR_WORKSPACE_ID` が設定済み | `echo $HERDR_PANE_ID` | `HERDR_PANE_ID が未設定 (herdr session 外で server が起動している)` |

**この 4 検査はすべて loud に落ちる** (fail-closed)。前提不成立のまま誤 dispatch へ進む経路は無い。observer も同じ 4 検査を最初の tick で通す。

その他、導入者が用意するもの:

| 前提 | 用途 | 検査方法 |
|---|---|---|
| `uv` | MCP server は PEP 723 script を `uv run --script` で起動する | `uv --version` |
| `gh` (認証済み) | GitHub tracker の観測・claim・label | `gh auth status` |
| `glab` (認証済み) | GitLab tracker を使う場合のみ | `glab auth status` |

## 3. 必要な settings

適用先 project の `.claude/settings.local.json` に次を置く。**plugin からは配れない** (Claude Code に plugin → settings の配信手段が無い)。

### 3.1 `sandbox.excludedCommands` (8 entry)

```json
"gh:*", "glab:*", "herdr:*",
"git push:*", "git fetch:*", "git pull:*", "git ls-remote:*", "git merge:*"
```

対象は **Bash tool から走るコマンド**だけ。MCP server が内部で起動する subprocess (server が呼ぶ `gh` / `herdr` / `git`) は Bash tool を通らないので sandbox の対象外で、この列の影響を受けない。

理由と不成立時の見え方:

| entry | 理由 | 欠けたときの見え方 |
|---|---|---|
| `herdr:*` | socket connect が sandbox 内で通らない | orchestrator 起動手順 0 の `herdr pane rename` が落ちる |
| `gh:*` / `glab:*` | 認証 token (`~/.config/gh/config.yml` 等) が sandbox の credential 保護で読取禁止 | worker の `gh issue view` / `gh pr create` が起動ごと失敗する |
| `git push:*` / `git fetch:*` / `git pull:*` / `git ls-remote:*` | sandbox が注入する SOCKS5 proxy 経由の SSH が、proxy 認証の有無でセッションごとに通ったり通らなかったりする | worker が PR を push できない。**failure はセッション依存で再現しない** |
| `git merge:*` | sandbox 組み込みの自己改変保護が `hooks/` / `.claude/hooks` / `.claude/skills` / `.claude/agents` への write を拒む (設定で解除できない) | **これらを in-tree で持つ repo で最も高くつく** — merge が `Operation not permitted` で落ち、**HEAD 据え置き + working tree だけ書き換わった中途半端な状態**になる |

**除外の照合は「コマンドの綴り」ではなく Bash 呼び出しの top-level segment に対して行われる。** `;` / `&&` / `||` / `|` で切った断片の先頭 token だけが見られ、`for` / `while` / `if` の**本体は分割されない**。ループ本体にしか `gh` / `git merge` が無い呼び出しは除外に当たらず、**呼び出し全体が sandbox 内**で走る (`cd <repo>; for n in ...; do gh issue view $n; done` は毎回起動失敗する)。**除外対象コマンドは 1 呼び出しにつき top-level の 1 断片として書く** ([#652](https://github.com/swat9013/swat-skills/issues/652)、Claude Code v2.1.237 で実測)。

`git push:*` を sandbox 外に出す以上、force push を止めるのは permission 層だけになる。`permissions.deny` の `Bash(git push --force:*)` / `Bash(git push -f:*)` を**対で維持する**こと。

### 3.2 `sandbox.filesystem.allowWrite`

```json
"~/.claude/issue-dispatch"
```

台帳 (`state.json` / `events.jsonl`) と宣言 config (`dispatch-project.toml`) の置き場。**MCP server 自身の書き込みには要らない** (server は Bash sandbox の外) が、**セットアップ時に session が Bash / Write でここへ config を置く経路**に要る。欠けると config の設置が deny され、§4 の宣言が置けない。

### 3.3 `permissions.allow` — herdr (10 entry)

```json
"Bash(herdr status:*)", "Bash(herdr integration status:*)",
"Bash(herdr pane list:*)", "Bash(herdr pane split:*)", "Bash(herdr pane rename:*)",
"Bash(herdr pane run:*)", "Bash(herdr pane get:*)", "Bash(herdr pane read:*)",
"Bash(herdr pane close:*)", "Bash(herdr wait:*)"
```

### 3.4 `permissions.allow` — dispatch-ops の MCP tool (21 entry)

```json
"mcp__plugin_swat-skills_dispatch-ops__ledger_record",
"mcp__plugin_swat-skills_dispatch-ops__ledger_transition",
"mcp__plugin_swat-skills_dispatch-ops__ledger_annotate",
"mcp__plugin_swat-skills_dispatch-ops__ledger_report_outcome",
"mcp__plugin_swat-skills_dispatch-ops__ledger_list",
"mcp__plugin_swat-skills_dispatch-ops__ledger_get",
"mcp__plugin_swat-skills_dispatch-ops__observe_issues",
"mcp__plugin_swat-skills_dispatch-ops__observe_prs",
"mcp__plugin_swat-skills_dispatch-ops__issue_claim",
"mcp__plugin_swat-skills_dispatch-ops__issue_unclaim",
"mcp__plugin_swat-skills_dispatch-ops__issue_comment",
"mcp__plugin_swat-skills_dispatch-ops__issue_label",
"mcp__plugin_swat-skills_dispatch-ops__observe_panes",
"mcp__plugin_swat-skills_dispatch-ops__pane_watch",
"mcp__plugin_swat-skills_dispatch-ops__pane_spawn",
"mcp__plugin_swat-skills_dispatch-ops__pane_close",
"mcp__plugin_swat-skills_dispatch-ops__pane_send",
"mcp__plugin_swat-skills_dispatch-ops__observe_worktrees",
"mcp__plugin_swat-skills_dispatch-ops__worktree_tidy",
"mcp__plugin_swat-skills_dispatch-ops__worktree_sweep",
"mcp__plugin_swat-skills_dispatch-ops__resolve"
```

`observe_project` / `project_doctor` / `project_setup` は上の列挙に含めていない (前 2 つは読み取り
専用、`project_setup` は導入時に 1 度書くだけなので、都度承認で足りる)。**件数を数え直すなら
正本は `settings/settings.local.json`** — 上の code block はそこから写したもので、`project_doctor`
の settings 検査も同じ正本から要求 entry を導出する (散文の件数は参照しない)。**server 全体を wildcard 1 entry で許可する記法は採らない** — 記法の裏付けが取れておらず、個別列挙のほうが確実で最小権限になる。

**欠けたときの見え方**: tool 呼び出しのたびに permission ダイアログが出る。worker / observer は pane 内で `blocked` のまま止まり、**メッセージでは解除できない** (人間が pane に入って答える必要がある)。

### 3.5 `permissions.allow` — git / gh

worker が実装から PR まで自走するために要る最小セット。既定 template では `Bash(git commit:*)` / `Bash(git push:*)` / `Bash(git pull:*)` / `Bash(git merge:*)` / `Bash(git worktree:*)` / `Bash(gh issue view:*)` / `Bash(gh pr create:*)` などを許可している。**`git merge:*` は allow と `excludedCommands` の両方が要る** (permission 層と sandbox 層は別)。

### 3.6 network

sandbox の `network.allowedDomains` に tracker の host を含める。GitHub なら `github.com` / `api.github.com` / `*.githubusercontent.com` / `codeload.github.com`。

## 4. 宣言 config (issue 置き場 / PR 置き場)

**どの tracker のどの repo を issue 置き場にするかは、台帳ディレクトリ直下の `dispatch-project.toml` が宣言する。** dispatch-ops server がこれを解決し、tracker 系 tool の `repo` / `pr_repo` の既定値にする。

置き場所 — **台帳と同じディレクトリ**:

```
~/.claude/issue-dispatch/<repo-key>/
├── dispatch-project.toml   # 宣言 (本書の対象)
├── state.json              # 台帳
└── events.jsonl
```

`<repo-key>` は project の anchor repo の remote URL から導く。`git@github.com:swat9013/swat-skills.git` → `github.com__swat9013__swat-skills` (host + path segment を `__` で連結)。remote を持たない repo は main worktree 実パス由来の `path__…` に倒れる。

書式 — 書ける table は `[issue]` / `[pr]`、key は `tracker` / `repo` だけ:

```toml
[issue]
# gh | glab | jira
tracker = "gh"
# 置き場の識別子。GitHub は owner/name、GitLab は group/project、Jira は project key
repo = "swat9013/swat-skills"

# [pr] は PR 置き場が issue 置き場と違うときだけ書く (issue = Jira / PR = GitLab 等)。
# 省略すると issue 置き場をそのまま継ぐ。書くなら repo は必須 (tracker は省くと issue 側を継ぐ)。
# 空の [pr] は error — 識別子を落とすと CLI の cwd 推論へ倒れ、別 repo の PR を黙って観測する。
```

設置 (この config は **version 管理の外**にある。clone しても付いてこないので環境ごとに置く):

**`/swat-skills:dispatch-setup` を実行する** — repo-key の導出も台帳ディレクトリの作成も `project_setup` が行うので、置き場所を手で組み立てる必要は無い。既存 config は `overwrite` を明示しない限り上書きされない。

手で置く場合 (skill を通さないとき) は、置き先を自分で導出する:

```sh
mkdir -p ~/.claude/issue-dispatch/github.com__swat9013__swat-skills
printf '[issue]\ntracker = "gh"\nrepo = "swat9013/swat-skills"\n' \
  > ~/.claude/issue-dispatch/github.com__swat9013__swat-skills/dispatch-project.toml
```

検査と不成立時の見え方:

| 状態 | 検査 | 見え方 |
|---|---|---|
| 正常 | `project_doctor` の `project_config` が `ok` (`observe_project` なら `issue.source` = `config` / `repo` 非 null / `config_path` が実在パス) | — |
| **config が無い** | `project_doctor` の `project_config` が `missing` (`observe_project` なら `repo` が **null** / `issue.source` = `remote`) | **silent**。tracker 種別だけ git remote の host からの推測で決まり (返せるのは gh / glab のみ)、repo 識別子は CLI の cwd 推論へ倒れる。置き場がメイン repo 自身なら結果は同じだが、**置き場が関連 repo の project では別の置き場を黙って観測し続ける**。**Jira 置き場は推測から出ないので、config が無い限り gh / glab と誤判定される** |
| **repo の綴りが違う** | server では検出できない。`observe_issues` の `issues[].url` を目視 (1 サイクルに 1 度) | **silent**。存在しない repo なら CLI error、実在する別 repo なら誤った置き場を観測し続ける |
| table / key / tracker 名の綴り違い | server が名指しで失敗させる | **loud**。握り潰して cwd 推論へ倒れることはない |

**config を編集したら dispatch-ops server の再起動が要る** (プロセス内 cache)。`project_doctor` だけは config を直読みするので、置いた直後の確認は再起動前でもできる — ただし**その config で観測が向く先が変わるのは再起動後**。

台帳ディレクトリそのものは `pane_spawn` が env (`ISSUE_DISPATCH_LEDGER_DIR`) で worker へ注入する。別 clone で走る worker の記帳も project の台帳 1 つへ着地するので、worker 側で設定するものは無い。

## 5. plugin 名の契約 — `swat-skills` 固定

**plugin 名は `swat-skills` 固定が配布契約。** dispatch 機構は plugin 名を文字列で直書きしている:

| 表記 | 例 |
|---|---|
| observer の自己再読込文面 | `/swat-skills:observer を実行する。skill の手順に完全に従うこと` |
| MCP tool 完全名 | `mcp__plugin_swat-skills_dispatch-ops__<tool>` |
| `/mcp` 上の server 名 | `plugin:swat-skills:dispatch-ops` |

**Claude Code に plugin 名を与える substitution token は存在しない** ため、動的に解決する手段が無い。plugin 名が違うと observer は毎 tick の自己再読込に失敗し、**手順を失ったまま観測を続ける** (silent)。

導入者にとっての含意は 2 点だけ:

- **plugin 名の正本は publisher 側の宣言**。skills-dir 配布なら `.claude-plugin/plugin.json` の `name`、marketplace 配布なら marketplace entry の `name`。どちらも install 側では変えられない
- **symlink 先のディレクトリ名は plugin 名に影響しない** — `ln -s <repo> ~/.claude/skills/<好きな名前>` の名前を変えても壊れない

つまり install 側の操作で plugin 名がずれる経路は無く、**導入者が守るべきことは実質無い**。契約が効くのは publisher が rename したときで、そちらは repo 内の pre-commit gate が直書きとの不整合を止める。

## 6. 導入チェックリスト

**手で 1 行ずつ確かめる前に `/swat-skills:dispatch-setup` を実行する。** #11 を除く全項目を機械検査 (`project_doctor`) し、不足を逐語で報告して、宣言 config (#10) はその場で生成する。本表は doctor が返す `checks[].id` と 1:1 で、doctor が読めない層 (#11 の綴り) と、doctor が直さない項目 (settings は `/apply-swat-settings`、install は導入者) を見分けるための対応表として残す。

| # | 前提 | 誰が用意するか | 検査 (`project_doctor` の id) | 不成立 |
|---|---|---|---|---|
| 1 | 現行 binary で新規起動した Claude Code セッション | 導入者 | `messaging` (`CLAUDE_CODE_MESSAGING_SOCKET` が設定済み) | loud |
| 2 | herdr が PATH に在る / daemon 稼働 | 導入者 | `herdr_daemon` (`herdr status` が exit 0) | loud |
| 3 | herdr session 内での起動 | 導入者 | `herdr_session` (`HERDR_ENV=1` / `HERDR_PANE_ID` 非空) | loud |
| 4 | herdr の Claude 連携 hook | 導入者 | `herdr_integration` (`claude: current` の行) | loud |
| 5 | `uv` | 導入者 | `uv` (`uv --version`) | loud (server が起動しない) |
| 6 | `gh` / `glab` 認証 | 導入者 | `tracker_cli` (宣言に現れる tracker の `auth status`) | loud |
| 7 | `sandbox.excludedCommands` 8 entry | 導入者が settings へ写す | `settings` (正本との突合) | 項目ごとに異なる (§3.1) |
| 8 | `sandbox.filesystem.allowWrite` に台帳ディレクトリ | 導入者が settings へ写す | `settings` (同上) | loud (config を置けない) |
| 9 | `permissions.allow` の MCP tool 21 entry + herdr 10 entry | 導入者が settings へ写す | `settings` (同上) | loud (pane 内で `blocked`) |
| 10 | `dispatch-project.toml` の設置 | **`project_setup` が生成する** | `project_config` (config の直読み) | **silent** |
| 11 | `dispatch-project.toml` の repo が正しい置き場を指す | 導入者 | **doctor では検査できない** — `observe_issues` の `issues[].url` を目視 | **silent** |
| 12 | plugin 名 = `swat-skills` | publisher (install 側は変えられない) | `plugin_name` (配布物の宣言のみ。登録名は `/plugin list`) | **silent** |

**silent な 3 件 (#10 / #11 / #12) が、この機構で最も高くつく前提。** 誤った置き場の observer は「issue が closed」を判断なしで台帳へ `done` と書ける役なので、**別 repo の同番号 issue を読むと稼働中 worker の作業ツリー消失に直結する**。doctor が閉じたのは #10 (置き忘れ) までで、**#11 (綴り) は依然として人間の目視が唯一の検知経路**。

doctor の検査は MCP server の中で走る (Bash tool を通らない)。**同じ検査を script 化して Bash から回すと、sandbox が `herdr` の socket と `gh` の認証 token を塞ぐため、settings 不足と binary 不在の区別が失われる** — `project_doctor` を呼ぶ経路以外で前提を確かめない。

前提が揃ったら、起動規約 (cwd の取り方・`--remote-control` の指定) は plugin の README を参照する。
