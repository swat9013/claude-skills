#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""research-gate.py - 調査レポート (.ai/research/*.md) の静的検査（6項目）を実行し PASS/WARN/FAIL JSON を出力

Usage: research-gate.py <report_md_path>
"""

import json
import os
import re
import sys
from pathlib import Path


# ── ヘルパー ─────────────────────────────────────────────────────


def _strip_code_blocks(text: str) -> str:
    """コードフェンス（``` ... ```）内を除去して誤検出を防ぐ"""
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


# ── 検査ロジック ────────────────────────────────────────────────


def check_section_completeness(text: str) -> dict:
    """モード非依存の必須セクション（調査概要 / 参考資料 / 未解決事項）の存在確認。

    番号スケルトン（## 1., ## 3., ...）には依存しない。モードごとに本体セクション名が
    変わる（比較なら技術比較、更新追跡なら更新一覧など）ため、タイトルで意味的に検査する。
    """
    required = [
        ("調査概要", r"^## (調査概要|概要)"),
        ("参考資料", r"^## (参考資料|参考|出典)"),
        ("未解決事項", r"^## 未解決事項"),
    ]
    missing = [label for label, pattern in required if not re.search(pattern, text, re.MULTILINE)]

    if missing:
        return {"label": "セクション完全性", "status": "FAIL", "detail": f"欠落セクション: {', '.join(missing)}"}
    return {"label": "セクション完全性", "status": "PASS", "detail": "必須セクション（調査概要・参考資料・未解決事項）すべて存在"}


def check_citations(text: str) -> dict:
    """出典 URL（[名](http...) 形式の markdown リンク）が最低 1 件あるか。

    外部技術情報の調査エンジンなので、出典 URL ゼロは裏取り不足のサイン。
    内部ファイル参照のみのモードもありうるため FAIL ではなく WARN に留める（コードブロック内は除外）。
    """
    target = _strip_code_blocks(text)
    links = re.findall(r"\[[^\]]+\]\(https?://[^)\s]+\)", target)
    count = len(links)
    if count == 0:
        return {"label": "出典", "status": "WARN", "detail": "出典 URL（[名](URL) 形式）が見つからない"}
    return {"label": "出典", "status": "PASS", "detail": f"出典 URL {count} 件"}


def check_references(text: str, base_dir: str) -> dict:
    """references: frontmatter ブロック記載パスの実在確認"""
    base_path = Path(base_dir)

    # frontmatter ブロック（--- ... ---）から references: を取得
    fm_match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not fm_match:
        return {"label": "参照正確性", "status": "PASS", "detail": "frontmatterなし（スキップ）"}

    fm_text = fm_match.group(1)

    # references: [] または references:\n  - path 形式を解析
    refs_match = re.search(r"^references:\s*(.*)$", fm_text, re.MULTILINE)
    if not refs_match:
        return {"label": "参照正確性", "status": "PASS", "detail": "referencesフィールドなし（スキップ）"}

    refs_value = refs_match.group(1).strip()

    # インラインの空リスト
    if refs_value == "[]":
        return {"label": "参照正確性", "status": "PASS", "detail": "参照パスなし"}

    # インラインリスト: [path1, path2]
    if refs_value.startswith("["):
        paths_raw = re.findall(r'"([^"]+)"|\'([^\']+)\'|([^\[\],\s]+)', refs_value.strip("[]"))
        paths = [next(p for p in g if p) for g in paths_raw if any(g)]
    else:
        # ブロックリスト形式: references: 直後のインデントブロックのみ抽出
        block_match = re.search(
            r"^references:\s*\n((?:[ \t]+-[ \t]+.+\n?)+)",
            fm_text,
            re.MULTILINE,
        )
        if block_match:
            block_items = re.findall(r"^\s+-\s+(.+)$", block_match.group(1), re.MULTILINE)
            paths = [item.strip().strip("\"'") for item in block_items]
        else:
            paths = []

    # http(s) URL は外部出典 (check_citations が数える)。references: は内部ファイルパス専用なので
    # 実在検査の対象から外す (URL を references: に書く誤用を「不実在パス」と誤判定しないため)。
    file_paths = [p for p in paths if p and not p.startswith(("http://", "https://"))]
    missing = [p for p in file_paths if not Path(p).exists() and not (base_path / p).exists()]
    if missing:
        return {"label": "参照正確性", "status": "FAIL", "detail": f"不実在パス: {', '.join(missing)}"}
    return {"label": "参照正確性", "status": "PASS", "detail": f"全参照パス実在（{len(file_paths)}件）"}


def check_ambiguous_words(text: str) -> dict:
    """曖昧語（受動形「推奨される」）の検出（コードブロック内は除外）"""
    target = _strip_code_blocks(text)
    pattern = re.compile(r"推奨される")
    matches = pattern.findall(target)
    count = len(matches)
    if matches:
        return {"label": "曖昧語検出", "status": "WARN", "detail": f"検出: 推奨される ({count}件)"}
    return {"label": "曖昧語検出", "status": "PASS", "detail": "曖昧語なし"}


def check_template_placeholders(text: str) -> dict:
    """テンプレート埋め忘れプレースホルダー（[…] / [...]）の検出（コードブロック内は除外）"""
    target = _strip_code_blocks(text)
    pattern = re.compile(r"\[…\]|\[\.{3}\]")
    matches = pattern.findall(target)
    count = len(matches)
    if count == 0:
        return {"label": "プレースホルダー検出", "status": "PASS", "detail": "プレースホルダーなし"}
    if count == 1:
        return {"label": "プレースホルダー検出", "status": "WARN", "detail": f"プレースホルダー1件: {matches[0]}"}
    return {
        "label": "プレースホルダー検出",
        "status": "FAIL",
        "detail": f"プレースホルダー{count}件: {', '.join(matches[:5])}",
    }


def check_unresolved_items(text: str) -> dict:
    """## 未解決事項 セクション内の - [ ] 行数をカウントして informational 出力（常に PASS）"""
    # ## 未解決事項 セクション内のテキストを抽出
    section_match = re.search(
        r"^## 未解決事項.*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    if not section_match:
        return {"label": "未解決事項数", "status": "PASS", "detail": "未解決事項セクションなし"}

    section_text = section_match.group(0)
    todo_lines = re.findall(r"^- \[ \] .+$", section_text, re.MULTILINE)
    count = len(todo_lines)
    return {"label": "未解決事項数", "status": "PASS", "detail": f"未解決チェックボックス: {count}件"}


# ── gate 集約 ───────────────────────────────────────────────────


def aggregate_gate(results: list) -> str:
    statuses = {r["status"] for r in results}
    if "FAIL" in statuses:
        return "FAIL"
    if "WARN" in statuses:
        return "WARN"
    return "PASS"


# ── エントリポイント ─────────────────────────────────────────────


def main():
    if len(sys.argv) != 2:
        print(json.dumps({"error": "Usage: research-gate.py <report.md>"}), file=sys.stderr)
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.exists(path):
        print(json.dumps({"error": f"File not found: {path}"}), file=sys.stderr)
        sys.exit(1)
    text = Path(path).read_text(encoding="utf-8")
    base_dir = os.path.dirname(os.path.abspath(path))
    results = [
        check_section_completeness(text),
        check_references(text, base_dir),
        check_citations(text),
        check_ambiguous_words(text),
        check_template_placeholders(text),
        check_unresolved_items(text),
    ]
    output = {"gate": aggregate_gate(results), "results": results}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
