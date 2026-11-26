#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""stacked-implement の CL 計画を、親が決めた行の組から導き、検査する。

判断 (issue のまとめ方・暗黙の依存・依存先が複数ある行の target の選択・外す行の成果を代わりに満たす行) は
呼び出し元が入力に書き、この script は入力から一意に決まる値だけを導く。入出力は JSON。

- `build <行の組の file> [--deps <read-issues.py deps の出力の file>]`: CL 計画を導いて stdout へ出す。行の組の file は
  `{"repo": <issue 置き場>, "default_branch": <名前>, "rows": [{"name", "branch", "issues": [<N か参照>], "deps": [<行の名前>],
  "base": <依存先が複数あるときに target にする行の名前>, "files": [<触る見込みの file>]}]}` (issues・deps・base・files は省ける。
  ほかの鍵 (担当範囲・後続の前提など) は出力の行へそのまま通す)。1 issue を複数の行に割るときは、割った全部の行の issues に書く。
  導くもの:
  - 行の依存先: 行の `deps` と、まとめた issue それぞれの明示の依存先が載る行を合わせる (同じ行の issue 同士は除く)
  - 対象の外の依存先: closed は依存にしない / 読めないものはその issue と、その issue に依存する issue を外し、issue が
    全部外れた行と、その行を依存先にする行と、外した行にだけ載っていた issue を、変化が無くなるまで外す / open は依存に
    せず `unresolved` に載せる (既定ブランチから積む)
  - 行どうしの依存の循環: あれば `cycles` に、組と、1 issue = 1 CL に戻す行 (issue を 2 件以上まとめた行) を出す
  - 名前の検査: `cl-` で始まり、ASCII の英数字とハイフンだけで、64 字以内
  - 分岐元 / target (`base_branch` と `target`。導いた時点では同じ値): 依存先が無ければ既定ブランチ、1 つならその行の
    branch、複数なら `base` の行の branch で、残りの依存先の branch を `merge_in` に出す
  - issue 参照: 1 行だけに載る issue は `Closes <issue 置き場>#<N>`、複数の行に割った issue は全行に `Refs <issue 置き場>#<N>`
    (割った行の数は、行を外す前の入力で数える)
  - 名前か branch が重なる行があれば、ほかを導かずに `errors` に出す
- `retarget <計画の file> --remove <行の名前>... [--done <行の名前>...]`: 外す行の branch を target にしている残る行の
  新しい target を出す。依存先 (計画の file の `deps` は呼び出し元が直した後の値) のうち外す行でも `--done` (CL が merged か
  closed) でもない行が 1 つならその branch、複数なら `choose` に挙げ、無ければ外す行の target (それも外す行の branch なら辿る)
- `check-merge-order <計画の file> <行の名前>... [--done <行の名前>...]`: 頼まれた merge の順で、依存先が `--done` (CL が
  merged) でも頼まれた列の前にも無い後続を `violations` に、頼まれた行の推奨順を `recommended` に出す。推奨順は依存の
  トポロジカル順で、同時に積める行の間では、他の行と共有する file の数が少ない順、同数なら計画の行の順

計画の file は build の出力 (呼び出し元が target を付け替えたら `target` を直したもの)。
exit code: 0 = 導けた・違反なし / 1 = 計画の誤り (`errors` か `cycles` か `choose` か `violations` を出す) / 2 = 入力の誤り
(file が読めない・鍵を欠く・状態の分からない依存先がある・--deps が無いか足りない・issue の参照の形が違う)。

invariant:

