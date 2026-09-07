"""GlabAdapter — TrackerPort の GitLab 実装 (`glab` CLI へ shell out)。

GitHub との差を吸収する箇所が 3 つある:

- 番号は `iid` (project 内番号)。中立 ref の番号にはこちらを使う
- state は `opened` / `closed`。`tracker.normalize_state` が内部語彙へ揃える
- blocker は link 型 `is_blocked_by`。リンク型を運用していない instance では
  block 系 label を fallback にする (この経路は blocker の個別列挙ができない)

明示 repo scope (ADR 0036) は 2 経路に分かれる: `glab issue` 系は `--repo` を足すだけだが、
**`glab api` は `--repo` を持たない**ので path の project 位置を差し替える。GitLab の project は
**数値 id か URL-encode した full path** でしか指せず、`group/project` を生のまま埋めると path の
階層が 1 段増えて 404 になる (`glab api projects/gitlab-org/cli` = 404 /
`projects/gitlab-org%2Fcli` = 200 を gitlab.com で実機確認済み)。

endpoint の綴りは大半が実機照合できない (手元に運用中の instance が無い) ため、テスト側の
argv pin だけが記録になる。既存 `dispatch_tracker.py` から移植した綴りは一字も変えていない。
例外は明示 repo scope の project 指定 (`projects/<URL-encode した full path>`) と review thread の
読み取り (`GET .../discussions`) で、こちらは実 instance へ投げて応答の形まで確かめてある。
"""

import urllib.parse

import tracker

ROLE_CLOSES = tracker.ROLE_CLOSES
ROLE_MENTION = tracker.ROLE_MENTION

# `is_blocked_by` リンク型が運用されていない instance の fallback
BLOCK_FALLBACK_LABELS = frozenset({"wayfinder:blocked"})

# GitLab が mergeability を計算し終えていない間の detailed_merge_status
UNSETTLED_MERGE_STATUS = frozenset({"checking", "unchecked"})

# glab の per-page 上限。これ超えの issue / review thread 運用は page ループ化が要る (未対応)
MAX_PER_PAGE = 100

# 複合 thread id (`<project>!<iid>:<discussion id>`) の区切り。綴りは中立 ref に揃える —
# `!` は PR ref (`glab!12`)、`:` は Jira ref (`jira:PROJ-9`) の継ぎ方
THREAD_ID_PROJECT_SEP = "!"
THREAD_ID_DISCUSSION_SEP = ":"

# 中立 state → glab issue list の追加 flag。既定 (flag なし) が opened
_LIST_STATE_FLAG = {"open": [], "closed": ["--closed"], "all": ["--all"]}


