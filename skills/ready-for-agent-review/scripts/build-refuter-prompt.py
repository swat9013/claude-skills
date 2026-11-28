#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""手順 4 の refuter の prompt を組み、file に書く。

入力は 2 つ。指摘・引用・出典などを埋めた雛形の file (`{{issue}}` の行を 1 行だけ持つ) と、手順 1 で
`issue-fetch.py read` が出した共通の鍵の JSON を保存した file。書き換え案があれば、その逐語の file も渡す。

    build-refuter-prompt.py <雛形の file> --issue <read の出力の file> [--rewrite <書き換え案の file>] --out <prompt の file>

- 出典の欄 (`- 規範の出典:`・`- 設計 doc の区分`・`- 裁定` で始まる行から、雛形の次の欄の行の手前まで。
  2 本目の出典を子の箇条で続けた行と、貼った節の空行・箇条の後の行も含む) の `<path>:<line>` の path の頭にある
  `${CLAUDE_SKILL_DIR}` を、この script を持つ skill の絶対 path (symlink は解かず、`..` は字面で畳む) に展開する
  (subagent 側ではこの変数が解決されない)。ほかの行 (`- issue 側の引用:` など) と `<issue>` ブロックの中は展開しない
- 最後の `- 対象 repo:` の行より後で最初の `{{issue}}` の行を `<issue>` ブロックに置き換える (それより前の
  `{{issue}}` の行は、逐語で貼った規範の中のものとして残す)。ブロックは題名・label・本文・(書き換え案があれば)「本文の書き換え案 (未反映)」の
  見出しと書き換え案・author と日時付きのコメントの逐語。旧本文の記録のコメント (`read` の出力の
  `old_body_records` と、`comments` のうち 1 行目が `旧本文の記録 (現行の仕様ではない)` のもの) は入れない

invariant:

1. 雛形の `<path>:<line>` の形の出典に `${CLAUDE_SKILL_DIR}` を残さず、逐語の引用と `<issue>` ブロックは変えない
2. `<issue>` ブロックに旧本文の記録のコメントを入れない
3. `- 対象 repo:` の行の後に `{{issue}}` の行が無いか、read の出力の鍵が欠けるか形が違えば、exit 1 で --out を書かない
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# symlink を解かない (plugin を symlink で置いた session は skill をその path で読む)
SKILL_DIR = Path(os.path.abspath(__file__)).parents[1]
PLACEHOLDER = "{{issue}}"
SKILL_DIR_SOURCE = re.compile(r"\$\{CLAUDE_SKILL_DIR\}([^\s:`」]*)(?=:\d)")
SOURCE_FIELDS = ("- 規範の出典:", "- 設計 doc の区分", "- 裁定")
# 雛形 (SKILL.md 手順 4) の欄の頭。欄はこのどれかで始まる行から次の欄の行の手前まで (貼った節の空行や箇条では切らない)
FIELDS = ("- 軸:", "- 指摘:", "- issue 側の引用:", *SOURCE_FIELDS, "- 対象 repo:")
LAST_FIELD = "- 対象 repo:"
OLD_BODY_RECORD_HEADING = "旧本文の記録 (現行の仕様ではない)"


class InputError(Exception):
    pass


def _check_issue(issue: object) -> None:
    if not isinstance(issue, dict):
        raise InputError("read の出力が object でない")
    for key, kind in (("title", str), ("body", str), ("labels", list), ("comments", list)):
        if not isinstance(issue.get(key), kind):
            raise InputError(f"read の出力の {key} が無いか {kind.__name__} でない")
    if not all(isinstance(label, str) for label in issue["labels"]):
        raise InputError("read の出力の labels が label 名の配列でない")
    for i, c in enumerate(issue["comments"]):
        if not isinstance(c, dict) or not isinstance(c.get("body"), str) or not {"author", "created_at"} <= c.keys():
            raise InputError(f"read の出力の comments[{i}] に author / created_at / body (文字列) のどれかが無い")


def _is_old_body_record(body: str) -> bool:
    return body.splitlines()[0].strip() == OLD_BODY_RECORD_HEADING if body else False


def issue_block(issue: dict, rewrite: str | None) -> str:
    lines = ["<issue>", f"題名: {issue['title']}", f"label: {', '.join(issue['labels']) or 'なし'}", "", "本文:", issue["body"]]
    if rewrite is not None:
        lines += ["", "本文の書き換え案 (未反映):", rewrite]
    comments = [c for c in issue["comments"] if not _is_old_body_record(c["body"])]
    lines += ["", f"コメント ({len(comments)} 件):"]
    for c in comments:
        lines += ["", f"--- {c['author'] or '(不明)'} {c['created_at'] or '(不明)'}", c["body"]]
    lines.append("</issue>")
    return "\n".join(lines)


def build(template: str, issue: object, rewrite: str | None) -> str:
    lines = template.split("\n")
    last_field = max((i for i, line in enumerate(lines) if line.startswith(LAST_FIELD)), default=-1)
    mark = next((i for i in range(last_field + 1, len(lines)) if lines[i].rstrip("\r") == PLACEHOLDER), None)
    if last_field < 0 or mark is None:
        raise InputError(f"雛形の {LAST_FIELD} の行の後に {PLACEHOLDER} の行が無い")
    _check_issue(issue)

    head, in_source = [], False
    for line in lines[:mark]:
        if line.startswith(FIELDS):
            in_source = line.startswith(SOURCE_FIELDS)
        if in_source:
            line = SKILL_DIR_SOURCE.sub(lambda m: os.path.normpath(str(SKILL_DIR) + m.group(1)), line)
        head.append(line)
    return "\n".join([*head, issue_block(issue, rewrite), *lines[mark + 1:]])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("template", type=Path)
    p.add_argument("--issue", type=Path, required=True)
    p.add_argument("--rewrite", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        template = args.template.read_text(encoding="utf-8")
        issue = json.loads(args.issue.read_text(encoding="utf-8"))
        rewrite = args.rewrite.read_text(encoding="utf-8") if args.rewrite else None
        prompt = build(template, issue, rewrite)
        args.out.write_text(prompt, encoding="utf-8")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, InputError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
