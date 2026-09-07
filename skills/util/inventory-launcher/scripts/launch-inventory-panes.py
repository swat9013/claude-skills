#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""inventory 系 skill を herdr の分割 pane へ独立 Claude Code セッションとして起動する。

対象 target ごとに `herdr pane split` → `rename` → `run` を撃ち、agent が立ったことを
確かめて結果 JSON を stdout へ出す。本 script が持つのは機械的に決まる判断
(label が占有済みか / 起動できたか / 残骸を畳むか) までで、**人へ何を頼むかは呼び出し元の
skill が決める** — `reason` は分岐用の語彙として出し、文面は付けない。

herdr の socket connect は sandbox 内から通らないため、本 script は
`sandbox.excludedCommands` へ登録した tilde path で literal に起動される前提で動く。

前提が 1 つでも欠けたら**どの target も起動せずに** exit 1 で止める (fail-closed)。
半分起動した状態で止めると、次の実行で何が残骸かを人が判別できなくなる。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

# pane 内で起動する実行ファイル名
AGENT_BIN = "claude"

# herdr が検出した agent 種別として pane に載る値。AGENT_BIN と綴りが同じなのは偶然なので、
# 一方が変わったときに他方を巻き込まないよう別の定数で持つ
HERDR_AGENT_KIND = "claude"

# target 名 → (起動する skill 名, pane label)
TARGETS = {
    "permissions": ("inventory-permissions", "inv-permissions"),
    "claude-md": ("inventory-claude-md", "inv-claude-md"),
    "project-values": ("inventory-project-values", "inv-project-values"),
}

# 各 pane へ渡す初期指示。skill 名だけを差し替える
PROMPT_TEMPLATE = "/swat-skills:{skill} を実行する。skill の手順に完全に従うこと"

# `herdr integration status` に立つべき行 (Claude 連携 hook が現行版であること)
HOOK_CURRENT = re.compile(r"^claude: current", re.MULTILINE)

SUBPROCESS_TIMEOUT_SEC = 60

# 起動後に agent が検出されるまでの poll (screen manifest の検出に数秒要る)
POLL_ATTEMPTS = 5
POLL_INTERVAL_SEC = 2


class LauncherError(Exception):
    """前提不成立および herdr CLI の失敗。握りつぶさず即座に表面化させる。"""


def run_command(args: list[str]) -> tuple[int, str, str]:
    """subprocess の継ぎ目 (テストが差し替える)。

    timeout は `LauncherError` へ畳む — 素の `TimeoutExpired` を上げると呼び出し側の
    except を素通りし、JSON を出さずに traceback で終わる (起動済み pane が報告に載らない)。
    """
    try:
        done = subprocess.run(
            args, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC
        )
    except subprocess.TimeoutExpired as exc:
        raise LauncherError(
            f"{' '.join(args)} が {SUBPROCESS_TIMEOUT_SEC} 秒で応答しなかった"
        ) from exc
    return done.returncode, done.stdout, done.stderr


def sleep(seconds: float) -> None:
    """poll の待ち (テストが差し替える)。"""
    time.sleep(seconds)


def run_herdr(args: list[str]) -> dict | None:
    """herdr subcommand を実行して JSON body を返す。body が空なら None。"""
    code, out, err = run_command(["herdr", *args])
    if code != 0:
        raise LauncherError(f"herdr {' '.join(args)} が失敗 (exit {code}): {err.strip()}")
    if not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise LauncherError(f"herdr {' '.join(args)} の stdout が JSON でない: {exc}") from exc


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise LauncherError(f"{name} が未設定 (herdr session 外で起動している)")
    return value


