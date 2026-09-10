"""events.jsonl から再構築できる SQLite の投影 (設計 `docs/design/dispatch-v2/system.md`「8. 永続化」)。

**正本ではない。** 正本は append-only の events.jsonl で、ここに在るのは `fold(events)` と観測
cache を平らな表へ写しただけのもの。消して作り直せるので schema 変更に移行手順が要らない
(設計 決定 9)。

## なぜ SQLite を挟むのか — thread の境界そのもの

dashboard HTTP は daemon に同居する (設計 決定 6) が、daemon では polling tick も回る
(ADR 0056)。dashboard を同じスレッドに載せると、browser の polling と keep-alive が inbox を
pull する HTTP と席を奪い合い、長い tick がそのまま画面の停止になる。

そこで **書き手 (daemon の周期処理スレッド) と読み手 (dashboard thread) の唯一の接点をこの
file の SQLite に置く**。

```
周期処理スレッド : events.jsonl 追記 → fold → ここへ投影 (書き。本 module)
dashboard 側     : mode=ro で開いて読むだけ (`view_read` module)
```

**書き手が 1 スレッドであることは `http_app.PeriodicWorker` からしか投影を回さないことで
保つ**。request は接続ごとのスレッドで走るが、そこから投影へは触らない — 触ると `sqlite3` の
thread 親和性 (`check_same_thread`) を破る。

読み手は正本にも memory 上の state にも触らないので、共有可変オブジェクトが 1 つも生まれない
(`principle-isolate-shared-writes`: まず共有そのものを解消する)。tick が長引いても dashboard は
応答し続ける — 止まるのではなく**古くなる**だけで、古さは `projected_at` に出る。

## 耐久性の違う 2 群

| 群 | table | 消えたら |
|---|---|---|
| 台帳由来 (`LEDGER_TABLES`) | projects / work_orders / sessions / escalations | events.jsonl から同じ内容へ再構築できる |
| 観測 cache 由来 (`OBSERVATION_TABLES`) | observed_* / observations | 再構築されない。次の tick の観測が埋めるまで「未観測」として理由付きで出る |

後者が再構築されないのは cache がそもそも正本でないから (`observation` module の宣言)。
**空に戻った列は「無い」ではなく理由付きの未観測として読まれる**ので、消えたことは沈黙しない。

台帳由来の唯一の例外は `projects.projected_at` で、これは**投影を回した時刻**であって正本から
導出できる値ではない (再構築すると再構築した時刻になる)。鮮度の印なので、その意味で正しい。

## 起動のたびに投影し直す

`open()` は schema を作った後に全行を捨てる。daemon が落ちた瞬間が「追記したが投影する前」
だった場合、revision だけが一致した stale な投影が残りうるため — 起動時に走査して収束させる
(`principle-idempotent-operations`)。表は数十行なので捨てて作り直す費用は無視できる。
"""

import json
import sqlite3
import sys
from pathlib import Path

from dispatch_v2 import observation

# 投影の版。schema を変えたら上げる。**移行 script は書かない** — 版が違えば `open()` が
# table ごと作り直す (正本は events.jsonl なので、捨てても失われるものが無い)
SCHEMA_VERSION = "2"

# 観測 cache 側の版を `meta` に置くときの鍵の接頭辞。**起動のたびに捨てる**ので、接頭辞で
# まとめて消せる形にしてある
_OBSERVATION_REVISION_PREFIX = "observation_revision:"

#: 台帳 (events.jsonl) だけから作れる table。**この群だけが再構築の対象**
LEDGER_TABLES = ("projects", "work_orders", "sessions", "escalations")

#: 外部 store の観測 cache から写した table。daemon の再起動で空に戻る
OBSERVATION_TABLES = (
    "observed_issues",
    "observed_candidates",
    "observed_cls",
    "observed_cl_links",
    "observed_cl_link_scans",
    "observations",
)

#: 投影そのものの不調を残す table。**台帳由来でも観測 cache 由来でもない** — 投影の失敗は
#: 台帳にも外部 store にも存在しない事実で、これが無いと落ちた project は画面から行ごと消える
DIAGNOSTIC_TABLES = ("projection_failures",)

