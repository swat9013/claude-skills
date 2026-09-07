"""escalation rule catalog 11 本 (設計 `docs/design/dispatch-v2/system.md` §6 / 決定 19・24)。

**判断を要する事象は、すべてこの表から機械発火する**。catalog に無い事象は発行されない
(v1 の「名指し 12 事象は上限ではない」条項は廃止 — 漏れは debrief で rule を足す)。

`rule_catalog` と紛らわしいので線を書いておく (「rule」の多義は設計 §5 / §6 から継いだもの):

| module | 表しているもの |
|---|---|
| `rule_catalog` | 機械が**外部へ及ぼしてよい作用**の catalog (deploy 等)。宣言 config の `[rules]` |
| `escalation_rules` (本 module) | 機械が**判断を LLM へ委ねる事象**の catalog。宣言では on / off しない |

## 構造: 述語だけを持つ

rule の評価関数が受け取るのは**観測済みの値だけ**で、台帳も port も外部 store も触らない。
これが「11 rule すべてが観測述語のみで発火する」の実体で、規約ではなく signature で真になる。

```
観測 (reconciler)  →  subject を組む  →  rule を評価  →  Finding  →  deliver (台帳)
                       ↑ 観測値のみ        ↑ 純粋関数              ↑ dedup / 再送 / 打ち切り
```

**dedup・再送 3 回・打ち切りは rule 側に無い**。`Ledger.deliver_escalation` が 1 箇所で持つので、
rule は「今 tick で条件が当たった」だけを報告する。rule ごとに数える形にすると rule の数だけ
打ち切りの実装が要り、揺れる。

## dedupKey の作り方

**rule id + 観測値から毎 tick 再計算する** (tick を跨ぐ counter を混ぜない)。key に混ぜる観測値は
「この事象がどの出来事かを識別するもの」で選ぶ — 粗すぎると 2 度目の同じ事象が打ち切り済みの
古い escalation へ吸収されて二度と上がらず、細かすぎると状況が動くたびに inbox が埋まる。

## 網羅性

2 段で機械検査する:

1. **catalog の形** — 本 module の import 時 (`require_catalog_consistency`)。rule_id の重複 /
   未知の scope / 条件文の欠け / 評価関数の欠け / rule が 1 つも無い scope が落ちる。
   **catalog に載っていない評価関数 (rule を消した残骸) は検出しない** — `rule_catalog` の
   `EFFECTS` と違って評価関数は `CATALOG` の欄そのものなので、突き合わせる第 2 の表が無い
2. **状態組合せの被覆** — 観測語彙から組合せを**生成**し、実物の評価関数を当てて、どの rule も
   立たない組合せが `UNCOVERED_COMBINATIONS` の宣言で説明されていることを検査する
   (`tests/test_dispatch_v2_escalation_rules.py`)。新しい rule も新しい語彙の段も、分類され
   ないまま増えると落ちる

## rule を 1 本足すときに触る場所

被覆の検査が落ちる形で強制されるので黙って腐りはしないが、落ちた test から辿る先を書いておく:

1. 本 module の `CATALOG` (+ `RULE_*` 定数と評価関数)
2. 本 module の `UNCOVERED_COMBINATIONS` — 新 rule が拾うようになった組合せの宣言を消す
   (どの組合せにも当たらない宣言は残骸として落ちる)
3. `tests/test_dispatch_v2_escalation_rules.py` の rule id 集合
4. 設計 `docs/design/dispatch-v2/system.md` §6 の rule 表と「既知の未被覆」の表
"""

from collections import namedtuple

from dispatch_v2 import deploy, ports, session_vocabulary

# --- 語彙 -----------------------------------------------------------------------

RULE_PARK_POINT = "park_point"
RULE_MERGED_BUT_ALIVE = "merged_but_alive"
RULE_CL_CONFLICT = "cl_conflict"
RULE_PARKED_REVIEW_PENDING = "parked_review_pending"
RULE_SESSION_DIED_EMPTY = "session_died_empty"
RULE_SESSION_BLOCKED = "session_blocked"
RULE_ISSUE_CLOSED_ALIVE = "issue_closed_alive"
RULE_CANDIDATE_APPEARED = "candidate_appeared"
RULE_STORE_UNREACHABLE = "store_unreachable"
RULE_ORPHAN_RESOURCES = "orphan_resources"
RULE_DEPLOY_DEGRADED = "deploy_degraded"

