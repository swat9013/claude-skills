"""Tracker port / ChangeHost port と、観測事実 (IssueFact / CLFact) の中立語彙。

設計正本 `docs/design/dispatch-v2/system.md` §2 / ADR 0055。観測系の port をここが持つ:

- **Tracker** — issue 置き場の観測 (候補集合・issue 1 件)
- **CLLink** — issue から CL への紐づきの供給。**issue 置き場の adapter が兼ねるとは
  限らない**ので Tracker から分ける (Jira 置き場では台帳が源になる — ADR 0059)
- **ChangeHost** — CL (PR / MR) 置き場の観測。issue と別 tracker でよいので port を分ける

**adapter の責務は語彙の写像だけ**。「conflict なら何をすべきか」は返さない (判断は rule 評価
と orchestrator が持つ)。tracker 固有の綴り (CLI 引数・生値の解釈) は adapter 側に閉じ、
本 module が持つのは中立語彙の正本と port の呼び出し口だけ。

観測事実は **意図を持たない cache** で、必ず `observed_at` を伴う (設計 §4 の「全観測に
observedAt 必須」)。時刻は adapter が生成せず**呼び出し側が注入する** — 観測 1 回ぶんの
事実に同じ時刻が付き、テストが実時刻に依存しない。

**「未観測」と「観測して空」を型で分ける**のが本 module の中核 (ADR 0055 の
`unresolvedThreads([]≠null)`)。`None` は「見ていない / 見たが読み切れなかった」、`[]` は
「見て、無かった」。両者を潰すと、観測できなかった review thread が「未解決 0 件」として
merge 可能と読まれる。
"""

from abc import ABC, abstractmethod

# --- 中立語彙 -------------------------------------------------------------------

ISSUE_STATES = ("open", "closed")

# CL status の語彙 6 値 (設計 §4)。`none` は「紐づく CL が 1 件も無い」を表す**集約専用の段**で、
# CLFact 1 件には入らない
CL_STATUSES = ("none", "conflict", "merged", "checking", "open", "closed")

# CLFact 1 件が取りうる status (集約専用の `none` を除いた 5 値)
CL_FACT_STATUSES = tuple(status for status in CL_STATUSES if status != "none")

# 複数の closes CL を 1 語へ畳むときの優先順位。**語彙の並び順とは別に宣言する** —
# `CL_STATUSES` は語彙の列挙であって強さの序列ではないので、そちらの並びを暗黙の優先順位として
# 読ませると、語彙に段を足しただけで集約の意味が変わる。
#
# 並べ方の軸は「**まだ動いている CL を、終わった CL より強く見る**」。強い順に
# conflict (人手で直す) → checking (判定待ち) → open (待てばよい) → merged → closed。
#
# **`merged` は「まだ動いている CL が 1 本も無く、着地したものがある」を意味する** (merge されず
# に閉じた `closed` は動いていないので数に入らない)。1 本 merged / 1 本 open の WorkOrder は
# まだ終わっていないので `open` を名乗る。逆順 (merged を open より強く) にすると、作業中の
# WorkOrder が rule #2 merged_but_alive で「資源を片付けろ」と言われ、rule #1 park_point は
# 永久に立たなくなる。
#
# **conflict は merged より強い** — 先に直すのは conflict の側で、merged 済み CL の後始末は
# その後でも失われない
CL_STATUS_PRECEDENCE = ("conflict", "checking", "open", "merged", "closed")

# mergeable の 3 値。**生値の綴りを読むのは adapter**、綴りの正本は本 module。adapter が独自の
# 綴りを返すと梯子の `== "CONFLICTING"` が黙って外れ、conflict が open (= 人手不要) に見える
MERGEABLE_VALUES = ("MERGEABLE", "CONFLICTING", "UNKNOWN")

