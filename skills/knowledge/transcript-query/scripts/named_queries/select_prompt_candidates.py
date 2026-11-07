"""手入力 prompt を長さ・repo・正規形で絞り、読み順を確定した slice。

条件 `repo` は既定の `cwd` で実行中の dir の repo に絞り、`all` で全 repo を対象にし、それ以外の値は
repo の識別子の完全一致で絞る。

invariant:
1. totality — 全 prompt の数は、候補の数と打ち切りの数と除外の理由ごとの件数の和に一致する
2. 読み順 — text_chars の降順 → timestamp → session_id → uuid の全順序で、入力の順に依らない
3. 短文帯 — min_chars 未満は候補に出ず、除外の件数が meta に出る
4. 打ち切りを隠さない — limit が削った件数が meta に出る
5. 定型 — 正規形が完全一致する群を落とし、落とした群を件数つきで一覧に出す。検出の母数は全 repo で、
   除外の帰属は repo の範囲の側で数える。repo で絞る slice の一覧と件数は範囲の中の値だけを持つ
6. cross-repo — 定型の repo 数が rule の入力へ届き、全 repo を対象にした slice だけが評価する
7. repo の範囲 — `cwd` は他 repo の prompt を載せず (消えた cwd を祖先で解決した分は contract の注記のとおり)、
   解決できなければ全 repo へ倒さずに止まる
"""

from __future__ import annotations

import collections
import json
import os

from named_queries import _prompts
from named_query import NamedQuery, QueryRun, Stopped

BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("1-10", 1, 10),
    ("11-59", 11, 59),
    ("60-120", 60, 120),
    ("121-300", 121, 300),
    ("301+", 301, None),
)
# 実測で 3〜5 のどこに置いても結果が変わらない (分布が二峰性) ので、条件に出さない
BOILERPLATE_MIN_GROUP = 3
# 定型の一覧は人が読み返す第 2 の候補源なので、判別できる長さを残す
FORM_PREVIEW_CHARS = 200
PRIORITY_PATTERN = "correct"
# repo をまたぐ再出現と読む repo の数の下限
MIN_REPO_COUNT = 2

CROSS_REPO_RULE = {
    "rule_id": "cross_repo_recurrence",
    "unit_kind": "boilerplate_form",
    "scope": "repo_scope が all の slice の boilerplate_form の全件",
    "bucket_candidate": "cross-repo-norm-candidate",
    "conditions": [{"name": "multi_repo", "report_near_miss": True,
                    "predicate": f"正規化した後の distinct な repo の数が {MIN_REPO_COUNT} 以上 (repo をまたぐ再出現 = 汎用性の証拠)"}],
    "open_predicates": [
        {"name": "norm_readable",
         "predicate": "逐語反復された文から規範が読み取れるか。起動文・依頼のテンプレートは反復されるが規範ではない"},
        {"name": "paste_boundary",
         "predicate": "貼り付けの境界より前の人間の記述だけで規範が復元できるか (貼り戻された assistant の応答を規範として帰属させない)"},
        {"name": "container_classification",
         "predicate": "反映先の器が決まるか (器の実体と分類は消費者の手順が持つ)"},
    ],
    "thresholds": {"min_repo_count": MIN_REPO_COUNT},
}

CONTRACT = {
    "schema_version": 1,
    "rows": "section が meta (1 行)・boilerplate_form・rule_candidate・steering_patterns (1 行)・repo・candidate の行で、"
            "中身は body が持つ。返り値の rows は section を含む行数で、候補の数は meta の emitted",
    "rules": [CROSS_REPO_RULE],
    "notes": [
        "読み順 (rank) は text_chars の降順の全順序で、提示の順ではない",
        "候補の text は切り詰めない",
        "boilerplate_form は定型として除外した正規形の一覧で、逐語反復された規範がここに落ちるので、拾い戻しの候補源として読む。"
        "repo_count は正規化した後 (host/owner/name) の distinct で、解決できなかった repo は数えない。"
        "定型の検出の母数は全 repo で、repo で絞る slice の count・repos・repo_count・sample は範囲の中の値だけを持つ",
        "条件 repo の既定は実行中の dir の repo で、解決できないときは全 repo へ倒さずに止まる",
        "repo の値は origin の URL で、`://` の形の URL は user と認証情報を落とす。origin の無い repo は repo の root の絶対 path で、"
        "repo_count でも別の repo に数える。条件 repo には section repo の値をそのまま渡す。"
        "消えた cwd は実在する祖先で解決するので、祖先の repo の中で別の clone を使っていた prompt が混ざりうる",
        "母集団は mart と同じく main session の transcript だけ",
        "時刻の欠けた record は保守的に窓の中へ倒す。record は dedup しない",
    ],
}


