"""TrackerPort — issue tracker (GitHub / GitLab) への観測・操作の継ぎ目。

spec §2: **adapter の責務は語彙の写像のみ**。「conflict なら何をすべきか」は返さない。
本 module は port (中立 API) と tracker 非依存の写像規則を持ち、tracker 固有の CLI 呼び出しは
`tracker_gh` / `tracker_glab` が実装する。

語彙の層が 2 つあるのが要点:

- **内部語彙** (`OPEN` / `MERGED` / `CONFLICTING`): adapter と `derive_pr_status` の間だけで
  使う。既存 `dispatch_tracker.py` から実証済みロジックを移植するにあたり、この層を
  書き換えないことで「実機で検証済み」という移植の価値を保つ
- **中立語彙** (`vocabulary` / `refs`): tool 境界を越える値だけがこちらへ写る
  (issue state `open`/`closed`、PR status 6 値、PR role `closes`/`mention`、ref `gh#386`/`gh!401`)

gh / glab CLI へ shell out し、認証は CLI へ委譲する (spec §4.2)。CLI の非 0 exit は
`TrackerError` として即座に表面化させる — 「CLI の実行失敗」を「blocker 無し」「PR 無し」と
誤読させないため (i217 誤 dispatch の真因)。
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import proc
import refs
import vocabulary

SUBPROCESS_TIMEOUT_SEC = 60

# tracker 判定の第一正 (repo が明示する運用文書)。無ければ git remote の host で判定する
TRACKER_DOC = Path("docs/agents/issue-tracker.md")

# `Blocked by: #N` 行 (dependencies 機能を持たない repo の fallback)
BLOCKED_LINE = re.compile(r"^\s*blocked[ -]?by\b[:\s]*(.*)$", re.IGNORECASE | re.MULTILINE)

# --- 内部語彙 (adapter の中だけ) ------------------------------------------------

_STATE_MAP = {"open": "OPEN", "opened": "OPEN", "closed": "CLOSED"}
_PR_STATE_MAP = {"open": "OPEN", "opened": "OPEN", "closed": "CLOSED", "merged": "MERGED"}
# GitHub の mergeable 語彙。GitLab 側もこの 3 値へ寄せる (tracker 差を status 導出まで持ち込まない)
MERGEABLE_VALUES = frozenset({"MERGEABLE", "CONFLICTING", "UNKNOWN"})
# GitLab が mergeability を計算し終えていない間の detailed_merge_status
GLAB_UNSETTLED_MERGE_STATUS = frozenset({"checking", "unchecked"})

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


def normalize_gh_mergeable(raw):
    """gh の mergeable → 3 値。未知語彙は UNKNOWN へ倒す (「conflict 無し」と読ませない側)。"""
    value = str(raw or "").upper()
    return value if value in MERGEABLE_VALUES else "UNKNOWN"


def normalize_glab_mergeable(raw):
    """glab の MR → 3 値。has_conflicts は計算完了後にしか信用できないので順に見る。"""
    if str(raw.get("detailed_merge_status") or "").lower() in GLAB_UNSETTLED_MERGE_STATUS:
        return "UNKNOWN"
    conflicts = raw.get("has_conflicts")
    if conflicts is None:
        return "UNKNOWN"
    return "CONFLICTING" if conflicts else "MERGEABLE"


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
    返り値は中立語彙 (`vocabulary.PR_STATUSES`)。
    """
    if not prs:
        return "none"
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


# --- tracker 判定 ---------------------------------------------------------------


def detect_tracker(root):
    """docs/agents/issue-tracker.md の H1 が第一正。無ければ git remote の host で判定。"""
    doc = Path(root) / TRACKER_DOC
    if doc.is_file():
        h1 = doc.read_text(encoding="utf-8").splitlines()[0] if doc.stat().st_size else ""
        if "GitHub" in h1:
            return "gh"
        if "GitLab" in h1:
            return "glab"
    rc, out, _err = run_command(["git", "-C", str(root), "remote", "-v"])
    if rc == 0:
        if "github.com" in out:
            return "gh"
        if "gitlab" in out:
            return "glab"
    return None


