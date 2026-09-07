#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.0,<3"]
# ///
"""dispatch-v2 MCP server の entry point (stdio transport)。

**本 server は reconciler daemon への薄い client** (設計 `docs/design/dispatch-v2/system.md`
「7. reconciler daemon」)。
台帳の読み書きも状態機械も daemon 側にあり、ここが持つのは tool の登録・LLM 向け interface
(docstring)・daemon 応答の素通しだけ。daemon が居なければ最初の tool 呼び出しで起動する。

v1 (`dispatch-ops`) と v2 は**別の server として併走する** (設計 system.md「10. 移行方針」)。
台帳は別ディレクトリ (`~/.claude/dispatch-v2`) に置き、tool は server prefix で区別される。
**`worktree_tidy` / `worktree_sweep` は v1 にも同名の tool があるが意味が違う** — v2 の
`worktree_tidy` は名指しの 1 件だけを回収し、`worktree_sweep` は削除しない read-only の報告
(v1 はどちらもまとめて掃除する側)。台帳の語彙 (`wo_*` / `session_*`) は v1 (`ledger_*` /
`pane_*`) と重ならない。

群は WorkOrder (`wo_*`) / escalation の inbox (`inbox_*`) / worker セッション (`session_*`) /
tracker と ChangeHost の観測 (`observe_*`) / 資源 (`worktree_*`) / 運用 (`dashboard_url` /
`dashboard_open`)。
tracker 作用の tool は後続 issue で足す。
"""

import functools
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

# uv run が PEP 723 script をどう起動しても sibling package を解決できるようにする
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from dispatch_v2 import actors, browser  # noqa: E402
from dispatch_v2 import client as client_mod  # noqa: E402
from dispatch_v2 import project as project_mod  # noqa: E402
from dispatch_v2 import session_vocabulary, vocabulary  # noqa: E402

SERVER_NAME = "dispatch-v2"
SERVER_VERSION = "0.1.0"

# この server が動いている実行単位を指す env (空 nudge の宛先と、worker を割る割り元として
# daemon へ渡す)。今の runtime は herdr 1 つなのでその綴りを直に置く — 表にするのは adapter が
# 2 つ目を持ったときで、今作ると使われない分岐が先に生える
RUNTIME_HANDLE_ENV = "HERDR_PANE_ID"
RUNTIME_WORKSPACE_ENV = "HERDR_WORKSPACE_ID"

