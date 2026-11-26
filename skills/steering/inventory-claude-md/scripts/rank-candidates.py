#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""inventory-claude-md の候補の est_tokens を合算し、bucket ごとの提示順を出す。

入力:

- `--observation`: `scan-claude-md.py` の observation JSON
- `--candidates`: 候補の JSON 配列の file。要素は `{"bucket": <bucket>, "unit": "<file>:<開始行>[-<終了行>]"}`。
  `<file>` は observation の `sources` の各 file の `path` と同じ表記

出力 (stdout の JSON): `buckets` は bucket が入力に最初に現れた順の配列で、各要素は `bucket` と
`candidates`。`candidates` は `unit` と `est_tokens` の組を est_tokens の降順に並べる。
est_tokens は、その file の `token_cost.per_line_est_tokens` の line-range の行の合算。

invariant:

1. line-range は両端を含む 1 始まりの行番号で、終了行を省くと 1 行
2. observation に無い file・file の行数の外の行・形の崩れた unit・`bucket` か `unit` が文字列でない要素が 1 つでもあれば、
   出力せずに非 0 で終わる (概算を黙って 0 にしない)
3. 同じ est_tokens の候補は入力の順を保つ

bucket は判定せず、入力の値をそのまま束ねる。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

UNIT_RE = re.compile(r"^(?P<file>.+):(?P<start>\d+)(?:-(?P<end>\d+))?$")


class CandidateError(ValueError):
    """候補が object の配列でないか、候補の unit が observation の行に当たらない。"""


def per_line_tokens(observation: dict[str, Any]) -> dict[str, list[int]]:
    """file の path から行単位の概算 token への対応。不在・読めない file は載せない。"""
    claude_md = observation["sources"]["claude_md"]
    files = [claude_md["root"], claude_md["local"], *claude_md["subdirs"], *observation["sources"]["rules"]]
    return {f["path"]: f["token_cost"]["per_line_est_tokens"] for f in files if f["status"] == "present"}


def est_tokens(unit: str, tokens: dict[str, list[int]]) -> int:
    m = UNIT_RE.match(unit)
    if not m:
        raise CandidateError(f"unit は <file>:<開始行>[-<終了行>] の形で渡してください: {unit}")
    path, start = m.group("file"), int(m.group("start"))
    end = int(m.group("end") or start)
    if path not in tokens:
        raise CandidateError(f"observation に無い file です: {unit}")
    per_line = tokens[path]
    if not 1 <= start <= end <= len(per_line):
        raise CandidateError(f"file の行 (1-{len(per_line)}) の外です: {unit}")
    return sum(per_line[start - 1:end])


def rank(observation: dict[str, Any], candidates: list[dict[str, str]]) -> dict[str, Any]:
    tokens = per_line_tokens(observation)
    if not isinstance(candidates, list):
        raise CandidateError("候補は JSON の配列で渡してください")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        if not (isinstance(c, dict) and isinstance(c.get("bucket"), str) and isinstance(c.get("unit"), str)):
            raise CandidateError(f"候補は bucket と unit を文字列で持つ object で渡してください: {json.dumps(c, ensure_ascii=False)}")
        grouped.setdefault(c["bucket"], []).append({"unit": c["unit"], "est_tokens": est_tokens(c["unit"], tokens)})
    return {"buckets": [
        {"bucket": bucket, "candidates": sorted(items, key=lambda item: -item["est_tokens"])}
        for bucket, items in grouped.items()
    ]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="inventory-claude-md の候補の est_tokens と bucket ごとの提示順を出す。")
    parser.add_argument("--observation", type=Path, required=True, help="scan-claude-md.py の observation JSON")
    parser.add_argument("--candidates", type=Path, required=True,
                        help='候補の JSON 配列の file。要素は {"bucket": ..., "unit": "<file>:<開始行>[-<終了行>]"}')
    args = parser.parse_args(argv)
    observation = json.loads(args.observation.read_text(encoding="utf-8"))
    candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
    try:
        result = rank(observation, candidates)
    except CandidateError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
