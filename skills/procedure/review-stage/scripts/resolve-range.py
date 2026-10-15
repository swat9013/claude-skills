#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""review-stage の巡が読む範囲 (base / head) を決め、JSON で stdout へ出す。

決めるのは git の状態と前巡の出力 file から機械的に決まることだけ — 初回か再レビューか、
base / head の SHA、止まるならその理由、出力へ積む注記行。段階 (docs / 軽量 / 標準) は
hunk を読んで決める判定なので本 script は持たない。

止まる巡も exit 0 で JSON を返す (`stop` が埋まる)。exit 非 0 は git 自体が動かないなど、
JSON を組めなかったときだけ。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

# 停止理由の token。review-stage の SKILL.md の停止テンプレートと突き合わせる
# (`reviewer 未起動` は reviewer を立てる側が出すので本 script は出さない)
STOP_UNCOMMITTED = "未 commit"
STOP_OTHER = "その他"
STOP_TOKENS = (STOP_UNCOMMITTED, STOP_OTHER)

STAGES = ("docs", "軽量", "標準")

# 注記のうち、SKILL.md が以降の手順を変える合図として読むもの (先頭の語)
NOTE_NO_INCREMENT = "増分なし:"
NOTE_WIDENED = "増分に絞れず:"
BRANCHING_NOTE_PREFIXES = (NOTE_NO_INCREMENT, NOTE_WIDENED)
NOTE_GUESSED_REMOTE = "起点 remote を推定:"
NOTE_LOCAL_UPSTREAM = "起点を local branch に:"
NOTE_LOCAL_UPSTREAM_UNUSABLE = "local の追跡先を使えず:"
# base= を省いた巡の起点の決め方を示す注記。停止した巡は notes を出さないので、これだけは detail へ写す
START_POINT_NOTE_PREFIXES = (NOTE_GUESSED_REMOTE, NOTE_LOCAL_UPSTREAM, NOTE_LOCAL_UPSTREAM_UNUSABLE)

PREV_REREVIEW_LINE = re.compile(r"^再レビュー: (.*)$", re.MULTILINE)
PREV_REVIEWED_LINE = re.compile(r"^reviewed: ([0-9a-f]{7,64})\.\.([0-9a-f]{7,64})\s*$", re.MULTILINE)
PREV_STAGE_LINE = re.compile(r"^段階: (\S+)", re.MULTILINE)


class Stop(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _other(summary: str, detail: str) -> Stop:
    return Stop(f"{STOP_OTHER}: {summary}", detail)


@dataclass
class Round:
    """1 巡ぶんの解決結果。stop で抜けても、そこまでに解決した値を出力へ残す。"""

    mode: str = "初回"
    requested: str = "args なし"
    base: str | None = None
    head: str | None = None
    prev_stage: str | None = None
    notes: list[str] = field(default_factory=list)

    def reviewed(self) -> str:
        if self.base and self.head:
            return f"{self.base}..{self.head}"
        return f"未解決: {self.requested}"


@dataclass
class Prev:
    rereview: str
    stage: str | None
    head: str | None

    @property
    def requires_rereview(self) -> bool:
        return self.rereview.startswith("要")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _git_out(*args: str) -> str:
    """失敗しない前提の git。失敗は script の外の異常なので例外で落とす。"""
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def _commit_sha(ref: str) -> str | None:
    proc = _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return proc.stdout.strip() if proc.returncode == 0 else None


def _merge_base(a: str, b: str) -> str | None:
    proc = _git("merge-base", a, b)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _has_diff(base: str, head: str) -> bool:
    """`git diff --quiet` は差分ありを exit 1 で返す。それ以外の非 0 は git 自体の失敗。"""
    proc = _git("diff", "--quiet", f"{base}...{head}")
    if proc.returncode not in (0, 1):
        raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)
    return proc.returncode == 1


def _head_sha() -> str:
    sha = _commit_sha("HEAD")
    if sha is None:
        raise _other("範囲が解決しない", "HEAD が commit を指していない (commit が 1 つも無い branch)")
    return sha


