"""GhAdapter — TrackerPort の GitHub 実装 (`gh` CLI へ shell out)。

認証は `gh` に委譲する (spec §4.2)。本 module が持つのは GitHub 語彙 → 内部語彙の
写像だけで、判断は一切持たない。

endpoint と `--json` field の綴りは実機でしか誤りが露見しない (sandbox 内では
gh subprocess が動かない) ため、テスト側で argv を逐語 pin してある。既存
`dispatch_tracker.py` から移植した綴りは一字も変えていない。

明示 repo scope は 2 経路に分かれる: `gh` の list / view 系は `--repo` を足すだけだが、
**`gh api` は `--repo` を持たない**ので `repos/{owner}/{repo}/...` の placeholder を
literal path へ置換する。置換し忘れると blocker 検査と timeline だけ別 repo を答え、
「観測は正しいのに blocker だけ他 repo」という最も高くつく取り違えになる。
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

# review thread の 1 回取得件数。取り切れなければ `truncated` で表に出す (黙って切ると
# 「未解決 0 件」に化ける)
REVIEW_THREAD_PAGE_SIZE = 100

# review thread の観測 (ADR 0039 が PR #617 で実測した経路)。`--json` を持つ CLI 経路が
# 無いので graphql を直に撃つ。**変数で渡して query 文字列は定数のまま**にするのは、
# 組み立てた文字列を pin しても綴りの記録にならないため
REVIEW_THREADS_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!) {"
    " repository(owner: $owner, name: $name) {"
    f" pullRequest(number: $number) {{ reviewThreads(first: {REVIEW_THREAD_PAGE_SIZE})"
    " { nodes { id isResolved } pageInfo { hasNextPage } } } } }"
)

# review thread を閉じる mutation。人間が再オープンできる可逆操作である前提で worker に
# 権限を持たせている (ADR 0039 の受け入れたコスト)
RESOLVE_REVIEW_THREAD_MUTATION = (
    "mutation($threadId: ID!) {"
    " resolveReviewThread(input: {threadId: $threadId}) { thread { id isResolved } } }"
)


class GhAdapter(tracker.TrackerPort):
    tracker = "gh"
    remote_hosts = ("github.com",)
    supports_review_threads = True

    # --- issue 観測 ------------------------------------------------------------

    def fetch_issues(self, state, limit, repo):
        raw = tracker.run_json(
            [
                "gh", "issue", "list",
                *repo_flag(repo),
                "--state", _LIST_STATE[state],
                "--limit", str(limit),
                "--json", ISSUE_FIELDS,
            ]
        )
        return [normalize_issue(item) for item in raw]

    def fetch_issue(self, number, repo):
        raw = tracker.run_json(
            ["gh", "issue", "view", str(number), *repo_flag(repo), "--json", ISSUE_FIELDS]
        )
        return normalize_issue(raw)

    def fetch_blocked(self, number, repo):
        """open blocker 検査。dependencies API を第一正、無い repo は body 行を fallback。

        `issue_dependencies_summary` field の有無で分岐するのが要点 — field が欠けた
        (= 機能が無い) ことと blocker が 0 件であることを混同しない。

        本 method の 3 endpoint は `gh api` (= `--repo` を持たない) なので、scope は
        path の literal 置換だけが担う。
        """
        scope = api_scope(repo)
        issue = tracker.run_json(["gh", "api", f"repos/{scope}/issues/{number}"])
        summary = issue.get("issue_dependencies_summary")
        if isinstance(summary, dict) and "blocked_by" in summary:
            open_blockers = []
            if int(summary["blocked_by"] or 0) > 0:
                detail = tracker.run_json(
                    ["gh", "api", f"repos/{scope}/issues/{number}/dependencies/blocked_by"]
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
            dep = tracker.run_json(["gh", "api", f"repos/{scope}/issues/{ref}"])
            if dep.get("state") == "open":
                open_blockers.append({"number": ref, "title": dep.get("title", "")})
        return _blocked_result(open_blockers, "body")

    # --- PR 観測 ---------------------------------------------------------------

    def linked_prs(self, number, repo):
        """issue → 紐づく PR の列。**repo で絞らず、PR ごとの所属 repo を載せて返す**。

        関連 repo の worker が出す PR は cross-repo の closing reference で紐づくので、
        自 repo 以外を落とすと正当な closes が mention へ落ちる (ADR 0036)。fork からの
        言及もそのまま載るので、どれを根拠に採るかは呼び出し側が `repo` を見て決める。
        """
        links = {}
        closing = tracker.run_json(
            [
                "gh", "issue", "view", str(number),
                *repo_flag(repo),
                "--json", "closedByPullRequestsReferences",
            ]
        )
        for ref in closing.get("closedByPullRequestsReferences") or []:
            links[(reference_repo(ref.get("repository")), ref["number"])] = ROLE_CLOSES
        timeline = tracker.run_json(
            ["gh", "api", f"repos/{api_scope(repo)}/issues/{number}/timeline", "--paginate"]
        )
        for event in timeline:
            if event.get("event") != "cross-referenced":
                continue
            source = (event.get("source") or {}).get("issue") or {}
            if not source.get("pull_request"):
                continue
            links.setdefault(
                (full_name(source.get("repository")), source["number"]), ROLE_MENTION
            )
        return [
            {"number": pr_number, "role": role, "repo": pr_repo}
            for (pr_repo, pr_number), role in links.items()
        ]

    def pr_detail(self, number, role, repo):
        """PR 1 件。`repo` は **その PR が居る repo** なので、issue と別 repo でも届く。"""
        raw = tracker.run_json(
            ["gh", "pr", "view", str(number), *repo_flag(repo), "--json", PR_FIELDS]
        )
        return {**normalize_pr(raw), "role": role, "repo": repo}

    def open_prs(self, limit, repo):
        """repo の open PR。role は issue 文脈が無いので付けない (None)。

        `--limit` は必須 — 省くと gh の既定 30 件で黙って切れる。repo 未指定のときは
        どの repo を見たのかが返り値から読めないので、`gh` が cwd から解いた slug を
        1 回問い合わせて載せる。
        """
        scope = repo or self.cwd_repo()
        raw = tracker.run_json(
            [
                "gh", "pr", "list",
                *repo_flag(repo),
                "--state", "open",
                "--limit", str(limit),
                "--json", PR_LIST_FIELDS,
            ]
        )
        return [
            {
                **normalize_pr(item),
                "role": None,
                "repo": scope,
                "head_branch": item.get("headRefName") or "",
                "closes_issues": [
                    ref["number"] for ref in item.get("closingIssuesReferences") or []
                ],
            }
            for item in raw
        ]

    # --- review thread 観測 / resolve (ADR 0039) ---------------------------------

    def fetch_review_threads(self, number, repo):
        """PR 1 件の review thread。`repo` は **その PR が居る repo** で cwd 推論へ倒れない。

        `gh api graphql` は `--repo` を持たず、owner / name を変数で渡すしかないので、
        識別子が無い呼び出しはここへ来る前に port が落とす (`_review_thread_target`)。
        """
        owner, name = split_repo(repo)
        raw = tracker.run_json(
            [
                "gh", "api", "graphql",
                "-f", f"query={REVIEW_THREADS_QUERY}",
                "-F", f"owner={owner}",
                "-F", f"name={name}",
                "-F", f"number={number}",
            ]
        )
        return normalize_review_threads(raw, number, repo)

    def resolve_review_thread(self, thread_id):
        raw = tracker.run_json(
            [
                "gh", "api", "graphql",
                "-f", f"query={RESOLVE_REVIEW_THREAD_MUTATION}",
                "-F", f"threadId={thread_id}",
            ]
        )
        thread = (
            ((raw.get("data") or {}).get("resolveReviewThread") or {}).get("thread") or {}
        )
        if not thread.get("id"):
            raise tracker.TrackerError(
                f"review thread {thread_id} の resolve 応答を読めない: {raw!r}"
            )
        return {"id": thread["id"], "resolved": bool(thread.get("isResolved"))}

    def cwd_repo(self):
        """`gh` が cwd から解決する repo slug (repo 未指定の観測に印を付けるため)。"""
        return tracker.run_json(["gh", "repo", "view", "--json", "nameWithOwner"])[
            "nameWithOwner"
        ]

    # --- 操作 -------------------------------------------------------------------

    def set_assignee(self, number, action, repo):
        flag = "--add-assignee" if action == "claim" else "--remove-assignee"
        tracker.run_checked(
            ["gh", "issue", "edit", str(number), *repo_flag(repo), flag, "@me"]
        )

    def post_comment(self, number, body, repo):
        tracker.run_checked(
            ["gh", "issue", "comment", str(number), *repo_flag(repo), "--body", body]
        )

    def edit_labels(self, number, add, remove, repo):
        """`gh issue edit` は 1 回で付け外しを両方受けるので呼び出しは常に 1 回。"""
        argv = ["gh", "issue", "edit", str(number), *repo_flag(repo)]
        for name in add:
            argv += ["--add-label", name]
        for name in remove:
            argv += ["--remove-label", name]
        tracker.run_checked(argv)
        return 1


# --- repo scope ---------------------------------------------------------------------


def repo_flag(repo):
    """list / view 系の `--repo`。未指定なら flag ごと足さず gh の cwd 推論に残す。"""
    return ["--repo", repo] if repo else []


def api_scope(repo):
    """`gh api` の path 用 scope。`gh api` は `--repo` を受けないので path 側で示す。

    未指定なら gh が cwd から解決する placeholder のまま返す。
    """
    return repo or "{owner}/{repo}"


def split_repo(repo):
    """`owner/name` → (owner, name)。読めなければ TrackerError。

    graphql は owner と name を別の変数で受けるので、slug をここで割る。割れない綴りを
    そのまま撃つと変数が空のまま query が通り、**別の PR の観測が返る余地**を残す。
    """
    parts = str(repo or "").split("/")
    if len(parts) != 2 or not all(parts):
        raise tracker.TrackerError(f"repo 識別子を owner/name に割れない: {repo!r}")
    return parts[0], parts[1]


def normalize_review_threads(raw, number, repo):
    """graphql 応答 → {"threads", "truncated"}。読めなければ TrackerError。

    `gh api graphql` は HTTP 応答をそのまま出すので `data` の下に置かれる。node が無い
    (PR が消えた / 権限が無い) 応答を空として返さない — 「観測して 0 件」に化ける。
    """
    pull_request = ((raw.get("data") or {}).get("repository") or {}).get("pullRequest")
    threads = (pull_request or {}).get("reviewThreads")
    if threads is None:
        raise tracker.TrackerError(
            f"{repo}#{number} の review thread を読めない (応答に reviewThreads が無い): {raw!r}"
        )
    return {
        "threads": [
            {"id": node["id"], "resolved": bool(node.get("isResolved"))}
            for node in threads.get("nodes") or []
        ],
        "truncated": bool((threads.get("pageInfo") or {}).get("hasNextPage")),
    }


def reference_repo(repository):
    """closing reference の repository → `owner/name`。読めなければ TrackerError。

    「どの repo の PR か」を読めないまま返すと、呼び出し側が根拠 repo を判断できない
    (かつ `pr_detail` が cwd repo の同番号 PR を引いて別の PR を答える)。
    """
    fields = repository or {}
    owner = (fields.get("owner") or {}).get("login")
    name = fields.get("name")
    if not owner or not name:
        raise tracker.TrackerError(
            f"closing reference から PR の repo を読めない: {repository!r}"
        )
    return f"{owner}/{name}"


def full_name(repository):
    """timeline の repository → `owner/name`。読めなければ TrackerError。"""
    name = (repository or {}).get("full_name")
    if not name:
        raise tracker.TrackerError(
            f"cross-reference から PR の repo を読めない: {repository!r}"
        )
    return name


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


def normalize_mergeable(raw):
    """gh の mergeable → 内部語彙 3 値。未知語彙は UNKNOWN へ倒す。

    `MERGEABLE` へ倒さないのは「conflict 無し」と読ませないため — gh が語彙を増やしたとき、
    観測は「まだ分からない」側に留まる。
    """
    value = str(raw or "").upper()
    return value if value in tracker.MERGEABLE_VALUES else "UNKNOWN"


def normalize_pr(raw):
    return {
        "number": raw["number"],
        "state": tracker.normalize_pr_state(raw.get("state")),
        "mergeable": normalize_mergeable(raw.get("mergeable")),
        "title": raw.get("title", ""),
        "url": raw.get("url", ""),
    }


def _blocked_result(open_blockers, source):
    return {"blocked": bool(open_blockers), "open_blockers": open_blockers, "source": source}