def get_adapter(tracker):
    """tracker 名 → adapter。未実装 tracker は継ぎ目であることを名指しで失敗させる。

    `refs.TRACKERS` に居るのに adapter が無い (jira) という非対称を、黙って
    「未知の tracker」に丸めず別のメッセージで表す — spec §8 の「port 境界だけ設計した」を
    実行時に反証可能な形で残すため。
    """
    if tracker == "gh":
        import tracker_gh

        return tracker_gh.GhAdapter()
    if tracker == "glab":
        import tracker_glab

        return tracker_glab.GlabAdapter()
    if tracker in refs.TRACKERS:
        raise TrackerError(
            f"{tracker} adapter は未実装 — port 境界 (継ぎ目) のみを設計してある (spec §8)"
        )
    raise TrackerError(f"未知の tracker: {tracker!r} (候補: {', '.join(refs.TRACKERS)})")


# --- port ------------------------------------------------------------------------


class TrackerPort:
    """issue tracker への中立 API。tracker 固有の呼び出しは下の継ぎ目 method が担う。

    公開 method (`observe_*` / `issue_*`) は中立語彙だけを受け渡しし、継ぎ目 method
    (`fetch_*` / `linked_prs` 等) は内部語彙のまま扱う。新しい tracker を足すときに
    実装するのは継ぎ目 method だけ。
    """

    tracker = None

    # tracker 側の 1 回取得上限 (None = 上限なし)。要求 limit がこれを超えるとき、
    # `truncated` は **実際に投げた件数** と比べないと必ず false になる (取れなかった
    # ぶんを「全部取れた」と報告してしまう)
    max_fetch = None

    # --- 継ぎ目 (adapter が実装する) -------------------------------------------

    def fetch_issues(self, state, limit):
        """issue 一覧を内部語彙の dict 列で返す (number / title / state / labels /
        assignees / created_at / updated_at / url)。"""
        raise NotImplementedError

    def fetch_issue(self, number):
        """issue 1 件を内部語彙の dict で返す (`fetch_issues` の要素と同じ形)。"""
        raise NotImplementedError

    def fetch_blocked(self, number):
        """open blocker 検査 → {"blocked", "open_blockers", "source"}。
        open_blockers は {"number", "title"} の列 (open のものだけ)。"""
        raise NotImplementedError

    def repo_scope(self):
        """自 repo を指す識別子 (別 repo からの言及を落とすために使う)。"""
        raise NotImplementedError

    def linked_prs(self, number, scope):
        """issue → {PR 番号: role}。role は `closes` / `mention`。"""
        raise NotImplementedError

    def pr_detail(self, number, role):
        """PR 1 件 → 内部語彙 dict (number / state / mergeable / title / url / role)。"""
        raise NotImplementedError

    def open_prs(self, scope, limit):
        """repo の open PR → 内部語彙 dict の列 (+ head_branch / closes_issues)。"""
        raise NotImplementedError

    def set_assignee(self, number, action):
        """assignee を設定 (`claim`) / 解除 (`unclaim`) する。"""
        raise NotImplementedError

    def post_comment(self, number, body):
        """issue にコメントを投稿する。"""
        raise NotImplementedError

    def edit_labels(self, number, add, remove):
        """label を付け外しする。実行した CLI 呼び出しの数を返す。"""
        raise NotImplementedError

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
        """
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

        fetched = self.fetch_issues(state, effective)
        selected = [
            issue
            for issue in fetched
            if _passes_filter(issue, labels_any, labels_none, assignee, updated_since)
        ]
        if ordering is not None:
            selected.sort(key=lambda issue: _order_key(issue, ordering), reverse=descending)

        entries = [self._neutral_issue(issue, include_blocked) for issue in selected]
        return {
            "tracker": self.tracker,
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

    def observe_issue(self, issue_ref):
        """issue 1 件を中立 schema で返す (`resolve` の join 用)。

        台帳 entry の現況を `observe_issues` の窓から拾おうとすると、`limit` の外へ
        こぼれた issue を「無い」と読むことになる (repo の issue 番号は窓を越えて増える)。
        番号で直接引く経路を分けてあるのはそのため。blocker 検査は join に要らないので
        しない (未検査は `blocked: null` のまま返る)。
        """
        return self._neutral_issue(self.fetch_issue(self.issue_number(issue_ref)), False)

    def observe_prs(self, issue_ref=None, limit=DEFAULT_PR_LIMIT):
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

        `limit` が効くのは repo 全体を見る経路だけ。issue に紐づく PR は issue 側で
        件数が閉じているので切らない (`truncated` は常に false)。
        """
        limit = _require_limit(limit)
        scope = self.repo_scope()
        if issue_ref is None:
            effective = self._effective_limit(limit)
            prs = self.open_prs(scope, effective)
            return {
                "tracker": self.tracker,
                "issue_ref": None,
                # issue 文脈が無いので集約 status は定義されない (role も同様)
                "status": None,
                "count": len(prs),
                "limit": limit,
                "effective_limit": effective,
                "truncated": len(prs) >= effective,
                "prs": [self._neutral_pr(pr, with_links=True) for pr in prs],
            }
        number = self.issue_number(issue_ref)
        links = self.linked_prs(number, scope)
        prs = [self.pr_detail(pr_number, role) for pr_number, role in sorted(links.items())]
        closing_prs = [pr for pr in prs if pr["role"] == "closes"]
        return {
            "tracker": self.tracker,
            "issue_ref": refs.parse_issue_ref(issue_ref)["ref"],
            "status": derive_pr_status(closing_prs),
            "count": len(prs),
            "limit": None,
            "effective_limit": None,
            "truncated": False,
            "prs": [self._neutral_pr(pr) for pr in prs],
        }

    # --- operate ------------------------------------------------------------------

    def issue_claim(self, issue_ref):
        """assignee を自分に設定する。"""
        return self._assignee_result(issue_ref, "claim")

    def issue_unclaim(self, issue_ref):
        """assignee を解除して候補プールへ返す。"""
        return self._assignee_result(issue_ref, "unclaim")

    def issue_comment(self, issue_ref, body):
        """issue にコメントを投稿する (汎用 tracker 操作)。"""
        if not isinstance(body, str) or not body.strip():
            raise TrackerError("body が空 (投稿する本文を文字列で渡す)")
        number = self.issue_number(issue_ref)
        self.post_comment(number, body)
        return {
            "tracker": self.tracker,
            "issue_ref": self.issue_ref(number),
            "action": "comment",
            "ok": True,
        }

    def issue_label(self, issue_ref, add=None, remove=None):
        """label を付け外しする。

        どの label が何を意味するかは環境ごとの運用であって server は知らない
        (spec §4.4「環境ごとの label 運用は LLM がこれで表現」)。ここは付け外しの
        実行だけを担う。
        """
        add = list(add or [])
        remove = list(remove or [])
        if not add and not remove:
            raise TrackerError("add / remove のどちらも空 (付け外しする label を渡す)")
        overlap = sorted(set(add) & set(remove))
        if overlap:
            # 同じ label を同時に付けて外すと結果が CLI の適用順に依存する
            raise TrackerError(f"add と remove に同じ label がある: {overlap}")
        number = self.issue_number(issue_ref)
        self.edit_labels(number, add, remove)
        return {
            "tracker": self.tracker,
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

    def _assignee_result(self, issue_ref, action):
        number = self.issue_number(issue_ref)
        self.set_assignee(number, action)
        return {
            "tracker": self.tracker,
            "issue_ref": self.issue_ref(number),
            "action": action,
            "ok": True,
        }

    def _neutral_issue(self, issue, include_blocked):
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
            checked = self.fetch_blocked(issue["number"])
            entry["blocked"] = checked["blocked"]
            entry["blocked_by"] = [
                {"ref": self.issue_ref(blocker["number"]), "title": blocker.get("title", "")}
                for blocker in checked["open_blockers"]
            ]
            entry["blocked_source"] = checked["source"]
        return entry

    def _neutral_pr(self, pr, with_links=False):
        entry = {
            "ref": self.pr_ref(pr["number"]),
            "number": pr["number"],
            "role": pr.get("role"),
            "status": pr_status(pr),
            "title": pr.get("title", ""),
            "url": pr.get("url", ""),
        }
        if with_links:
            entry["head_branch"] = pr.get("head_branch", "")
            entry["closes_issues"] = [
                self.issue_ref(number) for number in pr.get("closes_issues", [])
            ]
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


_require_import_time_consistency()
