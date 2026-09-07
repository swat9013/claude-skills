"""投影 (`materialized_view`) を read-only で読み、画面が並べられる形へ組む。

**書き手と分けてあるのは、変わる理由が別だから**。schema と投影の書き方が変わるのは台帳や
観測の語彙が動いたときで、ここが変わるのは画面に出す欄が動いたとき。同じ file に置くと、
列を 1 つ画面へ足すだけの変更が DDL と書き手の隣に差分を作る。

読み手が持つのは `mode=ro` の接続だけで、正本 (events.jsonl) にも daemon の memory 上の
state にも触らない。したがって dashboard の thread は書き手と同期を取る必要が無い
(境界の全体像は `materialized_view` の docstring が正本)。
"""

import sqlite3
from pathlib import Path

from dispatch_v2 import fold, observation

# view がまだ 1 度も書かれていないときに読み手へ返す理由。**「project が 0 件」と分ける** —
# daemon が起動していない / 起動直後で投影が回っていない、と「観測して 0 件」は別物
VIEW_NOT_WRITTEN_YET = "daemon がまだ投影を書いていない (起動直後か、daemon が居ない)"

# 面としては観測できているが、この WorkOrder の順番がまだ来ていない状態。**面ごと止まって
# いる理由と分ける** — こちらは待てば埋まる (1 tick の観測予算を跨いで巡る)
NOT_YET_VISITED = "この WorkOrder はまだ巡回で観測されていない (待てば埋まる)"


class ViewReader:
    """投影の読み手。**書き込み経路を持たない** (`mode=ro` の接続しか開かない)。

    dashboard thread が持つ唯一の data 源。正本 (events.jsonl) にも daemon の memory 上の
    state にも触らないので、読み手が書き手と同期する必要が無い。

    接続は呼び出しごとに開いて閉じる — thread ごとに持ち回るより単純で、dashboard の polling
    (数秒に 1 回) では接続確立の費用が問題にならない。
    """

    def __init__(self, path):
        self.path = Path(path)

    def connection(self):
        """read-only の接続を開く。**書き込みは SQLite が拒む** (規約ではなく構造)。"""
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def overview(self):
        """project 横断の現況。view がまだ無ければ理由を添えて空で返す。

        **投影が無いことを「project が 0 件」として返さない** — daemon が居ない / 起動直後で
        まだ投影が回っていない状態を「dispatch していない」と読ませない。
        """
        if not self.path.exists():
            return {"projects": [], "failed_projects": [], "reason": VIEW_NOT_WRITTEN_YET}
        connection = self.connection()
        try:
            # **`with connection:` で済ませない** — sqlite3 の context manager が閉じるのは
            # transaction であって接続ではない。dashboard は数秒ごとに polling されるので、
            # 閉じ損ねると daemon の file descriptor が単調に増える
            return {
                "projects": [
                    _project_overview(connection, row)
                    for row in connection.execute("SELECT * FROM projects ORDER BY project_key")
                ],
                # **投影できなかった project を別欄で返す** — 行が無いことを「その project は
                # 無い」と読ませない。落ちた project は一覧から消えるのではなく、理由付きで並ぶ
                "failed_projects": [
                    dict(row)
                    for row in connection.execute(
                        "SELECT * FROM projection_failures ORDER BY project_key"
                    )
                ],
                "reason": None,
            }
        finally:
            connection.close()


def _project_overview(connection, project_row):
    project_key = project_row["project_key"]
    observations = _observations_of(connection, project_key)
    sessions = _sessions_by_work_order(connection, project_key)
    escalations = _pending_escalations_by_work_order(connection, project_key)
    links = _cl_links_by_issue(connection, project_key)
    issues = {
        row["issue_ref"]: dict(row) for row in _rows(connection, "observed_issues", project_key)
    }
    return {
        "project_key": project_key,
        "projected_at": project_row["projected_at"],
        "clone_path": project_row["clone_path"],
        "anomaly_count": project_row["anomaly_count"],
        "observations": observations,
        "candidates": (
            [dict(row) for row in _rows(connection, "observed_candidates", project_key)]
            if observations["candidates"]["observed"]
            else None
        ),
        "work_orders": [
            _work_order_overview(
                row,
                sessions=sessions,
                escalations=escalations,
                links=links,
                issues=issues,
                observations=observations,
            )
            for row in connection.execute(
                "SELECT * FROM work_orders WHERE project_key = ? ORDER BY created_at, wo_id",
                (project_key,),
            )
        ],
        "escalations": [
            dict(escalation) for escalation in escalations.get(None, [])
        ],
    }


