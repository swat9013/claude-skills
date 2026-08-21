"""TrackerPort — issue tracker (GitHub / GitLab) への観測・操作の継ぎ目。

spec §2: **adapter の責務は語彙の写像のみ**。「conflict なら何をすべきか」は返さない。
本 module は port (中立 API) と tracker 非依存の写像規則を持ち、tracker 固有の綴り
(CLI 呼び出し・生値の解釈・remote host) は `tracker_gh` / `tracker_glab` が実装する。
core が持つのは中立語彙の正本と、adapter を引く登録表 (`_ADAPTERS`) だけ。

語彙の層が 2 つあるのが要点:

- **内部語彙** (`OPEN` / `MERGED` / `CONFLICTING`): adapter と `derive_pr_status` の間だけで
  使う。既存 `dispatch_tracker.py` から実証済みロジックを移植するにあたり、この層を
  書き換えないことで「実機で検証済み」という移植の価値を保つ
- **中立語彙** (`vocabulary` / `refs`): tool 境界を越える値だけがこちらへ写る
  (issue state `open`/`closed`、PR status 6 値、PR role `closes`/`mention`、ref `gh#386`/`gh!401`)

gh / glab CLI へ shell out し、認証は CLI へ委譲する (spec §4.2)。CLI の非 0 exit は
`TrackerError` として即座に表面化させる — 「CLI の実行失敗」を「blocker 無し」「PR 無し」と
誤読させないため (i217 誤 dispatch の真因)。

観測・操作の対象 repo は公開 method の `repo` 引数で明示できる (ADR 0036)。**本 module は
どの repo を見るかを決めない** — 識別子は受け取ったものをそのまま CLI の scope にするだけで、
project の宣言 (どこに issue / PR が置いてあるか) を解決するのは `project` module の責務。
未指定なら adapter は識別子を CLI へ足さず、対象 repo の推論を CLI (cwd の remote) へ委ねる。
"""

import importlib
import json
import re
from datetime import datetime, timezone

import proc
import refs
import vocabulary

SUBPROCESS_TIMEOUT_SEC = 60

# `Blocked by: #N` 行 (dependencies 機能を持たない repo の fallback)
BLOCKED_LINE = re.compile(r"^\s*blocked[ -]?by\b[:\s]*(.*)$", re.IGNORECASE | re.MULTILINE)

# --- 内部語彙 (adapter の中だけ) ------------------------------------------------

_STATE_MAP = {"open": "OPEN", "opened": "OPEN", "closed": "CLOSED"}
_PR_STATE_MAP = {"open": "OPEN", "opened": "OPEN", "closed": "CLOSED", "merged": "MERGED"}
# mergeable の内部語彙。生値の綴りは tracker ごとに違う (gh は同名の enum 文字列、glab は
# MR payload の複数 field) ので、**生値を読むのは adapter の責務**で core が持つのは 3 値の
# 正本だけ。tracker 差を status 導出まで持ち込まない
MERGEABLE_VALUES = frozenset({"MERGEABLE", "CONFLICTING", "UNKNOWN"})

# 内部 issue state → 中立語彙。vocabulary.ISSUE_STATES が正本
_NEUTRAL_ISSUE_STATE = {"OPEN": "open", "CLOSED": "closed"}

# derive_pr_status が返しうる値。梯子の段を増やしたのに vocabulary.PR_STATUSES へ
# 足し忘れる (逆も同じ) を import 時に落とすための控え
_DERIVED_PR_STATUSES = frozenset(
    {"none", "conflict", "merged", "checking", "open", "closed"}
)

# --- observe_issues の引数語彙 (spec §4.4) --------------------------------------

ISSUE_LIST_STATES = ("open", "closed", "all")

# PR role。adapter が自前の文字列を書いて vocabulary と食い違うのを防ぐため、正本から
# 名前付きで取り出して adapter はこれを使う
ROLE_CLOSES, ROLE_MENTION = vocabulary.PR_ROLES
ORDERINGS = ("updated", "created", "number")
# assignee filter の sentinel。login を渡せばその人の担当分、sentinel は担当有無で絞る
ASSIGNEE_NONE = "none"
ASSIGNEE_ANY = "any"

DEFAULT_ISSUE_LIMIT = 200
DEFAULT_PR_LIMIT = 200


class TrackerError(RuntimeError):
    """gh / glab CLI の失敗、または tracker 語彙の逸脱。"""


# tracker CLI (gh / glab) の起動境界。テストはこの名前を monkeypatch する
run_command = proc.command_runner(error=TrackerError, timeout_sec=SUBPROCESS_TIMEOUT_SEC)


def run_json(argv):
    """CLI を起動して stdout を JSON として読む。非 0 exit / 非 JSON は TrackerError。"""
    rc, out, err = run_command(argv)
    if rc != 0:
        raise TrackerError(f"{argv[0]} failed (exit {rc}): {err.strip()}")
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise TrackerError(f"{argv[0]} returned non-JSON stdout: {exc}") from exc


def run_checked(argv):
    """JSON を返さない書き込み系 CLI。非 0 exit を握り潰さない。"""
    rc, _out, err = run_command(argv)
    if rc != 0:
        raise TrackerError(f"{' '.join(argv[:3])} failed (exit {rc}): {err.strip()}")