# 版を読むために**先に**要る 1 表。残りの schema と分けてあるのは、版が違うと分かった時点で
# 残り全部を落とすため (版そのものを持つ表を落として読めなくしない)
META_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_key TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    projected_at TEXT NOT NULL,
    clone_path TEXT,
    anomaly_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS work_orders (
    project_key TEXT NOT NULL,
    wo_id TEXT NOT NULL,
    issue_ref TEXT NOT NULL,
    phase TEXT NOT NULL,
    note TEXT,
    evidence TEXT,
    outcome TEXT,
    outcome_summary TEXT,
    outcome_reported_by TEXT,
    outcome_reported_at TEXT,
    worktree_path TEXT,
    worktree_branch TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_key, wo_id)
);
CREATE TABLE IF NOT EXISTS sessions (
    project_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    wo_id TEXT NOT NULL,
    backend TEXT,
    label TEXT,
    handle TEXT,
    lifecycle TEXT NOT NULL,
    ended_reason TEXT,
    activity TEXT,
    activity_raw TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_key, session_id)
);
CREATE TABLE IF NOT EXISTS escalations (
    project_key TEXT NOT NULL,
    escalation_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    wo_id TEXT,
    dedup_key TEXT NOT NULL,
    evidence TEXT,
    attempts INTEGER NOT NULL,
    permanent INTEGER NOT NULL,
    raised_at TEXT NOT NULL,
    last_sent_at TEXT,
    acked_at TEXT,
    PRIMARY KEY (project_key, escalation_id)
);
CREATE TABLE IF NOT EXISTS observed_issues (
    project_key TEXT NOT NULL,
    issue_ref TEXT NOT NULL,
    state TEXT NOT NULL,
    observed_at TEXT,
    PRIMARY KEY (project_key, issue_ref)
);
CREATE TABLE IF NOT EXISTS observed_candidates (
    project_key TEXT NOT NULL,
    issue_ref TEXT NOT NULL,
    state TEXT NOT NULL,
    observed_at TEXT,
    PRIMARY KEY (project_key, issue_ref)
);
CREATE TABLE IF NOT EXISTS observed_cls (
    project_key TEXT NOT NULL,
    cl_ref TEXT NOT NULL,
    repo TEXT,
    status TEXT NOT NULL,
    mergeable TEXT,
    unresolved_threads INTEGER,
    head_sha TEXT,
    observed_at TEXT,
    PRIMARY KEY (project_key, cl_ref)
);
CREATE TABLE IF NOT EXISTS observed_cl_links (
    project_key TEXT NOT NULL,
    issue_ref TEXT NOT NULL,
    cl_ref TEXT NOT NULL,
    repo TEXT,
    role TEXT NOT NULL,
    observed_at TEXT,
    PRIMARY KEY (project_key, issue_ref, cl_ref)
);
-- 紐づきを **観測した issue** の一覧。link の行と別に要るのは、「観測して紐づく CL が 0 件」を
-- 「まだ観測していない」と分けるため (link の行が無い理由が 2 通りある)
CREATE TABLE IF NOT EXISTS observed_cl_link_scans (
    project_key TEXT NOT NULL,
    issue_ref TEXT NOT NULL,
    observed_at TEXT,
    PRIMARY KEY (project_key, issue_ref)
);
CREATE TABLE IF NOT EXISTS observations (
    project_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    observed INTEGER NOT NULL,
    observed_at TEXT,
    reason TEXT,
    PRIMARY KEY (project_key, subject)
);
-- 投影そのものが書けなかった project。**行が消えたことを画面へ届ける唯一の経路**で、
-- これが無いと投影に失敗した project は「daemon が抱えていない project」と区別が付かない
CREATE TABLE IF NOT EXISTS projection_failures (
    project_key TEXT NOT NULL PRIMARY KEY,
    reason TEXT NOT NULL,
    failed_at TEXT NOT NULL
);
"""


def _connect(path):
    """書き手の接続を開く。

    WAL にするのは **読み手 (dashboard thread) が書き込み中の transaction で待たされない**
    ため。読み手は同じ file を `mode=ro` で開くので、SQLite 側で書き込みが構造的に閉じる。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def _open_writer(path):
    """書き手の接続を張る: 版が違えば作り直し、前回の投影を捨てて空から始める。

    **版が違えば table ごと落とす**。DDL は `IF NOT EXISTS` なので、列を足しただけの版で
    古い file に当たると table は古い形のまま残り、INSERT が落ちて**その project の投影が
    永久に凍る** (投影の失敗は daemon を止めないので、画面が古いまま気付かれない)。
    """
    connection = _connect(path)
    connection.executescript(META_SCHEMA)
    if _stored_schema_version(connection) != SCHEMA_VERSION:
        _drop_everything(connection)
        connection.executescript(META_SCHEMA)
    connection.executescript(SCHEMA)
    with connection:
        for table in LEDGER_TABLES + OBSERVATION_TABLES + DIAGNOSTIC_TABLES:
            connection.execute(f"DELETE FROM {table}")
        # **観測側の版も一緒に捨てる**。`ObservationCache.revision` は daemon の起動ごとに
        # 0 から数え直すので、前回の版が meta に残っていると再起動後の観測が「同じ版だから
        # 書かなくてよい」と判定され、画面が「まだ観測が 1 度も成功していない」を出し続ける
        connection.execute(
            "DELETE FROM meta WHERE key LIKE ?", (f"{_OBSERVATION_REVISION_PREFIX}%",)
        )
        connection.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
    return connection


