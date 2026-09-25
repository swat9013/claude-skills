#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""dispatcher の実行状況を 1 コマンドで見る (`ps` / `watch`)。人が撃つ読み取り専用の script。

`log.jsonl` の `spawned` を起点に、process の一覧・`claude agents --json`・cwd の clone の作業ツリー・
tracker / CL host を読み直して「今」の worker 一覧を組む。自分では何も書かない (`tick.lock` が無ければ作らない)。

載せる worker は、`spawned` の起動記録のうち issue に `dispatcher:wip` が付いているか process が生きているもの
(終了して wip も剥がれた worker は載せない)。状態の正本は tracker にあるので gh は毎回叩く。外部 process が
失敗した列は `?` にして表は出し (落とさない)、理由を注記に残す。

置き場・config・gh の解決は dispatcher-tick.py と同じ実装を読み込んで使う (綴りを二重に持たない。
読み込む側なので依存も dispatcher-tick.py に揃える)。
"""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def _load_tick():
    """同じ dir の dispatcher-tick.py を module として読む (tick テストの module 名と衝突しない名前で)。"""
    spec = importlib.util.spec_from_file_location("_dispatcher_tick_for_status", Path(__file__).with_name("dispatcher-tick.py"))
    module = importlib.util.module_from_spec(spec)
    # dataclass が型注釈の解決で sys.modules を引くので、exec 前に登録する
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tick = _load_tick()
GhError = tick.GhError

EXIT_OK = 0
EXIT_USAGE = 2

# 外部 process が失敗して値を確かめられない列の値 (表でも JSON でも同じ綴り)
UNKNOWN = "?"
PROBE_TIMEOUT_SEC = 30
DEFAULT_INTERVAL_SEC = 5.0
COLUMNS = ("ISSUE", "KIND", "STATE", "ELAPSED", "SESSION", "BRANCH", "WIP", "CL", "TICK")
CLEAR_SCREEN = "\033[H\033[2J"


class StatusError(Exception):
    """指定された project が無い / project が 1 つも無い。表を出さない。"""


class ProbeError(Exception):
    """git / claude / ps が失敗した。該当列を `?` にする。"""


@dataclass(frozen=True)
class Probes:
    """外部 process の境界。tests はここを差し替える。"""

    gh: Callable[[list[str]], str]
    git: Callable[[list[str]], str]
    claude_agents: Callable[[], str]
    # pid → command 行。生きている process の一覧を 1 回で読む (起動記録ごとに ps を撃たない)
    processes: Callable[[], dict[int, str]]


# --- 外部 process ---


def _run(args: list[str], env) -> str:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SEC, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"{' '.join(args[:2])}: {exc}") from exc
    if completed.returncode != 0:
        raise ProbeError(f"{' '.join(args[:2])} failed (exit {completed.returncode}): {completed.stderr.strip()}")
    return completed.stdout


def make_probes(env) -> Probes:
    def processes():
        table = {}
        for line in _run(["ps", "-A", "-o", "pid=,command="], env).splitlines():
            pid, _, command = line.strip().partition(" ")
            table[int(pid)] = command
        return table

    return Probes(
        gh=tick.make_gh(env),
        git=lambda args: _run(["git", *args], env),
        claude_agents=lambda: _run(["claude", "agents", "--json"], env),
        processes=processes,
    )


# --- 収集 ---


def project_dirs(root: Path, project: str | None) -> list[Path]:
    if project is not None:
        directory = root / project
        if not directory.is_dir():
            raise StatusError(f"project の dir が無い: {directory}")
        return [directory]
    directories = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    if not directories:
        raise StatusError(f"project が 1 つも無い: {root}")
    return directories


def collect(project_dir: Path, probes: Probes, now: datetime) -> dict:
    """1 project の「今」を組む。返り値は表と `--json` の共通の像。"""
    notes: list[str] = []
    records, spawns = _read_log(project_dir, notes)
    report = {"project": project_dir.name, "tick": _tick_state(project_dir, records), "workers": [], "notes": notes}
    if not spawns:
        return report

    config = _load_config(project_dir, notes)
    wip_numbers = _wip_numbers(config, probes.gh, notes)
    processes = _probe(probes.processes, notes, "process の一覧を読めない — wip の付いた worker だけを載せた")
    for spawn in sorted(spawns, key=lambda s: (s["issue"], s["tick_ts"])):
        alive = UNKNOWN if processes == UNKNOWN else _is_worker_process(processes.get(spawn["pid"]), spawn)
        wip = UNKNOWN if wip_numbers == UNKNOWN else spawn["issue"] in wip_numbers
        if not _is_listed(alive, wip):
            continue
        report["workers"].append(
            {
                "issue": spawn["issue"],
                "kind": spawn["kind"],
                "pid": spawn["pid"],
                "state": UNKNOWN if alive == UNKNOWN else ("running" if alive else "exited"),
                "elapsed_sec": int((now - datetime.fromisoformat(spawn["tick_ts"])).total_seconds()),
                "wip": wip,
                "tick_ts": spawn["tick_ts"],
                "session_id": spawn["session_id"],
            }
        )

    if report["workers"]:
        issues = sorted({w["issue"] for w in report["workers"]})
        sessions = _sessions(probes, notes)
        branches = _branches(config, probes.git, issues, notes)
        cls = _cls(config, probes.gh, issues, notes)
        for worker in report["workers"]:
            session_id = worker.pop("session_id")
            worker["session"] = sessions if sessions == UNKNOWN else sessions.get(session_id or worker["pid"])
            worker["branch"] = branches if branches == UNKNOWN else branches.get(worker["issue"])
            worker["cl"] = cls if cls == UNKNOWN else cls[worker["issue"]]
    return report


def _read_log(project_dir: Path, notes: list[str]) -> tuple[list[dict], list[dict]]:
    """log.jsonl を読み、行の列と起動記録の列を返す。形の違う行 (途中で切れた行・旧形式) は飛ばして注記に残す。"""
    path = project_dir / tick.LOG_FILENAME
    if not path.exists():
        return [], []
    records, spawns, broken = [], [], 0
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or not isinstance(record.get("ts"), str):
                raise ValueError("ts を持つ object でない")
            entries = [] if "actor" in record else [_spawn_entry(e, record["ts"]) for e in record.get("spawned", [])]
        except (ValueError, KeyError, TypeError):
            broken += 1
            continue
        records.append(record)
        spawns += entries
    if broken:
        notes.append(f"{path} の読めない {broken} 行を飛ばした")
    return records, spawns


def _spawn_entry(entry: dict, tick_ts: str) -> dict:
    datetime.fromisoformat(tick_ts)  # ELAPSED の起点。読めない ts は行ごと飛ばす
    if not isinstance(entry["issue"], int) or not isinstance(entry["pid"], int) or not isinstance(entry["kind"], str):
        raise ValueError(f"spawned の要素は issue: int / kind: str / pid: int: {entry}")
    # session_id は tick が発行し始める前の行には無い
    return {"issue": entry["issue"], "kind": entry["kind"], "pid": entry["pid"], "tick_ts": tick_ts, "session_id": entry.get("session_id")}


def _is_listed(alive, wip) -> bool:
    """載せる起動記録: wip の付いた issue の起動か、process が生きている起動。確かめられなかった (`?`) だけでは載せない
    — 載せると log に残る過去の起動が全部並ぶ。"""
    return alive is True or wip is True


def _is_worker_process(command: str | None, spawn: dict) -> bool:
    """pid の process がその worker か。pid は再利用されるので、tick が渡した session id が command 行に在るかで見る。

    session id の無い旧形式の起動記録は command に claude を含むかで見る。この分岐は log.jsonl に旧形式の
    spawned 行が残っている間だけ要る (log を消すか、旧形式の行が全部終了済みの worker になったら消してよい)。"""
    if command is None:
        return False
    return spawn["session_id"] in command if spawn["session_id"] else "claude" in command


def _tick_state(project_dir: Path, records: list[dict]) -> dict:
    last = next((r for r in reversed(records) if "result" in r and "actor" not in r), None)
    return {
        "running": _lock_held(project_dir / tick.LOCK_FILENAME),
        "last_ts": last["ts"] if last else None,
        "last_result": last["result"] if last else None,
    }


def _lock_held(path: Path) -> bool:
    """tick が lock を取っているか。無い file は作らない。

    shared lock を一瞬だけ取って確かめる — その一瞬に cron の tick が重なると、その tick は `locked` の 1 行を
    残して終わる (次の周期で普通に回る)。取らずに覗く手段が flock には無い (/proc/locks は Linux だけ)。"""
    if not path.exists():
        return False
    with open(path) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock, fcntl.LOCK_UN)
    return False


def _load_config(project_dir: Path, notes: list[str]):
    try:
        return tick.load_config(project_dir / tick.CONFIG_FILENAME)
    except tick.ConfigError as exc:
        notes.append(f"config を読めない — wip / CL / BRANCH は ? ({exc})")
        return None


def _wip_numbers(config, gh, notes: list[str]):
    if config is None:
        notes.append("wip を読めない — process の生きている worker だけを載せた")
        return UNKNOWN
    try:
        listed = json.loads(
            gh([
                "issue", "list", "-R", config.issue_repo, "--label", tick.WIP_LABEL, "--state", "open",
                "--limit", str(tick.ISSUE_LIST_LIMIT), "--json", "number",
            ])
        )
        numbers = {issue["number"] for issue in listed}
    except (GhError, ValueError, KeyError, TypeError) as exc:
        notes.append(f"wip を読めない — process の生きている worker だけを載せた ({exc})")
        return UNKNOWN
    if len(listed) >= tick.ISSUE_LIST_LIMIT:
        # 上限で切り詰められた一覧を全量とみなすと、窓の外の wip issue が wip なしに見える
        notes.append(f"wip の付いた issue が {tick.ISSUE_LIST_LIMIT} 件以上あり全量を読めない — process の生きている worker だけを載せた")
        return UNKNOWN
    return numbers


def _sessions(probes: Probes, notes: list[str]):
    """session id → `claude agents --json` の行。

    pid の key は session id の無い旧形式の起動記録のためだけにある (撤去条件は `_is_worker_process` の旧形式分岐と同じ)。"""
    try:
        sessions = {}
        for row in json.loads(probes.claude_agents()):
            brief = {"id": row.get("id"), "status": row.get("status"), "state": row.get("state")}
            if row.get("sessionId"):
                sessions[row["sessionId"]] = brief
            sessions.setdefault(row["pid"], brief)
        return sessions
    except (ProbeError, ValueError, KeyError, TypeError, AttributeError) as exc:
        notes.append(f"claude agents を読めない — SESSION は ? ({exc!r})")
        return UNKNOWN


def _branches(config, git, issues: list[int], notes: list[str]):
    """issue → cwd の clone にある作業ツリーの branch と、origin/HEAD からの ahead 数。作業ツリーが無い issue は載らない。

    cwd の clone が CL 置き場の repo でなければ、同じ番号の別 repo の branch を拾うので引かない。"""
    if config is None:
        return UNKNOWN
    try:
        origin = git(["remote", "get-url", "origin"]).strip().removesuffix(".git")
        if not origin.endswith((f"/{config.cl_repo}", f":{config.cl_repo}")):
            notes.append(f"cwd の clone ({origin}) は {config.cl_repo} でない — BRANCH は ? (その clone を cwd にして撃つ)")
            return UNKNOWN
        listed = {
            line.removeprefix("branch refs/heads/")
            for line in git(["worktree", "list", "--porcelain"]).splitlines()
            if line.startswith("branch refs/heads/")
        }
    except ProbeError as exc:
        notes.append(f"cwd の clone を読めない — BRANCH は ? ({exc})")
        return UNKNOWN
    base = _probe(lambda: git(["rev-parse", "--abbrev-ref", "origin/HEAD"]).strip(), notes, "origin/HEAD を読めない — ahead は ?")
    branches = {}
    for issue in issues:
        name = tick.WORKER_BRANCH.format(issue=issue)
        if name in listed:
            ahead = UNKNOWN if base == UNKNOWN else _probe(
                lambda: int(git(["rev-list", "--count", f"{base}..{name}"]).strip()), notes, f"{name} の ahead を数えられない"
            )
            branches[issue] = {"name": name, "ahead": ahead}
    return branches


def _cls(config, gh, issues: list[int], notes: list[str]):
    """issue → worker の branch の最新 CL (番号と state)。CL の無い issue は None。"""
    if config is None:
        return UNKNOWN
    owner, name = config.cl_repo.split("/", 1)
    fields = "".join(
        f' i{issue}: pullRequests(headRefName: "{tick.WORKER_BRANCH.format(issue=issue)}", first: 1,'
        " orderBy: {field: CREATED_AT, direction: DESC}) { nodes { number state } }"
        for issue in issues
    )
    query = f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{{fields} }} }}"
    try:
        # -f は生文字列 (dispatcher-tick.py の PR 観測と同じ理由)
        repository = json.loads(gh(["api", "graphql", "-f", f"query={query}", "-f", f"owner={owner}", "-f", f"name={name}"]))[
            "data"
        ]["repository"]
        return {issue: next(iter(repository[f"i{issue}"]["nodes"]), None) for issue in issues}
    except (GhError, ValueError, KeyError, TypeError) as exc:
        notes.append(f"CL を読めない — CL は ? ({exc!r})")
        return UNKNOWN


def _probe(read, notes: list[str], consequence: str):
    """read の失敗を `?` にし、何が読めなかったかを理由付きで注記に残す (`?` だけを黙って出さない)。"""
    try:
        return read()
    except (ProbeError, ValueError) as exc:
        notes.append(f"{consequence} ({exc})")
        return UNKNOWN


# --- 描画 ---


def format_elapsed(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    return f"{seconds // 86400}d{seconds % 86400 // 3600:02d}h"


def _short_ts(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cell_session(session) -> str:
    if session in (None, UNKNOWN):
        return session or "-"
    # id / state は background の session だけが持つ (claude -p の worker は interactive)
    progress = "/".join(value for value in (session["status"], session["state"]) if value)
    return f"{session['id'] or '-'} {progress or '-'}"


def _cell_branch(branch) -> str:
    if branch in (None, UNKNOWN):
        return branch or "-"
    return f"+{branch['ahead']}"


def _cell_wip(wip) -> str:
    return wip if wip == UNKNOWN else ("yes" if wip else "no")


def _cell_cl(cl) -> str:
    if cl in (None, UNKNOWN):
        return cl or "-"
    return f"#{cl['number']} {cl['state']}"


def _render_project(report: dict) -> list[str]:
    state = report["tick"]
    last = f"{_short_ts(state['last_ts'])} {state['last_result']}" if state["last_ts"] else "なし"
    lines = [f"{report['project']}  {'tick 実行中' if state['running'] else '待機'}  最終 tick {last}"]
    lines += [f"  ! {note}" for note in report["notes"]]
    if not report["workers"]:
        return lines
    rows = [COLUMNS] + [
        (
            f"#{w['issue']}", w["kind"], w["state"], format_elapsed(w["elapsed_sec"]), _cell_session(w["session"]),
            _cell_branch(w["branch"]), _cell_wip(w["wip"]), _cell_cl(w["cl"]), _short_ts(w["tick_ts"]),
        )
        for w in report["workers"]
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(len(COLUMNS))]
    lines += ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows]
    return lines


def render_table(reports: list[dict]) -> str:
    return "\n\n".join("\n".join(_render_project(report)) for report in reports)


def render_json(reports: list[dict]) -> str:
    return json.dumps({"projects": reports}, ensure_ascii=False, indent=2)


def watch(render: Callable[[], str], *, interval: float, sleep=time.sleep, write=sys.stdout.write) -> None:
    """render の結果を interval 秒ごとに画面を消して描き直す。Ctrl-C で抜ける。"""
    try:
        while True:
            write(f"{CLEAR_SCREEN}{render()}\n")
            sleep(interval)
    except KeyboardInterrupt:
        return


def _positive_seconds(text: str) -> float:
    seconds = float(text)
    if seconds <= 0:
        # 0 は gh / git / claude を休みなく撃ち続け、tick と同じ gh の rate limit を食い潰す
        raise argparse.ArgumentTypeError(f"0 より大きい秒数が要る: {text}")
    return seconds


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="dispatcher の実行状況を見る (読み取り専用)。BRANCH 列は cwd の clone の作業ツリーを引くので、"
        "tick と同じく実装 repo の clone を cwd にして撃つ"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    ps = commands.add_parser("ps", help="今の tick の状態と worker 一覧を 1 回出す")
    ps.add_argument("project", nargs="?", help="省略すると <root> 配下の全 project")
    ps.add_argument("--json", action="store_true", dest="as_json", help="同じ内容を JSON で出す")
    watch_parser = commands.add_parser("watch", help="ps を周期的に描き直す (Ctrl-C で終わる)")
    watch_parser.add_argument("project", nargs="?", help="省略すると <root> 配下の全 project")
    watch_parser.add_argument("--interval", type=_positive_seconds, default=DEFAULT_INTERVAL_SEC, help="描き直す周期 (秒)")
    args = parser.parse_args(argv)

    env = tick.resolve_dependency_path(os.environ)
    root = tick.resolve_root(env)
    probes = make_probes(env)

    def reports():
        return [collect(d, probes, datetime.now(timezone.utc)) for d in project_dirs(root, args.project)]

    try:
        if args.command == "watch":
            watch(lambda: render_table(reports()), interval=args.interval)
        else:
            print(render_json(reports()) if args.as_json else render_table(reports()))
    except StatusError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