# --- 正規化 -------------------------------------------------------------------


def label_names(labels):
    """label 表現 (dict / str の混在) を名前の列に均す。"""
    return [
        label["name"] if isinstance(label, dict) else str(label)
        for label in (labels or [])
    ]


def normalize_state(raw):
    """issue state → 内部語彙。未知値は推測に倒さず TrackerError。

    OPEN / CLOSED のどちらかへ勝手に倒すと、誤回収 (open を closed と読む) や
    誤 unclaim に直結する。
    """
    state = _STATE_MAP.get(str(raw).lower())
    if state is None:
        raise TrackerError(f"未知の issue state: {raw!r}")
    return state


def normalize_pr_state(raw):
    """PR / MR state → 内部語彙。未知値は TrackerError (merged を open と読むと観測が逆になる)。"""
    state = _PR_STATE_MAP.get(str(raw).lower())
    if state is None:
        raise TrackerError(f"未知の PR state: {raw!r}")
    return state


def require_mergeable(value):
    """adapter が返した mergeable が内部語彙の 3 値かを検査して返す。逸脱は TrackerError。

    生値の解釈は adapter が持つが、**綴りの正本は core** に置く。adapter が独自の綴りを
    返すと梯子の `== "CONFLICTING"` が黙って外れ、conflict が open (= 人手不要) に見える。
    """
    if value not in MERGEABLE_VALUES:
        raise TrackerError(
            f"未知の mergeable: {value!r} "
            f"(adapter が返せるのは {', '.join(sorted(MERGEABLE_VALUES))})"
        )
    return value


def normalize_timestamp(value):
    """tracker の timestamp を UTC の ISO8601 (`...Z`) に揃える。

    `updated_since` の比較と ordering を文字列比較で行うため、tracker ごとの表記差
    (`Z` / `+09:00` / 小数秒) をここで吸収する。読めない値はそのまま返す — 比較が
    近似になるだけで、握り潰して None にするより情報が残る。
    """
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def parse_blocked_refs(body):
    """body の `Blocked by: #N` 行から参照 issue 番号を抜く (行内の複数 #N も拾う)。"""
    found = []
    for match in BLOCKED_LINE.finditer(body or ""):
        found.extend(int(n) for n in re.findall(r"#(\d+)", match.group(1)))
    return found


# --- PR status の梯子 (dispatch_tracker.derive_pr_status からの移植) ----------------


def derive_pr_status(prs):
    """紐づく PR 列 → 1 語の中立 status。actionable-first の梯子で最初に当たった段を返す。

    conflict を merged より先に見る: 両方あるのは stacked PR で、人手が要るのは
    conflict 側。merged を先に見ると「完了した」と読めて conflict が報告から消える。
    mergeable が UNKNOWN の open PR は checking (計算中) — open (conflict 無し) と
    混ぜない。push 直後の観測は日常的に UNKNOWN を踏むため、混ぜると誤って
    「conflict 無し」と報告する。

    引数の PR は内部語彙 (`state` = OPEN/MERGED/CLOSED、`mergeable` = 3 値)。
    返り値は中立語彙 (`vocabulary.PR_STATUSES`)。mergeable は梯子を掛ける前に検査する —
    生値を読むのは adapter なので、語彙の逸脱を落とせる最後の地点がここになる。
    """
    if not prs:
        return "none"
    for pr in prs:
        require_mergeable(pr["mergeable"])
    open_prs = [pr for pr in prs if pr["state"] == "OPEN"]
    if any(pr["mergeable"] == "CONFLICTING" for pr in open_prs):
        return "conflict"
    if any(pr["state"] == "MERGED" for pr in prs):
        return "merged"
    if any(pr["mergeable"] == "UNKNOWN" for pr in open_prs):
        return "checking"
    if open_prs:
        return "open"
    return "closed"


def pr_status(pr):
    """PR 1 件の中立 status。集約と同じ梯子を単一要素に当てるので判定が割れない。"""
    return derive_pr_status([pr])


def derive_closes(neutral_prs):
    """中立 PR の union → `role == "closes"` の部分列 (issue #608 の派生列)。

    集約 `status` は closes 限定なのに `count` / `prs[]` は mention 込みの union なので、
    呼び出し側は「status の根拠になった PR はどれか」を毎回 role で filter し直していた。
    同じ filter を観測側で 1 度だけ行って**併記する** — union は削らないので「現実のまま
    返す」原則は崩れず、集約 status と同じ母集団が列としても読める。

    載せるのは判定に使う 3 列だけ (`resolve` の派生列と同じ射影)。観測をそのまま複製すると
    同じ応答に PR が 2 度載るので、title / url が要る報告は `prs` を `ref` で引き直す。

    role が定義されない経路 (issue 文脈の無い repo 全体の一覧) では呼ばない。`[]` を返すと
    「closes が 1 件も無い」と読めるが、実際には役割を判定していない。
    """
    return [
        {"ref": pr.get("ref"), "repo": pr.get("repo"), "status": pr.get("status")}
        for pr in neutral_prs
        if pr.get("role") == "closes"
    ]


