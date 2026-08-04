"""GhAdapter — TrackerPort の GitHub 実装 (`gh` CLI へ shell out)。

認証は `gh` に委譲する (spec §4.2)。本 module が持つのは GitHub 語彙 → 内部語彙の
写像だけで、判断は一切持たない。

endpoint と `--json` field の綴りは実機でしか誤りが露見しない (sandbox 内では
gh subprocess が動かない) ため、テスト側で argv を逐語 pin してある。既存
`dispatch_tracker.py` から移植した綴りは一字も変えていない。
"""

import tracker

# GitHub の PR 参照 2 経路。closing reference (`Closes #N`) だけでは `Refs #N` で
# 紐づけた PR を取り落とし、cross-reference だけでは UI の Development 欄で
# 紐づけた PR を取り落とすため、両方から集めて和集合を採る
ROLE_CLOSES = tracker.ROLE_CLOSES
ROLE_MENTION = tracker.ROLE_MENTION

# gh issue list に投げる state (中立語彙 → gh の綴り)
_LIST_STATE = {"open": "open", "closed": "closed", "all": "all"}

ISSUE_FIELDS = "number,title,state,labels,assignees,createdAt,updatedAt,url"
PR_FIELDS = "number,state,mergeable,title,url"
PR_LIST_FIELDS = "number,state,mergeable,title,url,headRefName,closingIssuesReferences"


class GhAdapter(tracker.TrackerPort):
    tracker = "gh"

    # --- issue 観測 ------------------------------------------------------------

    def fetch_issues(self, state, limit):
        raw = tracker.run_json(
            [
                "gh", "issue", "list",
                "--state", _LIST_STATE[state],
                "--limit", str(limit),
                "--json", ISSUE_FIELDS,
            ]
        )
        return [normalize_issue(item) for item in raw]

    def fetch_issue(self, number):
        raw = tracker.run_json(
            ["gh", "issue", "view", str(number), "--json", ISSUE_FIELDS]
        )
        return normalize_issue(raw)

    def fetch_blocked(self, number):
        """open blocker 検査。dependencies API を第一正、無い repo は body 行を fallback。

        `issue_dependencies_summary` field の有無で分岐するのが要点 — field が欠けた
        (= 機能が無い) ことと blocker が 0 件であることを混同しない。
        """
        issue = tracker.run_json(["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{number}"])
        summary = issue.get("issue_dependencies_summary")
        if isinstance(summary, dict) and "blocked_by" in summary:
            open_blockers = []
            if int(summary["blocked_by"] or 0) > 0:
                detail = tracker.run_json(
                    [
                        "gh", "api",
                        f"repos/{{owner}}/{{repo}}/issues/{number}/dependencies/blocked_by",
                    ]
                )
                open_blockers = [
                    {"number": item["number"], "title": item.get("title", "")}
                    for item in detail
                    if item.get("state") == "open"
                ]
            return _blocked_result(open_blockers, "api")
        # dependencies 機能が無い repo のみここに来る: body 行の参照先 state を個別解決
        open_blockers = []
        for ref in tracker.parse_blocked_refs(issue.get("body")):
            dep = tracker.run_json(["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{ref}"])
            if dep.get("state") == "open":
                open_blockers.append({"number": ref, "title": dep.get("title", "")})
        return _blocked_result(open_blockers, "body")

    # --- PR 観測 ---------------------------------------------------------------

    def repo_scope(self):
        return tracker.run_json(["gh", "repo", "view", "--json", "nameWithOwner"])[
            "nameWithOwner"
        ]

    def linked_prs(self, number, scope):
        """issue → {PR 番号: role}。別 repo (fork) からの言及は slug 一致で落とす。"""
        links = {}
        closing = tracker.run_json(
            ["gh", "issue", "view", str(number), "--json", "closedByPullRequestsReferences"]
        )
        for ref in closing.get("closedByPullRequestsReferences") or []:
            repo = ref.get("repository") or {}
            owner = (repo.get("owner") or {}).get("login")
            if f"{owner}/{repo.get('name')}" == scope:
                links[ref["number"]] = ROLE_CLOSES
        timeline = tracker.run_json(
            ["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{number}/timeline", "--paginate"]
        )
        for event in timeline:
            if event.get("event") != "cross-referenced":
                continue
            source = (event.get("source") or {}).get("issue") or {}
            if not source.get("pull_request"):
                continue
            if (source.get("repository") or {}).get("full_name") == scope:
                links.setdefault(source["number"], ROLE_MENTION)
        return links

    def pr_detail(self, number, role):
        raw = tracker.run_json(
            ["gh", "pr", "view", str(number), "--json", PR_FIELDS]
        )
        return {**normalize_pr(raw), "role": role}

    def open_prs(self, scope, limit):
        """repo の open PR。role は issue 文脈が無いので付けない (None)。

        `--limit` は必須 — 省くと gh の既定 30 件で黙って切れる。
        """
        raw = tracker.run_json(
            [
                "gh", "pr", "list",
                "--state", "open",
                "--limit", str(limit),
                "--json", PR_LIST_FIELDS,
            ]
        )
        return [
            {
                **normalize_pr(item),
                "role": None,
                "head_branch": item.get("headRefName") or "",
                "closes_issues": [
                    ref["number"] for ref in item.get("closingIssuesReferences") or []
                ],
            }
            for item in raw
        ]

    # --- 操作 -------------------------------------------------------------------

    def set_assignee(self, number, action):
        flag = "--add-assignee" if action == "claim" else "--remove-assignee"
        tracker.run_checked(["gh", "issue", "edit", str(number), flag, "@me"])

    def post_comment(self, number, body):
        tracker.run_checked(["gh", "issue", "comment", str(number), "--body", body])

    def edit_labels(self, number, add, remove):
        """`gh issue edit` は 1 回で付け外しを両方受けるので呼び出しは常に 1 回。"""
        argv = ["gh", "issue", "edit", str(number)]
        for name in add:
            argv += ["--add-label", name]
        for name in remove:
            argv += ["--remove-label", name]
        tracker.run_checked(argv)
        return 1


# --- 正規化 -------------------------------------------------------------------------


def normalize_issue(raw):
    """gh issue → 内部語彙。state の未知値は TrackerError (推測に倒さない)。"""
    return {
        "number": raw["number"],
        "title": raw.get("title", ""),
        "state": tracker.normalize_state(raw.get("state")),
        "labels": tracker.label_names(raw.get("labels")),
        "assignees": [a["login"] for a in (raw.get("assignees") or [])],
        "created_at": tracker.normalize_timestamp(raw.get("createdAt")),
        "updated_at": tracker.normalize_timestamp(raw.get("updatedAt")),
        "url": raw.get("url", ""),
    }


def normalize_pr(raw):
    return {
        "number": raw["number"],
        "state": tracker.normalize_pr_state(raw.get("state")),
        "mergeable": tracker.normalize_gh_mergeable(raw.get("mergeable")),
        "title": raw.get("title", ""),
        "url": raw.get("url", ""),
    }


def _blocked_result(open_blockers, source):
    return {"blocked": bool(open_blockers), "open_blockers": open_blockers, "source": source}