1. 読めない依存先を持つ issue は、それに依存する issue ごと外す
2. 循環があれば target を導かずに exit 1
3. 名前の規則に反する行があれば exit 1
4. 依存先が複数あって `base` がその中に無い行は exit 1
5. 入力で複数の行に載る issue は、行を外した後も、どの行でも Closes にしない
6. 推奨順 (`recommended`) は、どの行も依存先より後に来る
7. 依存先が merged でなく、頼まれた列の前にも無い後続は violations に出す
8. 名前か branch が重なる行があれば exit 1
9. issue のある行の組で --deps が無いか、deps の出力に無い issue があれば exit 2 (依存先なしの計画を黙って出さない)
10. issue 参照は `N`・`#N` を含めて `<issue 置き場>#<N>` にそろえ、置き場の無い `Closes #N` を出さない
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"cl-[A-Za-z0-9-]+")
NAME_MAX = 64


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


REF_RE = re.compile(r"(?:([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)#|#?)([0-9]+)")  # 置き場の後の # は必須


def ref_of(issue: int | str, repo: str) -> str:
    """`N`・`#N`・`<置き場>#N` を read-issues.py と同じ `<置き場>#N` (置き場は casefold) にそろえる。"""
    m = REF_RE.fullmatch(str(issue))
    if not m:
        raise InputError(f"issue の参照の形が違う (<N> か <置き場>#<N>): {issue}")
    return f"{(m.group(1) or repo).casefold()}#{m.group(2)}"


def merge_order(rows: list[dict]) -> list[str]:
    names = [r["name"] for r in rows]
    file_rows: dict[str, set[str]] = {}
    for r in rows:
        for f in r.get("files") or []:
            file_rows.setdefault(f, set()).add(r["name"])
    shared = {r["name"]: sum(1 for f in set(r.get("files") or []) if len(file_rows[f]) > 1) for r in rows}
    deps = {r["name"]: [d for d in r["deps"] if d in names] for r in rows}
    done: list[str] = []
    while len(done) < len(names):
        ready = [n for n in names if n not in done and all(d in done for d in deps[n])]
        if not ready:
            raise InputError("依存が循環していて順を出せない")
        done.append(min(ready, key=lambda n: (shared[n], names.index(n))))
    return done


def cycles(names: list[str], deps: dict[str, list[str]]) -> list[list[str]]:
    def reach(start: str) -> set[str]:
        seen: set[str] = set()
        stack = list(deps[start])
        while stack:
            n = stack.pop()
            if n not in seen:
                seen.add(n)
                stack.extend(deps[n])
        return seen

    reachable = {n: reach(n) for n in names}
    groups: list[list[str]] = []
    for n in names:
        if n in reachable[n] and not any(n in g for g in groups):
            groups.append([m for m in names if m == n or (m in reachable[n] and n in reachable[m])])
    return groups


def _exclude(rows: list[dict], issue_deps: dict, states: dict, in_plan: set[str]) -> tuple[dict, dict]:
    """読めない依存先から外す issue と行を、変化が無くなるまで伝える。"""
    excluded: dict[str, str] = {}
    for i in sorted(in_plan):
        bad = [d for d in issue_deps.get(i, []) if d not in in_plan and states.get(d) == "unreadable"]
        if bad:
            excluded[i] = f"依存先 {', '.join(bad)} が読めない"
    removed: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for i in sorted(in_plan - excluded.keys()):
            gone = [d for d in issue_deps.get(i, []) if d in excluded]
            if gone:
                excluded[i] = f"依存先 {', '.join(gone)} を外した"
                changed = True
        for r in rows:
            if r["name"] in removed:
                continue
            gone_rows = [d for d in r["deps"] if d in removed]
            if r["issues"] and all(i in excluded for i in r["issues"]):
                removed[r["name"]] = "issue が全部外れた"
                changed = True
            elif gone_rows:
                removed[r["name"]] = f"依存先 {', '.join(gone_rows)} を外した"
                changed = True
        for i in sorted(in_plan - excluded.keys()):
            holders = [r["name"] for r in rows if i in r["issues"]]
            if all(n in removed for n in holders):
                excluded[i] = f"載っていた行 {', '.join(holders)} を外した"
                changed = True
    return excluded, removed


