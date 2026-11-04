#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""review-stage の巡の入力を一時ディレクトリへそろえ、path を JSON で stdout へ出す。

2 回に分けて呼ぶ。`collect` は差分と commit 一覧を取得する。呼び出し元が段階を判定した後の
`prompts` は、rubric の読み込み・issue 本文の取得・宣言された規約の収集・観点ごとの prompt の
組み立てを行う。段階の判定・issue の置き場の解決・当たる設計 doc の決定・指摘の区分・反証者の
prompt は読解が要るので本 script は持たない。

invariant:

1. 書き出す先は `collect` の呼び出しごとに一意のディレクトリで、並列の呼び出し元と file を共有しない
2. prompt file は差分・rubric・issue 本文の中身を持たず、それらの file の path だけを持つ
3. 重要領域の名前は攻撃役の prompt の `<critical-areas>` だけに書き、主張の出どころの file に混ぜない
4. Spec の入力は issue の body だけ。複数なら body ごとに `<issue ref="...">` で囲む
5. 取得できなかった issue は止めずに notes へ残す。本文が 1 件も無ければ Spec の prompt を作らない
6. 宣言の節を読めないときと宣言の file が無いときは止めずに notes へ残す。照合規則が既定の文
   (`paths` と同じ照合) でなければ、推測で照合せず宣言を読めないものとして扱う
7. git の失敗・入力の欠けは exit 非 0 で、stderr の 1 行目に要約を出す
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
PROMPTS_DIR = SKILL_DIR / "references" / "prompts"
RUBRIC = SKILL_DIR / "references" / "rubric.md"
CONFORMANCE_LEAF = (
    SKILL_DIR.parents[1] / "knowledge" / "principle-index" / "references" / "leaves" / "principle-design-conformance.md"
)
PERSPECTIVES = ("principle", "spec", "design", "adversarial")
ROUNDS = ("first", "re-review", "re-review-widened")
ISSUE_REF = re.compile(r"(?P<host>github|gitlab):(?P<placement>.+)#(?P<number>\d+)")
RE_REVIEW_ITEM_HEAD = (
    "- この差分は前巡のレビューへの修正で、読む範囲は <diff> に写っているものだけです。base より前の commit や、"
    "増分に写っていないコード・文書の file を追加で読まないでください (それ以前は前巡が読み終えています)。"
)
RE_REVIEW_ITEM_TAIL = {
    "principle": "規約 (CLAUDE.md・`.claude/rules/`・<declared-rules> の file) は増分の file に当たるものを読んでかまいません",
    "design": "<design-docs> の設計 doc は読んでかまいません",
}
RE_REVIEW_LINE = "この差分は前巡のレビューへの修正です。修正が既存の主張を壊していないかを攻めてください"


def _git(*args: str) -> bytes:
    # bytes のまま扱う: text にすると非 UTF-8 の差分で落ち、CRLF が変わる
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], check=True, capture_output=True).stdout


def collect(base: str, head: str, log_base: str | None) -> dict:
    out = Path(tempfile.mkdtemp(prefix="review-stage-"))
    files = {
        "diff": ("diff.txt", ("diff", f"{base}...{head}")),
        "commits": ("commits.txt", ("log", f"{base}..{head}", "--oneline")),
        "commit_messages": ("commit-messages.txt", ("log", f"{log_base or base}..{head}")),
        "numstat": ("numstat.txt", ("diff", "--numstat", f"{base}...{head}")),
        "changed_files": ("changed-files.txt", ("diff", "--name-only", f"{base}...{head}")),
    }
    result: dict = {"dir": str(out)}
    for key, (name, git_args) in files.items():
        (out / name).write_bytes(_git(*git_args))
        result[key] = str(out / name)
    return result


@dataclass
class Issue:
    ref: str
    body: str | None
    failure: str | None = None


def _fetch_issue(ref: str) -> Issue:
    m = ISSUE_REF.fullmatch(ref)
    if m is None:
        raise ValueError(f"--issue の形が違う: {ref} (github:<置き場>#<番号> か gitlab:<置き場>#<番号>)")
    number, placement = m["number"], m["placement"]
    if m["host"] == "github":
        argv, key = ["gh", "issue", "view", number, "-R", placement, "--json", "body"], "body"
    else:
        argv, key = ["glab", "issue", "view", number, "-R", placement, "--output", "json"], "description"
    try:
        proc = subprocess.run(argv, check=True, capture_output=True, text=True)
        body = json.loads(proc.stdout).get(key)
    except subprocess.CalledProcessError as e:
        lines = (e.stderr or "").strip().splitlines()
        return Issue(ref, None, lines[0] if lines else f"exit {e.returncode}")
    except (OSError, json.JSONDecodeError) as e:
        return Issue(ref, None, f"{type(e).__name__}: {e}")
    return Issue(ref, body) if body and body.strip() else Issue(ref, None, "本文が空")


