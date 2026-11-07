"""名指しした skill の実際の呼出しを、呼出しごとの位置つきで引く slice。

監査の単位は main session で、窓で呼出しを絞らない — 監査するのは最後に呼ばれた n 件で、期間ではない。
呼出しの判定は view が record の構造で持つ。ここが持つのは、名前の照合と読む順だけ。
"""

from __future__ import annotations

import datetime as dt
import os

from named_query import NamedQuery, QueryRun, Stopped

ARGS_TEXT_LIMIT = 300
CHANNELS = ("skill_tool", "command")
EXCLUSION_REASONS = ("queue_operation", "local_command_echo", "wrapper_embedded", "quoted_marker")

CONTRACT = {
    "rows": "読む順の transcript。scope (repo か cwd) ごとの最新 1 件を先に取り、残りを file の mtime の新しい順で埋める。"
            "環境差を確保する順なので、上から使う",
    "columns": {
        "path": "transcript の絶対 path",
        "session_id": "file の最後の session id",
        "cwd": "file の最後の cwd",
        "repo": "cwd の repo。解決できない (cwd が消えた・repo でない・git が動かない) と null",
        "mtime": "file の mtime (UTC の ISO 8601)。走査の後に file が消えると空",
        "invocation_count": "この file の呼出し回数",
        "channels": "経路ごとの呼出し回数 (skill_tool / command)",
        "invocations": "file の行の順の、呼出しごとの timestamp・uuid・channel・args・is_sidechain。"
                       "is_sidechain が真なら subagent 側からの呼出し",
    },
    "meta": {
        "total_invocations": "呼出し回数",
        "matched_files": "呼び出された transcript の file の数。呼出し回数ではない",
        "emitted_files": "rows の件数",
        "distinct_scopes": "呼び出された scope の数",
        "excluded": "記述はあるが呼出しではない record の理由別の件数。呼出し回数に入らず、逸脱の証拠でもない。"
                    "呼ばれ方を問わず数える",
        "other_invoked_name_calls": "invoked_name と呼ばれ方が違って外した呼出しの数",
    },
    "notes": [
        "invoked_name を渡さなければ、接頭辞を問わず同名の別 skill の呼出しも数える。渡すと matched_files・emitted_files・"
        "total_invocations・distinct_scopes と rows はその呼ばれ方の呼出しだけで決まる",
        "観測窓は形式の照合の範囲で、呼出しの絞り込みには使わない",
    ],
}


def _bare_name(skill: str) -> str:
    return skill.split(":")[-1].strip()


def _limit(text: str) -> int:
    try:
        limit = int(text)
    except ValueError as error:
        raise Stopped("bad_argument", "limit は 1 以上の整数で渡してください") from error
    if limit < 1:
        raise Stopped("bad_argument", "limit は 1 以上の整数で渡してください")
    return limit


def _read_order(hits: list[dict], limit: int) -> list[dict]:
    """scope ごとの最新 1 件を先に取り、残りを新しい順で埋める。1 環境の連続呼出しで枠を埋めない。"""
    ordered = sorted(hits, key=lambda hit: (hit["mtime"], hit["path"]), reverse=True)
    firsts: dict[str, dict] = {}
    for hit in ordered:
        firsts.setdefault(hit["scope"], hit)
    taken = {hit["path"] for hit in firsts.values()}
    return [*firsts.values(), *(hit for hit in ordered if hit["path"] not in taken)][:limit]


def _mtime(path: str) -> str:
    """走査の後に消えた file は mtime を持たないので空にする。呼出しは読めている。"""
    try:
        return dt.datetime.fromtimestamp(os.stat(path).st_mtime, dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except OSError:
        return ""


def _build(run: QueryRun) -> list[dict]:
    skill, invoked_name = run.params["skill"], run.params["invoked_name"].strip()
    bare = _bare_name(skill)
    if not bare:
        raise Stopped("bad_argument", "skill に skill の名前を渡してください")
    if invoked_name and _bare_name(invoked_name) != bare:
        raise Stopped("bad_argument", f"invoked_name {invoked_name!r} の skill 名が skill {skill!r} と違います。"
                                      "同じ skill の呼ばれ方 (<plugin>:<skill> か <skill>) を渡してください")
    limit = _limit(run.params["limit"])
    calls = sorted([*run.select("SELECT * FROM lake_skill_tool_call($bare)", bare=bare),
                    *run.select("SELECT * FROM lake_skill_slash_call($bare)", bare=bare)],
                   key=lambda call: call["line_seq"])
    matched = [call for call in calls if not invoked_name or call["called_name"].strip() == invoked_name]
    by_file: dict[str, list[dict]] = {}
    for call in matched:
        by_file.setdefault(call["path"], []).append(call)
    anchors = {row["path"]: row for row in run.select("SELECT * FROM lake_file_anchor($paths)", paths=sorted(by_file))} \
        if by_file else {}
    hits = []
    for path, invocations in by_file.items():
        anchor = anchors.get(path, {})
        cwd = anchor.get("cwd") or ""
        repo = run.resolve_repo(cwd)["repo"] if cwd else None
        hits.append({
            "path": path,
            "session_id": anchor.get("session_id") or "",
            "cwd": cwd,
            "repo": repo,
            "mtime": _mtime(path),
            "scope": repo or cwd or path,
            "invocation_count": len(invocations),
            "channels": {channel: sum(1 for call in invocations if call["channel"] == channel) for channel in CHANNELS},
            "invocations": [{"timestamp": call["ts"], "uuid": call["record_uuid"], "channel": call["channel"],
                             "args": (call["args"] or "").strip()[:ARGS_TEXT_LIMIT],
                             "is_sidechain": call["is_sidechain"]} for call in invocations],
        })
    selected = _read_order(hits, limit)
    excluded = {row["reason"]: row["hits"] for row in run.select(
        "SELECT reason, count(*) AS hits FROM lake_skill_marker_exclusion($bare) GROUP BY reason", bare=bare)}
    run.meta.update(
        skill=skill,
        invoked_name=invoked_name or None,
        matched_files=len(hits),
        emitted_files=len(selected),
        total_invocations=len(matched),
        distinct_scopes=len({hit["scope"] for hit in hits}),
        excluded={reason: excluded.get(reason, 0) for reason in EXCLUSION_REASONS},
        other_invoked_name_calls=len(calls) - len(matched),
    )
    return [{key: value for key, value in hit.items() if key != "scope"} for hit in selected]


QUERY = NamedQuery(contract=CONTRACT, params={"skill": None, "invoked_name": "", "limit": "3"}, build=_build)