def _derive_deps(rows: list[dict], issue_deps: dict, states: dict) -> list[str]:
    """行の依存先に issue の明示の依存先が載る行を合わせ、対象の外の open な依存先を unresolved に置く。"""
    issue_rows: dict[str, list[str]] = {}
    for r in rows:
        for i in r["issues"]:
            issue_rows.setdefault(i, []).append(r["name"])
    names = [r["name"] for r in rows]
    errors: list[str] = []
    for r in rows:
        deps = list(r["deps"])
        unresolved: list[str] = []
        for i in r["issues"]:
            for d in issue_deps.get(i, []):
                if d in issue_rows:
                    deps += [n for n in issue_rows[d] if n != r["name"]]
                elif states.get(d) == "open" and d not in unresolved:
                    unresolved.append(d)
        unknown_rows = [d for d in deps if d not in names]
        if unknown_rows:
            errors.append(f"{r['name']}: 依存先の行 {', '.join(unknown_rows)} が計画に無い")
        r["deps"] = [d for k, d in enumerate(deps) if d in names and d not in deps[:k]]
        r["unresolved"] = unresolved
    return errors


def _derive_fields(rows: list[dict], default: str, split_count: dict[str, int]) -> list[str]:
    """名前を検査し、分岐元 / target・取り込む前段・issue 参照を導く。"""
    by_name = {r["name"]: r for r in rows}
    errors: list[str] = []
    for r in rows:
        if not NAME_RE.fullmatch(r["name"]) or len(r["name"]) > NAME_MAX:
            errors.append(f"{r['name']}: 名前は cl- で始まり、ASCII の英数字とハイフンで {NAME_MAX} 字以内")
        if not r["deps"]:
            r["target"], r["merge_in"] = default, []
        elif len(r["deps"]) == 1:
            r["target"], r["merge_in"] = by_name[r["deps"][0]]["branch"], []
        elif r["base"] in r["deps"]:
            r["target"] = by_name[r["base"]]["branch"]
            r["merge_in"] = [by_name[d]["branch"] for d in r["deps"] if d != r["base"]]
        else:
            errors.append(f"{r['name']}: 依存先が複数 ({', '.join(r['deps'])}) あるので base に target にする行を書く")
        if "target" in r:
            r["base_branch"] = r["target"]
        r["issue_refs"] = [f"{'Closes' if split_count[i] == 1 else 'Refs'} {i}" for i in r["issues"]]
        del r["base"]
    return errors


def build(spec: dict, dep_info: dict) -> tuple[dict, int]:
    for key in ("repo", "default_branch", "rows"):
        if key not in spec:
            raise InputError(f"行の組の file に {key} が無い")
    repo, default = spec["repo"], spec["default_branch"]
    rows = [
        {
            **r,
            "issues": [ref_of(i, repo) for i in r.get("issues") or []],
            "deps": list(r.get("deps") or []),
            "base": r.get("base"),
            "files": list(r.get("files") or []),
        }
        for r in spec["rows"]
    ]
    dups = [f"{k} {v}" for k in ("name", "branch") for v in sorted({r[k] for r in rows}, key=str) if [x[k] for x in rows].count(v) > 1]
    if dups:
        return {"errors": [f"行の {d} が重複している" for d in dups]}, 1
    issue_deps: dict[str, list[str]] = dep_info.get("issues", {})
    states: dict[str, str] = dep_info.get("states", {})
    in_plan = {i for r in rows for i in r["issues"]}
    if in_plan and not dep_info:
        raise InputError("issue のある行の組には --deps (read-issues.py deps の出力) が要る")
    missing_deps = sorted(i for i in in_plan if i not in issue_deps)
    if missing_deps:
        raise InputError(f"deps の出力に無い issue: {', '.join(missing_deps)} (read-issues.py deps の --targets と --repo を確かめる)")
    unknown = sorted({d for i in in_plan for d in issue_deps.get(i, []) if d not in in_plan and d not in states})
    if unknown:
        raise InputError(f"状態の分からない依存先: {', '.join(unknown)} (read-issues.py deps に取得結果を足して呼び直す)")
    # Closes / Refs は、行を外す前に割った行の数で決める (外した行の分の作業は残っている)
    split_count = {i: sum(1 for r in rows if i in r["issues"]) for i in in_plan}

    excluded, removed = _exclude(rows, issue_deps, states, in_plan)
    rows = [r for r in rows if r["name"] not in removed]
    for r in rows:
        r["issues"] = [i for i in r["issues"] if i not in excluded]
    errors = _derive_deps(rows, issue_deps, states)
    found = cycles([r["name"] for r in rows], {r["name"]: r["deps"] for r in rows})
    if found:
        by_name = {r["name"]: r for r in rows}
        return {
            "cycles": [{"rows": g, "split": [n for n in g if len(by_name[n]["issues"]) > 1]} for g in found],
            "errors": errors,
        }, 1
    errors += _derive_fields(rows, default, split_count)
    if errors:
        return {"errors": errors}, 1
    return {
        "repo": repo,
        "default_branch": default,
        "rows": rows,
        "excluded": [{"ref": i, "reason": why} for i, why in excluded.items()],
        "removed_rows": [{"name": n, "reason": why} for n, why in removed.items()],
    }, 0


