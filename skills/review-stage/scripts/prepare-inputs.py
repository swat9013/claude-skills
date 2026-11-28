#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""review-stage の巡の入力を一時ディレクトリへそろえ、path を JSON で stdout へ出す。

2 回に分けて呼ぶ。`collect` は差分と commit 一覧を取得し、file ごとの insertions と commit message・
PR 本文の issue ref を返す。呼び出し元が段階を判定した後の `prompts` は、段階と巡から走らせる観点を
導き、rubric の読み込み・issue 本文の取得 (tracker の種類は remote の host から決める)・規約の収集
(CLAUDE.md・`paths` が当たる `.claude/rules/`・宣言された規約)・観点ごとの prompt の組み立てを行う。
実行されるコードかと重要領域の判定 (段階の判定)・issue の置き場の解決・当たる設計 doc の決定・
指摘の区分・反証者の prompt は読解が要るので本 script は持たない。

invariant:

1. 書き出す先は `collect` の呼び出しごとに一意のディレクトリで、並列の呼び出し元と file を共有しない
2. prompt file は差分・rubric・issue 本文の中身を持たず、それらの file の path だけを持つ
3. 重要領域の名前は攻撃役の prompt の `<critical-areas>` だけに書き、主張の出どころの file に混ぜない
4. Spec の入力は issue の body だけ。複数なら body ごとに `<issue ref="...">` で囲む
5. 取得できなかった issue は止めずに notes へ残す。本文が 1 件も無ければ Spec の prompt を作らない
6. 宣言の節を読めないときと宣言の file が無いときは止めずに notes へ残す。照合規則が既定の文
   (`paths` と同じ照合) でなければ、推測で照合せず宣言を読めないものとして扱う
7. git の失敗・入力の欠けは exit 非 0 で、stderr の 1 行目に要約を出す
8. `.gitattributes` が生成物と宣言した file (`linguist-generated` / `gitlab-generated`) は diff の file
   から除き、除いた path を notes と観点ごとの prompt に残す。numstat と変更 file の一覧からは除かない
9. insertions は binary の file を 0 行とし、rename は移動先の path に数える
10. issue ref は作業 branch の merge でない commit (first-parent) の message と PR 本文の `#N` と `<置き場>#N` (置き場は `/` 区切り) だけを引き、
    MR の参照 (`!N`) は引かない
11. 段階と巡から観点の組が決まらない組み合わせ (docs 段階の再レビュー) は exit 非 0
12. remote の host から tracker の種類を決められない ref は止めずに取得の失敗として notes へ残す
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path, PurePosixPath