def _read_issue_file(spec: str) -> Issue:
    ref, sep, path = spec.partition("=")
    if not sep:
        raise ValueError(f"--issue-file の形が違う: {spec} (<ref>=<file>)")
    try:
        body = Path(path).read_text()
    except (OSError, UnicodeDecodeError) as e:
        return Issue(ref, None, f"{type(e).__name__}: {e}")
    return Issue(ref, body) if body.strip() else Issue(ref, None, "本文が空")


def _spec_text(issues: list[Issue]) -> str:
    bodies = [(i.ref, i.body.rstrip("\n") + "\n") for i in issues if i.body]
    if len(bodies) == 1:
        return bodies[0][1]
    return "".join(f'<issue ref="{ref}">\n{body}</issue>\n' for ref, body in bodies)


def _glob_regex(pattern: str) -> re.Pattern[str]:
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


def _table_rows(section: str) -> list[list[str]]:
    rows = [line for line in section.splitlines() if line.lstrip().startswith("|")]
    # 両端の `|` は 1 本ずつだけ外す: `||` の空欄を列の欠けにしない
    cells = [[c.strip().strip("`") for c in row.strip().removeprefix("|").removesuffix("|").split("|")] for row in rows]
    return [row for row in cells if not all(set(c) <= set("-: ") for c in row) or not any(row)]