class GlabAdapter(tracker.TrackerPort):
    tracker = "glab"
    # self-hosted instance が主なので host そのものは固定できない。`gitlab` を部分一致で
    # 拾う (`gitlab.example.com` / `gitlab.com` の両方に当てるため)
    remote_hosts = ("gitlab",)
    # 1 page で取り切る前提 (page ループは未対応)。port はこの値で truncated を判定する
    max_fetch = MAX_PER_PAGE
    supports_repo_scope = True
    # `pr_detail` は project id を path に埋めるので cwd 推論へ倒れられない (`projects/None/...`)
    pr_detail_requires_repo = True
    # review thread は discussion へ写す (`notes[0].resolvable` が thread か否か、
    # `notes[0].resolved` が未解決か否かを持つ)。resolve と返信が MR 番号と discussion id の
    # 両方を path に要る非対称は、**複合 thread id を adapter 内で組んで**吸収する —
    # port の署名 (`resolve_review_thread` / `reply_review_thread`) は gh と同じまま
    supports_review_threads = True

    # --- issue 観測 ------------------------------------------------------------

    def fetch_issues(self, state, limit, repo):
        argv = [
            "glab", "issue", "list",
            *repo_flag(repo),
            "--output", "json",
            "--per-page", str(limit),
            *_LIST_STATE_FLAG[state],
        ]
        return [normalize_issue(item) for item in tracker.run_json(argv)]

    def fetch_issue(self, number, repo):
        raw = tracker.run_json(
            ["glab", "issue", "view", str(number), *repo_flag(repo), "--output", "json"]
        )
        return normalize_issue(raw)

    def fetch_blocked(self, number, repo):
        """open blocker 検査。scope の示し方が経路で分かれる (`api` は path、`issue` は flag)。"""
        links = tracker.run_json(
            ["glab", "api", f"projects/{api_scope(repo)}/issues/{number}/links"]
        )
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
        issue = tracker.run_json(
            ["glab", "issue", "view", str(number), *repo_flag(repo), "--output", "json"]
        )
        hit = sorted(set(tracker.label_names(issue.get("labels"))) & BLOCK_FALLBACK_LABELS)
        return {"blocked": bool(hit), "open_blockers": [], "source": "labels"}

    # --- MR 観測 ---------------------------------------------------------------

    def linked_prs(self, number, repo):
        """issue → 紐づく MR の列。gh 側と同じく closed_by と関連 MR の和集合を採る。

        **project で絞らない** (ADR 0036)。別 project の MR も現実のまま載せ、どれを
        根拠に採るかは呼び出し側が `repo` (= project id) を見て決める。
        """
        scope = api_scope(repo)
        links = {}
        for mr in tracker.run_json(
            ["glab", "api", f"projects/{scope}/issues/{number}/closed_by"]
        ):
            links[(mr_project(mr), mr["iid"])] = ROLE_CLOSES
        for mr in tracker.run_json(
            ["glab", "api", f"projects/{scope}/issues/{number}/related_merge_requests"]
        ):
            links.setdefault((mr_project(mr), mr["iid"]), ROLE_MENTION)
        return [
            {"number": iid, "role": role, "repo": project}
            for (project, iid), role in links.items()
        ]

    def pr_detail(self, number, role, repo):
        """MR 1 件。path の project は **その MR 自身の id** (別 project でも届く)。

        `:id` (cwd project) 固定のままだと、別 project の MR に同 iid の MR を返して
        まったく別の MR の status を答える。

        識別子は `linked_prs` 由来なら数値 id、台帳の記録や宣言 (`[pr] repo`) 由来なら
        full path で来る。**どちらも `encode_project` を通す** — path を生のまま埋めると
        404 になり、`observe_pr_refs` が宣言の値を既定にする経路がまるごと落ちる。
        """
        raw = tracker.run_json(
            ["glab", "api", f"projects/{encode_project(repo)}/merge_requests/{number}"]
        )
        return {**normalize_mr(raw), "role": role, "repo": repo}

    def open_prs(self, limit, repo):
        """project の open MR。

        closing issue は MR ごとに 1 回追加照会するので **CLI 起動は 1 + MR 件数** になる
        (open MR が 100 件なら 101 回)。件数が多い project では `limit` を絞って呼ぶ。

        各 MR の `repo` は list 応答の `project_id` から採る。この field が list endpoint に
        載ることは実機照合できていない (module docstring の「綴りは argv pin だけが記録」と
        同じ扱い) — 欠けていれば `mr_project` が名指しで失敗する。
        """
        scope = api_scope(repo)
        raw = tracker.run_json(
            ["glab", "api", f"projects/{scope}/merge_requests?state=opened&per_page={limit}"]
        )
        entries = []
        for item in raw:
            iid = item["iid"]
            closes = tracker.run_json(
                ["glab", "api", f"projects/{scope}/merge_requests/{iid}/closes_issues"]
            )
            entries.append(
                {
                    **normalize_mr(item),
                    "role": None,
                    "repo": mr_project(item),
                    "head_branch": item.get("source_branch") or "",
                    "closes_issues": [issue["iid"] for issue in closes],
                }
            )
        return entries

    # --- review thread 観測 / resolve (ADR 0039) ---------------------------------

    def fetch_review_threads(self, number, repo):
        """MR 1 件の review thread。`repo` は **その MR が居る project** で cwd 推論へ倒れない。

        返す `id` は複合 thread id — resolve が MR 番号を path に要るので、discussion id
        だけでは `resolve_review_thread(thread_id)` から MR へ戻れない。

        `truncated` は **1 ページ目が満杯かどうかの heuristic**。gh は graphql の
        `hasNextPage` という確定的な判定を持つが、`tracker.run_json` は stdout の JSON を
        パースするだけで header を見ないので、`X-Next-Page` を根拠にできない。偽陽性は
        `resolve` の `unresolved_review_threads` を null (未判定) へ倒すだけで、
        「観測して 0 件」を騙るより安い方向に外れる。
        """
        discussions = tracker.run_json(
            [
                "glab", "api",
                f"projects/{encode_project(repo)}/merge_requests/{number}"
                f"/discussions?per_page={MAX_PER_PAGE}",
            ]
        )
        return normalize_review_threads(discussions, number, repo)

    def resolve_review_thread(self, thread_id):
        project, number, discussion_id = parse_thread_id(thread_id)
        raw = tracker.run_json(
            [
                "glab", "api", "--method", "PUT",
                f"projects/{encode_project(project)}/merge_requests/{number}"
                f"/discussions/{discussion_id}?resolved=true",
            ]
        )
        notes = (raw or {}).get("notes") or []
        if not (raw or {}).get("id") or not notes:
            raise tracker.TrackerError(
                f"review thread {thread_id} の resolve 応答を読めない: {raw!r}"
            )
        return {
            "id": compose_thread_id(project, number, raw["id"]),
            "resolved": bool(notes[0].get("resolved")),
        }

    def reply_review_thread(self, thread_id, body):
        """discussion へ note を 1 つ積む。複合 thread id は resolve と同じ `parse_thread_id`
        で割る (返信の path も MR 番号と discussion id の両方を要る)。

        応答は **note 1 件**で、resolve が受け取る discussion (`notes` を持つ) とは別の形。
        本文は `-f` (生文字列) — `-F` は型付きなので数字だけの本文が数値へ化ける。
        """
        project, number, discussion_id = parse_thread_id(thread_id)
        raw = tracker.run_json(
            [
                "glab", "api", "--method", "POST",
                f"projects/{encode_project(project)}/merge_requests/{number}"
                f"/discussions/{discussion_id}/notes",
                "-f", f"body={body}",
            ]
        )
        if not (raw or {}).get("id"):
            raise tracker.TrackerError(
                f"review thread {thread_id} の返信応答を読めない: {raw!r}"
            )
        return {"comment_id": str(raw["id"])}

    # --- 操作 -------------------------------------------------------------------

    def post_comment(self, number, body, repo):
        tracker.run_checked(
            ["glab", "issue", "note", str(number), *repo_flag(repo), "--message", body]
        )

    def edit_labels(self, number, add, remove, repo):
        """`glab issue update` は 1 回で `--label` / `--unlabel` を両方受ける。"""
        argv = ["glab", "issue", "update", str(number), *repo_flag(repo)]
        for name in add:
            argv += ["--label", name]
        for name in remove:
            argv += ["--unlabel", name]
        tracker.run_checked(argv)
        return 1