SKILL_DIR = Path(__file__).resolve().parents[1]
PROMPTS_DIR = SKILL_DIR / "references" / "prompts"
RUBRIC = SKILL_DIR / "references" / "rubric.md"
# 他の skill の file は、この file の dir からの相対 path 1 本で指す (skill の置き場の段数は配り方で変わり、この形の path だけがそれに合わせて書き換わる)
SCRIPTS_DIR = Path(__file__).resolve().parent
CONFORMANCE_LEAF = (
    SCRIPTS_DIR / "../../principle-index/references/leaves/principle-design-conformance.md"
).resolve()
# .claude/rules/ の照合は ready-for-agent-review と共有する (同じ関数を使う)
RULES_MATCHER = (SCRIPTS_DIR / "../../claude-config-reference/scripts/list-matching-rules.py").resolve()
ROUNDS = ("first", "re-review", "re-review-widened")
STAGES = ("docs", "軽量", "標準")
# 段階 → (初回に走らせる観点, 再レビューに走らせる観点)。"defect" は欠陥探し (prompt を組まない観点)。
# docs 段階の再レビューは来ない (docs 段階の出力は再レビューを要さない)
PERSPECTIVE_TABLE: dict[str, tuple[tuple[str, ...], tuple[str, ...] | None]] = {
    "docs": (("principle", "spec", "design"), None),
    "軽量": (("defect", "adversarial", "principle", "spec", "design"), ("defect", "adversarial")),
    "標準": (("defect", "adversarial", "principle", "spec", "design"), ("defect", "adversarial", "principle", "design")),
}
# 出力の節の順と名前
SECTION_NAMES = {
    "defect": "欠陥探し", "adversarial": "adversarial verification", "principle": "原則", "spec": "Spec", "design": "設計",
}
ISSUE_REF = re.compile(r"(?:(?P<host>github|gitlab):)?(?P<placement>.+)#(?P<number>\d+)")
# commit message と PR 本文の issue ref。前が英数字・`/`・`!`・`#` の `#N` (URL の断片・MR の `!N`) と、
# 後ろに英数字が続くもの (色の `#123abc`) は引かない
# \w は ASCII に絞る: 日本語の助詞が続く `#12を閉じる` や前に付く `対応#3` も引く
TEXT_ISSUE_REF = re.compile(r"(?<![\w/!#.-])((?:[\w.-]+/)+[\w.-]+)?#(\d+)(?!\w)", re.ASCII)
RE_REVIEW_ITEM_HEAD = (
    "- この差分は前巡のレビューへの修正で、読む範囲は <diff> に写っているものだけです。base より前の commit や、"
    "増分に写っていないコード・文書の file を追加で読まないでください (それ以前は前巡が読み終えています)。"
)
RE_REVIEW_ITEM_TAIL = {
    "principle": "<rules>・<declared-rules> の file は読んでかまいません。<commit-messages> の file は前巡までの commit を含み、全部読んでかまいません",
    "design": "<design-docs> の設計 doc は読んでかまいません",
}
# GitHub と GitLab が PR・MR の差分で生成物として畳む属性
GENERATED_ATTRIBUTES = ("linguist-generated", "gitlab-generated")
EXCLUDED_FILE = "excluded-generated.txt"
RE_REVIEW_LINE = "この差分は前巡のレビューへの修正です。修正が既存の主張を壊していないかを攻めてください"


def _git(*args: str, input: bytes | None = None) -> bytes:
    # bytes のまま扱う: text にすると非 UTF-8 の差分で落ち、CRLF が変わる
    return subprocess.run(
        ["git", "-c", "core.quotePath=false", *args], input=input, check=True, capture_output=True
    ).stdout


def _generated_paths(base: str, head: str) -> list[str]:
    # rename の移動元も数える: 移動先だけを除くと、移動元の削除 hunk に生成物の行が残る
    names = _git("diff", "--name-only", "--no-renames", "-z", f"{base}...{head}")
    # diff の path は repo root 相対で、check-attr は cwd 相対に読む
    top = _git("rev-parse", "--show-toplevel").decode().strip()
    out = _git("-C", top, "check-attr", "-z", "--stdin", *GENERATED_ATTRIBUTES, input=names).split(b"\0")
    triples = zip(out[0::3], out[1::3], out[2::3])
    return sorted({path.decode() for path, _, value in triples if value in (b"set", b"true")})


def _insertions(base: str, head: str) -> dict[str, int]:
    """file ごとの insertions。binary の行 (`-`) は 0 行、rename は移動先の path に数える。"""
    # -z は rename を「insertions\tdeletions\t」の後に移動元と移動先を NUL 区切りで並べる
    fields = iter(_git("diff", "--numstat", "-z", f"{base}...{head}").decode(errors="replace").split("\0"))
    result: dict[str, int] = {}
    for field in fields:
        if not field:
            continue
        added, _, path = field.split("\t", 2)
        if not path:  # rename / copy: 次の 2 つが移動元と移動先
            next(fields, None)
            path = next(fields, "")
        result[path] = 0 if added == "-" else int(added)
    return result


def issue_refs(text: str) -> list[str]:
    """本文から issue ref (`#N` / `<置き場>#N`) を出現順に重複なく引く。"""
    return list(dict.fromkeys(f"{placement or ''}#{number}" for placement, number in TEXT_ISSUE_REF.findall(text)))