INSTRUCTIONS = """\
dispatch v2 の台帳 (WorkOrder) を読み書きする policy-free な server。v1 (dispatch-ops) とは
別の台帳で、併走中。

- 正本は append-only の events.jsonl。現況は常に fold(events) で、台帳に state file は無い
- WorkOrder は issue 1 件への着手意図。phase は**意図のみ** ${phase_flow}。
  Session の生死や worktree の有無は phase に混ぜない (導出値)
- 同一 issue の非終端 WorkOrder は 1 つまで。再着手は新しい WorkOrder になり、系譜は
  `wo_get` の `lineage` で読む
- 遷移の合法性は server が検証し、遷移するかどうかは呼び出し側が判断する
- Session は worker の 1 実行。鍵は ULID で runtime handle は属性。lifecycle (事実) と
  activity (観測) を分けて持ち、activity は runtime の生値を併記する
- 作業ツリーは WorkOrder の持ち物で、Session が終わっても残る。**削除は機械が自発的に
  行わない** — `worktree_sweep` が資格を判定し、`worktree_tidy` の名指し 1 回で回収する
- 台帳外の worktree / session は報告だけする (削除しない)
- 応答はすべて daemon の memory 上の state から返る。答えた時刻は `as_of`
- **観測系は「未観測 (null)」と「観測して空 ([])」を返し分ける**。null を 0 件と読まない
- 観測する置き場は台帳ディレクトリ直下の `dispatch-project.toml` が宣言する。宣言が無い
  project では観測が走らず、`observe_candidates` が `null` + `reason` を返す
- daemon は 60 秒ごとに tracker / CL を観測し、機械的に決まることだけを記帳する: Session 不在で
  issue が閉じた WorkOrder は closes CL が merged なら completed、そうでなければ abandoned へ
  evidence 付きで落ちる。判断が要る事象は escalation として上がるだけで、機械は tracker に
  何も書かない
- **issue → CL の紐づきの源は issue 置き場が決める**。GitHub / GitLab 置き場は closing
  reference から機械が引く。Jira 置き場は引けないので `wo_record_cl` で記録したものが源に
  なり、**1 件も記録していない WorkOrder は機械遷移で終わらない** (「まだ記録していない」を
  「CL が無い」の証拠にしないため。非終端のまま残る)。**escalation は落ちない** — 記録 0 件は
  「観測して 0 件」として rule に届くので、Session の死も駐機も他の置き場と同じ条件で上がる
- **判断を要する事象は宣言的な rule catalog からだけ発火する**。どの条件で立ったかは各
  escalation の `condition` が名乗る (rule id を覚えておく必要は無い)。**catalog に無い事象は
  上がらないので、inbox が空でも「判断待ちが無い」とは限らない** — 上げないと決めた組合せに
  既知の沈黙が 3 つある (成果を残して死んだ Session / issue が open のまま merge された CL /
  Jira 置き場で CL を 1 件も記録しないまま閉じた issue)。この 3 つは `wo_list` で非終端の
  WorkOrder を並べ、`observe_sessions` (Session の生死) と `observe_cls` (CL の status) で確かめる
- **escalation の配送は pull が正**。daemon が撃つのは「inbox を見に来い」だけの空 nudge で、
  中身を運ばない — 取りこぼしても `inbox_read` を呼べば全件揃う。dedup / 再送 3 回 /
  打ち切りは全 rule 共通で daemon が管理し、終了契機は `inbox_ack`
"""

server = MCPServer(
    name=SERVER_NAME,
    version=SERVER_VERSION,
    instructions=vocabulary.render_doc(INSTRUCTIONS),
)

# 「予期した失敗」として message ごと LLM へ返す例外。列挙にない例外は crash として SDK の
# 仕分けに残す。**新しい失敗様式を足したらここにも足す** — 漏れると message が黙って消える
ANTICIPATED_ERRORS = (
    client_mod.DaemonError,
    client_mod.DaemonUnavailable,
    client_mod.DaemonStopFailed,
    project_mod.ProjectError,
)


def tool():
    """`server.tool()` に domain error → `ToolError` の翻訳を噛ませた登録 decorator。

    SDK が message ごと LLM に返すのは `ToolError` だけで、他の例外は
    `Error executing tool <name>` に潰す。潰されると LLM は失敗の理由を読めない。
    """
    register = server.tool()

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ANTICIPATED_ERRORS as exc:
                raise ToolError(str(exc)) from exc

        return register(wrapper)

    return decorate


