---
name: issue-dispatch
disable-model-invocation: true
argument-hint: "[max]"
description: herdr (AI agent 向け terminal multiplexer) session 内で、ブロックされていない open issue を優先度順に取り出し、空き slot 分だけ対応 skill + issue 番号入りの Claude Code セッションを、呼び出し元と同じ workspace の分割 pane として起動する dispatcher。起動後は監視ループに入り、issue CLOSED / claude 終了の pane を回収して空き slot へ次候補を自動補充する。起動時と毎サイクルで default branch の最新化と merged branch/worktree の掃除も行う。
---

# issue-dispatch

herdr session 内で cwd repo の open issue から着手可能なものを優先度順に選び、**規定セッション数まで** Claude Code セッションを起動する dispatcher。展開先は**この skill を呼び出したセッションが居る herdr workspace の分割 pane** — user は同じ workspace 内で全セッションを見渡し、pane 移動で直接介入できる。各 pane の初期 prompt に「呼び出す skill + issue 番号」を含めるので、起動後は各 pane が自走する。dispatch 後は監視ループ (手順 7) で完了 pane を回収し、キューが尽きるまで slot を回転させる。起動時と監視ループの毎サイクルで repo の最新化と merged branch/worktree の掃除 (手順 3) も行う。

setup-matt-pocock-skills 由来の canonical triage labels + wayfinder labels 運用を前提とする。

## args

`/issue-dispatch [max]` — max は同時稼働セッション数の上限 (省略時 3)。

## script 起動規約

決定論部分は 3 script に外出ししてある。生の `gh` / `glab` / `herdr` / `git` を手で叩かず script を使う。除外・整列・fallback・template の規則は各 script の docstring とテストが正本 — 本書には書かない:

- [`scripts/dispatch_tracker.py`](scripts/dispatch_tracker.py) — tracker 操作 (detect / candidates / blocked / claim / unclaim / states)。テスト: repo root の `tests/test_dispatch_tracker.py`
- [`scripts/herdr_ops.py`](scripts/herdr_ops.py) — herdr 操作 (preflight / slots / spawn / watch / cleanup)。テスト: repo root の `tests/test_herdr_ops.py`
- [`scripts/repo_tidy.py`](scripts/repo_tidy.py) — repo 最新化 + merged branch/worktree 掃除 (run)。テスト: repo root の `tests/test_repo_tidy.py`

**起動は必ず「literal な tilde path を command 名にした単文」で行う。1 Bash 呼び出し = 1 script 起動:**

```bash
~/.claude/skills/swat-skills/skills/util/issue-dispatch/scripts/herdr_ops.py preflight
```

sandbox の `excludedCommands` 照合は command 文字列のテキスト一致であり、登録 entry (tilde 表記) と別形の起動 — `${CLAUDE_SKILL_DIR}` 展開 (絶対 path になる) / 変数間接 (`"$DISPATCH" ...`) / compound command (for loop・`VAR=x` 前置・`&&` 連結) — は照合されず sandbox 内に落ちる。落ちると subprocess の gh は `~/.config/gh` 読取 deny、herdr は socket connect deny で即死する (fail-closed なので誤 dispatch には進まないが、dispatch は不能)。

## 手順

### 0. 前提確認

```bash
~/.claude/skills/swat-skills/skills/util/issue-dispatch/scripts/herdr_ops.py preflight
```

`.ok` が false なら `.failed` に応じて user に修正依頼して終了する:

| `.failed` | 案内 |
|---|---|
| `herdr_env` | herdr session 外。herdr session 内でセッションを起動し直してもらう (herdr 以外の terminal multiplexer は非対応) |
| `hook` | `herdr integration install claude` の実行を依頼 (`not installed` / stale 版とも同扱い)。hook は session identity を herdr に報告して pane と Claude session を 1:1 対応させる。無いと agent field が populate されず起動確認が壊れる |
| `socket` | `.claude/settings.local.json` の `sandbox.excludedCommands` に `"herdr:*"` の追加を依頼 (settings の自己編集は拒否されうるため user に依頼)。socket は存在するが sandbox が connect を `PermissionDenied` で遮断している状態 |

