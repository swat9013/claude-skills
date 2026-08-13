#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.0,<3"]
# ///
"""dispatch-ops MCP server の entry point (stdio transport)。

spec (`docs/superpowers/specs/2026-08-01-issue-dispatch-redesign-design.md`) §4 に対応する。
**server はポリシーを一切持たない** — 記帳・遷移の合法性検証・観測の正規化だけを行い、
「何をすべきか」(候補選定・駐機・回収・drift 解消) は常に LLM が決める。

本 entry が提供するのは台帳 (ledger) 系・tracker 系 (observe_issues / observe_prs /
issue_claim / issue_unclaim / issue_comment / issue_label)・pane 系 (observe_panes /
pane_spawn / pane_close / pane_send / pane_watch)・worktree 系 (observe_worktrees /
worktree_tidy / worktree_sweep) と、台帳と外部 store を join する resolve の tool。

**本 module が持つのは配線だけ** — port / 台帳の生成 (`get_*`) と、tool 関数から domain module
への 1 式の委譲。観測束の組み立ても phase 遷移も domain 側 (resolve / worktree / pane / ledger) に
あり、tool 関数は port の返り値 dict を展開しない。ここに手続きが増えると、LLM 向け interface
(docstring) の module に振る舞いが溜まり、domain 単体では検証できない合成が生まれる。唯一の例外は
`pane_watch` の progress adapter (MCP の Context に依存するので下ろせない)。

配布・登録は plugin root の `.mcp.json` (spec §4.1)。server 名 `dispatch-ops`、
tool 完全名は `mcp__plugin_swat-skills_dispatch-ops__<tool>`。

台帳ディレクトリは **server プロセスの cwd** から導出する (spec §3.1)。全 tool の応答に
`repo_key` / `ledger_dir` を載せてあるので、想定と違う台帳を書いていないかは応答で確認する。
"""

import sys
from pathlib import Path
from typing import Any

# uv run が PEP 723 script をどう起動しても sibling module を解決できるようにする
# (script ディレクトリの sys.path 追加は起動側の実装に依存させない)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server import MCPServer  # noqa: E402
from mcp.server.mcpserver import Context  # noqa: E402

import ledger as ledger_mod  # noqa: E402
import pane as pane_mod  # noqa: E402
import repo_key as repo_key_mod  # noqa: E402
import resolve as resolve_mod  # noqa: E402
import tracker as tracker_mod  # noqa: E402
import vocabulary  # noqa: E402
import worktree as worktree_mod  # noqa: E402

SERVER_NAME = "dispatch-ops"
SERVER_VERSION = "0.1.0"

# 本 server が使う pane backend。tmux は port 境界だけを設計して実装しない (spec §8)
PANE_BACKEND = "herdr"

INSTRUCTIONS = """\
dispatch-ops の台帳 (durable ledger) を読み書きする policy-free な server。

- 台帳は「着手した後」のライフサイクルのみを扱う。候補プール (どの issue が着手可か) は
  台帳に入れない — 毎サイクル tracker の生データから LLM が判断する
- 外部 store (tracker / pane / git) が「現実」、台帳は「意図と記録」。食い違い (drift) の
  解消判断は LLM が行う
- issue / PR は中立 ref 形式で指す: issue = gh#386 / glab#12 / jira:PROJ-9、PR = gh!401 / glab!12
- phase は ${phase_flow}。
  遷移の合法性は server が検証し、遷移するかどうかは LLM が判断する
- note は次セッションの自分へ判断の文脈を引き継ぐ自由記述欄。機械はパースしない
- observe_issues の filter / ordering は任意。未指定なら絞らず並べ替えず全量を返す —
  どれが着手可かは環境の label 体系と issue の実態から LLM が読み取る
- observe_issues の blocker 検査は既定で走らない。未検査は `blocked: null` で返る
  (`blocked: false` = 検査して blocker 無し、とは別物)
- pane 系 tool は prompt / model / effort / 送信テキストを一切解釈しない。何を起動し
  何を送るかは呼び出し側が決める (server は起動と送出だけを行う)
- pane の agent status は中立 4 値 (running / idle / exited / gone) と backend の生の値
  (`agent_status_raw`) を両方返す。`blocked` (人間宛の問い合わせ待ち) が中立語彙では
  running に潰れるので、送信可否は raw を見て判断する
- worktree_tidy の保護対象 (phase ${protected_phases}) と回収対象 (phase ${reclaim_phases}) は台帳から
  自動導出する。駐機 worktree を回収したいなら先に done へ遷移させる
- worktree_tidy は dispatch 領域 (`i<N>` + 台帳) だけを掃除する。台帳が見られない木
  (harness の agent-* / 人間命名 / dirty で見送られ続けた木) は worktree_sweep が
  lock の pid 生存と最終活動時刻で回収する。E2BIG (worktree 蓄積で全 Bash が起動不能に
  なる障害) の予防はこちらが担う
"""