def preflight() -> dict[str, str]:
    """起動の前提を順に検査し、最初の失敗で止める (fail-closed)。

    順序は「環境変数 → hook の版 → 実際の疎通 → 起動対象の実在」と原因の粒度が粗い側から
    並べてある。呼び出し元は失敗した項目だけで直し方を選べる。
    """
    if os.environ.get("HERDR_ENV") != "1":
        raise LauncherError(
            "HERDR_ENV=1 でない (herdr session 内で Claude Code を起動し直す)"
        )
    self_pane_id = require_env("HERDR_PANE_ID")
    workspace = require_env("HERDR_WORKSPACE_ID")
    code, out, _err = run_command(["herdr", "integration", "status"])
    if code != 0 or not HOOK_CURRENT.search(out):
        # 人間可読な出力の文言に依存する検査なので、herdr 側で文言が変わると
        # 「hook が無い」と読み違える。実出力を添えて人が見分けられるようにする
        raise LauncherError(
            "herdr integration status に `claude: current` が無い "
            f"(`herdr integration install claude` で導入する)。実際の出力: {out.strip()}"
        )
    code, _out, err = run_command(["herdr", "status"])
    if code != 0:
        raise LauncherError(f"herdr status が失敗 (socket に届かない): {err.strip()}")
    agent_bin = shutil.which(AGENT_BIN)
    if not agent_bin:
        # pane を割った先の shell は別の PATH を組むことがあるので絶対 path で渡す。
        # ここで解決できないなら起動しても pane だけが残る
        raise LauncherError(f"{AGENT_BIN} が PATH に見つからない")
    return {"self_pane_id": self_pane_id, "workspace": workspace, "agent_bin": agent_bin}


def list_panes(workspace: str) -> list[dict]:
    """自 workspace の pane 一覧。

    `--workspace` を明示する — 省略すると全 workspace の pane が返り、別の workspace で
    走っている同名 label を自分の観測対象として拾う。
    """
    payload = run_herdr(["pane", "list", "--workspace", workspace]) or {}
    return payload.get("result", {}).get("panes", [])


def build_command(agent_bin: str, label: str, skill: str) -> str:
    """pane 内で起動する command 文字列を組み立てる。

    `shlex.quote` で組むのは、herdr が pane へ command を渡すときに呼び出し側の引用符が
    保たれず、pane の shell が改めて空白で単語分割するため。prompt を裸で置くと 1 引数の
    はずの初期指示が複数の argv へ割れて claude へ届く。

    `--name` に pane label と同じ文字列を渡すのは、名前でしか相手を見つけられない経路
    (ListAgents) から当該セッションを引けるようにするため。
    """
    parts = [agent_bin, "--name", label, PROMPT_TEMPLATE.format(skill=skill)]
    return " ".join(shlex.quote(part) for part in parts)


def launch_pane(self_pane_id: str, cwd: str, label: str, command: str) -> str:
    """pane を割って label を付け command を実行し、pane_id を返す。

    分割元に `--pane` を明示するのは、`--current` の解決先が UI のフォーカス中 pane に
    落ちるため。フォーカスが別 workspace にあると、起動した pane が `pane list
    --workspace` の観測窓の外へ出る。

    rename / run で落ちたときは割った pane を畳んでから送出する。畳まないと label の
    付かない pane が残り、label で走査する次回実行から見えない残骸になる。
    """
    created = run_herdr(
        [
            "pane", "split",
            "--pane", self_pane_id,
            "--direction", "right",
            "--no-focus",
            "--cwd", cwd,
        ]
    ) or {}
    pane_id = created.get("result", {}).get("pane", {}).get("pane_id")
    if not pane_id:
        raise LauncherError("herdr pane split が pane_id を返さなかった")
    try:
        run_herdr(["pane", "rename", pane_id, label])
        run_herdr(["pane", "run", pane_id, command])
    except LauncherError as exc:
        if not close_pane(pane_id):
            raise LauncherError(f"{exc} / 割った pane {pane_id} を畳めなかった") from exc
        raise
    return pane_id


def close_pane(pane_id: str) -> bool:
    """pane を畳めたかを返す。畳めなくても起動失敗の報告は続けるので送出しない。

    畳めなかった pane は label を占有したまま残り、次回実行がその target を
    `already_running` と読んで永久に起動しなくなる。呼び出し側が報告へ載せられるよう
    成否を返す (握りつぶすと user は人手 rename が要ることに気付けない)。
    """
    try:
        run_herdr(["pane", "close", pane_id])
    except LauncherError:
        return False
    return True


def await_agents(workspace: str, pane_ids: list[str]) -> set[str]:
    """agent が立った pane_id の集合を返す。全 pane 分の待ちを 1 回の list で共有する。

    一度立った pane は周回をまたいで積む — 各周の観測で上書きすると、herdr が再検出中で
    `agent` を一瞬落とした稼働中の pane が最終周の欠落だけで畳まれる。
    """
    settled: set[str] = set()
    for _attempt in range(POLL_ATTEMPTS):
        sleep(POLL_INTERVAL_SEC)
        settled |= {
            pane["pane_id"]
            for pane in list_panes(workspace)
            if pane.get("pane_id") in pane_ids and pane.get("agent") == HERDR_AGENT_KIND
        }
        if len(settled) == len(pane_ids):
            break
    return settled