class Scope:
    """この server プロセスが書く台帳の宛先 (root と project key)。

    project key は cwd から 1 度だけ導出して抱える — git の照会は tool 1 回ごとに払う費用では
    ないし、**プロセスの途中で宛先が変わってよい理由が無い**。
    """

    def __init__(self, root=None, project_key=None, repo_root=None, environ=None):
        # 未指定なら最初の参照で解決する。値を渡せるのはテストが実 git / 実 home を
        # 経由せずに宛先を固定するため
        self._root = root
        self._project_key = project_key
        self._repo_root = repo_root
        self._environ = os.environ if environ is None else environ

    def root(self):
        if self._root is None:
            self._root = project_mod.default_root()
        return self._root

    def project_key(self):
        if self._project_key is None:
            self._project_key = project_mod.derive_project_key()
        return self._project_key

    def repo_root(self):
        """この server の cwd が属する clone の root (= 稼働 clone)。

        **daemon ではなく server が解決する** — daemon はマシンに 1 プロセスで複数 project を
        抱えるので、どの clone から dispatch しているかは呼び出し側しか知らない。
        """
        if self._repo_root is None:
            self._repo_root = str(project_mod.main_worktree_root(os.getcwd()))
        return self._repo_root

    def notify_handle(self):
        """この server が動いている実行単位の runtime handle (取れなければ None)。

        **daemon ではなく server が解決する** — `repo_root` と同じ理屈で、どの pane から
        呼んでいるかを知っているのは cwd と環境を持つ呼び出し側だけ。daemon が自分の環境から
        読むと、lazy 起動の競合次第で「最初に daemon を起こした誰か」を宛先にしてしまう。

        env の綴りが runtime 固有なのは承知のうえで**ここで直に読む** — 解決に adapter を
        噛ませると server 側にも backend の選択が生まれ、「backend を選ぶのは daemon 1 箇所」
        (`daemon.build_server`) の線が 2 本になる。取れなければ空 nudge が飛ばないだけで、
        escalation は inbox に残る。
        """
        return self._environ.get(RUNTIME_HANDLE_ENV) or None

    def spawn_anchor(self):
        """worker を割る割り元 (この server が動いている実行単位)。取れなければ None。

        **daemon ではなく server が解決する** — daemon はマシンに 1 プロセスで長命なので、
        起動時に継承した実行単位が先に死ぬと以後の spawn が全部落ちる (gh#932)。この server は
        orchestrator セッションの子プロセスなので、その環境は今生きている pane を指す。

        workspace も一緒に名乗るのは、daemon 側が観測窓 (`pane list --workspace`) の内側か
        どうかを判定できないと、割った worker が観測の外に出たまま生き続けるため。**名乗れ
        なかったことは必ず log へ出す** — 名乗らない spawn は daemon 自身の (死んでいるかも
        しれない) 割り元へ縮退するので、黙って落ちると gh#932 の故障に戻ったことが誰にも
        見えない。
        """
        handle = self._environ.get(RUNTIME_HANDLE_ENV)
        workspace = self._environ.get(RUNTIME_WORKSPACE_ENV)
        if handle and workspace:
            return {"handle": handle, "workspace": workspace}
        print(
            f"[dispatch-v2] {RUNTIME_HANDLE_ENV} と {RUNTIME_WORKSPACE_ENV} が揃わないので "
            "割り元を名乗らない (daemon 自身の割り元へ縮退する)",
            file=sys.stderr,
        )
        return None

    def call(self, method, path, body=None):
        return client_mod.call(self.root(), method, f"/projects/{self.project_key()}{path}", body)


scope = Scope()


@tool()
@vocabulary.with_rendered_doc
def wo_create(issue_ref: str, note: str | None = None, actor: str = actors.ORCHESTRATOR) -> dict[str, Any]:
    """issue 1 件への着手意図を WorkOrder として記帳する (phase は assigned で始まる)。

    Args:
        issue_ref: 中立 issue ref (gh#850 / glab#12 / jira:PROJ-9)
        note: なぜ今この issue に着手するか (次セッションの自分への引き継ぎ)
        actor: 記帳の主体 (既定 orchestrator)

    同じ issue に非終端の WorkOrder が既にあれば失敗する。**再着手は前の WorkOrder を終端
    (${terminal_phases}) へ送ってから**新しく切る — 系譜は `wo_get` の `lineage` に出る。
    """
    return scope.call(
        "POST",
        "/workorders",
        # 稼働 clone は cwd を知っているこの server が解決して渡す (deploy rule が引く観測値)
        {"issue_ref": issue_ref, "note": note, "actor": actor, "repo_root": scope.repo_root()},
    )


@tool()
@vocabulary.with_rendered_doc
def wo_transition(
    wo_id: str, phase: str, note: str | None = None, actor: str = actors.ORCHESTRATOR
) -> dict[str, Any]:
    """WorkOrder の phase を遷移させる (合法性は server が検証する)。

    Args:
        wo_id: WorkOrder の id (`wo_create` / `wo_list` が返す)
        phase: ${phase_list}
        note: なぜその遷移をしたか
        actor: 遷移の主体 (既定 orchestrator)

    合法な遷移は ${transitions}。終端からは遷移しない (訂正は `wo_annotate` + 新しい
    WorkOrder)。phase の意味: ${phase_meanings}
    """
    return scope.call(
        "POST",
        f"/workorders/{quote(wo_id, safe='')}/transition",
        {"phase": phase, "note": note, "actor": actor},
    )