server = MCPServer(
    name=SERVER_NAME,
    version=SERVER_VERSION,
    instructions=vocabulary.render_doc(INSTRUCTIONS),
)

_ledger = None
_worktrees = None


def get_worktrees():
    """WorktreePort を返す (プロセス内で 1 回だけ main worktree root を解決する)。

    台帳と同じ root を基準にするので、pane (linked worktree の中) と dispatcher (repo root)
    が同じ worktree 集合を見る。
    """
    global _worktrees
    if _worktrees is None:
        _worktrees = worktree_mod.GitWorktrees(repo_key_mod.main_worktree_root(Path.cwd()))
    return _worktrees


_pane = None
_adapter = None


def get_adapter():
    """tracker adapter を返す (プロセス内で 1 回だけ tracker を判定する)。

    判定基準は `docs/agents/issue-tracker.md` の H1 が第一正、無ければ git remote の
    host。台帳と同じく main worktree root を基準にするので、pane (worktree の中) と
    dispatcher (repo root) が同じ tracker に収束する。
    """
    global _adapter
    if _adapter is None:
        root = repo_key_mod.main_worktree_root(Path.cwd())
        name = tracker_mod.detect_tracker(root)
        if name is None:
            raise tracker_mod.TrackerError(
                f"tracker を判定できない ({root}: issue-tracker.md 無し + remote host 不明)"
            )
        _adapter = tracker_mod.get_adapter(name)
    return _adapter


def get_ledger():
    """台帳を開く (プロセス内で 1 回だけ repo-key を導出する)。

    server プロセスの cwd は起動後に変わらない前提。導出に失敗したら握り潰さず
    例外を上げる — 別 repo の台帳へ書くより、tool 呼び出しが失敗するほうが安い。
    """
    global _ledger
    if _ledger is None:
        _ledger = ledger_mod.open_ledger()
    return _ledger


def get_pane():
    """pane adapter を返す (プロセス内で 1 回だけ組み立てる)。

    backend の前提検査 (herdr session 内か / hook が現行か / socket に届くか) は
    adapter 側が最初の pane 操作で行う — 検査に落ちた状態を覚え込ませないため。
    """
    global _pane
    if _pane is None:
        _pane = pane_mod.get_adapter(PANE_BACKEND)
    return _pane


def repo_root():
    """pane を起動する既定の cwd (main worktree root)。

    server プロセスの cwd が linked worktree の中でも main worktree へ解決する — 台帳の
    repo-key と同じ基準にすることで、dispatcher がどこから起動されても同じツリーを指す。
    """
    return str(repo_key_mod.main_worktree_root(Path.cwd()))


