#!/usr/bin/env python3
"""issue-dispatch 用の herdr wrapper。前提検査・slot 算出・pane 起動を決定論化する。

issue-dispatch skill の手順 0 / 2 / 5.3 / 7.1 / 7.3 に対応する 5 種の subcommand を
提供し、結果を JSON で stdout に出す。判断 (どの候補を dispatch するか) は skill の
推論側が行い、本 script は決定的な検査・集計・herdr API 呼び出しのみを担う。

subcommand:
- preflight               3 段検査 + 自 pane label 正規化 → {"ok": bool, "failed": ..., "self": ..., "workspace": ...}
  検査は (1) HERDR_ENV=1 (2) `herdr integration status` に `claude: current` 行
  (3) `herdr status` の socket 疎通、の順で行い最初の失敗で止める (fail-closed)。
  加えて自 pane ($HERDR_PANE_ID) の label が `i<番号>` 形 (前回 dispatch の残骸) なら
  `dispatch` へ rename する — 残骸 label は slot を 1 消費し、当該 issue を
  active-pane として誤除外する二重の事故源になる。
- slots --max N           自 workspace の pane label 集計 → {"active": [...], "used": N, "max": N, "free": N}
  `herdr pane list --workspace $HERDR_WORKSPACE_ID` を明示する (省略すると全
  workspace の pane が返り他 project の issue 番号と衝突する)。自 pane は label に
  かかわらず除外する (preflight rename 失敗時の二重防波堤)。
- watch [--timeout-sec 240] [--interval-sec 10]
  追跡 pane (`i<番号>` label) の変化まで block → {"event": ..., "panes": [...]}
  event: agent_exited (claude 終了) / pane_gone (pane 消失) / timeout / no_panes。
  どの event でも exit 0 — 変化なし timeout は失敗ではなく、skill 側の照合手順は
  event に依らず同一 (完了判定は issue state が正、event は報告用の参考情報)。
- cleanup N [--cwd DIR]       issue N の dispatch worktree を安全回収 → {"removed": bool, "reason": ...}
  clean なら remove + prune + merge 済み branch 削除。dirty / not_found は正常データ (exit 0)。
- spawn N --stage S [--prompt P] [--model M] [--effort E] [--cwd DIR]
  pane split → label `i<N>` 付与 → claude 起動 → agent field が "claude" になるまで
  poll (2 秒間隔 x 5 回。screen manifest 検出に数秒要る)。stage → prompt template と
  stage → model、resolved model → effort は本 script が正本 (--prompt / --model /
  --effort で上書き可。unknown stage の prompt は override 必須。model 対応表に無い
  stage は --model を付けず session default 継承。effort 対応表に無い model は
  --effort を付けず session default 継承)。
  ready-for-agent は claude 側 `--worktree i<N>` で作業ツリーを隔離する
  (`herdr worktree create` は使わない — Claude Code 側と二重管理になる)。
  claude binary は PATH から絶対パスへ解決する (pane spawn shell の PATH 差異対策)。
  prompt は shlex.quote で組み立てる (quote 崩れで pane の shell が誤解釈する事故対策)。

失敗の扱い: herdr CLI の非 0 exit は HerdrError (exit 1 + stderr) として即座に表面化
させる。spawn の agent 未検出は例外でなく {"ok": false} + exit 1 で返す (pane は
既に存在するため、pane_id を診断に使えるよう結果は出す)。

本 script は herdr を subprocess 起動するため sandbox 内では動かない (socket connect が
PermissionDenied で遮断される)。settings の `sandbox.excludedCommands` に登録して使う。
起動は登録 entry と同じ tilde 表記 (~/.claude/skills/...) の単文で行うこと —
絶対パス・変数・compound command 経由は entry と照合されず sandbox 内に落ちる。

終了コード: 0=成功 / 1=検査失敗・CLI 失敗・agent 未検出 / 2=引数エラー。
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

# sibling module を CLI 直接実行 (sys.path[0]=script dir) と importlib load の両方で解決させる
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from stage_vocabulary import require_full_coverage  # noqa: E402

ISSUE_LABEL = re.compile(r"^i(\d+)$")
HOOK_CURRENT = re.compile(r"^claude: current", re.MULTILINE)
SELF_RENAME_TO = "dispatch"
POLL_ATTEMPTS = 5
POLL_INTERVAL_SEC = 2
SUBPROCESS_TIMEOUT_SEC = 60

# 段階 label → 初期 prompt。issue 番号を必ず含める (pane が自走する前提)。
# key 集合の正本は stage_vocabulary.STAGES (直下の require_full_coverage が import 時に強制)
PROMPT_TEMPLATES = {
    "ready-for-agent": (
        "issue #{n} を実装する。まず issue 本文とコメントを読み、"
        "repo 規約に従って実装から PR 作成まで進める"
    ),
    "wayfinder:grilling": (
        "/swat-skills:grilling issue #{n} の計画を検証する。まず issue 本文とコメントを読む"
    ),
    "wayfinder:research": (
        "/swat-skills:researcher issue #{n} の調査を実施する。まず issue 本文とコメントを読む"
    ),
    "wayfinder:prototype": (
        "/swat-skills:prototype issue #{n} の検証プロトタイプを作る。まず issue 本文とコメントを読む"
    ),
    "wayfinder:task": (
        "issue #{n} の task を遂行する。まず issue 本文とコメントを読み、"
        "AFK で進められる部分は実行する。人間の手が必要な部分は精密な checklist を"
        " issue comment に残して報告する"
    ),
    "needs-triage": "/swat-skills:triage issue #{n} を triage する",
}
require_full_coverage(PROMPT_TEMPLATES, "herdr_ops.PROMPT_TEMPLATES")

# 段階 label → 起動モデル alias。PROMPT_TEMPLATES と並置で管理する (本 script が正本)。
# 表外 stage や None は --model を付けず session default を継承させる。
STAGE_MODELS = {
    "wayfinder:grilling": "fable",
    "wayfinder:research": "fable",
    "needs-triage": "sonnet",
}

# 起動モデル alias → reasoning effort。resolved model から派生させる (stage を経由しない)
# のは、--model override 時も対応表が効くようにするため。表外 model や None は
# --effort を付けず session default を継承させる。fable のみ high、その他は xhigh
# (spawn 後のセッションは長時間走るため effort を最大寄りに固定する)。haiku は
# --effort 未サポートなので表外に置く (=session default 継承)。
MODEL_EFFORTS = {
    "opus": "xhigh",
    "fable": "high",
    "sonnet": "xhigh",
}

_sleep = time.sleep


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
    """herdr subcommand を実行して JSON body を返す。成功時 body 空 (pane run) は None。"""
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


# --- preflight ---------------------------------------------------------------


def preflight():
    """3 段検査を順に通し、自 pane の残骸 label (`i<番号>`) を正規化する。

    最初の失敗で止めて failed に検査名を返す (fail-closed)。skill 側は failed の
    値に応じて user への修正依頼文を選ぶ。
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
    if label and ISSUE_LABEL.match(label):
        # 前回 dispatch の残骸 label: slot 消費 + active-pane 誤除外の事故源なので剥がす
        pane = _pane_of(_run_herdr(["pane", "rename", self_id, SELF_RENAME_TO]))
        renamed_from, label = label, pane.get("label")
    result["self"] = {"pane_id": self_id, "label": label, "renamed_from": renamed_from}
    result["ok"] = True
    return result