class MaterializedView:
    """投影の書き手。**最初に書いたスレッドだけが持つ** (書き手は 1 つという不変条件)。

    接続を組み立ての場で張らず最初の書き込みまで遅らせるのは、**sqlite の接続が張った
    スレッドでしか使えない** (`check_same_thread`) から。daemon は main thread で組み立て、
    投影を回すのは周期処理スレッド (`http_app.PeriodicWorker`) なので、組み立ての場で張ると
    最初の投影が必ず `ProgrammingError` で落ちる — しかも投影の失敗は project ごとに畳まれる
    ので、画面が起動時のまま凍ったことに誰も気付けない (gh#967)。
    """

    def __init__(self, path):
        self.path = Path(path)
        # 最初の書き込みで張る接続。**張ったスレッドが書き手**になる
        self._opened = None

    @property
    def _connection(self):
        """書き手の接続。**最初に触ったスレッドで張る** (`_open_writer` は何度撃っても同じ)。"""
        if self._opened is None:
            self._opened = _open_writer(self.path)
        return self._opened

    @classmethod
    def open(cls, path):
        """view を開く: 版が違えば作り直し、前回の投影を捨てて空から始める。

        **張った接続はここで閉じる**。組み立ては daemon の main thread で走るので、接続を
        持ち越すと最初の投影が別スレッドから触ることになる (`ProgrammingError`)。版の検査と
        作り直しだけを起動時に済ませ、書くための接続は書き手が張り直す。
        """
        path = Path(path)
        _open_writer(path).close()
        return cls(path)

    def close(self):
        if self._opened is None:
            return
        self._opened.close()
        self._opened = None


    def reopen_if_missing(self):
        """view file が消えていたら開き直す。開き直したなら True。

        **投影は消しても復活する** — 正本は events.jsonl なので、捨てて作り直せることが
        この file の存在理由そのもの (設計 決定 9)。daemon を止めずに捨てられる形にしないと、
        「schema を捨てるつもりで消す」操作が daemon の再起動を強いる。

        消えた file へ書き続けても例外は出ない (開いたままの inode に落ちるだけ) ので、
        **書き手が気付ける唯一の場所がここ**。読み手からは「daemon が投影を書いていない」に
        見えるが、実際には書けている — 一致しない 2 つの見え方を残さない。
        """
        if self.path.exists():
            return False
        self.close()
        # 本体だけ消えて WAL / shm が残っていると、新しい空の DB へ古い page が復元されうる。
        # 投影は捨ててよいものなので、道連れにして完全に空から始める
        for sidecar in (f"{self.path}-wal", f"{self.path}-shm"):
            Path(sidecar).unlink(missing_ok=True)
        # **張り直しは `_open_writer` 1 本に通す**。ここで schema を組み直すと、版の検査や
        # 観測版の掃除がこちらの経路にだけ無い状態が生まれる (実際に生まれていた)
        self._opened = _open_writer(self.path)
        return True

    def refresh_ledger(self, project_key, *, book, projected_at):
        """台帳の現況 (`fold(events)`) を写す。**台帳が動いていなければ何もしない**。

        投影は state の写しなので、その project の行を丸ごと入れ替える — event 1 件ずつ差分を
        当てる形にすると、live な投影と再構築した投影で 2 つの経路が生まれ、片方だけがずれる
        余地ができる。
        """
        if self._projected_revision(project_key) == book.revision:
            return
        state = book.state
        with self._connection:
            self._forget(LEDGER_TABLES, project_key)
            self._connection.execute(
                "INSERT INTO projects (project_key, revision, projected_at, clone_path,"
                " anomaly_count) VALUES (?, ?, ?, ?, ?)",
                (
                    project_key,
                    book.revision,
                    projected_at,
                    state["clone_path"],
                    len(state["anomalies"]),
                ),
            )
            self._connection.executemany(
                "INSERT INTO work_orders (project_key, wo_id, issue_ref, phase, note, evidence,"
                " outcome, outcome_summary, outcome_reported_by, outcome_reported_at,"
                " worktree_path, worktree_branch, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [_work_order_row(project_key, work_order) for work_order in state["work_orders"].values()],
            )
            self._connection.executemany(
                "INSERT INTO sessions (project_key, session_id, wo_id, backend, label, handle,"
                " lifecycle, ended_reason, activity, activity_raw, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [_session_row(project_key, session) for session in state["sessions"].values()],
            )
            self._connection.executemany(
                "INSERT INTO escalations (project_key, escalation_id, rule_id, wo_id, dedup_key,"
                " evidence, attempts, permanent, raised_at, last_sent_at, acked_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    _escalation_row(project_key, escalation)
                    for escalation in state["escalations"].values()
                ],
            )

    def refresh_observations(self, project_key, *, cache, projected_at):
        """外部 store の観測 cache を写す。**観測が動いていなければ何もしない**。

        `projected_at` は cache 群では使わない (行ごとの鮮度は観測時刻 `observed_at` が持つ) が、
        受け取るのは呼び出し側が台帳側と同じ 1 つの時刻で投影を回せるようにするため。
        """
        if self._projected_observation_revision(project_key) == cache.revision:
            return
        with self._connection:
            self._forget(OBSERVATION_TABLES, project_key)
            self._connection.executemany(
                "INSERT INTO observed_issues (project_key, issue_ref, state, observed_at)"
                " VALUES (?, ?, ?, ?)",
                [_issue_row(project_key, fact) for fact in cache.issues()],
            )
            self._connection.executemany(
                "INSERT INTO observed_candidates (project_key, issue_ref, state,"
                " observed_at) VALUES (?, ?, ?, ?)",
                [_issue_row(project_key, fact) for fact in cache.candidates() or []],
            )
            self._connection.executemany(
                "INSERT INTO observed_cls (project_key, cl_ref, repo, status, mergeable,"
                " unresolved_threads, head_sha, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [_cl_row(project_key, fact) for fact in cache.cls()],
            )
            self._connection.executemany(
                "INSERT INTO observed_cl_links (project_key, issue_ref, cl_ref, repo,"
                " role, observed_at) VALUES (?, ?, ?, ?, ?, ?)",
                list(_cl_link_rows(project_key, cache.cl_links_by_issue())),
            )
            self._connection.executemany(
                "INSERT INTO observed_cl_link_scans (project_key, issue_ref,"
                " observed_at) VALUES (?, ?, ?)",
                [
                    (project_key, issue_ref, observed["observed_at"])
                    for issue_ref, observed in cache.cl_links_by_issue().items()
                ],
            )
            self._connection.executemany(
                "INSERT INTO observations (project_key, subject, observed, observed_at, reason)"
                " VALUES (?, ?, ?, ?, ?)",
                _observation_rows(project_key, cache),
            )
            self._connection.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_observation_revision_key(project_key), str(cache.revision)),
            )

    def record_failure(self, project_key, *, reason, failed_at):
        """その project の投影が書けなかった事実を残す (次に成功したら消える)。

        **投影の失敗を stderr だけに書かない** — 行が書けていない project は画面から丸ごと
        消え、「daemon が抱えていない project」と区別が付かなくなる。台帳にも外部 store にも
        無い事実なので、投影自身が持つしかない。
        """
        with self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO projection_failures (project_key, reason, failed_at)"
                " VALUES (?, ?, ?)",
                (project_key, reason, failed_at),
            )

    def clear_failure(self, project_key):
        with self._connection:
            self._connection.execute(
                "DELETE FROM projection_failures WHERE project_key = ?", (project_key,)
            )

    def _forget(self, tables, project_key):
        for table in tables:
            self._connection.execute(f"DELETE FROM {table} WHERE project_key = ?", (project_key,))

    def _projected_revision(self, project_key):
        row = self._connection.execute(
            "SELECT revision FROM projects WHERE project_key = ?", (project_key,)
        ).fetchone()
        return row[0] if row is not None else None

    def _projected_observation_revision(self, project_key):
        """観測 cache 側の版。**`projects` に置かない** — 台帳と観測は別々の周期で動くので、
        1 行に同居させると片方の投影がもう片方の版を巻き戻す。
        """
        row = self._connection.execute(
            "SELECT value FROM meta WHERE key = ?", (_observation_revision_key(project_key),)
        ).fetchone()
        return int(row[0]) if row is not None else None


