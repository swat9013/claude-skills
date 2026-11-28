#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""issue の取得コマンドを出し、取得結果の file を検査して tracker によらない共通の鍵へそろえる。

tracker の CLI (gh / glab) はこの script の外で撃つ。この script が出すのは取得コマンドまでで、
ほかの subcommand は呼び出し元が取得の JSON 出力をそのまま保存した file を入力にする。
鍵の対応の正本は `references/fetch-result-keys.md` の表 (tests が表と下の定数を突き合わせる)。
値が null の本文・label・コメントは空として読む (本文の無い glab の issue は `description` が null になる)。

- `command --tracker <gh|glab> --repo <issue 置き場> [--with-blocked-by] <N>...`: issue ごとの取得コマンドを 1 行 1 件 stdout へ出す。
  `--with-blocked-by` は gh の `--json` に `blockedBy` を足す (古い gh は `blockedBy` を知らず取得が落ちるので、要る呼び出し元だけが付ける)
- `read <取得結果の file> --number <N>`: file を検査し、使えれば共通の鍵の JSON を stdout へ出す。
  共通の鍵は `form` (`gh` / `glab`)・`number`・`title`・`body`・`labels` (label 名の配列)・
  `comments` (`author` / `created_at` / `body` の配列。1 行目が `旧本文の記録 (現行の仕様ではない)` の
  コメントと、glab の `system` が true の note は除く)・`old_body_records` (除いた旧本文の記録のコメント。形は
  `comments` と同じ)・`closed_by` (gh の `closedByPullRequestsReferences` の
  値をそのまま。鍵が無い取得結果では null)・`state` (`open` / `closed`。gh の `MERGED` は `closed`、鍵が無いか値が未知なら null。null は状態が不明であることを示す)・
  `url` (gh の `url` / glab の `web_url`。鍵が無ければ null)・`blocked_by` (gh の `blockedBy.nodes` をそのまま。glab と、
  鍵が無い取得結果では null)
- `body <取得結果の file> --number <N> --out <file>`: `read` と同じ検査をし、本文の鍵の値を逐語で --out へ書く

`read` と `body` は、file が使えないとき (読めない・JSON でない・gh と glab のどちらの形でもない・番号 /
題名 / 本文 / label / コメントの鍵のどれかを欠く・番号が --number と一致しない) に stdout へ何も出さず、
stderr へ `取り直す: <理由>` を 1 行出して exit 2 で終える。呼び出し元は `command` のコマンドで取り直す。
`body` が --out へ書けないときは stderr へ `error: <理由>` を出して exit 1 で終える (取り直しでは直らない)。

invariant:

1. gh の取得コマンドは `--json` に `closedByPullRequestsReferences`・`state`・`url` を含め、`blockedBy` は `--with-blocked-by` のときだけ含める
2. glab の取得コマンドは `--comments` を含める (付けないとコメントの鍵が出ない)
3. 検査に落ちた file からは何も出力しない (exit 2)
4. `read` の comments に、1 行目が旧本文の記録のコメントと glab の system note を含めない (旧本文の記録は old_body_records に分ける)
5. `body` は本文の鍵の値を改行も含めて逐語で書く
6. `read` の `state` は open / closed / null だけを出し、未知の値を closed と読まない
7. 値が null の本文は空の文字列として読む
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

OLD_BODY_RECORD_HEADING = "旧本文の記録 (現行の仕様ではない)"
GH_FIELDS = "number,title,body,labels,comments,closedByPullRequestsReferences,state,url"
# blockedBy は古い gh の --json が知らず、取得そのものを落とすので、要る呼び出し元だけが --with-blocked-by で足す
GH_BLOCKED_BY = "blockedBy"

# 形ごとの、読む値の鍵 (fetch-result-keys.md の表)。FORM_KEYS は必須、OPTIONAL_KEYS は無ければ null
FORM_KEYS = {
    "gh": {"number": "number", "title": "title", "body": "body", "labels": "labels", "comments": "comments"},
    "glab": {"number": "iid", "title": "title", "body": "description", "labels": "labels", "comments": "Notes"},
}


OPTIONAL_KEYS = {
    "gh": {"state": "state", "url": "url"},
    "glab": {"state": "state", "url": "web_url"},
}
STATES = {"open": "open", "opened": "open", "closed": "closed", "merged": "closed"}


class Refetch(Exception):
    """取得結果の file が使えない。message が取り直す理由。"""


