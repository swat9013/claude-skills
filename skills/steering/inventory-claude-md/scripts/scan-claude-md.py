#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""inventory-claude-md の観測 script (observation JSON 生成)。

project 範囲の CLAUDE.md 系 (root `CLAUDE.md` + project-local `CLAUDE.local.md`
+ サブディレクトリ CLAUDE.md + `.claude/rules/**/*.md`) を **静的観測** し、次の
項目を含む observation JSON を出力する。

- 参照先実在性 (link_targets): markdown link と `@import` 展開先の実在チェック。
  fail-safe = path として復元可能なもののみ検査。URL / 動的展開文字列は check_mode
  ラベルを付けて null 判定。
- @import 展開 (imports): `@path/to/file` 参照の解決先とその存在。棚卸し
  boundary を確定させるための決定的観測。
- section 物理計測 (sections): markdown 見出し階層と各 section の行数。context
  提供のみ、閾値 enforce はしない。
- 静的トークンコスト (token_cost): **行単位**の概算 token 数と section 別の合計。
  CLAUDE.md 系は session 開始で無条件に載るので、行数ではなく token が実コスト。
  実績側 (どの file が何 session に注入されたか) は transcript-query の
  named query `overhead` が出し、突合は LLM 段階が行う。
- 規範の届き方 (reach): 棚卸しの前後で比べる量の材料。同じ入力には同じ値を出す。
  - scoped_lines: 場面か path を絞る語 (`SCOPING_PATTERNS`。中身が `/` か `*` を含む
    code span も数える) を持つ root / local CLAUDE.md の行 (`text` は前後の空白を落とした行の本文)
  - uncovered_scoped_lines: `--covered` を渡したときだけ出る。scoped_lines のうち、
    本文が `--covered` の file のどの行とも一致しない行。どの行が candidate・根拠つきの
    残置・人が承認した書き換えになったかは LLM 段階が決めて file に書き、数えるのは script が行う
  - index_targets: root / local CLAUDE.md の link が指す、repo 内に実在する file
  - unpointed_docs: どの索引行・rules・skill の手順からも指されていない agent 向け文書。
    母集団 (agent 向け文書) は、`*.md` の index_targets と同じ dir に置かれた `*.md`
    (`--before` を渡したときは、前の観測の母集団 `population_docs` をそのまま使う。索引行を消しても母集団は減らない)
    (再帰しない) のうち、CLAUDE.md 系・rules・`.claude/skills/**/SKILL.md` が指す文書から
    link で辿れない文書 (辿れる文書は、指された文書の下位の資料として母集団から外す)。
    指すかは link・`@import`・code span の `*.md` path で決める
  - missing_targets: CLAUDE.md 系と rules の link・`@import`・code span の `*.md` path の
    うち、検査して実在しなかったもの。code span は起点の決まった path (placeholder・glob を
    含まず、先頭の段が書いた file の dir か repo root に在る) だけを検査し、どちらの起点からも
    実在しないものを数える
  - duplicates: 同じ本文の行 (正規化して `MIN_DUPLICATE_CHARS` 字以上) が 2 か所以上に
    あり、1 か所以上が CLAUDE.md 系か rules にある組。比べるのは CLAUDE.md 系・rules・
    `.claude/skills/**/SKILL.md` と、CLAUDE.md 系・rules が直接指す repo 内の `*.md`
  - always_loaded: root / local CLAUDE.md と `paths:` の無い rules と、それらから `@import`
    で展開される repo 内の file の推定 token と字数
  - counts: missing_targets・unpointed_docs・duplicates の件数

原則: **決定的にできる推論は script へ、意味判断だけを LLM へ、判定は人間に**。
script は bucket を割り当てず、bucket の候補を決定的に出す rule も持たない。既存
CLAUDE.md 行の bucket 判定はすべて内容判断であり、count 0 / 完全一致のような
「観測の列だけで評価でき自然言語の解釈を要さない述語」が存在しないため。

出力: --output-dir に observation-<timestamp>.json を書き、path を stdout に
print する。Markdown レポートは LLM 段階の成果物で、本 script は生成しない。

汎用スキル制約: 依存は Claude Code 標準ファイルのみ (`<repo>/CLAUDE.md` /
`<repo>/CLAUDE.local.md` / `<repo>/.claude/rules/**/*.md`。届き方の観測では
`<repo>/.claude/skills/**/SKILL.md` と、それらが指す repo 内の文書も読む)。global
`~/.claude/CLAUDE.md` は読みも書きもしない (仕様上除外)。