def collect(base: str, head: str, log_base: str | None, pr_body: str | None = None) -> dict:
    out = Path(tempfile.mkdtemp(prefix="review-stage-"))
    log_range = f"{log_base or base}..{head}"
    generated = _generated_paths(base, head)
    files = {
        "diff": ("diff.txt", ("diff", f"{base}...{head}", "--", ":/", *(f":(top,exclude,literal){p}" for p in generated))),
        "commits": ("commits.txt", ("log", f"{base}..{head}", "--oneline")),
        "commit_messages": ("commit-messages.txt", ("log", log_range, "--format=medium")),
        "numstat": ("numstat.txt", ("diff", "--numstat", f"{base}...{head}")),
        "changed_files": ("changed-files.txt", ("diff", "--name-only", f"{base}...{head}")),
    }
    result: dict = {"dir": str(out)}
    for key, (name, git_args) in files.items():
        (out / name).write_bytes(_git(*git_args))
        result[key] = str(out / name)
    (out / EXCLUDED_FILE).write_text("".join(f"{p}\n" for p in generated))
    result["insertions"] = _insertions(base, head)
    # commit message はレビュー対象 repo に、PR 本文はその PR が載った置き場に載った文面 (置き場の読み方が違う)。
    # 作業 branch の commit だけから引く (first-parent の merge でない commit): 取り込んだ branch の commit と
    # `Merge pull request #N` の番号は別の PR のもので、この差分の issue ではない
    messages = _git("log", "--first-parent", "--no-merges", "--format=%B", log_range).decode(errors="replace")
    sources = [("commit_message", messages)]
    if pr_body is not None:
        sources.append(("pr_body", Path(pr_body).read_text()))
    result["issue_refs"] = [{"ref": ref, "source": source} for source, text in sources for ref in issue_refs(text)]
    result["notes"] = [f"生成物を除外: {', '.join(generated)} (.gitattributes の {' / '.join(GENERATED_ATTRIBUTES)})"] if generated else []
    return result


@dataclass
class Issue:
    ref: str
    body: str | None
    failure: str | None = None


def _remote_tracker() -> tuple[str | None, str]:
    """remote の host から tracker の種類 (github / gitlab) を決める。決められなければ (None, 理由)。"""
    names = _git("remote").decode().split()
    kinds = set()
    for name in names:
        url = _git("remote", "get-url", name).decode().strip()
        host = re.sub(r"^[\w+.-]+://", "", url).split("/")[0].split("@")[-1].split(":")[0].lower()
        kinds.add("github" if "github" in host else "gitlab" if "gitlab" in host else f"不明な host {host}")
    if len(kinds) == 1 and (kind := next(iter(kinds))) in ("github", "gitlab"):
        return kind, ""
    found = "・".join(sorted(kinds)) if kinds else "remote が無い"
    return None, f"remote の host から tracker を決められない ({found}) — github: か gitlab: を付けて渡す"


def _fetch_issue(ref: str) -> Issue:
    m = ISSUE_REF.fullmatch(ref)
    if m is None:
        raise ValueError(f"--issue の形が違う: {ref} ([github:|gitlab:]<置き場>#<番号>)")
    number, placement, host = m["number"], m["placement"], m["host"]
    if host is None:
        host, failure = _remote_tracker()
        if host is None:
            return Issue(ref, None, failure)
    if host == "github":
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


@functools.cache
def _rules_matcher():
    spec = spec_from_file_location("list_matching_rules", RULES_MATCHER)
    if spec is None or spec.loader is None:
        raise ValueError(f"{RULES_MATCHER} を読めない")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        matcher = _rules_matcher()
        patterns = [matcher.glob_regex(g) for g in matcher.split_globs(globs)]
        if patterns and not any(p.fullmatch(path) for p in patterns for path in changed):
            continue
        if (repo_root / file).is_file():
            declared.append(file)
        else:
            notes.append(f"宣言の file 不在: {file}")
    return declared, notes


