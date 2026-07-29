#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""inventory-claude-md の観測 script (observation JSON 生成)。

project 範囲の CLAUDE.md 系 (root `CLAUDE.md` + サブディレクトリ CLAUDE.md +
`.claude/rules/*.md`) を **静的観測** し、以下 3 項目を含む observation JSON を
出力する。

- 参照先実在性 (link_targets): markdown link と `@import` 展開先の実在チェック。
  fail-safe = path として復元可能なもののみ検査。URL / 動的展開文字列は check_mode
  ラベルを付けて null 判定。
- @import 展開 (imports): `@path/to/file` 参照の解決先とその存在。棚卸し
  boundary を確定させるための決定的観測。
- section 物理計測 (sections): markdown 見出し階層と各 section の行数。context
  提供のみ、閾値 enforce はしない。

原則 (map #209 / #214): **観測・集計は決定的に、判断は人間に、LLM は文章の具体化
のみ**。script は bucket (keep-inline / move-to-path-scoped / move-to-skill /
move-to-lint / delete / merge) を割り当てない。bucket 判定は SKILL.md 手順の LLM
段階で observation JSON を読んでから行う (循環依存の回避)。

出力: --output-dir に observation-<timestamp>.json を書き、path を stdout に
print する。Markdown レポートは LLM 段階の成果物で、本 script は生成しない。

汎用スキル制約: 依存は Claude Code 標準ファイルのみ (`<repo>/CLAUDE.md` /
`<repo>/.claude/rules/*.md`)。global `~/.claude/CLAUDE.md` は読みも書きもしない
(仕様上除外)。`#rule` buffer への依存は ADR 0018 で撤去済み。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

# --- 定数 --------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = Path("/tmp/inventory-claude-md")

# markdown link: [text](target)
MD_LINK_RE = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)")

# `@path` import: 行頭 or 空白直後の @followed-by-non-space (@ が @-mention でなく
# import と読める形)。安全側に「行頭または空白後、拡張子ありのもの」に絞る。
# 例: `@.claude/rules/foo.md` / `@docs/adr/0001.md`
IMPORT_RE = re.compile(r"(?:^|(?<=\s))(?P<expr>@(?P<path>[.\w][\w./\-]*\.[a-zA-Z0-9]+))")

# markdown 見出し
HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*$")

# rules frontmatter の paths: 値抽出 (簡易 — 単行 or 単純カンマ列)
PATHS_FRONTMATTER_RE = re.compile(
    r"^paths\s*:\s*(?P<value>.+?)\s*$", re.MULTILINE
)

# URL 判定
URL_RE = re.compile(r"^(https?|ftp|mailto):", re.IGNORECASE)

# 動的展開文字列 (dollar var / パイプ / スペース含み等) を link_target として除外
DYNAMIC_MARKERS = ("$", "{", "}", "|", "`", " ")


# --- 純関数 ------------------------------------------------------------------


def classify_link_target(target: str) -> str:
    """link target を check_mode に分類する。

    - url: http(s):// / ftp: / mailto: など
    - anchor: 単なる # 以降のアンカー
    - absolute: /path 始まり
    - dynamic: env 展開や template 変数を含むもの (fail-safe で存在検査しない)
    - relative-path: それ以外 (repo-root から解決)
    """
    if not target:
        return "empty"
    if URL_RE.match(target):
        return "url"
    if target.startswith("#"):
        return "anchor"
    if any(m in target for m in DYNAMIC_MARKERS):
        return "dynamic"
    if target.startswith("/"):
        return "absolute"
    return "relative-path"


def strip_anchor(target: str) -> str:
    """`path.md#section` → `path.md` (存在検査は path 部のみ)."""
    idx = target.find("#")
    return target[:idx] if idx > 0 else target


def parse_headings(lines: list[str]) -> list[dict[str, Any]]:
    """markdown 見出し行を (level, line, text) の list で返す (1-indexed line)."""
    out: list[dict[str, Any]] = []
    in_code_block = False
    for i, raw in enumerate(lines, start=1):
        # フェンスコードブロック検出 (```)
        if raw.lstrip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        m = HEADING_RE.match(raw)
        if not m:
            continue
        out.append({
            "level": len(m.group("hashes")),
            "line": i,
            "text": m.group("text"),
        })
    return out


