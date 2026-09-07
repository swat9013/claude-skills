"""GhAdapter — Tracker / CLLink / ChangeHost port の GitHub 実装 (`gh` CLI へ shell out)。

認証は `gh` に委譲する。本 module が持つのは **GitHub 語彙 → 中立語彙の写像だけ**で、判断は
持たない。GitLab 置き場は `glab_adapter` が同じ port を実装する。Jira 置き場は
`jira_adapter` だが、**あちらは Tracker port だけ**を実装する (ADR 0059)。

**1 つの class が 3 port (Tracker / CLLink / ChangeHost) を実装する**のは、gh が issue 置き場と
CL 置き場を兼ね、紐づきも自分で引けるため — CLI の呼び出し口と正規化を 3 度書かない。issue が
Jira / CL が GitLab のような分割は port が別なので成立する (別 adapter を別 port へ挿す)。

endpoint と `--json` field の綴りは実機でしか誤りが露見しない (sandbox 内で gh subprocess は
動かない) ため、**テスト側で argv を逐語 pin してある**。v1 (`mcp/dispatch-ops/tracker_gh.py`)
で実機検証済みの綴りを copy し、一字も変えていない — v1 とコードは共有しない (issue #850)。

明示 repo scope は 2 経路に分かれる: `gh` の list / view 系は `--repo` を足すだけだが、
**`gh api` は `--repo` を持たない**ので `repos/{owner}/{repo}/...` を literal path へ置換する。
置換し忘れると timeline だけ別 repo を答え、「観測は正しいのに紐づきだけ他 repo」という
最も高くつく取り違えになる。
"""

from dispatch_v2 import adapter_cli, ports, refs

TRACKER = "gh"

# 1 回の CLI 起動に許す上限秒。**daemon の HTTP は単一スレッド**で、tick の CLI 呼び出しは
# その間 tool 応答を塞ぐ。同梱 client の応答待ち (`client.REQUEST_TIMEOUT_SEC` = 20 秒) より
# 十分下に取らないと、tick が走っているだけで tool 呼び出しが timeout に見える
SUBPROCESS_TIMEOUT_SEC = 15

ISSUE_FIELDS = "number,state"
CL_FIELDS = "number,state,mergeable,headRefOid"

# 1 回の観測で扱える候補の上限。**省くと gh の既定 30 件で黙って切れる** — 切れた分は候補集合
# の差分から消え、`candidate_appeared` が上がらないまま候補が沈む。
#
# **現実の候補プールより桁で大きく取る**。溢れの検知 (下の probe) は観測を止めるので、実際に
# 起こりうる件数に上限を寄せると、正常に大きいプールが候補観測の全停止に化ける。ここは
# 「起きたら人が構成を見直す線」であって、日常的に当たる窓ではない。
#
# CLI へ渡すのは **上限 + 1 件**。ちょうど上限件数が返ったときに「全部見えた」と「切れた」を
# 区別できないと、正常な上限ちょうどのプールを溢れとして拒むか、溢れを見逃すかのどちらかに
# なる。1 件多く要求すれば、上限を超えたことだけが観測で判る
CANDIDATE_LIMIT = 1000
CANDIDATE_PROBE_LIMIT = CANDIDATE_LIMIT + 1

# review thread の 1 回取得件数。取り切れなければ未観測 (`None`) として表に出す — 黙って切ると
# 「未解決 0 件」に化ける
REVIEW_THREAD_PAGE_SIZE = 100

# review thread の観測 (ADR 0039 が PR #617 で実測した経路)。`--json` を持つ CLI 経路が無いので
# graphql を直に撃つ。**変数で渡して query 文字列は定数のまま**にするのは、組み立てた文字列を
# pin しても綴りの記録にならないため
REVIEW_THREADS_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!) {"
    " repository(owner: $owner, name: $name) {"
    f" pullRequest(number: $number) {{ reviewThreads(first: {REVIEW_THREAD_PAGE_SIZE})"
    " { nodes { id isResolved } pageInfo { hasNextPage } } } } }"
)

# gh の issue state → 中立語彙。**未知値は推測に倒さない** (open を closed と読むと機械遷移が
# 誤って terminal を確定させる。terminal は不変なので取り返しがつかない)
_ISSUE_STATES = {"open": "open", "closed": "closed"}

# gh の PR state → 内部語彙 (status の梯子へ渡す前段)
_CL_STATES = {"open": "OPEN", "closed": "CLOSED", "merged": "MERGED"}

run_command = adapter_cli.runner(timeout_sec=SUBPROCESS_TIMEOUT_SEC)


