#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""dispatcher の 1 tick: 宣言 config を読み、tracker / CL host を観測し、行動指示を導出する。

cron から起動される決定的な script (LLM なし)。持つのは**観測の正規化と、機械的に確定する
指示の導出まで**で、実行 (label 操作 / spawn / tracker への書き込み) は一切しない — 指示は
file に書いて orchestrator (LLM) に渡し、採否と実行はそちらの判断に残す。

置き場 (`<root>/<project>/`。root は env `DISPATCHER_ROOT`、既定 `~/.claude/dispatcher`):

    dispatcher-project.toml   宣言 config (issue 置き場 / CL 置き場 / 着手可 label / 並列上限 N)
    .config-verified          置き場 repo の実在検査に通った config の sha256 (内容が変わると再検査)
    tick.lock                 単一実行 lock (fcntl.flock)
    instructions/<ts>.json    指示ファイル (snapshot + 指示の列)。指示 0 件なら書かない
    decisions/<ts>.json       orchestrator が書く決定 file (起動する worker の列)。無ければ起動しない
    workers/<issue>-<ts>.log  detach 起動した worker の stdout / stderr
    log.jsonl                 毎 tick 1 行の実行記録 (正本ではない。cron の死活は最終行の時刻で見る)

`--dry-run` は config の検査 → 観測 → 指示の導出までを通して claude を起動する直前で止め、指示の件数を stdout に
1 行で出す。root 配下には何も書かない (lock / marker / log / 指示ファイルのいずれも)。`--cron-env` を添えると cron と
同じ最小環境変数 (HOME と `<uv の dir>:/usr/bin:/bin` の PATH) で自分を撃ち直す。

指示 0 件の tick は log 1 行だけを残して終わる。観測に失敗した tick は `result: error` として
log に残し、指示 0 件とは区別する (沈黙と観測不能を混同しない)。

