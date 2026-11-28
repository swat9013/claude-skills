# 中規模Pythonツール開発環境のベストプラクティス（2026年版）

**対象**: 写真管理ツールのようなCLI + GUI を含む中規模Pythonアプリケーション
**最終更新**: 2026-06-08

---

## 1. 依存関係管理

### 推奨: uv 単独で完結

**結論**: 2026年時点で **uv だけで開発環境を完結できます**。Poetry は不要です。

- **バージョン**: 0.11.19（2026-06-03）
- **開発元**: Astral（Ruffの開発元、2026-03 に OpenAI が買収）
- **特徴**:
  - pip の 10-100倍高速（JupyterLab: 2.6秒 vs 21.4秒）
  - 標準 `pyproject.toml`（PEP 621）完全対応
  - Python バージョン管理内蔵（pyenv 不要。同系列の Rye は 2026-02 廃止され uv へ統合）
  - pip/pip-tools/pipx/poetry/pyenv/virtualenv を単一ツールで置換
  - プロダクション対応の完全な依存関係エンジン
  - 月間ダウンロード約 7,500 万回で Poetry（約 6,600 万）を逆転し、新規プロジェクトのデファクト・スタンダードに

### 基本ワークフロー

```bash
# インストール
curl -LsSf https://astral.sh/uv/install.sh | sh

# プロジェクト初期化
uv init photo-manager
cd photo-manager

# 依存関係追加
uv add fastapi pillow

# 開発依存関係追加
uv add --dev pytest ruff pyright

# 仮想環境作成・依存関係同期
uv sync

# スクリプト実行
uv run python src/photo_manager/cli.py

# テスト実行
uv run pytest

# 開発サーバー起動
uv run fastapi dev src/photo_manager/main.py
```

### 依存関係の分離

**開発専用依存は PEP 735 `[dependency-groups]` に分離する（2026年標準）**。`[project.optional-dependencies]` は配布物に含まれる extra（ユーザーが `pip install pkg[extra]` で入れる）用途であり、pytest/ruff のような**配布されるべきでない開発依存**は `[dependency-groups]` が正しい置き場所。

```toml
[project]
dependencies = [
    # プロダクション依存関係（配布物に含まれる）
    "fastapi>=0.136.0",
    "pillow>=11.0.0",
]

[dependency-groups]
# 開発専用依存（配布されない・PEP 735）
dev = [
    "pytest>=9.0",
    "ruff>=0.15.0",
    "pyright",
]
test = ["pytest-cov"]
```

```bash
# 同期（プロジェクト + dev グループ）
uv sync

# dev を除外して同期
uv sync --no-group dev

# 特定グループのみ
uv sync --only-group test

# 全グループ
uv sync --all-groups
```

> `--dev` / `--no-dev` / `--only-dev` は `--group dev` 系の別名として継続動作する。

### Poetry が必要なケース（オプション）

以下の場合のみ Poetry を検討：

- **PyPI公開**: 公開機能が成熟（ただし uv でも可能）
- **既存プロジェクト**: Poetry で問題なければ移行不要
- **複雑な依存グラフ**: 極めて複雑な場合（稀）

**シンプルさ優先の原則**: 新規プロジェクトでは uv のみを推奨。