def _stored_schema_version(connection):
    row = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return row[0] if row is not None else None


def _drop_everything(connection):
    """この file にある table を全部落とす (版が変わったときの作り直し)。

    現在の `SCHEMA` に並んでいる名前ではなく **file の中身から数える** — 版を跨いで table を
    やめたときに、消し忘れた table が残らない。
    """
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    with connection:
        for name in names:
            connection.execute(f"DROP TABLE {name}")


def _observation_revision_key(project_key):
    return f"{_OBSERVATION_REVISION_PREFIX}{project_key}"


def _work_order_row(project_key, work_order):
    outcome = work_order["outcome"] or {}
    worktree = work_order["worktree"] or {}
    return (
        project_key,
        work_order["wo_id"],
        work_order["issue_ref"],
        work_order["phase"],
        work_order["note"],
        # **機械遷移の evidence は dict** (`reconciler` が観測した issue / CL / Session を丸ごと
        # 積む)。列に開かず JSON のまま持つ — 画面は「何を根拠に terminal へ落ちたか」を人が
        # 読む用途にしか使わない
        _as_json(work_order["evidence"]),
        outcome.get("outcome"),
        outcome.get("summary"),
        outcome.get("reported_by"),
        outcome.get("reported_at"),
        worktree.get("path"),
        worktree.get("branch"),
        work_order["created_at"],
        work_order["updated_at"],
    )