# --- adapter の登録 -------------------------------------------------------------
#
# **どの源の宣言が勝つか (解決順) は `project` module が持つ**。本 module が持つのは
# 「どの tracker にどんな実装と綴りが対応するか」の登録表で、tracker を 1 つ足す作業は
# ここに entry を 1 行足すことに閉じる (`refs.PATTERNS` と同じ登録式)。
#
# 登録表を経由させるのは、adapter 固有の綴りを core に書き戻させないため。remote host の
# 綴り (`github.com`) を core の表に持つと、tracker を足すたびに core が編集される。

# tracker 名 → (module 名, class 名)。lazy import は保つが、host 綴りを引くときだけは
# 全 adapter を import する (綴りは class 属性なので実体が要る)
_ADAPTERS = {"gh": ("tracker_gh", "GhAdapter"), "glab": ("tracker_glab", "GlabAdapter")}


def _adapter_class(tracker):
    module_name, class_name = _ADAPTERS[tracker]
    return getattr(importlib.import_module(module_name), class_name)


def get_adapter(tracker):
    """tracker 名 → adapter。未実装 tracker は継ぎ目であることを名指しで失敗させる。

    `refs.TRACKERS` に居るのに adapter が無い (jira) という非対称を、黙って
    「未知の tracker」に丸めず別のメッセージで表す — spec §8 の「port 境界だけ設計した」を
    実行時に反証可能な形で残すため。
    """
    if tracker in _ADAPTERS:
        return _adapter_class(tracker)()
    if tracker in refs.TRACKERS:
        raise TrackerError(
            f"{tracker} adapter は未実装 — port 境界 (継ぎ目) のみを設計してある (spec §8)"
        )
    raise TrackerError(f"未知の tracker: {tracker!r} (候補: {', '.join(refs.TRACKERS)})")


def tracker_for_remote(remote_output):
    """`git remote -v` の出力 → tracker 名 (どの adapter の host 綴りにも当たらなければ None)。

    綴りを持つのは adapter (`TrackerPort.remote_hosts`) で、本関数は登録順に照合するだけ。
    **判定に使うだけで、この経路を使うかどうかは `project` の解決順が決める**。
    """
    for tracker_name in _ADAPTERS:
        for host in _adapter_class(tracker_name).remote_hosts:
            if host in remote_output:
                return tracker_name
    return None


# --- port ------------------------------------------------------------------------