def _is_ancestor(base: str, head: str) -> bool:
    return _git("merge-base", "--is-ancestor", base, head).returncode == 0


def _config(key: str) -> str:
    return _git("config", key).stdout.strip()


def _start_branch_ref(notes: list[str]) -> str:
    """base= を省いた巡の起点 branch の ref。現在 branch の upstream が local branch なら、その local branch。
    それ以外は既定の remote の既定ブランチ (remote-tracking ref)。remote 名も branch 名も repo から引く。"""
    branch = _git("symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    configured = _config(f"branch.{branch}.remote") if branch else ""
    # branch.<name>.remote から remote を決められなかった理由。origin を推定したときの注記に載せる
    if not branch:
        unusable = "detached HEAD"
    elif not configured:
        unusable = f"branch.{branch}.remote が未設定"
    else:
        unusable = f"branch.{branch}.remote={configured} が remote 名でない"
    if configured == ".":
        # 利用者が local の追跡先を宣言している (`git switch -c <b> --track <local>`)。remote の数より先に見る
        tracked = _config(f"branch.{branch}.merge")
        if tracked and _commit_sha(tracked) is not None:
            notes.append(
                f"{NOTE_LOCAL_UPSTREAM} {tracked} (branch.{branch}.remote=.) — 違うなら base=<branch> で起点 branch を渡す"
            )
            return tracked
        unusable = (
            f"branch.{branch}.remote=. の追跡先 {tracked} が commit に解決しない"
            if tracked
            else f"branch.{branch}.remote=. で branch.{branch}.merge が未設定"
        )
    remote, guessed = _default_remote(branch, configured)
    # 起点の決め方は 1 つの決定につき 1 行。<remote>/HEAD を引く前に積む (それが無くて止まる巡でも detail に写る)
    if guessed:
        notes.append(f"{NOTE_GUESSED_REMOTE} {remote} ({unusable}) — 違うなら base=<branch> で起点 branch を渡す")
    elif configured == ".":
        notes.append(
            f"{NOTE_LOCAL_UPSTREAM_UNUSABLE} {unusable} — {remote} の既定ブランチを起点にする。違うなら base=<branch> で起点 branch を渡す"
        )
    proc = _git("symbolic-ref", "--quiet", f"refs/remotes/{remote}/HEAD")
    if proc.returncode != 0:
        raise _other(
            "起点 branch を解決できない",
            f"{remote}/HEAD が無い — `git remote set-head {remote} --auto` で設定するか、base=<branch> で起点 branch を渡す",
        )
    return proc.stdout.strip()


def _default_remote(branch: str, configured: str) -> tuple[str, bool]:
    """既定ブランチを引く remote と、それを名前 (origin) から推定したか。
    branch は現在 branch (detached HEAD なら空)、configured はその branch.<name>.remote。"""
    remotes = _git_out("remote").split()
    if not remotes:
        raise _other("起点 branch を解決できない", "remote が無い — base=<branch> で起点 branch を渡す")
    if len(remotes) == 1:
        return remotes[0], False
    if configured in remotes:
        return configured, False
    if (declared := _config("checkout.defaultRemote")) in remotes:
        # git が「remote が複数で曖昧なときに勝つ remote」として持つ宣言。宣言なので推定の注記は出さない
        return declared, False
    if "origin" in remotes:
        # clone の既定の remote 名を正本とみなす。detached HEAD で動く worker (claude-dispatcher の
        # pull request の worker) で、base= を省いた初回と再レビューの範囲の広げ直しを止めないため (#1251)。
        # 推定なので、origin が正本でない構成 (fork が origin) の読み手が気づけるよう呼び出し元が注記に残す
        # (止まった巡では resolve が detail へ写す)
        return "origin", True
    tried = ["checkout.defaultRemote", "origin"]
    if branch:
        tried.insert(0, f"branch.{branch}.remote")
    raise _other(
        "起点 branch を解決できない",
        f"remote が {len(remotes)} 個で {'・'.join(tried)} のどれからも既定の remote を決められない — base=<branch> で起点 branch を渡す",
    )


def _start_branch_sha(base_branch: str | None, notes: list[str]) -> str:
    ref = base_branch or _start_branch_ref(notes)
    sha = _commit_sha(ref)
    if sha is None:
        raise _other("起点 branch を解決できない", f"{ref} が commit に解決しない")
    return sha


def _read_prev(path: str) -> Prev:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise _other("前巡の出力 file を読めない", f"{path}: {e.strerror}") from e
    except UnicodeDecodeError as e:
        raise _other("前巡の出力 file を読めない", f"{path}: UTF-8 として読めない ({e.reason})") from e
    rereview = PREV_REREVIEW_LINE.search(text)
    if rereview is None:
        raise _other("前巡の出力 file を読めない", f"{path} に `再レビュー:` 行が無い")
    prev = Prev(rereview=rereview.group(1).strip(), stage=None, head=None)
    if not prev.requires_rereview:
        return prev
    reviewed = PREV_REVIEWED_LINE.search(text)
    stage = PREV_STAGE_LINE.search(text)
    if reviewed is None or stage is None or stage.group(1) not in STAGES:
        raise _other(
            "前巡の出力 file を読めない",
            f"{path} は `再レビュー: 要` なのに `reviewed: <base>..<head>` か `段階: <{' | '.join(STAGES)}>` の行が無い",
        )
    prev.head = reviewed.group(2)
    prev.stage = stage.group(1)
    return prev


def _check_worktree(r: Round) -> None:
    """head が HEAD の巡だけ見る。どの観点も commit 済みの差分しか読まない。"""
    if r.head != _commit_sha("HEAD"):
        return
    tracked: list[str] = []
    untracked: list[str] = []
    # -z は path を引用符も escape も付けずに NUL 区切りで返す (呼び出し元が path を照合できる)。
    # rename / copy は「新しい path」の後に「元の path」の record が続くので、元の側は読み飛ばす
    status = subprocess.run(
        ["git", "status", "--porcelain", "-z"], capture_output=True, text=True, check=True,
    )
    records = iter(status.stdout.split("\0"))
    for record in records:
        if not record:
            continue
        state, path = record[:2], record[3:]
        (untracked if state == "??" else tracked).append(path)
        if "R" in state or "C" in state:
            next(records, None)
    if tracked:
        raise Stop(STOP_UNCOMMITTED, f"tracked file に未 commit の変更がある: {', '.join(tracked)}")
    if untracked:
        r.notes.append(f"未追跡: {', '.join(untracked)}")


def _resolve_first(r: Round, range_arg: str | None, base_branch: str | None) -> None:
    if range_arg is None:
        r.head = _head_sha()
        r.base = _merge_base(r.head, _start_branch_sha(base_branch, r.notes))
    else:
        given_base, sep, given_head = range_arg.partition("..")
        if not sep or given_head.startswith("."):
            raise _other("範囲が解決しない", f"{range_arg} は <base>..<head> の形でない")
        # git と同じく、省いた側は HEAD と読む (`A..` / `..B`)
        given_base, given_head = given_base or "HEAD", given_head or "HEAD"
        base_sha, head_sha = _commit_sha(given_base), _commit_sha(given_head)
        if base_sha is None or head_sha is None:
            raise _other("範囲が解決しない", f"{range_arg} の {given_base if base_sha is None else given_head} が commit に解決しない")
        r.head = head_sha
        r.base = _merge_base(base_sha, head_sha)
        if r.base != base_sha:
            r.notes.append(f"範囲を置き換え: {base_sha} → {r.base}")
    if r.base is None:
        raise _other("範囲が解決しない", "base と head に共通の祖先が無い")
    _check_worktree(r)
    if not _has_diff(r.base, r.head):
        raise _other("差分が空", f"{r.base}..{r.head} に差分が無い")


def _resolve_rereview(r: Round, prev: Prev, range_arg: str | None, base_branch: str | None) -> None:
    r.mode = "再レビュー"
    r.prev_stage = prev.stage
    if range_arg is not None:
        r.notes.append(f"範囲を使わず: prev= があるので {range_arg} ではなく前巡の head から見た")
    r.head = _head_sha()
    r.base = _commit_sha(prev.head)
    if r.base is None:
        raise _other("範囲が解決しない", f"前巡の head {prev.head} が commit に解決しない")
    _check_worktree(r)
    if not _is_ancestor(r.base, r.head):
        r.base = _merge_base(r.head, _start_branch_sha(base_branch, r.notes))
        if r.base is None:
            raise _other("範囲が解決しない", "HEAD と起点 branch に共通の祖先が無い")
        r.notes.append(f"{NOTE_WIDENED} 前巡の head が head の祖先でない — 起点 branch からの全差分を見た")
        # 起点 branch からの全差分が空なら、前巡の指摘が指すコードごと無い。初回と同じく止める
        if not _has_diff(r.base, r.head):
            raise _other("差分が空", f"{r.base}..{r.head} に差分が無い (前巡の head も残っていない)")
    elif not _has_diff(r.base, r.head):
        r.notes.append(f"{NOTE_NO_INCREMENT} 前巡の head から差分が無い")


def resolve(range_arg: str | None, base_branch: str | None, prev_path: str | None) -> dict:
    # git repo の外なら範囲を決める前提が無い。Stop ではなく git の失敗として 1 行で返す
    _git_out("rev-parse", "--git-dir")
    r = Round()
    stop: Stop | None = None
    # 解決できなかった巡の `reviewed:` は、渡された args をそのまま並べる
    passed = [a for a in (range_arg, base_branch and f"base={base_branch}", prev_path and f"prev={prev_path}") if a]
    if passed:
        r.requested = " ".join(passed)
    try:
        prev = _read_prev(prev_path) if prev_path is not None else None
        if prev is not None and prev.requires_rereview:
            _resolve_rereview(r, prev, range_arg, base_branch)
        else:
            if prev is not None:
                r.notes.append(
                    f"前巡を数えず: 前巡の `再レビュー:` 行が `要` で始まらない ({prev.rereview}) — 初回として回した"
                )
            _resolve_first(r, range_arg, base_branch)
    except Stop as e:
        stop = e
        # 停止した巡は notes を出さない。起点を推定した・local branch にした巡は、止まった理由を問わず
        # 起点の決め方を detail で読めるようにする (起点が誤って差分が空になった巡を含む)。`未 commit` の
        # detail は末尾の file 列を読み手が照合するので、注記は先頭に置く
        start_point = [note for note in r.notes if note.startswith(START_POINT_NOTE_PREFIXES)]
        if start_point:
            stop.detail = " / ".join([*start_point, stop.detail])
    return {
        "mode": r.mode,
        "base": r.base,
        "head": r.head,
        "reviewed": r.reviewed(),
        "prev_stage": r.prev_stage,
        "stop": None if stop is None else {"reason": stop.reason, "detail": stop.detail},
        "notes": r.notes,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--range", dest="range_arg", help="<base>..<head> (review-stage の範囲 args)")
    ap.add_argument("--base-branch", help="起点 branch (review-stage の base=)。省略時は既定ブランチ (現在 branch の upstream が local branch なら、その local branch)")
    ap.add_argument("--prev", help="前巡の出力を保存した file (review-stage の prev=)")
    args = ap.parse_args(argv)
    try:
        result = resolve(args.range_arg, args.base_branch, args.prev)
    except subprocess.CalledProcessError as e:
        # 呼び出し元は stderr の 1 行目を停止理由に載せるので、traceback ではなく 1 行で出す
        reason = (e.stderr or "").strip().splitlines()
        print(f"{' '.join(e.cmd)} が失敗 (exit {e.returncode}): {reason[0] if reason else '出力なし'}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — 想定外の失敗も 1 行目に要約を出す (呼び出し元は 1 行目だけを読む)
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()  # 2 行目以降に発生箇所を残す
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
