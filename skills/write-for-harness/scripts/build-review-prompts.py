#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""write-for-harness 手順 4 の検証 subagent と構造 reviewer に渡す prompt を組み立てて stdout へ出す。

    build-review-prompts.py verify --component <component> --target <path>... [--snapshot <控え file>]
        [--model-ref <path>...] [--agent-ref] [--range <path>=<行範囲>...]
        [--design-later | --design-doc <置き場> <索引 (無ければ '')> <照らす対象 (, 区切り)>...]
    build-review-prompts.py structure --target <path>...

- verify: component 別 reference (`--agent-ref` で agent の reference も)・文面規範・`--model-ref` を参照に並べる。
  `--range` を渡した file だけを行範囲の行に並べ、渡さない file は全行を診断したものとして外す (どれにも渡さなければ
  行ごと落とす)。`--design-later` か、`--design-doc` が 0 件なら、原則 leaf の行と「照らす設計 doc」の行から末尾までを
  落とす。パッチの diff は、snapshot-changes.py take の控え (`--keep` で控えた中身、無ければ控えた HEAD の中身) から
  今の file への unified diff を省かずに埋める
- structure: 構造の reference と、list-harness-inventory.py --with-tracked に対象を渡した一覧を全件埋める

当たる設計 doc を決める判断はこの script に無い (`--design-doc` で受ける)。

invariant:

1. 参照する reference・設計 doc の置き場・索引の path が無ければ、prompt を出さずに stderr へ 1 行出して exit 1
2. template・reference・対象・設計 doc の行に `${` が残れば、出さずに stderr へ 1 行出して exit 1 (diff と一覧は
   file の中身なので見ない — skill の本文は `${CLAUDE_SKILL_DIR}` を正当に含む)
3. 対象のどれにも控えからの差分が無いか、控えに中身の無い対象があれば、出さずに stderr へ 1 行出して exit 1
4. 一覧の script が exit 非 0 なら、その stderr の 1 行を stderr へ出して exit 1
5. `--range` が `--target` の file の `<path>=<行範囲>` でないか、`--design-doc` の照らす対象が `--target` に無ければ、
   出さずに stderr へ 1 行出して exit 1
6. 控えた HEAD を引けないときは、空の起点で diff を作らずに stderr へ 1 行出して exit 1
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(os.path.abspath(__file__)).parent.parent
REFS = SKILL_DIR / "references"
# 他の skill の file は、この file の dir からの相対 path 1 本で指す (skill の置き場の段数は配り方で変わり、この形の path だけがそれに合わせて書き換わる)
CONFORMANCE_LEAF = Path(os.path.normpath(
    SKILL_DIR / "scripts" / "../../principle-index/references/leaves/principle-design-conformance.md"
))
INVENTORY_SCRIPT = SKILL_DIR / "scripts" / "list-harness-inventory.py"
RANGE = re.compile(r"^\d+(?:-\d+)?(?:,\s*\d+(?:-\d+)?)*$")

VERIFY_HEAD = """component: {component}

参照する reference (絶対 path):
{refs}

レビュー対象 (絶対 path):
{targets}

reference が公式 docs の確認を求めていても、外部の確認を試みず reference に書かれた内容の範囲で判定する。
"""
SCOPE_LINE = "レビューするのは、次の行範囲と、その範囲が他の行と食い違いを生む箇所だけ: {ranges}"
VERIFY_DESIGN = """照らす設計 doc (1 行に `<置き場> | <索引> | <照らすレビュー対象>`。索引があれば索引から読む file を決め、空なら置き場の全体を読む):
{design_docs}

レビュー対象に当てたパッチの diff (2 つの検査は、この diff が変えた箇所にだけ当てる):
{patch_diff}

各設計 doc を、その行に並べたレビュー対象にだけ照らし、reference の principle-design-conformance の 2 つの検査を当てる。その指摘は category を `conformance` か `design drift` にし、evidence に設計 doc の行を file:line で添える。
"""
STRUCTURE = """参照する reference (絶対 path):
- 共通: {architecture}
- 判断規則: {structure}
- 共通: {models}

レビュー対象 (今回書いた file、絶対 path):
{targets}

harness 全体一覧 (path + component):
{inventory}
"""


class BuildError(RuntimeError):
    pass


def _abs(path: str) -> Path:
    """prompt に出す path。symlink は解かない (渡された表記のまま読ませる)。"""
    return Path(os.path.abspath(Path(path).expanduser()))


def _key(path: Path) -> Path:
    """控えを引く鍵。snapshot-changes.py の take と同じく realpath にそろえる (git の root は symlink を解いた path)。"""
    return Path(os.path.realpath(path))


def _git(root: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)


def _bullets(items: list) -> str:
    return "\n".join(f"- {item}" for item in items)


def _require(paths: list[Path]) -> None:
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise BuildError(f"参照する path が無い: {', '.join(missing)}")


def _before(snapshot: dict, target: Path) -> str:
    """控えた時点の target の中身。控えに無ければ控えた HEAD の中身 (HEAD にも無ければ空)。"""
    key = _key(target)
    if str(key) in snapshot["kept"]:
        return snapshot["kept"][str(key)] or ""
    root = snapshot.get("repo")
    if root and key.is_relative_to(root):
        rel = key.relative_to(root).as_posix()
        if rel in snapshot["status"]:
            raise BuildError(f"控えの時点で未 commit の変更があり、中身を控えていない: {target} (take の --keep に渡す)")
        if _git(root, "cat-file", "-e", f"{snapshot['head']}^{{commit}}").returncode != 0:
            raise BuildError(f"控えた HEAD を引けない: {snapshot['head']} ({root})")
        if _git(root, "cat-file", "-e", f"{snapshot['head']}:{rel}").returncode != 0:
            return ""  # 控えた HEAD に無い file (外出しで新しく作る file)
        proc = _git(root, "show", f"{snapshot['head']}:{rel}")
        if proc.returncode != 0:
            raise BuildError(f"git show {snapshot['head']}:{rel} が失敗した: {proc.stderr.strip()}")
        return proc.stdout
    raise BuildError(f"控えに中身が無い: {target} (take の --keep に渡す)")