`CLAUDE.local.md` は memory 階層の project-local 層で、`.gitignore` 済みなら
VCS 管理外になる。script は**読むだけ**で tracked/untracked を判定しない
(git 依存を持たない)。`sources.claude_md.local` に `label: "project-local"` で
独立キーとして出し、LLM 段階が source class を JSON から直接読めるようにする。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
from collections.abc import Iterator
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

# token 概算の係数。**transcript-query の `views.sql` の係数と同じ規則**で、
# 変えるなら両方を同時に変える (片方だけ動かすと、同じ file の静的コストと注入実績が
# 別スケールになり突合できない)。parity は `tests/test_scan_claude_md.py` が検査する。
# script は DuckDB の macro を呼べないため写しになる。
CJK_RE = re.compile("[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef]")
ASCII_CHARS_PER_TOKEN = 4.0
CJK_CHARS_PER_TOKEN = 1.0
TOKEN_ESTIMATOR = "approx: cjk 1 字/token + その他 4 字/token (tokenizer 非使用)"

# 場面か path を絞る語。棚卸しの見直しで直す対象なので、語の一覧はここだけに置く。
# 語に加えて、中身が `/` か `*` を含む code span (path か glob) も場面か path を絞る印に数える
SCOPING_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    "とき", "場合", "の際", "際は", "前に", "後に", "編集", "着手", "触る",
    r"\bwhen\b", r"\bbefore\b", r"\bafter\b", r"\bwhile\b", r"\bif\b",
))
SCOPING_CODE_SPAN_CHARS = ("/", "*")

# code span。左から組で取り、span どうしの間の文字を span と読まない
CODE_SPAN_RE = re.compile(r"`(?P<body>[^`]+)`")
# code span の中身が `*.md` の path なら、索引行か Read させる行の指す先として扱う
CODE_MD_PATH_RE = re.compile(r"^(?P<path>[^\s]+\.md)$")
# `@import` を辿る深さの上限。Claude Code の memory の docs が挙げる import の再帰の上限 (5 hops) に合わせる
MAX_IMPORT_HOPS = 5
# code span の path のうち、実在を検査しない印 (placeholder・glob・変数)
CODE_SPAN_UNCHECKED_CHARS = ("<", ">", "*", "{", "}", "$")

# 写しとみなす行の最短の字数 (正規化した後)。短い定型句の一致を写しに数えない
MIN_DUPLICATE_CHARS = 30
LIST_MARKER_RE = re.compile(r"^(?:[-*+>]|\d+\.)\s+")


# --- 純関数 ------------------------------------------------------------------


