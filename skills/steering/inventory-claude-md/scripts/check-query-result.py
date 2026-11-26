#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""inventory-claude-md の手順 1b・1c が呼んだ named query の返り値が取れたかを判定する。

入力:

- `--query`: `overhead` (手順 1b) か `read-sessions` (手順 1c)
- `--result`: `transcript-query` が返した JSON (stdout) をそのまま書いた file。答えたときは、
  その `path` が指す成果物も読む

出力 (stdout の JSON):

- `obtained`: 取れたか
- `not_obtained`: 取れなかった理由の配列 (取れたら空)。`stopped: <種類>` (`empty_window` を含む。
  返り値の `stopped` 以外の値 (`next_action`・`error_type`・`missing_paths` など) を言い換えずに続ける)・`sessions_observed が 0`・
  `memory_files[] が 0 件` (`overhead` だけ)
- `zero_not_evidence`: 0 件を証拠に使えない値。`degradation` は劣化の件数のうち 0 でないもの
  (`{名前: 件数}`)、`snapshot_not_checked` は返り値にあればその値、無ければ null。
  `absent_optional_paths` は問いが読む path かの判断が要るので、ここに含めない

invariant:

1. `stopped` がある返り値は取れなかったとし、成果物を読まない
2. `stopped` が無く、`path` か `degradation` を欠く返り値は判定せず、非 0 で終わる
   (答えの無い返り値を取れたと読まない・劣化を黙って 0 にしない)
3. `degradation` のうち劣化でない件数 (`NOT_DEGRADATION`) は数えない。`NOT_DEGRADATION` は transcript-query の
   「返り値の読み方」が劣化でないとする名前の写しで、一致はテストが正本の文と突き合わせる

呼び直すかは判定しない。判定に渡すのは呼び直した後の返り値。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# degradation に載るが劣化の件数ではない値 (読んだ量)
NOT_DEGRADATION = frozenset({"files_read", "records", "workflow_journal_files"})


class UnjudgeableResult(ValueError):
    """返り値が答えも止まった理由も持たない。"""


def _overhead_reasons(artifact: dict[str, Any]) -> list[str]:
    row = artifact["rows"][0]
    return [reason for reason, hit in (("sessions_observed が 0", row["sessions_observed"] == 0),
                                       ("memory_files[] が 0 件", not row["memory_files"])) if hit]


def _read_sessions_reasons(artifact: dict[str, Any]) -> list[str]:
    return ["sessions_observed が 0"] if artifact["meta"]["sessions_observed"] == 0 else []


# 問いごとに、答えた成果物から取れなかった理由を出す
ARTIFACT_REASONS = {"overhead": _overhead_reasons, "read-sessions": _read_sessions_reasons}


def _stopped_reason(result: dict[str, Any]) -> str:
    details = [f"{key}: {json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value}"
               for key, value in result.items() if key != "stopped"]
    return " / ".join([f"stopped: {result['stopped']}", *details])


def not_obtained(query: str, result: dict[str, Any]) -> list[str]:
    if "stopped" in result:
        return [_stopped_reason(result)]
    missing = [key for key in ("path", "degradation") if key not in result]
    if missing:
        raise UnjudgeableResult(f"返り値に stopped が無く、{', '.join(missing)} もありません。"
                                "transcript-query の返り値をそのまま渡してください")
    artifact = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    return ARTIFACT_REASONS[query](artifact)


def judge(query: str, result: dict[str, Any]) -> dict[str, Any]:
    reasons = not_obtained(query, result)
    degradation = {
        name: count for name, count in result.get("degradation", {}).items()
        if name not in NOT_DEGRADATION and count
    }
    return {
        "obtained": not reasons,
        "not_obtained": reasons,
        "zero_not_evidence": {
            "degradation": degradation,
            "snapshot_not_checked": result.get("snapshot_not_checked"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="inventory-claude-md の手順 1b・1c の named query の返り値が取れたかを判定する。")
    parser.add_argument("--query", choices=sorted(ARTIFACT_REASONS), required=True)
    parser.add_argument("--result", type=Path, required=True, help="transcript-query が返した JSON をそのまま書いた file")
    args = parser.parse_args(argv)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    try:
        judged = judge(args.query, result)
    except UnjudgeableResult as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(judged, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
