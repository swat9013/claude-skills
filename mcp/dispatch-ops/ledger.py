"""dispatch 台帳 (state.json + events.jsonl) の読み書き。

spec §3: 外部 store (tracker / pane / git) が「現実」、台帳は「意図と記録」。本 module は
記録の永続化と phase 遷移の合法性検証だけを持ち、「何をすべきか」の判断は一切持たない。

同時書き込み耐性 (spec §3.1):

- stdio MCP server はセッションごとに別プロセスなので、dispatcher と複数 pane が
  同じ state.json を同時に触りうる。プロセスメモリに状態を持たず、毎回 read-modify-write
  を advisory lock (`fcntl.flock`) の内側で完結させる
- state.json は temp + `os.replace` の atomic write。途中で落ちても半端な JSON を残さない
- events.jsonl は lock 下の append-only。state.json の上書きで消える履歴をここが保全する
"""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import refs
import repo_key as repo_key_mod
import vocabulary

STATE_VERSION = 1
STATE_FILENAME = "state.json"
EVENTS_FILENAME = "events.jsonl"
LOCK_FILENAME = ".lock"

# 台帳 root。環境変数で差し替えられるのはテストと検証のため — 実運用では既定を使う
# ディレクトリ名 / 環境変数名は server 改名 (issue-dispatch → dispatch-ops) 後も旧名のまま — live 台帳の移行回避 (ADR 0028)
LEDGER_ROOT_ENV = "ISSUE_DISPATCH_LEDGER_ROOT"
DEFAULT_LEDGER_ROOT = Path.home() / ".claude" / "issue-dispatch"

# 台帳ディレクトリそのもの (`<root>/<key>`) の完全パス。**ROOT とは別物** — ROOT は
# 「どの root の下で cwd から key を導出するか」、DIR は「この プロセスが書くべき台帳は
# これ 1 つ」を指す。cwd と別の clone で走る worker が project の台帳へ着地するための
# 機械経路で、`pane_spawn` が起動プロセスの環境へ注入する (ADR 0036)
LEDGER_DIR_ENV = "ISSUE_DISPATCH_LEDGER_DIR"

# 記帳の主体。events.jsonl の `actor` 欄に載り、その行を誰が書いたかの帰属を決める。
#
# - `dispatcher`: orchestrator の記帳 (判断を伴う遷移)。綴りは server 改名前の旧名のまま
#   据え置く — 既存 events.jsonl の全行がこの綴りなので、改名すると過去の行だけが別の
#   書き手に見える (ADR 0028 と同じ live 台帳の移行回避)
# - `observer`: observer の記帳 (観測から一意に決まる機械的遷移だけ)。orchestrator と
#   同じ tool を同じ既定値で呼ぶので、**名乗らなければ `dispatcher` として残り帰属が
#   決まらない**。名乗る責務は呼び出し側 (observer skill 本文) が持つ
# - `pane`: worker の完了自己申告 (`report_outcome` の既定)
#
# **語彙は閉じない — 未知の actor もそのまま通す (fail-open)。** actor は機械が分岐に使わ
# ない記録欄 (outcome と同じ扱い) で、検証を足しても防げるのは綴り間違いだけな一方、書き手
# を 1 人増やすたびに server の変更が要るようになる。
DEFAULT_ACTOR = "dispatcher"
OBSERVER_ACTOR = "observer"
OUTCOME_ACTOR = "pane"


class LedgerError(RuntimeError):
    """台帳の読み書きに失敗した / 記帳の前提が成立しない。"""