# --- slots ---------------------------------------------------------------------


def compute_slots(panes, self_pane, max_slots):
    """pane 列 → 稼働 issue 番号と空き slot。自 pane は label にかかわらず除外する。"""
    active = sorted(
        {
            int(match.group(1))
            for pane in panes
            if pane.get("pane_id") != self_pane
            for match in [ISSUE_LABEL.match(pane.get("label") or "")]
            if match
        }
    )
    used = len(active)
    return {"active": active, "used": used, "max": max_slots, "free": max(0, max_slots - used)}


def slots(max_slots):
    workspace = _require_env("HERDR_WORKSPACE_ID")
    self_pane = os.environ.get("HERDR_PANE_ID")
    payload = _run_herdr(["pane", "list", "--workspace", workspace])
    panes = (payload or {}).get("result", {}).get("panes", [])
    return compute_slots(panes, self_pane, max_slots)


# --- watch -----------------------------------------------------------------------


def snapshot_from_panes(panes, self_pane):
    """pane 列 → 追跡対象 (`i<番号>` label、自 pane 除外) の [{pane_id, issue}]。"""
    tracked = []
    for pane in panes:
        if pane.get("pane_id") == self_pane:
            continue
        match = ISSUE_LABEL.match(pane.get("label") or "")
        if match:
            tracked.append({"pane_id": pane["pane_id"], "issue": int(match.group(1))})
    return tracked


