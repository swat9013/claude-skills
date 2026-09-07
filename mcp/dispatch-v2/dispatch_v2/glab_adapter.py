"""GlabAdapter — Tracker / CLLink / ChangeHost port の GitLab 実装 (`glab` CLI へ shell out)。

認証は `glab` に委譲する。本 module が持つのは **GitLab 語彙 → 中立語彙の写像だけ**で、判断は
持たない。`gh_adapter` と同じく **1 つの class が 3 port を実装する** — GitLab が issue 置き場と
CL 置き場を兼ね、紐づきも自分で引けるため、CLI の呼び出し口と正規化を 3 度書かない。

綴りは v1 (`mcp/dispatch-ops/tracker_glab.py`) から copy した。**v1 とコードは共有しない**
(issue #850)。endpoint の綴りは大半が実機照合できない (sandbox 内で glab subprocess は動かない)
ため、**テスト側で argv を逐語 pin してある**のが唯一の記録になる。

GitHub と挙動が割れる箇所は次のとおりで、写し違えるとどれも「観測できたのに中身が違う」形で出る:

- **project 識別子は API path に埋まる**。`glab api` は `--repo` を受けず、GitLab の project は
  数値 id か **URL-encode した full path** でしか指せない。`gh` は `--repo` を省けば CLI の
  cwd 推論に倒れて済むが、こちらで同じことをすると `:id` placeholder が「同じ iid の別 project
  の MR」を答える。そこで **repo 未指定は CLI 起動前に名指しで落とす** (`_project_scope`)
- **候補観測は page ループ**。`--per-page` の上限が 100 なので、gh のように「上限 + 1 件を
  一撃で要求する」probe が撃てない。満杯ページが続く限り次ページを取り、累計が上限を超えたら
  観測失敗にする (`fetch_candidates`)
- **番号は `iid`** (project 内番号)。中立 ref の番号にはこちらを使う
- **mergeable は 2 段の field から読む** (`_mergeable`)。GitHub の `mergeable` 1 本と違い、
  GitLab は `detailed_merge_status` (15.6+) と deprecated な `merge_status` に分かれる
- **head commit も 2 段** (`fetch_cl`)。GitHub の `headRefOid` 1 本に対し、GitLab の MR payload
  は top-level の `sha` と `diff_refs.head_sha` の両方が同じ commit を指す。前者は MR の一覧
  応答にも載る短い綴りだが**「取得できたなら」の field** で、後者は「source branch の head
  commit」と定義された正本。前者を先に読み、欠けていれば後者へ落ちる

**接続先 instance を宣言で指せない** (未対応)。`glab` は host を CLI の設定と cwd の remote から
解決するが、daemon はマシンに 1 プロセスで対象 project の clone の中では走らない (設計 §7)。
つまり **`glab` の既定 host 以外の instance は観測できない**。到達できなければ非 0 exit →
`ObservationError` → rule #9 (store 到達不能) として出るが、**既定 host に同じ full path の
project が在れば 200 が返り、別 instance の事実を観測成功として記帳する** — `_require_project`
が project 軸で塞いだのと同じ沈黙が host 軸には残っている。self-hosted instance を扱うなら、
host を宣言できるようにして未指定を名指しで落とすところから要る。

**review thread の id は生の discussion id**。v1 は複合 id (`<project>!<MR iid>:<discussion id>`)
を組んでいたが、あれは resolve の path が MR 番号を要るための細工で、**v2 は thread を resolve
しない** (ADR 0039 = worker が Host 上で行う)。消費者の居ない識別子を持ち回らない。
"""

import time
import urllib.parse

from dispatch_v2 import adapter_cli, ports, refs

TRACKER = "glab"

# 1 回の CLI 起動に許す上限秒。理由は `gh_adapter.SUBPROCESS_TIMEOUT_SEC` と同じ (daemon の
# HTTP は単一スレッドで、tick の CLI 呼び出しはその間 tool 応答を塞ぐ)
SUBPROCESS_TIMEOUT_SEC = 15

# `--per-page` の上限 (GitLab API 側の制約)。1 ページで取り切れないぶんは `--page` で進める
PAGE_SIZE = 100

# 1 回の観測で扱える候補の上限。**現実の候補プールより桁で大きく取る** — 溢れの検知は観測を
# 止めるので、実際に起こりうる件数へ上限を寄せると、正常に大きいプールが候補観測の全停止に
# 化ける (`gh_adapter.CANDIDATE_LIMIT` と同じ線・同じ値)
CANDIDATE_LIMIT = 1000

