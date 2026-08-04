"""dispatch skill 群の herdr CLI 境界 (pure module)。

`issue-dispatch` (`herdr_ops.py`) と `inventory-dispatch` (`inventory_herd_ops.py`)
に逐語コピーされていた subprocess 境界と 3 段 preflight を集約する (#389)。両者は
herdr CLI の exit 規約・JSON 応答形・検査順序という「変更時に全消費者の同時更新が
必須」な対象を共有しており、ADR 0013 の共有可基準に当たる。underscore 始まりの
ファイル名は「直接実行しない pure module」を表す (CLI entry point ではない)。

**現在の消費側は `inventory_herd_ops.py` のみ** — issue-dispatch 側は MCP server
(`mcp/dispatch-ops/pane_herdr.py` が独自の herdr 境界を持つ) へ移り、旧 CLI
`herdr_ops.py` は #408 で撤去した。本 module の正本テストは
`tests/test_herdr_lib.py` (消費側から独立させてある)。

共有スコープ: HerdrError / run_command / resolve_claude_bin / _run_herdr / _pane_of
/ _require_env / preflight。**意図的に共有しないもの**: label 語彙 (`i<N>` /
`inv-<target>`)・prompt template・agent 検出の poll ループ (`_sleep` seam と
POLL_* 定数を各 script が持つ)。preflight の label 語彙依存は引数化して剥がす。

**消費側は whole-module import (`import _herdr_lib` + `_herdr_lib.run_command(...)`)
で呼ぶこと。** `from _herdr_lib import run_command` で消費 module 側に名前を束ねると、
テストが消費 module へ当てた monkeypatch が本 module 内部の呼び出しを素通りし、
実 herdr CLI へ落ちる — しかも失敗せず緑のまま通る (旧 in-code コメントが共有化を
拒否した論拠はこの形で解消する)。例外クラス HerdrError だけは patch 対象でないため
from-import してよい。
"""

import json
import os
import re
import shutil
import subprocess

HOOK_CURRENT = re.compile(r"^claude: current", re.MULTILINE)
SUBPROCESS_TIMEOUT_SEC = 60


class HerdrError(Exception):
    """herdr CLI の失敗 (非 0 exit / 出力 parse 不能) と前提不成立。"""


def run_command(argv, timeout=SUBPROCESS_TIMEOUT_SEC):
    """subprocess 境界。stdout と stderr を分離したまま返す (テストで monkeypatch する)。"""
    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )
    return proc.returncode, proc.stdout, proc.stderr


def resolve_claude_bin():
    """claude binary の絶対パス解決 (テストで monkeypatch する)。"""
    return shutil.which("claude")


def _run_herdr(args):
    """herdr subcommand を実行して JSON body を返す。成功時 body 空 (pane run 等) は None。"""
    rc, out, err = run_command(["herdr", *args])
    if rc != 0:
        raise HerdrError(f"herdr {args[0]} failed (exit {rc}): {err.strip()}")
    if not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise HerdrError(f"herdr {args[0]} returned non-JSON stdout: {exc}") from exc


def _pane_of(payload):
    return (payload or {}).get("result", {}).get("pane", {})


def _require_env(name):
    value = os.environ.get(name)
    if not value:
        raise HerdrError(f"{name} が未設定 (herdr session 外か)")
    return value


def preflight(stale_label, rename_to):
    """3 段検査を順に通し、自 pane の残骸 label を正規化する。

    最初の失敗で止めて failed に検査名を返す (fail-closed)。skill 側は failed の
    値に応じて user への修正依頼文を選ぶ。

    stale_label は「前回 dispatch の残骸」と判定する compiled regex、rename_to は
    剥がした後に付け直す label で、いずれも呼び出し側の語彙 (どちらも dispatcher
    ごとに別物)。残骸 label を放置すると自 pane が稼働中セッションとして数えられ、
    誤除外・誤 duplicate 判定の事故源になる (具体的な事故の形は各 caller の docstring)。
    """
    result = {"ok": False, "failed": None, "checks": {}, "self": None, "workspace": None}

    result["checks"]["herdr_env"] = os.environ.get("HERDR_ENV") == "1"
    if not result["checks"]["herdr_env"]:
        result["failed"] = "herdr_env"
        return result

    rc, out, _err = run_command(["herdr", "integration", "status"])
    result["checks"]["hook"] = rc == 0 and bool(HOOK_CURRENT.search(out))
    if not result["checks"]["hook"]:
        result["failed"] = "hook"
        return result

    rc, _out, _err = run_command(["herdr", "status"])
    result["checks"]["socket"] = rc == 0
    if not result["checks"]["socket"]:
        result["failed"] = "socket"
        return result

    self_id = _require_env("HERDR_PANE_ID")
    result["workspace"] = _require_env("HERDR_WORKSPACE_ID")
    pane = _pane_of(_run_herdr(["pane", "get", self_id]))
    label = pane.get("label")
    renamed_from = None
    if label and stale_label.match(label):
        pane = _pane_of(_run_herdr(["pane", "rename", self_id, rename_to]))
        renamed_from, label = label, pane.get("label")
    result["self"] = {"pane_id": self_id, "label": label, "renamed_from": renamed_from}
    result["ok"] = True
    return result