def _rule_files(repo_root: Path, changed: list[str]) -> tuple[list[str], list[str]]:
    """差分の file に当たる規約: repo root と祖先 dir の CLAUDE.md と、`paths` が当たる `.claude/rules/`。"""
    dirs = {""} | {parent.as_posix() for path in changed for parent in PurePosixPath(path).parents if parent.as_posix() != "."}
    claude_md = [f"{d}/CLAUDE.md" if d else "CLAUDE.md" for d in sorted(dirs)]
    rules, notes = _rules_matcher().matching_rules(repo_root, changed)
    return [f for f in claude_md if (repo_root / f).is_file()] + rules, notes


def _perspectives(stage: str, round_: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """段階と巡から、この巡に走らせる観点と、次巡 (再レビュー) に走らせる観点。"""
    first, again = PERSPECTIVE_TABLE[stage]
    if round_ == "first":
        return first, again or ()
    if again is None:
        raise ValueError(f"{stage} 段階に再レビューの巡は無い (--stage {stage} --round {round_})")
    return again, ()


def _section_line(names: tuple[str, ...]) -> str:
    return ", ".join(SECTION_NAMES[k] for k in SECTION_NAMES if k in names) or "なし"


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
    diff, commits, commit_messages = out / "diff.txt", out / "commits.txt", out / "commit-messages.txt"
    if not diff.is_file() or not commits.is_file():
        raise ValueError(f"{out} に collect の出力が無い")
    run, next_round = _perspectives(args.stage, args.round)
    if "design" in run and bool(args.design_doc) == bool(args.design_skip):
        raise ValueError("設計を走らせる巡は --design-doc か --design-skip のどちらか一方が要る")

    issues = [_fetch_issue(ref) for ref in args.issue] + [_read_issue_file(s) for s in args.issue_file]
    issues += [Issue(ref, None, reason) for ref, _, reason in (s.partition("=") for s in args.issue_missing)]
    missing = "; ".join(f"{i.ref}: {i.failure}" for i in issues if i.body is None)
    spec = _spec_text(issues)
    notes: dict[str, list[str]] = {name: [] for name in SECTION_NAMES}
    result: dict = {
        "perspectives_line": _section_line(run), "next_round_line": _section_line(next_round),
        "prompts": {}, "spec": None, "claim_sources": None, "notes": notes,
    }
    timing = "初回" if args.round == "first" else "再レビュー"
    for name in SECTION_NAMES:
        if name not in run:
            notes[name].append(f"走らせず: {args.stage} 段階の{timing}では走らせない")

    if spec:
        (out / "spec.md").write_text(spec)
        result["spec"] = str(out / "spec.md")
    if missing and "adversarial" in run:
        notes["adversarial"].append(f"出どころ欠落: {missing}")
    if "spec" in run:
        if not issues:
            notes["spec"].append("Spec skip: issue ref が無い")
        elif not spec:
            notes["spec"].append(f"Spec skip: 本文を取得できなかった: {missing}")
        elif missing:
            notes["spec"].append(f"Spec 源欠落: {missing}")

    excluded = (out / EXCLUDED_FILE).read_text().splitlines() if (out / EXCLUDED_FILE).is_file() else []
    # 除いた path はタグで囲まない: 担い手はタグの中の path を入力の file として Read する
    excluded_line = (
        "対象 repo が生成物と宣言した次の file は <diff> から除いてあります。入力の file ではないので Read しないでください。"
        f"差分に無いことを指摘の根拠にしないでください: {', '.join(excluded)}"
    ) if excluded else ""
    common = {"DIFF": str(diff), "COMMITS": str(commits), "EXCLUDED_LINE": excluded_line}
    adoption = (PROMPTS_DIR / "adoption-rules.md").read_text().rstrip("\n")

    def re_review_item(name: str) -> str:
        return RE_REVIEW_ITEM_HEAD + RE_REVIEW_ITEM_TAIL[name] if args.round == "re-review" else ""

    texts: dict[str, str] = {}
    if "principle" in run:
        shutil.copyfile(RUBRIC, out / "rubric.md")
        repo_root = Path(_git("rev-parse", "--show-toplevel").decode().strip())
        changed = (out / "changed-files.txt").read_text(errors="replace").splitlines()
        rule_files, rule_notes = _rule_files(repo_root, changed)
        declared, declared_notes = _declared_rules(repo_root, changed)
        notes["principle"] += rule_notes + declared_notes
        texts["principle"] = _render("principle", {
            "DIFF": str(diff), "EXCLUDED_LINE": excluded_line, "COMMIT_MESSAGES": str(commit_messages),
            "RUBRIC": str(out / "rubric.md"), "RULE_FILES": "\n".join(rule_files) or "なし",
            "DECLARED_RULES": "\n".join(declared) or "なし",
            "ADOPTION_RULES": adoption, "RE_REVIEW_ITEM": re_review_item("principle"),
        })
    if "spec" in run and spec:
        texts["spec"] = _render("spec", {**common, "SPEC": result["spec"], "ADOPTION_RULES": adoption})
    if args.design_skip and "design" in run:
        notes["design"].append(f"設計 skip: {args.design_skip}")
    if "design" in run and args.design_doc:
        if not CONFORMANCE_LEAF.is_file():
            raise ValueError(f"{CONFORMANCE_LEAF} が無い")
        texts["design"] = _render("design", {
            **common, "DESIGN_DOCS": "\n".join(args.design_doc), "CONFORMANCE_LEAF": str(CONFORMANCE_LEAF),
            "ADOPTION_RULES": adoption, "RE_REVIEW_ITEM": re_review_item("design"),
        })
    if "adversarial" in run:
        pr_body = Path(args.pr_body).read_text() if args.pr_body else None
        claim_sources = out / "claim-sources.md"
        claim_sources.write_text(_claim_sources(commit_messages.read_text(errors="replace"), spec, pr_body))
        result["claim_sources"] = str(claim_sources)
        critical = f"<critical-areas>\n{args.critical_areas}\n</critical-areas>" if args.critical_areas else ""
        texts["adversarial"] = _render("adversarial", {
            "DIFF": str(diff), "EXCLUDED_LINE": excluded_line, "CLAIM_SOURCES": str(claim_sources), "CRITICAL_AREAS_BLOCK": critical,
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
    c.add_argument("--pr-body", help="PR・MR 本文の file。issue ref を引く元に足す")
    p = sub.add_parser("prompts", help="観点ごとの prompt file を組み立てる")
    p.add_argument("--dir", required=True, help="collect が返した dir")
    p.add_argument("--stage", choices=STAGES, required=True, help="この巡の段階。走らせる観点はこれと --round から決まる")
    p.add_argument("--round", choices=ROUNDS, required=True, help="re-review-widened は `増分に絞れず:` の巡")
    p.add_argument("--issue", action="append", default=[], help="[github:|gitlab:]<置き場>#<番号>。host を省くと remote の host で決める")
    p.add_argument("--issue-file", action="append", default=[], help="<ref>=<本文の file>。CLI で取れない tracker の本文")
    p.add_argument("--issue-missing", action="append", default=[], help="<ref>=<理由>。呼び出し元が本文を取得できなかった ref")
    p.add_argument("--design-doc", action="append", default=[], help="<置き場> | <索引>")
    p.add_argument("--design-skip", help="設計を走らせる巡で、当たる設計 doc が無い理由")
    p.add_argument("--critical-areas", help="攻撃役へ渡す重要領域の名前")
    p.add_argument("--pr-body", help="PR・MR 本文の file")
    args = ap.parse_args(argv)
    try:
        result = collect(args.base, args.head, args.log_base, args.pr_body) if args.command == "collect" else prompts(args)
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
