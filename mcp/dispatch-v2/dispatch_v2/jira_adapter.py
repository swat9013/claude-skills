"""JiraAdapter — Tracker port の Jira 実装 (`acli` CLI へ shell out)。

認証は `acli` に委譲する (`acli auth login`)。本 module が持つのは **Jira 語彙 → 中立語彙の
写像だけ**で、判断は持たない。ADR 0059 が ADR 0040 決定 3 (「server に Jira adapter を
実装しない」) を覆した実装で、覆した範囲は observation の 2 経路だけである。

**CLLink port も ChangeHost port も実装しない。** ここが gh / glab と決定的に違う:

- **ChangeHost でない** — Jira は変更置き場ではない (`refs.CL_PATTERNS` に jira が無い)。
  したがって **Jira 置き場の project は必ず cross-tracker** で、`[pr]` の宣言が要る
- **CLLink でない** — Jira から GitLab / GitHub の CL を列挙する経路を持たない。紐づきは
  台帳に記録したものが源になる (`cl_record.LedgerCLLinks` / ADR 0040 決定 1)。空の列を
  返す実装を置くと、merge 済みの成果を持つ issue が「紐づき 0 件」として `abandoned` で
  terminal に落ちる (terminal は不変) ため、**port ごと持たない**のが唯一安全な形

**argv は acli 1.3 の `--help` で照合済み、応答 schema は未検証。** flag の綴りと既定 field は
実バイナリの help 出力に照合したが、`acli jira auth login` が未実施のため**実 Jira を 1 度も
撃っていない** — 応答 JSON の包み方 (`{"issues": [...]}` か素の列か / `fields` 包みの有無) は
実測できていない。gh / glab adapter と同じ既知の状態で、**テスト側で argv を逐語 pin してあるのが
唯一の記録**になる。ADR 0059 の Consequences に代償として記録してある。

gh / glab と写像が割れる箇所は次のとおり:

- **識別子が番号でなく key** (`SWATCF-14`)。中立 ref も `jira:<KEY>` で、`gh#<N>` と区切りが
  違う (`refs.ISSUE_REF_FORMATS`)
- **open / closed という state が無い**。Jira の status は project ごとに任意なので、中立語彙
  へ写せるのは **statusCategory の 3 値** (`new` / `indeterminate` / `done`) だけ。個々の
  status 名 (`In Review` 等) は読まない — 読むと project ごとに写像表が要る
- **絞り込みは JQL の組み立て**。`--label` のような flag が無いので、宣言 config の
  `ready_label` を JQL へ埋める。埋める値は**charset を検査してから**通す
  (`principle-validate-at-boundaries`) — JQL に引用符を持ち込ませない
- **応答の包み方が 2 通りありうる**。Jira REST の search は `{"issues": [...]}` を返し、acli が
  それを剥がして列で返す可能性がある。**どちらでもない形は観測失敗**にして、`.get` が
  `AttributeError` を投げて観測経路の外へ抜けるのを塞ぐ (glab の `_is_object_list` と同じ線)
"""

import re

from dispatch_v2 import adapter_cli, ports, refs

TRACKER = "jira"

# 1 回の CLI 起動に許す上限秒。理由は `gh_adapter.SUBPROCESS_TIMEOUT_SEC` と同じ (tick は
# 台帳の門を掴んで走るので、tick の CLI 呼び出しはその間 他の tool を門の外で待たせる)
SUBPROCESS_TIMEOUT_SEC = 15

# 1 回の観測で扱える候補の上限。**現実の候補プールより桁で大きく取る** — 溢れの検知は観測を
# 止めるので、実際に起こりうる件数へ上限を寄せると、正常に大きいプールが候補観測の全停止に
# 化ける (gh / glab と同じ線・同じ値)
CANDIDATE_LIMIT = 1000

# CLI へ渡す件数。**上限 + 1 件**を要求するのは gh と同じ理由 (ちょうど上限件数のとき
# 「全部見えた」と「切れた」を区別できないと、正常なプールを拒むか溢れを見逃す)。
#
# `--limit` は acli 1.3 の help によれば「Maximum number of work items to fetch」= 総数で、
# `--paginate` (「Fetch all work items by paginating through the results」) がページ送りを
# CLI 側で回す。**glab のような page ループを組まない**のはこのため。両方渡すことで、上限を
# 1 件超えたところで観測が止まり、切れた集合が差分の基準になることを防ぐ
CANDIDATE_PROBE_LIMIT = CANDIDATE_LIMIT + 1

# statusCategory → 中立語彙。**個々の status 名は読まない** — project ごとに任意なので、
# 読むと写像表が project の数だけ要る。`done` だけが closed で、残りは着手前 (`new`) と
# 進行中 (`indeterminate`)。**未知値は推測に倒さない** (open を closed と読むと機械遷移が
# 誤って terminal を確定させる。terminal は不変なので取り返しがつかない)
_STATUS_CATEGORIES = {"new": "open", "indeterminate": "open", "done": "closed"}