# CL の紐づき方。closing reference (`Closes #N`) と単なる言及を分ける — 機械遷移が根拠に
# するのは closes だけで、mention を混ぜると無関係な PR の merge で issue が completed になる
ROLE_CLOSES = "closes"
ROLE_MENTION = "mention"
CL_ROLES = (ROLE_CLOSES, ROLE_MENTION)


class VocabularyMismatch(RuntimeError):
    """中立語彙に無い綴りを受け取った (観測の失敗ではなく、表と実装のずれ)。

    **`ObservationError` と分けてある** — あちらは「store が答えなかった」で、観測を数え直せば
    直る種類の失敗 (rule #9 の連続失敗に数えられる)。こちらは綴りが変わるまで永久に直らない
    ので、同じ型にすると健全な store が「到達できない」ことにされる。
    """


class ObservationError(RuntimeError):
    """外部 store を観測できない / 観測値が中立語彙へ写せない。

    **観測失敗を「観測して空」に倒さない**ための唯一の系統。CLI の非 0 exit も未知の語彙も
    ここへ束ね、呼び出し側 (観測 cache) が「前回の事実を保つ」と決められるようにする。
    """


def require_issue_state(state):
    """issue state が中立語彙かを検査して返す。"""
    if state not in ISSUE_STATES:
        raise ObservationError(f"未知の issue state: {state!r} (候補: {', '.join(ISSUE_STATES)})")
    return state


def require_cl_fact_status(status):
    """CLFact 1 件の status が中立語彙かを検査して返す。"""
    if status not in CL_FACT_STATUSES:
        raise ObservationError(
            f"未知の CL status: {status!r} (候補: {', '.join(CL_FACT_STATUSES)})"
        )
    return status


def require_mergeable(value):
    """mergeable が 3 値かを検査して返す。"""
    if value not in MERGEABLE_VALUES:
        raise ObservationError(
            f"未知の mergeable: {value!r} (候補: {', '.join(MERGEABLE_VALUES)})"
        )
    return value


def require_role(role):
    """CL の紐づき方が中立語彙かを検査して返す。"""
    if role not in CL_ROLES:
        raise ObservationError(f"未知の CL role: {role!r} (候補: {', '.join(CL_ROLES)})")
    return role


# --- 観測事実 -------------------------------------------------------------------


def issue_fact(*, issue_ref, state, observed_at):
    """IssueFact を組み立てる (語彙の検証込み)。"""
    return {
        "issue_ref": issue_ref,
        "state": require_issue_state(state),
        "observed_at": observed_at,
    }


def cl_fact(*, cl_ref, repo, status, mergeable, unresolved_threads, head_sha, observed_at):
    """CLFact を組み立てる (語彙の検証込み)。

    Args:
        unresolved_threads: 未解決 review thread の id 列。**`None` は未観測**
            (取り切れなかった / 権限が無い) で、`[]` は「観測して 0 件」。潰さない
        head_sha: 観測した時点の head commit。機械遷移の evidence が指す一意な地点になる
    """
    if unresolved_threads is not None and not isinstance(unresolved_threads, list):
        raise ObservationError(
            f"unresolved_threads は list か None: {type(unresolved_threads).__name__}"
        )
    return {
        "cl_ref": cl_ref,
        "repo": repo,
        "status": require_cl_fact_status(status),
        "mergeable": require_mergeable(mergeable),
        "unresolved_threads": unresolved_threads,
        "head_sha": head_sha,
        "observed_at": observed_at,
    }


def aggregate_cl_status(facts):
    """closes CL の CLFact 列を 1 語へ畳む (`CL_STATUSES` の 6 値)。

    紐づく CL が 1 件も無ければ `none`。1 件以上あれば `CL_STATUS_PRECEDENCE` の強い順に
    最初に見つかった status を名乗る。

    **未観測を受け取らない** — 呼び出し側は「1 件でも観測できなければ畳まない」を先に決めて
    おく (観測できなかった CL を落として畳むと、conflict している CL が `open` に化ける)。

    畳めない綴りは `VocabularyMismatch`。**`ObservationError` にしない** — この関数は adapter の
    try の外で呼ばれるので、観測失敗として流すと健全な store が「到達できない」ことにされる。
    """
    statuses = {fact["status"] for fact in facts}
    unknown = sorted(statuses - set(CL_STATUS_PRECEDENCE))
    if unknown:
        raise VocabularyMismatch(
            f"畳めない CL status: {unknown} (語彙: {', '.join(CL_STATUS_PRECEDENCE)})"
        )
    for status in CL_STATUS_PRECEDENCE:
        if status in statuses:
            return status
    return "none"


