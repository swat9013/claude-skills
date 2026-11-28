"""人間が手入力した prompt だけの mart。全 project を横断し、repo での絞り込みは持たない。

invariant:
1. totality — 走査した user record の数は、採用の数と除外の理由ごとの件数の和に一致する
2. 除外 — `EXCLUSION_REASONS` の各理由に落ち、人間の手入力だけが残る
3. anchor — 採用した prompt は session_id・repo・timestamp の key を持ち、解決できない repo は null で repo_resolution に数える
4. 母集団 — main の transcript だけを読む
5. 観測窓 — 窓の外の record を走査しない
"""

from __future__ import annotations

import collections

from named_queries import _prompts
from named_query import NamedQuery, QueryRun

CONTRACT = {
    "schema_version": 1,
    "rows": "section が meta (1 行)・repo・prompt の行で、中身は body が持つ。返り値の rows は section を含む行数で、prompt の数は meta の total_prompts",
    "notes": [
        "mart は全 project 横断で作る。repo での絞り込みは select-prompt-candidates の領分",
        "meta の excluded.no_prompt_source が急増していたら、CLI の schema の変更で観測が劣化した疑いがある"
        " (黙った 0 件にはならない)。急増の判定には前回の mart が要る",
        "母集団は main session の transcript だけ。subagent の transcript は走査せず、scanned_user_records にも"
        " excluded にも入らない。excluded.sidechain は main の transcript の中で sidechain と印された user record のうち、"
        "excluded の key の順で sidechain より前の理由に当たらなかったものの件数",
        "時刻の欠けた record は保守的に窓の中へ倒すので、返り値の degradation.ts_missing_records は"
        "窓の外の prompt が混ざりうる上限として読む",
        "record は dedup しない。session の再開で写った record は出現回数と定型の件数を水増ししうる",
        "prompt の text は text_limit の字数で切り、切れたことを truncated に残す。slice の候補は切らない",
    ],
}


def _repo_section(prompts: list[dict]) -> list[dict]:
    """repo の索引。解決できなかった分は repo が null の 1 行にまとめ、件数を隠さない。"""
    grouped: dict[str | None, list[dict]] = collections.defaultdict(list)
    for prompt in prompts:
        grouped[prompt["repo"]].append(prompt)
    index = [{"repo": repo, "prompt_count": len(items),
              "session_count": len({item["session_id"] for item in items}),
              "first_timestamp": min(item["ts"] for item in items),
              "last_timestamp": max(item["ts"] for item in items)}
             for repo, items in grouped.items()]
    index.sort(key=lambda entry: (-entry["prompt_count"], str(entry["repo"])))
    return index


def _prompt_body(row: dict) -> dict:
    return {"session_id": row["session_id"], "uuid": row["record_uuid"], "timestamp": row["ts"], "cwd": row["cwd"],
            "repo": row["repo"], "repo_source": row["repo_source"], "project_dir": row["project_dir"],
            "git_branch": row["git_branch"], "prompt_source": row["prompt_source"], "cli_version": row["cli_version"],
            "text": row["text"][:_prompts.MART_TEXT_LIMIT].strip(), "text_chars": row["text_chars"],
            "truncated": row["text_chars"] > _prompts.MART_TEXT_LIMIT, "steering_pattern": row["steering_pattern"]}


def _build(run: QueryRun) -> list[dict]:
    accepted, verdicts = _prompts.read_prompts(run)
    by_source = collections.Counter(row["repo_source"] for row in accepted if row["repo_source"])
    resolved = sum(by_source.values())
    meta = {
        **_prompts.window_meta(run.window),
        "text_limit": _prompts.MART_TEXT_LIMIT,
        "total_prompts": len(accepted),
        "distinct_sessions": len({row["session_id"] for row in accepted}),
        "distinct_repos": len({row["repo"] for row in accepted if row["repo"]}),
        "scanned_user_records": sum(verdicts.values()),
        "excluded": {reason: verdicts[reason] for reason in _prompts.EXCLUSION_REASONS},
        "steering_patterns": {pattern: sum(1 for row in accepted if row["steering_pattern"] == pattern)
                              for pattern in _prompts.STEERING_PATTERNS},
        "repo_resolution": {"resolved": resolved, "unresolved": len(accepted) - resolved,
                            "by_source": {source: by_source[source] for source in ("cwd", "ancestor")}},
    }
    return [_prompts.section("meta", meta),
            *(_prompts.section("repo", entry) for entry in _repo_section(accepted)),
            *(_prompts.section("prompt", _prompt_body(row)) for row in accepted)]


QUERY = NamedQuery(contract=CONTRACT, build=_build)