class GhAdapter(ports.TrackerPort, ports.CLLinkPort, ports.ChangeHostPort):
    """gh CLI 経由の Tracker / CLLink / ChangeHost 実装。"""

    tracker = TRACKER

    #: 置き場を列挙して 0 件だったのだから、「その issue に CL は無い」の証拠として使ってよい
    #: (`CLLinkPort` の既定は偽で、列挙できる adapter だけが名乗る)
    absence_is_evidence = True

    def __init__(self, run=None):
        # 起動境界を差し替えられる形にしてあるのは、sandbox / CI で gh を起こさずに argv を
        # 逐語検査するため (`principle-test-double-boundary`: gh は unmanaged dependency)
        self._run = run or run_command

    # --- Tracker port ----------------------------------------------------------

    def fetch_candidates(self, *, repo, label, observed_at):
        """候補プールの母集団 (open issue)。`label` が None なら絞らない。

        **絞り込みは宣言 config が渡した値でだけ行う** — 「どの issue が着手可か」の資格判定を
        adapter に埋めると、候補選定が環境固定される (ADR 0026 / 0056)。
        """
        argv = [
            "gh", "issue", "list",
            *_repo_flag(repo),
            "--state", "open",
            *(["--label", label] if label else []),
            "--limit", str(CANDIDATE_PROBE_LIMIT),
            "--json", ISSUE_FIELDS,
        ]
        raw = self._json(argv)
        if len(raw) > CANDIDATE_LIMIT:
            # **上限を超えた候補集合は観測失敗として扱う**。切れた集合をそのまま返すと、窓を
            # 出入りする issue が毎 tick「現れた / 消えた」に見えて candidate_appeared が誤発火し、
            # 窓の外にいる候補は永久に沈む。CL 側の truncation を `None` (未観測) で表に出して
            # いるのと同じ線。**ちょうど上限件数は正常** (probe の 1 件が余るので溢れていない)
            raise ports.ObservationError(
                f"候補が上限 {CANDIDATE_LIMIT} 件を超えた ({repo}, label={label!r}): "
                "切れた集合は差分の基準にできない"
            )
        return [_issue_fact(item, observed_at) for item in raw]

    def fetch_issue(self, issue_ref, *, repo, observed_at):
        number = _issue_number(issue_ref)
        argv = [
            "gh", "issue", "view", str(number), *_repo_flag(repo), "--json", ISSUE_FIELDS
        ]
        return _issue_fact(self._json(argv), observed_at)

    def fetch_cl_links(self, issue_ref, *, repo):
        """closing reference と cross-reference の和集合。

        closing reference (`Closes #N`) だけでは `Refs #N` で紐づけた CL を取り落とし、
        cross-reference だけでは UI の Development 欄で紐づけた CL を取り落とすため、両方から
        集める。**同じ CL が両方に出たら closes が勝つ** (根拠として強い側を残す)。
        """
        links = {}
        closing = self._json(
            [
                "gh", "issue", "view", str(_issue_number(issue_ref)),
                *_repo_flag(repo),
                "--json", "closedByPullRequestsReferences",
            ]
        )
        for reference in closing.get("closedByPullRequestsReferences") or []:
            key = (
                _reference_repo(reference.get("repository")),
                _referenced_cl_number(reference, "closing reference"),
            )
            links[key] = ports.ROLE_CLOSES
        timeline = self._json(
            [
                "gh", "api",
                f"repos/{_api_scope(repo)}/issues/{_issue_number(issue_ref)}/timeline",
                "--paginate",
            ]
        )
        if not isinstance(timeline, list):
            # 応答が list でない (error object 等) まま反復すると、key を舐めて意味の無い
            # `KeyError` になる。**観測できなかったこと**として上げる
            raise ports.ObservationError(f"timeline の応答が配列でない: {timeline!r}")
        for entry in timeline:
            if not isinstance(entry, dict) or entry.get("event") != "cross-referenced":
                continue
            source = (entry.get("source") or {}).get("issue") or {}
            if not source.get("pull_request"):
                continue
            links.setdefault(
                (
                    _full_name(source.get("repository")),
                    _referenced_cl_number(source, "cross-reference"),
                ),
                ports.ROLE_MENTION,
            )
        return [
            ports.cl_link(cl_ref=refs.format_cl_ref(TRACKER, number), repo=cl_repo, role=role)
            for (cl_repo, number), role in links.items()
        ]

    # --- ChangeHost port -------------------------------------------------------

    def fetch_cl(self, cl_ref, *, repo, observed_at):
        """CL 1 件。`repo` は **その CL が居る repo** なので、issue と別 repo でも届く。"""
        number = _cl_number(cl_ref)
        raw = self._json(
            ["gh", "pr", "view", str(number), *_repo_flag(repo), "--json", CL_FIELDS]
        )
        state = _cl_state(raw.get("state"))
        mergeable = _mergeable(raw.get("mergeable"))
        return ports.cl_fact(
            cl_ref=cl_ref,
            repo=repo,
            status=ports.cl_status_for(state, mergeable),
            mergeable=mergeable,
            unresolved_threads=self._unresolved_threads(number, repo),
            head_sha=raw.get("headRefOid") or None,
            observed_at=observed_at,
        )

    def _unresolved_threads(self, number, repo):
        """未解決 review thread の id 列。読み切れていなければ `None` (未観測)。

        `hasNextPage` が立っていて未解決が 1 件も見つかっていないときだけ `None` を返す —
        1 件でも見つかっていれば「未解決がある」は取り切れなくても真なので、`[]` に落とさない
        情報のほうが多い。**取り切れていないのに `[]` を返さない**のがこの分岐の要点。
        """
        owner, name = _split_repo(repo)
        raw = self._json(
            [
                "gh", "api", "graphql",
                "-f", f"query={REVIEW_THREADS_QUERY}",
                "-F", f"owner={owner}",
                "-F", f"name={name}",
                "-F", f"number={number}",
            ]
        )
        pull_request = ((raw.get("data") or {}).get("repository") or {}).get("pullRequest")
        threads = (pull_request or {}).get("reviewThreads")
        if threads is None:
            # node が無い (CL が消えた / 権限が無い) 応答を空として返さない — 「観測して 0 件」
            # に化ける
            return None
        unresolved = [
            node["id"] for node in threads.get("nodes") or [] if not node.get("isResolved")
        ]
        truncated = bool((threads.get("pageInfo") or {}).get("hasNextPage"))
        if truncated and not unresolved:
            return None
        return unresolved

    # --- 起動 -------------------------------------------------------------------

    def _json(self, argv):
        """gh を起動して stdout を JSON として読む。非 0 exit / 非 JSON は ObservationError。"""
        return adapter_cli.json_output(self._run, argv, cli=TRACKER)