@server.tool()
@vocabulary.with_rendered_doc
def ledger_record(
    issue_ref: str,
    title: str | None = None,
    repo: str | None = None,
    agent: dict[str, Any] | None = None,
    prs: list[dict[str, Any]] | None = None,
    note: str | None = None,
    actor: str = ledger_mod.DEFAULT_ACTOR,
) -> dict[str, Any]:
    """dispatch を新規記帳する (phase は claimed で開始)。

    Args:
        issue_ref: 中立 issue ref (gh#386 / glab#12 / jira:PROJ-9)
        title: issue の title (再入時に人が読んで思い出すため)
        repo: tracker 上の repo 識別子 (例: swat9013/swat-skills)
        agent: pane_backend / pane_id / pane_label / worktree / branch / model / effort
        prs: [{"ref": "gh!401", "role": "closes"|"mention", "last_seen_status": ...}]
        note: 判断の文脈 (自由記述)
        actor: 記帳の主体 (既定 dispatcher)

    同じ issue_ref が既に非終端 phase で記帳済みなら失敗する。再 dispatch は先に
    終端 phase (${terminal_phases}) へ遷移させてから記帳する。
    """
    return get_ledger().record(
        issue_ref, title=title, repo=repo, agent=agent, prs=prs, note=note, actor=actor
    )


@server.tool()
@vocabulary.with_rendered_doc
def ledger_transition(
    issue_ref: str,
    phase: str,
    note: str | None = None,
    agent: dict[str, Any] | None = None,
    prs: list[dict[str, Any]] | None = None,
    actor: str = ledger_mod.DEFAULT_ACTOR,
) -> dict[str, Any]:
    """phase を遷移させる (合法性は server が検証する)。

    Args:
        issue_ref: 中立 issue ref
        phase: ${phase_list}
        note: なぜその遷移をしたか (次セッションの自分への引き継ぎ)
        agent: 渡した key だけを上書きする (駐機なら {"pane_id": null})
        prs: 配列全体を置き換える
        actor: 遷移の主体 (既定 dispatcher)

    合法な遷移は ${transitions}。終端 phase からは遷移しない。
    """
    return get_ledger().transition(
        issue_ref, phase, note=note, agent=agent, prs=prs, actor=actor
    )


@server.tool()
def ledger_report_outcome(
    issue_ref: str,
    outcome: str,
    summary: str | None = None,
    actor: str = ledger_mod.OUTCOME_ACTOR,
) -> dict[str, Any]:
    """pane 側 agent の完了自己申告を記録する (phase は変えない)。

    Args:
        issue_ref: 中立 issue ref
        outcome: 緩い語彙の自己申告 (done / blocked / needs_review 等)
        summary: 何をして終わったかの要約
        actor: 申告の主体 (既定 pane)

    outcome の語彙を server は検証しない。dispatcher 側が読んで判断する材料であって、
    機械的な分岐には使わない。
    """
    return get_ledger().report_outcome(issue_ref, outcome, summary=summary, actor=actor)


@server.tool()
def ledger_list(phases: list[str] | None = None) -> dict[str, Any]:
    """記帳済み dispatch の一覧 (再入時の状態復元に使う)。

    Args:
        phases: 絞り込む phase の配列。未指定なら全件を返す
    """
    return get_ledger().list_view(phases)


@server.tool()
def ledger_get(issue_ref: str) -> dict[str, Any]:
    """1 件の dispatch entry を取り出す。

    Args:
        issue_ref: 中立 issue ref
    """
    return get_ledger().get(issue_ref)


