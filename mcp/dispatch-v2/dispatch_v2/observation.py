"""外部 store の観測 cache (IssueFact / CLFact / CandidateSet)。

設計 §4: 観測事実は**集約でない・意図を持たない cache**。台帳 (events.jsonl) と違って正本では
ないので、daemon の再起動で消えてよい — 消えたら次の tick で観測し直す。

本 module の中核は 2 つ:

- **未観測と「観測して空」を分ける**。読み取りは `None` (まだ見ていない / 見たが失敗した) と
  `[]` (見て、無かった) を返し分ける。潰すと、観測に失敗した候補プールが「候補ゼロ」に見え、
  観測できなかった review thread が「未解決ゼロ」に見える
- **観測に失敗しても前回の事実を消さない**。空で上書きすると、復帰した次の tick で全件が
  「新規」に見えて escalation が溢れる。代わりに失敗の連続回数を数える (rule #9 の条件)

`observed_at` は cache が生成せず**呼び出し側が注入する**。1 tick ぶんの観測に同じ時刻が付き、
テストが実時刻に依存しない (`principle-test-double-boundary`)。

**「未観測の理由」の語彙も本 module が持つ** (`NOT_YET_OBSERVED` / `stall_reason`)。読み手は
MCP 面と dashboard の投影の 2 つあり、どちらも同じ言葉で答えなければ「観測が止まっている」の
意味が面ごとにずれる。
"""


# 観測がまだ成功していないだけの状態。**宣言 config の欠落と分けて返す** — 前者は待てば
# 埋まり、後者は人が置くまで永久に埋まらない。同じ `null` でも次の一手が違う
NOT_YET_OBSERVED = "まだ観測が 1 度も成功していない"

#: 観測状況を別々に持つ主語。**面ごとに分けるのは鮮度も理由も別々に動くから** — 候補観測
#: だけが落ちて CL 観測は通っている状態が普通にある。読み手 (投影 / MCP 面) が場合分けを
#: 自前で持たずに済むよう、語彙も判定もここが正本
OBSERVATION_SUBJECTS = ("candidates", "issues", "cls")


def unobserved_status(reason=NOT_YET_OBSERVED):
    """まだ何も届いていない主語の観測状況。読み手が「欄が無い」場合に埋める既定。"""
    return {"observed": False, "observed_at": None, "reason": reason}


def stall_reason(cache, *, observed):
    """その観測が進んでいなければ理由を返す。進んでいれば `None`。

    **`observed` は問い合わせている観測そのものの成否**で、project 全体ではない。project 全体で
    畳むと、候補観測だけが毎 tick 落ちて settle 段が通っている状況で `candidates: null` に
    `reason: null` が付き、読み手との契約 (`null` なら理由が入る) が破れる。
    """
    if observed and cache.skip_reason() is None:
        return None
    return cache.skip_reason() or NOT_YET_OBSERVED


class StoreFailure:
    """1 つの store に対する連続観測失敗。復帰したら捨てる。

    `first_failed_at` を持つのは **escalation の dedup key に混ぜるため**。store が復帰して
    再び落ちたとき key が変わり、打ち切り済みの古い escalation に吸収されずに上がり直す。
    """

    def __init__(self, *, error, at):
        self.streak = 1
        self.error = error
        self.first_failed_at = at
        self.last_failed_at = at
        # この連続について escalation を配送した最後の tick。**1 tick に 1 回だけ配送する**ため
        # に持つ — 1 tick で同じ store を何度も観測するので、観測ごとに配送すると 3 回の予算が
        # 1 tick で尽き、間隔を空けた nudge が運用者に届かない
        self.last_delivered_at = None

    def extend(self, *, error, at):
        """同じ store の失敗をもう 1 回数える。**同一 tick 内の 2 度目は数えない**。

        1 tick で同じ store を何度観測するか (候補 1 回 + 非終端 WorkOrder のぶん) は台帳の
        中身で変わる。回数で数えると WorkOrder が多い project ほど早く rule #9 が発火し、
        「3 連続」の意味が project ごとに変わってしまう。観測時刻は 1 tick で同じ値なので、
        それを同一性の判定に使う。
        """
        if at != self.last_failed_at:
            self.streak += 1
            self.last_failed_at = at
        self.error = error
        return self


