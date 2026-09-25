#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""UI 設計ディレクトリの成果物間の相互参照を検査する。

画面・component・トークンは 3 つの file に分かれて書かれるので、片方だけ直して他方が
取り残される壊れ方をする。本 script は各 file を機械的に読み、参照が閉じているかを
突き合わせる:

1. **画面 ⇔ 遷移** — `screens.md` の画面一覧と `navigation.puml` の状態が一致する
   (片側にしか無い画面は違反)
2. **component ⇔ 画面** — `components.md` の出現画面が `screens.md` に実在する。
   出現画面が空の component は違反 (どの画面にも現れない部品は設計から落とす)
3. **state 網羅** — 種別を名乗った component が、その種別の必須 state を全部列挙する
4. **token 未定義参照** — `screens.md` / `components.md` が `` `--name` `` で名指した
   token が `tokens.md` に現れる
5. **省略の宣言** — 成果物を省いたなら README.md に `- 省略: <file> — <理由>` がある

様式 (表の列・見出し・ID の文字種) の正本は `references/artifacts.md`。本 script は
そこで決めた形だけを読む。

用法:
    check-ui-design.py <design-dir>

Exit 0 = 違反なし / 1 = 違反あり or 検査不能 / 2 = 引数エラー。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 省略可能な成果物 (README.md は入口なので省略できない)
OPTIONAL_ARTIFACTS = ("screens.md", "navigation.puml", "components.md", "tokens.md")

# 種別ごとの必須 state。`-` (種別なし) は要求しない。
# 正本は SKILL.md の「必須 state」表で、ここはその機械可読な写し。
REQUIRED_STATES: dict[str, frozenset[str]] = {
    "Button": frozenset({"default", "hover", "focus-visible", "active", "disabled", "loading"}),
    "Input": frozenset({"default", "hover", "focus", "error", "disabled", "read-only"}),
    "Link": frozenset({"default", "hover", "focus-visible", "visited"}),
    "Checkbox": frozenset({"default", "hover", "focus-visible", "checked", "indeterminate", "disabled"}),
    "Radio": frozenset({"default", "hover", "focus-visible", "checked", "indeterminate", "disabled"}),
    "Modal": frozenset({"closed", "opening", "open", "closing"}),
    "Alert": frozenset({"info", "success", "warning", "error"}),
}

SCREEN_TABLE_HEADING = re.compile(r"^##\s+画面一覧\s*$")
COMPONENT_TABLE_HEADING = re.compile(r"^##\s+component\s+一覧\s*$")
HEADING = re.compile(r"^#{1,6}\s")
TABLE_ROW = re.compile(r"^\s*\|(?P<body>.+)\|\s*$")
SEPARATOR_ROW = re.compile(r"^[\s|:-]+$")
SCREEN_ID = re.compile(r"^[A-Za-z0-9_.]+$")
# `' uncovered:` 等のコメント行を除いた遷移行 / 状態宣言行 (check-statechart.py と同じ範囲)
TRANSITION = re.compile(
    r"^\s*(?P<src>\[\*\]|[\w.]+)\s*"
    r"-+(?:up|down|left|right|u|d|l|r)?-*>\s*"
    r"(?P<dst>\[\*\]|[\w.]+)\s*"
    r"(?::\s*(?P<label>.+?))?\s*$"
)
STATE_DECL = re.compile(r'^\s*state\s+(?:"[^"]*"\s+as\s+)?(?P<name>[\w.]+)\s*$')
# 本文中の token 参照 `--name` (inline code の中だけを対象にする)
TOKEN_REF = re.compile(r"`(--[A-Za-z0-9_-]+)`")
TOKEN_DEF = re.compile(r"--[A-Za-z0-9_-]+")
# front matter (先頭の `---` で挟まれた区画)。DESIGN.md 形式の機械可読な値であって
# CSS 変数名との対応が自動で取れないため、token の定義とはみなさない (artifacts.md)。
FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
# 表のセル内の区切り。SKILL.md の必須 state 表が `/` 区切りなので、写して貼った
# `default / hover` も読めるようにする (`,` だけだと全 state が欠落と報告される)。
CELL_SEPARATOR = re.compile(r"\s*[,/]\s*")
OMISSION_DECL = re.compile(r"^\s*-\s*省略:\s*(?P<file>\S+)\s+[—-]\s+(?P<reason>\S.*)$")
INITIAL = "[*]"


