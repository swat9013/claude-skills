---
name: worktree-setup
disable-model-invocation: true
description: >-
  対象リポジトリに Claude Code の worktree 並列セッション環境をセットアップする。
  .worktreeinclude (gitignored ファイルのコピー設定) と WorktreeCreate hook
  (初期セットアップスクリプト自動実行) を repo に合わせて整備する。
  Use when「worktree セットアップ」「worktree 環境構築」「.worktreeinclude」
  「worktree に .env がコピーされない」「worktree 作成時に npm install / uv sync を自動実行したい」.
---

# worktree 環境セットアップ

対象リポジトリで 1 回実行し、worktree 並列セッション (`EnterWorktree` / `claude --worktree` / Agent `isolation: "worktree"`) 用の環境を整備する。成果物は対象 repo 内の 3 点。チーム共有資産としてまとめて commit する。

| 成果物 | 役割 |
|---|---|
| `.worktreeinclude` | gitignored なローカル設定の worktree 作成時コピー |
| `<対象repo>/scripts/worktree-setup.sh` | worktree 作成 + コピー + repo 固有の初期化 (WorktreeCreate hook 本体) |
| `.claude/settings.json` | WorktreeCreate hook の登録 |

初期化スクリプトが不要な repo (依存 install 不要) では `.worktreeinclude` のみで完了してよい。hook 側 (成果物 2, 3) は初期化コマンドがある場合だけ設置する。

## 検証済み仕様 (公式 + 実証 — 記憶で推定しない)

出典: https://code.claude.com/docs/en/worktrees / https://code.claude.com/docs/en/hooks (2026-07-15 確認)。公式に記述のない挙動はサンドボックス実証で確定済み (2026-07-15、Claude Code 2.1.210)

- `.worktreeinclude`: repo root に置く。`.gitignore` 構文。**gitignored かつパターン一致**のファイルのみ worktree **作成時**にコピーされる (追跡ファイルは対象外)。既存 worktree には効かない。`!` 否定パターンは公式記述なし — 使わない
- `WorktreeCreate` hook: worktree 作成時に発火 (git repo でも発火する。実証済み)。matcher 非対応。stdin JSON の実フィールドは `session_id` / `transcript_path` / `cwd` / `hook_event_name` / `name` (worktree 名。`EnterWorktree` 経由では `prompt_id` が加わる)
- hook を登録すると worktree 作成は hook の責務になる**置き換え型**: stdout に worktree path を返さないと作成自体がエラーで失敗する (デフォルト作成へのフォールバックは無い)。hook 経路では `.worktreeinclude` のネイティブコピーも走らないため、コピーは hook script 側で行う (本 skill の template が実装済み)
- `EnterWorktree` ツール経由でも同 hook が発火する (実証済み)
- 初期セットアップの自動実行は公式サポートなし (「手動で初期化せよ」のみ)。本 skill の hook 連鎖はこのギャップを埋める
- 上記以外の仕様詳細は公式 doc + 実証で裏取りしてから使う。未裏取りの詳細に依存する書き方をしない

## 手順

作業前提: 成果物はすべて対象 repo への差分になる。対象 repo の規約 (worktree / branch 運用) に従って作業場所を確保してから始める。

### 1. repo 分析

```bash
git status --porcelain --ignored | grep '^!!'   # gitignored 候補の列挙
ls package-lock.json pnpm-lock.yaml yarn.lock uv.lock poetry.lock Gemfile.lock go.sum Cargo.lock 2>/dev/null
```

lock ファイルから初期化コマンドを決める (例: `package-lock.json` → `npm ci` / `pnpm-lock.yaml` → `pnpm install --frozen-lockfile` / `uv.lock` → `uv sync` / `Gemfile.lock` → `bundle install`)。複数あれば全部。判断に迷う repo 固有手順 (DB migrate 等) は README / CONTRIBUTING を確認し、worktree 起動に必須なものだけ入れる。

### 2. `.worktreeinclude` 生成

仕分け基準 (下表) で候補を選別し、repo root に glob + 選定理由コメントで書く。

