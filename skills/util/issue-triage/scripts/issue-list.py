#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""issue の一覧の file から、件数の判定・重複探し・発端の行の補いを行う。

gh はこの script の外で撃ち、出力を保存した file を入力にする (script の中の gh は sandbox の除外指定に当たらない)。

- `limit <一覧の file> --limit <L>`: 件数が --limit に届いていなければ `件数: <n>` を出す
- `title-prefix <一覧の file> --path <負けた規範の path>`: 題名が `<path>:` で始まる issue を 1 行 1 件
  `#<番号> <題名>` で出す。当たらなければ `該当なし`
- `append <一覧の file> --number <N> --title <題名>`: 起票した issue を一覧の file に足す
- `origin-line <文案の file> --issue <N>`: 文案に `発端:` で始まる行が無ければ、末尾に独立した 1 行
  `発端: #<N>` を足して `足した` を出す。既にあれば変えずに `既にある` を出す
- `norm-draft <一覧の file> --path <置き場の path> --limit <L>`: `gh issue list --label ready-for-human
  --json number,comments` の出力から、行頭が `規範の文案: <path>` で path の後ろに何も無い行を持つコメントの
  issue を 1 行 1 件 `#<番号>` で出す。当たらなければ `該当なし`

一覧の file は `gh issue list --json number,title` (norm-draft は `number,comments`) の出力の JSON 配列で、gh の形だけを読む。

終了コード: 0 は出し終えた (該当なしを含む)。2 は使えない入力か、件数が --limit に届いた — stdout に何も出さず、
stderr に `引き直す: <理由>` を出す。

invariant:

1. `limit` と `norm-draft` は、件数が --limit に届いたら判定せずに exit 2 で終える (超えた分は gh が黙って打ち切る)
2. `origin-line` は `発端:` の行を 2 本にしない (行の置き方の正本は `issue-tracker-operations` の
   `references/body-rewrite.md`「書き換え案に残す行」)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ORIGIN = "発端:"
NORM_DRAFT = "規範の文案: "


class Refetch(Exception):
    """入力が使えない。message が理由。"""


def load(path: Path, keys: tuple[str, ...]) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise Refetch(f"JSON として読めない ({path}: {e})") from e
    if not isinstance(data, list) or not all(isinstance(i, dict) and all(k in i for k in keys) for i in data):
        raise Refetch(f"{'・'.join(keys)} を持つ issue の配列でない ({path})")
    return data


def check_limit(issues: list[dict], limit: int, path: Path) -> None:
    if len(issues) >= limit:
        raise Refetch(f"件数 {len(issues)} が --limit {limit} に届いた — --limit を上げて引き直す ({path})")


def has_norm_draft(issue: dict, path: str) -> bool:
    target = NORM_DRAFT + path
    return any(
        line.rstrip() == target
        for c in issue["comments"] or []
        for line in (c.get("body") or "").splitlines()
    )


def add_origin(text: str, issue: int) -> str | None:
    if any(line.startswith(ORIGIN) for line in text.splitlines()):
        return None
    return f"{text.rstrip()}\n\n{ORIGIN} #{issue}\n"


def run(args: argparse.Namespace) -> list[str]:
    if args.cmd == "limit":
        issues = load(args.file, ("number",))
        check_limit(issues, args.limit, args.file)
        return [f"件数: {len(issues)}"]
    if args.cmd == "title-prefix":
        prefix = f"{args.path}:"
        return [f"#{i['number']} {i['title']}" for i in load(args.file, ("number", "title")) if i["title"].startswith(prefix)]
    if args.cmd == "append":
        issues = load(args.file, ("number", "title"))
        issues.append({"number": args.number, "title": args.title})
        args.file.write_text(json.dumps(issues, ensure_ascii=False), encoding="utf-8")
        return [f"足した: #{args.number}"]
    if args.cmd == "origin-line":
        try:
            text = args.file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise Refetch(f"文案を読めない ({args.file}: {e})") from e
        new = add_origin(text, args.issue)
        if new is None:
            return ["既にある"]
        args.file.write_text(new, encoding="utf-8")
        return ["足した"]
    issues = load(args.file, ("number", "comments"))
    check_limit(issues, args.limit, args.file)
    return [f"#{i['number']}" for i in issues if has_norm_draft(i, args.path)]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("limit", help="件数が --limit に届いたかを見る")
    s.add_argument("file", type=Path)
    s.add_argument("--limit", type=int, required=True)
    s = sub.add_parser("title-prefix", help="題名が <path>: で始まる issue を探す")
    s.add_argument("file", type=Path)
    s.add_argument("--path", required=True)
    s = sub.add_parser("append", help="起票した issue を一覧の file に足す")
    s.add_argument("file", type=Path)
    s.add_argument("--number", type=int, required=True)
    s.add_argument("--title", required=True)
    s = sub.add_parser("origin-line", help="文案に発端の行が無ければ足す")
    s.add_argument("file", type=Path)
    s.add_argument("--issue", type=int, required=True)
    s = sub.add_parser("norm-draft", help="同じ path の規範の文案を待つ issue を探す")
    s.add_argument("file", type=Path)
    s.add_argument("--path", required=True)
    s.add_argument("--limit", type=int, required=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        lines = run(args)
    except Refetch as e:
        print(f"引き直す: {e}", file=sys.stderr)
        return 2
    print("\n".join(lines) if lines else "該当なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