def _declared_rules(repo_root: Path, changed: list[str]) -> tuple[list[str], list[str]]:
    domain = repo_root / "docs" / "agents" / "domain.md"
    if not domain.is_file():
        return [], []
    try:
        text = domain.read_text()
    except (OSError, UnicodeDecodeError) as e:
        return [], [f"宣言を読めず: {type(e).__name__}: {e}"]
    m = re.search(r"^## 規約の置き場\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if m is None:
        return [], []
    section = m[1]
    rows = _table_rows(section)
    if not rows or rows[0][:2] != ["file", "当たる path"]:
        return [], ["宣言を読めず: 表に file / 当たる path の列が無い"]
    if "`paths` と同じ照合" not in section:
        return [], ["宣言を読めず: 照合規則の文が無いか、既定の文 (`paths` と同じ照合) と違う"]
    if any(len(row) < 2 or not row[0] for row in rows[1:]):
        return [], ["宣言を読めず: 表に列の足りない行がある"]
    declared, notes = [], []
    for file, globs in (row[:2] for row in rows[1:]):
        # `{a,b}` の中のカンマでは切らない
        patterns = [_glob_regex(g.strip(" `")) for g in re.split(r",(?![^{]*\})", globs) if g.strip(" `")]
        if patterns and not any(p.fullmatch(path) for p in patterns for path in changed):
            continue
        if (repo_root / file).is_file():
            declared.append(file)
        else:
            notes.append(f"宣言の file 不在: {file}")
    return declared, notes


def _render(name: str, values: dict[str, str]) -> str:
    text = (PROMPTS_DIR / f"{name}.md").read_text()
    for key, value in values.items():
        # 空の欄は行ごと落とす
        text = text.replace("{{" + key + "}}", value) if value else text.replace("{{" + key + "}}\n", "")
    return text


def _claim_sources(commit_messages: str, spec: str, pr_body: str | None) -> str:
    sections = [("commit message", commit_messages), ("issue の body", spec), ("PR・MR 本文", pr_body)]
    return "\n".join(f"## {title}\n\n{text.rstrip()}\n" for title, text in sections if text)


def prompts(args: argparse.Namespace) -> dict:
    out = Path(args.dir)
    diff, commits = out / "diff.txt", out / "commits.txt"
    if not diff.is_file() or not commits.is_file():
        raise ValueError(f"{out} に collect の出力が無い")
    if "design" in args.perspective and not args.design_doc:
        raise ValueError("design には --design-doc が要る")

    issues = [_fetch_issue(ref) for ref in args.issue] + [_read_issue_file(s) for s in args.issue_file]
    issues += [Issue(ref, None, reason) for ref, _, reason in (s.partition("=") for s in args.issue_missing)]
    missing = "; ".join(f"{i.ref}: {i.failure}" for i in issues if i.body is None)
    spec = _spec_text(issues)
    notes: dict[str, list[str]] = {name: [] for name in PERSPECTIVES}
    result: dict = {"prompts": {}, "spec": None, "claim_sources": None, "notes": notes}

    if spec:
        (out / "spec.md").write_text(spec)
        result["spec"] = str(out / "spec.md")
    if missing and "adversarial" in args.perspective:
        notes["adversarial"].append(f"出どころ欠落: {missing}")
    if "spec" in args.perspective:
        if not issues:
            notes["spec"].append("Spec skip: issue ref が無い")
        elif not spec:
            notes["spec"].append(f"Spec skip: 本文を取得できなかった: {missing}")
        elif missing:
            notes["spec"].append(f"Spec 源欠落: {missing}")

    common = {"DIFF": str(diff), "COMMITS": str(commits)}
    adoption = (PROMPTS_DIR / "adoption-rules.md").read_text().rstrip("\n")

    def re_review_item(name: str) -> str:
        return RE_REVIEW_ITEM_HEAD + RE_REVIEW_ITEM_TAIL[name] if args.round == "re-review" else ""

    texts: dict[str, str] = {}
    if "principle" in args.perspective:
        shutil.copyfile(RUBRIC, out / "rubric.md")
        repo_root = Path(_git("rev-parse", "--show-toplevel").decode().strip())
        changed = (out / "changed-files.txt").read_text(errors="replace").splitlines()
        declared, notes["principle"] = _declared_rules(repo_root, changed)
        texts["principle"] = _render("principle", {
            **common, "RUBRIC": str(out / "rubric.md"), "DECLARED_RULES": "\n".join(declared) or "なし",
            "ADOPTION_RULES": adoption, "RE_REVIEW_ITEM": re_review_item("principle"),
        })
    if "spec" in args.perspective and spec:
        texts["spec"] = _render("spec", {**common, "SPEC": result["spec"], "ADOPTION_RULES": adoption})
    if "design" in args.perspective:
        if not CONFORMANCE_LEAF.is_file():
            raise ValueError(f"{CONFORMANCE_LEAF} が無い")
        texts["design"] = _render("design", {
            **common, "DESIGN_DOCS": "\n".join(args.design_doc), "CONFORMANCE_LEAF": str(CONFORMANCE_LEAF),
            "ADOPTION_RULES": adoption, "RE_REVIEW_ITEM": re_review_item("design"),
        })
    if "adversarial" in args.perspective:
        pr_body = Path(args.pr_body).read_text() if args.pr_body else None
        claim_sources = out / "claim-sources.md"
        claim_sources.write_text(_claim_sources((out / "commit-messages.txt").read_text(errors="replace"), spec, pr_body))
        result["claim_sources"] = str(claim_sources)
        critical = f"<critical-areas>\n{args.critical_areas}\n</critical-areas>" if args.critical_areas else ""
        texts["adversarial"] = _render("adversarial", {
            "DIFF": str(diff), "CLAIM_SOURCES": str(claim_sources), "CRITICAL_AREAS_BLOCK": critical,
            "RE_REVIEW_LINE": RE_REVIEW_LINE if args.round != "first" else "",
        })

    for name, text in texts.items():
        path = out / f"{name}.prompt.md"
        path.write_text(text)
        result["prompts"][name] = str(path)
    return result


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # 呼び出し元は stderr の 1 行目を読むので、usage は出さず原因だけを出す
        self.exit(2, f"{self.prog}: {message}\n")


def main(argv: list[str]) -> int:
    ap = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect", help="差分と commit 一覧を一時ディレクトリへ書く")
    c.add_argument("--base", required=True)
    c.add_argument("--head", required=True)
    c.add_argument("--log-base", help="commit message の起点。省略時は --base")
    p = sub.add_parser("prompts", help="観点ごとの prompt file を組み立てる")
    p.add_argument("--dir", required=True, help="collect が返した dir")
    p.add_argument("--perspective", action="append", choices=PERSPECTIVES, required=True)
    p.add_argument("--round", choices=ROUNDS, required=True, help="re-review-widened は `増分に絞れず:` の巡")
    p.add_argument("--issue", action="append", default=[], help="github:<置き場>#<番号> / gitlab:<置き場>#<番号>")
    p.add_argument("--issue-file", action="append", default=[], help="<ref>=<本文の file>。CLI で取れない tracker の本文")
    p.add_argument("--issue-missing", action="append", default=[], help="<ref>=<理由>。呼び出し元が本文を取得できなかった ref")
    p.add_argument("--design-doc", action="append", default=[], help="<置き場> | <索引>")
    p.add_argument("--critical-areas", help="攻撃役へ渡す重要領域の名前")
    p.add_argument("--pr-body", help="PR・MR 本文の file")
    args = ap.parse_args(argv)
    try:
        result = collect(args.base, args.head, args.log_base) if args.command == "collect" else prompts(args)
    except subprocess.CalledProcessError as e:
        lines = (e.stderr or b"").decode(errors="replace").strip().splitlines()
        print(f"{' '.join(e.cmd)} が失敗 (exit {e.returncode}): {lines[0] if lines else '出力なし'}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — 呼び出し元は stderr の 1 行目を停止理由に載せる
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