def patch_diff(snapshot: dict, targets: list[Path]) -> str:
    hunks: list[str] = []
    for target in targets:
        after = target.read_text(encoding="utf-8") if target.is_file() else ""
        hunks += difflib.unified_diff(
            _before(snapshot, target).splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a{target}", tofile=f"b{target}",
        )
    if not hunks:
        raise BuildError("控えからの差分が無い (控えを最初のパッチの前に取ったかを確かめる)")
    return "".join(h if h.endswith("\n") else h + "\n" for h in hunks).rstrip("\n")


def build_verify(args: argparse.Namespace) -> str:
    refs = [REFS / "components" / f"{args.component}.md"]
    if args.agent_ref:
        refs.append(REFS / "components" / "agent.md")
    refs += [REFS / "writing.md", *map(_abs, args.model_ref)]
    design_rows = [] if args.design_later else args.design_doc
    if design_rows:
        refs.append(CONFORMANCE_LEAF)
    _require(refs)
    _require([_abs(p) for place, index, _ in design_rows for p in (place, index) if p])
    targets = [_abs(t) for t in args.target]
    parts = [VERIFY_HEAD.format(component=args.component, refs=_bullets(refs), targets=_bullets(targets))]
    ranges = []
    for given in args.range:
        path, sep, lines = given.partition("=")
        if not sep or not RANGE.match(lines.strip()) or _abs(path) not in targets:
            raise BuildError(f"--range は --target に渡した file の <path>=<行範囲> で渡す (空白を含むなら引用する): {given}")
        ranges.append(f"{_abs(path)}: {lines.strip()}")
    if ranges:
        parts.append(SCOPE_LINE.format(ranges=" / ".join(ranges)) + "\n")
    if design_rows:
        if not args.snapshot:
            raise BuildError("設計 doc の行を並べるときは --snapshot を渡す (パッチの diff を埋める)")
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        rows = []
        for place, index, on in design_rows:
            applied = [_abs(t.strip()) for t in on.split(",") if t.strip()]
            if not applied or any(t not in targets for t in applied):
                raise BuildError(f"--design-doc の照らす対象は --target に渡した file を , 区切りで並べる: {on}")
            rows.append(f"{_abs(place)} | {_abs(index) if index else ''} | {', '.join(map(str, applied))}")
        _reject_unexpanded("\n".join(parts + rows))
        parts.append(VERIFY_DESIGN.format(design_docs="\n".join(rows), patch_diff=patch_diff(snapshot, targets)))
        return "\n".join(parts)
    _reject_unexpanded("\n".join(parts))
    return "\n".join(parts)


def _reject_unexpanded(text: str) -> None:
    """template・reference・対象・設計 doc の行に `${` が残っていれば止める。diff と一覧は file の中身なので見ない。"""
    if "${" in text:
        raise BuildError("prompt に `${` が残っている (path は展開済みの絶対 path で渡す)")


def build_structure(args: argparse.Namespace) -> str:
    refs = {name: REFS / f"{name}.md" for name in ("architecture", "structure", "models")}
    _require(list(refs.values()))
    targets = [_abs(t) for t in args.target]
    proc = subprocess.run(
        [sys.executable, str(INVENTORY_SCRIPT), "--with-tracked", *args.target], capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise BuildError((proc.stderr.strip().splitlines() or ["一覧の script が stderr なしで失敗した"])[0])
    _reject_unexpanded(STRUCTURE.format(**refs, targets=_bullets(targets), inventory=""))
    return STRUCTURE.format(**refs, targets=_bullets(targets), inventory=proc.stdout.rstrip("\n"))


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)
    v = sub.add_parser("verify", help="検証 subagent の prompt")
    v.add_argument("--component", required=True, choices=["skill", "claude-md", "rules", "hook-inject", "agent"])
    v.add_argument("--target", nargs="+", required=True, help="レビュー対象")
    v.add_argument("--snapshot", help="snapshot-changes.py take の控え (設計 doc の行を並べるときに要る)")
    v.add_argument("--model-ref", nargs="+", default=[], help="対象モデルの差分 reference")
    v.add_argument("--agent-ref", action="store_true", help="agent の reference も並べる (subagent を起こす skill)")
    v.add_argument("--range", nargs="+", default=[], help="<path>=<行範囲>。全行を診断した file には渡さない")
    design = v.add_mutually_exclusive_group()
    design.add_argument("--design-later", action="store_true", help="呼び出し元が `設計 doc は後段` と宣言した")
    design.add_argument("--design-doc", nargs=3, action="append", default=[], metavar=("PLACE", "INDEX", "TARGETS"))
    s = sub.add_parser("structure", help="構造 reviewer の prompt")
    s.add_argument("--target", nargs="+", required=True, help="レビュー対象 (今回書いた file)")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        prompt = build_verify(args) if args.mode == "verify" else build_structure(args)
    except (BuildError, OSError, ValueError) as exc:
        print(f"build-review-prompts: {exc}", file=sys.stderr)
        return 1
    print(prompt.rstrip("\n"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