# **次のページを取りに行ってよい間**。候補観測は本 adapter で唯一 CLI を複数回起こす経路なので、
# 1 回ぶんの timeout では全体が抑えられない — 上限まで詰まったプールで毎ページが timeout 際まで
# 粘ると、単一スレッドの daemon が 11 回ぶん (165 秒) HTTP を塞ぎ、client の応答待ち
# (`client.REQUEST_TIMEOUT_SEC` = 20 秒) を大きく超えて「daemon が居ない」と読まれる。
#
# **予算は「次を取りに行くか」の判定にだけ使い、走り出したページは最後まで待つ**ので、最悪は
# 予算 + CLI 1 回 = 30 秒。gh の 15 秒よりは長いが、165 秒とは桁が違う。予算を CLI 1 回ぶんと
# 同じ大きさに取ってあるのは、**健全に大きいプールを観測失敗へ倒さない**ため (ページが速い
# 通常時は 11 ページでも数秒で収まる)
CANDIDATE_PAGING_BUDGET_SEC = 15

# glab の issue state → 中立語彙。**未知値は推測に倒さない** (open を closed と読むと機械遷移が
# 誤って terminal を確定させる。terminal は不変なので取り返しがつかない)
_ISSUE_STATES = {"opened": "open", "closed": "closed"}

# glab の MR state → 内部語彙 (status の梯子へ渡す前段)。`locked` は merge 実行中の短命な
# 遷移状態なので、**まだ動いている側** (OPEN) へ写す — closed へ倒すと、merge の最中に
# 観測した WorkOrder が `abandoned` で terminal に落ちる
_CL_STATES = {"opened": "OPEN", "locked": "OPEN", "closed": "CLOSED", "merged": "MERGED"}

# `detailed_merge_status` (GitLab 15.6+) のうち、**conflict の有無をそのまま答える値だけ**を
# 表に持つ。`preparing` / `unchecked` / `checking` は「まだ計算していない / 計算中」なので
# `open` (conflict 無し) と混ぜない — push 直後の観測は日常的にここを踏む。
#
# **表に無い値は `merge_status` へ落とす**。`ci_still_running` / `not_approved` /
# `draft_status` のように「mergeability は計算済みだが別の理由で merge できない」値が多数あり、
# それらを MERGEABLE へ倒すと GitLab が語彙を増やしたときに未知値まで「conflict 無し」を
# 名乗る (中立語彙の規約は「未知は分からない側へ留まる」)
_DETAILED_MERGE_STATUSES = {
    "mergeable": "MERGEABLE",
    "conflict": "CONFLICTING",
    "preparing": "UNKNOWN",
    "unchecked": "UNKNOWN",
    "checking": "UNKNOWN",
}

# deprecated な `merge_status` → 中立語彙 3 値。`detailed_merge_status` を持たない instance
# (GitLab 15.6 未満) と、上の表に無い detailed 値の受け皿を兼ねる。
#
# **`has_conflicts` は読まない** — API doc が「`merge_status` に従属し、`cannot_be_merged` で
# ない限り false」と明記しており、計算中の false が「conflict 無し」に化ける
_MERGE_STATUSES = {
    "can_be_merged": "MERGEABLE",
    "cannot_be_merged": "CONFLICTING",
    "unchecked": "UNKNOWN",
    "checking": "UNKNOWN",
    "cannot_be_merged_recheck": "UNKNOWN",
    "cannot_be_merged_rechecking": "UNKNOWN",
}

run_command = adapter_cli.runner(timeout_sec=SUBPROCESS_TIMEOUT_SEC)