@tool()
def wo_annotate(wo_id: str, note: str, actor: str = actors.ORCHESTRATOR) -> dict[str, Any]:
    """phase を変えずに note を置き換える (引き継ぎの更新)。

    Args:
        wo_id: WorkOrder の id
        note: 今その WorkOrder で何が起きているか / 次に何を待っているか
        actor: 記帳の主体 (既定 orchestrator)

    書き換えは置換 (追記ではない)。空の note は受け付けない。
    """
    return scope.call(
        "POST", f"/workorders/{quote(wo_id, safe='')}/annotate", {"note": note, "actor": actor}
    )


@tool()
def wo_record_cl(
    wo_id: str, cl_ref: str, repo: str, role: str, actor: str = actors.ORCHESTRATOR
) -> dict[str, Any]:
    """WorkOrder に issue → CL の紐づきを記録する。

    Args:
        wo_id: WorkOrder の id
        cl_ref: 中立 CL ref (`gh!401` / `glab!12`)
        repo: **その CL が居る置き場**の識別子 (issue 置き場と違ってよい)
        role: `closes` (その CL の merge が issue を閉じる) か `mention` (言及のみ)。
            **既定を持たない** — `closes` を既定にすると、書き忘れた紐づきが機械遷移の
            根拠になり、無関係な CL の merge で WorkOrder が terminal (不変) へ落ちる
        actor: 記帳の主体 (既定 orchestrator)

    **必要なのは、issue 置き場が紐づきを自分で引けない project だけ** (今は Jira 置き場)。
    GitHub / GitLab 置き場では closing reference (`Closes #N`) から機械が引くので、ここで
    記録しなくてよい。

    記録した `closes` の CL は**機械遷移の根拠になる** — その CL が merged なら、issue が
    閉じて Session が居なくなった時点で WorkOrder が `completed` へ落ちる (terminal は不変)。
    したがって **1 件も記録しない WorkOrder は機械遷移で終わらない**: 「まだ記録していない」を
    「CL が無い」の証拠にしないため、非終端のまま残って人の判断を待つ。**止まるのは terminal の
    確定だけ**で、rule には「紐づき 0 件」として届く (escalation は他の置き場と同じに上がる)。

    同じ `cl_ref` の再記録は置換なので、`repo` / `role` の訂正はそのまま撃ち直す。誤った
    CL ref そのものを外すのは `wo_forget_cl`。
    """
    return scope.call(
        "POST",
        f"/workorders/{quote(wo_id, safe='')}/cls",
        {"cl_ref": cl_ref, "repo": repo, "role": role, "actor": actor},
    )


@tool()
def wo_forget_cl(wo_id: str, cl_ref: str, actor: str = actors.ORCHESTRATOR) -> dict[str, Any]:
    """記録した紐づきを取り消す (誤った CL ref を機械遷移の根拠から外す唯一の経路)。

    Args:
        wo_id: WorkOrder の id
        cl_ref: 取り消す CL ref
        actor: 記帳の主体 (既定 orchestrator)

    記録していない CL ref は 409 で拒む (取り消したつもりが空振りするのを防ぐ)。
    """
    return scope.call(
        "POST",
        f"/workorders/{quote(wo_id, safe='')}/cls/forget",
        {"cl_ref": cl_ref, "actor": actor},
    )


@tool()
def wo_get(wo_id: str) -> dict[str, Any]:
    """WorkOrder 1 件と、同じ issue の WorkOrder 系譜 (`lineage`) を返す。

    Args:
        wo_id: WorkOrder の id
    """
    return scope.call("GET", f"/workorders/{quote(wo_id, safe='')}")


