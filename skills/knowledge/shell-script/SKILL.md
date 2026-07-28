---
name: shell-script
user-invocable: false
description: |-
  bash で .sh single-file script を新規作成・編集する場面に参照する。決定的で単純 (テスト不要・標準コマンドで完結・100 行以内) なケースが対象。テスト・PyPI 依存・構造化レスポンス処理が要るときは python-single-file-script を使う。
  Use when「shell script」「bash script」「.sh ファイル作成」「決定的処理を shell 化」.
---

# shell-script 知識ベース

## 起動判定 (shell か python か)

下記の表で 1 つでも該当したら [python-single-file-script](../python-single-file-script/SKILL.md) へ。1 つも該当しなければ shell で書く。

| 兆候 | 例 |
|---|---|
| JSON/YAML を 2 段以上ネスト読み書き | レスポンス再構築、複雑な設定ファイル変換 |
| HTTP の構造化レスポンス解析 | curl + jq では body エラー時の fallback が書きにくい |
| if/else 3 階層以上 or for 内 if | shell のフロー制御は深くなると読めない |
| テストが要る (pytest で fixture / mock を書きたくなる) | 言語選択の核心基準 |
| PyPI 依存が要る | requests / pyyaml / beautifulsoup |
| 浮動小数演算・正確な日付計算 | bash の bc / date は罠が多い |
| 100 行を超える見込み | shell の possibility space が指数的に拡大 |
| bash 4+ の associative array や mapfile が要る | macOS デフォルト bash 3.2 で動かない |

## 構造テンプレート

```bash
#!/usr/bin/env bash
set -euo pipefail

# <1 行: この script が何を決定的にやるか>

INPUT="${1:?usage: $0 <input>}"

grep -E '<pattern>' "$INPUT" \
  | awk '<transform>' \
  | sort -u
```

## 必須規約

| 項目 | 規約 | 理由 |
|---|---|---|
| shebang | `#!/usr/bin/env bash` | PATH 解決で portable |
| 2 行目 | `set -euo pipefail` | エラー即停止 / 未定義変数禁止 / pipe 内エラー検出 |
| 実行権限 | `chmod +x` | shebang 経由の直接実行 |
| 引数チェック | `"${1:?usage: $0 <input>}"` | `set -u` 下で usage 自動表示付き必須化 |
| 配置 | global: `~/.dotfiles/dot_claude/scripts/<name>.sh` / project-local: `<repo>/scripts/<name>.sh` | reflect の allowlist 登録規約 |
| ファイル末尾 | 最終行に改行 | POSIX 標準 |

## 検証

| コマンド | 用途 |
|---|---|
| `bash -n <script>.sh` | 構文チェック (実行せず) |
| `shellcheck <script>.sh` | static lint。`brew install shellcheck` |

bats / shunit2 など test framework を入れたくなったら [python-single-file-script](../python-single-file-script/SKILL.md) へ escalate (本 skill の対象外)。

## Gotchas

| 問題 | 原因 | 対処 |
|---|---|---|
| `set -e` が pipe 内で効かない | `set -e` は最後のコマンドの exit のみ判定 | `set -o pipefail` 併用 (テンプレ既に含む) |
| `$1` 未定義で空文字扱い | `set -u` で error 化 | `"${1:?usage: ...}"` で usage 付き必須化 |
| macOS bash が 3.2 で古い | Apple が GPLv3 を避けて更新停止 | bash 4+ 機能を使うなら python へ escalate |
| 中間 tempfile での状態管理 / 複雑な trap を書きたくなる | shell は状態を持つと一気に読めなくなる | 状態管理が要るなら python へ escalate |

## 関連

- [python-single-file-script](../python-single-file-script/SKILL.md): escalate 先 (テスト・依存・複雑さが必要なケース)
- 配置と allowlist 登録は reflect skill が担う (本 skill は書き方知識のみ)