# rule を評価する単位。**subject の種類がそのまま scope** で、reconciler 側の観測の置き場と
# 1 対 1 に対応する。scope を持たせるのは、rule を足した人が「これをどこで評価するか」を
# 宣言しないまま catalog に載せられないようにするため
SCOPE_WORK_ORDER = "work_order"
SCOPE_CANDIDATE_POOL = "candidate_pool"
SCOPE_STORE = "store"
SCOPE_RESOURCES = "resources"
SCOPE_EFFECT = "effect"

SCOPES = (SCOPE_WORK_ORDER, SCOPE_CANDIDATE_POOL, SCOPE_STORE, SCOPE_RESOURCES, SCOPE_EFFECT)

# 観測失敗が何連続したら store_unreachable を上げるか (設計 §8 rule #9)。**条件なので rule 側が
# 持つ** — 観測する側が持つと、同じ「3 連続」を数える場所と判定する場所が割れる
UNREACHABLE_STREAK = 3

# rule #5 が拾う終わり方。**`closed_by_orchestrator` は入らない** — orchestrator が意図して
# 閉じた Session の再 spawn は閉じた側が既に判断しているので、それを事象として上げ返さない
DEATHS_WITHOUT_INTENT = ("exited", "gone", "launch_error")


class RuleCatalogError(RuntimeError):
    """catalog と実装が食い違っている (import 時 fail-closed)。"""


#: escalation 1 件ぶんの発火。**台帳を知らない** — 配送は `deliver` が行う。
#: `wo_id` を持たない rule (候補プール / store / 資源 / 作用) があるので既定を置く
Finding = namedtuple("Finding", "rule_id dedup_key evidence wo_id permanent", defaults=(None, False))

#: rule 1 件。`condition` は設計 §6 の条件を 1 行で書いたもの (inbox の読み手へ返す)
EscalationRule = namedtuple("EscalationRule", "rule_id scope condition evaluate")


# --- subject (観測値だけを運ぶ) --------------------------------------------------

#: WorkOrder 1 件の観測。**台帳の view ではなく観測述語の組**で、rule が読むのはここだけ。
#: `cl_status` の `None` は**未観測** (`CL_STATUSES` の `none` = 「観測して 1 件も無い」とは
#: 別物)。潰すと、読めなかった CL が「CL が無い」に化けて rule #5 が誤って立つ
WorkOrderSnapshot = namedtuple(
    "WorkOrderSnapshot", "wo_id issue_ref phase issue_state cl_status closes_cls session"
)

#: Session 1 件の観測 (WorkOrder に Session が 1 度も無ければ `WorkOrderSnapshot.session` は None)
SessionSnapshot = namedtuple("SessionSnapshot", "session_id lifecycle activity ended_reason observed_at")

#: 候補プールの観測。`announced` が None = 一度も観測していない (初回は差分を上げない)
CandidatePoolSnapshot = namedtuple(
    "CandidatePoolSnapshot", "observed announced engaged repo ready_label observed_at"
)

#: 1 store の連続観測失敗
StoreFailureSnapshot = namedtuple(
    "StoreFailureSnapshot", "store repo streak error first_failed_at last_failed_at"
)

#: 台帳外資源の巡回結果 (worktree だけ。session は project を跨ぐので載せない)
ResourceSnapshot = namedtuple("ResourceSnapshot", "orphan_worktrees")

#: 機械作用 1 巡の結果 (今は deploy だけ)
EffectSnapshot = namedtuple("EffectSnapshot", "clone_path deploy_outcome deploy_summary")


def work_order_snapshot(work_order, *, issue, closes_cls, session):
    """観測事実から WorkOrder の subject を組む (集約と正規化はここだけで行う)。

    Args:
        work_order: 台帳の WorkOrder view (読むのは `wo_id` / `issue_ref` / `phase` だけ)
        issue: IssueFact
        closes_cls: closes 紐づきの CLFact 列。**1 件でも観測できなかったなら `None`** —
            欠けたまま畳むと conflict している CL が `open` に化ける。`None` のとき
            `cl_status` も `None` になり、**CL を読む rule は自動的に降りる** (述語が
            6 値のどれかと突き合わせているため)。CL を読まない rule (#6 / #7) は働き続ける
        session: 台帳の Session view (この WorkOrder に Session が 1 度も無ければ None)
    """
    observed = closes_cls is not None
    return WorkOrderSnapshot(
        wo_id=work_order["wo_id"],
        issue_ref=work_order["issue_ref"],
        phase=work_order["phase"],
        issue_state=issue["state"],
        cl_status=ports.aggregate_cl_status(closes_cls) if observed else None,
        closes_cls=tuple(closes_cls) if observed else (),
        session=_session_snapshot(session),
    )


