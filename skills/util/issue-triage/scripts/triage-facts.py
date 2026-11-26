#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""issue の取得結果から、振り分けと反映の条件に使う label とコメントの事実を抜き出す。

usage: triage-facts.py <正規化した取得結果の file> [--root <対象 repo の root>]

入力は `issue-tracker-operations` の `scripts/issue-fetch.py read` の出力 (共通の鍵の JSON) を保存した file。
label の綴りは `<root>/docs/agents/triage-labels.md` の表の行 (1 列目に名前 (backtick は任意)、2 列目に backtick で囲んだ綴り) から引く
(`--root` の既定は cwd)。file が無いか表の行が 1 つも無ければ、どの label も名前をそのまま綴りにする。表があるときは、
表に行の無い既定の label (5 つの状態の label と `wontfix`) を名前どおりの綴りで読み、行の無い repo が足す label
(`needs-grilling`・`tracking-issue`) は無いものとする。

## 出力 (stdout に JSON)

- `labels_declaration`: `読めた` / `読めず (既定の名前)`
- `tracking_issue`: `tracking-issue` に当たる label が付いているか
- `state_labels`: 付いている状態の label の名前 (`needs-triage`・`needs-info`・`needs-grilling`・`ready-for-agent`・
  `ready-for-human` の順)
- `only_ready_for_agent`: 状態の label が `ready-for-agent` だけか
- `overturned_comments`: 1 行目が `覆した判断:` で始まるコメントの位置 (`index` は入力の comments の 0 始まり、
  `author`・`created_at` を添える)
- `grilling_handoff_comment`: 1 行目が深掘りの手順コメントの固定の文字列と同じコメントがあるか
- `needs_grilling_label`: `needs-grilling` に当たる label の綴り。表があって行が無ければ null

## 終了コード

- 0: 出力した
- 2: 入力の file を読めないか、`number`・`labels`・`comments` の鍵を欠くか、1 列目が既知の名前の表の行を読めない。
  stdout に何も出さない

invariant:

1. 状態の label は宣言の綴りで照らし、名前で出す
2. 宣言が無いときは、どの label も名前をそのまま綴りにする
3. 入力が `number`・`labels`・`comments` の鍵を欠けば exit 2 で終える
4. 1 列目が既知の名前なのに読めない表の行があれば exit 2 で終える (その label が黙って抜けるため)
5. `STATE_LABELS` は `issue-tracker-operations` の `references/state-label.md` の状態の label の写し
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

STATE_LABELS = ("needs-triage", "needs-info", "needs-grilling", "ready-for-agent", "ready-for-human")
DEFAULT_NAMES = ("needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "wontfix")
REPO_LOCAL_NAMES = ("needs-grilling", "tracking-issue")
OVERTURNED = "覆した判断:"
# references/grilling-handoff.md の雛形の 1 行目
GRILLING_HANDOFF = "深掘りのセッションへの手順 (issue の仕様ではない)"
ROW = re.compile(
    r"^\|\s*`?(" + "|".join(re.escape(n) for n in (*DEFAULT_NAMES, *REPO_LOCAL_NAMES)) + r")`?\s*\|\s*`([^`]+)`\s*\|"
)
KNOWN_ROW = re.compile(r"^\|\s*`?(" + "|".join(re.escape(n) for n in (*DEFAULT_NAMES, *REPO_LOCAL_NAMES)) + r")`?\s*\|")


class Unusable(Exception):
    """入力か宣言が使えない。exit 2 で返す。"""


def declared_labels(root: Path) -> dict[str, str] | None:
    path = root / "docs" / "agents" / "triage-labels.md"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    rows = {}
    for line in text.splitlines():
        m = ROW.match(line)
        if m:
            rows[m[1]] = m[2]
        elif KNOWN_ROW.match(line):
            raise Unusable(f"{path} の表の行を読めない: {line}")
    return rows or None


def first_line(body: str) -> str:
    return (body or "").lstrip().splitlines()[0].strip() if (body or "").strip() else ""


def facts(issue: dict, declared: dict[str, str] | None) -> dict:
    if declared is None:
        names = {n: n for n in (*DEFAULT_NAMES, *REPO_LOCAL_NAMES)}
    else:
        names = {**{n: n for n in DEFAULT_NAMES}, **declared}
    attached = set(issue["labels"])
    states = [n for n in STATE_LABELS if n in names and names[n] in attached]
    comments = issue["comments"]
    return {
        "labels_declaration": "読めた" if declared is not None else "読めず (既定の名前)",
        "tracking_issue": "tracking-issue" in names and names["tracking-issue"] in attached,
        "state_labels": states,
        "only_ready_for_agent": states == ["ready-for-agent"],
        "overturned_comments": [
            {"index": i, "author": c.get("author"), "created_at": c.get("created_at")}
            for i, c in enumerate(comments)
            if first_line(c.get("body", "")).startswith(OVERTURNED)
        ],
        "grilling_handoff_comment": any(first_line(c.get("body", "")) == GRILLING_HANDOFF for c in comments),
        "needs_grilling_label": names.get("needs-grilling"),
    }


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file", type=Path, help="issue-fetch.py read の出力を保存した file")
    p.add_argument("--root", type=Path, default=None, help="対象 repo の root (既定は cwd)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        issue = json.loads(args.file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"読めない: {args.file} ({e})", file=sys.stderr)
        return 2
    if not isinstance(issue, dict) or any(k not in issue for k in ("number", "labels", "comments")):
        print(f"読めない: {args.file} は number・labels・comments の鍵を持つ object でない", file=sys.stderr)
        return 2
    try:
        declared = declared_labels(args.root or Path.cwd())
    except Unusable as e:
        print(f"読めない: {e}", file=sys.stderr)
        return 2
    print(json.dumps(facts(issue, declared), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