@server.tool()
def resolve(issue_ref: str | None = None, include_prs: bool = True) -> dict[str, Any]:
    """台帳と外部 store (tracker / pane / git) を join した現況と drift 一覧を返す。

    Args:
        issue_ref: 中立 issue ref。渡すとその issue だけを突き合わせる (台帳に無い pane /
            worktree の検出もその issue に限る)。省略すると台帳の全 entry を見る
        include_prs: 紐づく PR も観測する (既定 on)。off にすると PR 由来の drift は
            出ない — 「PR に変化なし」ではなく「見ていない」であり、`stores.prs.observed`
            が false で返る

    外部 store が「現実」、台帳は「意図と記録」。**drift をどう解消するかは判断しない** —
    台帳を現実に合わせるか (`ledger_transition`)、現実を直すか (`pane_spawn` で再入 /
    `worktree_tidy` で回収) は毎回呼び出し側が決める。

    観測できなかった store は `stores.<name>.observed` が false で返り、その store 由来の
    drift は 1 件も出さない (`checked` が false の側面は判定していない)。**null を「無かった」
    と読まない** — pane を観測できていないのに「pane 消失」と読むのが最も高くつく誤読。

    tracker への問い合わせは非終端 phase の entry 1 件あたり CLI を約 5 回、直列で起動する。
    追跡数が増えた状態の引数なし呼び出しは 2 分を超えて自動 background 化されうる (spec §4.5)
    ので、終端 entry を溜めない (`worktree_tidy` で `cleaned` まで送る) か `issue_ref` で絞る。
    """
    return resolve_mod.resolve(
        get_ledger().list_entries(),
        tracker_port=get_adapter(),
        pane_port=get_pane(),
        worktree_port=get_worktrees(),
        scope_ref=issue_ref,
        include_prs=include_prs,
    )


@server.tool()
def observe_worktrees() -> dict[str, Any]:
    """`i<N>` 規約の dispatch worktree 一覧と dirty 状態を返す。

    規約外の worktree (main / 手動作成のツリー) は含まない。`dirty: null` は「検査できなかった」
    であって「clean」ではない — `dirty_error` に理由が入る。
    """
    return get_worktrees().observe()


@server.tool()
@vocabulary.with_rendered_doc
def worktree_tidy() -> dict[str, Any]:
    """merged branch と回収対象 worktree を安全規則に従って掃除する (引数なし)。

    保護対象と回収対象は**台帳から自動導出**する (spec §4.4):

    - phase ${protected_phases_code} の issue → 保護 (branch も worktree も触らない)
    - phase ${reclaim_phases_code} の issue → 回収対象。squash merge で `branch --merged` に現れない駐機
      worktree もここで回収される
    - 上記以外の merged branch → 通常どおり回収対象

    **駐機 (`parked`) の worktree は回収されない**。回収したいなら先に `ledger_transition` で
    `done` へ遷移させてから呼ぶ (spec §5.1 のループ順)。

    回収に成功した worktree を持つ `done` entry は `cleaned` へ自動遷移する — server 自身が
    今作った事実の記帳であって判断ではない。dirty で見送った worktree や、最初から存在しな
    かった worktree は遷移しない (後者は台帳と現実の食い違いなので `resolve` の領分)。

    dirty な worktree は消さず、稼働中セッション (`locked-live`) と読めない lock
    (`locked-unparsed`) は踏み越えず、`branch -d` を `-D` に昇格しない。この 3 規則は違反
    すると成果が消えるので、掃除が空振りに見えるときは `skipped` の理由を読む。

    pid の死んだ lock (pane_close 後に残る) は踏み越えて回収する — 越えないと駐機 → merge →
    回収の経路が完結せず、`done` の非終端 entry が台帳に残り続ける。
    """
    return worktree_mod.tidy_dispatches(get_worktrees(), get_ledger())