**出典**: [uv Documentation](https://docs.astral.sh/uv/), [Managing Python Projects With uv](https://realpython.com/python-uv/), [uv vs Poetry 2026](https://medium.com/@hitorunajp/poetry-vs-uv-which-python-package-manager-should-you-use-in-2025-4212cb5e0a14)

---

## 2. プロジェクト構造

### src layout（推奨）

中規模以上のアプリケーション、本番配布するパッケージには **src layout** が標準です。

```
photo-manager/
├── src/
│   └── photo_manager/
│       ├── __init__.py
│       ├── main.py         # FastAPI app（Web UI）
│       ├── cli.py          # CLI エントリーポイント
│       ├── routers/        # API エンドポイント
│       │   └── photos.py
│       ├── templates/      # Jinja2 テンプレート
│       │   └── gallery.html
│       ├── static/         # 静的ファイル
│       │   └── photos/
│       ├── core/           # コアロジック
│       │   ├── __init__.py
│       │   └── storage.py
│       └── utils/
│           ├── __init__.py
│           └── imaging.py
├── tests/
│   ├── __init__.py
│   ├── test_api.py         # API テスト
│   ├── test_cli.py
│   ├── test_e2e.py         # Playwright E2E
│   └── test_storage.py
├── docs/
├── pyproject.toml
├── README.md
└── LICENSE
```

### 採用理由

- **インポートエラー防止**: テストが「開発中のローカルコード」ではなく「インストールされたパッケージ」をテスト
- **明確な分離**: ソースコードをプロジェクトルートから分離
- **ビルド品質保証**: `pip install -e .` による明示的なインストールを強制

**出典**: [src layout vs flat layout - Python Packaging User Guide](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/), [Python Project Structure](https://medium.com/@adityaghadge99/python-project-structure-why-the-src-layout-beats-flat-folders-and-how-to-use-my-free-template-808844d16f35)

---

## 3. pyproject.toml 構成

### PEP 621 準拠（標準）

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "photo-manager"
version = "0.1.0"
description = "Personal photo management tool"
readme = "README.md"
license = {file = "LICENSE"}
authors = [
    {name = "Your Name", email = "you@example.com"}
]
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.136.0",
    "uvicorn[standard]>=0.35.0",
    "pillow>=11.0.0",
    "jinja2>=3.1.0",
    "typer>=0.26.0",
]

# 開発専用依存は PEP 735 dependency-groups（配布されない）
[dependency-groups]
dev = [
    "pytest>=9.0",
    "pytest-cov",
    "pytest-asyncio",
    "playwright>=1.60.0",
    "pytest-playwright",
    "ruff",
    "pyright",
    "pre-commit",
]

[project.scripts]
photo-manager = "photo_manager.cli:main"
photo-manager-web = "photo_manager.main:app"

[tool.ruff]
line-length = 88
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W"]

[tool.pyright]
venvPath = "."
venv = ".venv"
```

**出典**: [PEP 621](https://peps.python.org/pep-0621/), [pyproject.toml specification](https://packaging.python.org/en/latest/specifications/pyproject-toml/)

---

## 4. UI フレームワーク

### CLI: Typer（推奨）

- **バージョン**: 0.26.7（2026-06-03）
- **特徴**:
  - 型ヒントから自動でドキュメント生成
  - 最小のボイラープレート
  - Click の強力な機能をシンプルなAPIで提供
- **注記（0.26.0 破壊的変更）**: Typer は Click を第三者依存ではなく内部に vendoring（同梱）するようになった。依存競合は解消される一方、Click 固有機能（Click アプリの抽出・Click プラグイン）は非サポート化。通常の Typer API 利用には影響なし。

```python
# src/photo_manager/cli.py
import typer
from typing import Literal
from pathlib import Path

app = typer.Typer()

@app.command()
def import_photos(
    source: Path = typer.Argument(..., help="Source directory"),
    mode: Literal["copy", "move"] = "copy",
    verbose: bool = False,
):
    """Import photos from source directory."""
    if verbose:
        typer.echo(f"Importing from {source} (mode: {mode})")
    # 処理...

if __name__ == "__main__":
    app()
```

**出典**: [Typer](https://typer.tiangolo.com/), [Comparing Python Command Line Interface Tools](https://codecut.ai/comparing-python-command-line-interface-tools-argparse-click-and-typer/)

---

### UI: Web または ネイティブGUI

**「手軽に作れてテストもしやすい」を優先する場合、Web UI を推奨します。**

## A. Web UI（推奨）

### FastAPI + HTMX + Alpine.js

**理由**:
- ビルドプロセス不要（Webpack/Vite 不要）
- TestClient による単体テスト容易性
- Playwright による E2E テスト（2026年標準）
- ホットリロード開発体験（`fastapi dev`）
- ~29KB の軽量フロントエンド

### スタック構成

| 層 | 技術 | 役割 |
|----|------|------|
| バックエンド | FastAPI | API、非同期処理、型安全性 |
| フロントエンド | HTMX (~14KB) | サーバー通信、コンテンツ更新 |
| UI インタラクション | Alpine.js (~15KB) | モーダル、ドロップダウン等 |
| テンプレート | Jinja2 | サーバーサイドレンダリング |

> **HTMX 4.0** は XMLHttpRequest → Fetch API 移行（ストリーミング応答対応）を含む大規模リファクタリング。調査時点 (2026-06) ではアルファ版だったため本番は **2.0.9 安定版** 据え置きを推奨した。この判断は 4.0 の現況次第で変わるので、採用前にリリース状況を確認する。

### プロジェクト構造

```
photo-manager/
├── src/
│   └── photo_manager/
│       ├── main.py          # FastAPI app
│       ├── routers/
│       │   └── photos.py
│       ├── templates/
│       │   └── gallery.html # HTMX + Alpine
│       └── static/
│           └── photos/
├── tests/
│   ├── test_api.py          # pytest + TestClient
│   └── test_e2e.py          # Playwright
└── pyproject.toml
```

### 実装例

```python
# src/photo_manager/main.py
from fastapi import FastAPI, UploadFile, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

app = FastAPI()
templates = Jinja2Templates(directory="src/photo_manager/templates")
app.mount("/static", StaticFiles(directory="src/photo_manager/static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def gallery(request: Request):
    return templates.TemplateResponse("gallery.html", {"request": request})

@app.post("/api/photos")
async def upload_photo(file: UploadFile):
    # 非同期画像処理
    return {"id": "...", "url": f"/static/photos/{file.filename}"}

@app.get("/api/photos")
async def list_photos():
    # 写真一覧取得
    return [{"id": "1", "name": "photo.jpg", "url": "/static/photos/photo.jpg"}]
```

```html
<!-- src/photo_manager/templates/gallery.html -->
<!DOCTYPE html>
<html>
<head>
    <title>Photo Manager</title>
    <script src="https://unpkg.com/htmx.org@2.0.9"></script>
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
</head>
<body>
    <div x-data="{ filter: '' }">
        <input x-model="filter" placeholder="Filter photos...">

        <form hx-post="/api/photos"
              hx-encoding="multipart/form-data"
              hx-target="#gallery"
              hx-swap="afterbegin">
            <input type="file" name="file" accept="image/*">
            <button type="submit">Upload</button>
        </form>

        <div id="gallery"
             hx-get="/api/photos"
             hx-trigger="load"
             hx-target="this">
            <!-- 写真一覧がここに動的挿入 -->
        </div>
    </div>
</body>
</html>
```

### テスト例

```python
# tests/test_api.py
from fastapi.testclient import TestClient
from photo_manager.main import app

client = TestClient(app)

def test_upload_photo():
    with open("test.jpg", "rb") as f:
        response = client.post("/api/photos", files={"file": f})
    assert response.status_code == 200
    assert "url" in response.json()

# tests/test_e2e.py (Playwright)
from playwright.sync_api import Page, expect

def test_gallery_page(page: Page):
    page.goto("http://localhost:8000")
    expect(page.locator("input[placeholder='Filter photos...']")).to_be_visible()

    # ファイルアップロード
    page.set_input_files("input[type='file']", "test.jpg")
    page.click("button:has-text('Upload')")

    # 画像が表示されることを確認
    expect(page.locator("#gallery img")).to_be_visible()
```

### 開発コマンド

```bash
# 依存関係追加
uv add fastapi pillow jinja2
uv add --dev pytest playwright pytest-playwright

# 開発サーバー起動（ホットリロード）
uv run fastapi dev src/photo_manager/main.py

# テスト実行
uv run pytest
```

**出典**: [FastAPI vs Django vs Flask 2026](https://blog.jetbrains.com/pycharm/2025/02/django-flask-fastapi/), [HTMX and Alpine.js](https://www.infoworld.com/article/3856520/htmx-and-alpine-js-how-to-combine-two-great-lean-front-ends.html), [Playwright 2026](https://www.deviqa.com/blog/guide-to-playwright-end-to-end-testing-in-2025/)

---

## B. ネイティブGUI（オプション）

Web UI で要件を満たせない場合のみ検討。

| フレームワーク | 用途 | バージョン |
|--------------|------|-----------|
| **Flet** | モダンUI、クロスプラットフォーム | 0.85.3 |
| **PySide6** | 成熟した機能、商用配布可（LGPL） | 6.11.1 |
| **Tkinter** | 依存ゼロ、最軽量（標準ライブラリ） | - |

**出典**: [Python GUI Frameworks 2026](https://www.pythonguis.com/faq/which-python-gui-library/), [Flet](https://flet.dev/), [PySide6](https://doc.qt.io/qtforpython-6/)

---

## 5. テスト環境

### テストフレームワーク: pytest

```bash
# インストール
uv add --dev pytest pytest-cov pytest-asyncio pytest-xdist
```

```python
# tests/test_storage.py
import pytest
from photo_manager.core.storage import PhotoStorage

def test_import_photo():
    storage = PhotoStorage("/tmp/photos")
    result = storage.import_photo("test.jpg")
    assert result.success
```

### 推奨プラグイン

- **pytest-cov**: カバレッジ測定
- **pytest-asyncio**: 非同期テスト
- **pytest-xdist**: 並列テスト実行

```bash
# カバレッジ付きテスト実行
pytest --cov=src/photo_manager --cov-report=html
```

**出典**: [pytest documentation](https://docs.pytest.org/), [Best Python Testing Tools 2026](https://medium.com/@inprogrammer/best-python-testing-tools-2026-updated-884dcb78b115)

---

## 6. コード品質ツール

### Ruff（リンター + フォーマッター）

- **特徴**: Rust 製、Black より 30倍以上高速
- **統合機能**: Flake8/Black/isort/pydocstyle/pyupgrade を置換

```bash
# インストール
uv add --dev ruff

# リント実行
ruff check .

# フォーマット実行
ruff format .
```

### Pyright（型チェッカー）

- **特徴**: Microsoft 製、mypy より 3-5倍高速
- **型安全性**: 型ヒントから自動で型エラー検出

```bash
# インストール
uv add --dev pyright

# 型チェック実行
pyright
```

**出典**: [Ruff](https://docs.astral.sh/ruff/), [pyright](https://github.com/microsoft/pyright)

---

## 7. pre-commit フック

### 最小構成

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.16
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-pyright
    rev: v1.1.410
    hooks:
      - id: pyright

  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.30.1
    hooks:
      - id: gitleaks
```

```bash
# インストール
uv add --dev pre-commit
pre-commit install
```

**出典**: [pre-commit](https://pre-commit.com/), [The Power of Pre-Commit for Python Developers](https://dev.to/techishdeep/maximize-your-python-efficiency-with-pre-commit-a-complete-but-concise-guide-39a5)

---

## 8. CI/CD（GitHub Actions）

### 推奨ワークフロー

```yaml
# .github/workflows/ci.yml
name: Python CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        python-version: ['3.10', '3.11', '3.12', '3.13']
        os: [ubuntu-latest, macos-latest, windows-latest]

    steps:
    - uses: actions/checkout@v4

    - name: Install uv
      uses: astral-sh/setup-uv@v4

    - name: Set up Python ${{ matrix.python-version }}
      run: uv python install ${{ matrix.python-version }}

    - name: Install dependencies
      run: uv sync --all-groups

    - name: Run Ruff
      run: |
        uv run ruff check .
        uv run ruff format --check .

    - name: Run type checker
      run: uv run pyright

    - name: Run tests with coverage
      run: uv run pytest --cov=src --cov-report=xml

    - name: Upload coverage
      uses: codecov/codecov-action@v4
      with:
        file: ./coverage.xml
```

**出典**: [GitHub Actions for Python](https://realpython.com/github-actions-python/), [5 GitHub Actions CI/CD Best Practices](https://www.netapp.com/learn/cvo-blg-5-github-actions-cicd-best-practices/)

---

## 9. パッケージング・配布

### ビルドバックエンド: Hatchling（推奨）

- **理由**: Python Packaging Authority（PyPA）公式推奨
- **特徴**: 最小限の設定、プラグインサポート

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

```bash
# ビルド
uv build

# 生成物
# dist/photo_manager-0.1.0-py3-none-any.whl
# dist/photo_manager-0.1.0.tar.gz
```

**出典**: [Writing your pyproject.toml](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/), [The State of Python Packaging in 2026](https://learn.repoforge.io/posts/the-state-of-python-packaging-in-2026/)

---

## 10. バイナリ配布（オプション）

### PyInstaller vs Nuitka

| 観点 | PyInstaller | Nuitka |
|------|-------------|--------|
| **アプローチ** | バンドリング | C コンパイル |
| **実行速度** | 通常の Python と同等 | 2-10倍高速 |
| **起動速度** | 遅い | 高速 |
| **ビルド速度** | 高速 | 遅い |
| **知的財産保護** | 弱い | 強い |
| **Python サポート** | 3.13 対応（v6.20.0） | 2.6, 2.7, 3.4-3.14（3.14 は実験的、v4.1.2） |

### 推奨

- **簡単なパッケージング優先**: PyInstaller
- **パフォーマンス・知財保護優先**: Nuitka

```bash
# PyInstaller
pip install pyinstaller
pyinstaller --onefile src/photo_manager/cli.py

# Nuitka
pip install nuitka
python -m nuitka --onefile src/photo_manager/cli.py
```

**出典**: [PyInstaller vs. Nuitka](https://sparxeng.com/blog/software/python-standalone-executable-generators-pyinstaller-nuitka-cx-freeze), [Nuitka](https://github.com/Nuitka/Nuitka)

---

## 11. ドキュメント生成

### MkDocs（推奨：チュートリアル中心）

```bash
# インストール
uv add --dev mkdocs mkdocs-material

# 初期化
mkdocs new .
mkdocs serve  # http://127.0.0.1:8000 でライブプレビュー
```

```yaml
# mkdocs.yml
site_name: Photo Manager Documentation
theme:
  name: material
nav:
  - Home: index.md
  - User Guide: user-guide.md
  - API Reference: api.md
```

### Sphinx（API リファレンス中心）

```bash
# インストール
uv add --dev sphinx sphinx-rtd-theme

# 初期化
sphinx-quickstart docs
```

**出典**: [MkDocs vs Sphinx](https://www.pythonsnacks.com/p/python-documentation-generator), [Build Your Python Project Documentation With MkDocs](https://realpython.com/python-project-documentation-with-mkdocs/)

---

## 12. 推奨構成まとめ（シンプルさ優先）

### 依存関係管理
- **uv 単独** で完結（Poetry 不要）

### UI
- **CLI**: Typer
- **Web UI（推奨）**: FastAPI + HTMX + Alpine.js
  - 手軽に作れる、テストしやすい、ビルド不要
- **ネイティブGUI（オプション）**: Flet / PySide6 / Tkinter

### 開発ツール
- **テスト**: pytest + pytest-cov（単体）、Playwright（E2E）
- **リント**: Ruff
- **型チェック**: Pyright
- **フック**: pre-commit

### ビルド
- **ビルドバックエンド**: Hatchling
- **バイナリ配布（オプション）**: PyInstaller（簡単）/ Nuitka（高速）

### ドキュメント（オプション）
- **チュートリアル**: MkDocs
- **API リファレンス**: Sphinx

### クイックスタート

```bash
# プロジェクト作成
uv init photo-manager
cd photo-manager

# 依存関係追加
uv add fastapi uvicorn pillow jinja2 typer
uv add --dev pytest playwright ruff pyright

# 開発サーバー起動
uv run fastapi dev src/photo_manager/main.py

# テスト実行
uv run pytest
```

---

## 参考資料

- [Python Packaging User Guide](https://packaging.python.org/)
- [uv Documentation](https://docs.astral.sh/uv/)
- [Poetry Documentation](https://python-poetry.org/)
- [Typer Documentation](https://typer.tiangolo.com/)
- [pytest Documentation](https://docs.pytest.org/)
- [Ruff Documentation](https://docs.astral.sh/ruff/)
- [pre-commit](https://pre-commit.com/)
