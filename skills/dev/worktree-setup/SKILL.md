---
name: worktree-setup
disable-model-invocation: true
description: >-
  対象リポジトリに Claude Code の worktree 並列セッション環境をセットアップする。
  settings の worktree.sparsePaths / worktree.symlinkDirectories、.worktreeinclude
  (gitignored ファイルのコピー)、worktree 内で 1 回だけ走る初期化 hook を
  repo に合わせて選定・整備する。
  Use when「worktree セットアップ」「worktree 環境構築」「.worktreeinclude」
  「sparsePaths」「symlinkDirectories」「worktree に .env がコピーされない」
  「worktree ごとに node_modules を作りたくない」.
---

# worktree 環境セットアップ

対象リポジトリで 1 回実行し、worktree 並列セッション (`EnterWorktree` / `claude --worktree` / Agent `isolation: "worktree"`) 用の環境を整備する。チーム共有資産としてまとめて commit する。

| 成果物 | 役割 |
|---|---|
| `.claude/settings.json` の `worktree` | `sparsePaths` (checkout 範囲の絞り込み) / `symlinkDirectories` (大容量ディレクトリの共有) |
| `.worktreeinclude` | gitignored なローカル設定の worktree 作成時コピー |
| `<対象repo>/.claude/scripts/worktree-setup.sh` + `.claude/settings.json` の hook 登録 | repo 固有の初期化を worktree 内で 1 回だけ実行 (初期化コマンドがある場合のみ) |

repo に必要な機構だけ設置する。3 点すべてが要る repo は少ない。

## 反映方式の選択 (最初に決める)

初期化コマンドの実行方法は 2 通りあり、**既定は「追加型」**。

| 方式 | 実体 | native 機構 (`.worktreeinclude` / `sparsePaths` / `symlinkDirectories`) |
|---|---|---|
| **追加型 (既定)** | worktree の中で走る hook (`SessionStart` / `PostToolUse:EnterWorktree` / `SubagentStart`) | **そのまま効く** |
| 置き換え型 (例外) | `WorktreeCreate` hook が worktree 生成ごと担う | **3 つとも無効化される** — script 側で再現が必要 |

置き換え型を選ぶのは次のどちらかに当てはまるときだけ:

- 初期化に失敗したら worktree 作成自体を中止したい (原子性が要る)
- git 以外の VCS で worktree 相当を作る

該当しなければ追加型にする。初期化コマンドが不要な repo は hook 自体を設置せず、settings + `.worktreeinclude` だけで完了する。

## 検証済み仕様 (公式 + 実証 — 記憶で推定しない)

出典: https://code.claude.com/docs/en/worktrees / https://code.claude.com/docs/en/hooks / https://code.claude.com/docs/en/large-codebases#check-out-only-the-directories-you-need (2026-07-29 確認。`settings#worktree-settings` 側に散文の説明は無い)。公式に記述のない挙動はサンドボックス実証で確定済み (2026-07-15 / Claude Code 2.1.210、2026-07-29 / Claude Code 2.1.220。後者は対話セッションを別 pane で起動して計測)

### 反映機構

- `.worktreeinclude`: repo root に置く。`.gitignore` 構文。**gitignored かつパターン一致**のファイルのみ worktree **作成時**にコピーされる (追跡ファイルは対象外)。既存 worktree には効かない。`!` 否定パターンは公式記述なし — 使わない
- `worktree.sparsePaths`: git sparse-checkout (cone) で列挙**ディレクトリ**のみを checkout する。ファイルは列挙しない。path は repo root 相対。root 直下の**ファイル** (lock ファイル等) は常に checkout されるが、root 直下の**ディレクトリ**は列挙しない限り入らない — worktree 内で repo root の `.claude/` (settings / rules / skills / hook script) が要るなら `".claude"` を含める。session 内の全 worktree が同一 sparsePaths を共有する。scope をまたいで list は merge される (local 側は追加のみ)
- `worktree.symlinkDirectories`: worktree 内に、元 repo の同名ディレクトリを指す**絶対パス symlink** を作る (実証)。コピーではないので容量も初期化時間も消えるが、実体は 1 つなので branch 間で内容が共有される
- `sparsePaths` と `.worktreeinclude` の併用: sparse 対象外ディレクトリ配下の gitignored ファイルもコピーされ、そのディレクトリが worktree 内に作られる (実証)。両方に同じ領域を書かない
- 適用経路: `claude --worktree` と `EnterWorktree` の**両方**で上記 3 機構が適用される (実証)
- 設定の読み取り元: `sparsePaths` / `symlinkDirectories` は**起動ディレクトリ**の settings から worktree 作成**前**に読まれる。作成後の cwd は worktree root なので、worktree 内で効かせたい設定 (hooks / `permissions.deny` 等) は repo root の `.claude/settings.json` に置く
- sparse worktree があると git が共有 `.git/config` に `extensions.worktreeConfig = true` を書く。Claude Code は自分が付けた場合のみ最後の worktree 除去後に消す (2.1.207+)
- **symlink の罠**: symlink は `.gitignore` の `node_modules/` (末尾 `/`) にマッチせず untracked 扱いになる (実証)。結果、worktree は常に「未コミットの変更あり」と判定され、`git worktree remove` は `--force` 必須、セッション終了時も毎回 keep / remove を訊かれ、放置 worktree の自動 sweep はスキップされる。ignore パターンを末尾 `/` なし (`node_modules`) にして回避する (Claude Code 自身の remove は symlink があっても成功する)