def _session_snapshot(session):
    if session is None:
        return None
    return SessionSnapshot(
        session_id=session["session_id"],
        lifecycle=session["lifecycle"],
        activity=session["activity"],
        ended_reason=session["ended_reason"],
        # activity を最後に記帳した時刻。**activity は変化したときだけ記帳される**ので、
        # これが動いた = 状況が動いた。rule #6 の dedupKey がこれで出来事を識別する
        observed_at=session["updated_at"],
    )


# --- rule の評価 (すべて純粋関数) ------------------------------------------------


def _park_point(snapshot):
    """#1 Session idle ∧ closes CL open (mergeable)。

    `cl_status` が畳んだ 1 語なので、conflict / checking な CL が 1 件でもあれば `open` に
    ならない。**merged 済みの CL は駐機を妨げない** — 既に着地したぶんは判断材料ではなく、
    残りが clean な open なら「今この worker を降ろすか」を問える (`ports.CL_STATUS_PRECEDENCE`)。
    """
    if not _is_alive(snapshot.session) or snapshot.session.activity != "idle":
        return ()
    if snapshot.cl_status != "open":
        return ()
    return (
        Finding(
            rule_id=RULE_PARK_POINT,
            # **idle に入った観測時刻を混ぜる** (rule #6 と同じ理由)。駐機点は idle という
            # 移ろう状態の上に立つので、Session だけを key にすると「今回は降ろさない」と
            # ack した後、同じ worker が働いて再び idle になっても二度と問われない —
            # 降ろさない判断をした分岐こそ再 spawn が起きない分岐で、そこで沈む。
            # activity は変化したときだけ記帳されるので、この時刻が「今回の idle」を指す
            dedup_key=(
                f"{RULE_PARK_POINT}:{snapshot.wo_id}:{snapshot.session.session_id}"
                f"@{snapshot.session.observed_at}"
            ),
            evidence={
                "issue_ref": snapshot.issue_ref,
                "session_id": snapshot.session.session_id,
                "activity": snapshot.session.activity,
                "cl_status": snapshot.cl_status,
                "closes_cls": _cl_digest(snapshot.closes_cls),
            },
            wo_id=snapshot.wo_id,
        ),
    )


def _merged_but_alive(snapshot):
    """#2 closes CL merged ∧ Session alive。"""
    if snapshot.cl_status != "merged" or not _is_alive(snapshot.session):
        return ()
    return (
        Finding(
            rule_id=RULE_MERGED_BUT_ALIVE,
            # **Session 単位で足りる** (rule #1 と違って観測時刻を混ぜない)。merge は
            # 逆行しないので、条件が解除されて再び成立する経路が無い — 時刻を混ぜると
            # activity が動くたび同じ状況を上げ直すだけになる
            dedup_key=f"{RULE_MERGED_BUT_ALIVE}:{snapshot.wo_id}:{snapshot.session.session_id}",
            evidence={
                "issue_ref": snapshot.issue_ref,
                "session_id": snapshot.session.session_id,
                "activity": snapshot.session.activity,
                "closes_cls": _cl_digest(snapshot.closes_cls),
            },
            wo_id=snapshot.wo_id,
        ),
    )


def _cl_conflict(snapshot):
    """#3 closes CL conflict。**畳まずに CL ごとに上げる** — 直す対象が CL 単位だから。"""
    return tuple(
        Finding(
            rule_id=RULE_CL_CONFLICT,
            # head が動けば別の出来事。rebase して再び conflict したら上げ直る
            dedup_key=f"{RULE_CL_CONFLICT}:{fact['cl_ref']}@{fact['head_sha']}",
            evidence={
                "issue_ref": snapshot.issue_ref,
                "cl_ref": fact["cl_ref"],
                "repo": fact["repo"],
                "head_sha": fact["head_sha"],
                "mergeable": fact["mergeable"],
                "observed_at": fact["observed_at"],
            },
            wo_id=snapshot.wo_id,
        )
        for fact in snapshot.closes_cls
        if fact["status"] == "conflict"
    )