| 判定 | 対象 | 例 |
|---|---|---|
| 含める | 再生成できないローカル実行設定 | `.env` / `.env.*`、`.claude/settings.local.json`、ローカル証明書 `*.pem`、`config/local.*` |
| 除外 | コマンドで再生成可能 | `node_modules/`、`dist/` 等のビルド生成物、各種 cache (→ 初期化コマンド側で再生成) |
| 除外 (罠) | worktree 実体・セッション成果物 | `.claude/worktrees/` (再帰コピー)、agent 成果物ディレクトリ (`.ai/` `.superpowers/` `.reports/` 等) |

迷ったら「これが無いと新 worktree で起動・実行が失敗するか」で判定する。machine-local 設定を含めてよいのは worktree が同一ホスト内コピーのため。

### 3. setup script のインスタンス化

```bash
mkdir -p scripts
cp "${CLAUDE_SKILL_DIR}/scripts/worktree-create-hook.template.sh" scripts/worktree-setup.sh
chmod +x scripts/worktree-setup.sh
```

`<対象repo>/scripts/worktree-setup.sh` 内の `:  # SETUP_COMMANDS` 行を手順 1 で決めたコマンドに Edit で置換する。各コマンドの出力は stdout を汚さないよう `>&2` を付ける (例: `npm ci >&2`)。

前提と挙動の注意: template は stdin JSON の解析に python3 を使うため、hook が走るホストに python3 が必要。また作成される worktree は origin のデフォルトブランチ基点になる (remote 未設定時は HEAD 基点)。現在の branch の未 merge 変更は新 worktree に含まれない。

### 4. hook 登録

対象 repo の `.claude/settings.json` を **必ず Read してから** 以下をマージする (既存の hooks / permissions を消さない。不正 JSON なら中断してユーザーに報告する — 修復上書きしない):

```json
{
  "hooks": {
    "WorktreeCreate": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/scripts/worktree-setup.sh",
            "timeout": 600
          }
        ]
      }
    ]
  }
}
```

### 5. E2E 検証

```bash
# 1. 列挙パターンが実在の ignored ファイルにマッチするか
git status --porcelain --ignored | grep '^!!'
# 2. 成果物が gitignore されていないか (出力が出たら NG)
git check-ignore -v .worktreeinclude scripts/worktree-setup.sh
# 3. script 単体の動作確認 (worktree 作成 + コピー + 初期化まで実走。手順 3 で chmod +x 済みなので直接パス起動する)
printf '{"session_id":"e2e","transcript_path":"/dev/null","cwd":"%s","hook_event_name":"WorktreeCreate","name":"e2e-probe"}' "$(pwd)" | ./scripts/worktree-setup.sh
```

3 の出力 path 直下で、`.worktreeinclude` 列挙ファイルの存在と初期化結果 (例: `node_modules/`) を確認したら、`git worktree remove --force <path>` で worktree を先に消してから `git branch -d worktree-e2e-probe` (worktree 除去後は base ref と一致するため safe delete で消せる) で片付ける。hook 経由の発火確認まで行う場合は、新しい Claude Code セッション (例: `claude -p "ok とだけ出力して" --worktree hook-probe`) で worktree を 1 個作成し、確認後に `git worktree remove` で片付ける。

### 6. commit

3 成果物 (該当分) をまとめて commit する。チーム共有資産である旨を commit message に書く。

## Common Mistakes

| 間違い | 現実 |
|---|---|
| 構文・挙動を記憶からの推定で書く | 根拠は上記の検証済み仕様。それ以外は裏取りしてから書く |
| `.worktreeinclude` が既存 worktree に効くと期待する | 作成時のみ有効。既存分は手動対応 |
| git 追跡ファイルを `.worktreeinclude` に列挙する | 追跡ファイルは元々 worktree にある。列挙はノイズ |
| `node_modules/` 等をコピー対象に含める | 重く陳腐化する。初期化コマンドで再生成する |
| SETUP_COMMANDS の出力を stdout に流す | stdout は hook の path 応答チャネル。汚すと worktree 解決が壊れる。`>&2` 必須 |
| 既存 `.claude/settings.json` を上書きする | 必ず Read してマージ。不正 JSON は中断して報告 |
| 検証せずに完了報告する | 手順 5 の 1-3 を実施してから報告する |
