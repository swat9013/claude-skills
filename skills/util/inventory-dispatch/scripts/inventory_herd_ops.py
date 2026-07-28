#!/usr/bin/env python3
"""inventory-dispatch 用の herdr wrapper。前提検査・pane 起動・レポート監視を決定論化する。

inventory-dispatch skill の手順 0 / 2 / 4 に対応する 5 種の subcommand を提供し、
結果を JSON で stdout に出す。判断 (レポート候補のどれを適用するか) は常に人間
(skill 側の AskUserQuestion) が行い、本 script は決定的な検査・観測・herdr API
呼び出しのみを担う。

対象 target は 3 つ固定: permissions / claude-md / skill-mcp
(それぞれ inventory-permissions / inventory-claude-md / inventory-skill-mcp skill
に対応。pane label は `inv-<target>`、レポートは `/tmp/inventory-<skill名>/report-*.md`)。

subcommand:
- preflight               3 段検査 + 自 pane label 正規化 → {"ok": bool, "failed": ..., "self": ..., "workspace": ...}
  検査は (1) HERDR_ENV=1 (2) `herdr integration status` に `claude: current` 行
  (3) `herdr status` の socket 疎通、の順で行い最初の失敗で止める (fail-closed)。
  加えて自 pane ($HERDR_PANE_ID) の label が `inv-<target>` 形 (前回 dispatch の
  残骸) なら `inv-dispatch` へ rename する — 残骸 label は当該 target を稼働中と
  誤判定させ、spawn の duplicate 検査を誤爆させる事故源になる。
- spawn <target> [--cwd DIR]
  同 workspace に label `inv-<target>` の pane が既に在れば
  {"ok": false, "reason": "duplicate", "pane_id": <既存>, ...} + exit 1
  (既存 pane_id を返すのは skill 側が「既に稼働中」として監視対象へ取り込むため)。
  無ければ pane split → label `inv-<target>` 付与 → claude 起動 → agent field が
  "claude" になるまで poll (2 秒間隔 x 5 回。screen manifest 検出に数秒要る)。
  初期 prompt template は本 script が正本 (PROMPT_TEMPLATE)。返却 JSON の `ts`
  (spawn 開始時 epoch 秒) を skill が台帳に控え、watch の --since に使う。
  claude binary は PATH から絶対パスへ解決する (pane spawn shell の PATH 差異対策)。
  prompt は shlex.quote で組み立てる (quote 崩れで pane の shell が誤解釈する事故対策)。
- watch --since EPOCH [--timeout-sec 240] [--interval-sec 10]
  自 workspace の `inv-<target>` label pane を追跡し、いずれかの変化まで block →
  {"event": ..., "panes": [...], "reports": {target: {path, mtime} | null}}
  event: report_ready (いずれかの target のレポート dir に mtime >= since の
  report-*.md が出現。target ごとの newest を reports に返す) / agent_exited
  (claude 終了) / pane_gone (pane 消失) / timeout / no_panes。優先順は
  report_ready > agent_exited > pane_gone (報告の分かりやすさのためで、skill 側の
  照合は event に依らず reports + panes を同一手順で読む)。どの event でも exit 0 —
  変化なし timeout は失敗ではない。reports は event に依らず毎回全 target 分返す。
  処理済みレポートの再検出は skill 側が --since を進めて回避する (script は
  stateless — 既読状態を持たない)。
- send <target> --text T
  label `inv-<target>` の pane へ `herdr pane send-text` でテキストを届けた後、
  `herdr pane send-keys <pane> enter` で claude への submit まで行う。
  根拠: `herdr pane send-text --help` (実機確認 2026-07-23) は「Send literal text
  to a pane」のみで submit 手段・改行 submit の記載が無く、`send-keys --help` は
  key 名指定 (esc が canonical 等) の key 送出を提供する — literal text では
  Enter を表現できないため、send-text 後に send-keys で Enter key を送出する
  実装とする。pane 不在は {"ok": false, "reason": "not_found"} + exit 1
  (送り先不明のまま成功を返さない fail-closed)。
- close <target>          当該 pane を close → {"removed": bool, ...}
  pane 不在は {"removed": false, "reason": "not_found"} の正常データ (exit 0) —
  「既に消えている」は close の期待結果と同値。

issue-dispatch の herdr_ops.py と subprocess helper / preflight 検査 / agent poll が
同型だが共有 module 化はしない: 既存 tests/test_herdr_ops.py は `mod.run_command` の
monkeypatch を test seam にしており、共有 lib 内部からの呼び出しは patch を素通り
するため既存テストの前提を壊す。両 script 間で一致が構造的に要る定数も無い
(label 語彙 `i<番号>` / `inv-<target>` と prompt template は意図的に別物)。

本 script は herdr を subprocess 起動するため sandbox 内では動かない (socket connect
が PermissionDenied で遮断される)。settings の `sandbox.excludedCommands` に登録して
使う。起動は登録 entry と同じ tilde 表記 (~/.claude/skills/...) の単文で行うこと —
絶対パス・変数・compound command 経由は entry と照合されず sandbox 内に落ちる。

終了コード: 0=成功 / 1=検査失敗・CLI 失敗・duplicate・agent 未検出・pane 不在 (send) / 2=引数エラー。
"""

