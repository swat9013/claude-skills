#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PlantUML source を検査し、兄弟の <stem>.svg へ描画する。

設計ドキュメントの図の正本 (.puml) は、壊れても Markdown の diff からは分からない。
壊れ方は 2 層あり、それぞれ別の検査が要る:

1. 構文エラー — `plantuml --check-syntax` の exit code で判定する。`-tsvg` / `-tpng` は
   syntax error でもエラー画像を生成して exit 0 を返しうるので、生成物の有無を成功判定に
   使わない
2. `{field}` 欠落 — PlantUML は `()` の有無でフィールド / メソッドを判定するため、
   `issueRef : 中立ref (gh#386)` のような属性は `hide methods` 系の設定下でメソッド扱い
   され、画像から無言で消える (構文検査は exit 0 のまま)。class の属性行が `{field}` で
   始まることを行の形として検査する

描画の成否も exit code では見ず、stdout が SVG の root (`<svg` / `</svg>`) を持つかで
判定する (`-tpng` が exit 0 のまま 0 byte を返す実例がある)。

描画結果には source / render の sha256 指紋を `</svg>` の内側へ埋める。指紋は「この SVG は
どの source から描かれ、その後書き換えられていないか」を後段の鮮度検査が突合するための
もので、鮮度検査 (pre-commit gate 等) を持つリポジトリではその gate の生成側が正本 —
本 script は gate を持たない配布先での描画を受け持ち、出力の形は gate 検査と互換にする。

用法:
    render-puml.py <file.puml | dir> [...]           # 検査 + 描画 (兄弟 <stem>.svg を上書き)
    render-puml.py --check <file.puml | dir> [...]   # 検査のみ (書き込まない)

Exit 0 = 違反なし (描画モードなら全件描画成功) / 1 = 違反あり or 検査・描画不能 /
2 = 引数エラー。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

PLANTUML = "plantuml"

# plantuml の JVM が graphviz ごとハングしても呼び出し元を止めっぱなしにしない。
CHECK_TIMEOUT_SEC = 120

# `class WorkOrder <<作業指示>> {` / `abstract class X {` の開始行。
CLASS_OPEN = re.compile(r"^\s*(?:abstract\s+)?class\s+\S.*\{\s*$")
# 属性行として認める形。`{field}` で始まること (`{static}` を足すなら `{field} {static}`)。
FIELD_MODIFIER = re.compile(r"^\{field\}")
# 区画の区切り (`--`) や空行・コメントは属性行ではない。
SEPARATOR = re.compile(r"^(?:-{2,}|={2,}|\.{2,}|_{2,})\s*(?:\S.*)?$")
# 複数行 note の開始 (`note top of X`) と終了。1 行 note (`note bottom of X : text`) は
# `:` を持つので開始とみなさない。
NOTE_BLOCK_OPEN = re.compile(r"^note\b(?![^:]*:)", re.IGNORECASE)
NOTE_BLOCK_CLOSE = re.compile(r"^end\s*note\b", re.IGNORECASE)
# 波括弧の外の属性宣言 `WorkOrder : {field} issueRef : ...` の左辺と右辺。
MEMBER_LINE = re.compile(r'^(?P<owner>[A-Za-z_][\w.]*|"[^"]+")\s*:\s*(?P<member>\S.*)$')
# 関連 (`A ..> B : label` 等) は属性宣言ではない。線を構成する字面で外す。
RELATION_TOKEN = re.compile(r"(--|\.\.|->|<-|<\||\|>|\*-|-\*|o-|-o|\+-|#-|x-|\}-|-\{)")
# 属性宣言の左辺に来ない予約語 (これで始まる行は別の要素)。
NON_MEMBER_KEYWORDS = frozenset(
    {
        "note", "end", "skinparam", "hide", "show", "package", "namespace", "class",
        "abstract", "interface", "enum", "object", "entity", "usecase", "actor",
        "rectangle", "component", "together", "title", "legend", "header", "footer",
        "caption", "scale", "left", "right", "top", "bottom", "url", "page", "state",
        "allow_mixing",
    }
)

