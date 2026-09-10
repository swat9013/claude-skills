# dispatch 機構の前提条件

`orchestrator` skill と `dispatch-v2` MCP server + reconciler daemon からなる **dispatch 機構** (issue を選んで Claude Code セッションへ配車し、CL まで自走させる仕組み) を、**この plugin を入れた環境で動かせるか**を導入前に自力で判定するための文書。

対象読者は導入者 (人間)。実行時の手順は `SKILL.md` が正本で、本書は重複させない。

- **skill を使うだけなら本書は不要。** dispatch 以外の skill は追加の前提を持たない
- 前提を 1 つでも欠くと dispatch は動かない。ただし**黙って壊れるもの**と**起動時に止まるもの**があり、本書はその差を明示する (下表の「不成立時の見え方」列)

## 前提の層

```
  導入者が用意するもの                     環境で保証されるもの
  ────────────────────                     ──────────────────────
  herdr (必須)  ─┐                        ┌─ permissions.allow
  uv             │                        │   ├ Bash(herdr …) 10
  gh / glab      ├─► dispatch 機構 ◄──────┤   ├ Bash(git …) / Bash(gh …)
  dispatch-      │   (orchestrator /      │   └ mcp__plugin_swat-skills_
    project.toml │    worker /            │        dispatch-v2__*
  plugin 名      │    reconciler daemon)  ├─ sandbox.excludedCommands 8
    = swat-skills┘            ▲           └─ sandbox.filesystem.allowWrite
                              │                ~/.claude/dispatch-v2
                   Claude Code harness 機能
                   (受け入れた依存 — 代替実装を持たない)
```

**「環境で保証されるもの」は plugin では配れない。** Claude Code は plugin から `settings` を配信する手段を持たないので、この列は導入者が適用先 project の `.claude/settings.local.json` へ**自分で写す**必要がある (本書 §3 に全 entry を逐語で載せてあるのはこのため)。「保証される」と呼べるのは写し終えた後だけで、写す作業自体は導入者の仕事になる。

## 1. Claude Code harness 機能 (受け入れた依存)

dispatch 機構は次の harness 機能に**直接依存している**。無い環境で代替する経路は用意していない (fallback を持たない = 受け入れた依存)。

| 機能 | 使い方 | 無いとどうなるか |
|---|---|---|
| **cross-session messaging** (`SendMessage` / `ListAgents`) | orchestrator ↔ worker の質問中継 | worker の質問がどこにも届かない。**送信側にエラーは返らない** |
| `CLAUDE_CODE_MESSAGING_SOCKET` | 上記の受信可否の判定に使う。daemon が起動時に検査する | 未設定なら `session_spawn` が fail-closed で止まる (起動しない) |
| `claude --name <名前>` | 起動セッションの表示名。`ListAgents` の宛先解決に使う | 相手を名前で見つけられない |
| `claude --model` | worker の段階に応じた割り当て | 段階に関わらず既定モデルで走る |
| **plugin 同梱 MCP server** (`.mcp.json` + `${CLAUDE_PLUGIN_ROOT}`) | `dispatch-v2` の起動 | tool が 1 つも見えない |

**version 依存の落とし穴**: 旧 binary の resume セッションは messaging を**送信できても受信できない**。この差は `CLAUDE_CODE_MESSAGING_SOCKET` の有無に出るので、daemon は起動前に検査して止める。**現行 binary で新規起動したセッション**から orchestrator を起こすこと。

**v2 の Session 起動が組むのは `--model` と `--name` だけ**なので、worker の reasoning effort は agent の既定で決まる (`mcp/dispatch-v2/` の実装に `effort` の綴りは無く、tool の引数にも現れない)。段階ごとに effort を振り分けたいなら、**その必要性を実測してから** issue にする (v1 の固定値をそのまま持ち込まない)。

Remote Control (`--remote-control`) は**前提ではない**。有効にすると worker の質問と承認を claude.ai web / mobile から返せる。

## 2. herdr が必須 (tmux 非対応)

