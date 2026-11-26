#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""stacked-implement の入力の issue を読む: args の番号の展開・取得結果の分類・明示の依存の抜き出し。

取得結果の file は、issue-tracker-operations の `issue-fetch.py` の `command` で取得した出力を `read` に通した、
共通の鍵の JSON (使うのは `title`・`body`・`state`・`url`・`blocked_by`)。`read` が exit 2 で終えた issue は stdout が
空なので、その空の file を渡せば「読めない」に分かれる。gh と glab の出力の形の違いは `read` がそろえ、この script は持たない。
file は `<参照>=<file>` の形で渡す。`<参照>` は --repo の issue の番号 (`14`) か、置き場付きの
参照 (`owner/repo#14`)。

- `expand <arg>...`: 範囲 (`14-16`。両端を含む) と列挙 (`14 15`) を、重複を除いた番号の列へ展開し、1 行 1 件出す
- `classify --repo <issue 置き場> <参照>=<file>...`: 取得結果を target・closed・pr・unreadable に分けた JSON を出す。分ける順は
  unreadable (file が無い・空・JSON でない・read の出力でない・title / body / state / url の鍵を欠くか state が open / closed で
  ない・url が null・blocked_by が node の配列でも null でもない) → pr (url が `/pull/` を含む)
  → closed (state が open でない) → target