### hook 経路

- 追加型で使える event と cwd (すべて実証済み):

  | worktree の生成経路 | 発火する event | hook プロセスの cwd | 既定で登録 |
  |---|---|---|---|
  | `claude --worktree` (新セッション) | `SessionStart` (`source: startup`) | 新 worktree | ✓ |
  | セッション中の `EnterWorktree` | `PostToolUse` (matcher `EnterWorktree`) | 切り替え後の worktree | ✓ |
  | subagent `isolation: worktree` | `SubagentStart` | その subagent の worktree | opt-in (毎 spawn で初期化が走る) |

- `CwdChanged` は `EnterWorktree` では**発火しない** (実証)。worktree 切り替えの検知に使わない
- `SessionStart` は入力欄の表示は待たせないが、**最初のターンの処理は hook 完了まで進まない** (30 秒 hook で 2 回実測。hook 終了まで応答トークンが出ず、終了直後にターンが走る)。初期化がモデルの初手に間に合う。ただし hook の `timeout` (既定 60 秒) を超える初期化には `timeout` の明示が必要
- `SessionStart` は resume / clear / compact でも発火する。初期化 script は冪等ガード必須 (本 skill の template は `.git` 側 marker で実装済み)
- **`$CLAUDE_PROJECT_DIR` は event で指す先が違う** (実証): `SessionStart` は**セッションの project root** (= `--worktree` 起動ならその worktree)、`PostToolUse:EnterWorktree` は**起動ディレクトリのまま** (worktree に切り替わっても変わらない)。一方 hook プロセスの cwd は 3 経路とも対象 worktree。script の path は両方の root に存在する場所 (= `sparsePaths` に含めた `.claude/` 配下) を選ぶ。片方にしか無い場所に置くと、その経路だけ `/bin/sh: ...: No such file` で失敗する (UI に非ブロッキングエラーが 1 行出るだけ。実証)
- `SubagentStart` の worktree は spawn ごとに新規作成されるため、marker 方式の冪等ガードは効かない (毎 spawn で初期化が走る)。既定の登録には含めない — 下の手順 5 を参照
- project scope の hook は **workspace trust 承認後にのみ動く** (`--permission-mode bypassPermissions` は例外)。未承認ディレクトリでは `claude --worktree` 自体が "Workspace trust not yet accepted" で失敗する。headless (`-p`) では `SessionStart` は発火しない (`PostToolUse` は発火する)
- `WorktreeCreate` hook (置き換え型): worktree 作成時に発火 (`EnterWorktree` 経由でも発火する。実証済み)。matcher 非対応。stdin JSON の実フィールドは `session_id` / `transcript_path` / `cwd` / `hook_event_name` / `name` (`EnterWorktree` 経由では `prompt_id` が加わる)。stdout に worktree path を返さないと作成自体が失敗する (フォールバック無し)。この経路では `.worktreeinclude` / `sparsePaths` / `symlinkDirectories` が 3 つとも適用されない (実証)
- 初期セットアップの自動実行は公式サポートなし (「手動で初期化せよ」のみ)。本 skill の hook 連鎖はこのギャップを埋める
- 上記以外の仕様詳細は公式 doc + 実証で裏取りしてから使う。未裏取りの詳細に依存する書き方をしない

## 手順

作業前提: 成果物はすべて対象 repo への差分になる。対象 repo の規約 (worktree / branch 運用) に従って作業場所を確保してから始める。

### 1. repo 分析

```bash
git status --porcelain --ignored | grep '^!!'   # gitignored 候補の列挙
du -sh node_modules vendor .venv 2>/dev/null    # symlink 候補 (大容量な再生成可能ディレクトリ)
git ls-files | cut -d/ -f1 | uniq -c | sort -rn | head   # sparse 候補 (トップレベルの規模)
ls package-lock.json pnpm-lock.yaml yarn.lock uv.lock poetry.lock Gemfile.lock go.sum Cargo.lock 2>/dev/null
```

