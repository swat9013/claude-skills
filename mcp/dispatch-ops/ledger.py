"""dispatch 台帳 (state.json + events.jsonl + signals.jsonl) の読み書き。

spec §3: 外部 store (tracker / pane / git) が「現実」、台帳は「意図と記録」。本 module は
記録の永続化と phase 遷移の合法性検証だけを持ち、「何をすべきか」の判断は一切持たない。

同時書き込み耐性 (spec §3.1):

- stdio MCP server はセッションごとに別プロセスなので、dispatcher と複数 pane が
  同じ state.json を同時に触りうる。プロセスメモリに状態を持たず、毎回 read-modify-write
  を advisory lock (`fcntl.flock`) の内側で完結させる
- state.json は temp + `os.replace` の atomic write。途中で落ちても半端な JSON を残さない
- events.jsonl は lock 下の append-only。state.json の上書きで消える履歴をここが保全する
- signals.jsonl も lock 下の append-only。events.jsonl が entry 1 件のライフサイクルを記録
  するのに対し、こちらは **entry に紐づかない事象も同じ 1 本へ書く** project 単位の stream
  で、主語は `subject` が持つ。観測から作り直せない状態 (何をもう送ったか) の durable な
  置き場で、読み手はこの stream を読み直して状態を再計算する
"""

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import refs
import repo_key as repo_key_mod
import vocabulary

STATE_VERSION = 1
STATE_FILENAME = "state.json"
EVENTS_FILENAME = "events.jsonl"
SIGNALS_FILENAME = "signals.jsonl"
LOCK_FILENAME = ".lock"

# ref 集合の安定 key の長さ (sha256 hexdigest の先頭 N 文字)。集合の同一性判定にしか使わない
# ので全長は要らず、短いほうが escalation の文面へ写したときに読める
SET_KEY_LENGTH = 12

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


