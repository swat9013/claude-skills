#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""指示文の file から対象モデルと、Read する references/models/ の reference を決めて tab 区切りで stdout へ出す。

    resolve-target-model.py <指示文の file>

- frontmatter に `model:` が無い: references/models.md「文面の基準モデル」の `**現在は <モデル>**` を対象モデルにする
- `model:` が完全な ID (`claude-<系統>-<版>[-<小版>][-<日付>]`): ID からモデル名 (`claude-<系統>-<版>-<小版>` → `<系統> <版>.<小版>`。小版が無ければ `<系統> <版>`) を作る
- それ以外 (`opus` などの alias・`inherit`): 解決先は機械で決まらないので `判断` 行を出し、reference を出さない

モデル名から references/writing.md「モデル別の差分」の表の行を引き、行の link を reference として出す。行の 1 列目は
括弧の前の部分がモデル名か系統名 (`Haiku (…)`) と一致する行を当てる。表に行が無いときも `判断` 行を出す。

出力の行:

- `対象モデル\t<モデル名>\t<由来>`
- `reference\t<絶対 path>` (行の link ごとに 1 行)
- `注記\t<link の外の文>` (行の 2 列目に link のほかの文があるとき)
- `判断\t<本文へ返す判断>`: alias・表に行が無い
- `注記\t` で始まる最後の行: Agent tool の `model` 引数による指名は file から読めない旨

invariant:

1. 指示文の file か、references/models.md・writing.md が読めなければ stderr へ 1 行出して exit 1
2. models.md に基準モデルの文か、writing.md に「モデル別の差分」の表が見つからなければ stderr へ 1 行出して exit 1
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REFS = Path(os.path.abspath(__file__)).parent.parent / "references"
BASE_MODEL = re.compile(r"\*\*現在は ([A-Za-z]+ [\d.]+)\*\*")
FULL_ID = re.compile(r"^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
AGENT_TOOL_NOTE = "注記\tAgent tool の `model` 引数で指名する subagent への依頼文は file から読めないので、指名したモデルで決め直す"


class ResolveError(RuntimeError):
    pass


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ResolveError(f"{path} を読めない: {exc}") from exc


def declared_model(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        key, sep, value = line.partition(":")
        # 字下げした行 (入れ子の map・複数行の値) は最上位の key ではない
        if sep and not line[:1].isspace() and key.rstrip() == "model":
            return value.split("#")[0].strip().strip("\"'") or None
    return None


def diff_table() -> list[tuple[str, str]]:
    """writing.md「モデル別の差分」の表の (1 列目, 2 列目)。"""
    lines = _read(REFS / "writing.md").splitlines()
    try:
        start = lines.index("## モデル別の差分")
    except ValueError as exc:
        raise ResolveError("writing.md に「## モデル別の差分」の節が無い") from exc
    rows = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if len(cells) >= 2 and cells[0] != "対象モデル" and not set(cells[0]) <= set("-: "):
            rows.append((cells[0], cells[1]))
    if not rows:
        raise ResolveError("writing.md の「モデル別の差分」に表の行が無い")
    return rows


def resolve(path: Path) -> list[str]:
    declared = declared_model(_read(path))
    if declared is None:
        base = BASE_MODEL.search(_read(REFS / "models.md"))
        if not base:
            raise ResolveError("models.md に「**現在は <モデル>**」の文が無い")
        name, origin = base.group(1), "宣言なし (文面の基準モデル)"
    elif match := FULL_ID.match(declared):
        family, major, minor = match.groups()
        name = f"{family.capitalize()} {major}" + (f".{minor}" if minor else "")
        origin = f"frontmatter の model: {declared}"
    else:
        return [
            f"判断\tmodel: {declared} は alias か ID でない値で、解決先のモデルは機械で決まらない。解決先を確かめて対象モデルを決める",
            AGENT_TOOL_NOTE,
        ]
    out = [f"対象モデル\t{name}\t{origin}"]
    for first, second in diff_table():
        if first.split(" (")[0] in (name, name.split(" ")[0]):
            out += [f"reference\t{os.path.normpath(REFS / target)}" for target in LINK.findall(second)]
            rest = LINK.sub("", second).replace("+", "").strip()
            if rest:
                out.append(f"注記\t{rest}")
            break
    else:
        out.append(f"判断\twriting.md の「モデル別の差分」の表に {name} の行が無い。表に行を足すか、近い系統の行を選ぶ")
    return out + [AGENT_TOOL_NOTE]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 1
    try:
        print("\n".join(resolve(Path(argv[0]).expanduser())))
    except ResolveError as exc:
        print(f"resolve-target-model: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