def sections_from_headings(
    headings: list[dict[str, Any]], total_lines: int
) -> list[dict[str, Any]]:
    """見出しから section 境界を計算する。

    各 section = 対応する見出しから次の同等以上 level の見出しまで (排他)。
    最深部は総行数まで。
    """
    if not headings:
        return []
    sections: list[dict[str, Any]] = []
    for idx, h in enumerate(headings):
        end_line = total_lines
        for later in headings[idx + 1:]:
            if later["level"] <= h["level"]:
                end_line = later["line"] - 1
                break
        sections.append({
            "heading_level": h["level"],
            "heading_text": h["text"],
            "start_line": h["line"],
            "end_line": end_line,
            "line_count": max(0, end_line - h["line"] + 1),
        })
    return sections


def extract_link_targets(
    source_path: str, lines: list[str]
) -> list[dict[str, Any]]:
    """markdown link `[label](target)` を抜き出し、path 種別を分類する。

    存在検査は呼び出し側 (repo_root 依存)。ここでは source location と
    check_mode まで確定する。
    """
    out: list[dict[str, Any]] = []
    in_code_block = False
    for i, raw in enumerate(lines, start=1):
        if raw.lstrip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        for m in MD_LINK_RE.finditer(raw):
            target = m.group("target")
            out.append({
                "source": f"{source_path}:{i}",
                "text": m.group("text"),
                "target": target,
                "check_mode": classify_link_target(target),
            })
    return out


def extract_imports(
    source_path: str, lines: list[str]
) -> list[dict[str, Any]]:
    """`@path/to/file` 形式の import を抽出する。

    Claude Code の公式 @import 構文。行頭 or 空白後、拡張子ありのものだけ拾う
    (fail-safe: SNS mention 等の false positive を避ける)。
    """
    out: list[dict[str, Any]] = []
    in_code_block = False
    for i, raw in enumerate(lines, start=1):
        if raw.lstrip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        for m in IMPORT_RE.finditer(raw):
            out.append({
                "source": f"{source_path}:{i}",
                "expression": m.group("expr"),
                "target": m.group("path"),
            })
    return out


def check_target_existence(
    entries: list[dict[str, Any]], repo_root: Path
) -> list[dict[str, Any]]:
    """link_targets / imports の存在検査を追加する (fail-safe)。

    - relative-path: repo_root からの相対で resolve、anchor 除去後の path を検査
    - absolute: そのまま存在検査 (repo 外可)
    - url / anchor / dynamic / empty: 検査しない (exists=None)
    """
    out: list[dict[str, Any]] = []
    for e in entries:
        entry = dict(e)
        mode = entry.get("check_mode")
        target = entry.get("target", "")
        if mode == "relative-path":
            resolved = repo_root / strip_anchor(target)
            entry["resolved_path"] = str(resolved.relative_to(repo_root)) \
                if _is_within(resolved, repo_root) else str(resolved)
            entry["exists"] = resolved.exists()
        elif mode == "absolute":
            resolved = Path(strip_anchor(target))
            entry["resolved_path"] = str(resolved)
            entry["exists"] = resolved.exists()
        else:
            # url / anchor / dynamic / empty / import (check_mode 無し) を含む
            entry["resolved_path"] = None
            entry["exists"] = None
        out.append(entry)
    return out


def check_import_existence(
    entries: list[dict[str, Any]], repo_root: Path
) -> list[dict[str, Any]]:
    """@import 展開先の存在検査。import は常に repo-relative として扱う。"""
    out: list[dict[str, Any]] = []
    for e in entries:
        entry = dict(e)
        target = entry.get("target", "")
        resolved = repo_root / target
        entry["resolved_path"] = str(resolved.relative_to(repo_root)) \
            if _is_within(resolved, repo_root) else str(resolved)
        entry["exists"] = resolved.exists()
        out.append(entry)
    return out