import argparse
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

# target → 呼び出す inventory skill 名。pane label は `inv-<target>`、
# レポート dir は REPORT_BASE/<skill 名> (各 inventory skill の SKILL.md が正本)。
TARGET_SKILLS = {
    "permissions": "inventory-permissions",
    "claude-md": "inventory-claude-md",
    "skill-mcp": "inventory-skill-mcp",
}
INV_LABEL = re.compile(r"^inv-(permissions|claude-md|skill-mcp)$")
HOOK_CURRENT = re.compile(r"^claude: current", re.MULTILINE)
SELF_RENAME_TO = "inv-dispatch"
POLL_ATTEMPTS = 5
POLL_INTERVAL_SEC = 2
SUBPROCESS_TIMEOUT_SEC = 60
# 各 inventory skill のレポート出力先の親 dir (テストで monkeypatch する)
REPORT_BASE = "/tmp"

# 初期 prompt template (本 script が正本)。レポート path の報告を義務付ける —
# watch の report_ready 検出はファイル出現 (mtime) が正で、この報告は人間向けの補助。
PROMPT_TEMPLATE = (
    "/swat-skills:{skill} を実行する。skill の手順に完全に従うこと。"
    "完了したら最終メッセージにレポートファイルの絶対パスを含めて報告する"
)

_sleep = time.sleep
_now = time.time


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


def label_for(target):
    return f"inv-{target}"


def build_prompt(target):
    skill = TARGET_SKILLS.get(target)
    if skill is None:
        raise HerdrError(f"unknown target '{target}' (candidates: {', '.join(TARGET_SKILLS)})")
    return PROMPT_TEMPLATE.format(skill=skill)


# --- preflight ---------------------------------------------------------------