def _session_row(project_key, session):
    return (
        project_key,
        session["session_id"],
        session["wo_id"],
        session["backend"],
        session["label"],
        session["handle"],
        session["lifecycle"],
        session["ended_reason"],
        session["activity"],
        session["activity_raw"],
        session["created_at"],
        session["updated_at"],
    )


def _escalation_row(project_key, escalation):
    return (
        project_key,
        escalation["escalation_id"],
        escalation["rule_id"],
        escalation["wo_id"],
        escalation["dedup_key"],
        # evidence は rule ごとに形が違う自由な object なので、列に開かず JSON のまま持つ
        _as_json(escalation["evidence"]),
        escalation["attempts"],
        # SQLite に bool 型は無いので 0/1 で持つ。**1 度きりの配送で終わった escalation は
        # `attempts` が上限へ達しないまま止まる**ので、この列が無いと読み手が配送中と区別
        # できない (gh#930)
        int(escalation["permanent"]),
        escalation["raised_at"],
        escalation["last_sent_at"],
        escalation["acked_at"],
    )


def _as_json(value):
    """自由形式の欄 (evidence) を TEXT 列へ入る形にする。

    **SQLite は dict を bind できない**ので、写し忘れるとその project の投影が例外で落ち、
    版だけが先へ進んで**二度と一致しない** (画面が古い状態で凍る) — 自由形式の欄は必ず
    ここを通す。
    """
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _issue_row(project_key, fact):
    return (project_key, fact["issue_ref"], fact["state"], fact.get("observed_at"))


def _cl_row(project_key, fact):
    threads = fact["unresolved_threads"]
    return (
        project_key,
        fact["cl_ref"],
        fact["repo"],
        fact["status"],
        fact["mergeable"],
        # **未観測 (None) を 0 に潰さない** — 「未解決 thread を数えられなかった」と
        # 「数えて 0 件」は別物で、潰すと park してよいかの判断が変わる
        len(threads) if threads is not None else None,
        fact["head_sha"],
        fact.get("observed_at"),
    )