# Jira の project key。**先に検査してから JQL へ埋める** — 検証せずに埋めると、宣言の綴り
# 次第で JQL の構文ごと差し替わる。
#
# **charset は `refs.ISSUE_PATTERNS["jira"]` の key 部と揃える** (`[A-Z][A-Z0-9]*`)。緩めると、
# 宣言も CLI 起動も通ったうえで、返ってきた work item を中立 ref へ写す `format_issue_ref` が
# `RefError` を投げる — それは `ObservationError` ではないので観測経路の外へ抜け、その
# project の tick が丸ごと落ちる。Jira 自身は key に `_` を許さないので、狭める側に実害は無い
_PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9]*$")

# JQL へ埋めてよい label の綴り。Jira の label は空白を持てないので、この charset を外れた値は
# **宣言の誤りとして観測前に落とす** (引用符やクォート解除を JQL へ持ち込ませない)
_LABEL = re.compile(r"^[A-Za-z0-9_.\-]+$")

run_command = adapter_cli.runner(timeout_sec=SUBPROCESS_TIMEOUT_SEC)


class JiraAdapter(ports.TrackerPort):
    """acli CLI 経由の Tracker 実装 (issue 観測のみ)。"""

    tracker = TRACKER

    def __init__(self, run=None):
        # 起動境界を差し替えられる形にしてあるのは、sandbox / CI で acli を起こさずに argv を
        # 逐語検査するため (`principle-test-double-boundary`: acli は unmanaged dependency)
        self._run = run or run_command

    # --- Tracker port ----------------------------------------------------------

    def fetch_candidates(self, *, repo, label, observed_at):
        """候補プールの母集団 (statusCategory が Done でない work item)。

        **絞り込みは宣言 config が渡した値でだけ行う** — 「どの issue が着手可か」の資格判定を
        adapter に埋めると、候補選定が環境固定される (ADR 0026 / 0056)。

        **`--fields` を渡さない。** acli 1.3 の既定は
        `issuetype,key,assignee,priority,status,summary` で、要る 2 つ (`key` / `status`) が
        両方含まれる。`--fields` は custom field で不安定という報告があるので、既定で足りる
        場面で壊れうる flag を足さない。

        上限を超えたら観測失敗にする (**ちょうど上限件数は正常**)。切れた集合をそのまま返すと、
        窓を出入りする issue が毎 tick「現れた / 消えた」に見えて `candidate_appeared` が
        誤発火し、窓の外にいる候補は永久に沈む。
        """
        argv = [
            "acli", "jira", "workitem", "search",
            "--jql", _candidate_jql(repo, label),
            "--limit", str(CANDIDATE_PROBE_LIMIT),
            "--paginate",
            "--json",
        ]
        items = _require_work_item_list(self._json(argv), subject="候補")
        if len(items) > CANDIDATE_LIMIT:
            raise ports.ObservationError(
                f"候補が上限 {CANDIDATE_LIMIT} 件を超えた ({repo}, label={label!r}): "
                "切れた集合は差分の基準にできない"
            )
        return [_issue_fact(item, observed_at) for item in items]

    def fetch_issue(self, issue_ref, *, repo, observed_at):
        """work item 1 件の IssueFact。観測できなければ `ObservationError`。

        **key が宣言の project のものかを先に検査する。** Jira の key は置き場を指定せずに
        引けるので、検査を省くと宣言と違う project の issue を観測成功として記帳する
        (glab の `_require_project` が project 軸で塞いだのと同じ穴)。
        """
        key = _require_key_in_project(_issue_key(issue_ref), repo)
        raw = self._json(["acli", "jira", "workitem", "view", key, "--json"])
        if not isinstance(raw, dict):
            raise ports.ObservationError(f"work item の応答が object でない: {raw!r}")
        return _issue_fact(raw, observed_at)

    # --- 起動 -------------------------------------------------------------------

    def _json(self, argv):
        """acli を起動して stdout を JSON として読む。非 0 exit / 非 JSON は ObservationError。

        **`acli` が未導入でも同じ系統へ落ちる** (`proc.command_runner` が `OSError` を包む) —
        「CLI が無い」が「issue が無い」に化けない。
        """
        return adapter_cli.json_output(self._run, argv, cli=TRACKER)


# --- 宣言値の検証 ---------------------------------------------------------------


def _require_project_key(repo):
    """project key を検証して返す。JQL へ埋める前の唯一の関門。"""
    key = str(repo or "").strip()
    if not _PROJECT_KEY.match(key):
        raise ports.ObservationError(
            f"Jira の project key として読めない: {repo!r} "
            "(大文字始まりの大文字英数のみ。中立 ref の key 部と同じ綴りで、"
            "key は JQL へも埋まるので観測前に検査する)"
        )
    return key


