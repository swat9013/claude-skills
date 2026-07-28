# PEP 723 & uv リファレンス

## 目次

1. [PEP 723 メタデータブロック構文](#pep-723-メタデータブロック構文)
2. [uv コマンドリファレンス](#uv-コマンドリファレンス)
3. [exclude-newer による再現性確保](#exclude-newer-による再現性確保)
4. [テスト実行コマンド](#テスト実行コマンド)

---

## PEP 723 メタデータブロック構文

```python
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests<3",
#     "rich",
# ]
# ///
```

| 要素 | 説明 |
|------|------|
| `# /// script` | メタデータブロック開始 |
| `# ///` | メタデータブロック終了 |
| `requires-python` | 必要な Python バージョン（PEP 440 形式） |
| `dependencies` | 依存パッケージリスト（PEP 508 形式） |
| `[tool.*]` | ツール固有設定（オプション、例: `[tool.uv]`） |

---

## uv コマンドリファレンス

### 依存関係の追加

```bash
# メタデータブロックを自動生成・更新
uv add --script script.py 'requests<3' 'rich'
```

既存ブロックがあれば更新、なければ新規生成する。

### 実行

```bash
# 依存関係を自動インストールして実行
uv run script.py

# 引数付き
uv run script.py --verbose target
```

### 直接実行（shebang）

```bash
chmod +x script.py
./script.py   # shebang: #!/usr/bin/env -S uv run --script
```

### ロックファイル生成

```bash
# 再現性確保のためロックファイルを生成
uv lock --script script.py
# → script.py.lock が生成される
```

---

## exclude-newer による再現性確保

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
#
# [tool.uv]
# exclude-newer = "2025-01-01T00:00:00Z"
# ///
```

指定日時以降にリリースされたパッケージを除外する。CI/CD やチーム間で同一バージョンを再現する場合に有用。

---

## テスト実行コマンド

```bash
# 方法1: --with フラグで pytest を追加（推奨）
uv run --with pytest pytest script.py

# 方法2: dependencies に pytest を含める場合
uv run pytest script.py
```

`--with` フラグが必要な理由: pytest は PEP 723 メタデータを自動読み込みしない。
`dependencies` に `pytest` を含めれば `--with` は不要だが、本番依存と混在する。

### dependencies に含める場合のパターン

```python
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
#     "pytest",
# ]
# ///
```