def classify_watch(baseline_ids, current):
    """スナップショット → 返却イベント。変化無しは None (poll 継続)。

    agent_exited を pane_gone より優先する — どちらも skill 側の照合手順は同一なので
    優先順は報告の分かりやすさのためだけにある。
    """
    if any(entry["agent"] is None for entry in current):
        return "agent_exited"
    if baseline_ids - {entry["pane_id"] for entry in current}:
        return "pane_gone"
    return None


def _observe_panes():
    """追跡 pane の現在 snapshot (agent 込み)。

    pane list と pane get の間に pane が消える race では get が失敗する。再 list で
    不在が確定したら「消えた」として除外する (期待イベントであり異常ではない)。
    在るのに get が失敗するのは herdr 側の異常なので fail-closed で再送出する。
    """
    workspace = _require_env("HERDR_WORKSPACE_ID")
    self_pane = os.environ.get("HERDR_PANE_ID")

    def list_tracked():
        payload = _run_herdr(["pane", "list", "--workspace", workspace])
        return snapshot_from_panes((payload or {}).get("result", {}).get("panes", []), self_pane)

    observed = []
    for entry in list_tracked():
        try:
            pane = _pane_of(_run_herdr(["pane", "get", entry["pane_id"]]))
        except HerdrError:
            # list と get の間に pane が消えた race。再 list で不在なら「消えた」が確定 (期待イベント)。
            # 在るのに get が失敗するのは herdr 側の異常なので fail-closed で再送出する。
            if any(p["pane_id"] == entry["pane_id"] for p in list_tracked()):
                raise
            continue
        if "agent" not in pane:
            # agent field 欠落を null (= claude 終了) と誤読すると全 pane 誤回収・誤 unclaim に直結する
            raise HerdrError(f"pane get {entry['pane_id']} の応答に agent field が無い")
        observed.append({**entry, "agent": pane["agent"]})
    return observed


