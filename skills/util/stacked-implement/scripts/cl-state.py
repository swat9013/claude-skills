#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""stacked-implement が merge に追従するときの、CL の状態の区分と、merge で head branch を消す設定を出す。

tracker の CLI はこの script の外で撃ち、その出力をそのまま保存した file を入力にする。撃つコマンドは `command` が出す
(読む鍵の正本は下の GH_PR_FIELDS と normalize で、コマンドもそこから組み立てる)。

- `command --tracker <gh|glab> --repo <CL の repo> <番号>...`: CL ごとの状態の取得コマンドを 1 行 1 件出す (--tracker と番号は必須)
- `command --auto-delete --repo <CL の repo>`: merge で head branch を消す設定の取得コマンド (gh だけ) を 1 行出す。番号か `--tracker glab` を一緒に渡すと exit 2
- `classify <計画の file> <CL の取得結果の file>...`: 計画の file は cl-plan.py build の出力 (target を付け替えたら
  `target` を直したもの)。取得結果は `command` のコマンドの出力で、head branch で計画の行に当てる。CL ごとに次を JSON で出す
  - `conflict`: open の CL に出す。true は conflict している (`mergeable` が `CONFLICTING` / `has_conflicts` が true)、false は
    していない、null は未確定 (gh の `UNKNOWN` など。時間を置いて引き直す)
  - 真偽の鍵 (`conflict`・`recover`・`cleanable`・`merge_ok`) は値で、値を持つ鍵 (`retarget_to`・`auto_retargeted_to`) は
    鍵があるかで読む。glab の `locked` (merge の処理中) は open と読む
  - `retarget_to`: open で merged の CL の branch を target にしている。値はその merged の CL の target
  - `recover`: merged の CL の branch を target にしていて closed (merge されずに) になった
  - `auto_retargeted_to`: open で、計画の target が merged の CL の branch なのに今の target がそれと違う。値は今の target
  - `cleanable`: merged か closed で、`recover` が出ず、その branch を target にしている open の CL も、recover で reopen
    する CL も無い。ある CL は `open_dependents` と `recover_dependents` に挙げる
  - `merge_ok`: open で、target が merged の CL の branch でなく、既定ブランチか merged の CL の target (計画のまだ merge
    されていない行の branch を除く) で、計画の行の `merge_in` の branch がすべて merged で、conflict が無いと確定し、tracker が
    今 merge できるとしている (gh の `mergeable` が `MERGEABLE` で `mergeStateStatus` が `CLEAN`・`HAS_HOOKS`・`UNSTABLE` のどれか、glab の `detailed_merge_status` が `mergeable` (無ければ
    `merge_status` が `can_be_merged`)。draft・rebase が要る・承認待ちなどは conflict が無くても false)
- `auto-delete <file>`: `command --auto-delete` のコマンドの stdout を保存した file を読み、`無効` (値が false のときだけ) か `有効` (true) か `確かめられなかった` (失敗・null・空・file が無い。有効として扱う)
  を 1 行出す

exit code: 0 = 出せた / 2 = 入力の誤り (file が読めない・鍵を欠く・head branch が計画に無い)。

invariant:

1. auto-delete が `無効` を出すのは値が false のときだけ
2. cleanable は、その branch を target にしている open の CL か recover で reopen する CL が残る間は false
3. merge_ok は、conflict の有無が確定していないか、tracker が今 merge できるとしていないか、target が merged の CL の
   branch のままか、merge_in に merged でない branch があれば false
4. conflict の有無が確定していない CL の conflict は null で、false にしない
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path


GH_PR_FIELDS = "number,headRefName,state,baseRefName,mergeable,mergeStateStatus"
# gh が今 merge できるとする mergeStateStatus (DRAFT・BLOCKED・BEHIND などは conflict が無くても merge できない)
GH_READY_STATES = {"CLEAN", "HAS_HOOKS", "UNSTABLE"}
# glab の merge の可否をまだ計算し終えていない状態。has_conflicts はこの間 false のままなので未確定と読む
GLAB_PENDING_STATUSES = {"unchecked", "checking", "preparing", "cannot_be_merged_recheck", "approvals_syncing"}


class InputError(Exception):
    """入力が使えない。message が理由。"""


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise InputError(f"{path} を JSON として読めない: {e}") from e
    if not isinstance(data, dict):
        raise InputError(f"{path} の最上位が object でない")
    return data