pane の起動・観測・rename は [herdr](https://github.com/HerdrHQ/herdr) (AI agent 向け terminal multiplexer) の CLI 経由でのみ行う。**tmux backend は実装していない** — SessionRuntime port は設計してあるが adapter が herdr の 1 つしか無い。

導入者が用意するもの:

| 前提 | 検査方法 | 不成立時の見え方 |
|---|---|---|
| `herdr` が PATH に在る | `herdr status` | Session 系 tool が `herdr status が失敗` で落ちる |
| **herdr session 内で Claude Code を起動している** | `echo $HERDR_ENV` が `1` | `HERDR_ENV=1 でない (herdr session の外で server が起動している)` |
| **herdr の Claude 連携 hook が現行版** | `herdr integration status` の出力に `claude: current` の行 | `herdr integration status に \`claude: current\` が無い (hook が古い / 未導入)`。導入は `herdr integration install claude` |
| herdr daemon へ疎通できる | `herdr status` が exit 0 | `herdr status が失敗 (socket に届かない)` |
| `HERDR_PANE_ID` / `HERDR_WORKSPACE_ID` が設定済み | `echo $HERDR_PANE_ID` | server 側: `HERDR_PANE_ID と HERDR_WORKSPACE_ID が揃わないので割り元を名乗らない` (stderr)、daemon 側: `session_spawn` が `anchor_handle / anchor_workspace が無い` で 400。**worker の pane は割り元の隣に開く**ので、割り元を名乗れない spawn は起こさない (ADR 0065) |

**この 4 検査はすべて loud に落ちる** (fail-closed)。前提不成立のまま誤 dispatch へ進む経路は無い。

その他、導入者が用意するもの:

| 前提 | 用途 | 検査方法 |
|---|---|---|
| `uv` | MCP server は PEP 723 script を `uv run --script` で起動する | `uv --version` |
| `gh` (認証済み) | GitHub tracker の観測・close・起票 | `gh auth status` |
| `glab` (認証済み) | GitLab tracker を使う場合のみ | `glab auth status` |
| `acli` (認証済み) | Jira を issue 置き場にする場合のみ | `acli jira auth status` |

**Jira 置き場の project は `[pr]` の宣言が必須。** Jira は変更置き場ではないので CL 置き場を継げない (§4)。加えて紐づき (issue → CL) を Jira から引けないため、**CL に到達したら `wo_record_cl` で台帳へ記録する**運用が要る — 記録しない WorkOrder は機械遷移で終わらず、非終端のまま残る ([ADR 0059](../../../docs/adr/0059-jira-tracker-adapter-with-ledger-supplied-cl-links.md))。

## 3. 必要な settings

適用先 project の `.claude/settings.local.json` に次を置く。**plugin からは配れない** (Claude Code に plugin → settings の配信手段が無い)。

### 3.1 `sandbox.excludedCommands` (8 entry)

```json
"gh:*", "glab:*", "herdr:*",
"git push:*", "git fetch:*", "git pull:*", "git ls-remote:*", "git merge:*"
```

対象は **Bash tool から走るコマンド**だけ。MCP server と daemon が内部で起動する subprocess (`gh` / `herdr` / `git`) は Bash tool を通らないので sandbox の対象外で、この列の影響を受けない。

理由と不成立時の見え方:

| entry | 理由 | 欠けたときの見え方 |
|---|---|---|
| `herdr:*` | socket connect が sandbox 内で通らない | orchestrator 起動手順 0 の `herdr pane rename` が落ちる |
| `gh:*` / `glab:*` | 認証 token (`~/.config/gh/config.yml` 等) が sandbox の credential 保護で読取禁止 | worker の `gh issue view` / `gh pr create` と、orchestrator の issue close / 起票が起動ごと失敗する |
| `git push:*` / `git fetch:*` / `git pull:*` / `git ls-remote:*` | sandbox が注入する SOCKS5 proxy 経由の SSH が、proxy 認証の有無でセッションごとに通ったり通らなかったりする | worker が CL を push できない。**failure はセッション依存で再現しない** |
| `git merge:*` | sandbox 組み込みの自己改変保護が `hooks/` / `.claude/hooks` / `.claude/skills` / `.claude/agents` への write を拒む (設定で解除できない) | **これらを in-tree で持つ repo で最も高くつく** — merge が `Operation not permitted` で落ち、**HEAD 据え置き + working tree だけ書き換わった中途半端な状態**になる |

**除外の照合は「コマンドの綴り」ではなく Bash 呼び出しの top-level segment に対して行われる。** `;` / `&&` / `||` / `|` で切った断片の先頭 token だけが見られ、`for` / `while` / `if` の**本体は分割されない**。ループ本体にしか `gh` / `git merge` が無い呼び出しは除外に当たらず、**呼び出し全体が sandbox 内**で走る。**除外対象コマンドは 1 呼び出しにつき top-level の 1 断片として書く** ([#652](https://github.com/swat9013/swat-skills/issues/652)、Claude Code v2.1.237 で実測)。

**除外対象コマンドへ渡す path は `$TMPDIR` に依存できない。** sandbox 外へ出た呼び出しから見た `$TMPDIR` は sandbox 内のそれと別ディレクトリを指すので、sandbox 内で `$TMPDIR/body.md` に書いた本文を `gh issue create --body-file "$TMPDIR/body.md"` で渡すと gh が読めない。セッションの scratchpad の絶対パスで書く。

`git push:*` を sandbox 外に出す以上、force push を止めるのは permission 層だけになる。`permissions.deny` の `Bash(git push --force:*)` / `Bash(git push -f:*)` を**対で維持する**こと。

### 3.2 `sandbox.filesystem.allowWrite`

```json
"~/.claude/dispatch-v2"
```

台帳 (`events.jsonl`) と宣言 config (`dispatch-project.toml`)・SQLite 投影の置き場。**daemon 自身の書き込みには要らない** (daemon は Bash sandbox の外) が、**セットアップ時に session が Bash / Write でここへ config を置く経路**に要る。欠けると config の設置が deny され、§4 の宣言が置けない。

根の path は環境変数 `DISPATCH_V2_ROOT` で移せる。移した環境では allowWrite もその path に合わせる。

### 3.3 `permissions.allow` — herdr (10 entry)

```json
"Bash(herdr status:*)", "Bash(herdr integration status:*)",
"Bash(herdr pane list:*)", "Bash(herdr pane split:*)", "Bash(herdr pane rename:*)",
"Bash(herdr pane run:*)", "Bash(herdr pane get:*)", "Bash(herdr pane read:*)",
"Bash(herdr pane close:*)", "Bash(herdr wait:*)"
```

### 3.4 `permissions.allow` — dispatch-v2 の MCP tool

```json
"mcp__plugin_swat-skills_dispatch-v2__wo_create",
"mcp__plugin_swat-skills_dispatch-v2__wo_transition",
"mcp__plugin_swat-skills_dispatch-v2__wo_annotate",
"mcp__plugin_swat-skills_dispatch-v2__wo_report_outcome",
"mcp__plugin_swat-skills_dispatch-v2__wo_record_cl",
"mcp__plugin_swat-skills_dispatch-v2__wo_forget_cl",
"mcp__plugin_swat-skills_dispatch-v2__wo_list",
"mcp__plugin_swat-skills_dispatch-v2__wo_get",
"mcp__plugin_swat-skills_dispatch-v2__observe_candidates",
"mcp__plugin_swat-skills_dispatch-v2__observe_cls",
"mcp__plugin_swat-skills_dispatch-v2__observe_sessions",
"mcp__plugin_swat-skills_dispatch-v2__inbox_read",
"mcp__plugin_swat-skills_dispatch-v2__inbox_ack",
"mcp__plugin_swat-skills_dispatch-v2__session_spawn",
"mcp__plugin_swat-skills_dispatch-v2__session_send",
"mcp__plugin_swat-skills_dispatch-v2__session_close",
"mcp__plugin_swat-skills_dispatch-v2__worktree_sweep",
"mcp__plugin_swat-skills_dispatch-v2__worktree_tidy"
```

`dashboard_url` / `dashboard_open` は上の列挙に含めていない (前者は人間へ URL を返すだけ、後者は人間が画面を求めたときにしか呼ばれない副作用付きの口なので、どちらも都度承認で足りる)。**正本は `settings/settings.local.json`** — 上の code block はそこから写したもの。**server 全体を wildcard 1 entry で許可する記法は採らない** — 記法の裏付けが取れておらず、個別列挙のほうが確実で最小権限になる。

**欠けたときの見え方**: tool 呼び出しのたびに permission ダイアログが出る。worker は pane 内で `blocked` のまま止まり、**メッセージでは解除できない** (人間が pane に入って答える必要がある)。

### 3.5 `permissions.allow` — git / gh

worker が実装から CL まで自走するために要る最小セット。既定 template では `Bash(git commit:*)` / `Bash(git push:*)` / `Bash(git pull:*)` / `Bash(git merge:*)` / `Bash(git worktree:*)` / `Bash(gh issue view:*)` / `Bash(gh pr create:*)` などを許可している。**`git merge:*` は allow と `excludedCommands` の両方が要る** (permission 層と sandbox 層は別)。

**orchestrator は tracker への書き込みを CLI で行うので、`Bash(gh issue close:*)` / `Bash(gh issue create:*)` (GitLab なら `glab` 側) が要る。** v2 の tool 面は tracker へ書く tool を持たないため、この 2 つは条件付きではなく **dispatch を入れる全 project の要件**になった。**不成立時は loud** — close / 起票のたびに permission ダイアログで止まり、orchestrator は user が見ている pane なのでその場で気づける。

**deploy (`git pull --ff-only`) は daemon が行う。** daemon は Bash tool を通らないので permission entry は要らないが、稼働 clone の `hooks/` を sandbox の自己改変保護が守る状況では pull が半適用になりうる — daemon はそのとき `deploy_degraded` を escalation する (止めない)。

### 3.6 network

sandbox の `network.allowedDomains` に tracker の host を含める。GitHub なら `github.com` / `api.github.com` / `*.githubusercontent.com` / `codeload.github.com`。

## 4. 宣言 config (issue 置き場 / CL 置き場 / 機械作用の可否)

**どの tracker のどの repo を issue 置き場にするかは、台帳ディレクトリ直下の `dispatch-project.toml` が宣言する。** daemon がこれを解決し、観測する置き場を決める。同じ file が機械作用 (rule) の可否も持つ。

置き場所 — **台帳と同じディレクトリ**:

```
~/.claude/dispatch-v2/<project-key>/
├── dispatch-project.toml   # 宣言 (本書の対象)
└── events.jsonl            # 台帳の正本 (append-only。現況は fold で作る)
```

**`state.json` は無い。** 現況は常に events の fold なので、台帳を読みたいときは `wo_list` / `wo_get` を通すか `events.jsonl` を grep する。

`<project-key>` は project の anchor repo の remote URL から導く。`git@github.com:swat9013/swat-skills.git` → `github.com__swat9013__swat-skills`。remote を持たない repo は main worktree 実パス由来の `path__…` に倒れる。

書式 — 書ける table は `[issue]` / `[pr]` / `[rules]`、key は `[issue]` が `tracker` / `repo` / `ready_label`、`[pr]` が `tracker` / `repo`、`[rules]` が rule id:

```toml
[issue]
# gh | glab | jira (adapter を持つ tracker だけ)
tracker = "gh"
# 置き場の識別子。GitHub は owner/name、GitLab は group/project、Jira は project key (SWATCF)
repo = "swat9013/swat-skills"
# 候補プールを表す triage label。消すと候補観測が止まる
ready_label = "ready-for-agent"

# [pr] は CL 置き場が issue 置き場と違うときだけ書く。省略すると issue 置き場をそのまま継ぐ。
# tracker = "jira" のときは必須 (Jira は変更置き場ではないので継げない)
[pr]
repo = "swat9013/swat-skills"

# 機械作用の可否。既定は deploy_ff_only = true / close_idle_… = false
[rules]
deploy_ff_only = true
close_idle_session_on_terminal_workorder = false
```

**未知の table / key / rule は名指しで loud に落ちる。** 綴りを間違えた宣言が黙って既定値のまま通ると、「off にしたつもりの機械作用が動き続ける」「読まない宣言が効いていると誤読する」という最も危ない読み違いが起きる。**v1 の宣言を丸ごと copy して置いた環境はここで落ちる** — v2 は `close_on_merge` / `done_status` / `claim_label` / `[worker]` を受け取らない。

**2 系統 (`[issue]` / `[pr]` と `[rules]`) は独立している。** `[rules]` だけを置いた project は合法で、置き場を宣言していないだけ。

**tracker をまたいだ置き場が書けるかは issue 置き場が決める。** gh / glab は自分で紐づきを引くので、別 tracker の CL 置き場を指すと名指しで落ちる (その置き場の CL が 1 件も現れず、merge 済みの issue が `abandoned` へ落ちるため)。jira は引かないので台帳の記録が源になり、**issue = Jira / CL = GitLab は合法**。

**`ready_label` は既定を持たない。** その環境の triage 体系が決める語彙で、推測できる綴りが無い。外した綴りは error にならず 0 件を返し、**「候補が 1 件も無い」と読める形で dispatch が静かに止まる**。gh 置き場では**宣言した綴りを置き場 repo に実在させる**。

宣言が無い / `[issue]` が無い project は異常ではないが、**その置き場は観測されない** — `observe_candidates` が `candidates: null` + `reason` を返す。

**config を編集したら daemon の再起動が要る。**

## 5. plugin 名の契約 — `swat-skills` 固定

**plugin 名は `swat-skills` 固定が配布契約。** dispatch 機構は plugin 名を文字列で直書きしている:

| 表記 | 例 |
|---|---|
| MCP tool 完全名 | `mcp__plugin_swat-skills_dispatch-v2__<tool>` |
| `/mcp` 上の server 名 | `plugin:swat-skills:dispatch-v2` |

**Claude Code に plugin 名を与える substitution token は存在しない** ため、動的に解決する手段が無い。

導入者にとっての含意は 2 点だけ:

- **plugin 名の正本は publisher 側の宣言**。skills-dir 配布なら `.claude-plugin/plugin.json` の `name`、marketplace 配布なら marketplace entry の `name`。どちらも install 側では変えられない
- **symlink 先のディレクトリ名は plugin 名に影響しない**

つまり install 側の操作で plugin 名がずれる経路は無く、**導入者が守るべきことは実質無い**。契約が効くのは publisher が rename したときで、そちらは repo 内の pre-commit gate が直書きとの不整合を止める。

## 6. 導入チェックリスト

| # | 前提 | 誰が用意するか | 不成立 |
|---|---|---|---|
| 1 | 現行 binary で新規起動した Claude Code セッション (`CLAUDE_CODE_MESSAGING_SOCKET` が設定済み) | 導入者 | loud |
| 2 | herdr が PATH に在る / daemon 稼働 | 導入者 | loud |
| 3 | herdr session 内での起動 (daemon 側 `HERDR_ENV=1` / 呼び出し側 `HERDR_PANE_ID` + `HERDR_WORKSPACE_ID` 非空) | 導入者 | loud |
| 4 | herdr の Claude 連携 hook (`claude: current`) | 導入者 | loud |
| 5 | `uv` | 導入者 | loud (server が起動しない) |
| 6 | `gh` / `glab` 認証 | 導入者 | loud |
| 7 | `sandbox.excludedCommands` 8 entry | 導入者が settings へ写す | 項目ごとに異なる (§3.1) |
| 8 | `sandbox.filesystem.allowWrite` に台帳ディレクトリ | 導入者が settings へ写す | loud (config を置けない) |
| 9 | `permissions.allow` の MCP tool + herdr entry | 導入者が settings へ写す | loud (pane 内で `blocked`) |
| 10 | `permissions.allow` の `gh issue close` / `gh issue create` (GitLab なら `glab` 側) | 導入者が settings へ写す | loud (close / 起票のたびに `blocked`) |
| 11 | `dispatch-project.toml` の設置 (置き場 + `ready_label` の宣言) | 導入者 | 置き場が無いと **loud** (`candidates: null` + `reason`) |
| 12 | `dispatch-project.toml` の repo と `ready_label` の**綴り**が実在の置き場 / label を指す | 導入者 | **silent** (repo は別の置き場を観測し続ける / label は候補が 1 件も現れず dispatch が止まる) |
| 13 | issue 置き場が `gh` の project でだけ: `ready_label` が置き場 repo に実在する | 導入者 (`gh label list` で確認) | loud (候補が 1 件も返らない) |
| 14 | plugin 名 = `swat-skills` | publisher (install 側は変えられない) | **silent** |

**silent な 2 件 (#12 / #14) が、この機構で最も高くつく前提。** 誤った置き場を観測している daemon は「issue が closed」を根拠に WorkOrder を終端へ落とすので、**別 repo の同番号 issue を読むと稼働中 worker の作業ツリーが回収資格を得る**。

**v1 (`dispatch-ops`) が持っていた `project_setup` / `project_doctor` は v2 の tool 面に無い。** 宣言 config は手で置き、前提は本表を手で確かめる。

```sh
mkdir -p ~/.claude/dispatch-v2/github.com__swat9013__swat-skills
printf '[issue]\ntracker = "gh"\nrepo = "swat9013/swat-skills"\nready_label = "ready-for-agent"\n' \
  > ~/.claude/dispatch-v2/github.com__swat9013__swat-skills/dispatch-project.toml
```

前提が揃ったら、起動規約 (cwd の取り方・`--remote-control` の指定) は plugin の README を参照する。

## 設計

前提ではなく、機構がこの形を採っている理由。導入判断には要らないが、手順の意図を疑ったときに読む。

- **issue-less の worker spawn 経路は持たない** (user から頼まれた小作業も issue を起票してから通常経路で dispatch する) — WorkOrder・CL の closing reference・作業ツリー名がいずれも issue ref を key にしているため、key を持たない spawn を足すと台帳と観測の全体に「key の無い WorkOrder」の分岐が増える
- **機械が実行してよいのは記帳と escalation の発行まで**で、外部への作用は宣言で個別に有効化する `[rules]` に限る。**作業ツリーの削除 (不可逆) は機械に渡さない** — `worktree_sweep` が資格を判定し、`worktree_tidy` の名指し 1 回で人 (orchestrator) が回収する
- **escalation の配送は pull が正で、push は「見に来い」だけの空 nudge**。内容を pull に限定するので push の配送失敗が沈黙しても取りこぼしにならない