def _repo_filter(run: QueryRun) -> tuple[str | None, str]:
    """絞り込みの repo と `repo_scope`。"""
    repo = run.params["repo"]
    if repo == "all":
        return None, "all"
    if repo != "cwd":
        return repo, "explicit"
    resolved = run.resolve_repo(os.getcwd())
    if resolved["repo"] is None:
        raise Stopped("bad_argument", f"実行中の dir の repo を解決できません ({resolved['cause']})。"
                                      "他 repo の prompt を黙って載せないので、--param repo に識別子か all を渡してください")
    return resolved["repo"], "cwd"


def _boilerplate_groups(accepted: list[dict], min_chars: int) -> dict[str, list[dict]]:
    """正規形が閾値の件数以上そろう群。母数は min_chars を通した全 repo の prompt。"""
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for row in accepted:
        if row["text_chars"] >= min_chars:
            groups[_prompts.normalize_text(row["text"])].append(row)
    return {form: rows for form, rows in groups.items() if len(rows) >= BOILERPLATE_MIN_GROUP}


def _distinct_repos(rows: list[dict]) -> list[str]:
    """解決できなかった repo を 1 repo に数えると、採用の gate が誤って通る。"""
    return sorted({normalized for row in rows if (normalized := _prompts.normalize_repo_identifier(row["repo"]))})


def _boilerplate_forms(groups: dict[str, list[dict]], excluded: collections.Counter,
                       repo_filter: str | None) -> list[dict]:
    """repo で絞る slice に他 repo の本文と件数を載せない。"""
    forms = []
    for form, rows in groups.items():
        scoped = [row for row in rows if repo_filter in (None, row["repo"])]
        if not scoped:
            continue
        repos = _distinct_repos(scoped)
        sample = scoped[0]
        forms.append({"normalized": form[:FORM_PREVIEW_CHARS].strip(), "count": len(scoped),
                      "excluded_from_slice": excluded[form], "repos": repos, "repo_count": len(repos),
                      "sample": {"session_id": sample["session_id"], "timestamp": sample["ts"], "repo": sample["repo"],
                                 "text": sample["text"][:FORM_PREVIEW_CHARS].strip()}})
    forms.sort(key=lambda entry: (-entry["count"], entry["normalized"]))
    return forms


def _rule_candidates(run: QueryRun, forms: list[dict], repo_scope: str) -> list[dict]:
    run.con.execute("CREATE TEMP TABLE prompt_form (unit VARCHAR, repo_count BIGINT, repos VARCHAR[], occurrences BIGINT)")
    if forms:
        run.con.executemany("INSERT INTO prompt_form VALUES (?, ?, ?, ?)",
                            [[form["normalized"], form["repo_count"], form["repos"], form["count"]] for form in forms])
    verdicts = run.select("SELECT * FROM lake_rule_cross_repo('prompt_form', $scope, $min_repo_count)",
                          scope=repo_scope, min_repo_count=MIN_REPO_COUNT)
    for verdict in verdicts:
        verdict["rule_inputs"] = json.loads(verdict["rule_inputs"])
        verdict["near_misses"] = [{**miss, "inputs": json.loads(miss["inputs"])} for miss in verdict["near_misses"]]
    return verdicts


def _band_histogram(accepted: list[dict], min_chars: int) -> list[dict]:
    """帯ごとの件数。repo で絞る前の母数で数える。"""
    return [{"band": label, "in_scope": upper is None or upper >= min_chars,
             "count": sum(1 for row in accepted
                          if row["text_chars"] >= lower and (upper is None or row["text_chars"] <= upper))}
            for label, lower, upper in BANDS]


