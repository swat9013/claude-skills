"""HerdrAdapter — herdr (AI agent 向け terminal multiplexer) の CLI 境界。

`pane.PanePort` の継ぎ目 method を herdr CLI への shell out で実装する。認証も接続も
CLI に委譲し、本 module は「呼び出しと応答の写像」だけを持つ (spec §2)。

`skills/util/_herdr_lib.py` とは**共有しない** (issue #404 / spec §8)。同じ CLI 境界を
2 箇所に持つのは重複だが、旧 CLI (`skills/util/issue-dispatch/scripts/herdr_ops.py`) は
#408 まで併存し、その間 inventory-dispatch も同じ共有 module を使っている。撤去前に
共有すると「server 側の都合で inventory-dispatch が壊れる」結合を、撤去のためだけに
作ることになる (ADR 0013 の共有可基準: 変更時に全消費者の同時更新が必須なもの、に
当たらない — 本 server は自分の都合だけで境界を変えたい側)。

herdr CLI の失敗 (非 0 exit / 非 JSON 応答) は `PaneError` として即座に表面化させる。
「CLI の実行失敗」を「pane 無し」「agent 終了」と誤読させると、全 pane を誤って回収し
assignee まで外すことになる。

server プロセスは harness が起動するので、herdr の環境変数 (`HERDR_ENV` /
`HERDR_WORKSPACE_ID` / `HERDR_PANE_ID`) は起動元セッションの pane から継承される
前提。継承されていなければ preflight が fail-closed で止める。
"""

import json
import os
import re
import shutil
import time

import pane
import proc
import refs

BACKEND = "herdr"

SUBPROCESS_TIMEOUT_SEC = 60

# `herdr integration status` の出力に立つべき行 (hook が現行版であること)
HOOK_CURRENT = re.compile(r"^claude: current", re.MULTILINE)

# 自 pane に前回 dispatch の残骸 label (`i<N>`) が残っていたときの付け直し先。
# 残骸を放置すると、自分の pane が「issue N を作業中のセッション」として数えられ、
# slot を 1 消費し当該 issue を二重に起動しない側へ倒す誤除外を同時に起こす
SELF_RENAME_TO = "dispatch"

# 起動後に agent が検出されるまでの poll (screen manifest の検出に数秒要る)
POLL_ATTEMPTS = 5
POLL_INTERVAL_SEC = 2

# subprocess の sleep 継ぎ目 (テストが差し替える)
_sleep = time.sleep


# herdr CLI の起動境界。テストはこの名前を monkeypatch する
run_command = proc.command_runner(error=pane.PaneError, timeout_sec=SUBPROCESS_TIMEOUT_SEC)


def run_herdr(args):
    """herdr subcommand を実行して JSON body を返す。成功時 body 空 (pane run 等) は None。"""
    rc, out, err = run_command(["herdr", *args])
    if rc != 0:
        raise pane.PaneError(f"herdr {args[0]} failed (exit {rc}): {err.strip()}")
    if not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise pane.PaneError(f"herdr {args[0]} returned non-JSON stdout: {exc}") from exc


def pane_of(payload):
    return (payload or {}).get("result", {}).get("pane", {})


def panes_of(payload):
    return (payload or {}).get("result", {}).get("panes", [])


def require_env(name):
    value = os.environ.get(name)
    if not value:
        raise pane.PaneError(f"{name} が未設定 (herdr session 外で server が起動している)")
    return value