# --- repo scope ---------------------------------------------------------------------


def repo_flag(repo):
    """`glab issue` 系の `--repo`。未指定なら flag ごと足さず glab の cwd 推論に残す。

    受ける綴りは `OWNER/REPO` / `GROUP/NAMESPACE/REPO` (`glab issue list --help` で確認)。
    """
    return ["--repo", repo] if repo else []


def api_scope(repo):
    """`glab api` の path 用 project。`glab api` は `--repo` を受けないので path 側で示す。

    未指定なら glab が cwd から解決する placeholder (`:id`) のまま返す。
    """
    return encode_project(repo) if repo else ":id"


def encode_project(project):
    """project 識別子 → API path に埋められる綴り。

    GitLab の project は数値 id か **URL-encode した full path** で指す (数値はそのまま
    通る)。`group/project` を生のまま埋めると path の階層が増えて 404 になる。
    """
    return urllib.parse.quote(str(project), safe="")


# --- 正規化 -------------------------------------------------------------------------


def mr_project(raw):
    """MR payload → 中立 schema の `repo` (数値 project id の文字列)。

    gh の `owner/name` と型を揃えるため文字列にする。**宣言 (`[pr] repo`) が full path
    ならこの値と綴りが揃わない** — 台帳記録との (repo, ref) 突合はその形の差を吸収しない。
    """
    project_id = raw.get("project_id")
    if project_id is None:
        raise tracker.TrackerError(f"MR から project を読めない: {raw!r}")
    return str(project_id)


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