@server.tool()
@vocabulary.with_rendered_doc
def worktree_sweep(
    grace_hours: float = worktree_mod.SWEEP_DEFAULT_GRACE_HOURS,
    max_age_hours: float = worktree_mod.SWEEP_DEFAULT_MAX_AGE_HOURS,
    dry_run: bool = False,
) -> dict[str, Any]:
    """`.claude/worktrees` 配下の worktree を生成主体を問わず回収し、総数を有界化する。

    spec: `docs/superpowers/specs/2026-08-02-worktree-sweep-all-trees-design.md`。
    worktree が溜まると Bash sandbox profile が肥大し、E2BIG で全 Bash が起動不能になる
    (発症後はセッション内から回復できない)。**`worktree_tidy` では届かない木**が対象:
    harness の `agent-*` / 人間命名 / 台帳に entry の無い `i<N>` / dirty で見送られ続けた木。

    保護の順 (上から確定):

    1. 台帳が保護する issue (phase ${protected_phases_code}) → `ledger-protected`
    2. server 自身が立っている木 → `server-cwd`
    3. lock の pid が生存、または pid を読めない lock → `locked-live` / `locked-unparsed`
    4. 最終活動が不明 or `grace_hours` 以内 → `age-unknown` / `young`
    5. clean → 回収
    6. dirty かつ最終活動が `max_age_hours` より古い → force 回収 (返り値に preview 付き)
    7. それ以外 → `dirty-young` (次回の呼び出しで規則 6 が回収する)

    **契約**: 未 commit 作業は `max_age_hours` (既定 24) までしか保護されない。それを超えた木の
    未 commit 内容は返り値の `preview` (diffstat + untracked) 以外に痕跡を残さず失われる。
    branch は消さないので commit 済み作業は branch ref から復元できる。

    引数は判断ではなく dial。`dry_run=True` は判定だけを返し git の破壊操作を撃たない —
    force 回収が出る状況では先に dry run で `planned` を読むとよい。
    """
    return worktree_mod.sweep_dispatches(
        get_worktrees(),
        get_ledger(),
        grace_hours=grace_hours,
        max_age_hours=max_age_hours,
        dry_run=dry_run,
    )


@server.tool()
def observe_panes() -> dict[str, Any]:
    """pane 一覧と agent の状態を返す。

    追跡対象 (`i<N>` label) に絞らず**全 pane**を返す。空き slot をいくつと見るか・
    どの pane を無視するかは呼び出し側の判断で、server は `tracked` (issue 由来の
    label か) と `is_self` (dispatcher 自身か) の印だけを付ける。

    各 pane の `agent_status` は中立 4 値、`agent_status_raw` は backend の生の値。
    `blocked` (permission 待ち・質問待ち) は中立語彙では running に潰れるので、
    「今このセッションへ送っていいか」は raw を見て判断する。
    """
    return get_pane().observe_panes()


@server.tool()
async def pane_watch(
    context: Context,
    timeout_sec: int = pane_mod.WATCH_DEFAULT_TIMEOUT_SEC,
    interval_sec: int = pane_mod.WATCH_DEFAULT_INTERVAL_SEC,
) -> dict[str, Any]:
    """追跡 pane (`i<N>` label・自 pane 除く) の変化か timeout まで待つ。

    Args:
        timeout_sec: 最大待ち秒数 (既定 90 / 上限 110)
        interval_sec: poll 間隔秒数 (既定 10)

    event は agent_exited / pane_gone / agent_idle / timeout / no_panes。**timeout は
    失敗ではない** (変化が無かったという観測)。どの event でも呼び出し側の照合手順は
    同じで、完了判定は issue / PR の観測が正 — event は「何が変わって返ってきたか」の
    参考情報。

    上限を 110 秒に切ってあるのは、2 分を超える MCP 呼び出しが自動 background 化され、
    「結果を見て次を決める」同期フローが崩れるため。長い監視は本 tool を反復して組む。
    poll ごとに progress notification を送るので、進行中であることは呼び出し側から
    観測できる。
    """
    # MCP の Context を pane 側の progress callback へ写す唯一の adapter。domain module へ
    # 下ろすと pane.py が mcp SDK に依存し、SDK 抜きで走る pane のテストが動かなくなるので、
    # 純委譲の例外としてここに置く
    async def report(waited, total):
        """progress は best-effort — 送れなくても監視は続ける。

        進捗の通知に失敗して監視自体が落ちると、pane の変化を観測する経路が丸ごと
        止まる。通知は「呼び出し側が進行中だと分かる」ための補助でしかないので、
        送れない状況 (progress token 無しの呼び出し・transport の一時失敗) は握って
        poll を続ける側に倒す。
        """
        try:
            await context.report_progress(waited, total, f"{waited}/{total}s 監視中")
        except Exception:  # noqa: BLE001
            pass

    return await get_pane().pane_watch(
        timeout_sec=timeout_sec, interval_sec=interval_sec, progress=report
    )