def _steering_patterns(candidates: list[dict]) -> dict:
    """発話型の内訳と、訂正を先に読むための優先帯の索引。優先帯は rank を動かさない。"""
    histogram = collections.Counter(candidate["steering_pattern"] for candidate in candidates)
    priority = [c["rank"] for c in candidates if c["steering_pattern"] == PRIORITY_PATTERN]
    rest = [c["rank"] for c in candidates if c["steering_pattern"] != PRIORITY_PATTERN]
    return {"priority_pattern": PRIORITY_PATTERN, "histogram": dict(sorted(histogram.items())),
            "priority_order": priority + rest,
            "definition": f"{PRIORITY_PATTERN} を先頭帯に、帯内は rank の昇順 (rank = text_chars の降順の読み順。優先帯は rank を変えない)"}


def _repo_index(candidates: list[dict]) -> list[dict]:
    grouped: dict[str | None, list[dict]] = collections.defaultdict(list)
    for candidate in candidates:
        grouped[candidate["repo"]].append(candidate)
    index = [{"repo": repo, "candidate_count": len(items), "session_count": len({item["session_id"] for item in items})}
             for repo, items in grouped.items()]
    index.sort(key=lambda entry: (-entry["candidate_count"], str(entry["repo"])))
    return index


def _build(run: QueryRun) -> list[dict]:
    min_chars = _prompts.int_param(run, "min_chars")
    limit = _prompts.int_param(run, "limit") if run.params["limit"] else None
    repo_filter, repo_scope = _repo_filter(run)

    accepted, _verdicts = _prompts.read_prompts(run)
    groups = _boilerplate_groups(accepted, min_chars)
    form_of = {id(row): form for form, rows in groups.items() for row in rows}

    excluded: collections.Counter = collections.Counter()
    boilerplate_excluded: collections.Counter = collections.Counter()
    matched = []
    for row in accepted:
        if row["text_chars"] < min_chars:
            excluded["below_min_chars"] += 1
        elif repo_filter is not None and row["repo"] != repo_filter:
            excluded["other_repo"] += 1
        elif id(row) in form_of:
            excluded["boilerplate"] += 1
            boilerplate_excluded[form_of[id(row)]] += 1
        else:
            matched.append(row)

    matched.sort(key=lambda row: (-row["text_chars"], row["ts"], row["session_id"], row["record_uuid"]))
    candidates = [{"rank": rank, "session_id": row["session_id"], "uuid": row["record_uuid"], "timestamp": row["ts"],
                   "repo": row["repo"], "git_branch": row["git_branch"], "text_chars": row["text_chars"],
                   "steering_pattern": row["steering_pattern"], "text": row["text"]}
                  for rank, row in enumerate(matched[:limit], start=1)]

    forms = _boilerplate_forms(groups, boilerplate_excluded, repo_filter)
    meta = {
        **_prompts.window_meta(run.window),
        "min_chars": min_chars,
        "repo_filter": repo_filter,
        "repo_scope": repo_scope,
        "limit": limit,
        "total_prompts": len(accepted),
        "selected": len(matched),
        "emitted": len(candidates),
        "truncated_by_limit": len(matched) - len(candidates),
        "excluded": {reason: excluded[reason] for reason in ("below_min_chars", "other_repo", "boilerplate")},
        "band_histogram": _band_histogram(accepted, min_chars),
        "boilerplate": {"min_group": BOILERPLATE_MIN_GROUP,
                        "detection_scope": "min_chars を通した全 repo の採用 prompt",
                        "detected_forms": len(forms), "detected_prompts": sum(form["count"] for form in forms)},
        "read_order": "text_chars の降順 → timestamp → session_id → uuid (全順序で再現できる)",
    }
    return [_prompts.section("meta", meta),
            *(_prompts.section("boilerplate_form", form) for form in forms),
            *(_prompts.section("rule_candidate", verdict) for verdict in _rule_candidates(run, forms, repo_scope)),
            _prompts.section("steering_patterns", _steering_patterns(candidates)),
            *(_prompts.section("repo", entry) for entry in _repo_index(candidates)),
            *(_prompts.section("candidate", candidate) for candidate in candidates)]


QUERY = NamedQuery(contract=CONTRACT, params={"repo": "cwd", "min_chars": "60", "limit": ""}, build=_build)