def _parked_review_pending(snapshot):
    """#4 parked ∧ unresolved threads ≥ 1。

    **未観測 (`None`) では立てない** — 読み切れなかった thread を「未解決 0 件」とも
    「未解決あり」とも読まない (`ports` が None と [] を分けている理由そのもの)。
    """
    if snapshot.phase != "parked":
        return ()
    return tuple(
        Finding(
            rule_id=RULE_PARKED_REVIEW_PENDING,
            # **thread の集合が key に入る**。返信して 1 本閉じ、別の指摘が付いたら別の出来事。
            # head_sha は混ぜない — push のたびに同じ未解決 thread を上げ直すことになる
            dedup_key=(
                f"{RULE_PARKED_REVIEW_PENDING}:{snapshot.wo_id}:{fact['cl_ref']}:"
                + ",".join(sorted(fact["unresolved_threads"]))
            ),
            evidence={
                "issue_ref": snapshot.issue_ref,
                "cl_ref": fact["cl_ref"],
                "repo": fact["repo"],
                "unresolved_threads": list(fact["unresolved_threads"]),
                "observed_at": fact["observed_at"],
            },
            wo_id=snapshot.wo_id,
        )
        for fact in snapshot.closes_cls
        if fact["unresolved_threads"]
    )


def _session_died_empty(snapshot):
    """#5 issue open ∧ Session ended (exited / gone / launch_error) ∧ closes CL 無し。

    成果が 1 つも残っていない死だけを拾う。CL が在る死は駐機と同じ形 (成果は残っている) なので、
    再開の判断は候補ではなく WorkOrder 一覧から行う。

    **issue が open であることを条件に足してある** (設計 §6 の条文には無い)。この rule が問う
    のは「再 spawn するか」で、issue が閉じている WorkOrder は同じ tick に終端の機械遷移で
    `abandoned` へ落ちる — terminal は不変なので再 spawn の道は無く、答えようのない判断依頼が
    inbox に残ることになる。決定 22 が「issue が閉じるたび LLM が起きる」のを避けた線と同じ。
    """
    session = snapshot.session
    if snapshot.issue_state != "open" or session is None or session.lifecycle != "ended":
        return ()
    if session.ended_reason not in DEATHS_WITHOUT_INTENT or snapshot.cl_status != "none":
        return ()
    return (
        Finding(
            rule_id=RULE_SESSION_DIED_EMPTY,
            # Session 単位。再 spawn すれば新しい session_id になるので、同じ死を 2 度問わない
            dedup_key=f"{RULE_SESSION_DIED_EMPTY}:{session.session_id}",
            evidence={
                "issue_ref": snapshot.issue_ref,
                "session_id": session.session_id,
                "ended_reason": session.ended_reason,
                "phase": snapshot.phase,
            },
            wo_id=snapshot.wo_id,
        ),
    )


def _session_blocked(snapshot):
    """#6 activity = blocked (permission 待ち等で worker が止まっている)。"""
    if not _is_alive(snapshot.session) or snapshot.session.activity != "blocked":
        return ()
    return (
        Finding(
            rule_id=RULE_SESSION_BLOCKED,
            # **観測時刻を混ぜる**。blocked → 解除 → 再び blocked は別の出来事で、Session だけを
            # key にすると 2 度目が打ち切り済みの escalation へ吸収されて誰にも見えない。
            # activity は変化したときだけ記帳されるので、この時刻は blocked に入った時刻
            dedup_key=(
                f"{RULE_SESSION_BLOCKED}:{snapshot.session.session_id}"
                f"@{snapshot.session.observed_at}"
            ),
            evidence={
                "issue_ref": snapshot.issue_ref,
                "session_id": snapshot.session.session_id,
                "activity": snapshot.session.activity,
                "observed_at": snapshot.session.observed_at,
            },
            wo_id=snapshot.wo_id,
        ),
    )


def _issue_closed_alive(snapshot):
    """#7 issue closed ∧ Session alive。

    Session が居るので終端の機械遷移は走らない (terminal は不変で、生きた worker の帰り道を
    塞ぐため)。誰かが閉じた issue の上で worker が走り続けている状態を人へ返す。
    """
    if snapshot.issue_state != "closed" or not _is_alive(snapshot.session):
        return ()
    return (
        Finding(
            rule_id=RULE_ISSUE_CLOSED_ALIVE,
            dedup_key=f"{RULE_ISSUE_CLOSED_ALIVE}:{snapshot.wo_id}:{snapshot.session.session_id}",
            evidence={
                "issue_ref": snapshot.issue_ref,
                "issue_state": snapshot.issue_state,
                "session_id": snapshot.session.session_id,
                "activity": snapshot.session.activity,
                "cl_status": snapshot.cl_status,
            },
            wo_id=snapshot.wo_id,
        ),
    )