@tool()
def wo_list(phases: list[str] | None = None, issue_ref: str | None = None) -> dict[str, Any]:
    """WorkOrder の一覧 (再入時の状態復元に使う)。

    Args:
        phases: 絞り込む phase の配列。未指定なら全件
        issue_ref: この issue の WorkOrder だけに絞る (系譜を含む全件)

    1 件の詳細と系譜は `wo_get`。
    """
    query = urlencode(
        [*(("phase", phase) for phase in phases or []), *([("issue_ref", issue_ref)] if issue_ref else [])]
    )
    return scope.call("GET", f"/workorders?{query}" if query else "/workorders")


@tool()
def observe_candidates() -> dict[str, Any]:
    """候補プール (宣言 config が指す issue 置き場) の観測 cache を読む。

    `candidates` が **`null` なら未観測**で、`reason` にその理由が入る (宣言 config が無い /
    壊れている / まだ 1 度も観測が成功していない)。`[]` は「観測して候補が 0 件」— この 2 つを
    混同すると、宣言の置き忘れが「今は着手できる issue が無い」に化ける。**理由は次の一手を
    決める**: 宣言が無いなら人が置くまで永久に埋まらず、観測未成功なら次の tick で埋まりうる。

    **どれが着手可かは判断しない** — server が返すのは宣言どおりに観測した母集団だけで、
    選定は呼び出し側 (orchestrator) が issue の実態から読み取る。
    """
    return scope.call("GET", "/candidates")


@tool()
def observe_cls() -> dict[str, Any]:
    """観測済みの CL (PR / MR) の事実と、issue → CL の紐づきを読む。

    `cls[].status` は 5 値 (conflict / merged / checking / open / closed)、`mergeable` は 3 値。
    `unresolved_threads` は **`null` が未観測** (読み切れなかった) で `[]` が「観測して 0 件」。
    `head_sha` は観測した時点の head commit で、完了の機械遷移が evidence に載せる地点。

    `cl_links` は issue ごとの紐づき観測 (`links` と、その列を観測した `observed_at`)。
    `role` が `closes` の CL だけが完了の機械遷移の根拠になる (`mention` は混ぜない)。
    """
    return scope.call("GET", "/cls")


@tool()
def inbox_read() -> dict[str, Any]:
    """未 ack の escalation (機械が判断を委ねてきたイベント) を読む。

    reconciler は判断を持たないので、条件に当たった事実だけが構造化イベントとして積まれる。
    各 escalation には発火した rule の条件 (`condition`) が付く。読んだら対応を決め、
    `inbox_ack` で閉じる (ack が再送の打ち切り契機)。

    **escalation はここでしか読めない**。daemon が撃つ nudge は「見に来い」だけの空の合図で
    中身を運ばないので、nudge を取りこぼしても本 tool を呼べば全件揃う。
    """
    handle = scope.notify_handle()
    query = f"?{urlencode({'notify_handle': handle})}" if handle else ""
    return scope.call("GET", f"/inbox{query}")


@tool()
def inbox_ack(escalation_id: str, actor: str = actors.ORCHESTRATOR) -> dict[str, Any]:
    """escalation を受け取ったと記帳する (inbox から落ちる)。

    Args:
        escalation_id: `inbox_read` が返した id
        actor: ack した主体 (既定 orchestrator)
    """
    return scope.call("POST", f"/inbox/{quote(escalation_id, safe='')}/ack", {"actor": actor})


@tool()
def wo_report_outcome(
    wo_id: str, outcome: str, summary: str | None = None, actor: str = actors.WORKER
) -> dict[str, Any]:
    """自分が担当した WorkOrder の顛末を自己申告する (worker が終了直前に呼ぶ)。

    Args:
        wo_id: 担当した WorkOrder の id
        outcome: 何をどう終えたか (「PR に到達した: #866」「人手が要って停止した: <理由>」等)
        summary: 何を作ったか / 何を検証したか / 残った判断
        actor: 申告の主体 (既定 worker)

    **phase は動かない** — 終端へ送るかどうかは orchestrator の判断か、merge 観測からの
    機械遷移が決める。申告は `wo_get` の `outcome` で読める。
    """
    return scope.call(
        "POST",
        f"/workorders/{quote(wo_id, safe='')}/outcome",
        {"outcome": outcome, "summary": summary, "actor": actor},
    )