def _require_label(label):
    """JQL へ埋める label を検証して返す。"""
    if not _LABEL.match(str(label)):
        raise ports.ObservationError(
            f"Jira の label として読めない: {label!r} "
            "(英数と _ . - のみ。label は JQL へ埋まるので綴りを検査してから通す)"
        )
    return label


def _require_key_in_project(key, repo):
    """work item key が宣言の project のものであることを検証して返す。"""
    project = _require_project_key(repo)
    if key.split("-", 1)[0] != project:
        raise ports.ObservationError(
            f"{key} は宣言の project ({project}) の work item ではない "
            "(key は置き場を指定せずに引けるので、宣言と違う project を観測しうる)"
        )
    return key


def _candidate_jql(repo, label):
    """候補プールの母集団を表す JQL。`label` が None なら絞らない。

    **母集団は「statusCategory が Done でない」**。gh / glab の `state = open` に当たる線で、
    個々の status 名には触れない (project ごとに任意なので、触ると環境固定になる)。
    """
    clauses = [f"project = {_require_project_key(repo)}", "statusCategory != Done"]
    if label is not None:
        clauses.append(f'labels = "{_require_label(label)}"')
    return " AND ".join(clauses)


# --- 正規化 ---------------------------------------------------------------------


def _issue_key(issue_ref):
    """中立 issue ref → Jira の work item key。jira 以外の ref は ObservationError。"""
    split = refs.split_issue_ref(issue_ref)
    if split["tracker"] != TRACKER:
        raise ports.ObservationError(
            f"{TRACKER} adapter に {split['tracker']} の ref が渡った: {issue_ref!r}"
        )
    return split["key"]


def _issue_fact(raw, observed_at):
    """work item payload → IssueFact。読めない payload は ObservationError。"""
    key = raw.get("key")
    if not isinstance(key, str) or not key:
        raise ports.ObservationError(f"work item の key を読めない: {raw!r}")
    return ports.issue_fact(
        issue_ref=refs.format_issue_ref(TRACKER, key),
        state=_issue_state(raw),
        observed_at=observed_at,
    )


def _issue_state(raw):
    """work item payload → 中立語彙の issue state。

    読むのは `status.statusCategory.key` の 1 経路だけ。**`name` へ落ちない** — 表示名は
    locale と instance 設定で変わるので、綴りが変わった瞬間に黙って別の state を答える。

    **途中の段が object でない形も語彙違反として落とす** (`_as_dict`)。`or {}` は None しか
    守らないので、`"status": "In Progress"` のように文字列で来ると `.get` が
    `AttributeError` を投げ、観測経路の外 (`_tick_one` の project 隔離) まで浮上する — その
    project の tick が丸ごと落ち、「観測できなかった」という分類も連続失敗の数え上げ
    (rule #9) も経ない。応答 schema は実 Jira で未実測なので、3 つ目の形は仮定でなく可能性。
    """
    category = _as_dict(_as_dict(_fields(raw).get("status")).get("statusCategory")).get("key")
    state = _STATUS_CATEGORIES.get(str(category).lower())
    if state is None:
        raise ports.ObservationError(
            f"未知の Jira statusCategory: {category!r} "
            f"(候補: {', '.join(sorted(_STATUS_CATEGORIES))})"
        )
    return state


def _as_dict(value):
    """object ならそのまま、そうでなければ空 dict。

    **`or {}` の代わりに使う** — あちらは `None` しか守らないので、文字列や数値が来たときに
    その先の `.get` が `AttributeError` を投げ、観測経路の外へ抜ける。空 dict へ倒せば、
    読めなかったことは呼び出し側の語彙検査が `ObservationError` として名指しで落とす。
    """
    return value if isinstance(value, dict) else {}


def _fields(raw):
    """work item payload から field 群を取り出す。

    Jira REST は `{"key": ..., "fields": {...}}` と包むが、**acli が剥がして返す可能性がある**
    (reference は JSON 形状を明示していない)。包みが在れば中を、無ければ payload 自身を読む。
    どちらでもない形 (`fields` が object でない) は包みが無い側として扱い、その先の
    `_issue_state` が語彙として落とす。
    """
    fields = raw.get("fields")
    return fields if isinstance(fields, dict) else raw


def _require_work_item_list(raw, *, subject):
    """search の応答を work item の列として読む。読めなければ ObservationError。

    Jira REST の search は `{"issues": [...]}` を返し、acli がそれを剥がして列で返す可能性が
    ある。**どちらでもない形は観測失敗**にするのが要点 — 列だけ確かめて中身を舐めると
    `.get` が `AttributeError` を投げ、観測経路の外 (`_tick_one` の project 隔離) まで浮上して、
    「観測できなかった」という分類も連続失敗の数え上げ (rule #9) も経ずに落ちる。
    """
    items = raw.get("issues") if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ports.ObservationError(f"{subject}の応答が work item の列でない: {raw!r}")
    return items