def _candidate_appeared(snapshot):
    """#8 候補集合の差分 (新規)。

    **初回の観測では上げない** (`announced` が None)。既にある候補は「現れた」ではないので、
    全件を上げると導入直後に inbox が候補プールの写しになる。

    台帳にある非終端 WorkOrder の issue (`engaged`) は通知しない。**資格判定ではなく台帳の事実**
    — 着手中の issue を毎 tick「新しい候補が現れた」として上げると、orchestrator が自分で
    切った WorkOrder に起こされ続ける。
    """
    if snapshot.announced is None:
        return ()
    known = set(snapshot.announced)
    busy = set(snapshot.engaged)
    return tuple(
        Finding(
            rule_id=RULE_CANDIDATE_APPEARED,
            # 観測時刻を含めるのは、**この「現れた」がどの出現かを識別するため** — ref だけを
            # key にすると、一度 ack した issue が候補から外れて再び現れたときに ack 済みの
            # key へ吸収され、二度と通知されない (候補が沈む)
            dedup_key=f"{RULE_CANDIDATE_APPEARED}:{issue_ref}@{snapshot.observed_at}",
            evidence={
                "issue_ref": issue_ref,
                "repo": snapshot.repo,
                "ready_label": snapshot.ready_label,
                "observed_at": snapshot.observed_at,
            },
        )
        for issue_ref in snapshot.observed
        if issue_ref not in known and issue_ref not in busy
    )


def _store_unreachable(snapshot):
    """#9 同一 store の観測失敗 3 連続。"""
    if snapshot.streak < UNREACHABLE_STREAK:
        return ()
    return (
        Finding(
            rule_id=RULE_STORE_UNREACHABLE,
            # 連続の開始時刻を key に混ぜる。**store が復帰してから再び落ちたとき別の事象に
            # なる** ため — 固定 key だと打ち切り済みの古い escalation に吸収され、2 度目の
            # 障害が誰にも見えない
            dedup_key=(
                f"{RULE_STORE_UNREACHABLE}:{snapshot.store}:{snapshot.repo}:"
                f"{snapshot.first_failed_at}"
            ),
            evidence={
                "store": snapshot.store,
                "repo": snapshot.repo,
                "streak": snapshot.streak,
                "error": snapshot.error,
                "first_failed_at": snapshot.first_failed_at,
                "last_failed_at": snapshot.last_failed_at,
            },
        ),
    )


def _orphan_resources(snapshot):
    """#10 台帳外の worktree を検出 (**報告のみ。削除しない**)。"""
    if not snapshot.orphan_worktrees:
        return ()
    return (
        Finding(
            rule_id=RULE_ORPHAN_RESOURCES,
            dedup_key=f"{RULE_ORPHAN_RESOURCES}:"
            + ",".join(sorted(one["path"] for one in snapshot.orphan_worktrees)),
            evidence={
                "worktrees": snapshot.orphan_worktrees,
                "note": "報告のみ。削除は行わない (ADR 0048)",
            },
        ),
    )


def _deploy_degraded(snapshot):
    """#11 稼働 clone の `git pull --ff-only` が劣化した。

    再試行では直らない不成立 (git repo でない / upstream 未設定 / detached HEAD 等) は
    `permanent` で 1 度きりの配送にする — 直らない条件を再送に乗せると、上限まで同じ inbox 行を
    3 回押し付けたうえで打ち切る (ADR 0047)。
    """
    outcome = snapshot.deploy_outcome or {}
    if outcome.get("status") not in deploy.DEGRADED_STATUSES:
        return ()
    return (
        Finding(
            rule_id=RULE_DEPLOY_DEGRADED,
            dedup_key=f"{RULE_DEPLOY_DEGRADED}:{snapshot.clone_path}:{outcome['status']}",
            evidence={
                "clone_path": snapshot.clone_path,
                # 何をする rule が劣化したのかを catalog の言葉で載せる (受け手は inbox しか読まない)
                "rule": snapshot.deploy_summary,
                **outcome,
            },
            permanent=outcome["status"] in deploy.PERMANENT_STATUSES,
        ),
    )


# --- catalog --------------------------------------------------------------------