@tool()
def session_spawn(
    wo_id: str, prompt: str, model: str | None = None, actor: str = actors.ORCHESTRATOR
) -> dict[str, Any]:
    """WorkOrder に worker セッションを 1 つ起こす (作業ツリーの用意も含む)。

    Args:
        wo_id: 起動先の WorkOrder の id
        prompt: worker へ渡す初期 prompt (server は中身を解釈しない)
        model: 起動する agent の model (未指定なら agent の既定)
        actor: 起動の主体 (既定 orchestrator)

    作業ツリーは **WorkOrder の持ち物**で、Session が終わっても残る。同じ WorkOrder への
    再 spawn は同じツリーを引き継ぐ。起動に失敗しても WorkOrder は phase を変えず、
    Session が `ended(launch_error)` として残るのでそのまま再発行できる。
    """
    anchor = scope.spawn_anchor() or {}
    return scope.call(
        "POST",
        f"/workorders/{quote(wo_id, safe='')}/sessions",
        {
            "prompt": prompt,
            "model": model,
            "actor": actor,
            "repo_root": scope.repo_root(),
            # 割り元は**この server の環境から観測して渡す**。daemon の環境は起動時の化石で、
            # 継承した pane が死ぬと以後の spawn が全部落ちる (gh#932)
            "anchor_handle": anchor.get("handle"),
            "anchor_workspace": anchor.get("workspace"),
        },
    )


@tool()
def session_send(session_id: str, text: str) -> dict[str, Any]:
    """走っている worker セッションへテキストを届ける。

    Args:
        session_id: `session_spawn` / `observe_sessions` が返す id
        text: 送る本文 (server は解釈しない)
    """
    return scope.call("POST", f"/sessions/{quote(session_id, safe='')}/send", {"text": text})


@tool()
def session_close(session_id: str, actor: str = actors.ORCHESTRATOR) -> dict[str, Any]:
    """worker セッションを閉じる (作業ツリーは残る)。

    Args:
        session_id: 閉じる Session の id
        actor: 閉じる主体 (既定 orchestrator)

    runtime に既に居なければ `ended(gone)` として記帳する。**既に終わっている Session でも
    実行単位が残っていれば閉じる** (台帳は動かさず資源だけ解放する — `worktree_sweep` の
    `stranded_sessions` に出るものがこれ)。
    """
    return scope.call("POST", f"/sessions/{quote(session_id, safe='')}/close", {"actor": actor})


@tool()
@session_vocabulary.with_rendered_doc
def observe_sessions() -> dict[str, Any]:
    """Session の一覧 (lifecycle の事実と activity の観測を runtime の生値つきで返す)。

    lifecycle: ${lifecycle_flow}

    ended.reason:${ended_reasons}

    activity (alive の間だけ):${activities}

    中立 3 値へ写せなかった生値は `activity` が null のまま `activity_raw` に残る
    (未分類を running に寄せない)。
    """
    return scope.call("GET", "/sessions")


@tool()
def worktree_sweep() -> dict[str, Any]:
    """作業ツリーの回収資格の機械判定と、台帳が抱えきれていない資源の報告 (**何も削除しない**)。

    - `candidates`: 台帳が所有する worktree の回収資格 (`eligible` と `blockers`)
    - `orphan_worktrees` / `orphan_sessions`: 台帳が知らない資源
    - `unverified_worktrees`: `.git` を読めず残骸かどうか判定できなかったツリー。**回収対象に
      数えない** (ADR 0048 — 権限で読めないだけの生きたツリーを消させないため)
    - `stranded_sessions`: 終わった Session なのに runtime に実行単位が残っているもの
      (`session_close` で閉じられる)

    どれも**報告だけ**する。消すかどうかとその手順は repo 側の判断。
    """
    return scope.call("GET", "/resources")