def retarget(plan: dict, remove: list[str], done: list[str]) -> tuple[dict, int]:
    by_name = {r["name"]: r for r in plan["rows"]}
    missing = [n for n in remove + done if n not in by_name]
    if missing:
        raise InputError(f"計画に無い行: {', '.join(missing)}")
    removed_branch = {by_name[n]["branch"]: by_name[n] for n in remove}
    changes, choose = [], []
    for r in plan["rows"]:
        if r["name"] in remove or r["target"] not in removed_branch:
            continue
        left = [d for d in r["deps"] if d in by_name and d not in remove and d not in done]
        if len(left) > 1:
            choose.append({"name": r["name"], "candidates": [by_name[d]["branch"] for d in left]})
            continue
        if left:
            to = by_name[left[0]]["branch"]
        else:
            to, seen = r["target"], set()
            while to in removed_branch and to not in seen:
                seen.add(to)
                to = removed_branch[to]["target"]
        changes.append({"name": r["name"], "from": r["target"], "to": to})
    return {"changes": changes, "choose": choose}, (1 if choose else 0)


def check_merge_order(plan: dict, requested: list[str], done: list[str]) -> tuple[dict, int]:
    by_name = {r["name"]: r for r in plan["rows"]}
    missing = [n for n in requested + done if n not in by_name]
    if missing:
        raise InputError(f"計画に無い行: {', '.join(missing)}")
    violations = [
        {"name": n, "before": d}
        for k, n in enumerate(requested)
        for d in by_name[n]["deps"]
        if d not in done and d not in requested[:k]
    ]
    recommended = [n for n in merge_order(plan["rows"]) if n in requested]
    return {"violations": violations, "recommended": recommended}, (1 if violations else 0)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="行の組から CL 計画を導く")
    b.add_argument("spec", type=Path)
    b.add_argument("--deps", type=Path, help="read-issues.py deps の出力の file")
    t = sub.add_parser("retarget", help="外す行を target にしている行の新しい target を出す")
    t.add_argument("plan", type=Path)
    t.add_argument("--remove", nargs="+", required=True)
    t.add_argument("--done", nargs="*", default=[])
    c = sub.add_parser("check-merge-order", help="頼まれた merge の順が依存に反するかを見る")
    c.add_argument("plan", type=Path)
    c.add_argument("requested", nargs="+")
    c.add_argument("--done", nargs="*", default=[])
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.cmd == "build":
            result, code = build(load_json(args.spec), load_json(args.deps) if args.deps else {})
        else:
            plan = load_json(args.plan)
            if args.cmd == "retarget":
                result, code = retarget(plan, args.remove, args.done)
            else:
                result, code = check_merge_order(plan, args.requested, args.done)
    except (InputError, KeyError) as e:
        print(f"入力の誤り: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
