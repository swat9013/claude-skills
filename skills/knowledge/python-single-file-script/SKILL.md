---
name: python-single-file-script
user-invocable: false
description: |-
  PEP 723 インラインメタデータ + uv run で単一ファイル Python スクリプトを新規作成・編集する場面に参照する。テスト (pytest) / PyPI 依存 / 100 行超 / 浮動小数演算 / 正確な日付計算 のいずれかが要るケースが対象。標準コマンドで完結する簡素な処理なら shell-script を使う。
  Use when「PEP 723」「uv run」「Python スクリプト」「single-file script」「インラインメタデータ」「script 化」「pytest 必要な決定的処理」.
---

# python-single-file-script 知識ベース

## 起動判定 (python か shell か)

下記のいずれかに該当したら本 skill を使う。1 つも該当しなければ [shell-script](../shell-script/SKILL.md) を使う。

| この skill を使う | この skill を使わない (= shell-script) |
|---|---|
| pytest テストを書きたい複雑さ | テスト不要なほど単純 |
| PyPI パッケージが要る (requests, pyyaml, etc.) | 標準コマンド (grep/awk/jq) で完結 |
| 100 行超 / 浮動小数演算 / 正確な日付計算 | 数行の grep+pipe 変換 |

詳細な escalate 条件 (全 8 兆候) は [shell-script 側の起動判定表](../shell-script/SKILL.md) が source of truth。

単一ファイルに収まらない (パッケージ構造・複数モジュール) ならこの skill の対象外。独立 `pyproject.toml` project を立てる。

## 構造テンプレート

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests<3",   # バージョン制約必須 (無制約は再現性が壊れる)
# ]
# ///
"""<1 行で何を決定的にやるか書く。--help に表示される>"""

import argparse
import pathlib
import sys


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input")
    return p.parse_args(argv)


def main(args: argparse.Namespace) -> int:
    # 戻り値が exit code: 0=成功 / 1=一般エラー / 2=引数エラー (argparse 自動) / 130=SIGINT
    path = pathlib.Path(args.input)
    print(path.read_text())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)
```

## 必須規約

| 項目 | 規約 | 理由 |
|---|---|---|
| shebang | `#!/usr/bin/env -S uv run --script` | `-S` で複数引数を 1 文字列化 (Linux execve 制約回避) |
| 構造順序 | shebang → PEP 723 → docstring → import → 関数 → main → if __name__ | docstring を PEP 723 直後に置くと `--help` 自動表示 |
| 依存バージョン | 明示制約 (`"requests<3"`) | 無制約は uv run 時に最新解決され再現性破綻 |
| parse_args 分離 | `main()` から独立関数化 | sys.argv 注入なしで parse_args 自体をテストできる |
| 終了コード | 0=成功 / 1=一般 / 2=引数 (argparse 任せ) / 130=SIGINT | CLI 標準慣習 |
| テスト配置 | fixture 共有 / CI で個別 collect / cognitive load 大 → `test_<name>.py` 分離。それ以外は同一ファイル末尾に `def test_*` を並べる | 同一は完結性、分離は CI 統合と fixture 活用に有利 (行数閾値は heuristic、絶対ではない) |
| ファイル末尾 | 最終行に改行 | POSIX 標準 |

## 実行・テスト

```bash
# 実行 (依存を自動 install してから実行)
uv run script.py [args]

# 直接実行 (shebang 経由)
chmod +x script.py && ./script.py [args]

# テスト: pytest は PEP 723 を自動読み込みしないので --with 必須
uv run --with pytest pytest script.py

# 再現性が要るなら lock ファイル生成 (.lock を script と一緒に commit すること)
uv lock --script script.py    # → script.py.lock
```

niche option (`exclude-newer` 等) や uv add コマンド詳細は [references/pep723-and-uv.md](references/pep723-and-uv.md)。

## Gotchas

| 問題 | 原因 | 対処 |
|---|---|---|
| pytest がスクリプト内依存を認識しない | pytest が PEP 723 を自動読み込みしない | `uv run --with pytest pytest script.py` |
| shebang が Linux で失敗 | Linux execve が複数引数を 1 つにまとめる | `-S` フラグを必ず付ける |
| 再実行で依存解決が変わる | バージョン制約なし | `dependencies` に制約 or `exclude-newer` 設定 |
| IDE 補完が効かない | Pylance が PEP 723 未対応 (2026-05 時点) | pyproject.toml + venv を別途用意 |
| pytest を `dependencies` に常駐させる誘惑 | `--with` を書く手間を惜しむ | 本番依存に test 依存が混入する。`--with pytest` で都度注入 |

## 関連

- [shell-script](../shell-script/SKILL.md): escalate 元。境界条件は shell-script 側の表が source of truth
- [references/pep723-and-uv.md](references/pep723-and-uv.md): uv コマンド詳細・`exclude-newer`・lock 運用
- 配置先決定 (global: `~/.dotfiles/dot_claude/scripts/<name>.py` / local: `<repo>/scripts/<name>.py`) と allowlist 登録は本 skill の責務外 (本 skill は書き方知識のみ)

## メンテナンス

| 項目 | 値 |
|---|---|
| 確認コマンド | `uv --version 2>/dev/null \|\| echo "(未インストール)"` |
| 記録バージョン | uv 0.11.16 (確認日: 2026-05-28) |

実環境の uv バージョンが記録と乖離していたら WebFetch で uv リリースノートを取得し、本 skill のテンプレ・コマンドを更新する。