def _read(path: Path) -> str | None:
    """読めれば本文、無ければ None。読めるが壊れている場合は空文字ではなく本文を返す."""
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _table_after(text: str, heading: re.Pattern[str]) -> list[list[str]]:
    """見出しの直後に現れる最初の表の、区切り行を除いた行をセル列で返す."""
    rows: list[list[str]] = []
    in_section = False
    seen_table = False
    for line in text.splitlines():
        if heading.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        if HEADING.match(line):
            break
        m = TABLE_ROW.match(line)
        if not m:
            # 表が始まった後の空行以外は表の終わり
            if seen_table and line.strip():
                break
            continue
        seen_table = True
        if SEPARATOR_ROW.match(m.group("body")):
            continue
        rows.append([cell.strip() for cell in m.group("body").split("|")])
    return rows[1:] if rows else []  # 先頭行は見出し行


def _screens(text: str) -> tuple[list[str], list[str]]:
    """画面一覧表から画面 ID を取り出す。(ID 列, 違反) を返す."""
    errors: list[str] = []
    ids: list[str] = []
    for row in _table_after(text, SCREEN_TABLE_HEADING):
        if not row or not row[0]:
            continue
        screen_id = row[0]
        if not SCREEN_ID.match(screen_id):
            errors.append(
                f"[screen id invalid] screens.md: '{screen_id}' は画面 ID に使えない "
                f"(英数字・_・. のみ。日本語は画面名の列へ)"
            )
            continue
        if screen_id in ids:
            errors.append(f"[screen duplicated] screens.md: 画面 ID '{screen_id}' が重複")
            continue
        ids.append(screen_id)
    return ids, errors


def _puml_states(text: str) -> set[str]:
    states: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("'"):
            continue
        m = STATE_DECL.match(line)
        if m:
            states.add(m.group("name"))
            continue
        m = TRANSITION.match(line)
        if m:
            for name in (m.group("src"), m.group("dst")):
                if name != INITIAL:
                    states.add(name)
    return states


def _components(text: str) -> tuple[list[tuple[str, str, list[str], list[str]]], list[str]]:
    """component 一覧表を (名前, 種別, 出現画面, state) の列にする。(行, 違反) を返す."""
    errors: list[str] = []
    rows: list[tuple[str, str, list[str], list[str]]] = []
    for row in _table_after(text, COMPONENT_TABLE_HEADING):
        if not row or not row[0]:
            continue
        if len(row) < 4:
            errors.append(
                f"[component row malformed] components.md: '{row[0]}' の行は "
                f"component / 種別 / 出現画面 / state の 4 列が要る"
            )
            continue
        name, kind = row[0], row[1]
        screens = [s for s in CELL_SEPARATOR.split(row[2].strip()) if s]
        states = [s for s in CELL_SEPARATOR.split(row[3].strip()) if s]
        rows.append((name, kind, screens, states))
    return rows, errors


def _token_refs(text: str) -> set[str]:
    return set(TOKEN_REF.findall(text))


def _omissions(text: str) -> set[str]:
    return {m.group("file") for line in text.splitlines() if (m := OMISSION_DECL.match(line))}


