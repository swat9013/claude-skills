#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""issue を閉じる open CL を、gh の出力を保存した file から列挙する。

gh はこの script の外で撃つ。script の中の gh は sandbox の除外指定に当たらないので、呼び出し元が出力を
file に保存し、この script はその file を入力にする。どの CL を数えるか (closing keyword・issue の書き方) の
規則の正本は `references/body-rewrite.md`「open CL の確かめ」で、この script はその規則を GitHub の語で判定する。

- `body <CL の一覧の file> --issue <N> --issue-repo <owner/repo> --cl-repo <owner/repo> --limit <L>`:
  `gh pr list --json number,body` の出力から、本文が closing keyword の直後にこの issue を書いた CL を
  1 行 1 件 `#<番号>` で出す。当たらなければ `該当なし`
- `closed-by <閉じる関連の file> --number <N> [--views <file>...]`: 閉じる関連の file は
  `closedByPullRequestsReferences` の鍵を持つ `gh issue view --json` の出力 (`number` の鍵は無くてよい)。
  `--views` は各 CL の `gh pr view <url> --json url,state` の出力を 1 件 1 file で受けたもの。状態が open の
  CL を 1 行 1 件 `<url>` で出す。当たらなければ `該当なし`

終了コード: 0 は出力し終えた (該当なしを含む)。2 は引き直す — stdout に何も出さず、stderr に
`引き直す: <理由>` を 1 行出す。

invariant:

1. `body` は、返った件数が --limit に届いたら判定せずに exit 2 で終える (超えた分は gh が黙って打ち切る)
2. `closed-by` は、関連の CL のうち状態の file が無いものがあれば exit 2 で終える (引けないまま進むと見落とす)
3. 閉じる関連の file に `closedByPullRequestsReferences` の鍵が無いか、`number` が --number と違えば exit 2 で終える
4. 番号の直後が数字の参照 (`#12` に対する `#123`) を当てない
5. 置き場の無い `#<N>` は、CL 置き場と issue 置き場が同じときだけ当てる
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KEYWORD = r"(?<![A-Za-z0-9_])(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)(?::\s*|\s+)"
REF = re.compile(
    KEYWORD
    + r"(?:https?://[^/\s]+/(?P<url_repo>[\w.-]+/[\w.-]+)/issues/(?P<url_n>\d+)"
    + r"|(?P<repo>[\w.-]+/[\w.-]+)?#(?P<n>\d+))(?!\d)",
    re.IGNORECASE,
)


class Refetch(Exception):
    """入力の file が判定に使えない。message が引き直す理由。"""


def _json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise Refetch(f"JSON として読めない ({path}: {e})") from e


def closes(body: str, issue: int, issue_repo: str, cl_repo: str) -> bool:
    same = issue_repo.lower() == cl_repo.lower()
    for m in REF.finditer(body or ""):
        repo, n = (m["url_repo"], m["url_n"]) if m["url_n"] else (m["repo"], m["n"])
        if int(n) != issue:
            continue
        if (repo is None and same) or (repo is not None and repo.lower() == issue_repo.lower()):
            return True
    return False


def body_matches(path: Path, issue: int, issue_repo: str, cl_repo: str, limit: int) -> list[int]:
    data = _json(path)
    if not isinstance(data, list) or not all(isinstance(c, dict) and "number" in c and "body" in c for c in data):
        raise Refetch(f"number と body を持つ CL の配列でない ({path})")
    if len(data) >= limit:
        raise Refetch(f"件数 {len(data)} が --limit {limit} に届いた — --limit を上げて引き直す ({path})")
    return [c["number"] for c in data if closes(c["body"], issue, issue_repo, cl_repo)]


def closing_refs(path: Path, number: int) -> list[str]:
    data = _json(path)
    if not isinstance(data, dict) or data.get("number", number) != number:
        raise Refetch(f"番号 {number} の閉じる関連の取得結果でない ({path})")
    if "closedByPullRequestsReferences" not in data:
        raise Refetch(f"closedByPullRequestsReferences の鍵が無い — --json に足して取り直す ({path})")
    refs = data["closedByPullRequestsReferences"] or []
    if not all(isinstance(r, dict) and r.get("url") for r in refs):
        raise Refetch(f"closedByPullRequestsReferences に url の無い要素がある ({path})")
    return [r["url"] for r in refs]


def open_closers(path: Path, number: int, views: list[Path]) -> list[str]:
    states = {}
    for v in views:
        data = _json(v)
        if not isinstance(data, dict) or "url" not in data or "state" not in data:
            raise Refetch(f"url と state を持つ gh pr view の出力でない ({v})")
        states[data["url"]] = data["state"]
    urls = closing_refs(path, number)
    missing = [u for u in urls if u not in states]
    if missing:
        raise Refetch(f"状態の file が無い CL がある: {' '.join(missing)}")
    return [u for u in urls if str(states[u]).upper() == "OPEN"]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("body", help="CL の本文の closing keyword から閉じる CL を列挙する")
    b.add_argument("file", type=Path)
    b.add_argument("--issue", type=int, required=True)
    b.add_argument("--issue-repo", required=True)
    b.add_argument("--cl-repo", required=True)
    b.add_argument("--limit", type=int, required=True)
    c = sub.add_parser("closed-by", help="閉じる関連の CL のうち open のものを列挙する")
    c.add_argument("file", type=Path)
    c.add_argument("--number", type=int, required=True)
    c.add_argument("--views", type=Path, nargs="*", default=[])
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.cmd == "body":
            lines = [f"#{n}" for n in body_matches(args.file, args.issue, args.issue_repo, args.cl_repo, args.limit)]
        else:
            lines = open_closers(args.file, args.number, args.views)
    except Refetch as e:
        print(f"引き直す: {e}", file=sys.stderr)
        return 2
    print("\n".join(lines) if lines else "該当なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