class GlabAdapter(ports.TrackerPort, ports.CLLinkPort, ports.ChangeHostPort):
    """glab CLI 経由の Tracker / CLLink / ChangeHost 実装。"""

    tracker = TRACKER

    #: 置き場を列挙して 0 件だったのだから、「その issue に CL は無い」の証拠として使ってよい
    #: (`CLLinkPort` の既定は偽で、列挙できる adapter だけが名乗る)
    absence_is_evidence = True

    def __init__(self, run=None, monotonic=time.monotonic):
        # 起動境界を差し替えられる形にしてあるのは、sandbox / CI で glab を起こさずに argv を
        # 逐語検査するため (`principle-test-double-boundary`: glab は unmanaged dependency)。
        # 単調時計も同じ理由で注入する — page ループの予算を実時刻で待たずに検査できる
        self._run = run or run_command
        self._monotonic = monotonic

    # --- Tracker port ----------------------------------------------------------

    def fetch_candidates(self, *, repo, label, observed_at):
        """候補プールの母集団 (open issue)。`label` が None なら絞らない。

        **絞り込みは宣言 config が渡した値でだけ行う** — 「どの issue が着手可か」の資格判定を
        adapter に埋めると、候補選定が環境固定される (ADR 0026 / 0056)。

        **state は flag の不在で指定する**。`glab issue list` の既定が opened で、`--closed` /
        `--all` を足すと母集団が変わる (`--all` は「全 state」で、「全ページ」ではない)。

        満杯のページは「続きがある」の合図。切れた集合をそのまま返すと、窓を出入りする issue が
        毎 tick「現れた / 消えた」に見えて `candidate_appeared` が誤発火し、窓の外にいる候補は
        永久に沈む。上限を超えたら観測失敗として上げる (**ちょうど上限件数は正常**)。
        """
        scope = _repo_flag(repo)
        facts = []
        page = 1
        deadline = self._monotonic() + CANDIDATE_PAGING_BUDGET_SEC
        while True:
            raw = self._json(
                [
                    "glab", "issue", "list",
                    *scope,
                    "--output", "json",
                    "--per-page", str(PAGE_SIZE),
                    "--page", str(page),
                    *(["--label", label] if label else []),
                ]
            )
            if not _is_object_list(raw):
                raise ports.ObservationError(f"候補の応答が issue の列でない: {raw!r}")
            facts.extend(_issue_fact(item, observed_at) for item in raw)
            if len(facts) > CANDIDATE_LIMIT:
                raise ports.ObservationError(
                    f"候補が上限 {CANDIDATE_LIMIT} 件を超えた ({repo}, label={label!r}): "
                    "切れた集合は差分の基準にできない"
                )
            if len(raw) < PAGE_SIZE:
                return facts
            if self._monotonic() >= deadline:
                # **切れた集合を返さずに観測失敗として上げる**。次の tick で最初から取り直す
                raise ports.ObservationError(
                    f"候補観測が {CANDIDATE_PAGING_BUDGET_SEC} 秒の予算を使い切った "
                    f"({repo}, {page} ページ目まで): 切れた集合は差分の基準にできない"
                )
            page += 1

    def fetch_issue(self, issue_ref, *, repo, observed_at):
        argv = [
            "glab", "issue", "view", str(_issue_number(issue_ref)),
            *_repo_flag(repo),
            "--output", "json",
        ]
        return _issue_fact(self._json(argv), observed_at)

    def fetch_cl_links(self, issue_ref, *, repo):
        """closing reference と関連 MR の和集合。**project で絞らない**。

        `closed_by` だけでは `Refs #N` で紐づけた MR を取り落とし、`related_merge_requests`
        だけでは UI から閉じるよう設定した MR の役割 (closes) を取り落とすため、両方から集める。
        **同じ MR が両方に出たら closes が勝つ** (根拠として強い側を残す)。

        別 project / fork からの MR も所属 project 付きでそのまま返す — 自 project 以外を
        落とすと正当な closes が消え、機械遷移が根拠を失う (ADR 0036 と同じ理屈)。
        """
        scope = _project_scope(repo)
        number = _issue_number(issue_ref)
        links = {}
        for merge_request in self._merge_requests(
            f"projects/{scope}/issues/{number}/closed_by"
        ):
            links[_link_key(merge_request)] = ports.ROLE_CLOSES
        for merge_request in self._merge_requests(
            f"projects/{scope}/issues/{number}/related_merge_requests"
        ):
            links.setdefault(_link_key(merge_request), ports.ROLE_MENTION)
        return [
            ports.cl_link(cl_ref=refs.format_cl_ref(TRACKER, iid), repo=project, role=role)
            for (project, iid), role in links.items()
        ]

    def _merge_requests(self, endpoint):
        """紐づき endpoint の応答を MR の列として読む。列でなければ ObservationError。

        **`per_page` を明示し、満杯なら観測失敗として上げる**。GitLab の既定は 1 ページ 20 件
        で、黙って切れた紐づきは「観測して 0 件」と区別が付かない — merge 済みの closes MR が
        ページから落ちると、完了した issue が `abandoned` で terminal に落ちる (terminal は
        不変)。**候補観測と違って page ループを組まない**のは、1 件の issue に 100 件の
        紐づく MR がある状態が構成の異常であって日常の窓ではないから (候補プールは日常的に
        大きくなりうるので、あちらはループする)。
        """
        raw = self._json(["glab", "api", f"{endpoint}?per_page={PAGE_SIZE}"])
        if not _is_object_list(raw):
            raise ports.ObservationError(f"{endpoint} の応答が MR の列でない: {raw!r}")
        if len(raw) == PAGE_SIZE:
            raise ports.ObservationError(
                f"{endpoint} の紐づきが 1 ページ ({PAGE_SIZE} 件) を超えた: "
                "切れた紐づきは「紐づく CL が無い」と区別できない"
            )
        return raw

    # --- ChangeHost port -------------------------------------------------------

    def fetch_cl(self, cl_ref, *, repo, observed_at):
        """MR 1 件。`repo` は **その MR が居る project** なので、issue と別 project でも届く。"""
        number = _cl_number(cl_ref)
        scope = _project_scope(repo)
        raw = self._json(["glab", "api", f"projects/{scope}/merge_requests/{number}"])
        mergeable = _mergeable(raw)
        return ports.cl_fact(
            cl_ref=cl_ref,
            repo=repo,
            status=ports.cl_status_for(_cl_state(raw.get("state")), mergeable),
            mergeable=mergeable,
            unresolved_threads=self._unresolved_threads(number, scope),
            head_sha=raw.get("sha") or (raw.get("diff_refs") or {}).get("head_sha") or None,
            observed_at=observed_at,
        )

    def _unresolved_threads(self, number, scope):
        """未解決 review thread の id 列。読み切れていなければ `None` (未観測)。

        discussion は review thread とは限らない (MR 全体への通常コメント、bot の自動コメント、
        GitLab が積む system note も同じ列に並ぶ)。**thread か否かは先頭 note の `resolvable`**
        が持ち、未解決か否かは同じ note の `resolved` が持つ。

        **満杯判定は絞り込む前の生の件数で行う** — thread へ絞った後の件数と `PAGE_SIZE` を
        比べると、通常コメントが混ざるページで truncation の検知が壊れる。

        満杯かつ未解決が 1 件も見つかっていないときだけ `None` を返すのは gh と同じ線 —
        1 件でも見つかっていれば「未解決がある」は取り切れなくても真なので、`[]` に落とさない
        情報のほうが多い。**取り切れていないのに `[]` を返さない**のがこの分岐の要点。
        """
        raw = self._json(
            [
                "glab", "api",
                f"projects/{scope}/merge_requests/{number}/discussions?per_page={PAGE_SIZE}",
            ]
        )
        if not _is_object_list(raw):
            # 応答が列でない (MR が消えた / 権限が無い) を空として返さない — 「観測して 0 件」
            # に化ける。**要素の形が違うときも同じ扱い**にするのは、列だけ確かめて中身を舐めると
            # `AttributeError` が観測経路の外へ抜け、ここが守っている「未観測」の区別ごと
            # 失われるため
            return None
        unresolved = [
            discussion["id"]
            for discussion in raw
            if _is_unresolved_review_thread(discussion)
        ]
        if len(raw) == PAGE_SIZE and not unresolved:
            return None
        return unresolved

    # --- 起動 -------------------------------------------------------------------

    def _json(self, argv):
        """glab を起動して stdout を JSON として読む。非 0 exit / 非 JSON は ObservationError。"""
        return adapter_cli.json_output(self._run, argv, cli=TRACKER)