def check(design_dir: Path) -> list[str]:
    errors: list[str] = []

    if not design_dir.is_dir():
        return [f"[dir missing] {design_dir} がディレクトリとして読めない"]

    readme = _read(design_dir / "README.md")
    if readme is None:
        return [f"[readme missing] {design_dir}/README.md が無い (索引は省略できない)"]
    omitted = _omissions(readme)

    sources: dict[str, str] = {}
    for name in OPTIONAL_ARTIFACTS:
        text = _read(design_dir / name)
        if text is None:
            if name not in omitted:
                errors.append(
                    f"[omission undeclared] {name} が無い。省略するなら README.md に "
                    f"'- 省略: {name} — <理由>' を書く"
                )
            continue
        sources[name] = text

    screen_ids: list[str] = []
    if "screens.md" in sources:
        screen_ids, screen_errors = _screens(sources["screens.md"])
        errors.extend(screen_errors)
        if not screen_ids:
            errors.append(
                "[screens empty] screens.md に '## 画面一覧' の表が無い、または行が 0 件"
            )

    # 1. 画面 ⇔ 遷移
    if "navigation.puml" in sources and "screens.md" in sources:
        states = _puml_states(sources["navigation.puml"])
        for screen_id in screen_ids:
            if screen_id not in states:
                errors.append(
                    f"[screen not in navigation] 画面 '{screen_id}' が "
                    f"navigation.puml に現れない"
                )
        for state in sorted(states - set(screen_ids)):
            errors.append(
                f"[state not in screens] navigation.puml の '{state}' が "
                f"screens.md の画面一覧に無い"
            )

    # 2. component ⇔ 画面 / 3. state 網羅
    if "components.md" in sources:
        components, component_errors = _components(sources["components.md"])
        errors.extend(component_errors)
        for name, kind, screens, states in components:
            if not screens:
                errors.append(
                    f"[component orphan] component '{name}' の出現画面が空 "
                    f"(どの画面にも現れない部品は設計から落とす)"
                )
            if "screens.md" in sources:
                for screen_id in screens:
                    if screen_id not in screen_ids:
                        errors.append(
                            f"[component screen unknown] component '{name}' が指す画面 "
                            f"'{screen_id}' が screens.md に無い"
                        )
            # 種別は `Checkbox / Radio` のように併記されうる (SKILL.md の表がその形)。
            kinds = [k for k in CELL_SEPARATOR.split(kind.strip()) if k and k != "-"]
            unknown = [k for k in kinds if k not in REQUIRED_STATES]
            if unknown:
                errors.append(
                    f"[component kind unknown] component '{name}' の種別 "
                    f"'{', '.join(unknown)}' は未知 "
                    f"(既知: {', '.join(sorted(REQUIRED_STATES))}、無しは '-')"
                )
                continue
            if not kinds:
                continue
            required = frozenset().union(*(REQUIRED_STATES[k] for k in kinds))
            missing = required - set(states)
            if missing:
                errors.append(
                    f"[state missing] component '{name}' ({kind}) に必須 state が無い: "
                    f"{', '.join(sorted(missing))}"
                )

    # 4. token 未定義参照
    if "tokens.md" in sources:
        body = FRONT_MATTER.sub("", sources["tokens.md"])
        defined = set(TOKEN_DEF.findall(body))
        for source_name in ("screens.md", "components.md"):
            if source_name not in sources:
                continue
            for ref in sorted(_token_refs(sources[source_name])):
                if ref not in defined:
                    errors.append(
                        f"[token undefined] {source_name} が参照する '{ref}' が "
                        f"tokens.md に無い"
                    )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="UI 設計ディレクトリの成果物間の相互参照を検査する",
        epilog="様式の正本は references/artifacts.md",
    )
    parser.add_argument("design_dir", type=Path, help="docs/design/<name>/ のパス")
    args = parser.parse_args(argv)

    errors = check(args.design_dir)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(f"\n{len(errors)} 件の違反", file=sys.stderr)
        return 1
    print(f"OK: {args.design_dir} の相互参照は閉じている")
    return 0


if __name__ == "__main__":
    sys.exit(main())