class TrackerPort:
    """issue tracker への中立 API。tracker 固有の呼び出しは下の継ぎ目 method が担う。

    公開 method (`observe_*` / `issue_*`) は中立語彙だけを受け渡しし、継ぎ目 method
    (`fetch_*` / `linked_prs` 等) は内部語彙のまま扱う。新しい tracker を足すときに
    書くのは継ぎ目 method と綴りの宣言 (`tracker` / `remote_hosts`) で、core 側は
    `_ADAPTERS` の 1 行だけ。

    継ぎ目 method はすべて対象 repo の識別子 (`repo`) を最後の引数で受ける。`None` は
    「呼び出し側が指定しなかった」であって「自 repo」ではない — adapter は識別子を CLI へ
    足さず、対象 repo の推論を CLI 側へ残す。
    """

    tracker = None

    # git remote の URL に現れる host 綴り。`tracker_for_remote` が宣言の無い環境の
    # fallback 判定に使う。**綴りを adapter に持たせるのは、tracker を足す作業を
    # adapter 1 file に閉じるため** (core の表に書き戻すと足すたびに core が動く)。
    # self-hosted instance を含むので部分一致で照合する (`gitlab.example.com`)
    remote_hosts = ()

    # tracker 側の 1 回取得上限 (None = 上限なし)。要求 limit がこれを超えるとき、
    # `truncated` は **実際に投げた件数** と比べないと必ず false になる (取れなかった
    # ぶんを「全部取れた」と報告してしまう)
    max_fetch = None

    # 明示 repo scope に対応するか。False の adapter は `repo` を渡された時点で CLI を
    # 起動する前に失敗する — 識別子を無視して cwd repo を観測・操作すると、宣言と別の
    # repo へ claim / label が飛ぶ (i217 誤 dispatch と同型で最も高くつく取り違え)。
    # **宣言の既定注入もこの値を見る** (server 側の `injectable_repo`) — 未対応の adapter へ
    # 宣言値が流れ込むと tracker 系 tool が全滅するので、出所を名指しして落とす (#620)
    supports_repo_scope = True

    # `pr_detail` が repo 識別子を必須とするか。識別子を path へ埋める adapter (glab の
    # `projects/{repo}/...`) は cwd 推論へ倒れられないので、None のまま撃つと存在しない
    # path を叩く。`observe_pr_refs` が撃つ前に名指しで落とすための宣言
    pr_detail_requires_repo = False

    # PR の review thread を観測・resolve できるか (ADR 0039)。**既定は False で、実装した
    # adapter だけが名乗る**。未対応の adapter は `require_review_threads` が撃つ前に名指しで
    # 落ちる — 空配列を返すと「未解決 thread が 0 件」= レビュー対応済みと読まれ、駐機した
    # worker が再入されないまま静かに滞留する (`blocked: null` と `false` を分ける規則と同じ)
    supports_review_threads = False

    # --- 継ぎ目 (adapter が実装する) -------------------------------------------

    def fetch_issues(self, state, limit, repo):
        """issue 一覧を内部語彙の dict 列で返す (number / title / state / labels /
        assignees / created_at / updated_at / url)。"""
        raise NotImplementedError

    def fetch_issue(self, number, repo):
        """issue 1 件を内部語彙の dict で返す (`fetch_issues` の要素と同じ形)。"""
        raise NotImplementedError

    def fetch_blocked(self, number, repo):
        """open blocker 検査 → {"blocked", "open_blockers", "source"}。
        open_blockers は {"number", "title"} の列 (open のものだけ)。"""
        raise NotImplementedError

    def linked_prs(self, number, repo):
        """issue → 紐づく PR の列 ({"number", "role", "repo"})。

        role は `closes` / `mention`、`repo` は **その PR が居る repo** の識別子
        (issue の repo とは限らない)。同じ PR の重複は adapter が closes 優先で潰す。
        """
        raise NotImplementedError

    def pr_detail(self, number, role, repo):
        """PR 1 件 → 内部語彙 dict (number / state / mergeable / title / url / role / repo)。

        `repo` は `linked_prs` が返した PR 自身の repo で、issue の repo ではない。
        """
        raise NotImplementedError

    def open_prs(self, limit, repo):
        """repo の open PR → 内部語彙 dict の列 (+ head_branch / closes_issues / repo)。"""
        raise NotImplementedError

    def set_assignee(self, number, action, repo):
        """assignee を設定 (`claim`) / 解除 (`unclaim`) する。"""
        raise NotImplementedError

    def post_comment(self, number, body, repo):
        """issue にコメントを投稿する。"""
        raise NotImplementedError

    def edit_labels(self, number, add, remove, repo):
        """label を付け外しする。実行した CLI 呼び出しの数を返す。"""
        raise NotImplementedError

    def fetch_review_threads(self, number, repo):
        """PR 1 件の review thread → {"threads", "truncated"}。

        `threads` は `{"id", "resolved"}` の列 (id は tracker が発行する thread 識別子で、
        resolve 操作にそのまま渡せるもの)。`truncated` は 1 回で取り切れなかったこと
        (取り切れたのに false を返すと「未解決 0 件」を無条件に信じてよいと読まれる)。
        """
        raise NotImplementedError

    def resolve_review_thread(self, thread_id):
        """review thread 1 件を resolve する → {"id", "resolved"} (操作後の観測)。"""
        raise NotImplementedError

    # --- repo scope -------------------------------------------------------------

    def require_repo_scope(self, repo):
        """明示 repo scope の可否を CLI 起動前に確かめる。

        未対応の adapter が識別子を黙って捨てると cwd repo へ倒れるので、名指しで
        失敗させる (`get_adapter` の未実装 tracker と同じ「継ぎ目を反証可能に残す」形)。
        """
        if repo is not None and not self.supports_repo_scope:
            raise TrackerError(
                f"{self.tracker} adapter は明示 repo scope 未実装 — 継ぎ目のみを設計してある "
                f"(受け取った識別子: {repo!r})"
            )

    # --- ref ヘルパ -------------------------------------------------------------

    def issue_ref(self, number):
        return refs.format_issue_ref(self.tracker, number=number)

    def pr_ref(self, number):
        return refs.format_pr_ref(self.tracker, number)

    def issue_number(self, issue_ref):
        """中立 issue ref → 自 tracker の番号。別 tracker の ref は TrackerError。"""
        parsed = refs.parse_issue_ref(issue_ref)
        if parsed["tracker"] != self.tracker:
            raise TrackerError(
                f"{issue_ref} は {parsed['tracker']} の ref だが、本 server の tracker は "
                f"{self.tracker}"
            )
        if parsed["number"] is None:
            raise TrackerError(f"{issue_ref} は番号を持たない ref")
        return parsed["number"]

    # --- observe ----------------------------------------------------------------

    def observe_issues(
        self,
        *,
        state="open",
        limit=DEFAULT_ISSUE_LIMIT,
        labels_any=None,
        labels_none=None,
        assignee=None,
        updated_since=None,
        ordering=None,
        descending=False,
        include_blocked=False,
        repo=None,
    ):
        """issue の生データを中立 schema で返す (spec §4.4)。

        filter / ordering はすべて任意。未指定なら絞らず並べ替えず、どれが着手可かは
        LLM が生データを読んで決める (spec §2)。`state` と `limit` だけ CLI へ押し下げ、
        残りは取得後に本 method が適用する — `--label` の AND 意味 (`labels_any` と
        食い違う) や `labels_none` に対応する flag の不在を写像で誤魔化さないため。

        `include_blocked` を立てたときだけ blocker を検査する。**未検査は
        `blocked: null`** で返し、空配列にしない — 「検査して blocker 無し」と
        読めてしまう形は i217 誤 dispatch と同型の誤読を作る。検査は 1 issue あたり
        1 回以上 CLI を起動するので、filter で絞ってから立てる。

        `repo` を渡すと issue 一覧も blocker 検査もその repo に向く。返り値の `repo` は
        受け取った識別子の echo (未指定なら null) で、宣言と実際の観測先が食い違って
        いないかは呼び出し側がこれで確かめる。
        """
        self.require_repo_scope(repo)
        if state not in ISSUE_LIST_STATES:
            raise TrackerError(
                f"未知の state: {state!r} (候補: {', '.join(ISSUE_LIST_STATES)})"
            )
        if ordering is not None and ordering not in ORDERINGS:
            raise TrackerError(
                f"未知の ordering: {ordering!r} (候補: {', '.join(ORDERINGS)})"
            )
        limit = _require_limit(limit)
        effective = self._effective_limit(limit)

        fetched = self.fetch_issues(state, effective, repo)
        selected = [
            issue
            for issue in fetched
            if _passes_filter(issue, labels_any, labels_none, assignee, updated_since)
        ]
        if ordering is not None:
            selected.sort(key=lambda issue: _order_key(issue, ordering), reverse=descending)

        entries = [self._neutral_issue(issue, include_blocked, repo) for issue in selected]
        return {
            "tracker": self.tracker,
            "repo": repo,
            "count": len(entries),
            "fetched": len(fetched),
            # 取得上限に張り付いた = tracker 側にまだ残っている可能性がある。件数を
            # 黙って切らないための印 (limit を上げて呼び直すか、filter を絞る)。
            # 比較先は要求 limit ではなく実際に投げた effective_limit
            "truncated": len(fetched) >= effective,
            "state": state,
            "limit": limit,
            "effective_limit": effective,
            "filter": {
                "labels_any": list(labels_any) if labels_any else None,
                "labels_none": list(labels_none) if labels_none else None,
                "assignee": assignee,
                "updated_since": updated_since,
            },
            "ordering": ordering,
            "descending": bool(descending) if ordering is not None else False,
            "blocked_checked": bool(include_blocked),
            "issues": entries,
        }

    def observe_issue(self, issue_ref, repo=None):
        """issue 1 件を中立 schema で返す (`resolve` の join 用)。

        台帳 entry の現況を `observe_issues` の窓から拾おうとすると、`limit` の外へ
        こぼれた issue を「無い」と読むことになる (repo の issue 番号は窓を越えて増える)。
        番号で直接引く経路を分けてあるのはそのため。blocker 検査は join に要らないので
        しない (未検査は `blocked: null` のまま返る)。
        """
        self.require_repo_scope(repo)
        return self._neutral_issue(
            self.fetch_issue(self.issue_number(issue_ref), repo), False, repo
        )

    def observe_prs(self, issue_ref=None, limit=DEFAULT_PR_LIMIT, repo=None, issue_tracker=None):
        """PR を観測する (spec §4.4)。

        `issue_ref` を渡すとその issue に紐づく PR と 1 語の集約 status を返す。
        省略すると repo の open PR を closing issue ref 付きで返す — 台帳を参照して
        補完すると port が台帳に依存してしまうため、ここでは純粋な tracker 観測に
        留める (台帳との突き合わせは resolve の領分 = #406)。

        集約 `status` は `role == "closes"` の PR だけから算出する。`count` / `prs[]` は
        closes + mention の union なので、mention しか無い issue は `status: "none"` かつ
        `count: 1` になる。role を混ぜると mention の merged が closes の open を隠し、
        未 merge のまま駐機 worktree を回収する経路に乗る (#445)。`resolve` の
        `pr_merged` drift も closes 限定なので、同じ問いへの答えを 1 つに保つ。

        **その closes の部分列を `closes` に併記する** (#608)。`status` の母集団を呼び出し側が
        role で filter し直さずに読めるようにする派生列で (`ref` / `repo` / `status` の射影)、
        union (`prs` / `count`) は従来どおり併記される。role が定義されない経路
        (issue 文脈の無い repo 全体の一覧) では `null` —
        `[]` にすると「closes が 1 件も無い」と読めるが、実際には役割を判定していない。

        **紐づく PR は repo で絞らない** (ADR 0036)。worker が関連 repo へ出した PR は
        cross-repo の closing reference で issue に紐づくので、自 repo 以外を落とすと
        正当な closes を mention 扱いへ落として駐機判定と merge 検知を壊す。代わりに
        各 PR の `repo` (その PR が居る repo) を schema に載せて現実のまま返す — その
        帰結として **fork から張られた `Closes` も closes として集約 `status` に効く**。
        どの repo の PR を根拠に採るかは `prs[].repo` を読んで呼び出し側が決める。

        `limit` が効くのは repo 全体を見る経路だけ。issue に紐づく PR は issue 側で
        件数が閉じているので切らない (`truncated` は常に false)。トップレベルの `repo`
        は問い合わせ先 repo の echo (未指定なら null) で、`prs[].repo` とは別物。

        `issue_tracker` は **issue 置き場**の tracker (未指定なら自 tracker と同じと見なす)。
        PR 置き場と別なら、repo 全体の一覧に載る closing reference は中立 issue ref へ写せない
        ので `closes_issues` に混ぜず `closes_unmappable` へ落とす (#576)。
        """
        self.require_repo_scope(repo)
        limit = _require_limit(limit)
        if issue_ref is None:
            effective = self._effective_limit(limit)
            prs = self.open_prs(effective, repo)
            return {
                "tracker": self.tracker,
                "repo": repo,
                "issue_ref": None,
                # issue 文脈が無いので集約 status は定義されない (role も同様)
                "status": None,
                "count": len(prs),
                "limit": limit,
                "effective_limit": effective,
                "truncated": len(prs) >= effective,
                "prs": [
                    self._neutral_pr(pr, with_links=True, issue_tracker=issue_tracker)
                    for pr in prs
                ],
                # role が定義されないので closes の部分列も作れない ([] は「1 件も無い」の意)
                "closes": None,
                "unmappable_prs": [],
            }
        number = self.issue_number(issue_ref)
        links = sorted(
            self.linked_prs(number, repo), key=lambda link: (link["repo"], link["number"])
        )
        prs = [
            self.pr_detail(link["number"], link["role"], link["repo"]) for link in links
        ]
        closing_prs = [pr for pr in prs if pr["role"] == "closes"]
        neutral = [self._neutral_pr(pr) for pr in prs]
        return {
            "tracker": self.tracker,
            "repo": repo,
            "issue_ref": refs.parse_issue_ref(issue_ref)["ref"],
            "status": derive_pr_status(closing_prs),
            "count": len(prs),
            "limit": None,
            "effective_limit": None,
            "truncated": False,
            "prs": neutral,
            "closes": derive_closes(neutral),
            "unmappable_prs": [],
        }

    def observe_pr_refs(self, records, *, issue_ref=None, repo=None):
        """**台帳が記録した PR** を (repo, ref) の組で観測する (`observe_prs` と同じ schema)。

        issue 置き場が PR 置き場と別 tracker (issue = Jira / PR = GitLab) のとき、issue から
        PR を引く経路が無い — closing reference は PR 置き場の番号空間にしか綴りが無く、
        Jira の課題を指せない。**そのとき現況の観測の種になるのは台帳の記録だけ**なので、
        記録した `{ref, role, repo}` から直接 PR を引く経路をここに置く (#576)。

        `role` は台帳の記録をそのまま採る。どの PR が closes かを知っているのが台帳しかない
        以上、**記録が誤っていれば mention の merged も `pr_merged` として出る** — 記録の
        正しさは記帳側 (`ledger_record` / `ledger_transition`) の責務。

        `repo` は記録に無いときの既定。`require_repo_scope` を掛けないのは、この経路の識別子が
        CLI の scope flag ではなく `pr_detail` が PR 自身の在り処として受ける引数だから
        (glab の `projects/{repo}/...` は別 project にも届く)。代わりに識別子を必須とする
        adapter には、撃つ前に名指しで落とす (`pr_detail_requires_repo`)。

        自 tracker で観測できない記録 (別 tracker の PR ref / 識別子が無い) は黙って落とさず
        `unmappable_prs` に残す。
        """
        prs, unmappable = [], []
        for record in records or []:
            detail, reason = self._pr_from_record(record, repo)
            if reason is not None:
                unmappable.append(
                    {"ref": record.get("ref"), "repo": record.get("repo"), "reason": reason}
                )
                continue
            prs.append(detail)
        closing_prs = [pr for pr in prs if pr["role"] == "closes"]
        neutral = [self._neutral_pr(pr) for pr in prs]
        return {
            "tracker": self.tracker,
            "repo": repo,
            "issue_ref": refs.parse_issue_ref(issue_ref)["ref"] if issue_ref else None,
            "status": derive_pr_status(closing_prs),
            "count": len(prs),
            "limit": None,
            "effective_limit": None,
            "truncated": False,
            "prs": neutral,
            "closes": derive_closes(neutral),
            "unmappable_prs": unmappable,
        }

    def _pr_from_record(self, record, repo):
        """台帳の PR 記録 1 件 → (観測した内部語彙 dict, 観測できない理由)。"""
        ref = record.get("ref")
        try:
            parsed = refs.parse_pr_ref(ref)
        except refs.RefError as exc:
            return None, str(exc)
        if parsed["tracker"] != self.tracker:
            return None, (
                f"{ref} は {parsed['tracker']} の PR ref で、PR 置き場の tracker "
                f"({self.tracker}) では観測できない"
            )
        scope = record.get("repo") if record.get("repo") is not None else repo
        if scope is None and self.pr_detail_requires_repo:
            return None, (
                f"{self.tracker} は PR 1 件の観測に repo 識別子が要る "
                "(台帳の prs[].repo か pr_repo に渡す)"
            )
        return self.pr_detail(parsed["number"], record.get("role"), scope), None

    # --- review thread (ADR 0039) --------------------------------------------------

    def require_review_threads(self):
        """review thread の観測・resolve の可否を CLI 起動前に確かめる。

        **未対応を空 (未解決 0 件) で表さない。** 「観測していない」と「観測して未解決が
        0 件」は読み手にとって正反対の意味を持つ — 後者はレビュー対応済みと読まれ、
        駐機した worker を再入させないまま滞留させる。`get_adapter` の未実装 tracker と
        同じく、継ぎ目であることを名指しで失敗させる。
        """
        if not self.supports_review_threads:
            raise TrackerError(
                f"{self.tracker} adapter は review thread の観測・resolve が未実装 — "
                "**未解決 0 件として扱わない** (「観測していない」を「レビュー対応済み」と"
                "読むと、指摘の付いた PR が駐機したまま滞留する)"
            )

    def observe_review_threads(self, records, *, repo=None):
        """台帳が持つ PR 記録の列 → PR ごとの review thread 観測。

        `records` は `{"ref", "repo"}` を持つ dict の列 (`observe_prs` の `prs[]` や台帳の
        `prs[]` をそのまま渡せる)。`repo` は記録に `repo` が無いときの既定。

        観測できない記録 (別 tracker の PR ref / repo 識別子が無い) は黙って落とさず
        `unmappable_prs` に理由付きで残す — `observe_pr_refs` と同じ規則で、**取り落としを
        「未解決 0 件」に化けさせない**ため。

        thread の中身 (指摘の本文) は返さない。ここで要るのは「未解決が在るか」と
        「閉じるための id」だけで、本文は対応する worker が PR を読めばよい (`resolve` の
        応答は台帳の全 entry 分が積み上がる)。
        """
        self.require_review_threads()
        targets, unmappable = [], []
        for record in records or []:
            ref = record.get("ref")
            target, reason = self._review_thread_target(record, repo)
            if reason is not None:
                unmappable.append({"ref": ref, "repo": record.get("repo"), "reason": reason})
                continue
            targets.append(target)
        observed = []
        for target in targets:
            found = self.fetch_review_threads(target["number"], target["repo"])
            observed.append(
                {
                    "ref": target["ref"],
                    "repo": target["repo"],
                    "threads": found["threads"],
                    "truncated": found["truncated"],
                }
            )
        return {
            "tracker": self.tracker,
            "repo": repo,
            "count": len(observed),
            "prs": observed,
            "unmappable_prs": unmappable,
        }

    def _review_thread_target(self, record, repo):
        """PR 記録 1 件 → (問い合わせ先, 観測できない理由)。観測できるなら理由は None。"""
        try:
            parsed = refs.parse_pr_ref(record.get("ref"))
        except refs.RefError as exc:
            return None, str(exc)
        if parsed["tracker"] != self.tracker:
            return None, (
                f"{record.get('ref')} は {parsed['tracker']} の PR ref で、PR 置き場の tracker "
                f"({self.tracker}) では観測できない"
            )
        scope = record.get("repo") if record.get("repo") is not None else repo
        if scope is None:
            return None, (
                f"{self.tracker} は review thread の観測に repo 識別子が要る "
                "(台帳の prs[].repo か pr_repo に渡す)"
            )
        return {"ref": parsed["ref"], "repo": scope, "number": parsed["number"]}, None

    def review_thread_resolve(self, thread_id):
        """review thread 1 件を resolve する (対応した worker 自身が閉じる — ADR 0039)。

        返す `resolved` は**操作後に tracker が返した状態**で、「呼び出しが成功した」とは
        別物。閉じたつもりで閉じていない状態を true で覆わない。
        """
        self.require_review_threads()
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise TrackerError("thread_id が空 (resolve する thread の id を文字列で渡す)")
        thread = self.resolve_review_thread(thread_id.strip())
        return {
            "tracker": self.tracker,
            "action": "resolve",
            "thread_id": thread["id"],
            "resolved": thread["resolved"],
        }

    # --- operate ------------------------------------------------------------------

    def issue_claim(self, issue_ref, repo=None):
        """assignee を自分に設定する。"""
        return self._assignee_result(issue_ref, "claim", repo)

    def issue_unclaim(self, issue_ref, repo=None):
        """assignee を解除して候補プールへ返す。"""
        return self._assignee_result(issue_ref, "unclaim", repo)

    def issue_comment(self, issue_ref, body, repo=None):
        """issue にコメントを投稿する (汎用 tracker 操作)。"""
        self.require_repo_scope(repo)
        if not isinstance(body, str) or not body.strip():
            raise TrackerError("body が空 (投稿する本文を文字列で渡す)")
        number = self.issue_number(issue_ref)
        self.post_comment(number, body, repo)
        return {
            "tracker": self.tracker,
            "repo": repo,
            "issue_ref": self.issue_ref(number),
            "action": "comment",
            "ok": True,
        }

    def issue_label(self, issue_ref, add=None, remove=None, repo=None):
        """label を付け外しする。

        どの label が何を意味するかは環境ごとの運用であって server は知らない
        (spec §4.4「環境ごとの label 運用は LLM がこれで表現」)。ここは付け外しの
        実行だけを担う。
        """
        self.require_repo_scope(repo)
        add = list(add or [])
        remove = list(remove or [])
        if not add and not remove:
            raise TrackerError("add / remove のどちらも空 (付け外しする label を渡す)")
        overlap = sorted(set(add) & set(remove))
        if overlap:
            # 同じ label を同時に付けて外すと結果が CLI の適用順に依存する
            raise TrackerError(f"add と remove に同じ label がある: {overlap}")
        number = self.issue_number(issue_ref)
        self.edit_labels(number, add, remove, repo)
        return {
            "tracker": self.tracker,
            "repo": repo,
            "issue_ref": self.issue_ref(number),
            "action": "label",
            "added": add,
            "removed": remove,
            "ok": True,
        }

    # --- 中立化 ---------------------------------------------------------------------

    def _effective_limit(self, limit):
        """実際に tracker へ投げる件数。adapter の 1 回取得上限で頭打ちにする。"""
        return min(limit, self.max_fetch) if self.max_fetch else limit

    def _assignee_result(self, issue_ref, action, repo):
        self.require_repo_scope(repo)
        number = self.issue_number(issue_ref)
        self.set_assignee(number, action, repo)
        return {
            "tracker": self.tracker,
            "repo": repo,
            "issue_ref": self.issue_ref(number),
            "action": action,
            "ok": True,
        }

    def _neutral_issue(self, issue, include_blocked, repo):
        entry = {
            "ref": self.issue_ref(issue["number"]),
            "number": issue["number"],
            "title": issue["title"],
            "state": _NEUTRAL_ISSUE_STATE[issue["state"]],
            "labels": issue["labels"],
            "assignees": issue["assignees"],
            "created_at": issue["created_at"],
            "updated_at": issue["updated_at"],
            "url": issue.get("url", ""),
            # 未検査を「検査して blocker 無し」と読ませないための null
            "blocked": None,
            "blocked_by": None,
            "blocked_source": None,
        }
        if include_blocked:
            checked = self.fetch_blocked(issue["number"], repo)
            entry["blocked"] = checked["blocked"]
            entry["blocked_by"] = [
                {"ref": self.issue_ref(blocker["number"]), "title": blocker.get("title", "")}
                for blocker in checked["open_blockers"]
            ]
            entry["blocked_source"] = checked["source"]
        return entry

    def _neutral_pr(self, pr, with_links=False, issue_tracker=None):
        entry = {
            "ref": self.pr_ref(pr["number"]),
            "number": pr["number"],
            # その PR が居る repo。issue と別 repo でも落とさず現実のまま載せる (ADR 0036)
            "repo": pr.get("repo"),
            "role": pr.get("role"),
            "status": pr_status(pr),
            "title": pr.get("title", ""),
            "url": pr.get("url", ""),
        }
        if with_links:
            entry["head_branch"] = pr.get("head_branch", "")
            numbers = list(pr.get("closes_issues", []))
            # closing reference は **PR 置き場の番号空間**。issue 置き場が別 tracker なら
            # 自 tracker の ref を騙ると別の issue を指すので写さず、取りこぼしとして残す
            mappable = issue_tracker in (None, self.tracker)
            entry["closes_issues"] = (
                [self.issue_ref(number) for number in numbers] if mappable else []
            )
            entry["closes_unmappable"] = (
                []
                if mappable
                else [
                    {"number": number, "pr_tracker": self.tracker, "issue_tracker": issue_tracker}
                    for number in numbers
                ]
            )
        return entry