@server.tool()
def observe_issues(
    state: str = "open",
    limit: int = tracker_mod.DEFAULT_ISSUE_LIMIT,
    labels_any: list[str] | None = None,
    labels_none: list[str] | None = None,
    assignee: str | None = None,
    updated_since: str | None = None,
    ordering: str | None = None,
    descending: bool = False,
    include_blocked: bool = False,
) -> dict[str, Any]:
    """issue の生データを中立 schema で返す (候補選定の判断材料)。

    Args:
        state: open / closed / all (既定 open)
        limit: tracker から取る件数の上限。応答の `truncated` が true なら取り残しがある
        labels_any: いずれかを持つ issue に絞る
        labels_none: いずれかを持つ issue を落とす
        assignee: login で絞る。`none` = 未 assign / `any` = assign 済み
        updated_since: この ISO8601 時刻以降に更新された issue に絞る
        ordering: updated / created / number。未指定なら tracker の順序のまま
        descending: ordering を降順にする (既定は昇順)
        include_blocked: blocker を検査する。1 issue あたり 1 回以上 CLI が起動するので
            filter で絞ってから立てる

    未検査の blocker は `blocked: null` で返る。`blocked: false` (検査して blocker 無し)
    と混同しない — 検査していないものを「着手可」と読むと誤 dispatch になる。
    """
    return get_adapter().observe_issues(
        state=state,
        limit=limit,
        labels_any=labels_any,
        labels_none=labels_none,
        assignee=assignee,
        updated_since=updated_since,
        ordering=ordering,
        descending=descending,
        include_blocked=include_blocked,
    )


