#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""write-for-harness 手順 4 の検証 subagent と構造 reviewer の最終メッセージを読み、扱いを tab 区切りで stdout へ出す。

    classify-review-reply.py <検証|構造> <最終メッセージの file> [--retried]

前後の文を外して JSON object を取り出し、期待した鍵 (検証は `findings`、構造は `structural_proposals`) があるかを見る。

| 返答 | 判定 |
|---|---|
| 期待した鍵がある | `得た` |
| `unread_refs` に実在しない path がある (`${CLAUDE_SKILL_DIR}` は本 skill の絶対 path に置き換えて確かめる) | `未実施` (やり直しても同じ返答になる) |
| `unread_refs` の path がすべて実在する / 期待した鍵が無い・JSON が無い | 1 回目は `やり直す`、`--retried` (やり直した後の返答) なら `未実施` |

出力の行:

- `判定\t<得た|やり直す|未実施>`
- `未実施` のとき: `未実施行\t<検証|構造>レビュー未実施: <理由>` と `次の手順\t<文>`
- `未レビュー\t<path>`: `unread_targets` に挙がった file
- `判断材料を欠いた file\t<path>`: `unread_inputs` に挙がった file
- `構造提案\t<番号 (1 から)>\timpact=<値>\tmigration_cost=<値>\t<採用候補|採用候補でない|判定不能>`:
  high > medium > low で比べ、impact が migration_cost を上回る提案を採用候補にする

invariant:

1. 最終メッセージの file が読めなければ stderr へ 1 行出して exit 1
2. 判定できたら、判定が `未実施` でも exit 0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(os.path.abspath(__file__)).parent.parent
EXPECTED_KEY = {"検証": "findings", "構造": "structural_proposals"}
LEVELS = {"low": 0, "medium": 1, "high": 2}
NEXT_MISSING = "挙がった path の欠落か書き誤りを直してから、その subagent だけをやり直す"
NEXT_UNREAD_AGAIN = "挙がった path を Read できるかと、prompt に `${` が残っていないかを確かめてから、その subagent だけをやり直す"
NEXT_OTHER = "その subagent だけをやり直す"


def extract_json(text: str, key: str) -> dict | None:
    """text の中の JSON object。期待した鍵か unread_refs を持つものを優先し、無ければ最初の object。"""
    decoder = json.JSONDecoder()
    found: list[dict] = []
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            if key in obj or "unread_refs" in obj:
                return obj
            found.append(obj)
    return found[0] if found else None


def _paths(value: object) -> list[str]:
    """path の list を期待する欄。文字列 1 つは 1 件にし (1 文字ごとに回さない)、ほかの形は判定不能の 1 件にする
    (黙って空にすると、読めなかった file が出力から消える)。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return [f"判定不能 (値の形: {type(value).__name__})"]


def adoption(proposal: object) -> str:
    if not isinstance(proposal, dict):
        return "判定不能"
    impact, cost = LEVELS.get(str(proposal.get("impact"))), LEVELS.get(str(proposal.get("migration_cost")))
    if impact is None or cost is None:
        return "判定不能"
    return "採用候補" if impact > cost else "採用候補でない"


def classify(kind: str, text: str, retried: bool) -> list[str]:
    key = EXPECTED_KEY[kind]
    reply = extract_json(text, key) or {}
    lines: list[str] = []
    if key in reply:
        lines.append("判定\t得た")
    else:
        refs = _paths(reply.get("unread_refs"))
        missing = [r for r in refs if not Path(r.replace("${CLAUDE_SKILL_DIR}", str(SKILL_DIR))).exists()]
        if missing:
            reason, step = f"reference が実在しない: {', '.join(missing)}", NEXT_MISSING
        elif refs:
            reason, step = f"やり直しても reference を読めなかった: {', '.join(refs)}", NEXT_UNREAD_AGAIN
        else:
            reason, step = f"やり直しても `{key}` を持つ JSON が返らなかった", NEXT_OTHER
        if missing or retried:
            lines += ["判定\t未実施", f"未実施行\t{kind}レビュー未実施: {reason}", f"次の手順\t{step}"]
        else:
            lines.append("判定\tやり直す")
    lines += [f"未レビュー\t{p}" for p in _paths(reply.get("unread_targets"))]
    lines += [f"判断材料を欠いた file\t{p}" for p in _paths(reply.get("unread_inputs"))]
    proposals = reply.get("structural_proposals")
    for n, proposal in enumerate(proposals if isinstance(proposals, list) else [], start=1):
        shown = proposal if isinstance(proposal, dict) else {}
        lines.append(
            f"構造提案\t{n}\timpact={shown.get('impact')}\tmigration_cost={shown.get('migration_cost')}\t{adoption(proposal)}"
        )
    return lines


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("kind", choices=sorted(EXPECTED_KEY))
    p.add_argument("reply", type=Path, help="subagent の最終メッセージを書いた file")
    p.add_argument("--retried", action="store_true", help="やり直した後の返答を渡す")
    args = p.parse_args(argv)
    try:
        text = args.reply.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"classify-review-reply: {args.reply} を読めない: {exc}", file=sys.stderr)
        return 1
    print("\n".join(classify(args.kind, text, args.retried)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