CATALOG = (
    EscalationRule(RULE_PARK_POINT, SCOPE_WORK_ORDER, "Session idle ∧ closes CL open (mergeable)", _park_point),
    EscalationRule(RULE_MERGED_BUT_ALIVE, SCOPE_WORK_ORDER, "closes CL merged ∧ Session alive", _merged_but_alive),
    EscalationRule(RULE_CL_CONFLICT, SCOPE_WORK_ORDER, "closes CL conflict", _cl_conflict),
    EscalationRule(
        RULE_PARKED_REVIEW_PENDING,
        SCOPE_WORK_ORDER,
        "parked ∧ unresolved threads ≥ 1",
        _parked_review_pending,
    ),
    EscalationRule(
        RULE_SESSION_DIED_EMPTY,
        SCOPE_WORK_ORDER,
        "issue open ∧ Session ended (exited / gone / launch_error) ∧ closes CL 無し",
        _session_died_empty,
    ),
    EscalationRule(RULE_SESSION_BLOCKED, SCOPE_WORK_ORDER, "activity = blocked", _session_blocked),
    EscalationRule(
        RULE_ISSUE_CLOSED_ALIVE, SCOPE_WORK_ORDER, "issue closed ∧ Session alive", _issue_closed_alive
    ),
    EscalationRule(
        RULE_CANDIDATE_APPEARED, SCOPE_CANDIDATE_POOL, "候補集合の差分 (新規)", _candidate_appeared
    ),
    EscalationRule(
        RULE_STORE_UNREACHABLE,
        SCOPE_STORE,
        f"同一 store の観測失敗 {UNREACHABLE_STREAK} 連続",
        _store_unreachable,
    ),
    EscalationRule(
        RULE_ORPHAN_RESOURCES, SCOPE_RESOURCES, "台帳外の worktree を検出", _orphan_resources
    ),
    EscalationRule(
        RULE_DEPLOY_DEGRADED, SCOPE_EFFECT, "pull 失敗 / pull 後 dirty / ff 不可", _deploy_degraded
    ),
)


def conditions():
    """rule id → 条件の 1 行。**inbox の読み手へ返す** (受け手は inbox しか読まない)。"""
    return {rule.rule_id: rule.condition for rule in CATALOG}


def condition_of(rule_id):
    """その rule の条件文。**catalog に無い rule id でも文字列を返す**。

    台帳は append-only で daemon の再起動を越えて残るのに、条件文は catalog から引く生の値
    なので、rule を rename / 削除すると**未 ack のまま残っていた escalation の条件が消える**。
    `None` を返すと「条件を持たない escalation」が inbox に現れ、rule id を覚えなくてよいと
    宣言している tool の契約が破れる — 読み手が次に何を調べればよいかだけは残す。
    """
    return conditions().get(rule_id, f"catalog に無い rule ({rule_id}) — 改名か削除された")


def rules_for(scope):
    """その scope で評価する rule (宣言順)。"""
    return tuple(rule for rule in CATALOG if rule.scope == scope)


def evaluate(scope, subject):
    """scope の rule を全部当てて Finding を集める。**台帳を触らない**。"""
    findings = []
    for rule in rules_for(scope):
        findings.extend(rule.evaluate(subject))
    return tuple(findings)


def deliver(book, findings):
    """Finding を台帳へ配送し、**実際に配送されたもの**だけを返す。

    dedup / 再送 / 打ち切りの判定は `Ledger.deliver_escalation` が持つので、ここは翻訳だけ。
    ack 済み・打ち切り済みは `None` が返るので落とす (空 nudge を撃つかの判断がこれで決まる)。
    """
    delivered = []
    for finding in findings:
        escalation = book.deliver_escalation(
            rule_id=finding.rule_id,
            dedup_key=finding.dedup_key,
            evidence=finding.evidence,
            wo_id=finding.wo_id,
            permanent=finding.permanent,
        )
        if escalation is not None:
            delivered.append(escalation)
    return delivered


# --- 網羅性: 状態組合せの被覆 -----------------------------------------------------

#: どの rule も立たない状態組合せの宣言。**理由まで書く** — 「catalog に無い事象は発行されない」
#: (設計 決定 24) を機械に守らせるには、上げないと決めた組合せが名前と理由を持っている必要が
#: ある。宣言と生成した組合せの突合は test が行い、**説明されない組合せも、どの組合せにも
#: 当たらない宣言も落とす** (新 rule / 新語彙を足したときの分類漏れがここで止まる)
UncoveredCombination = namedtuple("UncoveredCombination", "name matches reason")