# --- filter / ordering ---------------------------------------------------------------


def _require_limit(limit):
    limit = int(limit)
    if limit < 1:
        raise TrackerError(f"limit は 1 以上で渡す (受け取った値: {limit})")
    return limit


def _passes_filter(issue, labels_any, labels_none, assignee, updated_since):
    labels = set(issue["labels"])
    if labels_any and not (labels & set(labels_any)):
        return False
    if labels_none and (labels & set(labels_none)):
        return False
    if assignee is not None and not _assignee_matches(issue["assignees"], assignee):
        return False
    if updated_since and issue["updated_at"] < normalize_timestamp(updated_since):
        return False
    return True


def _assignee_matches(assignees, wanted):
    """assignee filter。login のほか `none` (未 assign) / `any` (assign 済み) を解する。"""
    if wanted == ASSIGNEE_NONE:
        return not assignees
    if wanted == ASSIGNEE_ANY:
        return bool(assignees)
    return wanted in assignees


def _order_key(issue, ordering):
    """並べ替え key。同値は number で決める (呼び出しごとに順序が揺れないように)。"""
    if ordering == "number":
        return (issue["number"], issue["number"])
    field = "updated_at" if ordering == "updated" else "created_at"
    return (issue[field], issue["number"])


def _require_import_time_consistency():
    """中立語彙の写像が vocabulary の正本と一致することを import 時に検証する。"""
    mapped = set(_NEUTRAL_ISSUE_STATE.values())
    if mapped != set(vocabulary.ISSUE_STATES):
        raise ValueError(
            "tracker._NEUTRAL_ISSUE_STATE: issue state の写像先が "
            f"vocabulary.ISSUE_STATES と不一致 (mapped={sorted(mapped)})"
        )
    internal = set(_STATE_MAP.values())
    if internal != set(_NEUTRAL_ISSUE_STATE):
        raise ValueError(
            "tracker._NEUTRAL_ISSUE_STATE: 内部 issue state を網羅していない "
            f"(internal={sorted(internal)})"
        )
    if _DERIVED_PR_STATUSES != set(vocabulary.PR_STATUSES):
        raise ValueError(
            "tracker._DERIVED_PR_STATUSES: derive_pr_status の段が "
            f"vocabulary.PR_STATUSES と不一致 (derived={sorted(_DERIVED_PR_STATUSES)})"
        )
    # 逆向き (TRACKERS ⊆ _ADAPTERS) は検査しない — adapter を持たない tracker (jira) が
    # 居るのは仕様 (spec §8)。ここで見るのは「綴りを間違えた登録」だけ
    unknown = set(_ADAPTERS) - set(refs.TRACKERS)
    if unknown:
        raise ValueError(f"tracker._ADAPTERS: 未知の tracker {sorted(unknown)}")


_require_import_time_consistency()