lock ファイルから初期化コマンドを決める (例: `package-lock.json` → `npm ci` / `pnpm-lock.yaml` → `pnpm install --frozen-lockfile` / `uv.lock` → `uv sync` / `Gemfile.lock` → `bundle install`)。複数あれば全部。判断に迷う repo 固有手順 (DB migrate 等) は README / CONTRIBUTING を確認し、worktree 起動に必須なものだけ入れる。

### 2. `worktree` 設定 (sparsePaths / symlinkDirectories)

不要なら設置しない (小さい repo に sparse は無意味なオーバーヘッド)。設置する場合は下表で判定する。

| 設定 | 入れる条件 | 入れない条件 |
|---|---|---|
| `sparsePaths` | 全部 checkout すると遅い / 重い規模で、作業対象のディレクトリが事前に絞れる (monorepo の一部パッケージ等) | 単一パッケージ repo。作業対象がタスクごとに変わり列挙が破綻する |
| `symlinkDirectories` | 再生成が重く (install に分単位)、branch 間で内容が食い違わない依存ディレクトリ | lockfile が branch ごとに変わる / 実体共有だと壊れる (片方の install が他方を壊す) |

`.claude/settings.json` (repo root。チーム共有なので commit する):

```json
{
  "worktree": {
    "sparsePaths": [".claude", "packages/api", "packages/shared"],
    "symlinkDirectories": ["node_modules"]
  }
}
```

`symlinkDirectories` を設定したら、対象ディレクトリの `.gitignore` パターンを末尾 `/` なし (`node_modules`) に直す (検証済み仕様の「symlink の罠」)。

### 3. `.worktreeinclude` 生成

仕分け基準 (下表) で候補を選別し、repo root に glob + 選定理由コメントで書く。

| 判定 | 対象 | 例 |
|---|---|---|
| 含める | 再生成できないローカル実行設定 | `.env` / `.env.*`、`.claude/settings.local.json`、ローカル証明書 `*.pem`、`config/local.*` |
| 除外 | コマンドで再生成可能 | `node_modules/`、`dist/` 等のビルド生成物、各種 cache (→ 初期化コマンド、または `symlinkDirectories` 側に回す) |
| 除外 (罠) | worktree 実体・セッション成果物 | `.claude/worktrees/` (再帰コピー)、agent 成果物ディレクトリ (`.ai/` `.superpowers/` `.reports/` 等) |

迷ったら「これが無いと新 worktree で起動・実行が失敗するか」で判定する。machine-local 設定を含めてよいのは worktree が同一ホスト内コピーのため。

### 4. 初期化 script のインスタンス化 (初期化コマンドがある場合のみ)

```bash
mkdir -p .claude/scripts
cp "${CLAUDE_SKILL_DIR}/scripts/worktree-post-setup.template.sh" .claude/scripts/worktree-setup.sh
chmod +x .claude/scripts/worktree-setup.sh
```

`:  # SETUP_COMMANDS` 行を手順 1 で決めたコマンドに Edit で置換する。template が実行ブロックごと stderr に閉じ込めるので、個々のコマンドに `>&2` を付ける必要はない。

置き場所を `.claude/scripts/` にするのは `sparsePaths` 併用時に script ごと checkout 対象から外れないため (`.claude` は sparse リストに入れる前提)。`sparsePaths` を使わない repo なら `scripts/` でもよいが、揃えておくと後から sparse を入れても壊れない。

### 5. hook 登録

