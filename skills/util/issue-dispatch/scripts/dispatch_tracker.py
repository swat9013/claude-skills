#!/usr/bin/env python3
"""issue-dispatch 用の gh/glab wrapper。tracker 判定・候補整列・blocker 検査・claim を決定論化する。

issue-dispatch skill の手順 1 / 4-5 / 7 に対応する
5 種の subcommand を提供し、結果を JSON で stdout に出す。判断 (どの候補を
dispatch するか・prompt template の選択) は skill の推論側が行い、本 script は
決定的な取得・分類・整列・API 呼び出しのみを担う。

subcommand:
- detect [--root DIR]         tracker 判定 → {"tracker": "gh"|"glab"}
  docs/agents/issue-tracker.md の H1 を第一正、無ければ git remote -v で fallback
- candidates --tracker T [--limit N] [--active 1,2]
  open issue を取得 → 除外 (needs-info/wontfix > assignee > 稼働 pane) →
  段階分類 → downstream-first + 段階内 FIFO で整列した candidates と
  excluded (理由付き) を返す
- blocked N --tracker T       open blocker 検査 → {"blocked": bool, "open_blockers": [...], "source": ...}
  gh: issue_dependencies_summary (open のみ数える)。field 欠落時は body の
  `Blocked by: #N` 行を fallback とし、参照先の state を個別解決する。
  glab: links の is_blocked_by。リンク型不在の環境では block 系 label を fallback
- claim N --tracker T / unclaim N --tracker T
  assignee の設定/解除。glab は username でなく user id を PUT (CLI version 差を回避)
- states --tracker T --issues 1,2   issue の open/closed 照合 → {"states": [{"number": N, "state": "OPEN"|"CLOSED"}]}
  監視ループの完了判定用。state は大文字に正規化し、未知値は TrackerError (誤回収防止)

失敗の扱い: gh/glab の非 0 exit は TrackerError (exit 1 + stderr) として即座に表面化
させる — 「CLI の実行失敗」を「field 欠落 = blocker 無し」と誤読させない (実行失敗の
素通しが i217 誤 dispatch の真因)。stdout/stderr は常に分離して parse する。

本 script は gh/glab を subprocess 起動するため sandbox 内では動かない (`~/.config/gh`
読取 deny で gh が即死する)。settings の `sandbox.excludedCommands` に登録して使う。

終了コード: 0=成功 / 1=CLI 失敗・tracker 不明 / 2=引数エラー。
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# sibling module を CLI 直接実行 (sys.path[0]=script dir) と importlib load の両方で解決させる
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from stage_vocabulary import require_full_coverage  # noqa: E402

# 段階 label → 優先度 (小さいほど downstream = 完成に近い)。
# key 集合の正本は stage_vocabulary.STAGES (直下の require_full_coverage が import 時に強制)
STAGE_TABLE = {
    "ready-for-agent": 1,
    "wayfinder:grilling": 2,
    "wayfinder:research": 3,
    "wayfinder:prototype": 3,
    "wayfinder:task": 4,
    "needs-triage": 5,
}
require_full_coverage(STAGE_TABLE, "dispatch_tracker.STAGE_TABLE")
UNLABELED_STAGE_LABEL = "needs-triage"
# STAGE_TABLE lookup で導出する — fallback stage の語彙包含を構造的に保証する (表外なら KeyError)
UNLABELED_STAGE = (STAGE_TABLE[UNLABELED_STAGE_LABEL], UNLABELED_STAGE_LABEL)
EXCLUDE_LABELS = frozenset({"needs-info", "wontfix"})
BLOCK_FALLBACK_LABELS = frozenset({"wayfinder:blocked"})
TRACKER_DOC = Path("docs/agents/issue-tracker.md")
BLOCKED_LINE = re.compile(r"^\s*blocked[ -]?by\b[:\s]*(.*)$", re.IGNORECASE | re.MULTILINE)
SUBPROCESS_TIMEOUT_SEC = 60


class TrackerError(Exception):
    """gh / glab CLI の失敗 (非 0 exit / 出力 parse 不能)。"""


def run_command(argv, timeout=SUBPROCESS_TIMEOUT_SEC):
    """subprocess 境界。stdout と stderr を分離したまま返す (テストで monkeypatch する)。"""
    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )
    return proc.returncode, proc.stdout, proc.stderr


def _run_json(argv):
    rc, out, err = run_command(argv)
    if rc != 0:
        raise TrackerError(f"{argv[0]} failed (exit {rc}): {err.strip()}")
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise TrackerError(f"{argv[0]} returned non-JSON stdout: {exc}") from exc


# --- 正規化 ----------------------------------------------------------------


def _label_names(labels):
    return [
        label["name"] if isinstance(label, dict) else str(label)
        for label in (labels or [])
    ]


def normalize_gh_issue(raw):
    return {
        "number": raw["number"],
        "title": raw.get("title", ""),
        "labels": _label_names(raw.get("labels")),
        "assignees": [a["login"] for a in (raw.get("assignees") or [])],
        "created_at": raw.get("createdAt", ""),
    }


def normalize_glab_issue(raw):
    return {
        "number": raw["iid"],
        "title": raw.get("title", ""),
        "labels": _label_names(raw.get("labels")),
        "assignees": [a["username"] for a in (raw.get("assignees") or [])],
        "created_at": raw.get("created_at", ""),
    }


# --- 分類・整列 --------------------------------------------------------------


def classify_stage(labels):
    """段階 label 集合 → (優先度, 段階名)。複数併記は最上位 (最小優先度値) を採る。

    label 無し → needs-triage 扱い。label はあるが段階 label が 1 つも無い
    (例: wayfinder:map のみ) → None (dispatch 対象外)。
    """
    matches = [(prio, label) for label, prio in STAGE_TABLE.items() if label in labels]
    if matches:
        return min(matches)
    if not labels:
        return UNLABELED_STAGE
    return None


def select_candidates(issues, active):
    """正規化済み issue 列 → 除外適用 + 優先度整列した candidates / excluded。

    除外 label は他のどの label より優先。次いで assignee あり、稼働 pane 番号。
    """
    candidates = []
    excluded = []
    for issue in issues:
        labels = set(issue["labels"])
        hit = sorted(labels & EXCLUDE_LABELS)
        if hit:
            excluded.append(_excluded(issue, f"excluded-label:{hit[0]}"))
            continue
        if issue["assignees"]:
            excluded.append(_excluded(issue, "assigned"))
            continue
        if issue["number"] in active:
            excluded.append(_excluded(issue, "active-pane"))
            continue
        stage = classify_stage(labels)
        if stage is None:
            excluded.append(_excluded(issue, "no-dispatchable-stage"))
            continue
        priority, stage_label = stage
        candidates.append({**issue, "stage": stage_label, "priority": priority})
    candidates.sort(key=lambda c: (c["priority"], c["created_at"], c["number"]))
    return {"candidates": candidates, "excluded": excluded}


def _excluded(issue, reason):
    return {"number": issue["number"], "reason": reason, "labels": issue["labels"]}


# --- blocker 検査 ------------------------------------------------------------


def parse_blocked_refs(body):
    """body の `Blocked by: #N` 行から参照 issue 番号を抜く (行内の複数 #N も拾う)。"""
    refs = []
    for match in BLOCKED_LINE.finditer(body or ""):
        refs.extend(int(n) for n in re.findall(r"#(\d+)", match.group(1)))
    return refs


def check_blocked(tracker, number):
    if tracker == "gh":
        return _check_blocked_gh(number)
    return _check_blocked_glab(number)


def _check_blocked_gh(number):
    issue = _run_json(["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{number}"])
    summary = issue.get("issue_dependencies_summary")
    if isinstance(summary, dict) and "blocked_by" in summary:
        open_blockers = []
        if int(summary["blocked_by"] or 0) > 0:
            detail = _run_json(
                ["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{number}/dependencies/blocked_by"]
            )
            open_blockers = [
                {"number": d["number"], "state": d["state"], "title": d.get("title", "")}
                for d in detail
                if d.get("state") == "open"
            ]
        return _blocked_result(number, open_blockers, "api")
    # dependencies 機能が無い repo のみここに来る: body 行の参照先 state を個別解決
    open_blockers = []
    for ref in parse_blocked_refs(issue.get("body")):
        dep = _run_json(["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{ref}"])
        if dep.get("state") == "open":
            open_blockers.append(
                {"number": ref, "state": "open", "title": dep.get("title", "")}
            )
    return _blocked_result(number, open_blockers, "body")


def _check_blocked_glab(number):
    links = _run_json(["glab", "api", f"projects/:id/issues/{number}/links"])
    is_blocked_by = [link for link in links if link.get("link_type") == "is_blocked_by"]
    if is_blocked_by:
        open_blockers = [
            {"number": link["iid"], "state": link["state"], "title": link.get("title", "")}
            for link in is_blocked_by
            if link.get("state") == "opened"
        ]
        return _blocked_result(number, open_blockers, "api")
    # is_blocked_by リンク型が運用されていない環境: block 系 label を fallback
    issue = _run_json(["glab", "issue", "view", str(number), "--output", "json"])
    hit = sorted(set(_label_names(issue.get("labels"))) & BLOCK_FALLBACK_LABELS)
    result = _blocked_result(number, [], "labels")
    result["blocked"] = bool(hit)
    result["block_labels"] = hit
    return result


def _blocked_result(number, open_blockers, source):
    return {
        "number": number,
        "blocked": bool(open_blockers),
        "open_blockers": open_blockers,
        "source": source,
    }


# --- claim / unclaim ---------------------------------------------------------


def claim(tracker, number):
    return _set_assignee(tracker, number, action="claim")


def unclaim(tracker, number):
    return _set_assignee(tracker, number, action="unclaim")


def _set_assignee(tracker, number, action):
    if tracker == "gh":
        flag = "--add-assignee" if action == "claim" else "--remove-assignee"
        rc, _out, err = run_command(["gh", "issue", "edit", str(number), flag, "@me"])
        if rc != 0:
            raise TrackerError(f"gh issue edit failed (exit {rc}): {err.strip()}")
    else:
        # username は instance 間で衝突しうる + CLI flag が version 依存のため user id を PUT する
        uid = _run_json(["glab", "api", "user"])["id"] if action == "claim" else 0
        _run_json(
            ["glab", "api", f"projects/:id/issues/{number}", "-X", "PUT", "-F", f"assignee_ids={uid}"]
        )
    return {"number": number, "action": action, "ok": True}


# --- states ------------------------------------------------------------------


_STATE_MAP = {"open": "OPEN", "opened": "OPEN", "closed": "CLOSED"}


def _normalize_state(raw):
    state = _STATE_MAP.get(str(raw).lower())
    if state is None:
        # 未知値を OPEN/CLOSED のどちらかへ推測で倒すと誤回収・誤 unclaim に直結する
        raise TrackerError(f"未知の issue state: {raw!r}")
    return state


def issue_states(tracker, numbers):
    """監視ループの完了照合用。issue 番号列 → OPEN/CLOSED の正規化列 (入力順を保存)。"""
    states = []
    for number in numbers:
        if tracker == "gh":
            raw = _run_json(["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{number}"])
        else:
            raw = _run_json(["glab", "issue", "view", str(number), "--output", "json"])
        states.append({"number": number, "state": _normalize_state(raw.get("state"))})
    return {"states": states}


# --- tracker 判定 -------------------------------------------------------------


def detect_tracker(root):
    """docs/agents/issue-tracker.md の H1 が第一正。無ければ git remote の host で判定。"""
    doc = Path(root) / TRACKER_DOC
    if doc.is_file():
        h1 = doc.read_text(encoding="utf-8").splitlines()[0] if doc.stat().st_size else ""
        if "GitHub" in h1:
            return "gh"
        if "GitLab" in h1:
            return "glab"
    rc, out, _err = run_command(["git", "-C", str(root), "remote", "-v"])
    if rc == 0:
        if "github.com" in out:
            return "gh"
        if "gitlab" in out:
            return "glab"
    return None


# --- CLI -----------------------------------------------------------------------


def fetch_issues(tracker, limit):
    if tracker == "gh":
        raw = _run_json(
            [
                "gh", "issue", "list", "--state", "open", "--limit", str(limit),
                "--json", "number,title,labels,assignees,createdAt",
            ]
        )
        return [normalize_gh_issue(r) for r in raw]
    # glab の per-page 上限は 100。それ超えの open issue 運用は想定外 (要 page ループ化)
    raw = _run_json(["glab", "issue", "list", "--output", "json", "--per-page", "100"])
    return [normalize_glab_issue(r) for r in raw]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_detect = sub.add_parser("detect", help="tracker (gh/glab) を判定する")
    p_detect.add_argument("--root", default=".", help="repo root (default: cwd)")

    p_cand = sub.add_parser("candidates", help="dispatch 候補を優先度順で返す")
    p_cand.add_argument("--tracker", required=True, choices=["gh", "glab"])
    p_cand.add_argument("--limit", type=int, default=200, help="gh issue list の取得上限")
    p_cand.add_argument("--active", default="", help="稼働中 pane の issue 番号 (comma 区切り)")

    for name in ("blocked", "claim", "unclaim"):
        p = sub.add_parser(name)
        p.add_argument("number", type=int)
        p.add_argument("--tracker", required=True, choices=["gh", "glab"])

    p_states = sub.add_parser("states", help="issue の open/closed を照合する")
    p_states.add_argument("--tracker", required=True, choices=["gh", "glab"])
    p_states.add_argument("--issues", required=True, help="issue 番号 (comma 区切り)")

    return parser.parse_args(argv)


def main(args):
    try:
        if args.command == "detect":
            tracker = detect_tracker(args.root)
            if tracker is None:
                print("tracker を判定できない (doc 無し + remote host 不明)", file=sys.stderr)
                return 1
            result = {"tracker": tracker}
        elif args.command == "candidates":
            active = {int(n) for n in args.active.split(",") if n.strip()}
            result = select_candidates(fetch_issues(args.tracker, args.limit), active)
            result["tracker"] = args.tracker
        elif args.command == "blocked":
            result = check_blocked(args.tracker, args.number)
        elif args.command == "states":
            numbers = [int(n) for n in args.issues.split(",") if n.strip()]
            result = issue_states(args.tracker, numbers)
            result["tracker"] = args.tracker
        elif args.command == "claim":
            result = claim(args.tracker, args.number)
        else:
            result = unclaim(args.tracker, args.number)
    except TrackerError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)