@tool()
def worktree_tidy(wo_id: str, actor: str = actors.ORCHESTRATOR) -> dict[str, Any]:
    """名指しした WorkOrder の作業ツリーを 1 件だけ回収する。

    Args:
        wo_id: 回収する作業ツリーを所有している WorkOrder の id
        actor: 回収を指示した主体 (既定 orchestrator)

    回収資格 (WorkOrder が終端 ∧ 非終端 Session が居ない ∧ ツリーが clean ∧ lock 無し) を
    満たさなければ拒む。資格の一覧は `worktree_sweep` で読む。
    """
    return scope.call(
        "POST", f"/workorders/{quote(wo_id, safe='')}/worktree/reclaim", {"actor": actor}
    )


@tool()
def daemon_restart() -> dict[str, Any]:
    """走っている dispatch daemon を止めて起こし直す (割り元をこのセッションのものに入れ替える)。

    起こし直した daemon は **この server の環境を継承する** — server は呼んでいるセッションの
    子プロセスなので、新しい daemon の割り元は今生きている実行単位になる。走行中の daemon が
    死んだ割り元を握っていて `session_spawn` が落ち続けるとき (gh#932)、LLM から復旧できる
    唯一の経路。**新しい割り元で `session_spawn` が live な handle を返すことまで確かめる** —
    daemon が戻ったことは復旧の証拠にならない。

    **影響はマシン全体に及ぶ**: daemon は既定 root で 1 プロセス・全 project 共有なので、
    起こし直しの数秒は全 project の観測が止まる。失われるものは無い — 台帳は append-only の
    events.jsonl が正本で現況は fold、worker は独立プロセスなので死なない。観測 cache だけが
    捨てられ、次の tick が埋め直す (それまで観測系は「未観測」を返す)。

    返り値の `stopped_pid` は止めた daemon の pid。居なければ `null` で、起こすだけになる。
    """
    return client_mod.restart_daemon(scope.root())


@tool()
def dashboard_url() -> dict[str, Any]:
    """人間が現況を 1 枚で読む dashboard の URL を答える (daemon が居なければ起こす)。

    **LLM 向けの data 源ではない** — 台帳と観測を読むのは `wo_list` / `observe_*` で、この
    tool が返すのは人間へ渡す URL だけ。`bound` が false なら開けなかった理由が `reason` に
    入る (port が塞がっている / 設定の綴りが読めない)。dashboard が開けなくても daemon の
    本務 (台帳と観測) は動いている。

    画面は daemon が書いた SQLite の投影を read-only で読むので、**開いても記帳や外部への
    作用は起きない**。project 横断で、この daemon が抱える全 project が 1 枚に並ぶ。
    """
    return _dashboard_facts()


@tool()
def dashboard_open() -> dict[str, Any]:
    """dashboard を人間の browser で開く (daemon が居なければ起こす)。

    **副作用のある tool** — 人間が画面を求めたときだけ呼ぶ。現況を LLM が読むなら `wo_list` /
    `observe_*` を、URL を人間へ文字で渡すだけなら `dashboard_url` を呼ぶ (こちらは窓を出さない)。

    返り値は `dashboard_url` の答え + `opened` / `open_reason`。**`bound` が false なら browser を
    撃たない**。`open_reason` が空になるのは確かめられた成功のときだけで、開けなかったときも、
    渡したが成否を確かめられなかったときも埋まる — 逐語でそのまま人間へ渡す。

    開く先は**この server が走っているマシンの既定 browser**。画面は read-only なので、開いても
    記帳や外部への作用は起きない。
    """
    return browser.open_for_human(_dashboard_facts())


def _dashboard_facts() -> dict[str, Any]:
    """daemon の `/health` から dashboard の bind 結果を読む (無い構成でも同じ形で答える)。"""
    health = client_mod.call(scope.root(), "GET", "/health")
    facts = health.get("dashboard")
    if facts is None:
        return {"bound": False, "url": None, "reason": "この daemon は dashboard を持たない"}
    return facts


def main():
    """stdio transport で待ち受ける (daemon は最初の tool 呼び出しで起動する)。"""
    server.run("stdio")


if __name__ == "__main__":
    main()