# --- project scope ---------------------------------------------------------------


def _repo_flag(repo):
    """`glab issue` 系の `--repo`。**未指定は許さない** (`_project_scope` と同じ理由)。

    受ける綴りは `OWNER/REPO` / `GROUP/NAMESPACE/REPO` (`glab issue list --help`)。
    """
    return ["--repo", _require_project(repo)]


def _project_scope(repo):
    """`glab api` の path 用 project (`glab api` は `--repo` を受けないので path 側で示す)。

    GitLab の project は数値 id か **URL-encode した full path** でしか指せない。
    `group/project` を生のまま埋めると path の階層が 1 段増えて 404 になる。
    """
    return urllib.parse.quote(_require_project(repo), safe="")


def _require_project(repo):
    """project 識別子を検証して返す。空なら CLI を起動する前に ObservationError。

    **gh の `--repo` 省略 (cwd 推論) に相当する fallback を置かない**。GitLab は project を
    path に埋めるので、未指定を `:id` placeholder へ倒すと「同じ iid の別 project の MR」を
    黙って答える。観測できないことにするほうが安い。
    """
    identifier = str(repo or "").strip()
    if not identifier:
        raise ports.ObservationError(
            f"project 識別子が空 ({repo!r}): glab は project を path に埋めるので、"
            "未指定のまま撃つと別 project の同 iid を観測する"
        )
    return identifier