def watch(timeout_sec, interval_sec):
    """追跡 pane の変化 (agent null / pane 消失) か timeout まで block する。

    event はどれでも skill 側の照合手順は同一 (報告用の参考情報)。exit code も
    event で変えない — 「変化なし timeout」は失敗ではない。
    """
    baseline = _observe_panes()
    if not baseline:
        return {"event": "no_panes", "panes": []}
    baseline_ids = {entry["pane_id"] for entry in baseline}
    current = baseline
    event = classify_watch(baseline_ids, current)
    for _attempt in range(max(1, timeout_sec // interval_sec)):
        if event:
            break
        _sleep(interval_sec)
        current = _observe_panes()
        event = classify_watch(baseline_ids, current)
    return {"event": event or "timeout", "panes": current}


# --- cleanup ---------------------------------------------------------------------


def _run_git(cwd, args):
    """git subprocess 境界。非 0 exit は HerdrError で即表面化する (fail-closed)。"""
    rc, out, err = run_command(["git", "-C", cwd, *args])
    if rc != 0:
        raise HerdrError(f"git {args[0]} failed (exit {rc}): {err.strip()}")
    return out


def cleanup(number, cwd):
    """issue N の dispatch worktree (.claude/worktrees/i<N>) を安全に回収する。

    issue CLOSED で pane を回収した後に呼ぶ。clean なら remove + prune + (merge 済みなら)
    branch 削除。dirty は成果未回収の可能性があるため削除せず reason で報告に回す。
    worktree が無いのは正常 (worktree を作らない stage もある / claude 側で掃除済み)。
    branch -d の失敗 (未 merge) は許容して branch_deleted=false で返す — -D への
    自動昇格はしない (未 merge branch の強制削除は成果喪失に直結する)。
    """
    root = _run_git(cwd, ["rev-parse", "--show-toplevel"]).strip()
    wt_path = os.path.join(root, ".claude", "worktrees", f"i{number}")
    result = {
        "number": number,
        "worktree": wt_path,
        "removed": False,
        "branch": None,
        "branch_deleted": False,
        "reason": None,
    }
    if not os.path.isdir(wt_path):
        # 登録だけ残った stale entry があれば併せて掃除する
        _run_git(root, ["worktree", "prune"])
        result["reason"] = "not_found"
        return result
    if _run_git(wt_path, ["status", "--porcelain"]).strip():
        result["reason"] = "dirty"
        return result
    branch = _run_git(wt_path, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    result["branch"] = branch
    _run_git(root, ["worktree", "remove", wt_path])
    _run_git(root, ["worktree", "prune"])
    if branch and branch != "HEAD":
        rc, _out, _err = run_command(["git", "-C", root, "branch", "-d", branch])
        result["branch_deleted"] = rc == 0
    result["removed"] = True
    result["reason"] = "removed"
    return result


# --- spawn -----------------------------------------------------------------------


def build_prompt(stage, number, override=None):
    if override:
        return override
    template = PROMPT_TEMPLATES.get(stage)
    if template is None:
        raise HerdrError(f"stage '{stage}' の prompt template が無い (--prompt で明示する)")
    return template.format(n=number)


def resolve_model(stage, override=None):
    """stage → model alias。override が対応表より優先。

    表外 stage で override も無ければ None を返す (--model を付けず session default 継承)。
    """
    if override:
        return override
    return STAGE_MODELS.get(stage)


def resolve_effort(model, override=None):
    """resolved model → reasoning effort。override が対応表より優先。

    表外 model や None (session default 継承の stage) で override も無ければ None を返す
    (--effort を付けず session default 継承)。
    """
    if override:
        return override
    if model is None:
        return None
    return MODEL_EFFORTS.get(model)


def build_claude_command(claude_bin, number, stage, prompt, model=None, effort=None):
    parts = [claude_bin]
    if model:
        parts += ["--model", model]
    if effort:
        parts += ["--effort", effort]
    if stage == "ready-for-agent":
        # 実装セッション同士の file 衝突を Claude Code 側 --worktree で隔離する
        parts += ["--worktree", f"i{number}"]
    parts.append(prompt)
    return " ".join(shlex.quote(part) for part in parts)


def spawn(number, stage, cwd, prompt=None, model=None, effort=None):
    claude_bin = resolve_claude_bin()
    if not claude_bin:
        raise HerdrError("claude binary が PATH に見つからない")
    resolved_model = resolve_model(stage, model)
    command = build_claude_command(
        claude_bin,
        number,
        stage,
        build_prompt(stage, number, prompt),
        resolved_model,
        resolve_effort(resolved_model, effort),
    )
    label = f"i{number}"

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
        "number": number,
        "stage": stage,
        "pane_id": pane_id,
        "label": label,
        "agent": agent,
        "ok": agent == "claude",
        "command": command,
    }


# --- CLI -----------------------------------------------------------------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preflight", help="3 段検査 + 自 pane label 正規化")

    p_slots = sub.add_parser("slots", help="自 workspace の稼働 issue と空き slot を算出する")
    p_slots.add_argument("--max", type=int, required=True, help="同時稼働セッション数の上限")

    p_spawn = sub.add_parser("spawn", help="pane split → claude 起動 → agent 検出まで")
    p_spawn.add_argument("number", type=int, help="issue 番号")
    p_spawn.add_argument("--stage", required=True, help="candidates の .stage をそのまま渡す")
    p_spawn.add_argument("--prompt", default=None, help="prompt template の上書き")
    p_spawn.add_argument("--model", default=None, help="stage → model 対応表の上書き")
    p_spawn.add_argument("--effort", default=None, help="model → effort 対応表の上書き")
    p_spawn.add_argument("--cwd", default=".", help="新 pane の cwd (default: cwd)")

    p_watch = sub.add_parser("watch", help="追跡 pane の変化か timeout まで block する")
    p_watch.add_argument("--timeout-sec", type=int, default=240, help="最大 block 秒数")
    p_watch.add_argument("--interval-sec", type=int, default=10, help="poll 間隔秒数")

    p_cleanup = sub.add_parser("cleanup", help="issue N の dispatch worktree を安全回収する")
    p_cleanup.add_argument("number", type=int, help="issue 番号")
    p_cleanup.add_argument("--cwd", default=".", help="対象 repo 内の path (default: cwd)")

    return parser.parse_args(argv)


def main(args):
    try:
        if args.command == "preflight":
            result = preflight()
            ok = result["ok"]
        elif args.command == "slots":
            result = slots(args.max)
            ok = True
        elif args.command == "watch":
            result = watch(args.timeout_sec, args.interval_sec)
            ok = True
        elif args.command == "cleanup":
            result = cleanup(args.number, args.cwd)
            ok = True
        else:
            result = spawn(args.number, args.stage, args.cwd, args.prompt, args.model, args.effort)
            ok = result["ok"]
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