def cl_link(*, cl_ref, repo, role):
    """issue → CL の紐づき 1 件 (どの CL が、どの repo で、どう紐づいているか)。"""
    return {"cl_ref": cl_ref, "repo": repo, "role": require_role(role)}


def cl_status_for(state, mergeable):
    """CL 1 件の `state` (OPEN / MERGED / CLOSED) と mergeable から status を決める。

    `mergeable` が UNKNOWN の open CL は `checking` (計算中) で、`open` (conflict 無し) と
    混ぜない — push 直後の観測は日常的に UNKNOWN を踏むため、混ぜると「conflict 無し」と
    誤って報告する。
    """
    if state == "MERGED":
        return "merged"
    if state == "CLOSED":
        return "closed"
    if state != "OPEN":
        raise ObservationError(f"未知の CL state: {state!r} (候補: OPEN / MERGED / CLOSED)")
    require_mergeable(mergeable)
    if mergeable == "CONFLICTING":
        return "conflict"
    if mergeable == "UNKNOWN":
        return "checking"
    return "open"


# --- port ------------------------------------------------------------------------


# --- Session 観測の継ぎ目 ---------------------------------------------------------
#
# **SessionRuntime port そのものは issue #852 の担当**。ここに置くのは、完了の機械遷移が
# 「Session 不在」を判定するために読む**返り値の形**だけ — port の実装者が形を探して
# reconciler の private を読む羽目にならないようにするため。

#: Session 観測の返り値。`sessions` は生きている Session の列 (空 = 不在)、`source` は
#: **何を根拠に不在と言えるか**。terminal は不変なので、evidence に残った `source` が誤分類を
#: 後から監査する唯一の材料になる (ADR 0055)
SESSION_OBSERVATION_FIELDS = ("sessions", "source")


def session_observation(*, sessions, source):
    """Session 観測を組み立てる (機械遷移の evidence にそのまま載る形)。

    Args:
        sessions: 生きている Session の列。**空 list が「不在」** で、不在を `None` で表さない
            — 観測できなかったのか居ないのかは `source` が語る
        source: 不在の根拠 (例: 台帳に Session が無い / runtime を観測した)
    """
    if not isinstance(sessions, list):
        raise ObservationError(f"sessions は list: {type(sessions).__name__}")
    if not source:
        raise ObservationError("source が空 (何を根拠に不在と言えるかが evidence に残らない)")
    return {"sessions": sessions, "source": source}


class TrackerPort(ABC):
    """issue 置き場の観測。**どの repo を見るかは決めない** — 識別子は宣言 config から渡る。

    **紐づき (issue → CL) の発見は持たない** — あれは `CLLinkPort` の担当で、issue 置き場の
    adapter が兼ねるとは限らない。Jira は issue 置き場になれるが、GitLab の MR を自力で
    列挙する経路を持たないので本 port だけを実装する (ADR 0059)。
    """

    #: adapter が担当する tracker の中立語彙 (`refs.ISSUE_PATTERNS` の key)
    tracker = None

    @abstractmethod
    def fetch_candidates(self, *, repo, label, observed_at):
        """候補プールの母集団を観測して IssueFact の列を返す。

        Args:
            repo: 置き場の識別子 (gh なら `owner/name`)
            label: 宣言 config が指定した絞り込み label。**None なら絞らない** —
                資格判定を adapter に持たせない (ADR 0026 / 0056)
            observed_at: 観測時刻 (呼び出し側が注入する)

        観測できなければ `ObservationError`。空の置き場は `[]` を返す (両者を潰さない)。
        """

    @abstractmethod
    def fetch_issue(self, issue_ref, *, repo, observed_at):
        """issue 1 件の IssueFact。観測できなければ `ObservationError`。"""