def preflight():
    """3 段検査を順に通し、自 pane の残骸 label (`inv-<target>`) を正規化する。

    最初の失敗で止めて failed に検査名を返す (fail-closed)。skill 側は failed の
    値に応じて user への修正依頼文を選ぶ (issue-dispatch と同じ 3 種)。
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
    if label and INV_LABEL.match(label):
        # 前回 dispatch の残骸 label: 当該 target が稼働中と誤判定され spawn が duplicate で弾かれる
        pane = _pane_of(_run_herdr(["pane", "rename", self_id, SELF_RENAME_TO]))
        renamed_from, label = label, pane.get("label")
    result["self"] = {"pane_id": self_id, "label": label, "renamed_from": renamed_from}
    result["ok"] = True
    return result


# --- pane 追跡 ------------------------------------------------------------------


def tracked_from_panes(panes, self_pane):
    """pane 列 → 追跡対象 (`inv-<target>` label、自 pane 除外) の [{pane_id, target, label}]。"""
    tracked = []
    for pane in panes:
        if pane.get("pane_id") == self_pane:
            continue
        match = INV_LABEL.match(pane.get("label") or "")
        if match:
            tracked.append(
                {"pane_id": pane["pane_id"], "target": match.group(1), "label": pane["label"]}
            )
    return tracked


def _list_tracked():
    workspace = _require_env("HERDR_WORKSPACE_ID")
    self_pane = os.environ.get("HERDR_PANE_ID")
    payload = _run_herdr(["pane", "list", "--workspace", workspace])
    return tracked_from_panes((payload or {}).get("result", {}).get("panes", []), self_pane)


def _find_pane(target):
    """label `inv-<target>` の pane を返す (不在は None — 呼び手が意味づけする)。"""
    for entry in _list_tracked():
        if entry["target"] == target:
            return entry
    return None


# --- report 検出 -----------------------------------------------------------------


def newest_report(target, since):
    """target のレポート dir から mtime >= since の report-*.md の newest を返す。

    無ければ None。dir 不在も None (pane がまだレポートを書いていないだけ)。
    mtime 基準なのは、report ファイル名の timestamp 形式が skill ごとに揺れても
    決定的に比較できるようにするため。
    """
    pattern = os.path.join(REPORT_BASE, TARGET_SKILLS[target], "report-*.md")
    newest = None
    for path in glob.glob(pattern):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue  # glob と stat の間に消えた race — 「無い」として扱う
        if mtime < since:
            continue
        if newest is None or mtime > newest["mtime"]:
            newest = {"path": path, "mtime": mtime}
    return newest


def collect_reports(since):
    """全 target 分の newest report (mtime >= since) を返す (無い target は None)。"""
    return {target: newest_report(target, since) for target in TARGET_SKILLS}


# --- watch -----------------------------------------------------------------------


def classify_watch(baseline_ids, panes, reports):
    """スナップショット → 返却イベント。変化無しは None (poll 継続)。

    report_ready > agent_exited > pane_gone。優先順は報告の分かりやすさのため
    だけにある — skill 側の照合は event に依らず reports + panes を同一手順で読む。
    """
    if any(report is not None for report in reports.values()):
        return "report_ready"
    if any(entry["agent"] is None for entry in panes):
        return "agent_exited"
    if baseline_ids - {entry["pane_id"] for entry in panes}:
        return "pane_gone"
    return None


def _observe_panes():
    """追跡 pane の現在 snapshot (agent 込み)。

    pane list と pane get の間に pane が消える race では get が失敗する。再 list で
    不在が確定したら「消えた」として除外する (期待イベントであり異常ではない)。
    在るのに get が失敗するのは herdr 側の異常なので fail-closed で再送出する。
    """
    observed = []
    for entry in _list_tracked():
        try:
            pane = _pane_of(_run_herdr(["pane", "get", entry["pane_id"]]))
        except HerdrError:
            if any(p["pane_id"] == entry["pane_id"] for p in _list_tracked()):
                raise
            continue
        if "agent" not in pane:
            # agent field 欠落を null (= claude 終了) と誤読すると全 pane 誤死亡判定に直結する
            raise HerdrError(f"pane get {entry['pane_id']} の応答に agent field が無い")
        observed.append({**entry, "agent": pane["agent"]})
    return observed


def watch(since, timeout_sec, interval_sec):
    """追跡 pane の変化 (report 出現 / agent null / pane 消失) か timeout まで block する。

    event はどれでも skill 側の照合手順は同一 (報告用の参考情報)。exit code も
    event で変えない — 「変化なし timeout」は失敗ではない。
    """
    panes = _observe_panes()
    reports = collect_reports(since)
    if not panes and not any(report is not None for report in reports.values()):
        return {"event": "no_panes", "panes": [], "reports": reports}
    baseline_ids = {entry["pane_id"] for entry in panes}
    event = classify_watch(baseline_ids, panes, reports)
    for _attempt in range(max(1, timeout_sec // interval_sec)):
        if event:
            break
        _sleep(interval_sec)
        panes = _observe_panes()
        reports = collect_reports(since)
        event = classify_watch(baseline_ids, panes, reports)
    return {"event": event or "timeout", "panes": panes, "reports": reports}


# --- spawn -----------------------------------------------------------------------


def spawn(target, cwd):
    ts = int(_now())
    existing = _find_pane(target)
    if existing is not None:
        # 既存 pane_id を返す — skill 側が「既に稼働中」として監視対象へ取り込む
        return {
            "ok": False,
            "reason": "duplicate",
            "target": target,
            "skill": TARGET_SKILLS[target],
            "pane_id": existing["pane_id"],
            "label": existing["label"],
            "ts": ts,
        }
    claude_bin = resolve_claude_bin()
    if not claude_bin:
        raise HerdrError("claude binary が PATH に見つからない")
    command = " ".join(shlex.quote(part) for part in [claude_bin, build_prompt(target)])
    label = label_for(target)

    pane = _pane_of(
        _run_herdr(["pane", "split", "--current", "--direction", "right", "--no-focus", "--cwd", cwd])
    )
    pane_id = pane["pane_id"]
    _run_herdr(["pane", "rename", pane_id, label])
    _run_herdr(["pane", "run", pane_id, command])

    agent = None
    for _attempt in range(POLL_ATTEMPTS):
        _sleep(POLL_INTERVAL_SEC)
        agent = _pane_of(_run_herdr(["pane", "get", pane_id])).get("agent")
        if agent == "claude":
            break
    return {
        "ok": agent == "claude",
        "target": target,
        "skill": TARGET_SKILLS[target],
        "pane_id": pane_id,
        "label": label,
        "ts": ts,
        "agent": agent,
        "command": command,
    }


# --- send / close ----------------------------------------------------------------


def send(target, text):
    """pane へテキスト送信 + Enter key で submit する (方式の根拠は module docstring)。"""
    pane = _find_pane(target)
    if pane is None:
        return {"ok": False, "target": target, "reason": "not_found"}
    _run_herdr(["pane", "send-text", pane["pane_id"], text])
    _run_herdr(["pane", "send-keys", pane["pane_id"], "enter"])
    return {"ok": True, "target": target, "pane_id": pane["pane_id"], "chars": len(text)}


def close(target):
    """pane を close する。不在は正常データ (既に消えている = close の期待結果と同値)。"""
    pane = _find_pane(target)
    if pane is None:
        return {"removed": False, "target": target, "reason": "not_found"}
    _run_herdr(["pane", "close", pane["pane_id"]])
    return {"removed": True, "target": target, "pane_id": pane["pane_id"]}


# --- CLI -----------------------------------------------------------------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    targets = sorted(TARGET_SKILLS)

    sub.add_parser("preflight", help="3 段検査 + 自 pane label 正規化")

    p_spawn = sub.add_parser("spawn", help="pane split → claude 起動 → agent 検出まで")
    p_spawn.add_argument("target", choices=targets, help="起動する inventory target")
    p_spawn.add_argument("--cwd", default=".", help="新 pane の cwd (default: cwd)")

    p_watch = sub.add_parser("watch", help="レポート出現・pane 変化か timeout まで block する")
    p_watch.add_argument("--since", type=float, required=True, help="report mtime の下限 epoch 秒 (spawn の ts)")
    p_watch.add_argument("--timeout-sec", type=int, default=240, help="最大 block 秒数")
    p_watch.add_argument("--interval-sec", type=int, default=10, help="poll 間隔秒数")

    p_send = sub.add_parser("send", help="pane へテキスト送信 + Enter submit")
    p_send.add_argument("target", choices=targets, help="送信先 inventory target")
    p_send.add_argument("--text", required=True, help="送信するテキスト")

    p_close = sub.add_parser("close", help="pane を close する (不在は正常データ)")
    p_close.add_argument("target", choices=targets, help="close する inventory target")

    return parser.parse_args(argv)


def main(args):
    try:
        if args.command == "preflight":
            result = preflight()
            ok = result["ok"]
        elif args.command == "spawn":
            result = spawn(args.target, args.cwd)
            ok = result["ok"]
        elif args.command == "watch":
            result = watch(args.since, args.timeout_sec, args.interval_sec)
            ok = True
        elif args.command == "send":
            result = send(args.target, args.text)
            ok = result["ok"]
        else:
            result = close(args.target)
            ok = True
    except HerdrError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main(parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)