def now_iso():
    """UTC の ISO8601 (秒精度 + `Z`)。テストは本関数を差し替える。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_ledger_root():
    """台帳 root を返す (環境変数 > 既定)。"""
    override = os.environ.get(LEDGER_ROOT_ENV)
    return Path(override).expanduser() if override else DEFAULT_LEDGER_ROOT


def resolve_ledger_dir(cwd=None, root=None, run=repo_key_mod.run_git):
    """開くべき台帳ディレクトリを **path として**解く (作らない・実在検査もしない)。

    優先順は **明示 root 引数 > `ISSUE_DISPATCH_LEDGER_DIR` > `ISSUE_DISPATCH_LEDGER_ROOT`
    > 既定 root**。DIR が立っているときだけ cwd からの導出を行わない — 導出すると、
    別 clone の worktree で走る worker が自分の repo-key で新しい台帳を作り、project の
    台帳から記録が迷子になる (ADR 0036 の壊れ点 1)。

    DIR に明示 root 引数を勝たせるのは、開発者の shell に DIR が残っているだけでテストの
    書き込み先が変わる (非 hermetic になる) のを避けるため。実運用の呼び出しは引数なし
    なので、機械経路としての強さは変わらない。

    **台帳を開く `open_ledger` と、置き場の宣言を読む `project` がこの 1 関数を共有する**
    (ADR 0036 の追補 / #589)。宣言 config は台帳と同じディレクトリに置くので、優先順が
    1 文字でもずれると宣言と台帳が別 project を指す — 揃えるのではなく、同じ関数にする。

    返り値: ``{"path": Path, "repo_key": str | None, "source": "dir_env" | "derived"}``
    (`repo_key` は DIR 経由では判らない — 注入された path をそのまま使うため)。
    """
    if root is None:
        directory = os.environ.get(LEDGER_DIR_ENV)
        if directory:
            return {"path": Path(directory).expanduser(), "repo_key": None, "source": "dir_env"}
    key = repo_key_mod.derive_repo_key(cwd, run=run)
    base = Path(root).expanduser() if root is not None else default_ledger_root()
    return {"path": base / key, "repo_key": key, "source": "derived"}


def open_ledger(cwd=None, root=None, run=repo_key_mod.run_git):
    """開くべき台帳を決めて Ledger を返す (ディレクトリは必要なら作る)。

    どのディレクトリを開くかは `resolve_ledger_dir` が決める (優先順はそちらの docstring)。

    **DIR が実在しないディレクトリを指していたら失敗させる**。注入する側 (`pane_spawn`) は
    既存の台帳を指すので、実在しない DIR は古い env か手で置いた値。そこに新しい台帳を作ると
    `ledger_list` が空を返し、「進行中の dispatch は無い」と読める — DIR が防ぐはずの迷子が
    別の形で再発する。
    """
    resolved = resolve_ledger_dir(cwd, root, run=run)
    path = resolved["path"]
    if resolved["source"] == "dir_env" and not path.is_dir():
        raise LedgerError(
            f"{LEDGER_DIR_ENV} が指す台帳ディレクトリが無い: {path} "
            "(pane_spawn の注入なら既存の台帳を指す。手で設定した env なら外す)"
        )
    return Ledger(path, repo_key=resolved["repo_key"])


class Ledger:
    """1 project 分の台帳ディレクトリ (`<root>/<key>/`)。

    key の導出式はメイン repo の repo-key のまま (ADR 0036 — 意味だけが「この repo の
    台帳」から「この project の台帳」へ移る)。project の実装 repo が複数あっても台帳は
    ここ 1 つで、どの clone で実装したかは entry 側 (`repo` / `agent.worktree`) が持つ。
    """

    def __init__(self, directory, repo_key=None):
        self.directory = Path(directory)
        self.repo_key = repo_key or self.directory.name
        self.state_path = self.directory / STATE_FILENAME
        self.events_path = self.directory / EVENTS_FILENAME
        self.lock_path = self.directory / LOCK_FILENAME

    # --- public API ---------------------------------------------------------

    def record(
        self,
        issue_ref,
        *,
        title=None,
        repo=None,
        agent=None,
        prs=None,
        note=None,
        actor=DEFAULT_ACTOR,
    ):
        """新規記帳 (phase は claimed で開始)。

        既存 entry があるときは、それが終端 phase の場合に限り新しい entry で置き換える
        (spec §3.3「再試行は新規 entry」)。稼働中の entry を黙って踏み潰さない。
        """
        parsed = refs.parse_issue_ref(issue_ref)
        with self._locked() as state:
            dispatches = state["dispatches"]
            previous = dispatches.get(parsed["ref"])
            replaced_phase = None
            if previous is not None:
                replaced_phase = previous["phase"]
                if replaced_phase not in vocabulary.TERMINAL_PHASES:
                    raise LedgerError(
                        f"{parsed['ref']} は既に phase={replaced_phase} で記帳済み。"
                        f"終端 phase ({vocabulary.DOC_FRAGMENTS['terminal_phases']}) へ"
                        "遷移させてから記帳する"
                    )
            timestamp = now_iso()
            entry = {
                "issue": {
                    "tracker": parsed["tracker"],
                    "repo": repo,
                    "number": parsed["number"],
                    "key": parsed["key"],
                    "title": title,
                },
                "agent": _normalize_agent(agent),
                "prs": _normalize_prs(prs),
                "phase": vocabulary.INITIAL_PHASE,
                "outcome": None,
                "note": note,
                "updated_at": timestamp,
            }
            dispatches[parsed["ref"]] = entry
            self._write_state(state)
            self._append_event(
                {
                    "ts": timestamp,
                    "issue": parsed["ref"],
                    "event": "record",
                    "phase": vocabulary.INITIAL_PHASE,
                    "replaced_phase": replaced_phase,
                    "actor": actor,
                    "note": note,
                }
            )
            return self._view(parsed["ref"], entry)

    def transition(
        self,
        issue_ref,
        phase,
        *,
        note=None,
        agent=None,
        prs=None,
        actor=DEFAULT_ACTOR,
    ):
        """合法性検証つきの phase 遷移。

        `agent` / `prs` を同時に更新できるのは、遷移と観測の更新が同じ瞬間に起きるため
        (active → parked は「pane を降ろした」= `agent.pane_id` が null になる遷移で、
        両者を別呼び出しに割ると台帳が一時的に嘘をつく)。`agent` は渡した key だけを
        上書きする merge、`prs` はリスト全体の置換。
        """
        parsed = refs.parse_issue_ref(issue_ref)
        with self._locked() as state:
            entry = state["dispatches"].get(parsed["ref"])
            if entry is None:
                raise LedgerError(f"{parsed['ref']} は台帳に無い (先に ledger_record する)")
            current = entry["phase"]
            vocabulary.validate_transition(current, phase)
            timestamp = now_iso()
            entry["phase"] = phase
            if agent is not None:
                entry["agent"] = _merge_agent(entry["agent"], agent)
            if prs is not None:
                entry["prs"] = _normalize_prs(prs)
            if note is not None:
                entry["note"] = note
            entry["updated_at"] = timestamp
            self._write_state(state)
            self._append_event(
                {
                    "ts": timestamp,
                    "issue": parsed["ref"],
                    "event": "transition",
                    "from": current,
                    "to": phase,
                    "actor": actor,
                    "note": note,
                }
            )
            return self._view(parsed["ref"], entry)

    def annotate(self, issue_ref, note, *, actor=DEFAULT_ACTOR):
        """phase を変えずに note だけ差し替える (引き継ぎの更新)。

        note は次セッションの自分へ判断の文脈を引き継ぐ欄だが、**引き継ぎたい状況ほど
        phase は動かない** (回答待ち / 裏取り中)。`transition` は同一 phase を非合法として
        撥ねるので、この method が無いと phase が動くまで note を書き換えられない。

        遷移表を触らず別 method を立てるのは、合法性検証を `transition` に閉じたまま
        にするため — 同一 phase を許すと「遷移」の名前と責務が食い違い、終端 phase の
        自己遷移という新しい合法遷移まで抱え込む。

        note は置換 (追記ではない。`transition` の note と同じ意味)。空文字は撥ねる —
        中身の無い annotate は「誰かが何かを書いた」だけの行を events.jsonl に残し、
        追跡の役に立たないまま履歴を濁す。終端 phase の entry にも書ける (事後の記録)。
        """
        parsed = refs.parse_issue_ref(issue_ref)
        if not isinstance(note, str) or not note.strip():
            raise LedgerError("note が空 (phase を変えずに残したい文脈を文字列で渡す)")
        with self._locked() as state:
            entry = state["dispatches"].get(parsed["ref"])
            if entry is None:
                raise LedgerError(f"{parsed['ref']} は台帳に無い (先に ledger_record する)")
            timestamp = now_iso()
            entry["note"] = note
            entry["updated_at"] = timestamp
            self._write_state(state)
            self._append_event(
                {
                    "ts": timestamp,
                    "issue": parsed["ref"],
                    "event": "annotate",
                    "phase": entry["phase"],
                    "actor": actor,
                    "note": note,
                }
            )
            return self._view(parsed["ref"], entry)

    def report_outcome(self, issue_ref, outcome, *, summary=None, actor=OUTCOME_ACTOR):
        """pane 側 agent の完了自己申告を記録する (phase は変えない)。

        outcome の語彙を検証しないのは spec §3.2 の設計どおり — `done` / `blocked` /
        `needs_review` 等の緩い語彙を pane が自分の言葉で置く欄で、機械は分岐に使わない。
        """
        parsed = refs.parse_issue_ref(issue_ref)
        if not isinstance(outcome, str) or not outcome.strip():
            raise LedgerError("outcome が空 (pane が何と報告したかを文字列で渡す)")
        with self._locked() as state:
            entry = state["dispatches"].get(parsed["ref"])
            if entry is None:
                raise LedgerError(f"{parsed['ref']} は台帳に無い (先に ledger_record する)")
            timestamp = now_iso()
            entry["outcome"] = {
                "status": outcome.strip(),
                "summary": summary,
                "reported_at": timestamp,
            }
            entry["updated_at"] = timestamp
            self._write_state(state)
            self._append_event(
                {
                    "ts": timestamp,
                    "issue": parsed["ref"],
                    "event": "outcome",
                    "outcome": outcome.strip(),
                    "summary": summary,
                    "actor": actor,
                }
            )
            return self._view(parsed["ref"], entry)

    def log_event(self, issue_ref, event, *, fields=None, actor=DEFAULT_ACTOR):
        """state を変えずに events.jsonl へ 1 行足す (履歴だけの記帳)。

        phase も outcome も動かさない操作 (spec §4.4 の `pane_send`「events.jsonl に
        記録」) の記録先。**台帳に entry が無くても書く** — 記録するのは「起きたこと」で
        あって「現況」ではなく、まだ記帳していない pane への送信も履歴としては本物。

        `event` の綴りを検証しないのは、値を置くのが server のコード (tool 実装) であって
        LLM の入力ではないため。issue_ref だけは書式を検証する — 壊れた ref で書くと
        履歴が現況と対応付かなくなる。
        """
        parsed = refs.parse_issue_ref(issue_ref)
        if not isinstance(event, str) or not event.strip():
            raise LedgerError("event が空 (何が起きたかの名前を文字列で渡す)")
        record = {
            "ts": now_iso(),
            "issue": parsed["ref"],
            "event": event.strip(),
            "actor": actor,
            **(fields or {}),
        }
        with self._locked():
            self._append_event(record)
        return record

    def list_entries(self, phases=None):
        """記帳済み entry の一覧 (phase で絞り込み可能)。"""
        if phases is not None:
            phases = [vocabulary.require_phase(phase) for phase in phases]
        with self._locked() as state:
            entries = [
                self._view(ref, entry)
                for ref, entry in state["dispatches"].items()
                if phases is None or entry["phase"] in phases
            ]
        entries.sort(key=lambda item: (item["updated_at"] or "", item["issue_ref"]))
        return entries

    def list_view(self, phases=None):
        """tool 応答用の一覧。どの台帳を見ているかと phase 語彙を毎回添える。

        `_view` が entry 1 件に repo_key / ledger_dir を添えるのと同じ理由 (診断) で、
        一覧にも台帳の所在を載せる。phase 語彙を返すのは、呼び出し側が絞り込みに使える
        値を応答から読めるようにするため。
        """
        entries = self.list_entries(phases)
        return {
            "repo_key": self.repo_key,
            "ledger_dir": str(self.directory),
            "phases": list(vocabulary.PHASES),
            "count": len(entries),
            "entries": entries,
        }

    def get(self, issue_ref):
        """1 件の entry。無ければ LedgerError。"""
        parsed = refs.parse_issue_ref(issue_ref)
        with self._locked() as state:
            entry = state["dispatches"].get(parsed["ref"])
            if entry is None:
                raise LedgerError(f"{parsed['ref']} は台帳に無い")
            return self._view(parsed["ref"], entry)

    # --- internals ----------------------------------------------------------

    def _view(self, issue_ref, entry):
        """tool 応答用の entry。どの台帳を見ているかを毎回添える。

        repo_key / ledger_dir を返すのは診断のため — server プロセスの cwd が期待と
        ずれると別 repo の台帳を書いてしまい、症状 (「記帳したのに出てこない」) から
        原因が見えない。
        """
        return {
            "issue_ref": issue_ref,
            "repo_key": self.repo_key,
            "ledger_dir": str(self.directory),
            **json.loads(json.dumps(entry)),  # 呼び出し側の変更が state に漏れない複製
        }

    def ensure_directory(self):
        """台帳ディレクトリを実体化して絶対パスを返す (記帳前に anchor を配るときに使う)。

        `open_ledger` は DIR が実在しなければ失敗する。まだ 1 件も記帳していない台帳を anchor
        として配るとその失敗を踏むので、配る側が先に作る。
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        return str(self.directory)

    @contextmanager
    def _locked(self):
        """advisory lock を握って state を読み、context を抜けるまで排他を保つ。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield self._read_state()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_state(self):
        if not self.state_path.exists():
            return {"version": STATE_VERSION, "dispatches": {}}
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LedgerError(f"{self.state_path} を読めない: {exc}") from exc
        version = state.get("version")
        if version != STATE_VERSION:
            raise LedgerError(
                f"{self.state_path} の version が {version!r} (本 server は {STATE_VERSION} のみ扱う)"
            )
        if not isinstance(state.get("dispatches"), dict):
            raise LedgerError(f"{self.state_path} の dispatches が dict でない")
        return state

    def _write_state(self, state):
        """temp + rename の atomic write。同一ディレクトリに置いて rename を保証する。"""
        payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(self.directory),
            prefix=f".{STATE_FILENAME}.",
            suffix=".tmp",
            delete=False,
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.state_path)
        except OSError as exc:
            temp_path.unlink(missing_ok=True)
            raise LedgerError(f"{self.state_path} を書けない: {exc}") from exc

    def _append_event(self, event):
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        try:
            with open(self.events_path, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise LedgerError(f"{self.events_path} に追記できない: {exc}") from exc


def _normalize_agent(agent):
    """agent block を既知 key だけの dict に正規化する (未指定は None 埋め)。"""
    agent = agent or {}
    if not isinstance(agent, dict):
        raise LedgerError("agent は object で渡す")
    unknown = set(agent) - set(vocabulary.AGENT_FIELDS)
    if unknown:
        raise LedgerError(
            f"agent に未知の key: {sorted(unknown)} "
            f"(既知: {', '.join(vocabulary.AGENT_FIELDS)})"
        )
    return {field: agent.get(field) for field in vocabulary.AGENT_FIELDS}


def _merge_agent(current, update):
    """渡された key だけを上書きする merge (null を渡せば明示的に消せる)。"""
    if not isinstance(update, dict):
        raise LedgerError("agent は object で渡す")
    unknown = set(update) - set(vocabulary.AGENT_FIELDS)
    if unknown:
        raise LedgerError(
            f"agent に未知の key: {sorted(unknown)} "
            f"(既知: {', '.join(vocabulary.AGENT_FIELDS)})"
        )
    merged = dict(current or {})
    merged.update(update)
    return {field: merged.get(field) for field in vocabulary.AGENT_FIELDS}


def _normalize_prs(prs):
    """prs[] を検証して正規化する。ref / role / status は中立語彙のみ受け付ける。"""
    if prs is None:
        return []
    if not isinstance(prs, list):
        raise LedgerError("prs は配列で渡す")
    normalized = []
    for item in prs:
        if not isinstance(item, dict):
            raise LedgerError("prs の要素は object で渡す")
        unknown = set(item) - set(vocabulary.PR_FIELDS)
        if unknown:
            raise LedgerError(
                f"prs に未知の key: {sorted(unknown)} (既知: {', '.join(vocabulary.PR_FIELDS)})"
            )
        ref = refs.parse_pr_ref(item.get("ref"))["ref"]
        role = item.get("role")
        if role not in vocabulary.PR_ROLES:
            raise LedgerError(
                f"未知の PR role: {role!r} (候補: {', '.join(vocabulary.PR_ROLES)})"
            )
        status = item.get("last_seen_status")
        if status is not None and status not in vocabulary.PR_STATUSES:
            raise LedgerError(
                f"未知の PR status: {status!r} (候補: {', '.join(vocabulary.PR_STATUSES)})"
            )
        # repo は検証しない自由記述 (server は repo 識別子の形を決めない)。無いまま記録
        # すると、別 repo に同番号の PR が居る運用で resolve が突合先を一意に決められない
        normalized.append(
            {"ref": ref, "role": role, "last_seen_status": status, "repo": item.get("repo")}
        )
    return normalized