def _is_within(target: Path, base: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def read_file_safe(path: Path) -> tuple[str, list[str]]:
    """utf-8 で file を読む。読めなければ空を返す (fail-safe)。

    Returns (status, lines) where status ∈ {"present", "missing", "unreadable"}.
    """
    if not path.exists():
        return "missing", []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return "unreadable", []
    return "present", text.splitlines()


def observe_markdown_file(
    path: Path, repo_root: Path, label: str
) -> dict[str, Any]:
    """1 ファイルの決定的観測を組み立てる。"""
    status, lines = read_file_safe(path)
    if status != "present":
        return {
            "label": label,
            "path": str(path.relative_to(repo_root)) if _is_within(path, repo_root) else str(path),
            "status": status,
            "line_count": 0,
            "headings": [],
            "sections": [],
            "link_targets": [],
            "imports": [],
        }
    headings = parse_headings(lines)
    sections = sections_from_headings(headings, len(lines))
    link_source = str(path.relative_to(repo_root)) if _is_within(path, repo_root) else str(path)
    link_targets = check_target_existence(
        extract_link_targets(link_source, lines), repo_root
    )
    imports = check_import_existence(
        extract_imports(link_source, lines), repo_root
    )
    return {
        "label": label,
        "path": link_source,
        "status": "present",
        "line_count": len(lines),
        "headings": headings,
        "sections": sections,
        "link_targets": link_targets,
        "imports": imports,
    }


def observe_rules_file(path: Path, repo_root: Path) -> dict[str, Any]:
    """rules/*.md は markdown 観測 + frontmatter `paths:` 抽出。"""
    obs = observe_markdown_file(path, repo_root, label="rules")
    if obs["status"] == "present":
        text = (repo_root / obs["path"]).read_text(encoding="utf-8")
        m = PATHS_FRONTMATTER_RE.search(text)
        obs["paths_frontmatter"] = m.group("value") if m else None
    else:
        obs["paths_frontmatter"] = None
    return obs


# --- 集計 --------------------------------------------------------------------


def find_subdir_claude_md(repo_root: Path) -> list[Path]:
    """root 直下以外の CLAUDE.md を探す (worktree 内の重複を許容)."""
    return sorted(
        p for p in repo_root.rglob("CLAUDE.md")
        if p.resolve() != (repo_root / "CLAUDE.md").resolve()
        and ".git" not in p.parts
        and ".claude/worktrees" not in str(p.relative_to(repo_root))
    )


def find_rules_files(repo_root: Path) -> list[Path]:
    rules_dir = repo_root / ".claude" / "rules"
    if not rules_dir.exists():
        return []
    return sorted(rules_dir.glob("*.md"))


def summarize_meta(root_obs: dict[str, Any]) -> dict[str, Any]:
    """CLAUDE.md 自体のメタ観測 (行数 / 見出し内訳 / @import 数 / 参照 fail 数)。"""
    headings = root_obs.get("headings", [])
    by_level: dict[int, int] = {}
    for h in headings:
        by_level[h["level"]] = by_level.get(h["level"], 0) + 1
    link_targets = root_obs.get("link_targets", [])
    checkable = [t for t in link_targets if t["exists"] is not None]
    fails = [t for t in checkable if t["exists"] is False]
    imports = root_obs.get("imports", [])
    import_fails = [t for t in imports if t.get("exists") is False]
    return {
        "line_count": root_obs.get("line_count", 0),
        "headings_by_level": by_level,
        "import_count": len(imports),
        "import_fail_count": len(import_fails),
        "link_check_total": len(checkable),
        "link_check_fail_count": len(fails),
    }


# --- 出力 --------------------------------------------------------------------


def build_observation(repo_root: Path) -> dict[str, Any]:
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    root_path = repo_root / "CLAUDE.md"
    root_obs = observe_markdown_file(root_path, repo_root, label="root")

    subdir_obs = [
        observe_markdown_file(p, repo_root, label="subdir")
        for p in find_subdir_claude_md(repo_root)
    ]
    rules_obs = [observe_rules_file(p, repo_root) for p in find_rules_files(repo_root)]

    return {
        "meta": {
            "generated_at": generated_at,
            "repo_root": str(repo_root),
            "root_meta": summarize_meta(root_obs),
        },
        "sources": {
            "claude_md": {
                "root": root_obs,
                "subdirs": subdir_obs,
            },
            "rules": rules_obs,
        },
    }


def write_observation(
    observation: dict[str, Any], output_dir: Path
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = output_dir / f"observation-{ts}.json"
    out_path.write_text(
        json.dumps(observation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path


# --- CLI ---------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "inventory-claude-md の観測 script。project 範囲の CLAUDE.md 系を"
            "静的に観測し observation JSON を出力する。"
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="CLAUDE.md を探す repo root (default: cwd)。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="observation JSON の出力先 (default: /tmp/inventory-claude-md)。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    output_dir = Path(args.output_dir).expanduser()

    observation = build_observation(repo_root)
    out_path = write_observation(observation, output_dir)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