def _cl_link_rows(project_key, links_by_issue):
    for issue_ref, observed in links_by_issue.items():
        for link in observed["links"]:
            yield (
                project_key,
                issue_ref,
                link["cl_ref"],
                link["repo"],
                link["role"],
                observed["observed_at"],
            )


def _observation_rows(project_key, cache):
    """主語ごとの観測状況を行にする。**判定は cache が持つ** (`status_of`)。

    ここで面ごとの成否を組み直すと、読み手 (`view_read`) と 2 つの定義が生まれる — 実際に
    1 度そうなり、CL を 1 度も観測していない面が「観測済み」を名乗った。
    """
    return [
        (
            project_key,
            subject,
            int(status["observed"]),
            status["observed_at"],
            status["reason"],
        )
        for subject, status in (
            (subject, cache.status_of(subject))
            for subject in observation.OBSERVATION_SUBJECTS
        )
    ]


class Projection:
    """全 project を投影し直す callable。**daemon の周期処理スレッドが毎周回呼ぶ**。

    tick (60 秒周期) ではなく毎周回に載せるのは、**HTTP 経由の記帳も画面へ届かせる**
    ため — `wo_create` で立った WorkOrder が次の tick まで画面に出ないと、dashboard は「今
    何が起きているか」を答えられない。台帳も観測 cache も版 (`revision`) が動いていなければ
    書かないので、毎周回呼んでも実費は版の比較だけで済む。

    **1 project の失敗を他へ波及させない**。投影は正本ではないので、1 つ書けなくても daemon の
    本務は続く — 代わりに理由を stderr へ出す (毎周回同じ行を積まないよう、内容が変わった
    ときだけ)。
    """

    def __init__(self, view, *, registry, reconciler, clock, log=None):
        self._view = view
        self._registry = registry
        self._reconciler = reconciler
        self._clock = clock
        self._log = log
        self._complaints = {}

    def __call__(self):
        projected_at = self._clock()
        if self._view.reopen_if_missing():
            # **消えるたびに 1 行出す**。dedup は同じ状態を積まないためのもので、再発を
            # 握り潰すためではない — 忘れないと 2 度目以降の削除が無言になる
            self._complaints.pop(None, None)
            self._complain(None, "view file が消えていたので作り直した (投影は捨てても復活する)")
        for project_key in self._registry.known_projects():
            try:
                self._project_one(project_key, projected_at)
            except Exception as exc:  # noqa: BLE001 — project 境界での隔離がこの 1 箇所
                reason = f"投影に失敗: {type(exc).__name__}: {exc}"
                self._complain(project_key, reason)
                self._record_failure(project_key, reason, projected_at)
            else:
                # **失敗から復帰した回だけ消す**。無条件に消すと、何も起きていない project へ
                # 毎周回 DELETE を撃つことになり、「版が動いていなければ書かない」が崩れる
                if self._complaints.pop(project_key, None) is not None:
                    self._view.clear_failure(project_key)

    def _record_failure(self, project_key, reason, projected_at):
        """失敗を投影へ残す。**残すこと自体が失敗しても諦める** (log には既に出ている)。"""
        try:
            self._view.record_failure(project_key, reason=reason, failed_at=projected_at)
        except Exception:  # noqa: BLE001 — 失敗の記録に失敗しても serve は続ける
            self._complain(project_key, f"投影の失敗を記録することにも失敗した: {reason}")

    def _project_one(self, project_key, projected_at):
        self._view.refresh_ledger(
            project_key, book=self._registry.ledger_for(project_key), projected_at=projected_at
        )
        self._view.refresh_observations(
            project_key, cache=self._reconciler.cache_for(project_key), projected_at=projected_at
        )

    def _complain(self, project_key, message):
        """同じ失敗を繰り返し出さない (周期処理は 1 秒に 2 度回る)。

        `project_key` が `None` なのは投影 file そのものについての報告 (project を選べない)。
        """
        if self._complaints.get(project_key) == message:
            return
        self._complaints[project_key] = message
        subject = f"{project_key}: " if project_key is not None else ""
        print(f"[dispatch-v2] {subject}{message}", file=self._log or sys.stderr)