UNCOVERED_COMBINATIONS = (
    UncoveredCombination(
        name="the_closes_cls_could_not_be_observed",
        matches=lambda snapshot: snapshot.cl_status is None,
        reason=(
            "closes CL を観測できていない。CL を読む rule (#1〜#5) は判断材料が欠けたまま "
            "立てず (未観測を「CL 無し」とも「conflict 無し」とも読まない)、CL を読まない "
            "rule (#6 / #7) だけが働く。**観測できないこと自体は rule #9 store_unreachable が "
            "別に上げる**ので、この組合せは沈黙ではなく分業"
        ),
    ),
    UncoveredCombination(
        name="worker_not_in_the_runtime_yet",
        matches=lambda snapshot: (
            _cl_observed(snapshot)
            and snapshot.issue_state == "open"
            and snapshot.cl_status != "merged"
            and (snapshot.session is None or snapshot.session.lifecycle in ("requested", "starting"))
        ),
        reason=(
            "Session がまだ runtime に居ない (未発行 / 起動中)。起動そのものの失敗は "
            "ended(launch_error) として rule #5 が拾うので、この段では判断を要する事象が無い"
        ),
    ),
    UncoveredCombination(
        name="issue_closed_and_the_session_is_gone",
        matches=lambda snapshot: (
            _cl_observed(snapshot)
            and snapshot.issue_state == "closed"
            and (snapshot.session is None or snapshot.session.lifecycle == "ended")
        ),
        reason=(
            "終端への機械遷移が担当する組合せ (closes CL に merged があれば completed、"
            "無ければ abandoned)。escalation にすると issue が閉じるたび LLM が起きる (設計 決定 22)。"
            "**紐づきの源が台帳の置き場 (Jira) で記録が 0 件のときだけ、その担当が成立しない** — "
            "記録 0 件は「CL が無い」の証拠にならないので機械遷移が降り、WorkOrder は非終端のまま "
            "`wo_list` に残る。ここも escalation にしない理由は同じで、記録を撃つのが正常な段取り "
            "なのだから、上げると Jira 置き場の issue が閉じるたび判断依頼になる (ADR 0059)"
        ),
    ),
    UncoveredCombination(
        name="issue_closed_while_the_session_is_starting",
        matches=lambda snapshot: (
            _cl_observed(snapshot)
            and snapshot.issue_state == "closed"
            and snapshot.session is not None
            and snapshot.session.lifecycle in ("requested", "starting")
        ),
        reason=(
            "起動中の Session。次の tick で alive (rule #7) か ended (機械遷移) のどちらかへ "
            "落ちるので、その途中の状態を事象として上げない"
        ),
    ),
    UncoveredCombination(
        name="a_running_worker_is_not_a_decision",
        matches=lambda snapshot: (
            _cl_observed(snapshot)
            and snapshot.issue_state == "open"
            and snapshot.cl_status not in ("conflict", "merged")
            and _is_alive(snapshot.session)
            and snapshot.session.activity == "running"
        ),
        reason="worker が動いている。判断を求める相手は今のところ居ない",
    ),
    UncoveredCombination(
        name="an_unclassified_activity_is_not_a_decision",
        matches=lambda snapshot: (
            _cl_observed(snapshot)
            and snapshot.issue_state == "open"
            and snapshot.cl_status not in ("conflict", "merged")
            and _is_alive(snapshot.session)
            and snapshot.session.activity is None
        ),
        reason=(
            "runtime の生 status を中立語彙へ写せていない (adapter の写像の欠落)。"
            "写せないことは worker の状況ではないので、事象として上げない"
        ),
    ),
    UncoveredCombination(
        name="an_idle_worker_without_a_clean_open_cl",
        matches=lambda snapshot: (
            snapshot.issue_state == "open"
            and snapshot.cl_status in ("none", "checking", "closed")
            and _is_alive(snapshot.session)
            and snapshot.session.activity == "idle"
        ),
        reason=(
            "駐機の判断材料が揃っていない (CL が無い / mergeable を計算中 / CL が merge されずに "
            "閉じた)。rule #1 は clean な open CL があるときだけ立つ"
        ),
    ),
    UncoveredCombination(
        name="a_session_the_orchestrator_closed_itself",
        matches=lambda snapshot: (
            _cl_observed(snapshot)
            and snapshot.issue_state == "open"
            and snapshot.cl_status != "merged"
            and snapshot.session is not None
            and snapshot.session.ended_reason == "closed_by_orchestrator"
        ),
        reason=(
            "orchestrator が意図して閉じた Session。再 spawn するかの判断は閉じた側が既に "
            "持っているので、閉じた事実を事象として上げ返さない"
        ),
    ),
    UncoveredCombination(
        name="a_dead_session_that_left_a_cl_behind",
        matches=lambda snapshot: (
            snapshot.issue_state == "open"
            and snapshot.cl_status in ("checking", "open", "closed")
            and snapshot.session is not None
            and snapshot.session.lifecycle == "ended"
            and snapshot.session.ended_reason in DEATHS_WITHOUT_INTENT
        ),
        reason=(
            "rule #5 は成果が 1 つも残っていない死だけを拾う (設計 §6)。CL が在る死は駐機と "
            "同じ形なので、再開の判断は候補プールではなく WorkOrder 一覧から行う。**沈黙する "
            "組合せとして既知** — CL が動かないまま worker だけ死ぬと誰にも上がらない"
        ),
    ),
    UncoveredCombination(
        name="a_merged_cl_while_the_issue_stays_open",
        matches=lambda snapshot: (
            snapshot.issue_state == "open"
            and snapshot.cl_status == "merged"
            and not _is_alive(snapshot.session)
        ),
        reason=(
            "**既知の穴 (#851 が残したもの)**。closes CL が merged なのに issue が open のまま "
            "(既定 branch 以外への merge 等) だと、終端の機械遷移 (引き金は issue closed) にも "
            "rule #2 (Session alive が条件) にも当たらない。設計 §6 の 11 本の枠を動かさず、"
            "本表で明示的に未被覆として残す — 12 本目を足すかは debrief の判断 (設計 決定 24)"
        ),
    ),
)