- `deps --repo <issue 置き場> --targets <N,...> <参照>=<file>...`: target の issue ごとに、tracker の依存関係
  (`blocked_by`。gh の `blockedBy`) と本文の明示参照 (`Blocked by #N` / `Depends on #N` / `#N に依存`。キーワードの後の `:` と強調の `**`、参照を `,` `、` `・` `and`
  で並べたものを含む。`#N に依存しない`・`not blocked by #N`・`no longer depends on #N` などの打ち消し (英語はキーワードの直前の not・n't・no longer・never、日本語は「に依存」の後の同じ句の ない・ません・ず・なく・なけれ) は含めない) から依存先を抜き出し、`<issue 置き場>#<N>` の形の参照にそろえた JSON を出す。鍵は `issues` (target の参照 → 依存先の参照の配列)・`states` (依存先の参照 → `open` / `closed` / `unreadable`)・`unknown` (状態を引けない依存先の参照の配列)。素の `#N` は本文が
  載った置き場 (--repo) の番号として読む。置き場は大文字小文字をそろえて (casefold) 出す。blocked_by の node の url が issue の
  参照として読めなければ exit 2。gh の target は `--with-blocked-by` で取った取得結果を求め、blocked_by が null なら exit 2。依存先の状態は blocked_by の値か、渡された file から引き (読めない file は
  unreadable)、どちらでも引けない参照を `unknown` に並べる。呼び出し元は `unknown` の参照を
  その置き場で取得し、file を足して呼び直す

出力の JSON は stdout へ 1 つ。引数の誤り (範囲の始点が終点より大きい・数でない・参照の形が違う) は stderr に理由を出して
exit 2。

invariant:

1. 範囲は両端を含めて展開し、範囲と列挙の重複は 1 件にする
2. url が `/pull/` を含む取得結果は、state によらず pr に分ける
3. 読めない file (read を通していない取得結果を含む) は、どの分類よりも先に unreadable に分ける
4. 依存先の参照は必ず置き場付きで出す (素の `#N` は --repo を付ける)
5. 状態を引けない依存先は `unknown` に挙げ、状態を推測しない
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# issue-fetch.py read の出力のうち、この script が読む鍵 (state は open / closed、url は null を許さない)
REQUIRED_KEYS = ("title", "body", "state", "url")
# blocked_by の node の state (gh の値そのまま) の読み
NODE_STATES = {"open": "open", "closed": "closed", "merged": "closed"}

_REF = r"\**(?:[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)?#[0-9]+\**"
_REF_LIST = rf"{_REF}(?:\s*(?:,|、|・|and|&)\s*{_REF})*"
BODY_DEP_PATTERNS = [
    re.compile(rf"\b(?:blocked\s+by|depends\s+on)\**\s*[:：]?\s*({_REF_LIST})", re.IGNORECASE),
    # 「に依存」の後の同じ句 (読点・句点・改行まで) に打ち消しか条件の語があれば明示の依存としない
    re.compile(rf"({_REF_LIST})\s*に依存(?!は|[^、。\n]{{0,8}}?(?:ない|なかった|ません|ず|なく|なけれ))"),
]
# 英語のキーワードの直前の打ち消し (強調の ** と空白・改行を挟んでよい)
EN_NEGATION_RE = re.compile(r"(?:\bnot|n['’]t|\bno\s+longer|\bnever)[\s*]*$", re.IGNORECASE)
REF_RE = re.compile(r"(?:([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+))?#([0-9]+)")
ISSUE_URL_RE = re.compile(r"^https?://[^/]+/(.+?)/(?:-/)?issues/(\d+)$")


class ArgError(Exception):
    """引数の形が違う。message が理由。"""


class Unreadable(Exception):
    """取得結果の file が使えない。message が理由。"""


def expand(args: list[str]) -> list[int]:
    out: list[int] = []
    for a in args:
        m = re.fullmatch(r"([0-9]+)-([0-9]+)", a)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                raise ArgError(f"範囲の始点が終点より大きい: {a}")
            nums = range(lo, hi + 1)
        elif re.fullmatch(r"[0-9]+", a):
            nums = [int(a)]
        else:
            raise ArgError(f"番号でも範囲でもない: {a}")
        out.extend(n for n in nums if n not in out)
    return out


def placed_ref(place: str | None, number: str, repo: str) -> str:
    """置き場付きの参照にそろえる (素の #N は repo の番号。置き場は casefold)。"""
    return f"{(place or repo).casefold()}#{number}"


def canonical_ref(token: str, repo: str) -> str:
    m = REF_RE.fullmatch(token)
    if not m:
        raise ArgError(f"参照の形が違う (<N> か <置き場>#<N>): {token}")
    return placed_ref(m.group(1), m.group(2), repo)


def parse_pairs(pairs: list[str], repo: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in pairs:
        ref, sep, path = p.partition("=")
        if not sep or not path:
            raise ArgError(f"<参照>=<file> の形でない: {p}")
        token = ref if "#" in ref else f"#{ref}"
        out[canonical_ref(token, repo)] = Path(path)
    return out


def load(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise Unreadable(f"file を読めない ({path}: {e})") from e
    if not text.strip():
        raise Unreadable(f"file が空 ({path})")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise Unreadable(f"JSON として読めない ({path}: {e.msg} line {e.lineno})") from e
    if not isinstance(data, dict):
        raise Unreadable(f"JSON の最上位が object でない ({path})")
    if data.get("form") not in ("gh", "glab"):
        raise Unreadable(f"issue-fetch.py read の出力でない (form の鍵が無い。生の取得結果は read に通す) ({path})")
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise Unreadable(f"鍵 {', '.join(missing)} が無い (issue-fetch.py の command で取り直す) ({path})")
    if data["state"] not in ("open", "closed"):
        raise Unreadable(f"state が open / closed でない (値: {data['state']!r}。null は tracker の状態が不明) ({path})")
    if data["url"] is None:
        raise Unreadable(f"url が null (issue-fetch.py の command で取り直す) ({path})")
    nodes = data.get("blocked_by")
    if nodes is not None and not (isinstance(nodes, list) and all(isinstance(n, dict) for n in nodes)):
        raise Unreadable(f"blocked_by が node の配列でない ({path})")
    return data


def kind_of(data: dict) -> str:
    if "/pull/" in str(data["url"]):
        return "pr"
    if data["state"] != "open":
        return "closed"
    return "target"


def classify(files: dict[str, Path]) -> dict:
    out: dict = {"target": [], "closed": [], "pr": [], "unreadable": []}
    for ref, path in files.items():
        try:
            data = load(path)
        except Unreadable as e:
            out["unreadable"].append({"ref": ref, "reason": str(e)})
            continue
        out[kind_of(data)].append(ref)
    return out


def body_deps(body: str, repo: str) -> list[str]:
    refs: list[str] = []
    body = body or ""
    for pat in BODY_DEP_PATTERNS:
        for m in pat.finditer(body):
            if pat is BODY_DEP_PATTERNS[0] and EN_NEGATION_RE.search(body[max(0, m.start() - 20):m.start()]):
                continue
            for r in REF_RE.finditer(m.group(1)):
                got = placed_ref(r.group(1), r.group(2), repo)
                if got not in refs:
                    refs.append(got)
    return refs


def blocked_by(data: dict, target: str) -> list[tuple[str, str]]:
    nodes = data.get("blocked_by") or []
    out = []
    for n in nodes:
        m = ISSUE_URL_RE.match(str(n.get("url", "")))
        if not m:
            raise ArgError(f"target {target} の取得結果の blocked_by の node の url を issue の参照として読めない "
                           f"({n.get('url')!r}。issue-fetch.py の command で取り直す)")
        out.append((placed_ref(m.group(1), m.group(2), ""), str(n.get("state", "")).lower()))
    return out


def deps(files: dict[str, Path], targets: list[str], repo: str) -> dict:
    loaded: dict[str, dict | None] = {}
    for ref, path in files.items():
        try:
            loaded[ref] = load(path)
        except Unreadable:
            loaded[ref] = None
    issues: dict[str, list[str]] = {}
    states: dict[str, str] = {}
    for t in targets:
        got = loaded.get(t)
        if got is None:
            raise ArgError(f"target {t} の読める取得結果が渡されていない")
        if got["form"] == "gh" and got.get("blocked_by") is None:
            raise ArgError(f"target {t} の gh の取得結果に blocked_by が無い (issue-fetch.py の command に --with-blocked-by を付けて取り直す)")
        found: list[str] = []
        for dep, st in blocked_by(got, t):
            found.append(dep)
            if st in NODE_STATES:  # 分からない値は状態を推測せず unknown に回す
                states[dep] = NODE_STATES[st]
        found += body_deps(got["body"], repo)
        issues[t] = [r for i, r in enumerate(found) if r != t and r not in found[:i]]
    unknown: list[str] = []
    for ref in (r for rs in issues.values() for r in rs):
        if ref in loaded:
            got = loaded[ref]
            states[ref] = "unreadable" if got is None else got["state"]
        elif ref not in states and ref not in unknown:
            unknown.append(ref)
    used = {r for rs in issues.values() for r in rs}
    return {"issues": issues, "states": {k: v for k, v in states.items() if k in used}, "unknown": unknown}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("expand", help="範囲と列挙を番号の列へ展開する")
    e.add_argument("args", nargs="+")
    c = sub.add_parser("classify", help="取得結果を target・closed・pr・unreadable に分ける")
    c.add_argument("--repo", required=True, help="issue 置き場")
    c.add_argument("pairs", nargs="+", metavar="<参照>=<file>")
    d = sub.add_parser("deps", help="target の issue の明示の依存先を抜き出す")
    d.add_argument("--repo", required=True, help="issue 置き場 (本文の素の #N をこの置き場で読む)")
    d.add_argument("--targets", required=True, help="依存を抜き出す issue の参照を , 区切りで")
    d.add_argument("pairs", nargs="+", metavar="<参照>=<file>")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.cmd == "expand":
            print("\n".join(str(n) for n in expand(args.args)))
            return 0
        files = parse_pairs(args.pairs, args.repo)
        if args.cmd == "classify":
            result = classify(files)
        else:
            targets = [canonical_ref(t if "#" in t else f"#{t}", args.repo) for t in args.targets.split(",") if t]
            result = deps(files, targets, args.repo)
    except ArgError as e:
        print(f"引数の誤り: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
