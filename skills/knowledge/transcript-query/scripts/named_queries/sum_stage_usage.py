"""名指しした transcript 1 件の、工程ごとの所要・subagent 数・token の mart。

工程の区切りは決めない。区切りは呼び出し元が transcript を読んで決めて渡し、ここが持つのは境界の内側の算術だけ。

- token は input + output + cache_creation で、cache_read は足さずに別掲する (同じ context を turn ごとに再計上する)
- 応答の最終の usage を、その応答の最初の record がある工程に帰属させる
- subagent は工程の中の起動に対応する体を数え、入れ子の体は親に畳む。subagent 数に入れ子は入れない
- workflow の体は、run を指す最初の起動のある工程に帰属させる
- 対応が取れない値は 0 にせず null と理由で返す (0 だと消費が無かった工程と区別できない)
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from named_query import ALL_POPULATION, NamedQuery, QueryRun, Stopped

COUNTED_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens")
UNMATCHED_REASON = ("起動した tool_use に対応する agent の file か meta が見つからないか、1 つに決まらない"
                    " (workflow は run の dir か体の transcript か meta。agent id・tool_use id の重なりを含む)")

CONTRACT = {
    "rows": "渡した順の工程。全行が同じ key を持ち、持たない値は null",
    "columns": {
        "stage": "渡した工程名",
        "from_uuid": "渡した最初の record",
        "to_uuid": "渡した最後の record",
        "error": "境界を数えられない理由。値があれば、その工程の他の値は全部 null",
        "duration_seconds": "境界の 2 record の時刻の差 (秒)。subagent に委ねた分はこの間に含まれる",
        "duration_reason": "duration_seconds が null の理由。どちらの境界の時刻が欠けているか解釈できないか",
        "tokens": "main 側の input + output + cache_creation",
        "cache_read_input_tokens": "main 側の cache read。tokens に足さない",
        "subagents": "count (起動した体の数)・tokens・cache_read_input_tokens・unmatched_tool_use_ids・reason。"
                     "対応が取れないと tokens と cache_read_input_tokens が null で、reason に理由が入る。"
                     "tokens はこの工程の tokens に足さず別に読む",
    },
    "meta": {
        "stage_errors": "error を持つ工程の数",
        "unresolved_agent_ids": "起動元に辿れない subagent。どの工程の値にも入っていない",
    },
    "notes": [
        "degradation の件数は、この transcript と session dir の下の file の値。unrecognized_nested_files が 0 でなければ"
        "数えから外れた subagent がありうる",
        "観測窓は形式の照合の範囲で、工程の絞り込みには使わない",
        "absent_optional_paths の subagent の meta の path は、この session の meta で見る。subagent を起こしていなければ並ぶ",
    ],
}


@dataclasses.dataclass(frozen=True)
class Body:
    """subagent 1 体。"""

    agent_id: str
    path: str
    run_id: str | None
    has_transcript: bool
    meta_readable: bool
    parent_agent_id: str | None
    launch_tool_use_id: str | None


@dataclasses.dataclass(frozen=True)
class Session:
    """工程を数えるのに要る、1 transcript の事実。"""

    positions: dict[str, int]
    epochs: dict[int, float | None]
    usage: list[dict]
    launches: list[dict]
    first_launch_runs: dict[str, str]
    bodies: list[Body]
    roots: dict[str, str | None]
    run_dirs: set[str]
    subagent_usage: dict[str, list[dict]]


def _stages(text: str) -> list[dict]:
    try:
        stages = json.loads(text)
    except ValueError as error:
        raise Stopped("bad_argument", "stages は JSON の配列で渡してください") from error
    if not isinstance(stages, list) or not all(
            isinstance(stage, dict) and "stage" in stage
            and isinstance(stage.get("from_uuid"), str) and isinstance(stage.get("to_uuid"), str) for stage in stages):
        raise Stopped("bad_argument", "stages は {stage, from_uuid, to_uuid} の object の配列で渡してください")
    return stages


def _sum_usage(usages: list[dict]) -> dict[str, int]:
    return {"tokens": sum(usage[key] for usage in usages for key in COUNTED_KEYS),
            "cache_read_input_tokens": sum(usage["cache_read_input_tokens"] for usage in usages)}


def _resolve_roots(bodies: list[Body]) -> dict[str, str | None]:
    """agent id → 起動元の top-level の agent id。辿れない体は None。

    meta が無い・読めない体、親の meta が無い体、親の連鎖が循環する体、辿り着いた先が起動を持たない体は、
    どの起動に属するかが決まらない。
    agent id が重なる体は親の連鎖も起動も 1 つに決まらない。
    """
    by_id: dict[str, Body] = {}
    duplicated = set()
    for body in bodies:
        if body.agent_id in by_id:
            duplicated.add(body.agent_id)
        by_id[body.agent_id] = body
    roots: dict[str, str | None] = {}
    for body in bodies:
        current: Body | None = body
        seen: set[str] = set()
        while current is not None and current.meta_readable and current.parent_agent_id:
            if current.agent_id in seen:
                current = None
                break
            seen.add(current.agent_id)
            current = by_id.get(current.parent_agent_id)
        reached_launch = current is not None and current.meta_readable and current.launch_tool_use_id
        resolved = reached_launch and not (seen | {body.agent_id, current.agent_id}) & duplicated
        roots[body.agent_id] = current.agent_id if resolved else None
    return roots


def _sum_subagents(launch_ids: list[str], workflow_runs: dict[str, str], session: Session) -> dict:
    """工程の中の起動 (`Task` / `Agent` の id と、run を指す最初の `Workflow` の id → run の id) に対応する体を数える。"""
    result: dict[str, Any] = {"count": len(launch_ids), "tokens": None, "cache_read_input_tokens": None,
                              "unmatched_tool_use_ids": [], "reason": None}
    if not launch_ids and not workflow_runs:
        return {**result, "tokens": 0, "cache_read_input_tokens": 0}
    bodies = session.bodies
    by_tool_use: dict[str, list[Body]] = {}
    for body in bodies:
        if body.meta_readable and not body.parent_agent_id and body.launch_tool_use_id:
            by_tool_use.setdefault(body.launch_tool_use_id, []).append(body)
    # 起動に対応した体の agent id → 起動の id。agent id が重なる体は複数の起動に対応しうる
    launch_of: dict[str, set[str]] = {}
    for launch_id in launch_ids:
        found = by_tool_use.get(launch_id, [])
        for body in found:
            launch_of.setdefault(body.agent_id, set()).add(launch_id)
        # 対応する体が無いか、複数あってどれがその起動の体かが決まらない
        if len(found) != 1:
            result["unmatched_tool_use_ids"].append(launch_id)
    for launch_id, run_id in workflow_runs.items():
        # run の体は path から決まるので、meta の読めない体も入れる (下で起動ごと null にする)
        run_bodies = [body for body in bodies if body.run_id == run_id and not body.parent_agent_id]
        if not run_bodies and run_id not in session.run_dirs:
            result["unmatched_tool_use_ids"].append(launch_id)
        for body in run_bodies:
            launch_of.setdefault(body.agent_id, set()).add(launch_id)
        # meta の読めない体は unresolved_agent_ids の側で出す (数に足すと二重に数えられる)
        result["count"] += sum(1 for body in run_bodies if body.meta_readable)
    roots = session.roots
    # 起動に対応したのに起動元へ解決できない体と、起動に属するのに transcript の無い体は数えられない
    unreadable = {launch_id for agent_id, ids in launch_of.items() if roots[agent_id] is None for launch_id in ids}
    unreadable |= {launch_id for body in bodies if roots[body.agent_id] in launch_of and not body.has_transcript
                   for launch_id in launch_of[roots[body.agent_id]]}
    result["unmatched_tool_use_ids"] += sorted(unreadable - set(result["unmatched_tool_use_ids"]))
    if result["unmatched_tool_use_ids"]:
        return {**result, "reason": UNMATCHED_REASON}
    # 応答の途中までが別の file へ写るので、id ごとに書き終えた値 (output が最大の値) だけを数える
    final: dict[str, dict] = {}
    anonymous: list[dict] = []
    for body in bodies:
        if roots[body.agent_id] in launch_of:
            for usage in session.subagent_usage.get(body.path, []):
                if usage["message_id"] is None:
                    anonymous.append(usage)
                elif usage["output_tokens"] >= final.get(usage["message_id"], usage)["output_tokens"]:
                    final[usage["message_id"]] = usage
    return {**result, **_sum_usage([*final.values(), *anonymous])}


def _duration(first: float | None, last: float | None) -> tuple[float | None, str | None]:
    if first is not None and last is not None:
        return round(last - first, 3), None
    missing = "最初と最後" if first is None and last is None else "最初" if first is None else "最後"
    return None, f"{missing}の境界の record の時刻が欠けているか解釈できない"


def _summarize(stage: dict, session: Session) -> dict:
    out: dict[str, Any] = {"stage": stage["stage"], "from_uuid": stage["from_uuid"], "to_uuid": stage["to_uuid"],
                           "error": None, "duration_seconds": None, "duration_reason": None, "tokens": None,
                           "cache_read_input_tokens": None, "subagents": None}
    start, end = session.positions.get(stage["from_uuid"]), session.positions.get(stage["to_uuid"])
    missing = [key for key, position in (("from_uuid", start), ("to_uuid", end)) if position is None]
    if missing:
        return {**out, "error": f"transcript に無い uuid: {', '.join(missing)}"}
    if start > end:
        return {**out, "error": "from_uuid が to_uuid より後ろにある"}
    duration, reason = _duration(session.epochs[start], session.epochs[end])
    inside = [launch for launch in session.launches if start <= launch["line_seq"] <= end]
    launch_ids = list(dict.fromkeys(launch["tool_use_id"] for launch in inside if launch["launch_kind"] == "subagent"))
    workflow_runs = {launch["tool_use_id"]: session.first_launch_runs[launch["tool_use_id"]] for launch in inside
                     if launch["tool_use_id"] in session.first_launch_runs}
    return {**out, "duration_seconds": duration, "duration_reason": reason,
            **_sum_usage([usage for usage in session.usage if start <= usage["first_line_seq"] <= end]),
            "subagents": _sum_subagents(launch_ids, workflow_runs, session)}


def _session(run: QueryRun, transcript: str) -> Session:
    positions: dict[str, int] = {}
    epochs: dict[int, float | None] = {}
    for record in run.select("SELECT line_seq, record_uuid, ts_epoch FROM lake_record WHERE path = $main ORDER BY line_seq",
                             main=transcript):
        if record["record_uuid"]:
            positions.setdefault(record["record_uuid"], record["line_seq"])
        epochs[record["line_seq"]] = record["ts_epoch"]
    usage_by_path: dict[str, list[dict]] = {}
    for usage in run.select("SELECT * FROM lake_file_usage ORDER BY path, line_seq"):
        usage_by_path.setdefault(usage["path"], []).append(usage)
    launches = run.select(
        """SELECT tool_use_id, launch_kind, line_seq FROM lake_tool_use
           WHERE path = $main AND launch_kind IS NOT NULL AND tool_use_id <> '' ORDER BY line_seq, block_no""",
        main=transcript)
    # run の id は結果の record にあり、工程の境界の外に出うるので file 全体から引く。
    # 結果が run の id を持たない Workflow (拒否・失敗) は run を起こしていないので入らない
    run_ids = {row["tool_use_id"]: row["workflow_run_id"] for row in run.select(
        "SELECT tool_use_id, workflow_run_id FROM lake_workflow_run WHERE path = $main ORDER BY line_seq",
        main=transcript)}
    first_launch_runs: dict[str, str] = {}
    for launch in launches:
        launch_id = launch["tool_use_id"]
        if launch["launch_kind"] == "workflow" and launch_id in run_ids \
                and run_ids[launch_id] not in first_launch_runs.values():
            first_launch_runs[launch_id] = run_ids[launch_id]
    bodies = [Body(row["agent_id"], row["path"], row["workflow_run_id"], row["has_transcript"], row["meta_readable"],
                   row["parent_agent_id"], row["launch_tool_use_id"]) for row in run.select(
        # 同じ run を指す起動が重なると 1 体が複数行になる。run の体の工程は first_launch_runs が決める
        """SELECT path, agent_id, workflow_run_id, has_transcript, meta_readable, parent_agent_id,
                  min(launch_tool_use_id) AS launch_tool_use_id
           FROM lake_subagent_origin
           GROUP BY ALL ORDER BY path""")]
    run_dirs = {row["workflow_run_id"] for row in run.select(
        "SELECT DISTINCT workflow_run_id FROM lake_file WHERE nullif(workflow_run_id, '') IS NOT NULL")}
    return Session(positions, epochs, usage_by_path.get(transcript, []), launches, first_launch_runs,
                   bodies, _resolve_roots(bodies), run_dirs, usage_by_path)


def _build(run: QueryRun) -> list[dict]:
    stages = _stages(run.params["stages"])
    # 読む範囲は名指しした 1 session なので、main の file は 1 つ。渡された path は symlink を含みうる
    transcript = run.select("SELECT path FROM lake_file WHERE transcript_kind = 'main'")[0]["path"]
    session = _session(run, transcript)
    rows = [_summarize(stage, session) for stage in stages]
    run.meta.update(transcript=transcript, stages=len(rows), stage_errors=sum(1 for row in rows if row["error"]),
                    unresolved_agent_ids=sorted({agent_id for agent_id, root in session.roots.items() if root is None}))
    return rows


QUERY = NamedQuery(contract=CONTRACT, params={"transcript": None, "stages": None}, population=lambda _params: ALL_POPULATION,
                   build=_build, named_transcript="transcript")