# --- repo scope -----------------------------------------------------------------


def _repo_flag(repo):
    """list / view 系の `--repo`。未指定なら flag ごと足さず gh の cwd 推論に残す。"""
    return ["--repo", repo] if repo else []


def _api_scope(repo):
    """`gh api` の path 用 scope (`gh api` は `--repo` を受けないので path 側で示す)。"""
    return repo or "{owner}/{repo}"


def _split_repo(repo):
    """`owner/name` → (owner, name)。割れなければ ObservationError。

    graphql は owner と name を別の変数で受けるので、slug をここで割る。割れない綴りをそのまま
    撃つと変数が空のまま query が通り、**別の CL の観測が返る余地**を残す。
    """
    parts = str(repo or "").split("/")
    if len(parts) != 2 or not all(parts):
        raise ports.ObservationError(f"repo 識別子を owner/name に割れない: {repo!r}")
    return parts[0], parts[1]


# --- 正規化 ---------------------------------------------------------------------


def _issue_number(issue_ref):
    """中立 issue ref → gh の issue 番号。gh 以外の ref は ObservationError。"""
    return adapter_cli.issue_number(issue_ref, tracker=TRACKER)


def _cl_number(cl_ref):
    """中立 CL ref → gh の PR 番号。gh 以外の ref は ObservationError。"""
    return adapter_cli.cl_number(cl_ref, tracker=TRACKER)


def _issue_fact(raw, observed_at):
    state = _ISSUE_STATES.get(str(raw.get("state")).lower())
    if state is None:
        raise ports.ObservationError(f"未知の gh issue state: {raw.get('state')!r}")
    return ports.issue_fact(
        issue_ref=refs.format_issue_ref(TRACKER, raw["number"]),
        state=state,
        observed_at=observed_at,
    )


def _cl_state(raw):
    state = _CL_STATES.get(str(raw).lower())
    if state is None:
        raise ports.ObservationError(f"未知の gh PR state: {raw!r}")
    return state


def _mergeable(raw):
    """gh の mergeable → 中立語彙 3 値。未知語彙は UNKNOWN へ倒す。

    `MERGEABLE` へ倒さないのは「conflict 無し」と読ませないため — gh が語彙を増やしたとき、
    観測は「まだ分からない」側に留まる。
    """
    value = str(raw or "").upper()
    return value if value in ports.MERGEABLE_VALUES else "UNKNOWN"


def _referenced_cl_number(fields, source):
    """紐づき応答の payload から CL 番号を読む (`_cl_number` は中立 ref を割る側)。

    素の添字だと `KeyError` が観測経路の外へ漏れ、tick 全体を落として「観測できなかった」
    という分類も失う。
    """
    number = fields.get("number")
    if not isinstance(number, int):
        raise ports.ObservationError(f"{source} から CL 番号を読めない: {fields!r}")
    return number


def _reference_repo(repository):
    """closing reference の repository → `owner/name`。読めなければ ObservationError。

    「どの repo の CL か」を読めないまま返すと、`fetch_cl` が cwd repo の同番号 CL を引いて
    別の CL を答える。
    """
    fields = repository or {}
    owner = (fields.get("owner") or {}).get("login")
    name = fields.get("name")
    if not owner or not name:
        raise ports.ObservationError(
            f"closing reference から CL の repo を読めない: {repository!r}"
        )
    return f"{owner}/{name}"


def _full_name(repository):
    """timeline の repository → `owner/name`。読めなければ ObservationError。"""
    name = (repository or {}).get("full_name")
    if not name:
        raise ports.ObservationError(f"cross-reference から CL の repo を読めない: {repository!r}")
    return name
