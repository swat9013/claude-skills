"""GlabAdapter — TrackerPort の GitLab 実装 (`glab` CLI へ shell out)。

GitHub との差を吸収する箇所が 3 つある:

- 番号は `iid` (project 内番号)。中立 ref の番号にはこちらを使う
- state は `opened` / `closed`。`tracker.normalize_state` が内部語彙へ揃える
- blocker は link 型 `is_blocked_by`。リンク型を運用していない instance では
  block 系 label を fallback にする (この経路は blocker の個別列挙ができない)

assignee 操作で username でなく user id を PUT するのは、username が instance 間で
衝突しうるのと CLI flag が version 依存なため。

glab は実機照合できないので、endpoint の綴りはテスト側の argv pin だけが記録になる。
既存 `dispatch_tracker.py` から移植した綴りは一字も変えていない。
"""

import tracker

ROLE_CLOSES = tracker.ROLE_CLOSES
ROLE_MENTION = tracker.ROLE_MENTION

# `is_blocked_by` リンク型が運用されていない instance の fallback
BLOCK_FALLBACK_LABELS = frozenset({"wayfinder:blocked"})

# glab の per-page 上限。これ超えの issue 運用は page ループ化が要る (未対応)
MAX_PER_PAGE = 100

# 中立 state → glab issue list の追加 flag。既定 (flag なし) が opened
_LIST_STATE_FLAG = {"open": [], "closed": ["--closed"], "all": ["--all"]}


class GlabAdapter(tracker.TrackerPort):
    tracker = "glab"
    # 1 page で取り切る前提 (page ループは未対応)。port はこの値で truncated を判定する
    max_fetch = MAX_PER_PAGE

    # --- issue 観測 ------------------------------------------------------------

    def fetch_issues(self, state, limit):
        argv = [
            "glab", "issue", "list", "--output", "json",
            "--per-page", str(limit),
            *_LIST_STATE_FLAG[state],
        ]
        return [normalize_issue(item) for item in tracker.run_json(argv)]

    def fetch_issue(self, number):
        raw = tracker.run_json(["glab", "issue", "view", str(number), "--output", "json"])
        return normalize_issue(raw)

    def fetch_blocked(self, number):
        links = tracker.run_json(["glab", "api", f"projects/:id/issues/{number}/links"])
        is_blocked_by = [
            link for link in links if link.get("link_type") == "is_blocked_by"
        ]
        if is_blocked_by:
            open_blockers = [
                {"number": link["iid"], "title": link.get("title", "")}
                for link in is_blocked_by
                if link.get("state") == "opened"
            ]
            return {
                "blocked": bool(open_blockers),
                "open_blockers": open_blockers,
                "source": "api",
            }
        # リンク型が運用されていない instance: block 系 label を fallback とする。
        # この経路では blocker を列挙できない (blocked だけが分かる)
        issue = tracker.run_json(["glab", "issue", "view", str(number), "--output", "json"])
        hit = sorted(set(tracker.label_names(issue.get("labels"))) & BLOCK_FALLBACK_LABELS)
        return {"blocked": bool(hit), "open_blockers": [], "source": "labels"}

    # --- MR 観測 ---------------------------------------------------------------

    def repo_scope(self):
        return tracker.run_json(["glab", "api", "projects/:id"])["id"]

    def linked_prs(self, number, scope):
        """issue → {MR iid: role}。gh 側と同じく closed_by と関連 MR の和集合を採る。"""
        links = {}
        for mr in tracker.run_json(["glab", "api", f"projects/:id/issues/{number}/closed_by"]):
            if mr.get("project_id") == scope:
                links[mr["iid"]] = ROLE_CLOSES
        for mr in tracker.run_json(
            ["glab", "api", f"projects/:id/issues/{number}/related_merge_requests"]
        ):
            if mr.get("project_id") == scope:
                links.setdefault(mr["iid"], ROLE_MENTION)
        return links

    def pr_detail(self, number, role):
        raw = tracker.run_json(["glab", "api", f"projects/:id/merge_requests/{number}"])
        return {**normalize_mr(raw), "role": role}

    def open_prs(self, scope, limit):
        """project の open MR。

        closing issue は MR ごとに 1 回追加照会するので **CLI 起動は 1 + MR 件数** になる
        (open MR が 100 件なら 101 回)。件数が多い project では `limit` を絞って呼ぶ。
        """
        raw = tracker.run_json(
            ["glab", "api", f"projects/:id/merge_requests?state=opened&per_page={limit}"]
        )
        entries = []
        for item in raw:
            iid = item["iid"]
            closes = tracker.run_json(
                ["glab", "api", f"projects/:id/merge_requests/{iid}/closes_issues"]
            )
            entries.append(
                {
                    **normalize_mr(item),
                    "role": None,
                    "head_branch": item.get("source_branch") or "",
                    "closes_issues": [issue["iid"] for issue in closes],
                }
            )
        return entries

    # --- 操作 -------------------------------------------------------------------

    def set_assignee(self, number, action):
        uid = tracker.run_json(["glab", "api", "user"])["id"] if action == "claim" else 0
        tracker.run_json(
            [
                "glab", "api", f"projects/:id/issues/{number}",
                "-X", "PUT", "-F", f"assignee_ids={uid}",
            ]
        )

    def post_comment(self, number, body):
        tracker.run_checked(["glab", "issue", "note", str(number), "--message", body])

    def edit_labels(self, number, add, remove):
        """`glab issue update` は 1 回で `--label` / `--unlabel` を両方受ける。"""
        argv = ["glab", "issue", "update", str(number)]
        for name in add:
            argv += ["--label", name]
        for name in remove:
            argv += ["--unlabel", name]
        tracker.run_checked(argv)
        return 1


# --- 正規化 -------------------------------------------------------------------------


def normalize_issue(raw):
    return {
        "number": raw["iid"],
        "title": raw.get("title", ""),
        "state": tracker.normalize_state(raw.get("state")),
        "labels": tracker.label_names(raw.get("labels")),
        "assignees": [a["username"] for a in (raw.get("assignees") or [])],
        "created_at": tracker.normalize_timestamp(raw.get("created_at")),
        "updated_at": tracker.normalize_timestamp(raw.get("updated_at")),
        "url": raw.get("web_url", ""),
    }


def normalize_mr(raw):
    return {
        "number": raw["iid"],
        "state": tracker.normalize_pr_state(raw.get("state")),
        "mergeable": tracker.normalize_glab_mergeable(raw),
        "title": raw.get("title", ""),
        "url": raw.get("web_url", ""),
    }