def normalize_targets(raw: list[str]) -> list[str]:
    """空白区切り / comma 区切りのどちらでも受け、宣言順に畳んで返す。

    skill の args 表記が両方を許すので、呼び出し側に正規化を強いない。未知の名前は
    LauncherError で止める — 綴り違いを黙って無視すると、頼んだ target が起動しないまま
    「起動した」と報告される。
    """
    names = [name for chunk in raw for name in chunk.split(",") if name]
    unknown = [name for name in names if name not in TARGETS]
    if unknown:
        raise LauncherError(
            f"未知の target: {', '.join(unknown)} (指定できるのは {', '.join(sorted(TARGETS))})"
        )
    return [target for target in TARGETS if target in names] or list(TARGETS)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "targets",
        nargs="*",
        metavar="TARGET",
        help=f"起動する target ({' / '.join(sorted(TARGETS))}。省略時は全部)",
    )
    return parser.parse_args(argv)


def start_targets(targets: list[str], env: dict[str, str], cwd: str) -> tuple[list, list, list]:
    """占有 label を避けながら target ごとに pane を起こす (agent の検出はまだ見ない)。"""
    occupied = {
        pane.get("label"): pane.get("pane_id") for pane in list_panes(env["workspace"])
    }
    launched: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    for target in targets:
        skill, label = TARGETS[target]
        if label in occupied:
            reason = (
                "self_pane_stale_label"
                if occupied[label] == env["self_pane_id"]
                else "already_running"
            )
            skipped.append({"target": target, "label": label, "reason": reason,
                            "pane_id": occupied[label]})
            continue
        try:
            pane_id = launch_pane(
                env["self_pane_id"], cwd, label,
                build_command(env["agent_bin"], label, skill),
            )
        except LauncherError as exc:
            failed.append({"target": target, "label": label,
                           "reason": "launch_failed", "detail": str(exc)})
            continue
        launched.append({"target": target, "label": label, "pane_id": pane_id})
    return launched, skipped, failed


def settle_launched(workspace: str, launched: list[dict], failed: list[dict]) -> None:
    """agent が立たなかった pane を畳んで failed へ差し戻す (launched / failed を書き換える)。"""
    settled = await_agents(workspace, [entry["pane_id"] for entry in launched])
    for entry in list(launched):
        if entry["pane_id"] in settled:
            continue
        # pane は割れたが Claude Code が立っていない。畳めば label が解放され、
        # 同じ target を撃ち直せる
        closed = close_pane(entry["pane_id"])
        launched.remove(entry)
        failed.append({
            **entry,
            "reason": "agent_not_detected",
            "detail": "pane は畳んだ" if closed else "pane を畳めず label が残っている",
            "label_released": closed,
        })


def launch_targets(targets: list[str], env: dict[str, str], cwd: str) -> dict:
    """target ごとに起動を試み、launched / skipped / failed に振り分ける。

    起こした後に herdr が落ちても report は返す — 起動済み pane を報告から落とすと、
    呼び出し元も user も何が生きているかを知らないまま残骸だけが残る。
    """
    launched, skipped, failed = start_targets(targets, env, cwd)
    report = {"launched": launched, "skipped": skipped, "failed": failed}
    try:
        settle_launched(env["workspace"], launched, failed)
    except LauncherError as exc:
        # agent の検出ができていないので launched は「起こしたが未確認」の意味になる
        report["settle_error"] = str(exc)
    return report


def emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    print()


def main(args: argparse.Namespace) -> int:
    """どの経路でも JSON 1 件を出して終わる。

    起動を始めた後の herdr 失敗まで JSON へ畳むのは、traceback で終わると起こした pane が
    どこにも報告されないため — 呼び出し元は `launched[]` を見ないと残骸を回収できない。
    """
    # 綴り違いは環境の不備ではないので欄を分ける (呼び出し元の案内先が変わる)
    try:
        targets = normalize_targets(args.targets)
    except LauncherError as exc:
        emit({"ok": False, "arg_error": str(exc)})
        return 1
    try:
        env = preflight()
    except LauncherError as exc:
        emit({"ok": False, "preflight_error": str(exc)})
        return 1
    try:
        report = launch_targets(targets, env, os.getcwd())
    except LauncherError as exc:
        emit({"ok": False, "launch_error": str(exc)})
        return 1
    emit({"ok": not (report["failed"] or report.get("settle_error")), **report})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)