def classify_link_target(target: str) -> str:
    """link target を check_mode に分類する。

    - url: http(s):// / ftp: / mailto: など
    - anchor: 単なる # 以降のアンカー
    - absolute: /path 始まり
    - dynamic: env 展開や template 変数を含むもの (fail-safe で存在検査しない)
    - relative-path: それ以外 (link を書いた file のディレクトリから解決)
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


def estimate_tokens(text: str) -> int:
    """文字列の token 数を概算する (`TOKEN_ESTIMATOR` の係数)。

    tokenizer を持ち込まないのは、観測の決定性 (同じ入力に同じ数) と依存ゼロを
    優先するため。**桁の比較にだけ使える精度**で、bucket 判定の単独根拠にしない。
    """
    if not text:
        return 0
    cjk = len(CJK_RE.findall(text))
    other = len(text) - cjk
    return math.ceil(cjk / CJK_CHARS_PER_TOKEN + other / ASCII_CHARS_PER_TOKEN)


def build_token_cost(
    lines: list[str], sections: list[dict[str, Any]]
) -> dict[str, Any]:
    """行単位の概算 token と section 別合計。

    CLAUDE.md 系は session 開始で**無条件に**載るので、実コストは行数ではなく
    token になる。行単位で出すのは、bucket の単位が行 / 行群 (SKILL.md 手順 2) で
    あり、「この 3 行に毎 session 何 token 払っているか」が候補の重み付けに直接
    効くため。

    `per_line` は index 0 = 1 行目の配列で持つ (行ごとの dict にすると同じ情報が
    数倍に膨らみ、observation JSON を読む窓を食う)。
    """
    per_line = [estimate_tokens(line) for line in lines]
    total = sum(per_line)
    section_costs = []
    for section in sections:
        start = max(1, section["start_line"])
        end = min(len(per_line), section["end_line"])
        est = sum(per_line[start - 1:end]) if end >= start else 0
        section_costs.append({
            "heading_text": section["heading_text"],
            "start_line": section["start_line"],
            "end_line": section["end_line"],
            "est_tokens": est,
            "share": (est / total) if total else 0.0,
        })
    return {
        "estimator": TOKEN_ESTIMATOR,
        "total_est_tokens": total,
        "per_line_est_tokens": per_line,
        "sections": section_costs,
    }


def iter_prose_lines(lines: list[str]) -> Iterator[tuple[int, str]]:
    """フェンスコードブロック (```) の外の行を (1-indexed の行番号, 行) で返す。

    見出し・link・`@import` はどれもコードブロックの中では記法として効かないので、
    3 つの抽出が同じ判定を使う。フェンスの行そのものも返さない。
    """
    in_code_block = False
    for i, raw in enumerate(lines, start=1):
        if raw.lstrip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if not in_code_block:
            yield i, raw


def parse_headings(lines: list[str]) -> list[dict[str, Any]]:
    """markdown 見出し行を (level, line, text) の list で返す (1-indexed line)."""
    out: list[dict[str, Any]] = []
    for i, raw in iter_prose_lines(lines):
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

    存在検査は呼び出し側 (link を書いた file のディレクトリ依存)。ここでは
    source location と check_mode まで確定する。
    """
    out: list[dict[str, Any]] = []
    for i, raw in iter_prose_lines(lines):
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
    for i, raw in iter_prose_lines(lines):
        for m in IMPORT_RE.finditer(raw):
            out.append({
                "source": f"{source_path}:{i}",
                "expression": m.group("expr"),
                "target": m.group("path"),
            })
    return out


def check_target_existence(
    entries: list[dict[str, Any]], repo_root: Path, base_dir: Path
) -> list[dict[str, Any]]:
    """link_targets / imports の存在検査を追加する (fail-safe)。

    - relative-path: `base_dir` (link を書いた file のディレクトリ) からの相対で
      resolve、anchor 除去後の path を検査。markdown の相対 link は書いた file から
      の相対で読まれるので、repo root を基準にすると repo 直下以外の file
      (`.claude/rules/**/*.md` 等) の `../../` が必ず外れる
    - absolute: そのまま存在検査 (repo 外可)
    - url / anchor / dynamic / empty: 検査しない (exists=None)
    """
    out: list[dict[str, Any]] = []
    for e in entries:
        entry = dict(e)
        mode = entry.get("check_mode")
        target = entry.get("target", "")
        if mode == "relative-path":
            resolved = base_dir / strip_anchor(target)
            entry["resolved_path"] = _repo_relative(resolved, repo_root)
            entry["exists"] = resolved.exists()
        elif mode == "absolute":
            resolved = Path(strip_anchor(target))
            entry["resolved_path"] = str(resolved)
            entry["exists"] = resolved.exists()
        else:
            # url / anchor / dynamic / empty
            entry["resolved_path"] = None
            entry["exists"] = None
        out.append(entry)
    return out


def import_target(source: Path, target: str) -> Path:
    """`@import` の target の解決先。Claude Code は import を書いた file の dir から解決する。"""
    return source.parent / target


def check_import_existence(
    entries: list[dict[str, Any]], repo_root: Path, source: Path
) -> list[dict[str, Any]]:
    """@import 展開先の存在検査。"""
    out: list[dict[str, Any]] = []
    for e in entries:
        entry = dict(e)
        target = entry.get("target", "")
        resolved = import_target(source, target)
        entry["resolved_path"] = _repo_relative(resolved, repo_root)
        entry["exists"] = resolved.exists()
        out.append(entry)
    return out


def _repo_relative(path: Path, repo_root: Path) -> str:
    """レポート表示用の path 表記 — repo 内なら repo 相対、外なら絶対。

    `..` を含む path をそのまま出すと解決基準が読み取れないので、表示だけ
    normpath で畳む (存在検査は畳む前の path で行う — normpath は字面操作なので
    symlink を挟んだ `..` を実際の解決先とは違う場所に畳んでしまう)。
    """
    normalized = Path(os.path.normpath(path))
    try:
        return str(normalized.relative_to(repo_root))
    except ValueError:
        return str(normalized)


def _is_within(target: Path, base: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def read_file_safe(path: Path) -> tuple[str, str]:
    """utf-8 で file を読む。読めなければ空を返す (fail-safe)。

    Returns (status, text) where status ∈ {"present", "missing", "unreadable"}.
    """
    if not path.exists():
        return "missing", ""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return "unreadable", ""
    return "present", text


def observe_markdown_file(
    path: Path, repo_root: Path, label: str
) -> dict[str, Any]:
    """1 ファイルの決定的観測を組み立てる。"""
    status, text = read_file_safe(path)
    lines = text.splitlines()
    display_path = str(path.relative_to(repo_root)) if _is_within(path, repo_root) else str(path)
    if status != "present":
        return {
            "label": label,
            "path": display_path,
            "status": status,
            "line_count": 0,
            "chars": 0,
            "headings": [],
            "sections": [],
            "link_targets": [],
            "imports": [],
            "token_cost": build_token_cost([], []),
        }
    headings = parse_headings(lines)
    sections = sections_from_headings(headings, len(lines))
    link_targets = check_target_existence(
        extract_link_targets(display_path, lines), repo_root, path.parent
    )
    imports = check_import_existence(
        extract_imports(display_path, lines), repo_root, path
    )
    return {
        "label": label,
        "path": display_path,
        "status": "present",
        "line_count": len(lines),
        # 改行を含む字数
        "chars": len(text),
        "headings": headings,
        "sections": sections,
        "link_targets": link_targets,
        "imports": imports,
        "token_cost": build_token_cost(lines, sections),
    }


def observe_rules_file(path: Path, repo_root: Path) -> dict[str, Any]:
    """rules/**/*.md は markdown 観測 + frontmatter `paths:` 抽出。"""
    obs = observe_markdown_file(path, repo_root, label="rules")
    if obs["status"] == "present":
        # 本文の `paths:` 行 (例示の YAML など) を frontmatter と読まないよう、先頭の frontmatter だけを探す
        lines = _read_lines(path)
        frontmatter = "\n".join(lines[1:max(_frontmatter_end(lines) - 1, 0)])
        m = PATHS_FRONTMATTER_RE.search(frontmatter)
        obs["paths_frontmatter"] = m.group("value") if m else None
    else:
        obs["paths_frontmatter"] = None
    return obs


# --- 集計 --------------------------------------------------------------------


def find_subdir_claude_md(repo_root: Path) -> list[Path]:
    """root 直下以外の CLAUDE.md を探す (worktree 内の重複を許容)。

    glob は完全一致 `CLAUDE.md` なので `CLAUDE.local.md` は拾わない
    (project-local 層は build_observation が root 直下のみ別キーで観測する)。
    """
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
    # サブディレクトリの rules も Claude Code が読むので再帰で拾う
    return sorted(rules_dir.rglob("*.md"))


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
        # 常時ロードの実コスト。行数と併記するのは、行数が同じでも token は数倍
        # 違う (表・コードブロック・日本語) ため
        "est_tokens": root_obs.get("token_cost", {}).get("total_est_tokens", 0),
    }


# --- 規範の届き方 -------------------------------------------------------------


def find_skill_files(repo_root: Path) -> list[Path]:
    skills_dir = repo_root / ".claude" / "skills"
    if not skills_dir.exists():
        return []
    return sorted(skills_dir.rglob("SKILL.md"))


def find_scoped_lines(display_path: str, lines: list[str]) -> list[dict[str, Any]]:
    """場面か path を絞る語を持つ行 (見出しとコードブロックの中は除く)。"""
    out: list[dict[str, Any]] = []
    for i, raw in iter_prose_lines(lines):
        if HEADING_RE.match(raw):
            continue
        matched = [m.group(0) for pattern in SCOPING_PATTERNS for m in pattern.finditer(raw)]
        matched += [m.group(0) for m in CODE_SPAN_RE.finditer(raw)
                    if any(c in m.group("body") for c in SCOPING_CODE_SPAN_CHARS)]
        if matched:
            out.append({"source": f"{display_path}:{i}", "text": raw.strip(), "matched": matched})
    return out


def _repo_file(path: Path, repo_root: Path) -> Path | None:
    """repo 内に実在する file なら解決した path を返す。"""
    if path.is_file() and _is_within(path, repo_root):
        return path.resolve()
    return None


def referenced_files(path: Path, lines: list[str], repo_root: Path) -> set[Path]:
    """file が指す repo 内の実在 file (link・`@import`・backtick 内の `*.md` path)。

    backtick 内の path は書いた file の dir から、次に repo root から解決する。
    """
    found: set[Path] = set()
    for _i, raw in iter_prose_lines(lines):
        for m in MD_LINK_RE.finditer(raw):
            target = m.group("target")
            mode = classify_link_target(target)
            if mode == "relative-path":
                hit = _repo_file(path.parent / strip_anchor(target), repo_root)
            elif mode == "absolute":
                hit = _repo_file(Path(strip_anchor(target)), repo_root)
            else:
                hit = None
            if hit:
                found.add(hit)
        for m in IMPORT_RE.finditer(raw):
            hit = _repo_file(import_target(path, m.group("path")), repo_root)
            if hit:
                found.add(hit)
        for _target, hit in _code_md_paths(path, raw, repo_root):
            if hit:
                found.add(hit)
    return found


def _code_md_paths(path: Path, raw: str, repo_root: Path) -> list[tuple[str, Path | None]]:
    """行の code span のうち `*.md` の path と、その解決先 (書いた file の dir から、次に repo root から)。"""
    out: list[tuple[str, Path | None]] = []
    for span in CODE_SPAN_RE.finditer(raw):
        m = CODE_MD_PATH_RE.match(span.group("body"))
        if m:
            target = m.group("path")
            out.append((target, _repo_file(path.parent / target, repo_root)
                        or _repo_file(repo_root / target, repo_root)))
    return out


def _anchored(target: str, path: Path, repo_root: Path) -> bool:
    """code span の path が、どこから読むかの決まった path か。

    placeholder・glob を含む path と、先頭の段がどちらの起点にも無い path (散文で名指した
    skill の dir からの相対など、文脈で起点が決まる path) は、実在を検査できないので外す。
    """
    if any(c in target for c in CODE_SPAN_UNCHECKED_CHARS) or "/" not in target:
        return False
    # 絶対 path・home・親 dir から始まる path は repo の外を指しうる。repo の外の実在は検査しない
    if target.startswith(("/", "~", "..")):
        return False
    head = target.split("/", 1)[0]
    return (path.parent / head).exists() or (repo_root / head).exists()


def missing_code_md_paths(path: Path, display_path: str, repo_root: Path) -> list[dict[str, Any]]:
    """code span で書いた起点の決まった `*.md` の path のうち、どちらの起点からも実在しないもの。

    link の label の code span は、link の側で検査するので数えない (同じ参照を 2 度数えない)。
    """
    return [
        {"source": f"{display_path}:{i}", "target": target}
        for i, raw in iter_prose_lines(_read_lines(path))
        for target, hit in _code_md_paths(path, MD_LINK_RE.sub("", raw), repo_root)
        if hit is None and _anchored(target, path, repo_root)
    ]


def _read_lines(path: Path) -> list[str]:
    return read_file_safe(path)[1].splitlines()


def imported_files(starts: list[Path], repo_root: Path) -> set[Path]:
    """starts から `@import` を辿って展開される repo 内の file (starts は含まない)。

    Claude Code が展開する深さ (`MAX_IMPORT_HOPS`) までだけを辿る。
    """
    start_set = {s.resolve() for s in starts}
    seen: set[Path] = set()
    frontier = [(s, 0) for s in starts]
    while frontier:
        current, hops = frontier.pop()
        if hops >= MAX_IMPORT_HOPS:
            continue
        for _i, raw in iter_prose_lines(_read_lines(current)):
            for m in IMPORT_RE.finditer(raw):
                hit = _repo_file(import_target(current, m.group("path")), repo_root)
                if hit and hit not in seen and hit not in start_set:
                    seen.add(hit)
                    frontier.append((hit, hops + 1))
    return seen


def reachable_files(starts: set[Path], repo_root: Path) -> set[Path]:
    """starts から repo 内の `*.md` の参照を辿って届く file の集合 (starts を含む)。"""
    seen = set(starts)
    frontier = sorted(starts)
    while frontier:
        current = frontier.pop()
        if current.suffix != ".md":
            continue
        for hit in referenced_files(current, _read_lines(current), repo_root):
            if hit not in seen:
                seen.add(hit)
                frontier.append(hit)
    return seen


def _frontmatter_end(lines: list[str]) -> int:
    """先頭の YAML frontmatter の閉じ行の行番号 (1-indexed)。無ければ 0。"""
    if not lines or lines[0].strip() != "---":
        return 0
    for i, raw in enumerate(lines[1:], start=2):
        if raw.strip() == "---":
            return i
    return 0


def _normalize_body(raw: str) -> str:
    text = " ".join(raw.split())
    return LIST_MARKER_RE.sub("", text)


def find_duplicates(files: list[Path], anchors: set[Path], repo_root: Path) -> list[dict[str, Any]]:
    """同じ本文の行が 2 か所以上にあり、そのうち 1 か所以上が anchors の file にある組。

    anchors (CLAUDE.md 系と rules) を含まない組は、文書どうしの一致で棚卸しの対象の外。
    frontmatter・見出し・表の行は比べない。表の見出し行や frontmatter の定型は、
    本文の写しでなくても一致する。
    """
    anchor_displays = {_repo_relative(p, repo_root) for p in anchors}
    locations: dict[str, list[str]] = {}
    for path in files:
        display = _repo_relative(path, repo_root)
        lines = _read_lines(path)
        body_start = _frontmatter_end(lines)
        for i, raw in iter_prose_lines(lines):
            if i <= body_start or HEADING_RE.match(raw) or raw.lstrip().startswith("|"):
                continue
            body = _normalize_body(raw)
            if len(body) >= MIN_DUPLICATE_CHARS:
                locations.setdefault(body, []).append(f"{display}:{i}")
    return [
        {"text": body, "locations": places}
        for body, places in sorted(locations.items())
        if len(places) >= 2 and any(place.rsplit(":", 1)[0] in anchor_displays for place in places)
    ]


def build_reach(
    repo_root: Path,
    claude_md_obs: list[dict[str, Any]],
    rules_obs: list[dict[str, Any]],
    covered: set[str] | None,
    population_docs: set[Path] | None = None,
) -> dict[str, Any]:
    """規範の届き方の材料。値は入力の file の中身と covered と population_docs だけで決まる。

    population_docs は前の観測が決めた母集団。None なら今回の index_targets の dir で決める。

    covered は candidate・根拠つきの残置・人が承認した書き換えになった行の本文 (前後の空白を落とす)。
    行番号でなく本文で照合するのは、反映の後に起動し直すと行番号がずれるため。
    """
    present = [o for o in [*claude_md_obs, *rules_obs] if o["status"] == "present"]
    source_paths = [repo_root / o["path"] for o in present]
    skill_paths = find_skill_files(repo_root)
    excluded = {p.resolve() for p in [*source_paths, *skill_paths]}

    top = [o for o in claude_md_obs if o["label"] in ("root", "project-local") and o["status"] == "present"]
    scoped_lines = [
        line for o in top for line in find_scoped_lines(o["path"], _read_lines(repo_root / o["path"]))
    ]
    always = [*top, *(o for o in rules_obs if o["status"] == "present" and o["paths_frontmatter"] is None)]
    # `@import` の先は session の開始で載る。常時載る量に足し、Read させる索引行の先には数えない
    imported = imported_files([repo_root / o["path"] for o in always], repo_root)
    index_targets = sorted({
        hit
        for o in top
        for hit in referenced_files(repo_root / o["path"], _read_lines(repo_root / o["path"]), repo_root)
    } - excluded - imported)

    pointed_by_sources: set[Path] = set()
    for path in source_paths:
        pointed_by_sources |= referenced_files(path, _read_lines(path), repo_root)
    pointed = set(pointed_by_sources)
    for path in skill_paths:
        pointed |= referenced_files(path, _read_lines(path), repo_root)
    reachable = reachable_files(pointed, repo_root)
    population = (population_docs if population_docs is not None else {
        doc.resolve()
        for d in sorted({t.parent for t in index_targets if t.suffix == ".md"})
        for doc in d.glob("*.md")
    }) - excluded
    unpointed = sorted(_repo_relative(p, repo_root) for p in population - reachable)

    missing_targets = [
        {"source": e["source"], "target": e["target"]}
        for o in present
        for e in [*o["link_targets"], *o["imports"]]
        if e.get("exists") is False
    ] + [
        entry for o in present for entry in missing_code_md_paths(repo_root / o["path"], o["path"], repo_root)
    ]

    sources_resolved = {p.resolve() for p in source_paths}
    compared = sorted(sources_resolved | {p.resolve() for p in skill_paths}
                      | {p for p in pointed_by_sources if p.suffix == ".md"})
    duplicates = find_duplicates(compared, sources_resolved, repo_root)

    always_files = [repo_root / o["path"] for o in always] + sorted(imported)
    always_texts = [read_file_safe(p)[1] for p in always_files]
    coverage: dict[str, Any] = {}
    if covered is not None:
        uncovered = [line for line in scoped_lines if line["text"] not in covered]
        coverage = {"uncovered_scoped_lines": uncovered}
    return {
        **coverage,
        "scoping_patterns": [p.pattern for p in SCOPING_PATTERNS] + [
            f"code span containing {' or '.join(SCOPING_CODE_SPAN_CHARS)}"],
        "scoped_lines": scoped_lines,
        "index_targets": [_repo_relative(p, repo_root) for p in index_targets],
        "population_docs": sorted(_repo_relative(p, repo_root) for p in population),
        "unpointed_docs": unpointed,
        "missing_targets": missing_targets,
        "duplicates": duplicates,
        "always_loaded": {
            "files": [_repo_relative(p, repo_root) for p in always_files],
            # token は行ごとに概算して足す (token_cost と同じ数え方)
            "est_tokens": sum(estimate_tokens(line) for text in always_texts for line in text.splitlines()),
            "chars": sum(len(text) for text in always_texts),
            "estimator": TOKEN_ESTIMATOR,
        },
        "counts": {
            **({"uncovered_scoped_lines": len(coverage["uncovered_scoped_lines"])} if coverage else {}),
            "unpointed_docs": len(unpointed),
            "missing_targets": len(missing_targets),
            "duplicates": len(duplicates),
        },
    }


# --- 出力 --------------------------------------------------------------------


def build_observation(
    repo_root: Path,
    covered: set[str] | None = None,
    population_docs: set[Path] | None = None,
) -> dict[str, Any]:
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    root_path = repo_root / "CLAUDE.md"
    root_obs = observe_markdown_file(root_path, repo_root, label="root")

    # project-local 層 (memory 階層の 4 層目)。対象は root 直下の 1 path のみ
    # (skill 側の観測境界。SKILL.md スコープ節を参照)。
    local_path = repo_root / "CLAUDE.local.md"
    local_obs = observe_markdown_file(local_path, repo_root, label="project-local")

    subdir_obs = [
        observe_markdown_file(p, repo_root, label="subdir")
        for p in find_subdir_claude_md(repo_root)
    ]
    rules_obs = [observe_rules_file(p, repo_root) for p in find_rules_files(repo_root)]
    reach = build_reach(repo_root, [root_obs, local_obs, *subdir_obs], rules_obs, covered, population_docs)

    return {
        "reach": reach,
        "meta": {
            "generated_at": generated_at,
            "repo_root": str(repo_root),
            "root_meta": summarize_meta(root_obs),
            "local_meta": summarize_meta(local_obs),
        },
        "sources": {
            "claude_md": {
                "root": root_obs,
                "local": local_obs,
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
    parser.add_argument(
        "--covered",
        type=Path,
        help=(
            "candidate・根拠つきの残置・人が承認した書き換えになった CLAUDE.md の行の本文を 1 行 1 件で書いた file。"
            "渡すと reach に uncovered_scoped_lines とその件数が出る。"
        ),
    )
    parser.add_argument(
        "--before",
        type=Path,
        help=(
            "前の観測の observation JSON。渡すと unpointed_docs の母集団を、その reach.population_docs"
            " にする (後の値を、前の値と同じ母集団で数える)。"
        ),
    )
    return parser


def read_before_population(path: Path, repo_root: Path) -> set[Path]:
    """--before の observation JSON から前の母集団を読む。読めなければ例外のまま止まる (数を黙って 0 にしない)。"""
    observation = json.loads(path.read_text(encoding="utf-8"))
    return {(repo_root / t).resolve() for t in observation["reach"]["population_docs"]}


def read_covered(path: Path) -> set[str]:
    """--covered の file を読む。読めなければ例外のまま止まり、observation を書かない (数を黙って 0 にしない)。"""
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    output_dir = Path(args.output_dir).expanduser()

    covered = read_covered(args.covered) if args.covered else None
    population_docs = read_before_population(args.before, repo_root) if args.before else None
    observation = build_observation(repo_root, covered, population_docs)
    out_path = write_observation(observation, output_dir)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