@server.tool()
def pane_spawn(
    prompt: str,
    issue_ref: str | None = None,
    label: str | None = None,
    worktree: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> dict[str, Any]:
    """pane を割って agent セッションを起動する。

    Args:
        prompt: 起動セッションへ渡す初期指示 (**文面は呼び出し側が決める**)
        issue_ref: 中立 issue ref (gh#386 / glab#12)。pane label `i<N>` の由来になる
        label: issue に紐づかない起動での pane label (issue_ref とは排他)。
            英小文字・数字・ハイフンのみ。`i<N>` 表記は issue slug の予約なので弾く
        worktree: 新しい作業ツリーを作って隔離する場合の名前 (例: `i386`)
        cwd: 既存の駐機 worktree へ再入する場合のパス (worktree とは排他)
        model: 起動モデル。未指定なら session default を継承
        effort: reasoning effort。未指定なら session default を継承

    `issue_ref` と `label` はどちらか一方だけを渡す。label 起動の pane は台帳にも
    `tracked` にも載らない — 記帳・監視・回収の対象は issue 由来の pane だけで、label
    起動は「起動して手を離す」用途 (棚卸し skill の並列実行など) を想定している。

    worktree / cwd の組み合わせで 3 通りに分岐し、どれを実行したかは返り値の `mode` で
    返る: どちらも無し = `plain` (repo root でそのまま起動) / worktree = `created`
    (新規ツリーへ隔離) / cwd = `reentered` (既存ツリーへ再入)。再入で新しいツリーを
    作らないのは、直したい成果と別のツリーで作業してしまうため。

    同じ label の pane が既にあるときは起動しない (同じ作業対象に 2 セッションを入れると
    同じ作業ツリーへ並列書き込みして成果が壊れる)。

    起動セッションには pane label と同じ表示名を `--name` で与える (返り値の `command` に
    出る)。名前でしか相手を指せない経路 (ListAgents) から worker を引くための印で、宛先の
    主経路は初回コンタクトの返信アドレスのまま。

    agent を検出できずに `ok: false` で返ったときは **pane が残ったまま label を占有する**
    ので、そのままだと同じ label の再 dispatch が以後ずっと撥ねられる。`pane_close` で
    畳む (issue 由来なら併せて `ledger_transition` で `spawn_failed` へ送る。再試行は
    新規 entry)。
    """
    return get_pane().pane_spawn(
        prompt,
        repo_root(),
        issue_ref=issue_ref,
        label=label,
        worktree=worktree,
        cwd=cwd,
        model=model,
        effort=effort,
    )


@server.tool()
def pane_close(pane_id: str) -> dict[str, Any]:
    """pane を閉じる。

    Args:
        pane_id: 閉じる pane の id

    既に消えているのは失敗ではない (`closed: false` + `reason: not_found`)。閉じても
    worktree と assignee は残るので、駐機 (pane だけ降ろして作業ツリーを温存する) は
    本 tool の呼び出しと台帳の遷移で表す。
    """
    return get_pane().pane_close(pane_id)


@server.tool()
def pane_send(pane_id: str, text: str, issue_ref: str | None = None) -> dict[str, Any]:
    """稼働中の pane へ自由テキストを送る (送出 + submit)。

    Args:
        pane_id: 送り先の pane
        text: 送る本文 (**内容の規範は持たない**)
        issue_ref: 渡すと events.jsonl に送信を記録する (何を割り込ませたかの履歴)

    送信前に観測した agent status を返り値に載せる。`blocked` (permission 待ち・質問
    待ち) の pane へ送ると人間宛の問い合わせへ代答してしまうので、送る前に
    observe_panes で raw status を見る。pane 不在 / 自 pane / agent 終了は失敗として
    返す (受け手が居ない、または自分自身への送信)。
    """
    return pane_mod.send_and_log(
        get_pane(), get_ledger(), pane_id, text, issue_ref=issue_ref
    )


@server.tool()
def observe_prs(
    issue_ref: str | None = None, limit: int = tracker_mod.DEFAULT_PR_LIMIT
) -> dict[str, Any]:
    """PR を観測する (role + status)。

    Args:
        issue_ref: 中立 issue ref。渡すとその issue に紐づく PR と 1 語の集約 status を返す。
            省略すると repo の open PR を closing issue ref 付きで返す (集約 status は
            issue 文脈が無いので null)
        limit: repo 全体を見るときの取得上限。応答の `truncated` が true なら取り残しがある

    status は open / conflict / checking / merged / closed / none。conflict を merged より
    先に見る梯子なので、stacked PR でも人手が要る側が消えない。

    **`status` は `role: closes` の PR だけの集約、`count` / `prs[]` は closes + mention の
    union。** mention しか無い issue は `status: "none"` かつ `count: 1` になる (矛盾ではない)。
    「この issue を閉じる PR はどこまで進んだか」に `status` が答え、mention の merged は
    `prs[]` を読んで報告に添える。
    """
    return get_adapter().observe_prs(issue_ref, limit=limit)


@server.tool()
def issue_claim(issue_ref: str) -> dict[str, Any]:
    """issue の assignee を自分に設定する。

    Args:
        issue_ref: 中立 issue ref (gh#386 / glab#12)
    """
    return get_adapter().issue_claim(issue_ref)


@server.tool()
def issue_unclaim(issue_ref: str) -> dict[str, Any]:
    """issue の assignee を解除して候補プールへ返す。

    Args:
        issue_ref: 中立 issue ref
    """
    return get_adapter().issue_unclaim(issue_ref)


@server.tool()
def issue_comment(issue_ref: str, body: str) -> dict[str, Any]:
    """issue にコメントを投稿する。

    Args:
        issue_ref: 中立 issue ref
        body: 投稿する本文
    """
    return get_adapter().issue_comment(issue_ref, body)


@server.tool()
def issue_label(
    issue_ref: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict[str, Any]:
    """issue の label を付け外しする。

    Args:
        issue_ref: 中立 issue ref
        add: 付ける label
        remove: 外す label

    どの label が何を意味するかは環境ごとの運用で、server は解釈しない。stage 遷移や
    除外の表現は呼び出し側がこの tool で組み立てる。
    """
    return get_adapter().issue_label(issue_ref, add=add, remove=remove)


def main():
    server.run("stdio")


if __name__ == "__main__":
    main()