def _work_order_overview(row, *, sessions, escalations, links, issues, observations):
    """1 WorkOrder ぶんの行に、観測で分かることを足す。

    **観測由来の欄は「未観測 (`null`) + 理由」と「観測して無い」を分けて返す**。潰すと、
    ChangeHost の観測が落ちている project の全 WorkOrder が「PR 無し」に、tracker の観測が
    落ちていれば全部が「issue の状態は不明」ではなく「無い」に見える。
    """
    work_order = dict(row)
    work_order["session"] = sessions.get(row["wo_id"])
    work_order["escalations"] = [dict(escalation) for escalation in escalations.get(row["wo_id"], [])]
    work_order["issue"], work_order["issue_reason"] = _observed_or_reason(
        issues.get(row["issue_ref"]), observations["issues"]
    )
    work_order["cls"], work_order["cls_reason"] = _observed_or_reason(
        links.get(row["issue_ref"]), observations["cls"]
    )
    return work_order


def _observed_or_reason(found, status):
    """その行の観測値と、無いときの理由。**「無い」に潰さない**。

    理由は 2 段ある: 面ごと止まっている (宣言 config が無い / 一度も成功していない) のと、
    面は動いているがこの WorkOrder の順番がまだ来ていない (1 tick の観測予算を跨いで巡る —
    設計 §7) の 2 つで、次の一手が違う (前者は人が直す、後者は待てば埋まる)。
    """
    if not status["observed"]:
        return None, status["reason"]
    if found is None:
        return None, NOT_YET_VISITED
    return found, None


def _observations_of(connection, project_key):
    rows = {
        row["subject"]: {
            "observed": bool(row["observed"]),
            "observed_at": row["observed_at"],
            "reason": row["reason"],
        }
        for row in _rows(connection, "observations", project_key)
    }
    # 投影がまだ届いていない主語も欄として返す (読み手が subject の有無を場合分けしないで済む)
    return {
        subject: rows.get(subject, observation.unobserved_status())
        for subject in observation.OBSERVATION_SUBJECTS
    }


def _sessions_by_work_order(connection, project_key):
    """WorkOrder → **最後に記帳された Session**。

    1 WorkOrder に複数の Session が並ぶのは再 spawn したときで、非終端は 0..1 に限られる
    (ADR 0055)。画面が示すべきは「今どうなっているか」なので末尾を採る。
    """
    latest = {}
    for row in connection.execute(
        "SELECT * FROM sessions WHERE project_key = ? ORDER BY created_at, session_id",
        (project_key,),
    ):
        latest[row["wo_id"]] = dict(row)
    return latest


def _pending_escalations_by_work_order(connection, project_key):
    """まだ ack されていない escalation を WorkOrder ごとに束ねる (紐づかないものは `None` の鍵)。"""
    pending = {}
    for row in connection.execute(
        "SELECT * FROM escalations WHERE project_key = ? AND acked_at IS NULL"
        " ORDER BY raised_at, escalation_id",
        (project_key,),
    ):
        escalation = dict(row)
        # 打ち切りの判定は台帳側 (`fold`) が持つ — 上限の綴りを読み手側へ写経しない
        escalation["delivery_cut_off"] = fold.delivery_is_cut_off(escalation)
        pending.setdefault(row["wo_id"], []).append(escalation)
    return pending


def _cl_links_by_issue(connection, project_key):
    """issue → 紐づく CL。観測済みの CLFact があれば status まで合わせて返す。

    **観測した issue を先に空で並べる** — 紐づきを観測して 0 件だった issue が、まだ観測して
    いない issue と同じ「鍵が無い」に落ちると、読み手が両者を区別できない。
    """
    facts = {
        row["cl_ref"]: dict(row) for row in _rows(connection, "observed_cls", project_key)
    }
    by_issue = {
        row["issue_ref"]: []
        for row in _rows(connection, "observed_cl_link_scans", project_key)
    }
    for row in _rows(connection, "observed_cl_links", project_key):
        fact = facts.get(row["cl_ref"], {})
        by_issue.setdefault(row["issue_ref"], []).append(
            {
                "cl_ref": row["cl_ref"],
                "repo": row["repo"],
                "role": row["role"],
                "observed_at": row["observed_at"],
                # CL 本体を観測できていないときは status を作らない (`null` のまま出す)
                "status": fact.get("status"),
                "mergeable": fact.get("mergeable"),
                "unresolved_threads": fact.get("unresolved_threads"),
                "head_sha": fact.get("head_sha"),
            }
        )
    return by_issue


def _rows(connection, table, project_key):
    return connection.execute(f"SELECT * FROM {table} WHERE project_key = ?", (project_key,))