preflight は自 pane の残骸 label (`i<番号>`) を `dispatch` へ自動 rename する — dispatcher 自身の pane が issue label のままだと slot を 1 消費し、当該 issue が稼働中と誤判定されて候補から漏れる。`.self.renamed_from` が非 null なら手順 6 の報告に添える (その issue は実際には稼働していない)。

### 1. tracker 判定

```bash
~/.claude/skills/swat-skills/skills/util/issue-dispatch/scripts/dispatch_tracker.py detect
```

`.tracker` (gh / glab) を以降の `--tracker` に使う。exit 1 (判定不能) なら user に確認する。

### 2. 空き slot 算出

```bash
~/.claude/skills/swat-skills/skills/util/issue-dispatch/scripts/herdr_ops.py slots --max 3
```

dispatch 済みセッションは自 workspace 内の pane label `i<番号>` で管理する (workspace 限定・自 pane 除外は script が行う)。`.active` は手順 3・4 の両方に渡す。`.free` が 0 なら手順 3 を実行したうえで「満席」と報告して終了する (最新化と掃除は満席でも行う)。

### 3. repo tidy (最新化 + 掃除)

```bash
~/.claude/skills/swat-skills/skills/util/issue-dispatch/scripts/repo_tidy.py run --cwd /path/to/repo --active "215,221"
```

default branch の最新化 (fetch --prune → checkout → pull --ff-only) と、merged になった local branch / worktree の回収を行う。`--cwd` は repo root を literal で渡す (省略時 cwd)。`--active` は手順 2 の `.active` を comma 区切りで渡す — **稼働中 pane の branch / worktree を掃除対象から外す唯一の手段**で、渡し忘れると PR merge 直後にまだ生きているセッションの作業ツリーを消しうる。

**tidy は fail-open** — exit 1 でも dispatch は止めず、次の手順へ進んで報告に載せる (dispatch 本体の fail-closed とは扱いが逆)。読む先は 3 つ:

| 出力 | 意味 | 扱い |
|---|---|---|
| `.pull.ok` が false | `.pull.error` の通り fetch / checkout / pull が失敗した (root が dirty で checkout が拒まれた・main に local commit がある 等) | 報告に載せて続行。掃除フェーズは default branch 基準なので checkout 失敗でも正しく効く |
| `.skipped` / `.excluded` | dirty worktree / 稼働中 pane / protected branch のため残したもの | 報告に載せるのみ (正常データ) |
| `.failed` | worktree remove 失敗 / 未 merge で `branch -d` が拒否 | 報告に載せて user 判断に委ねる (`-D` 相当の強制削除は script も skill も行わない) |

前提不成立 (work tree 外 / linked worktree からの呼び出し / default branch 解決不能) は JSON 無しの exit 1 になる。この場合は tidy を諦めて先へ進み、最終報告に残す。

### 4. 候補取得と優先度整列

```bash
~/.claude/skills/swat-skills/skills/util/issue-dispatch/scripts/dispatch_tracker.py candidates --tracker gh --active "215,221"
```

`--active` は手順 2 の `.active` を comma 区切りで (空なら空文字)。`candidates` は dispatch すべき順に整列済み (downstream-first: 完成に近い段階から slot を埋める設計)。先頭から手順 5 に流す。`excluded` (番号 + 理由) は手順 6 の報告に使う。

### 5. dispatch loop

上位候補から 1 件ずつ以下 3 コマンドを通し、slot が埋まるか候補が尽きるまで回す (起動規約どおり各コマンドは単文 — shell の for loop に畳まない):

1. **blocker 検査** (通過候補のみ検査する — 全件検査しない):

   ```bash
   ~/.claude/skills/swat-skills/skills/util/issue-dispatch/scripts/dispatch_tracker.py blocked 217 --tracker gh
   ```

   `.blocked` が true なら skip して次候補へ (`.open_blockers` を skip 理由の報告に使う)。**exit 1 (検査失敗) も blocked 扱いで skip する** — 検査不能を「blocker 無し」と読まない (fail-closed。i217 誤 dispatch は gh の実行失敗を「field 欠落」と誤読して素通ししたのが真因)