SVG_ROOT_OPEN = "<svg"
SVG_ROOT_CLOSE = "</svg>"

# 描画結果に埋める 2 つの指紋。`</svg>` の内側へ置く — root の外に出すと、GitHub の
# sanitizer や SVG を読み書きするツールが黙って落としうる。source 側は「どの `.puml`
# から描かれたか」、render 側は「描いてから中身が変わっていないか」を名乗る。
FINGERPRINT_TEMPLATE = "<!--puml-source-sha256:{source}--><!--puml-render-sha256:{render}-->"

GUIDANCE = """\
直し方:
  - 構文エラー → 上のメッセージの行番号を直す。手元での再現:
      plantuml --check-syntax <file> ; echo $?
  - {field} 欠落 → その属性行の先頭に `{field} ` を足す。付けないと `()` を含む属性が
    画像から無言で消える (構文検査は exit 0 のまま通る)。
  - plantuml が無い → brew install plantuml"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("paths", nargs="+", help=".puml ファイルまたはそれを含むディレクトリ")
    p.add_argument("--check", action="store_true", help="検査のみ (svg を書かない)")
    return p.parse_args(argv)


def collect_puml_files(paths: list[str]) -> tuple[list[Path], list[str]]:
    """引数の path 群を .puml の一覧へ展開する。展開できない path は問題として返す。"""
    targets: list[Path] = []
    problems: list[str] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            found = sorted(path.rglob("*.puml"))
            if not found:
                problems.append(f"[puml not found] {path} — .puml が 1 件も無い")
            targets.extend(found)
        elif path.is_file() and path.suffix == ".puml":
            targets.append(path)
        else:
            problems.append(f"[puml not found] {path} — .puml ファイルでもディレクトリでもない")
    return targets, problems


def _member_outside_braces(line: str) -> str | None:
    """波括弧の外の属性宣言 `X : issueRef ...` なら属性部を返す。違うなら None."""
    if RELATION_TOKEN.search(line):
        return None
    match = MEMBER_LINE.match(line)
    if not match:
        return None
    if match.group("owner").split(".")[0].lower() in NON_MEMBER_KEYWORDS:
        return None
    return match.group("member")


def field_annotation_violations(path: Path, text: str) -> list[str]:
    """`{field}` で始まっていない class 属性宣言を返す。"""
    violations: list[str] = []
    in_class = False
    in_note = False
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("'"):
            continue
        if in_note:
            # note の本文は自由記述なので、`x : y` のような行が属性に見える。
            if NOTE_BLOCK_CLOSE.match(line):
                in_note = False
            continue
        if NOTE_BLOCK_OPEN.match(line):
            in_note = True
            continue
        if not in_class:
            if CLASS_OPEN.match(raw):
                in_class = True
                continue
            member = _member_outside_braces(line)
            if member is not None and not FIELD_MODIFIER.match(member):
                violations.append(
                    f"[puml field annotation] {path}:{lineno} — 属性宣言が "
                    f"{{field}} で始まっていない: {line!r}"
                )
            continue
        if line == "}":
            in_class = False
            continue
        if SEPARATOR.match(line):
            continue
        if not FIELD_MODIFIER.match(line):
            violations.append(
                f"[puml field annotation] {path}:{lineno} — class 本体の属性行が "
                f"{{field}} で始まっていない: {line!r}"
            )
    return violations


def check_syntax(path: Path) -> list[str]:
    """plantuml --check-syntax を exit code で判定する。"""
    try:
        completed = subprocess.run(
            [PLANTUML, "--check-syntax", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=CHECK_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return [
            f"[puml timeout] {path} — plantuml --check-syntax が {CHECK_TIMEOUT_SEC} 秒で"
            f"終わらなかった (JVM / graphviz のハングを疑う)"
        ]
    if completed.returncode == 0:
        return []
    detail = (completed.stdout + completed.stderr).strip() or "(plantuml が詳細を出力しなかった)"
    return [f"[puml syntax] {path} — plantuml --check-syntax が exit {completed.returncode}:\n{detail}"]


def fingerprinted(svg: str, source_text: str) -> str:
    """SVG の root の内側 (末尾 `</svg>` の直前) に 2 つの指紋を差し込む。

    render 側の指紋は挿入前の SVG 全文の sha256。鮮度検査側は指紋コメントを取り除いた
    本文で同じ値を再計算するので、挿入位置がどこでも突合が成立する。
    """
    head, close, tail = svg.rpartition(SVG_ROOT_CLOSE)
    if not close:
        # render_svg が root の形を確かめてからここへ渡すので、通常は到達しない。
        raise ValueError(f"SVG の root 終端 {SVG_ROOT_CLOSE} が無い")
    marker = FINGERPRINT_TEMPLATE.format(
        source=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        render=hashlib.sha256(svg.encode("utf-8")).hexdigest(),
    )
    return f"{head}{marker}{close}{tail}"


def svg_root_violations(path: Path, svg: str, returncode: int, stderr: str) -> list[str]:
    """描画結果が SVG の root を持つかで成否を判定する (exit code は証拠にならない)。"""
    if SVG_ROOT_OPEN in svg and SVG_ROOT_CLOSE in svg:
        return []
    detail = stderr.strip() or "(plantuml が詳細を出力しなかった)"
    return [
        f"[puml render failed] {path} — plantuml -tsvg が SVG を返さなかった "
        f"(exit {returncode}, stdout {len(svg)} 文字):\n{detail}"
    ]


def render_svg(path: Path, text: str) -> tuple[str | None, list[str]]:
    """`.puml` を SVG へ描く。先に構文を exit code で通してから描く。"""
    syntax_errors = check_syntax(path)
    if syntax_errors:
        return None, syntax_errors
    try:
        completed = subprocess.run(
            # `.puml` が非 ASCII を含むとき、locale 既定に委ねると Python 側は
            # UnicodeEncodeError、JVM 側は文字化けしたラベルを「形の正しい SVG」として
            # 返す。pipe の両端で UTF-8 を名指しする。
            [PLANTUML, "-charset", "UTF-8", "-tsvg", "-pipe"],
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=CHECK_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return None, [
            f"[puml render timeout] {path} — plantuml -tsvg が {CHECK_TIMEOUT_SEC} 秒で"
            f"終わらなかった (JVM / graphviz のハングを疑う)"
        ]
    problems = svg_root_violations(path, completed.stdout, completed.returncode, completed.stderr)
    if problems:
        return None, problems
    return fingerprinted(completed.stdout, text), []


def main(args: argparse.Namespace) -> int:
    targets, problems = collect_puml_files(args.paths)

    if targets and shutil.which(PLANTUML) is None:
        problems.append(
            f"plantuml が見つからない。検査対象の .puml が {len(targets)} 件あるので検査不能: "
            f"{', '.join(str(p) for p in targets)}"
        )
        targets = []

    for path in targets:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"[puml unreadable] {path}: {exc}")
            continue
        field_problems = field_annotation_violations(path, text)
        problems.extend(field_problems)
        if args.check:
            problems.extend(check_syntax(path))
            continue
        svg, render_problems = render_svg(path, text)
        if svg is None:
            problems.extend(render_problems)
            continue
        destination = path.with_suffix(".svg")
        try:
            destination.write_text(svg, encoding="utf-8")
        except OSError as exc:
            problems.append(f"[puml render unwritable] {destination}: {exc}")
            continue
        print(f"rendered: {destination}")

    for line in problems:
        print(line, file=sys.stderr)
    if problems:
        print(GUIDANCE, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)
