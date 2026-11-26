#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""対象 repo の `.claude/rules/**/*.md` のうち、frontmatter の `paths` が渡した path に当たるものを列挙する。

`paths` を持たない rules は全 path に当たる (Claude Code が常時読み込む rules)。照合は repo root 相対の glob
(`**` は dir をまたぎ、`*` と `?` は `/` をまたがず、`{a,b}` は選択)。`paths` の値は、カンマ区切りの文字列・
`[a, b]` の inline の列・`- a` の block の列のどれでも読む。

stdout は `rules` (当たる rules の repo root 相対 path。名前順) と `notes` (読めなかった rules) を持つ JSON 1 つ。

invariant:

1. `paths` を持たない rules は、渡した path の有無によらず当たる
2. frontmatter か `paths` の値を読めない rules は、落とさず当たるものとして返し、`notes` に `paths を読めず:` で残す
   (読み落とすより、読む規約が 1 つ増えるほうを取る)
3. `.claude/rules/` が無い repo は空の `rules` を返す (rules を置かないのは正常)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RULES_DIR = Path(".claude") / "rules"
NOTE_UNREADABLE = "paths を読めず:"


def glob_regex(pattern: str) -> re.Pattern[str]:
    """repo root 相対の glob を、path 全体に当てる正規表現にする (`fullmatch` で使う)。"""
    parts = []
    for token in re.split(r"(\*\*/|\*\*|\*|\?|\{[^}]*\})", pattern):
        if token == "**/":
            parts.append("(?:.*/)?")
        elif token == "**":
            parts.append(".*")
        elif token == "*":
            parts.append("[^/]*")
        elif token == "?":
            parts.append("[^/]")
        elif token.startswith("{") and token.endswith("}"):
            parts.append("(?:" + "|".join(re.escape(a) for a in token[1:-1].split(",")) + ")")
        else:
            parts.append(re.escape(token))
    return re.compile("".join(parts))


def split_globs(value: str) -> list[str]:
    """カンマ区切りの glob の列を切る。`{a,b}` の中のカンマでは切らない。"""
    return [g.strip().strip("`'\"") for g in re.split(r",(?![^{]*\})", value) if g.strip().strip("`'\"")]


def _strip_comment(value: str) -> str:
    """YAML の行末 comment (引用符の外の ` #` から後ろ) を外す。"""
    quote = None
    for i, ch in enumerate(value):
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"" and value[:i].rstrip()[-1:] in ("", "[", ","):
            quote = ch  # YAML の引用符は scalar の頭でだけ働く (`o'reilly` の `'` は文字)
        elif ch == "#" and (i == 0 or value[i - 1] in " \t"):
            return value[:i].rstrip()
    return value


class Unreadable(Exception):
    pass


def _frontmatter(text: str) -> list[str] | None:
    """frontmatter の行。frontmatter が無ければ None。閉じていなければ Unreadable。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return lines[1:i]
    raise Unreadable("frontmatter が閉じていない")


def rule_globs(text: str) -> list[str] | None:
    """rules の本文から `paths` の glob の列。`paths` を持たなければ None (全 path に当たる)。"""
    front = _frontmatter(text)
    if front is None:
        return None
    for i, line in enumerate(front):
        m = re.match(r"^paths\s*:\s*(.*)$", line)
        if m is None:
            continue
        value = _strip_comment(m[1]).strip()
        if value.startswith("["):
            if not value.endswith("]"):
                raise Unreadable("paths の inline の列が閉じていない")
            globs = split_globs(value[1:-1])
        elif value:
            globs = split_globs(value)
        else:
            items = []
            for item in front[i + 1:]:
                if not item.strip() or item.lstrip().startswith("#"):
                    continue  # 列の中の空行と comment 行
                bullet = re.match(r"^\s*-(?:\s+(.*)|)$", item)  # YAML は indent の無い `- a` も列に読む
                if bullet is None:
                    break
                items.append(_strip_comment(bullet[1] or ""))
            globs = [g for item in items for g in split_globs(item)]
        if not globs:
            raise Unreadable("paths の値が空")
        return globs
    return None


def matching_rules(repo_root: Path, paths: list[str]) -> tuple[list[str], list[str]]:
    """当たる rules の repo root 相対 path と、読めなかった rules の注記。"""
    rules_dir = repo_root / RULES_DIR
    if not rules_dir.is_dir():
        return [], []
    matched, notes = [], []
    for rule in sorted(rules_dir.rglob("*.md")):
        rel = rule.relative_to(repo_root).as_posix()
        try:
            globs = rule_globs(rule.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, Unreadable) as e:
            notes.append(f"{NOTE_UNREADABLE} {rel} ({e}) — 全 path に当たるとして返した")
            matched.append(rel)
            continue
        if globs is None or any(glob_regex(g).fullmatch(p) for g in globs for p in paths):
            matched.append(rel)
    return matched, notes


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="対象 repo の root (省略時は cwd)")
    ap.add_argument("paths", nargs="*", help="照合する path (repo root 相対)")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"--root {args.root} が dir でない", file=sys.stderr)
        return 1
    rules, notes = matching_rules(root, args.paths)
    print(json.dumps({"rules": rules, "notes": notes}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