# --- 述語の共有 -----------------------------------------------------------------


def _is_alive(session):
    return session is not None and session.lifecycle == "alive"


def _cl_observed(snapshot):
    """closes CL を観測できたか。**未観測を「CL 無し」と読まない**ための唯一の判定。"""
    return snapshot.cl_status is not None


def _cl_digest(facts):
    """evidence に載せる CL の要約 (どの CL のどの地点を見て立った rule かが読める形)。"""
    return [
        {
            "cl_ref": fact["cl_ref"],
            "repo": fact["repo"],
            "status": fact["status"],
            "head_sha": fact["head_sha"],
        }
        for fact in facts
    ]


# --- 網羅性: catalog ↔ 実装 ------------------------------------------------------


def require_catalog_consistency(catalog):
    """catalog の形を検査する。ずれていれば `RuleCatalogError` で即死 (import 時)。

    **rule を足した人が宣言し忘れられる欄を全部落とす** — scope の無い rule は評価される場所が
    無く、条件文の無い rule は inbox の読み手に条件が届かず、評価関数の無い rule は永久に
    立たない。どれも「catalog には載っているのに発火しない」形で静かに欠ける。
    """
    declared = [rule.rule_id for rule in catalog]
    duplicated = sorted({one for one in declared if declared.count(one) > 1})
    if duplicated:
        raise RuleCatalogError(f"CATALOG に rule_id の重複がある {duplicated}")
    unknown_scopes = sorted({rule.scope for rule in catalog} - set(SCOPES))
    if unknown_scopes:
        raise RuleCatalogError(
            f"未知の scope {unknown_scopes} (評価される場所が無い。候補: {', '.join(SCOPES)})"
        )
    without_condition = sorted(rule.rule_id for rule in catalog if not rule.condition)
    if without_condition:
        raise RuleCatalogError(
            f"条件文が無い rule {without_condition} (inbox の読み手に条件が届かない)"
        )
    not_callable = sorted(rule.rule_id for rule in catalog if not callable(rule.evaluate))
    if not_callable:
        raise RuleCatalogError(f"評価関数が無い rule {not_callable} (永久に立たない)")
    empty_scopes = sorted(set(SCOPES) - {rule.scope for rule in catalog})
    if empty_scopes:
        raise RuleCatalogError(
            f"rule が 1 つも無い scope {empty_scopes} "
            "(観測だけして誰も評価しない場所が残る。使わないなら SCOPES から外す)"
        )


require_catalog_consistency(CATALOG)


# rule の条件が literal で書いている Session の綴り。**語彙と突き合わせる先** (下の検査)
ACTIVITIES_READ_BY_RULES = ("running", "idle", "blocked")
LIFECYCLES_READ_BY_RULES = ("requested", "starting", "alive", "ended")


def require_session_vocabulary(activities, lifecycles, ended_reasons):
    """rule が読む Session の綴りが語彙と揃っていることを検査する。

    条件を literal で書いている以上、語彙側の綴りが変わると **rule が黙って立たなくなる**。
    一致を test の assertion だけに頼らず import 時に落とす (`principle-structure-simplicity`
    の「複数 module で同じ定数を使うなら構造で保証する」)。
    """
    for read_here, vocabulary_values, what in (
        (ACTIVITIES_READ_BY_RULES, activities, "activity"),
        (LIFECYCLES_READ_BY_RULES, lifecycles, "lifecycle"),
        (DEATHS_WITHOUT_INTENT, ended_reasons, "ended reason"),
    ):
        missing = sorted(set(read_here) - set(vocabulary_values))
        if missing:
            raise RuleCatalogError(
                f"rule が読む {what} {missing} が語彙に無い "
                "(綴りが変わると rule が黙って立たなくなる)"
            )


require_session_vocabulary(
    session_vocabulary.ACTIVITIES,
    session_vocabulary.LIFECYCLES,
    session_vocabulary.ENDED_REASONS,
)