def normalize(data: dict, path: Path) -> dict:
    """gh / glab の取得結果を {number, branch, state, base, conflict} へそろえる。conflict は未確定なら None。"""
    if "iid" in data:
        keys = ("iid", "source_branch", "state", "target_branch", "has_conflicts")
        missing = [k for k in keys if k not in data]
        if missing:
            raise InputError(f"{path}: glab の形で鍵 {', '.join(missing)} が無い")
        state = {"opened": "open", "locked": "open"}.get(data["state"], data["state"])
        status = data.get("detailed_merge_status") or data.get("merge_status")
        pending = status in GLAB_PENDING_STATUSES
        conflict = data["has_conflicts"] if isinstance(data["has_conflicts"], bool) and not pending else None
        ready = status == "mergeable" if data.get("detailed_merge_status") else status == "can_be_merged"
        return {"number": data["iid"], "branch": data["source_branch"], "state": state,
                "base": data["target_branch"], "conflict": conflict, "ready": conflict is False and ready}
    keys = tuple(GH_PR_FIELDS.split(","))
    missing = [k for k in keys if k not in data]
    if missing:
        raise InputError(f"{path}: gh の形で鍵 {', '.join(missing)} が無い")
    conflict = {"CONFLICTING": True, "MERGEABLE": False}.get(data["mergeable"])
    return {"number": data["number"], "branch": data["headRefName"], "state": str(data["state"]).lower(),
            "base": data["baseRefName"], "conflict": conflict,
            "ready": conflict is False and data["mergeStateStatus"] in GH_READY_STATES}


def classify(plan: dict, cls: list[dict]) -> list[dict]:
    rows = {r["branch"]: r for r in plan["rows"]}
    default = plan["default_branch"]
    unknown = [c["branch"] for c in cls if c["branch"] not in rows]
    if unknown:
        raise InputError(f"計画に無い head branch: {', '.join(unknown)}")
    merged = {c["branch"]: c["base"] for c in cls if c["state"] == "merged"}
    # merge 先として既定ブランチへ届く branch: 既定ブランチと merged の CL の target。計画のまだ merge されていない行の branch は除く
    pending = {r["branch"] for r in plan["rows"]} - merged.keys()
    landed = ({default} | set(merged.values())) - pending
    to_recover = {c["branch"] for c in cls if c["state"] == "closed" and c["base"] in merged}
    out = []
    for c in cls:
        row = rows[c["branch"]]
        planned = row["target"]
        entry = {"name": rows[c["branch"]]["name"], "number": c["number"], "state": c["state"], "target": c["base"]}
        if c["state"] == "open":
            entry["conflict"] = c["conflict"]
            if c["base"] in merged:
                entry["retarget_to"] = merged[c["base"]]
            if planned in merged and c["base"] != planned:
                entry["auto_retargeted_to"] = c["base"]
            entry["merge_ok"] = (
                c["ready"]
                and c["base"] not in merged
                and c["base"] in landed
                and all(b in merged for b in row.get("merge_in") or [])
            )
        else:
            recover = c["branch"] in to_recover
            if recover:
                entry["recover"] = True
            dependents = [d for d in cls if d["base"] == c["branch"]]
            entry["open_dependents"] = [rows[d["branch"]]["name"] for d in dependents if d["state"] == "open"]
            entry["recover_dependents"] = [rows[d["branch"]]["name"] for d in dependents if d["branch"] in to_recover]
            entry["cleanable"] = not entry["open_dependents"] and not entry["recover_dependents"] and not recover
        out.append(entry)
    return out


def commands(tracker: str, repo: str, numbers: list[int], auto_delete_setting: bool) -> list[str]:
    repo = shlex.quote(repo)  # 出力の行はそのまま Bash で撃たれる
    if auto_delete_setting:
        return [f"gh repo view {repo} --json deleteBranchOnMerge --jq .deleteBranchOnMerge"]
    if tracker == "gh":
        return [f"gh pr view {n} -R {repo} --json {GH_PR_FIELDS}" for n in numbers]
    return [f"glab mr view {n} -R {repo} --output json" for n in numbers]


def auto_delete(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return "確かめられなかった"
    return {"false": "無効", "true": "有効"}.get(value, "確かめられなかった")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("command", help="CL の状態か、head branch を消す設定の取得コマンドを出す")
    m.add_argument("--tracker", choices=["gh", "glab"], help="CL の状態の取得で必須")
    m.add_argument("--repo", required=True, help="CL の repo")
    m.add_argument("--auto-delete", action="store_true", help="merge で head branch を消す設定の取得コマンド (gh だけ)")
    m.add_argument("numbers", nargs="*", type=int)
    c = sub.add_parser("classify", help="CL ごとの状態の区分を出す")
    c.add_argument("plan", type=Path)
    c.add_argument("files", nargs="+", type=Path)
    a = sub.add_parser("auto-delete", help="merge で head branch を消す設定を読む")
    a.add_argument("file", type=Path)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cmd == "command":
        if args.auto_delete and (args.numbers or args.tracker == "glab"):
            print("引数の誤り: --auto-delete は gh の設定だけを引き、CL の番号も glab も取らない", file=sys.stderr)
            return 2
        if not args.auto_delete and (not args.tracker or not args.numbers):
            print("引数の誤り: CL の状態の取得には --tracker と CL の番号が要る", file=sys.stderr)
            return 2
        print("\n".join(commands(args.tracker, args.repo, args.numbers, args.auto_delete)))
        return 0
    if args.cmd == "auto-delete":
        print(auto_delete(args.file))
        return 0
    try:
        plan = load_json(args.plan)
        cls = [normalize(load_json(f), f) for f in args.files]
        result = classify(plan, cls)
    except (InputError, KeyError) as e:
        print(f"入力の誤り: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