class HerdrAdapter(pane.PanePort):
    """herdr CLI で pane を観測・操作する adapter。"""

    backend = BACKEND

    def __init__(self):
        # 検査の成功だけを覚える。失敗を覚えると、socket が復旧しても以後ずっと
        # 落ち続ける (herdr の再起動で直る種類の失敗を恒久化しない)
        self._ready = None

    # --- 前提検査 -----------------------------------------------------------------

    def ensure_ready(self):
        """3 段検査 + 自 pane の残骸 label 正規化 (プロセス内で 1 回だけ通す)。

        検査は (1) `HERDR_ENV=1` (2) `herdr integration status` に `claude: current` 行
        (3) `herdr status` の socket 疎通、の順に行い最初の失敗で止める (fail-closed)。
        順序は「環境変数 → hook の版 → 実際の疎通」と原因の粒度が粗い側から並べてあり、
        呼び出し側が failed の値だけで直し方を選べる。
        """
        if self._ready is not None:
            return self._ready

        if os.environ.get("HERDR_ENV") != "1":
            raise pane.PaneError(
                "HERDR_ENV=1 でない (herdr session の外で server が起動している。"
                "herdr session 内で Claude Code を起動し直す)"
            )
        rc, out, _err = run_command(["herdr", "integration", "status"])
        if rc != 0 or not HOOK_CURRENT.search(out):
            raise pane.PaneError(
                "herdr integration status に `claude: current` が無い (hook が古い / 未導入)"
            )
        rc, _out, err = run_command(["herdr", "status"])
        if rc != 0:
            raise pane.PaneError(f"herdr status が失敗 (socket に届かない): {err.strip()}")

        self_id = require_env("HERDR_PANE_ID")
        workspace = require_env("HERDR_WORKSPACE_ID")
        detail = pane_of(run_herdr(["pane", "get", self_id]))
        label = detail.get("label")
        renamed_from = None
        if label and refs.parse_issue_slug(label) is not None:
            detail = pane_of(run_herdr(["pane", "rename", self_id, SELF_RENAME_TO]))
            renamed_from, label = label, detail.get("label")
        self._ready = {
            "backend": self.backend,
            "workspace": workspace,
            "self_pane_id": self_id,
            "self_label": label,
            "renamed_from": renamed_from,
        }
        return self._ready

    # --- 継ぎ目 -------------------------------------------------------------------

    def self_pane_id(self):
        """自 pane の id (server プロセスを起動したセッションの pane)。"""
        return os.environ.get("HERDR_PANE_ID")

    def list_panes(self):
        """自 workspace の pane 一覧。

        `--workspace` を明示する — 省略すると全 workspace の pane が返り、別 project の
        issue 番号と衝突した label を自分の追跡対象として拾う。
        """
        workspace = require_env("HERDR_WORKSPACE_ID")
        payload = run_herdr(["pane", "list", "--workspace", workspace])
        return [
            {"pane_id": entry.get("pane_id"), "label": entry.get("label")}
            for entry in panes_of(payload)
        ]

    def get_pane(self, pane_id):
        """pane 1 件の詳細。agent field の欠落は port 側が検知するのでここでは触らない。

        label も欠落時は key ごと落とす。port は `pane list` の結果へこの応答を重ねる
        ので、`None` を載せると list 側で読めていた label を打ち消す — 追跡 pane
        (`i<N>`) が追跡外に化け、agent field の検査ごと素通りする。
        """
        detail = pane_of(run_herdr(["pane", "get", pane_id]))
        mapped = {
            "pane_id": detail.get("pane_id", pane_id),
            "agent_status_raw": detail.get("agent_status") or pane.UNKNOWN_RAW_STATUS,
        }
        for field in ("label", "agent"):
            if field in detail:
                mapped[field] = detail[field]
        return mapped

    def resolve_agent_bin(self):
        """claude binary の絶対パス。

        絶対パスへ解決するのは、pane を割った先の shell が別の PATH を持ちうるため
        (対話 shell の rc で PATH を組む環境では `claude` が見えない)。
        """
        return shutil.which(pane.AGENT_BIN)

    def launch_pane(self, command, cwd, label):
        """pane split → label 付与 → command 実行 → agent 検出 poll → (pane_id, agent)。

        agent が検出できなくても例外にしない — pane は既に在るので、呼び出し側が
        pane_id を診断 (と後始末) に使えるほうが安い。
        """
        created = pane_of(
            run_herdr(
                [
                    "pane",
                    "split",
                    "--current",
                    "--direction",
                    "right",
                    "--no-focus",
                    "--cwd",
                    cwd,
                ]
            )
        )
        pane_id = created["pane_id"]
        run_herdr(["pane", "rename", pane_id, label])
        run_herdr(["pane", "run", pane_id, command])

        agent = None
        for _attempt in range(POLL_ATTEMPTS):
            _sleep(POLL_INTERVAL_SEC)
            agent = pane_of(run_herdr(["pane", "get", pane_id])).get("agent")
            if agent == pane.AGENT_BIN:
                break
        return pane_id, agent

    def close_pane(self, pane_id):
        run_herdr(["pane", "close", pane_id])

    def send_text(self, pane_id, text):
        """テキスト送出 + Enter で submit する。

        `send-text` 単体には submit 手段が無いので Enter key を別途送る (送りっぱなしだと
        入力欄に文字が残ったまま agent は動かない)。
        """
        run_herdr(["pane", "send-text", pane_id, text])
        run_herdr(["pane", "send-keys", pane_id, "enter"])