2. **claim**: assignee を自分に設定して二重 dispatch を防ぐ:

   ```bash
   ~/.claude/skills/swat-skills/skills/util/issue-dispatch/scripts/dispatch_tracker.py claim 217 --tracker gh
   ```

   exit 1 (claim 失敗) なら stderr を確認し、起動せず次候補へ

3. **spawn**: pane split → label `i<番号>` 付与 → claude 起動 → agent 検出までを一括で行う:

   ```bash
   ~/.claude/skills/swat-skills/skills/util/issue-dispatch/scripts/herdr_ops.py spawn 217 --stage wayfinder:task --cwd /path/to/repo
   ```

   `--stage` は candidates の `.stage` をそのまま渡す (段階 → 初期 prompt / 段階 → model / model → effort の対応は script が正本。`--prompt` / `--model` / `--effort` で上書き可)。`--cwd` は repo root を literal で渡す (省略時 cwd)。`ready-for-agent` は Claude Code 側 `--worktree i<番号>` で作業ツリーが自動隔離される (`herdr worktree create` は使わない — 二重管理になる)。exit 1 (`.ok` false = agent 未検出 / herdr 失敗) は起動失敗として `.pane_id` を添えて報告し、`dispatch_tracker.py unclaim <n> --tracker <T>` で assignee を外してから次候補へ (外し忘れると当該 issue が candidates から永久除外される)。同一 issue の spawn 再失敗は会話内台帳で検知し、以後そのセッション中は skip する (無限再試行防止)

### 6. 初回報告

以下 5 点を user に提示してから監視ループ (手順 7) に入る:

1. 起動した pane 一覧 (`i<番号>` / 段階 / pane_id)
2. loop 内で skip した候補とその理由 (open blocker / claim 失敗 / 起動失敗)
3. 未検査で終わった残候補数 (blocker 未検証である旨を添える)
4. tidy の結果 (`.pull` の成否 / 回収した branch・worktree / dirty や未 merge で残したもの)
5. 参加方法 — 同じ workspace 内に展開済み。herdr の keybinding (`prefix+...` — bind は user config 依存) で pane 移動 / zoom して介入する

preflight で `.self.renamed_from` が非 null だった場合はその旨も添える (残骸 label を剥がしたので当該 issue は候補に戻っている)。

### 7. 監視ループ

初回報告の後は終了せず、完了 pane の回収と slot 補充を回す。**追跡 pane が 0 かつ未着手候補が 0 になったら**最終報告して終了する。回収した pane / unclaim した issue は自分の会話内で台帳として保持する (状態ファイルは持たない — 真実源は assignee と pane label)。

1. **待機**:

   ```bash
   ~/.claude/skills/swat-skills/skills/util/issue-dispatch/scripts/herdr_ops.py watch --timeout-sec 240
   ```

   `i<番号>` pane のいずれかで claude が終了する (`agent_exited`) / pane が消える (`pane_gone`) か、`timeout` で返る。**どの event でも次の照合手順は同一** — event は報告用の参考情報で、完了判定は issue state が正。`no_panes` (追跡 pane 0) なら本節 4 (補充) へ。この Bash 呼び出しは tool の timeout を 300000ms 以上に明示指定する — 指定しないと default 120 秒で watch (既定 240 秒) が途中終了させられ、`timeout` event が返らない。

2. **完了照合**: `.panes` の issue 番号 + 自分が dispatch したのに `.panes` から消えた issue 番号をまとめて照合する:

   ```bash
   ~/.claude/skills/swat-skills/skills/util/issue-dispatch/scripts/dispatch_tracker.py states --tracker gh --issues "217,222"
   ```

   exit 1 (照合失敗) ならループを中断して user に報告する — 照合不能のまま回収・unclaim に進まない (fail-closed)。