対象 repo の `.claude/settings.json` を **必ず Read してから** 以下をマージする (手順 2 の `worktree` キーや既存の hooks / permissions を消さない。不正 JSON なら中断してユーザーに報告する — 修復上書きしない)。

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/scripts/worktree-setup.sh", "timeout": 600 }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "EnterWorktree",
        "hooks": [
          { "type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/scripts/worktree-setup.sh", "timeout": 600 }
        ]
      }
    ]
  }
}
```

`timeout` は初期化の実測より長くする (既定 60 秒。`npm ci` 等は超えやすい)。

`isolation: worktree` の subagent にも初期化を届けたい場合だけ、同じ command を `SubagentStart` にも足す。**subagent の worktree は spawn ごとに新規なので marker が効かず、fan-out するたびに初期化コマンドが丸ごと走る**。その代わりに次のどちらかを先に検討する。

- 重い依存が `symlinkDirectories` で共有できるなら、subagent 側は初期化不要になる (これが `node_modules` 系の本命)
- 初期化なしでは動かない subagent だけ、その agent 定義側で必要コマンドを実行させる

### 6. E2E 検証

```bash
# 1. 列挙パターンが実在の ignored ファイルにマッチするか
git status --porcelain --ignored | grep '^!!'
# 2. 成果物が gitignore されていないか (出力が出たら NG)
git check-ignore -v .worktreeinclude .claude/scripts/worktree-setup.sh
# 3. script 単体: main checkout では何もしないこと (出力が出たら NG)
./.claude/scripts/worktree-setup.sh
```

続けて実際に worktree を 1 個作り、中身を確認する (`claude -p "ok とだけ出力して" --worktree probe`。project hook は workspace trust 承認後にしか動かないため、hook の発火確認は対話セッションで行う)。

```bash
WT=.claude/worktrees/probe
git -C "$WT" sparse-checkout list   # sparsePaths 通りか (未設定なら "not sparse" が正)
ls -la "$WT"                         # symlink と .worktreeinclude 列挙ファイルの有無
git -C "$WT" status --porcelain      # symlink が "?? " で出るなら .gitignore の末尾 / を外す
ls "$(git -C "$WT" rev-parse --path-format=absolute --git-dir)/worktree-setup-done"  # 初期化 marker
```

確認後は `git worktree remove --force "$WT"` (`-p` 実行の worktree は lock が残ることがある。その場合は `git worktree unlock` を先に実行) で片付ける。

### 7. commit

該当する成果物をまとめて commit する。チーム共有資産である旨と、追加型 / 置き換え型のどちらを選んだかを commit message に書く。

## 例外: 置き換え型 (`WorktreeCreate`) を使う場合

原子性が要る、または git 以外の VCS のときだけ選ぶ。`${CLAUDE_SKILL_DIR}/scripts/worktree-create-hook.template.sh` を `<対象repo>/scripts/worktree-setup.sh` として複製し、次の 3 箇所を埋める。

- `:  # SETUP_COMMANDS` → 初期化コマンド (出力は `>&2` 必須。stdout は worktree path の応答チャネル)
- `SPARSE_PATHS=()` / `SYMLINK_DIRS=()` → 手順 2 で決めた値。この経路では settings の `worktree` キーが効かないため、settings からは消して script 側に一本化する
- `.worktreeinclude` のコピーは template が実装済み (repo root の `.worktreeinclude` をそのまま読む)

登録は `hooks.WorktreeCreate` に 1 本だけ。template は stdin JSON の解析に python3 を使うため、hook が走るホストに python3 が必要。作成される worktree は origin のデフォルトブランチ基点になる (remote 未設定時は HEAD 基点)。

## Common Mistakes

| 間違い | 現実 |
|---|---|
| 構文・挙動を記憶からの推定で書く | 根拠は上記の検証済み仕様。それ以外は裏取りしてから書く |
| 初期化のために既定で `WorktreeCreate` を選ぶ | 置き換え型は native 3 機構を巻き添えで無効化する。既定は worktree 内で走る追加型 |
| 初期化 script に冪等ガードを入れない | `SessionStart` は resume / clear / compact でも発火する。marker で 1 回に抑える |
| marker を worktree の working tree に置く | untracked が増え、自動 cleanup / sweep 判定を狂わせる。marker は `.git` 側に置く |
| hook script を `sparsePaths` 外に置く | `SessionStart` 経路の `$CLAUDE_PROJECT_DIR` は worktree を指すため、worktree 内に無いと `/bin/sh: No such file` で失敗する (`EnterWorktree` 経路だけ成功して見えるのが厄介)。`.claude/scripts/` に置く |
| `SubagentStart` を既定で登録する | subagent の worktree は毎回新規で marker が効かない。fan-out ごとに初期化が丸ごと走る。まず `symlinkDirectories` で不要にできないか見る |
| `sparsePaths` に `.claude` を入れ忘れる | root 直下ディレクトリは列挙必須。worktree 内で repo root の settings / rules / skills / hook script が消える |
| `sparsePaths` に個別ファイルを列挙する | cone モードはディレクトリ単位。root 直下のファイルは列挙不要で常に入る |
| `symlinkDirectories` の対象を `node_modules/` (末尾 /) で ignore する | symlink にマッチせず untracked 化。remove に `--force` が要り、自動 sweep も止まる |
| branch ごとに lockfile が変わる依存を symlink する | 実体は 1 つ。別 worktree の install が現 worktree を壊す。コピーか再 install を選ぶ |
| `.worktreeinclude` が既存 worktree に効くと期待する | 作成時のみ有効。既存分は手動対応 |
| git 追跡ファイルを `.worktreeinclude` に列挙する | 追跡ファイルは元々 worktree にある。列挙はノイズ |
| hook の `timeout` を既定のままにする | 既定 60 秒。`npm ci` 等は超えて初期化が途中で切られる |
| 既存 `.claude/settings.json` を上書きする | 必ず Read してマージ。不正 JSON は中断して報告 |
| 検証せずに完了報告する | 手順 6 を実施してから報告する |