@dataclass(frozen=True)
class EntryView:
    """台帳 entry 1 件の読み取り面。**entry の構造を知るのは本 class だけ**。

    消費側 (resolve / worktree) が `(entry["issue"] or {}).get("repo")` を書ける限り、schema の
    知識は module 境界を越えて散り、欄の意味 (`issue.repo` は issue 置き場ではなく**実装 repo**
    — ADR 0036 §4) を読み解く責務まで一緒に散る。述語の名前でその意味をここへ閉じる。

    **dict の subclass にしない** — 「まだ掘れる」逃げ道を残すと、述語を足す代わりに掘る側へ
    倒れて収束しない。

    `raw` は tool 応答へそのまま載せる dict (`Ledger._view` の出力)。**出力整形のためだけ**に
    開いてあり、判断に使う値は述語から採る。欄が欠けた entry (手で編集された state / 台帳導入
    前の記録) でも属性参照で落ちない — 落とすと「保護したつもりの worktree が消える」に化ける。

    view は in-memory のみで、state.json / events.jsonl の形式には触らない (ADR 0028 / 0036 の
    live 台帳を移行しない方針)。
    """

    raw: dict

    @property
    def issue_ref(self):
        return self.raw.get("issue_ref")

    @property
    def phase(self):
        return self.raw.get("phase")

    @property
    def updated_at(self):
        return self.raw.get("updated_at")

    @property
    def tracker(self):
        return self._issue.get("tracker")

    @property
    def implementation_repo(self):
        """**この issue を実装する repo** の識別子 (issue 置き場ではない — ADR 0036 §4)。"""
        return self._issue.get("repo")

    @property
    def recorded_worktree(self):
        """台帳が記録した作業ツリーのパス (未記録なら None)。"""
        return self._agent.get("worktree")

    @property
    def recorded_pane_id(self):
        """台帳が記録した pane id (未記録 / 駐機で降ろした後は None)。"""
        return self._agent.get("pane_id")

    @property
    def recorded_prs(self):
        """台帳が記録した PR record の列 (要素の綴りは `vocabulary.PR_FIELDS`)。"""
        return self.raw.get("prs") or []

    @property
    def slug(self):
        """issue slug (`i386` / `proj-9`)。綴れない ref は `refs.RefError` を投げる。"""
        return refs.format_issue_slug(self.issue_ref)

    @property
    def compact(self):
        """一覧を走査するための 1 行射影 (entry 全体の代わりに載せる dict)。

        全 entry を数えたり phase を見比べたりするだけの用途に entry 全体を配ると、数十件の
        台帳で数万トークンを食う。`raw` と違い**述語から組む** — 射影する欄が増えても schema の
        知識は本 class の外へ出ない。
        """
        return {
            "issue_ref": self.issue_ref,
            "phase": self.phase,
            "repo": self.implementation_repo,
            "worktree": self.recorded_worktree,
            "pr_count": len(self.recorded_prs),
            "updated_at": self.updated_at,
        }

    @property
    def is_terminal(self):
        """終端 phase か (履歴であって現況の突合先が無い)。"""
        return self.phase in vocabulary.TERMINAL_PHASES

    @property
    def is_protected(self):
        """作業ツリーを掃除から守る phase か。"""
        return self.phase in vocabulary.PROTECTED_PHASES

    @property
    def is_reclaimable(self):
        """作業ツリーを回収してよい phase か。"""
        return self.phase in vocabulary.RECLAIM_PHASES

    @property
    def _issue(self):
        return self.raw.get("issue") or {}

    @property
    def _agent(self):
        return self.raw.get("agent") or {}


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
        self.signals_path = self.directory / SIGNALS_FILENAME
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

    def log_signal(self, subject, kind, *, fields=None, refs=None, actor=DEFAULT_ACTOR):
        """project 単位の signal stream (signals.jsonl) へ 1 行足す。

        `log_event` との違いは主語の縛り — あちらは台帳 entry の履歴なので issue ref を
        要求するが、**observer が運ぶ状態には issue に紐づかない事象がある** (候補プールの
        出現 / deploy の前提が成立しない)。`subject` を ref として検証しないのはそのためで、
        検証を掛けると pseudo-ref を切る羽目になる。issue に紐づく事象は `subject` に
        issue ref (`gh#386`) を置いて同じ 1 本へ書く。

        `refs` を渡すと**重複除去とソートを server が行い、`refs` と安定 key を書く**。同じ
        集合なら同じ key になるので、読み手は集合を持ち回らずに「前と同じ集合か」を判定
        できる。正規化を server 側に置くのは、並び順や重複で key が揺れると同一性の判定に
        使えなくなるため。`refs` を渡さない signal は両方 null (集合を伴わない事象)。

        `kind` と `subject` の語彙は検証しない (`event` と同じ理由 — 何を書くかは呼び出し側の
        ポリシー)。空だけは撥ねる: 主語も種別も無い行は後から誰にも読み解けない。
        """
        subject = _require_text(subject, "subject", "この signal が何についてかを文字列で渡す")
        kind = _require_text(kind, "kind", "何が起きたかの種別を文字列で渡す")
        normalized_refs = _normalize_ref_set(refs)
        signal = {
            "ts": now_iso(),
            "actor": actor,
            "subject": subject,
            "kind": kind,
            "refs": normalized_refs,
            "key": _set_key(normalized_refs),
            "fields": fields or {},
        }
        with self._locked():
            self._append_line(self.signals_path, signal)
        return signal

    def read_signals(self, since=None, subject=None, kind=None, limit=None):
        """signal stream を読む (`since` 以降 / `subject` / `kind` で絞り、`limit` で末尾を取る)。

        **`since` はその ts の行を含む** (境界は取りこぼすより重複させる)。`now_iso` は秒
        精度なので同じ秒に複数行が並びうる。排他にすると同秒の兄弟行が読み手へ届かないまま
        落ち、欠落は読み手側に何の信号も出さない。重複は読み手が潰せる。

        **`limit` が返すのは古い側ではなく新しい側の N 行**。stream は消えないので、周期的に
        読む側が「最新の 1 行」を採るたびに全履歴を受け取ることになる。
        """
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            raise LedgerError("limit は 1 以上の整数で渡す")
        boundary = _normalize_boundary(since, "since")
        signals = []
        for signal in self._read_stream(self.signals_path):
            if boundary is not None and signal.get("ts", "") < boundary:
                continue
            if subject is not None and signal.get("subject") != subject:
                continue
            if kind is not None and signal.get("kind") != kind:
                continue
            signals.append(signal)
        return signals[-limit:] if limit is not None else signals

    def signals_view(self, since=None, subject=None, kind=None, limit=None):
        """tool 応答用の signal 一覧。どの台帳を見ているかを毎回添える。"""
        signals = self.read_signals(since=since, subject=subject, kind=kind, limit=limit)
        return {
            "repo_key": self.repo_key,
            "ledger_dir": str(self.directory),
            "count": len(signals),
            "signals": signals,
        }

    def read_events(self, since=None):
        """entry のライフサイクル履歴 (events.jsonl) を読む (`since` 以降だけに絞れる)。

        境界の解釈 (`_normalize_boundary`) と欠損・破損の扱い (`_read_stream`) を `read_signals`
        と共有する — **`since` はその ts の行を含み**、まだ 1 行も書いていない台帳は空を返し、
        壊れた行では loud に落ちる。片方だけ規律が違うと、同じ境界を渡した呼び出しが stream
        ごとに違う解釈を受ける。**揃うのはこの解釈までで、2 本を 1 つの lock 窓では読まない**
        (`_read_stream` は path ごとに lock を取り直す)。
        """
        boundary = _normalize_boundary(since, "since")
        events = []
        for event in self._read_stream(self.events_path):
            if boundary is not None and event.get("ts", "") < boundary:
                continue
            events.append(event)
        return events

    def changes_since(self, since_ts):
        """`since_ts` 以降に起きたことを件数と対象 ref へ畳んだ射影 (tool `changes_since` の実体)。

        報告はチャット出力なので、**前回の報告内容は compaction を跨ぐと失われる**。前回の
        報告時刻さえ渡せば「その後に何が動いたか」を 2 本の stream から作り直せる、という
        のが本 method の存在理由 (前回値を保存する新しい欄も file も増やさない)。

        群の内訳と `since_ts` の契約は tool 側の docstring (`main.py`) が正本。ここに残すのは
        実装の側の理由 2 つ:

        - **群ごとの畳み方は `_transition_groups` / `_event_groups` / `_signal_groups` が持つ。**
          本 method は窓を切って渡すだけで、どの key で束ねるかを知らない
        - **row をそのまま出力へ写す経路を作らない。** 件数と対象しか出さないので、
          `note` / `summary` / `pane_send` の `text` が差分へ漏れる経路が構造的に無い —
          漏らすと context を食わずに差分を読むという目的が自己否定される
        """
        boundary = _normalize_boundary(since_ts, "since_ts")
        events = self.read_events(since=boundary)
        signals = self.read_signals(since=boundary)
        transition_rows = [row for row in events if row.get("event") == "transition"]
        other_rows = [row for row in events if row.get("event") != "transition"]
        return {
            "repo_key": self.repo_key,
            "ledger_dir": str(self.directory),
            "since_ts": boundary,
            "counts": {
                "transitions": len(transition_rows),
                "events": len(other_rows),
                "signals": len(signals),
            },
            "transitions": _transition_groups(transition_rows),
            "events": _event_groups(other_rows),
            "signals": _signal_groups(signals),
        }

    def list_entries(self, phases=None):
        """記帳済み entry の一覧 (phase で絞り込み可能)。**`EntryView` の列を返す**。

        tool 応答の dict を返す他の method と返り値の型が違うのは意図した非対称 — 本 method
        だけが server 内部 (resolve / worktree) の消費者を持ち、そこに entry の構造を知らせ
        ないために typed view を配る。tool 面へ出る method は MCP 応答そのものなので dict の
        まま (`raw` を経た出力整形は `EntryView` の docstring)。
        """
        if phases is not None:
            phases = [vocabulary.require_phase(phase) for phase in phases]
        with self._locked() as state:
            entries = [
                EntryView(self._view(ref, entry))
                for ref, entry in state["dispatches"].items()
                if phases is None or entry["phase"] in phases
            ]
        entries.sort(key=lambda item: (item.updated_at or "", item.issue_ref))
        return entries

    def list_view(self, phases=None, compact=False):
        """tool 応答用の一覧。どの台帳を見ているかと phase 語彙を毎回添える。

        `_view` が entry 1 件に repo_key / ledger_dir を添えるのと同じ理由 (診断) で、
        一覧にも台帳の所在を載せる。phase 語彙を返すのは、呼び出し側が絞り込みに使える
        値を応答から読めるようにするため。

        `compact` が切り替えるのは entries の中身だけで、台帳の所在・phase 語彙・件数は
        どちらでも据え置く (診断のために毎回添える設計は射影の有無と独立)。
        """
        entries = self.list_entries(phases)
        return {
            "repo_key": self.repo_key,
            "ledger_dir": str(self.directory),
            "phases": list(vocabulary.PHASES),
            "count": len(entries),
            "entries": [entry.compact if compact else entry.raw for entry in entries],
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
        """tool 応答用の entry dict。どの台帳を見ているかを毎回添える。

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

    def _read_stream(self, path):
        """jsonl の stream を lock の内側で読む (未作成なら lock も取らずに空)。

        **append は不可分ではない** — `_append_line` は buffered writer で書くので、
        `pane_send` のように pane へ送った prompt 全文を載せた 1 行は複数回の write に
        割れうる。lock を取らずに読むと、その途中を読んだ読み手が**壊れていない file を
        壊れていると報告する** (再実行すれば通る「破損」は、実在する破損の信号を薄める)。

        書き手の `_locked` と違い共有 lock なので、読み手同士は待たない。state.json を
        読まないのも意図した差 — stream の読みに state の健全性を巻き込まない。
        """
        if not path.exists():
            return []
        with open(self.lock_path, "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                return _parse_jsonl(path)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

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
        self._append_line(self.events_path, event)

    def _append_line(self, path, record):
        """jsonl へ 1 行 append する (lock は呼び出し側が握っている前提)。"""
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise LedgerError(f"{path} に追記できない: {exc}") from exc


def _parse_jsonl(path):
    """実在する jsonl を 1 行 1 record へ解く (壊れた行は loud)。

    壊れた行を黙って飛ばすと、履歴の欠落が読み手に何の信号も出さない。**未作成かどうかは
    問わない** — 台帳が空か否かの判定は lock を取る前に済ませる話なので `_read_stream` が持つ。
    """
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise LedgerError(f"{path} に壊れた行がある: {exc}") from exc
    return records


def _normalize_boundary(value, name):
    """stream を絞る境界を `now_iso` と同じ書式 (UTC 秒精度 + `Z`) へ正規化する (未指定は None)。

    `ts` との比較は文字列比較なので、`+09:00` 付きの正当な ISO8601 をそのまま渡すと
    **辞書順が時刻順とずれ、静かに全件 / 0 件へ倒れる**。窓そのものが狂う誤りが「変化なし」
    という正常な形の答えになるため、境界は書式を検証してから比較する。**2 本の stream の
    reader が同じ 1 つの正規化を通る** — 片方だけ検証すると、同じ境界を渡した射影が
    stream ごとに違う窓を見る。

    offset の無い naive な値は UTC と決めつけずに撥ねる — 決めつけると、手元時刻を
    渡した呼び出しが時差の分だけずれた窓を黙って受け取る。小数秒は切り捨てる (窓が広がる
    側 = 取りこぼさない側)。
    """
    if value is None:
        return None
    text = _require_text(value, name, "ISO8601 (例 2026-09-02T04:00:00Z) で渡す")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerError(
            f"{name} を時刻として読めない: {text!r} (ISO8601 で渡す。例 2026-09-02T04:00:00Z)"
        ) from exc
    if parsed.tzinfo is None:
        raise LedgerError(
            f"{name} に timezone が無い: {text!r} (`Z` か offset を付けて渡す。例 2026-09-02T04:00:00Z)"
        )
    utc = parsed.astimezone(timezone.utc).replace(microsecond=0)
    return utc.isoformat().replace("+00:00", "Z")


def _transition_groups(rows):
    """phase 遷移の行を `from` → `to` の対ごとに畳む。"""
    return [
        {"from": key[0], "to": key[1], "count": count, "refs": refs}
        for key, count, refs in _tally(
            rows,
            lambda row: (row.get("from") or "", row.get("to") or ""),
            lambda row: row.get("issue"),
        )
    ]


def _event_groups(rows):
    """phase 遷移以外の events.jsonl の行を `event` ごとに畳む。"""
    return [
        {"event": key[0], "count": count, "refs": refs}
        for key, count, refs in _tally(
            rows,
            lambda row: (row.get("event") or "",),
            lambda row: row.get("issue"),
        )
    ]


def _signal_groups(rows):
    """signal を `kind` + `fields.topic` の対ごとに畳む。

    対象を `refs` ではなく `subjects` で持つのは、signal の主語が issue ref とは限らない
    ため (候補プール / deploy)。行が伴う `refs` (候補集合そのもの) は畳まない — 最新の
    集合が要るなら `signal_read` が 1 行で返す。
    """
    return [
        {"kind": key[0], "topic": key[1] or None, "count": count, "subjects": subjects}
        for key, count, subjects in _tally(
            rows,
            lambda row: (row.get("kind") or "", _signal_topic(row) or ""),
            lambda row: row.get("subject"),
        )
    ]


def _signal_topic(signal):
    """signal の `fields.topic` (空・非文字列は topic 無しと同じ扱い)。"""
    topic = (signal.get("fields") or {}).get("topic")
    return topic if isinstance(topic, str) and topic.strip() else None


def _tally(rows, group_of, target_of):
    """行を group key で束ね、`(key, 件数, 対象の列)` を件数の多い順に返す。

    並びを出現順でなく件数順にするのは、報告の `changed` 1 行が「何がいちばん動いたか」
    から読まれるため。同数のときは key の辞書順で止める (同じ入力から同じ差分が出る)。
    """
    groups = {}
    for row in rows:
        bucket = groups.setdefault(group_of(row), {"count": 0, "targets": set()})
        bucket["count"] += 1
        target = target_of(row)
        if target is not None:
            bucket["targets"].add(target)
    ordered = sorted(groups.items(), key=lambda item: (-item[1]["count"], item[0]))
    return [(key, bucket["count"], sorted(bucket["targets"])) for key, bucket in ordered]


def _require_text(value, name, hint):
    """空でない文字列を要求して strip した値を返す。"""
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f"{name} が空 ({hint})")
    return value.strip()


def _normalize_ref_set(refs):
    """ref 集合を重複除去 + ソートした列にする (未指定は None のまま)。"""
    if refs is None:
        return None
    if not isinstance(refs, list):
        raise LedgerError("refs は配列で渡す")
    for ref in refs:
        if not isinstance(ref, str) or not ref.strip():
            raise LedgerError(f"refs に空でない文字列でない要素がある: {ref!r}")
    return sorted({ref.strip() for ref in refs})


def _set_key(refs):
    """ref 集合の安定 key (集合が同じなら同じ key)。集合を伴わない signal は None。

    集合そのものを持ち回らずに同一性を判定するための短縮表現で、衝突しても実害が出ない
    用途 (同じ集合か違う集合か) にしか使わないため全長を持たない。
    """
    if refs is None:
        return None
    canonical = json.dumps(refs, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:SET_KEY_LENGTH]


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