指示があれば cwd (= 実装 repo の clone) で orchestrator (`claude -p "/swat-skills:dispatcher <指示ファイル> <決定 file>"`)
を起動して終了を待ち、決定 file の判断を log に写し、決定どおり worker を新しい process session として detach 起動する
(LLM が決めて機械が起動する — 理由は ADR 0073)。
"""

from __future__ import annotations

import argparse
import fcntl
import functools
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import tomllib
import traceback
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

CONFIG_FILENAME = "dispatcher-project.toml"
VERIFIED_MARKER = ".config-verified"
LOCK_FILENAME = "tick.lock"
LOG_FILENAME = "log.jsonl"
INSTRUCTIONS_DIRNAME = "instructions"
DECISIONS_DIRNAME = "decisions"
WORKERS_DIRNAME = "workers"

# claim 信号と人返しの label。着手可 label だけが config で綴りを変えられる
WIP_LABEL = "dispatcher:wip"
HUMAN_LABEL = "ready-for-human"

# 観測を実装済みの tracker。他は config 読み込みで loud に落とす (silent に空を観測しない)
SUPPORTED_TRACKERS = ("gh",)

# config の許す table / key。未知の綴りは名指しで落とす (「宣言していない」と同じ挙動にしない)
CONFIG_SCHEMA = {
    "issue": {"tracker", "repo", "ready_label"},
    "cl": {"repo"},
    "limits": {"max_wip"},
    "auth": {"token_file"},
}

# gh が認証に読む環境変数。どちらかがあれば token_file は読まない (env が勝つ)
GH_TOKEN_ENVS = ("GH_TOKEN", "GITHUB_TOKEN")
# cron が撃つときに残る環境変数と、PATH のうち uv の dir の後ろに付く部分 (cron の既定 PATH)
CRON_ENV_KEEP = ("HOME", "LOGNAME", "USER")
CRON_BASE_PATH = "/usr/bin:/bin"

# cron の最小 PATH で gh / claude が見つからないときに足す候補 (前に居るものから順に探す)
DEPENDENCIES = ("gh", "claude")
DEPENDENCY_PATH_CANDIDATES = (
    "~/.local/bin",
    "~/.local/share/mise/shims",
    "/opt/homebrew/bin",
    "/usr/local/bin",
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG_ERROR = 2
EXIT_LOCKED = 3
EXIT_AUTH_ERROR = 4

# exit code ↔ log.jsonl の `result`。cron.log の行にも同じ語を載せ、log.jsonl と同じ語で grep できるようにする
RESULT_BY_EXIT = {
    EXIT_OK: "ok", EXIT_ERROR: "error", EXIT_CONFIG_ERROR: "config_error", EXIT_LOCKED: "locked",
    EXIT_AUTH_ERROR: "auth_error",
}

# gh の認証が通らない徴候: 認証が要るときの exit code と、未認証 / token 失効のときに stderr へ出る文言
GH_AUTH_EXIT = 4
GH_AUTH_MARKERS = ("HTTP 401", "gh auth login")

GH_TIMEOUT_SEC = 120

# orchestrator は指示の採否と wip 付与だけの短いセッション。超えたら kill して log に残す
ORCHESTRATOR_TIMEOUT_SEC = 900
# orchestrator が決定 file に書く採否の語彙
# 指示の種別 → (採否を要求する issue の取り出し, その issue に許される採否)。見送りは skip として残す
# (候補を人へ返す・候補外の issue に着手する、は指示の外)
INSTRUCTION_KINDS = {
    "start": (lambda i: [c["number"] for c in i["candidates"]], ("start", "skip")),
    "reenter": (lambda i: [i["issue"]], ("reenter", "skip")),
    "anomaly": (lambda i: list(i["issues"]), ("skip", "ready-for-human")),
}
DECISION_ACTIONS = tuple(dict.fromkeys(a for _, actions in INSTRUCTION_KINDS.values() for a in actions))
# spawn の kind は着手形態。採否と同じ語彙 (start = 新規着手、reenter = 既存 CL の branch へ再入)
SPAWN_KINDS = ("start", "reenter")
PLAYBOOKS_DIR = Path(__file__).resolve().parents[3] / "procedure"


@dataclass(frozen=True)
class ReenterCondition:
    """reenter の条件 1 つ: snapshot の CL 状態に対する述語と、worker へ渡す対応 playbook。"""

    holds: Callable[[dict], bool]
    playbook: Path


# reenter 指示に併記する CL の条件カタログ (条件名・述語・対応 playbook の定義元はここだけ)。
# snapshot の CL 状態から機械的に確定するものだけを置き、並びが指示の `conditions` の順になる
REENTER_CONDITIONS = {
    "conflict": ReenterCondition(
        lambda cl: cl["mergeable"] == "CONFLICTING", PLAYBOOKS_DIR / "playbook-conflict-resolution" / "SKILL.md"
    ),
    "review": ReenterCondition(
        lambda cl: cl["unresolved_threads"] > 0, PLAYBOOKS_DIR / "playbook-review-response" / "SKILL.md"
    ),
    "ci": ReenterCondition(
        lambda cl: cl["checks"] in ("FAILURE", "ERROR"), PLAYBOOKS_DIR / "playbook-ci-fix" / "SKILL.md"
    ),
}
# start の選定母集合: 新しい CL を作る playbook は frontmatter metadata で印 (deliverable: cl) と
# 選定条件 (dispatch-when) を宣言する (設計 §10)。tick ごとに glob で集める — path を列挙すると、
# 印を付けた新しい playbook だけが黙って選定から外れる
START_PLAYBOOK_GLOB = "playbook-*/SKILL.md"
DELIVERABLE_KEY = "deliverable"
CL_DELIVERABLE = "cl"
DISPATCH_WHEN_KEY = "dispatch-when"
# worker が作る branch 名の規約 (設計 §10。SKILL.md の spawn prompt 契約と同じ綴り)
WORKER_BRANCH = "worktree-issue-{issue}"
LABEL_LIST_LIMIT = 500
ORCHESTRATOR_SKILL = "/swat-skills:dispatcher"
# -p では prompt へ落ちる経路が無く、classifier が止めた操作は実行されずセッションは続く
PERMISSION_MODE = "auto"

# 1 往復で読む上限。**上限に達したら観測不能として落とす** (黙って切り詰めると、窓の外の open CL を
# 持つ issue が候補へ戻り二重着手の start 指示になる)。超える project は上限を上げる変更で対応する
ISSUE_LIST_LIMIT = 500
PR_PAGE_SIZE = 100
CLOSING_REFS_PAGE_SIZE = 20
REVIEW_THREADS_PAGE_SIZE = 100

# open PR の紐づき (closing reference) と状態 (conflict / checks / 未解決 thread) を 1 往復で引く
PR_QUERY = f"""
query($owner: String!, $name: String!) {{
  repository(owner: $owner, name: $name) {{
    pullRequests(states: OPEN, first: {PR_PAGE_SIZE}, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
      pageInfo {{ hasNextPage }}
      nodes {{
        number
        url
        headRefName
        baseRefName
        isDraft
        mergeable
        closingIssuesReferences(first: {CLOSING_REFS_PAGE_SIZE}) {{
          totalCount
          nodes {{ number repository {{ nameWithOwner }} }}
        }}
        reviewThreads(first: {REVIEW_THREADS_PAGE_SIZE}) {{ totalCount nodes {{ isResolved }} }}
        commits(last: 1) {{ nodes {{ commit {{ statusCheckRollup {{ state }} }} }} }}
      }}
    }}
  }}
}}
"""


class ConfigError(Exception):
    """宣言 config が読めない / 綴りが誤っている / 置き場 repo が実在しない。観測を開始しない。"""


class GhError(Exception):
    """gh CLI が失敗した (観測不能)。指示 0 件とは区別して log に残す。"""


class GhAuthError(GhError):
    """gh の認証が通らない (未認証 / token の失効・権限不足)。config の綴りとは別の失敗として止める。"""


class TickStop(Exception):
    """観測を始める前後で tick を止める失敗。log の `result` と exit code を持つ。"""

    def __init__(self, exit_code: int, error: str):
        super().__init__(error)
        self.exit_code = exit_code
        self.error = error


class ObservationTruncated(Exception):
    """1 往復の上限に達し、観測が全量でない。切り詰めた像から指示を出さない。"""


class TickFailure(Exception):
    """指示ファイルを書いた後の失敗。`fragment` (orchestrator の記録 / 起動済み worker) を log 行へ残す。"""

    def __init__(self, message: str, fragment: dict | None = None):
        super().__init__(message)
        self.fragment = fragment or {}


class LaunchError(TickFailure):
    """claude を起動できなかった (PATH に無い / exec 失敗)。"""


class OrchestratorFailed(TickFailure):
    """orchestrator が正常終了しなかった (timeout / 異常終了)。決定 file は読まない。"""


class DecisionsError(TickFailure):
    """orchestrator の決定 file が無い / 読めない / 形が違う。1 件も起動しない (半分起動した残骸を作らない)。"""


@dataclass(frozen=True)
class OrchestratorRun:
    exit_code: int
    seconds: float
    timed_out: bool
    session_id: str


@dataclass(frozen=True)
class WorkerLaunch:
    pid: int
    session_id: str


@dataclass(frozen=True)
class Config:
    issue_tracker: str
    issue_repo: str
    ready_label: str
    cl_repo: str
    max_wip: int
    token_file: Path | None = None


@dataclass(frozen=True)
class TickResult:
    exit_code: int
    logged_ts: str | None  # log.jsonl を書けなかった tick は None
    error: str | None = None


# --- 依存と置き場の解決 ---


def resolve_root(env) -> Path:
    """config / log の root。env `DISPATCHER_ROOT` が勝ち、無ければ `$HOME/.claude/dispatcher`。"""
    override = env.get("DISPATCHER_ROOT")
    if override:
        return Path(override)
    return Path(env.get("HOME") or Path.home()) / ".claude" / "dispatcher"


def resolve_dependency_path(env, *, candidates=DEPENDENCY_PATH_CANDIDATES) -> dict:
    """gh / claude が PATH に無ければ候補ディレクトリを前置した env を返す (cron の最小環境変数対策)。"""
    resolved = dict(env)
    if all(shutil.which(name, path=resolved.get("PATH")) for name in DEPENDENCIES):
        return resolved
    extra = [os.path.expanduser(c) for c in candidates if os.path.isdir(os.path.expanduser(c))]
    resolved["PATH"] = ":".join(extra + [resolved.get("PATH", "")])
    return resolved


def make_gh(env):
    """gh CLI を撃つ callable。stdout を返し、失敗は GhError に包む。"""

    def gh(args):
        try:
            completed = subprocess.run(
                ["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT_SEC, env=env, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GhError(f"gh {' '.join(args[:2])}: {exc}") from exc
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            auth = completed.returncode == GH_AUTH_EXIT or any(marker in stderr for marker in GH_AUTH_MARKERS)
            raise (GhAuthError if auth else GhError)(
                f"gh {' '.join(args[:2])} failed (exit {completed.returncode}): {stderr}"
            )
        return completed.stdout

    return gh


class ClaudeLauncher:
    """claude CLI の起動口。orchestrator は待ち、worker は detach する。どちらも cwd (実装 repo の clone) で
    新しい process session として起動する (orchestrator は timeout 時に process group ごと止めるため、worker は
    tick の終了に巻き込まれないため)。

    claude の session id は起動ごとにここで発行して `--session-id` で渡し、呼び出し側へ返す (log から transcript
    へ辿る鍵。設計 §7)。"""

    def __init__(self, env, cwd: Path, *, orchestrator_timeout_sec: float = ORCHESTRATOR_TIMEOUT_SEC):
        self.env = env
        self.cwd = cwd
        self.orchestrator_timeout_sec = orchestrator_timeout_sec
        # 起動した worker の Popen を launcher の寿命まで持つ。worker の回収 (waitpid) は WorkerLaunch.pid の受け手に
        # 任せる (tick は回収せず pid を log に残すだけ。回収するのはテスト)。手放すと、GC 時の Popen.__del__ と後続の
        # Popen 生成が走らせる subprocess._cleanup() が終了済みの worker を先に回収し、受け手から終了状態が消える。
        self._workers: list[subprocess.Popen] = []

    def _popen(self, args, log_file: Path) -> subprocess.Popen:
        try:
            log = open(log_file, "w")
        except OSError as exc:
            raise LaunchError(f"起動 log を開けない ({log_file}): {exc}") from exc
        try:
            return subprocess.Popen(
                args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                cwd=self.cwd, env=self.env, start_new_session=True,
            )
        except OSError as exc:
            raise LaunchError(f"claude を起動できない ({' '.join(args[:2])}): {exc}") from exc
        finally:
            log.close()

    @staticmethod
    def _claude_args(prompt: str, session_id: str) -> list[str]:
        return ["claude", "-p", prompt, "--permission-mode", PERMISSION_MODE, "--session-id", session_id]

    def run_orchestrator(self, instruction_file: Path, decisions_file: Path) -> OrchestratorRun:
        prompt = f"{ORCHESTRATOR_SKILL} {instruction_file} {decisions_file}"
        session_id = str(uuid.uuid4())
        started = time.monotonic()
        process = self._popen(self._claude_args(prompt, session_id), orchestrator_log(decisions_file))
        try:
            exit_code = process.wait(timeout=self.orchestrator_timeout_sec)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return OrchestratorRun(exit_code=-1, seconds=time.monotonic() - started, timed_out=True, session_id=session_id)
        return OrchestratorRun(exit_code=exit_code, seconds=time.monotonic() - started, timed_out=False, session_id=session_id)

    def spawn_worker(self, prompt: str, log_file: Path) -> WorkerLaunch:
        session_id = str(uuid.uuid4())
        process = self._popen(self._claude_args(prompt, session_id), log_file)
        self._workers.append(process)
        return WorkerLaunch(pid=process.pid, session_id=session_id)


def orchestrator_log(decisions_file: Path) -> Path:
    """orchestrator の stdout / stderr の置き場 (決定 file の隣)。"""
    return decisions_file.with_suffix(".orchestrator.log")


# --- config ---


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"宣言 config が無い: {path}")
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: TOML として読めない: {exc}") from exc

    for table, keys in raw.items():
        if table not in CONFIG_SCHEMA:
            raise ConfigError(f"{path}: 未知の table [{table}] (許すのは {sorted(CONFIG_SCHEMA)})")
        if not isinstance(keys, dict):
            raise ConfigError(f"{path}: [{table}] は table でなければならない")
        unknown = set(keys) - CONFIG_SCHEMA[table]
        if unknown:
            raise ConfigError(f"{path}: [{table}] に未知の key {sorted(unknown)} (許すのは {sorted(CONFIG_SCHEMA[table])})")

    issue = raw.get("issue", {})
    for key in ("tracker", "repo", "ready_label"):
        if not issue.get(key):
            raise ConfigError(f"{path}: [issue].{key} は必須")
    cl = raw.get("cl", {})
    if "cl" in raw and not cl.get("repo"):
        raise ConfigError(f"{path}: [cl] を書くなら repo は必須 (空の [cl] は「同じ」ではなく誤り)")
    limits = raw.get("limits", {})
    max_wip = limits.get("max_wip")
    if not isinstance(max_wip, int) or isinstance(max_wip, bool) or max_wip < 1:
        raise ConfigError(f"{path}: [limits].max_wip は 1 以上の整数が必須")

    token_file = None
    if "auth" in raw:
        raw_token_file = raw["auth"].get("token_file")
        if not isinstance(raw_token_file, str) or not raw_token_file:
            raise ConfigError(f"{path}: [auth] を書くなら token_file は必須 (非空の path)")
        token_file = Path(raw_token_file).expanduser()
        if not token_file.is_absolute():
            # 相対 path は cwd (cron の cd 先) で指す先が変わる
            raise ConfigError(f"{path}: [auth].token_file は絶対 path か ~ 始まり: {raw_token_file}")

    config = Config(
        issue_tracker=issue["tracker"],
        issue_repo=issue["repo"],
        ready_label=issue["ready_label"],
        cl_repo=cl.get("repo", issue["repo"]),
        max_wip=max_wip,
        token_file=token_file,
    )
    if config.issue_tracker not in SUPPORTED_TRACKERS:
        raise ConfigError(f"{path}: tracker '{config.issue_tracker}' は未対応 (対応: {SUPPORTED_TRACKERS})")
    for repo in (config.issue_repo, config.cl_repo):
        # gh repo view は裸の name も HOST/OWNER/NAME も通すが、issue list と graphql 変数は owner/name
        # だけを受ける。検査に通った綴りが観測で落ちる (しかも hash が一致して再検査されない) のを塞ぐ
        parts = repo.split("/")
        if len(parts) != 2 or not all(parts):
            raise ConfigError(f"{path}: repo '{repo}' は owner/name の形でなければならない")
    return config


def with_token_file(config: Config, config_path: Path, env) -> dict:
    """gh / claude の子プロセスへ渡す env を返す。env に gh の token が無ければ `[auth].token_file` の中身を
    `GH_TOKEN` として載せる。

    env に token があれば file は見ない (利用者が渡した token を上書きしない)。token 自体は error 文にも出さない。
    """
    if config.token_file is None or any(env.get(name) for name in GH_TOKEN_ENVS):
        return dict(env)
    try:
        mode = config.token_file.stat().st_mode
        if mode & 0o077:
            raise ConfigError(
                f"{config_path}: [auth].token_file が group / other から読める mode ({oct(mode & 0o777)}): "
                f"{config.token_file}。`chmod 600` にする"
            )
        token = config.token_file.read_text().strip()
    except OSError as exc:
        raise ConfigError(f"{config_path}: [auth].token_file を読めない: {config.token_file} ({exc.strerror})") from exc
    if not token:
        raise ConfigError(f"{config_path}: [auth].token_file が空: {config.token_file}")
    return {**env, "GH_TOKEN": token}


def verify_config_once(config: Config, config_path: Path, gh, *, marker_path: Path) -> None:
    """verify_config を config の内容ごとに 1 度だけ通す。通った config の hash を marker に残し、同じ内容なら再検査しない。"""
    digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    if marker_path.exists() and marker_path.read_text().strip() == digest:
        return
    verify_config(config, config_path, gh)
    marker_path.write_text(digest + "\n")


def verify_config(config: Config, config_path: Path, gh) -> None:
    """置き場 repo の実在と、機構が付ける label の存在を loud に検査する (何も書かない)。

    gh の認証が通らない失敗 (GhAuthError) は config の綴りと取り違えないよう、ConfigError に包まずそのまま上げる。"""
    for repo in dict.fromkeys((config.issue_repo, config.cl_repo)):
        try:
            gh(["repo", "view", repo, "--json", "nameWithOwner"])
        except GhAuthError:
            raise
        except GhError as exc:
            raise ConfigError(f"{config_path}: 置き場 repo '{repo}' を確認できない — {exc}") from exc
    try:
        listed = json.loads(gh(["label", "list", "-R", config.issue_repo, "--json", "name", "--limit", str(LABEL_LIST_LIMIT)]))
        labels = {label["name"] for label in listed}
    except GhAuthError:
        raise
    except (GhError, ValueError, KeyError, TypeError) as exc:
        raise ConfigError(f"{config_path}: 置き場 '{config.issue_repo}' の label を読めない — {exc}") from exc
    if len(listed) >= LABEL_LIST_LIMIT:
        raise ConfigError(f"{config_path}: 置き場 '{config.issue_repo}' の label が {LABEL_LIST_LIMIT} 件以上あり検査できない")
    missing = [name for name in (WIP_LABEL, HUMAN_LABEL) if name not in labels]
    if missing:
        raise ConfigError(
            f"{config_path}: 置き場 '{config.issue_repo}' に label {missing} が無い (gh は無い label の付与を落とす)。"
            f" `gh label create <name> -R {config.issue_repo}` で作る"
        )


# --- 観測 ---


def observe(config: Config, gh, now: datetime) -> dict:
    """tracker と CL host を読み、1 tick の snapshot (正規化像) を組む。"""
    issues = fetch_open_issues(config, gh)
    cls = [_normalize_cl(pr, config.issue_repo) for pr in fetch_open_prs(config, gh)]
    snapshot = classify(issues, cls, config)
    snapshot.update(
        observed_at=now.isoformat(),
        issue_repo=config.issue_repo,
        cl_repo=config.cl_repo,
        limits={"max_wip": config.max_wip, "wip_count": len(snapshot["issues"]["wip"])},
        observed={"issues": len(issues), "cls": len(cls)},
    )
    return snapshot


def fetch_open_issues(config: Config, gh) -> list[dict]:
    issues = json.loads(
        gh([
            "issue", "list", "-R", config.issue_repo, "--state", "open",
            "--limit", str(ISSUE_LIST_LIMIT), "--json", "number,title,labels,url,body",
        ])
    )
    if len(issues) >= ISSUE_LIST_LIMIT:
        raise ObservationTruncated(f"open issue が {ISSUE_LIST_LIMIT} 件以上あり切り詰められた ({config.issue_repo})")
    return issues


def fetch_open_prs(config: Config, gh) -> list[dict]:
    owner, name = config.cl_repo.split("/", 1)
    # -f は生文字列。-F だと数字だけの owner / name が Int に型付けされ String! 変数に入らない
    payload = json.loads(
        gh(["api", "graphql", "-f", f"query={PR_QUERY}", "-f", f"owner={owner}", "-f", f"name={name}"])
    )
    page = payload["data"]["repository"]["pullRequests"]
    if page["pageInfo"]["hasNextPage"]:
        raise ObservationTruncated(f"open PR が {PR_PAGE_SIZE} 件を超え切り詰められた ({config.cl_repo})")
    for pr in page["nodes"]:
        for field, size in (("closingIssuesReferences", CLOSING_REFS_PAGE_SIZE), ("reviewThreads", REVIEW_THREADS_PAGE_SIZE)):
            if pr[field]["totalCount"] > size:
                raise ObservationTruncated(f"PR #{pr['number']} の {field} が {size} 件を超え切り詰められた")
    return page["nodes"]


def classify(issues: list[dict], cls: list[dict], config: Config) -> dict:
    """open issue を label と紐づく open CL で 3 bucket に分け、issue → open CL の紐づきを 1 度だけ組む。

    `linked_cls` は指示の導出が読む写像で、指示ファイルにもそのまま載る (CL 側の `issues` と同じ紐づきを
    issue 側から引ける形)。
    """
    open_numbers = {issue["number"] for issue in issues}
    linked_cls: dict[int, list[int]] = {}
    for cl in cls:
        for number in cl["issues"]:
            if number in open_numbers:
                linked_cls.setdefault(number, []).append(cl["number"])

    candidates, wip, ready_for_human = [], [], []
    for issue in issues:
        labels = {label["name"] for label in issue["labels"]}
        brief = {"number": issue["number"], "title": issue["title"], "url": issue["url"]}
        if WIP_LABEL in labels:
            wip.append(brief)
        if HUMAN_LABEL in labels:
            ready_for_human.append(issue["number"])
        if (
            config.ready_label in labels
            and WIP_LABEL not in labels
            and HUMAN_LABEL not in labels
            and not linked_cls.get(issue["number"])
        ):
            # 候補だけ本文を持つ (orchestrator の playbook 選定の信号。gh 呼び出しを候補数に比例させない)
            candidates.append({**brief, "body": issue["body"]})

    return {
        "issues": {"candidates": candidates, "wip": wip, "ready_for_human": ready_for_human},
        "linked_cls": dict(sorted(linked_cls.items())),
        "cls": cls,
    }


def _normalize_cl(pr: dict, issue_repo: str) -> dict:
    linked = [
        ref["number"]
        for ref in pr["closingIssuesReferences"]["nodes"]
        if ref["repository"]["nameWithOwner"] == issue_repo
    ]
    commits = pr["commits"]["nodes"]
    rollup = commits[0]["commit"]["statusCheckRollup"] if commits else None
    return {
        "number": pr["number"],
        "url": pr["url"],
        "branch": pr["headRefName"],
        "base": pr["baseRefName"],
        "draft": pr["isDraft"],
        "issues": linked,
        "mergeable": pr["mergeable"],
        "checks": rollup["state"] if rollup else None,
        "unresolved_threads": sum(1 for t in pr["reviewThreads"]["nodes"] if not t["isResolved"]),
    }


# --- 指示の導出 ---


def _frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8")
    head, sep, _ = text.removeprefix("---\n").partition("\n---")
    if not text.startswith("---\n") or not sep:
        raise ValueError(f"frontmatter が無い: {skill_md}")
    try:
        front = yaml.safe_load(head)
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter が読めない: {skill_md}: {exc}") from exc
    if not isinstance(front, dict):
        raise ValueError(f"frontmatter が mapping でない: {skill_md}")
    return front


def scan_start_playbooks(playbooks_dir: Path = PLAYBOOKS_DIR) -> list[dict]:
    """start の選定母集合 ([{path, dispatch_when}])。印を判定できない / 選定条件の無い playbook は落とす。"""
    playbooks = []
    for skill_md in sorted(playbooks_dir.glob(START_PLAYBOOK_GLOB)):
        metadata = _frontmatter(skill_md).get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError(f"metadata が mapping でない: {skill_md}")
        if metadata.get(DELIVERABLE_KEY) != CL_DELIVERABLE:
            continue
        dispatch_when = metadata.get(DISPATCH_WHEN_KEY)
        if not isinstance(dispatch_when, str) or not dispatch_when.strip():
            raise ValueError(f"metadata.{DELIVERABLE_KEY}: {CL_DELIVERABLE} なのに {DISPATCH_WHEN_KEY} が無い: {skill_md}")
        playbooks.append({"path": str(skill_md), "dispatch_when": dispatch_when})
    if not playbooks:
        raise ValueError(f"start の選定母集合が空 ({playbooks_dir / START_PLAYBOOK_GLOB})")
    return playbooks


def derive_instructions(snapshot: dict) -> list[dict]:
    """snapshot から機械的に確定する指示だけを出す。分類できない観測は anomaly として上げる。"""
    issues = snapshot["issues"]
    wip_numbers = [i["number"] for i in issues["wip"]]
    free_slots = snapshot["limits"]["max_wip"] - len(wip_numbers)
    # 指示を出さない issue (抑止条件は設計 §6)
    suppressed = set(wip_numbers) | set(issues["ready_for_human"])
    cl_by_number = {cl["number"]: cl for cl in snapshot["cls"]}

    reenters, anomalies = [], []
    for number, cl_numbers in snapshot["linked_cls"].items():
        if len(cl_numbers) > 1:
            anomalies.append({"kind": "anomaly", "reason": "multiple_open_cls", "issues": [number], "cls": sorted(cl_numbers)})
            continue
        cl = cl_by_number[cl_numbers[0]]
        if number in suppressed or cl["branch"] != WORKER_BRANCH.format(issue=number):
            continue
        conditions = [
            {"name": name, "playbook": str(condition.playbook)}
            for name, condition in REENTER_CONDITIONS.items()
            if condition.holds(cl)
        ]
        if conditions:
            # 再入 worker が要る分だけの CL の像 (番号 / URL / head / base)
            brief = {key: cl[key] for key in ("number", "url", "branch", "base")}
            reenters.append({"kind": "reenter", "issue": number, "cl": brief, "conditions": conditions})

    # slot は reenter → start の順に配る (設計 §6)
    instructions = reenters[: max(free_slots, 0)]
    free_slots -= len(instructions)

    if issues["candidates"] and free_slots > 0:
        instructions.append(
            {"kind": "start", "free_slots": free_slots, "candidates": issues["candidates"], "playbooks": scan_start_playbooks()}
        )

    if len(wip_numbers) > snapshot["limits"]["max_wip"]:
        instructions.append({"kind": "anomaly", "reason": "wip_over_limit", "issues": wip_numbers})

    both = sorted(set(wip_numbers) & set(issues["ready_for_human"]))
    if both:
        instructions.append({"kind": "anomaly", "reason": "wip_and_ready_for_human", "issues": both})

    return instructions + anomalies


# --- tick ---


@dataclass(frozen=True)
class Observation:
    """config の検査から指示の導出までを通した結果 (本番 tick と試運転が共有する)。"""

    env: dict  # gh / claude の子プロセスへ渡す env (token_file の token を載せた後)
    snapshot: dict
    instructions: list[dict]
    counts: dict[str, int]  # 指示の種別 → 件数


def observe_tick(config_path: Path, *, env, make_gh, verify, now: datetime) -> Observation:
    """config の検査 → token の解決 → 置き場の検査 (verify) → 観測 → 指示の導出。失敗は TickStop で止める。

    gh は token を載せた env で make_gh から作る (子プロセスへ渡る env を呼び出し側が組み直せるように)。
    verify は `(config, config_path, gh)` を受ける検査 — 本番は marker で 1 度だけ、試運転は毎回。"""
    try:
        config = load_config(config_path)
        run_env = with_token_file(config, config_path, env)
    except ConfigError as exc:
        raise TickStop(EXIT_CONFIG_ERROR, str(exc)) from exc
    gh = make_gh(run_env)
    try:
        verify(config, config_path, gh)
        snapshot = observe(config, gh, now)
    except ConfigError as exc:
        raise TickStop(EXIT_CONFIG_ERROR, str(exc)) from exc
    except GhAuthError as exc:
        raise TickStop(EXIT_AUTH_ERROR, f"gh の認証が通らない: {exc}") from exc
    except (GhError, ObservationTruncated, KeyError, ValueError) as exc:
        raise TickStop(EXIT_ERROR, f"観測できなかった: {exc}") from exc
    try:
        instructions = derive_instructions(snapshot)
    except (OSError, ValueError) as exc:
        raise TickStop(EXIT_ERROR, f"指示を導出できなかった: {exc}") from exc
    return Observation(run_env, snapshot, instructions, dict(Counter(i["kind"] for i in instructions)))


def run_tick(project: str, *, root: Path, env, make_gh, make_claude, now: datetime) -> TickResult:
    """1 tick を回す。make_gh / make_claude は子プロセスへ渡す env (token 解決後) を受けて起動口を作る。"""
    project_dir = root / project
    config_path = project_dir / CONFIG_FILENAME
    tick_ts = now.isoformat()
    record = {"ts": tick_ts, "project": project}

    if not config_path.exists():
        error = f"宣言 config が無い: {config_path}"
        if not project_dir.is_dir():
            # project dir ごと無いときは log を置く場所も無い (stderr だけ)
            return TickResult(EXIT_CONFIG_ERROR, None, error)
        _append_log(project_dir, {**record, "result": "config_error", "error": error})
        return TickResult(EXIT_CONFIG_ERROR, tick_ts, error)

    with open(project_dir / LOCK_FILENAME, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # 前 tick が長引いている間も log は進める (最終行の時刻で cron の死活を見るため)
            error = f"前の tick がまだ走っている ({project_dir / LOCK_FILENAME})"
            _append_log(project_dir, {**record, "result": "locked", "error": error})
            return TickResult(EXIT_LOCKED, tick_ts, error)

        verify = functools.partial(verify_config_once, marker_path=project_dir / VERIFIED_MARKER)
        try:
            observation = observe_tick(config_path, env=env, make_gh=make_gh, verify=verify, now=now)
        except TickStop as exc:
            _append_log(project_dir, {**record, "result": RESULT_BY_EXIT[exc.exit_code], "error": exc.error})
            return TickResult(exc.exit_code, tick_ts, exc.error)

        snapshot, instructions = observation.snapshot, observation.instructions
        record.update(
            observed=snapshot["observed"],
            candidates=len(snapshot["issues"]["candidates"]),
            wip=snapshot["limits"]["wip_count"],
            instructions=observation.counts,
            instruction_file=None,
        )
        if not instructions:
            _append_log(project_dir, {**record, "result": "ok"})
            return TickResult(EXIT_OK, tick_ts)

        instruction_file = _write_instructions(project_dir, now, snapshot, instructions)
        record.update(instruction_file=str(instruction_file))
        try:
            fragment = _drive_orchestrator(project_dir, instruction_file, instructions, make_claude(observation.env), now)
        except TickFailure as exc:
            _append_log(project_dir, {**record, **exc.fragment, "result": "error", "error": str(exc)})
            return TickResult(EXIT_ERROR, tick_ts, str(exc))

        _append_log(project_dir, {**record, **fragment, "result": "ok"})
        return TickResult(EXIT_OK, tick_ts)


def dry_run_tick(project: str, *, root: Path, env, make_gh, now: datetime) -> tuple[TickResult, dict | None]:
    """試運転: config の検査 → 観測 → 指示の導出までを通し、claude を起動する直前で止める。

    root 配下には何も書かない (lock / marker / log / 指示ファイル) ので、実 config と実 root のまま撃ってよい。
    返り値は (結果, stdout に出す 1 行の record — 失敗なら None)。
    """
    config_path = root / project / CONFIG_FILENAME
    if not config_path.exists():
        return TickResult(EXIT_CONFIG_ERROR, None, f"宣言 config が無い: {config_path}"), None
    # claude の起動経路を通らない分の代わりに、起動に要る依存が最終的な PATH で解決できるかを見る
    unresolved = [name for name in DEPENDENCIES if not shutil.which(name, path=env.get("PATH"))]
    if unresolved:
        return TickResult(EXIT_ERROR, None, f"{unresolved} が PATH に無い (PATH={env.get('PATH', '')})"), None
    try:
        observation = observe_tick(config_path, env=env, make_gh=make_gh, verify=verify_config, now=now)
    except TickStop as exc:
        return TickResult(exc.exit_code, None, exc.error), None
    snapshot = observation.snapshot
    record = {
        "ts": now.isoformat(), "project": project, "dry_run": True, "result": "ok",
        "observed": snapshot["observed"], "candidates": len(snapshot["issues"]["candidates"]),
        "wip": snapshot["limits"]["wip_count"], "instructions": observation.counts,
    }
    return TickResult(EXIT_OK, None), record


def cron_env(env) -> dict:
    """cron が撃つときと同じ最小の環境変数 (HOME 等と `<uv の dir>:/usr/bin:/bin` の PATH)。uv が無ければ ValueError。

    `DISPATCHER_ROOT` だけは継ぐ (撃ち直し前と同じ置き場を読むため。cron の行には書かない調整口)。
    """
    uv = shutil.which("uv", path=env.get("PATH"))
    if not uv:
        raise ValueError(f"uv が PATH に無い (PATH={env.get('PATH', '')})。cron の tick 行に前置する uv の dir を決められない")
    stripped = {name: env[name] for name in (*CRON_ENV_KEEP, "DISPATCHER_ROOT") if env.get(name)}
    stripped.update(SHELL="/bin/sh", PATH=f"{os.path.dirname(uv)}:{CRON_BASE_PATH}")
    return stripped


def _drive_orchestrator(project_dir: Path, instruction_file: Path, instructions: list[dict], claude, now: datetime) -> dict:
    """orchestrator を起動して待ち、決定 file の判断を log に写し、決定どおり worker を起動する。

    返り値は tick の log 行に載せる断片 (`orchestrator` / `spawned`)。失敗は TickFailure にその時点の断片を
    載せて投げる (起動済みの worker の pid / log を log 行から落とさない)。
    """
    decisions_file = project_dir / DECISIONS_DIRNAME / f"{_tick_stem(now)}.json"
    decisions_file.parent.mkdir(exist_ok=True)
    run = claude.run_orchestrator(instruction_file, decisions_file)
    fragment = {
        "orchestrator": {
            "exit_code": run.exit_code, "seconds": round(run.seconds, 1), "timed_out": run.timed_out,
            "session_id": run.session_id,
        },
        "spawned": [],
    }
    try:
        if run.timed_out or run.exit_code != 0:
            # 途中で死んだ orchestrator の決定 file は信用しない (wip を付けた後に書き切れていない可能性)。
            # 付いた wip は機械では剥がさない — stale wip として triage が回収する
            raise OrchestratorFailed(
                f"orchestrator が正常終了しなかった (exit {run.exit_code}, timed_out={run.timed_out})。決定 file は読まない"
            )
        decisions = _read_decisions(decisions_file, instructions)
        _log_orchestrator_decisions(project_dir, instruction_file, decisions, now)
        # 網羅の欠けは error にするが、書かれた分の起動は先に行う — orchestrator が wip を付けた issue を
        # 起動せずに残すと、次 tick では普通の wip に見えて誰も拾わない
        coverage_gap = _decision_coverage_gap(decisions, instructions)
        _spawn_workers(project_dir, decisions["spawn"], claude, now, fragment["spawned"])
        if coverage_gap:
            raise DecisionsError(coverage_gap)
    except TickFailure as exc:
        exc.fragment = fragment
        raise
    return fragment


def _log_orchestrator_decisions(project_dir: Path, instruction_file: Path, decisions: dict, now: datetime) -> None:
    _append_log(
        project_dir,
        {
            "ts": now.isoformat(), "project": project_dir.name, "actor": "orchestrator",
            "instruction_file": str(instruction_file), "decisions": decisions["decisions"],
        },
    )


def _spawn_workers(project_dir: Path, spawn: list[dict], claude, now: datetime, spawned: list[dict]) -> None:
    """決定どおり worker を起動し、起動記録を `spawned` に積む (途中で失敗しても起動済みの記録は残る)。"""
    workers_dir = project_dir / WORKERS_DIRNAME
    workers_dir.mkdir(exist_ok=True)
    for entry in spawn:
        log_file = workers_dir / f"{entry['issue']}-{_tick_stem(now)}.log"
        launch = claude.spawn_worker(entry["prompt"], log_file)
        spawned.append(
            {"issue": entry["issue"], "kind": entry["kind"], "pid": launch.pid, "log": str(log_file), "session_id": launch.session_id}
        )


def _read_decisions(decisions_file: Path, instructions: list[dict]) -> dict:
    """決定 file を読んで形を検査する。orchestrator は spawn 0 件でも file を書く (無いのは書けなかった徴候)。"""
    if not decisions_file.exists():
        raise DecisionsError(f"orchestrator が決定 file を書かなかった ({decisions_file})")
    # reenter の worker へ渡してよい playbook は、指示に載せた条件の playbook を条件の順に並べたもの
    reenter_playbooks = {
        i["issue"]: [c["playbook"] for c in i["conditions"]] for i in instructions if i["kind"] == "reenter"
    }
    # start の worker へ渡してよい playbook は、指示に載せた選定母集合の 1 本
    start_playbooks = {p["path"] for i in instructions if i["kind"] == "start" for p in i["playbooks"]}
    try:
        decisions = json.loads(decisions_file.read_text())
        seen = set()
        for entry in decisions["decisions"]:
            if not isinstance(entry["issue"], int) or not isinstance(entry["reason"], str):
                raise ValueError(f"decisions の要素は issue: int / action / reason: str: {entry}")
            if entry["action"] not in DECISION_ACTIONS:
                raise ValueError(f"decisions の action は {DECISION_ACTIONS} のいずれか: {entry}")
            if entry["issue"] in seen:
                raise ValueError(f"decisions に issue {entry['issue']} が 2 回ある")
            seen.add(entry["issue"])
        action_of = {entry["issue"]: entry["action"] for entry in decisions["decisions"]}
        for entry in decisions["spawn"]:
            if not isinstance(entry["issue"], int) or entry["kind"] not in SPAWN_KINDS:
                raise ValueError(f"spawn の要素は issue: int / kind: {SPAWN_KINDS} / prompt: str: {entry}")
            if not isinstance(entry["prompt"], str) or not entry["prompt"]:
                raise ValueError(f"spawn の prompt は非空文字列: {entry}")
            if "${" in entry["prompt"]:
                # 未展開の変数 (${CLAUDE_SKILL_DIR} 等) は worker から解決できず、playbook も索引も届かないまま走る
                raise ValueError(f"spawn の prompt に未展開の変数が残っている (issue {entry['issue']})")
            playbooks = entry["playbooks"]
            if not isinstance(playbooks, list) or not playbooks or not all(isinstance(p, str) and p for p in playbooks):
                raise ValueError(f"spawn の playbooks は非空の path 列: {entry}")
            for playbook in playbooks:
                # worker が読むのは prompt の path だけ。列挙と prompt の食い違い・消えた playbook は起動前に落とす
                if playbook not in entry["prompt"]:
                    raise ValueError(f"spawn の playbook が prompt に載っていない (issue {entry['issue']}): {playbook}")
                if not Path(playbook).is_file():
                    raise ValueError(f"spawn の playbook が実在しない (issue {entry['issue']}): {playbook}")
            if entry["kind"] == "reenter":
                # 読み直しで外れた条件は落としてよいが、指示に無い playbook・順序の入れ替えは写し間違い。
                # iterator への `in` は一致した位置まで消費するので、順序ごと部分列かを判定する (list にすると順序の検査が消える)
                allowed = iter(reenter_playbooks.get(entry["issue"], []))
                if not all(playbook in allowed for playbook in playbooks):
                    raise ValueError(
                        f"reenter の spawn の playbooks が指示の条件の playbook を条件の順に並べたものでない"
                        f" (issue {entry['issue']}): {playbooks}"
                    )
            if entry["kind"] == "start" and (len(playbooks) != 1 or playbooks[0] not in start_playbooks):
                raise ValueError(
                    f"start の spawn の playbooks が指示の選定母集合の 1 本でない (issue {entry['issue']}): {playbooks}"
                )
            if action_of.get(entry["issue"]) != entry["kind"]:
                raise ValueError(f"spawn の issue {entry['issue']} (kind: {entry['kind']}) に同じ action の decision が無い")
    except (ValueError, KeyError, TypeError) as exc:
        raise DecisionsError(f"決定 file が読めない ({decisions_file}): {exc}") from exc
    return decisions


def _decision_coverage_gap(decisions: dict, instructions: list[dict]) -> str | None:
    """指示ごとに採否が書かれているかを見て、欠けを 1 文で返す (無ければ None)。

    指示が採否を要求する issue の 1 件ごとに、その種別に許される採否 (INSTRUCTION_KINDS) が要る。判断不能を orchestrator が黙って落とせないようにする検査。
    """
    action_of = {entry["issue"]: entry["action"] for entry in decisions["decisions"]}
    gaps = []
    for instruction in instructions:
        issues_of, allowed = INSTRUCTION_KINDS[instruction["kind"]]
        undecided = [n for n in issues_of(instruction) if action_of.get(n) not in allowed]
        if undecided:
            label = instruction["kind"] + (f" ({instruction['reason']})" if "reason" in instruction else "")
            gaps.append(f"{label} の issue {undecided} に採否 ({' / '.join(allowed)}) が無い")
    return " / ".join(gaps) or None


def _tick_stem(now: datetime) -> str:
    """tick を識別する file 名の幹。マイクロ秒まで入れる (同じ秒に 2 tick 走ると前の file を上書きしてしまう)。"""
    return now.strftime("%Y%m%dT%H%M%S.%fZ")


def _write_instructions(project_dir: Path, now: datetime, snapshot: dict, instructions: list[dict]) -> Path:
    directory = project_dir / INSTRUCTIONS_DIRNAME
    directory.mkdir(exist_ok=True)
    path = directory / f"{_tick_stem(now)}.json"
    path.write_text(json.dumps({"snapshot": snapshot, "instructions": instructions}, ensure_ascii=False, indent=2))
    return path


def _append_log(project_dir: Path, record: dict) -> None:
    with open(project_dir / LOG_FILENAME, "a") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")


def cron_log_line(project: str, result: TickResult, *, at: datetime) -> str:
    """失敗 tick が stderr (= crontab 行が append する cron.log) へ出す 1 行。

    `<at> [<project>] tick=<log.jsonl の ts か -> result=<log.jsonl の result> <error>`。cron.log を tail だけで
    「いつ・どの tick が・何で・なぜ」落ちたか読めるように時刻・tick・種別を前置し、複数行の error (gh の stderr 等) は
    ` / ` で 1 行に畳む。
    """
    error = " / ".join(line.strip() for line in result.error.splitlines() if line.strip())
    return (
        f"{at.isoformat(timespec='seconds')} [{project}] tick={result.logged_ts or '-'} "
        f"result={RESULT_BY_EXIT[result.exit_code]} {error}"
    )


def _log_crash(project_dir: Path, tick_ts: str, error: str) -> str | None:
    """想定外の例外で止まった tick も log.jsonl に 1 行残す (cron.log の行が指す先を失敗の種類で変えない)。

    残せた行の `ts` を返す。project dir が無い / 書けないときは None (cron.log の行だけが残る)。
    """
    if not project_dir.is_dir():
        return None
    try:
        _append_log(project_dir, {"ts": tick_ts, "project": project_dir.name, "result": "error", "error": error})
    except OSError:
        return None
    return tick_ts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="dispatcher の 1 tick (観測 → 指示導出 → log → 指示があれば orchestrator 起動 → 決定どおり worker 起動)。"
        "cwd を実装 repo の clone にして撃つ (orchestrator / worker はその cwd で走る)"
    )
    parser.add_argument("project", help="<root>/<project>/dispatcher-project.toml を読む project 名")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="観測と指示の導出までを通し、claude を起動せずに指示の件数を stdout に出す。root 配下に何も書かない",
    )
    parser.add_argument(
        "--cron-env", action="store_true",
        help="cron と同じ最小の環境変数 (HOME と <uv の dir>:/usr/bin:/bin の PATH) で自分を撃ち直す。--dry-run と併用する",
    )
    args = parser.parse_args(argv)
    if args.cron_env and not args.dry_run:
        parser.error("--cron-env は --dry-run と併用する (cron 相当の環境で撃つのは試運転だけ)")

    if args.cron_env:
        # 呼び出し側が env -i を前置すると sandbox の除外指定 (先頭 token で照合) から外れるので、撃ち直しは script が持つ。
        # shebang から起動し直すので、uv の解決も cron と同じ条件で通る
        try:
            child_env = cron_env(os.environ)
        except ValueError as exc:
            result = TickResult(EXIT_ERROR, None, str(exc))
            print(cron_log_line(args.project, result, at=datetime.now(timezone.utc)), file=sys.stderr)
            return result.exit_code
        return subprocess.run([str(Path(__file__).resolve()), "--dry-run", args.project], env=child_env, check=False).returncode

    env = resolve_dependency_path(os.environ)
    root = resolve_root(env)
    now = datetime.now(timezone.utc)
    try:
        if args.dry_run:
            result, record = dry_run_tick(args.project, root=root, env=env, make_gh=make_gh, now=now)
            if record is not None:
                print(json.dumps(record, ensure_ascii=False))
        else:
            result = run_tick(
                args.project, root=root, env=env, make_gh=make_gh,
                make_claude=lambda run_env: ClaudeLauncher(run_env, Path.cwd()), now=now,
            )
    except Exception as exc:
        # traceback を先に出し、前置付きの 1 行を最後に置く (cron.log は tail で末尾から読まれる)
        traceback.print_exc()
        error = f"想定外の例外で止まった: {type(exc).__name__}: {exc}"
        # dry-run は root 配下に何も書かない (想定外の例外でも log.jsonl に行を残さない)
        logged_ts = None if args.dry_run else _log_crash(root / args.project, now.isoformat(), error)
        result = TickResult(EXIT_ERROR, logged_ts, error)
    if result.error:
        print(cron_log_line(args.project, result, at=datetime.now(timezone.utc)), file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