# --- 正規化 ---------------------------------------------------------------------


def _issue_number(issue_ref):
    """中立 issue ref → glab の issue iid。glab 以外の ref は ObservationError。"""
    return adapter_cli.issue_number(issue_ref, tracker=TRACKER)


def _cl_number(cl_ref):
    """中立 CL ref → glab の MR iid。glab 以外の ref は ObservationError。"""
    return adapter_cli.cl_number(cl_ref, tracker=TRACKER)


def _issue_fact(raw, observed_at):
    state = _ISSUE_STATES.get(str(raw.get("state")).lower())
    if state is None:
        raise ports.ObservationError(f"未知の glab issue state: {raw.get('state')!r}")
    return ports.issue_fact(
        issue_ref=refs.format_issue_ref(TRACKER, _iid(raw, "issue")),
        state=state,
        observed_at=observed_at,
    )


def _cl_state(raw):
    state = _CL_STATES.get(str(raw).lower())
    if state is None:
        raise ports.ObservationError(f"未知の glab MR state: {raw!r}")
    return state


def _mergeable(raw):
    """MR payload → 中立語彙 3 値。**gh と違って引数は生値でなく payload ごと**。

    mergeability の答えは 2 つの field に分かれる: `detailed_merge_status` (GitLab 15.6+) と、
    それが入る前の `merge_status`。前者を先に読み、**前者の表に無い値は後者へ落ちる**
    (「merge できない理由」は多数あるのに対し、中立語彙が答えるのは conflict の有無だけなので、
    表に載るのはその 3 つに写せる値に限られる)。

    **どちらも読めなければ `UNKNOWN`** — `MERGEABLE` へ倒さないのは「conflict 無し」と
    読ませないため。GitLab が語彙を増やしたとき、観測は「まだ分からない」側に留まる。
    """
    detailed = _DETAILED_MERGE_STATUSES.get(
        str(raw.get("detailed_merge_status") or "").lower()
    )
    if detailed is not None:
        return detailed
    return _MERGE_STATUSES.get(str(raw.get("merge_status") or "").lower(), "UNKNOWN")


def _is_object_list(raw):
    """応答が「object の列」か。**要素まで見る**のがこの関数の理由。

    列であることだけ確かめて中身を舐めると、`.get` が `AttributeError` を投げて観測経路の
    外 (`_tick_one` の project 隔離) まで浮上する — その project の観測が「観測できなかった」
    という分類も連続失敗の数え上げ (rule #9) も経ずに、汎用の例外名で丸ごと落ちる。

    **3 つの列読み経路 (候補 / 紐づき / discussion) で同じ判定を使う**。1 経路だけ厳しくすると、
    同じ形の応答が経路によって観測失敗になったり例外で抜けたりする。
    """
    return isinstance(raw, list) and all(isinstance(item, dict) for item in raw)


def _link_key(merge_request):
    """紐づき応答 1 件 → `(project, iid)`。読めなければ ObservationError。"""
    return _merge_request_project(merge_request), _iid(merge_request, "MR")


def _iid(raw, subject):
    """payload から iid を読む。読めなければ ObservationError。

    素の添字だと `KeyError` が観測経路の外へ漏れ、tick 全体を落として「観測できなかった」
    という分類も失う。
    """
    iid = raw.get("iid")
    if not isinstance(iid, int):
        raise ports.ObservationError(f"{subject} 番号を読めない: {raw!r}")
    return iid


def _merge_request_project(raw):
    """MR payload → 中立 schema の `repo` (数値 project id の文字列)。

    gh の `owner/name` と型を揃えるため文字列にする。**宣言 (`[pr] repo`) が full path なら
    この値と綴りが揃わない**が、`_project_scope` は数値 id も full path も同じように
    URL-encode して受けるので、観測の経路はどちらでも通る。

    「どの project の MR か」を読めないまま返すと、`fetch_cl` が宣言側 project の同 iid の
    MR を引いて別の MR を答える。
    """
    project_id = raw.get("project_id")
    if project_id is None:
        raise ports.ObservationError(f"MR から project を読めない: {raw!r}")
    return str(project_id)


def _is_unresolved_review_thread(discussion):
    """discussion 1 件が「未解決の review thread」か。

    system note は `resolvable` が false で来る instance しか観測できていないので、判定は
    resolvable 側に持たせたまま `system` を保険として重ねる。
    """
    notes = discussion.get("notes") or []
    if not notes:
        return False
    head = notes[0]
    return bool(head.get("resolvable")) and not head.get("system") and not head.get("resolved")