class ObservationCache:
    """1 project ぶんの観測 cache。"""

    def __init__(self):
        self._candidates = None
        self._candidates_observed_at = None
        self._issues = {}
        self._cls = {}
        self._cl_links = {}
        self._failures = {}
        self._skip_reason = None
        self._current_tick = None
        # 投影 (`materialized_view`) が「観測が動いたか」を測る目盛。台帳の `Ledger.revision` と
        # 同じ役目で、**cache へ写した事実の数**を数える。連続失敗 (`record_failure`) は投影の
        # 対象ではないので進めない — 進めると、落ち続けている store が毎 tick 投影を書き直す
        self.revision = 0

    # --- 観測しない理由 --------------------------------------------------------

    def set_skip_reason(self, reason):
        """この project を観測しない理由 (宣言 config の欠落・破損)。

        **未観測の理由を 2 種に分ける**ためだけに持つ: 「宣言が無いので観測しない」(置き忘れ。
        人が直すまで永久に埋まらない) と「まだ観測が成功していない」(待てば埋まる)。潰すと、
        置き忘れが「今は候補が無い」に化ける。
        """
        self._set_skip_reason(reason)

    def clear_skip_reason(self):
        self._set_skip_reason(None)

    def _set_skip_reason(self, reason):
        """**理由が変わったときだけ版を進める**。

        `reconciler` は健全な project で毎 tick `clear_skip_reason` を、宣言が壊れている
        project で毎 tick `set_skip_reason` を呼ぶ (どちらも値が変わったかを見ない)。無条件に
        版を進めると、何も動いていない project の投影を毎 tick 書き直すことになる。
        """
        if self._skip_reason == reason:
            return
        self._skip_reason = reason
        self.revision += 1

    def skip_reason(self):
        return self._skip_reason

    # --- 候補集合 --------------------------------------------------------------

    def put_candidates(self, issue_facts, *, observed_at):
        self._candidates = list(issue_facts)
        self._candidates_observed_at = observed_at
        for fact in issue_facts:
            self._issues[fact["issue_ref"]] = fact
        self.revision += 1

    def candidates(self):
        """観測した候補の IssueFact 列。**一度も観測できていなければ None**。"""
        return list(self._candidates) if self._candidates is not None else None

    def candidates_observed_at(self):
        return self._candidates_observed_at

    def has_observed(self):
        """この project で観測が 1 度でも成功したか (候補・CL・紐づきのいずれか)。

        **CL 側の「未観測」を表すのに使う**。CL は非終端 WorkOrder が居て初めて観測されるので、
        「CL 0 件」は正常な観測結果になりうる — 何も観測できていないこととは別物で、潰すと
        宣言 config の置き忘れが「CL が 1 件も無い」に見える。

        候補観測だけを見ないのは、候補が落ちても settle 段の CL 観測は走るから — 片方だけを
        根拠にすると、CL の事実を持っているのに「未観測」と答える。
        """
        return (
            self.has_observed_candidates()
            or self.has_observed_issues()
            or self.has_observed_cls()
        )

    def has_observed_candidates(self):
        return self._candidates is not None

    def has_observed_issues(self):
        return bool(self._issues)

    def has_observed_cls(self):
        """**CL 面だけ**の成否 (CLFact も紐づきも 1 件も届いていなければ False)。

        `has_observed` と分けるのは、面ごとの鮮度と理由を出す読み手が要るため。混ぜると、
        候補観測だけ成功した project の CL 面が「観測済み」を名乗り、紐づきが 1 件も無い
        こと (面ごと未観測) が「その WorkOrder の巡回がまだ来ていない」に化ける。
        """
        return bool(self._cls) or bool(self._cl_links)

    def observed_at_of(self, subject):
        """主語ごとの鮮度 = **一番新しい観測時刻**。1 件も無ければ None。

        issue も CL も WorkOrder ごとに別々の時刻で観測される (1 tick の予算を跨いで巡る)
        ので、面として 1 つの時刻を出すなら「どこまで進んだか」を答える最新側になる。
        個々の古さは行ごとの `observed_at` に残るので、畳んでも失われない。
        """
        if subject == "candidates":
            return self._candidates_observed_at
        if subject == "issues":
            return _latest(fact["observed_at"] for fact in self._issues.values())
        return _latest(
            [fact["observed_at"] for fact in self._cls.values()]
            + [observed["observed_at"] for observed in self._cl_links.values()]
        )

    def status_of(self, subject):
        """主語 1 つの観測状況 (`observed` / `observed_at` / `reason`)。

        **`observed` と `reason` を両方持つ**。観測に成功した後で宣言 config を壊した project は
        両方を持つ (古い値を出しつつ、止まっていることも見せる)。
        """
        observed = {
            "candidates": self.has_observed_candidates,
            "issues": self.has_observed_issues,
            "cls": self.has_observed_cls,
        }[subject]()
        return {
            "observed": observed,
            "observed_at": self.observed_at_of(subject),
            "reason": stall_reason(self, observed=observed),
        }

    # --- issue / CL ------------------------------------------------------------

    def put_issue(self, fact):
        self._issues[fact["issue_ref"]] = fact
        self.revision += 1

    def issues(self):
        """観測済み IssueFact の全件 (観測順)。候補かどうかは問わない。"""
        return list(self._issues.values())

    def put_cl(self, fact):
        self._cls[fact["cl_ref"]] = fact
        self.revision += 1

    def cls(self):
        """観測済み CLFact の全件 (観測順)。"""
        return list(self._cls.values())

    def put_cl_links(self, issue_ref, links, *, observed_at):
        """issue → CL の紐づき観測。**紐づきにも観測時刻を付ける** (設計 §4「全観測に
        observedAt 必須」) — 紐づきは CL の状態と別に古びるので、CLFact の時刻では代用できない。
        """
        self._cl_links[issue_ref] = {
            "links": list(links),
            "observed_at": observed_at,
        }
        self.revision += 1

    def cl_links(self, issue_ref):
        """issue に紐づく CL の観測。**未観測なら None** (「紐づく CL が無い」と分ける)。"""
        observed = self._cl_links.get(issue_ref)
        return {**observed, "links": list(observed["links"])} if observed is not None else None

    def cl_links_by_issue(self):
        """観測済みの紐づきを issue ref ごとに返す (観測順)。"""
        return {issue_ref: self.cl_links(issue_ref) for issue_ref in self._cl_links}

    # --- 観測失敗 --------------------------------------------------------------

    def begin_tick(self, now):
        """新しい tick の開始を告げ、**直前の tick を丸ごと通過した store の連続失敗を捨てる**。

        連続失敗を「1 tick でも失敗したか」で数えるための唯一の地点。個々の観測の成功で捨てる
        形にすると、成功と失敗の**順序**で結果が変わる — 1 tick の中で同じ store を何度も
        観測する (候補 1 回 + 非終端 WorkOrder のぶん) ので、settle 段だけが恒常的に落ちる
        store では「成功 → 失敗」の順に並び、毎 tick streak が 1 に戻って rule #9 が永久に
        鳴かない。tick の境界で切れば順序に依存しない。
        """
        if self._current_tick is not None:
            for store in list(self._failures):
                if self._failures[store].last_failed_at != self._current_tick:
                    del self._failures[store]
        self._current_tick = now

    def record_failure(self, store, *, error, at):
        """store の観測失敗を数える。返り値は現在の連続失敗。"""
        existing = self._failures.get(store)
        self._failures[store] = (
            existing.extend(error=error, at=at)
            if existing is not None
            else StoreFailure(error=error, at=at)
        )
        return self._failures[store]


def _latest(stamps):
    """観測時刻の集まりから一番新しいものを採る (どれも無ければ None)。"""
    seen = [stamp for stamp in stamps if stamp]
    return max(seen) if seen else None