def normalize_mergeable(raw):
    """MR payload → 内部語彙 3 値。**gh と違って引数は生値でなく MR payload ごと**。

    mergeability の答えが 2 field に分かれており、`has_conflicts` は計算完了後にしか
    信用できない (計算中は既定値の False が入る) ので順に見る。
    """
    if str(raw.get("detailed_merge_status") or "").lower() in UNSETTLED_MERGE_STATUS:
        return "UNKNOWN"
    conflicts = raw.get("has_conflicts")
    if conflicts is None:
        return "UNKNOWN"
    return "CONFLICTING" if conflicts else "MERGEABLE"


def normalize_mr(raw):
    return {
        "number": raw["iid"],
        "state": tracker.normalize_pr_state(raw.get("state")),
        "mergeable": normalize_mergeable(raw),
        "title": raw.get("title", ""),
        "url": raw.get("web_url", ""),
    }


# --- review thread -------------------------------------------------------------------


def normalize_review_threads(discussions, number, repo):
    """discussion の列 → {"threads", "truncated"}。読めなければ TrackerError。

    discussion は review thread とは限らない (MR 全体への通常コメント、bot の自動コメント、
    GitLab が積む system note も同じ列に並ぶ)。**thread か否かは先頭 note の `resolvable`**
    が持ち、未解決か否かは同じ note の `resolved` が持つ。
    """
    if not isinstance(discussions, list):
        raise tracker.TrackerError(
            f"{repo}!{number} の review thread を読めない (応答が discussion の列でない): "
            f"{discussions!r}"
        )
    threads = []
    for discussion in discussions:
        notes = discussion.get("notes") or []
        head = notes[0] if notes else {}
        # system note は `resolvable` が false で来る instance しか観測できていないので、
        # 判定は resolvable 側に持たせたまま system を保険として重ねる
        if not head.get("resolvable") or head.get("system"):
            continue
        threads.append(
            {
                "id": compose_thread_id(repo, number, discussion["id"]),
                "resolved": bool(head.get("resolved")),
            }
        )
    return {"threads": threads, "truncated": len(discussions) == MAX_PER_PAGE}


def compose_thread_id(project, number, discussion_id):
    """thread 識別子 → `<project>!<MR iid>:<discussion id>`。

    resolve の path が MR 番号を要るのに port は thread id しか渡さないので、**adapter が
    自分で組んで自分で割る**。両端が adapter 内なので port の署名は gh と同じまま済む。
    """
    return (
        f"{project}{THREAD_ID_PROJECT_SEP}{number}"
        f"{THREAD_ID_DISCUSSION_SEP}{discussion_id}"
    )


def parse_thread_id(thread_id):
    """複合 thread id → (project, MR iid, discussion id)。割れない綴りは TrackerError。

    **右端から割る** — project は数値 id とは限らず、宣言 (`[pr] repo`) 由来の full path
    (`group/sub/project`) で来ると区切りより左に階層が混じる。

    生の discussion id を「project 未指定」として cwd 推論へ倒す fallback は**置かない**。
    どの MR の thread か判らないまま撃つと別 MR の指摘を閉じるので、名指しで落とす。
    """
    head, _, discussion_id = thread_id.rpartition(THREAD_ID_DISCUSSION_SEP)
    project, _, number = head.rpartition(THREAD_ID_PROJECT_SEP)
    if not (project and number and discussion_id):
        raise tracker.TrackerError(
            f"review thread {thread_id!r} は glab の複合 thread id "
            f"(`<project>{THREAD_ID_PROJECT_SEP}<MR iid>{THREAD_ID_DISCUSSION_SEP}"
            "<discussion id>`) に割れない — resolve は MR 番号を要るので "
            "discussion id 単体では撃てない"
        )
    return project, number, discussion_id