class CLLinkPort(ABC):
    """issue → CL の紐づきを供給する。**issue 置き場の adapter が兼ねるとは限らない**。

    tracker 側から引ける置き場 (GitHub / GitLab) はその adapter が本 port も実装する。
    引けない置き場 (Jira は GitLab の MR を列挙できない) では、**台帳に記録した紐づきが
    源になる** (`cl_record.LedgerCLLinks`) — ADR 0040 決定 1 が「join は台帳の CL 記録で
    表現する」と決めた経路の v2 実装で、そちらは ADR 0059 が引く。

    port を Tracker から分けてあるのは、**供給できない adapter に空実装を持たせないため**。
    `fetch_cl_links` を `TrackerPort` の abstract に残すと、Jira adapter は `[]` か
    `NotImplementedError` のどちらかを返すことになり、後者は観測経路の外へ抜ける。
    """

    #: **紐づきが 1 件も無いことを「その issue に CL は無い」の証拠として使ってよいか。**
    #:
    #: tracker から列挙する実装は真 — 置き場を見て 0 件だったのだから、merge を伴わない close
    #: を `abandoned` と確定してよい。台帳から読む実装は偽 — **誰も記録していないだけかもしれない**
    #: ので、同じ 0 件で terminal (不変) を確定させると、記録前に閉じた WorkOrder の成果が
    #: 失われる。
    #:
    #: **`[]` を返さないことでは表さない** (ADR 0059)。`None` (未観測) に倒すと、CL を読む rule が
    #: 全部降りてしまい、Jira 置き場では `session_died_empty` (rule #5) が永久に立たなくなる —
    #: 「worker が成果を残さず死んだ」を人へ返す経路が消える。**止めたいのは terminal の確定
    #: だけ**なので、止める条件を値ではなく源の性質として持つ。
    #:
    #: **既定は偽**。書き忘れた実装が terminal を確定させる側に倒れると、誤った `abandoned` は
    #: 巻き戻らない — 倒れ先を可逆な側 (非終端のまま残る) に置く。列挙できる adapter が真を名乗る。
    absence_is_evidence = False

    @abstractmethod
    def fetch_cl_links(self, issue_ref, *, repo):
        """issue に紐づく CL の列 (`cl_link`)。**repo で絞らない**。

        関連 repo / fork からの CL も所属 repo 付きでそのまま返す — 自 repo 以外を落とすと
        正当な closes が消え、機械遷移が根拠を失う (ADR 0036 と同じ理屈)。

        **必ず列を返す** (引けなければ `ObservationError`)。0 件は `[]` で、その 0 件を terminal
        の根拠にしてよいかは `absence_is_evidence` が答える。

        **観測時刻を受け取らない**のは、紐づきが「いつの事実か」を持つのは cache 側だから
        (`observation.ObservationCache.put_cl_links`)。IssueFact / CLFact と違って紐づきは
        1 件ごとの事実ではなく列全体が 1 回の観測なので、時刻は列に 1 つ付く。
        """


class ChangeHostPort(ABC):
    """CL (PR / MR) 置き場の観測。issue と別 tracker でよいので Tracker と分ける。"""

    #: adapter が担当する tracker の中立語彙 (`refs.CL_PATTERNS` の key)
    tracker = None

    @abstractmethod
    def fetch_cl(self, cl_ref, *, repo, observed_at):
        """CL 1 件の CLFact。`repo` は **その CL が居る repo**。

        観測できなければ `ObservationError`。未解決 review thread を読み切れなかったときは
        `unresolved_threads=None` を載せて返す (観測失敗として全体を落とさない)。
        """