def fetch_commands(tracker: str, repo: str, numbers: list[int], with_blocked_by: bool) -> list[str]:
    repo = shlex.quote(repo)  # 出力の行はそのまま Bash で撃たれる
    if tracker == "gh":
        fields = f"{GH_FIELDS},{GH_BLOCKED_BY}" if with_blocked_by else GH_FIELDS
        return [f"gh issue view {n} -R {repo} --json {fields}" for n in numbers]
    return [f"glab issue view {n} -R {repo} --comments --output json" for n in numbers]


def _load(path: Path, number: int) -> tuple[str, dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise Refetch(f"file を読めない ({path}: {e})") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise Refetch(f"JSON として読めない ({path}: {e.msg} line {e.lineno})") from e
    if not isinstance(data, dict):
        raise Refetch(f"JSON の最上位が object でない ({path})")
    if "number" in data:
        form = "gh"
    elif "iid" in data:
        form = "glab"
    else:
        raise Refetch(f"gh の形 (number) と glab の形 (iid) のどちらの番号の鍵も無い ({path})")
    missing = [k for k in FORM_KEYS[form].values() if k not in data]
    if missing:
        raise Refetch(f"{form} の形で鍵 {', '.join(missing)} が無い ({path})")
    got = data[FORM_KEYS[form]["number"]]
    if got != number:
        raise Refetch(f"番号 {got!r} が見出しの {number} と一致しない ({path})")
    return form, data


def _state(raw: object) -> str | None:
    return STATES.get(raw.lower()) if isinstance(raw, str) else None


def _body(form: str, data: dict) -> str:
    value = data[FORM_KEYS[form]["body"]] or ""
    if not isinstance(value, str):
        raise TypeError(f"本文の値が文字列でない ({type(value).__name__})")
    return value


def _is_old_body_record(body: str) -> bool:
    first = body.splitlines()[0] if body else ""
    return first.strip() == OLD_BODY_RECORD_HEADING


def normalize(form: str, data: dict) -> dict:
    keys = FORM_KEYS[form]
    if form == "gh":
        labels = [label["name"] for label in data["labels"] or []]
        comments = [
            {"author": (c.get("author") or {}).get("login"), "created_at": c.get("createdAt"), "body": c["body"] or ""}
            for c in data["comments"] or []
        ]
        closed_by = data.get("closedByPullRequestsReferences")
    else:
        labels = list(data["labels"] or [])
        comments = [
            {"author": (n.get("author") or {}).get("username"), "created_at": n.get("created_at"), "body": n["body"] or ""}
            for n in (data["Notes"] or [])
            if not n.get("system")
        ]
        closed_by = None
    optional = OPTIONAL_KEYS[form]
    return {
        "form": form,
        "number": data[keys["number"]],
        "title": data[keys["title"]],
        "body": _body(form, data),
        "labels": labels,
        "comments": [c for c in comments if not _is_old_body_record(c["body"])],
        "old_body_records": [c for c in comments if _is_old_body_record(c["body"])],
        "closed_by": closed_by,
        "state": _state(data.get(optional["state"])),
        "url": data.get(optional["url"]),
        "blocked_by": (data.get(GH_BLOCKED_BY) or {}).get("nodes") if form == "gh" and GH_BLOCKED_BY in data else None,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("command", help="issue ごとの取得コマンドを出す")
    c.add_argument("--tracker", choices=sorted(FORM_KEYS), required=True)
    c.add_argument("--repo", required=True, help="issue 置き場 (-R に渡す値)")
    c.add_argument("--with-blocked-by", action="store_true", help="gh の --json に blockedBy を足す (古い gh は知らない)")
    c.add_argument("numbers", nargs="+", type=int)
    r = sub.add_parser("read", help="取得結果の file を検査し、共通の鍵の JSON を出す")
    r.add_argument("file", type=Path)
    r.add_argument("--number", type=int, required=True)
    b = sub.add_parser("body", help="取得結果の file から本文の鍵の値を逐語で切り出す")
    b.add_argument("file", type=Path)
    b.add_argument("--number", type=int, required=True)
    b.add_argument("--out", type=Path, required=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cmd == "command":
        print("\n".join(fetch_commands(args.tracker, args.repo, args.numbers, args.with_blocked_by)))
        return 0
    try:
        form, data = _load(args.file, args.number)
    except Refetch as e:
        print(f"取り直す: {e}", file=sys.stderr)
        return 2
    try:
        normalized = normalize(form, data)
    except (KeyError, TypeError, AttributeError) as e:
        print(f"取り直す: {form} の形で値の形が違う ({args.file}: {e!r})", file=sys.stderr)
        return 2
    if args.cmd == "read":
        print(json.dumps(normalized, ensure_ascii=False, indent=2))
        return 0
    try:
        args.out.write_text(normalized["body"], encoding="utf-8")
    except OSError as e:
        print(f"error: --out へ書けない ({args.out}: {e})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