3. **回収** (pane ごとに判定):

   | 状態 | 操作 |
   |---|---|
   | issue CLOSED | `herdr pane close <pane_id>` (完了回収。agent 生存中でも issue が閉じていれば仕事は済んでいる)。続けて `~/.claude/skills/swat-skills/skills/util/issue-dispatch/scripts/herdr_ops.py cleanup <n> --cwd <repo>` で worktree を回収する (`.reason` が dirty なら未回収変更ありとして報告に載せ、削除しない) |
   | agent=null かつ issue OPEN | `herdr pane close <pane_id>` + `dispatch_tracker.py unclaim <n> --tracker <T>` (セッション死亡 — issue をキューへ返す)。cleanup は呼ばない (worktree の WIP を温存し、再 dispatch での再開余地を残す) |
   | pane 消失かつ issue OPEN | `unclaim <n>` のみ (assignee 残留は candidates から永久除外になる) |
   | agent 生存かつ issue OPEN | 触らない (permission 待ち・質問待ちの可能性。サイクル報告に載せるのみ) |
   | pane 消失かつ issue CLOSED | 操作不要だが cleanup <n> は呼ぶ (worktree が残っている可能性があるため)。最終報告に記録する |

4. **tidy**: 回収の直後に**毎サイクル**実行する (merged branch が生まれるのは PR merge = 回収の瞬間なので、この位置が最も効く):

   ```bash
   ~/.claude/skills/swat-skills/skills/util/issue-dispatch/scripts/repo_tidy.py run --cwd /path/to/repo --active "221"
   ```

   `--active` には**本節 3 で「触らない」と判定して残した追跡 pane の issue 番号**を渡す (回収済みの番号は含めない — 含めるとその worktree/branch が永久に掃除されない)。読み方と fail-open の扱いは手順 3 と同一。結果はサイクル報告に積む。

5. **補充**: 手順 2 / 4 / 5 (slots → candidates → blocked → claim → spawn) を再実行して空き slot を埋める。新しい skip / 起動失敗はサイクル報告に積む

6. **終了判定**: 追跡 pane 0 かつ未着手候補 0 → 最終報告して終了。それ以外は 1 へ戻る。最終報告に含める: 回収した pane (issue / 完了・死亡の別) / unclaim してキューへ返した issue / 触らず残した生存 pane / 残 excluded / 回収した worktree と dirty で残した worktree / tidy の累計 (削除した branch・worktree と、dirty・未 merge・失敗で残したもの) / tidy が最後まで解消しなかった `.pull` 失敗 (user 介入が要る)

## 責務境界

dispatch → 監視ループ → 全回収で止まる。以下は起動された各セッション / user / 別 skill の領分:

- 実装・調査・triage の中身 (各 pane の仕事)
- issue を CLOSED にする操作 (各セッションの仕事 — dispatcher は CLOSED を観測して回収するだけで、close 判断はしない)
- agent が生存したまま停滞している pane への介入 (permission 待ち・質問待ちは user が pane に入って解く。dispatcher は報告のみ)
- label 遷移 (各セッションが行う)
- 外部タスク管理サービスとの連携は行わない (dispatch の入出力は issue tracker に閉じる)
- tidy が残したもの (dirty worktree / 未 merge branch / `.pull` 失敗の原因になった root の dirty・local commit) の後始末。dispatcher は merged かつ clean なものだけを回収し、判断が要るものは報告して user に返す

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| script が `gh failed ... operation not permitted` / `herdr ... PermissionDenied` を返す (settings 反映済みなのに) | 起動形が `excludedCommands` の tilde entry とテキスト一致しない: `${CLAUDE_SKILL_DIR}` 展開・絶対 path・変数間接・for loop 等の compound command 経由 | 起動規約どおり literal tilde path の単文で起動し直す |
| `dispatch_tracker.py` が `gh failed ... failed to read configuration` (TrackerError) を返す | 配布 settings の `sandbox.excludedCommands` に script が未登録の環境では、subprocess の gh が sandbox 内に落ち `~/.config/gh` 読取 deny で即死する (単発の `gh ...` だけ `gh:*` 除外で成功するため「gh 自体は動くのに」と混乱しやすい) | 配布 settings (`settings/settings.local.json`) の 6 entry (dispatch_tracker.py / herdr_ops.py / repo_tidy.py の各 2 形式 = `permissions.allow` + `sandbox.excludedCommands`) を反映する。反映まで dispatch は不能 — fail-closed なので誤 dispatch には進まない (repo_tidy.py だけ未反映なら tidy が sandbox 内で git fetch に失敗するが、fail-open なので dispatch は続く) |
| dispatch した覚えのない issue が候補から漏れる / slot が 1 埋まっている | dispatcher 自身の pane label が前回 dispatch の残骸 `i<番号>` のまま | preflight が `dispatch` へ自動 rename して解消する (`.self.renamed_from` で検知)。preflight を飛ばして slots を直接叩かない |
| spawn が `.ok: false` (agent が null のまま) | ① poll (2 秒 × 5 回) 内に screen manifest 未検出 ② `--prompt` 上書き文の崩れで pane の shell が誤解釈 | `herdr pane read <pane_id> --source recent --lines 40` で pane 内を調査。`herdr pane list --workspace <WS>` から pane_id が消えていれば command line で shell が exit した死亡確定 (zombie pane はない) — 次候補で再試行 |
| `herdr wait agent-status` が `internal_error: failed to decode pane get error` | 対象 pane が既に消えている | `wait` の前に `herdr pane get <pane_id>` が `pane_not_found` を返さないことを確認する |
| 同一 issue に 2 セッション | claim (assignee) 前に spawn した / 別マシンから同時 dispatch | 手順 5 の claim → spawn の順序を守る。pane label `i<番号>` の重複チェックも二重の防波堤 |
| slot が回復しない | agent が生存したまま停滞している pane (permission 待ち・質問待ち) は監視ループの回収対象外 | user が pane に入って解消する。issue CLOSED / agent=null の pane は監視ループ (手順 7) が自動回収する |
| 過去に claim した issue が二度と候補に出ない | セッションが完遂せず assignee が残留 | assignee を外せば候補に戻る (`dispatch_tracker.py unclaim <n> --tracker <T>`) |
| 実装セッション同士が file 衝突 | 同一 working tree で並列書き込み | `ready-for-agent` 段階は spawn が `--worktree i<番号>` を自動付与して隔離する。`herdr worktree create` は使わない (Claude Code 側 `--worktree` と二重管理を避ける) |
| spawn / script 起動が単発で `PermissionDenied` になる (retry で通る) | sandbox の classifier が一時停止していると Bash 実行が拒否され得る (systemreminder に `claude-sonnet-5[1m] is temporarily unavailable` が出る)。herdr socket の恒久拒否と混同しやすい | preflight の socket 検査が通っていれば socket は生きている。1 回だけ retry する。それでも通らないなら classifier 復旧を数分待つ。sandbox 恒久拒否なら preflight も失敗するので切り分け可能 |
| worktree が堆積する | pane close で claude を殺すと claude 自身の worktree 掃除 (終了時プロンプト) が走らない | issue CLOSED の回収時に cleanup <n> が、merge 済みの取りこぼしは毎サイクルの tidy が回収する。dirty で残ったものは最終報告を見て user が消す |
| tidy が `.pull.ok: false` を返し続ける | root の working tree が dirty で checkout が拒まれた / default branch に push していない local commit があり `pull --ff-only` が進めない | user が root を整理する (tidy は fail-open なので dispatch 自体は続く)。dispatcher は root を強制的に綺麗にしない |
| merge 済みなのに branch / worktree が残る | ① 対応 worktree が dirty (`.skipped`) ② 稼働中 pane の branch (`.excluded` が `active-pane`) ③ `branch -d` が未 merge と判定 (`.failed`) | ①③ は user 判断で消す (`-D` 相当は script も skill も行わない)。② は pane 回収後の次サイクルで自動的に対象へ戻る |
| 起動時に居た branch が消えている | tidy が default branch へ checkout した後、その branch は「現在 HEAD」ではなくなり、merged なら削除対象に入る (git tidy 同等の挙動) | issue-dispatch は default branch から起動する前提。作業中の branch に居る状態で起動しない |
| `repo_tidy.py` が JSON 無しで exit 1 する | linked worktree から呼んだ / work tree 外 / default branch を解決できない (origin/HEAD も local main/master も無い) | `--cwd` に repo root を literal で渡す。dispatcher は repo root から起動する前提 |
