#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.0,<3"]
# ///
"""dispatch-ops MCP server の entry point (stdio transport)。

spec (`docs/superpowers/specs/2026-08-01-issue-dispatch-redesign-design.md`) §4 に対応する。
**server はポリシーを一切持たない** — 記帳・遷移の合法性検証・観測の正規化だけを行い、
「何をすべきか」(候補選定・駐機・回収・drift 解消) は常に LLM が決める。

本 entry が提供するのは台帳 (ledger) 系・tracker 系 (observe_project / observe_issues /
observe_prs / issue_claim / issue_unclaim / issue_comment / issue_label)・pane 系 (observe_panes /
pane_spawn / pane_close / pane_send / pane_watch)・worktree 系 (observe_worktrees /
worktree_tidy / worktree_sweep)・初期設定系 (project_doctor / project_setup) と、台帳と外部
store を join する resolve の tool。

**本 module が持つのは配線だけ** — port / 台帳の生成を束ねた `Ports` container (継ぎ目は
`get_ports` 1 つ) と、tool 関数から domain module への 1 式の委譲。観測束の組み立ても phase 遷移も
domain 側 (resolve / worktree / pane / ledger) にあり、tool 関数は port の返り値 dict を展開しない。
ここに手続きが増えると、LLM 向け interface (docstring) の module に振る舞いが溜まり、domain 単体では
検証できない合成が生まれる。唯一の例外は `pane_watch` の progress adapter (MCP の Context に依存する
ので下ろせない)。

配布・登録は plugin root の `.mcp.json` (spec §4.1)。server 名 `dispatch-ops`、
tool 完全名は `mcp__plugin_swat-skills_dispatch-ops__<tool>`。

台帳ディレクトリは **server プロセスの cwd** から導出する (spec §3.1)。全 tool の応答に
`repo_key` / `ledger_dir` を載せてあるので、想定と違う台帳を書いていないかは応答で確認する。
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# uv run が PEP 723 script をどう起動しても sibling module を解決できるようにする
# (script ディレクトリの sys.path 追加は起動側の実装に依存させない)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server import MCPServer  # noqa: E402
from mcp.server.mcpserver import Context  # noqa: E402

import doctor as doctor_mod  # noqa: E402
import ledger as ledger_mod  # noqa: E402
import pane as pane_mod  # noqa: E402
import project as project_mod  # noqa: E402
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
- note は次セッションの自分へ判断の文脈を引き継ぐ自由記述欄。機械はパースしない。
  phase が動かないまま状況だけが動いたときは `ledger_annotate` で更新する (遷移を伴う
  更新は `ledger_transition` の note)
- 記帳の帰属は `actor` で決まる (events.jsonl の欄)。既定の `dispatcher` は orchestrator の
  記帳、observer は `observer`、worker の自己申告は `pane`。**server は語彙を検証しない**ので、
  名乗らない書き手の記帳は orchestrator のものと区別が付かないまま残る
- observe_issues の filter / ordering は任意。未指定なら絞らず並べ替えず全量を返す —
  どれが着手可かは環境の label 体系と issue の実態から LLM が読み取る
- observe_issues の blocker 検査は既定で走らない。未検査は `blocked: null` で返る
  (`blocked: false` = 検査して blocker 無し、とは別物)
- **issue 置き場 / PR 置き場は project の宣言で決まり、server が解決する**。tracker 系 tool の
  `repo` / `pr_repo` は未指定なら宣言の値が入るので、宣言どおりに観測するなら渡さなくてよい
  (渡すのは宣言と違う repo を 1 回だけ見るとき)。宣言の中身は `observe_project` で読める。
  宣言が無い repo は従来どおり CLI の cwd 推論のまま
- 導入時 (と挙動が怪しいとき) は `project_doctor` が前提を機械検査して**不足項目を逐語で**
  返す。宣言 config が無ければ `project_setup` が生成する — 何を宣言するかは呼び出し側が
  決め、server は書式の生成と検証だけを行う。settings は書かない (`/apply-swat-settings` の責務)
- 紐づく PR は repo で絞らない。関連 repo / fork の PR も `prs[].repo` 付きで現実の
  まま返るので、どれを根拠に採るかは呼び出し側が判断する (集約 `status` は closes 限定
  のまま、repo をまたいで算出される)
- pane 系 tool は prompt / model / effort / 送信テキストを一切解釈しない。何を起動し
  何を送るかは呼び出し側が決める (server は起動と送出だけを行う)
- pane_spawn の `repo_root` は任意。未指定なら server プロセスの clone で、別 clone で
  実装させるときだけ**既存 clone のパス**を渡す (無いパスは error — server は clone
  しない)。どの clone で実装するかは呼び出し側の判断
- pane_spawn は起動プロセスへ project の台帳ディレクトリを env で渡す。別 clone で走る
  worker の記帳も project の台帳 1 つに着地する (台帳は project に 1 つで、どの clone で
  実装したかは entry の `repo` / `agent.worktree` が持つ)
- pane label は issue slug (gh / glab は `i386`、jira は `proj-9`) で、issue 由来の起動は
  tracker を問わず `issue_ref` で行う (`tracked: true` で返る)。jira は `issue_ref` に
  番号が無いので `issue_number` は null になり、代わりに `issue_ref` が載る。resolve は
  中立 ref で join するので jira の entry も現況に出る (pane / worktree は突き合わせ、
  issue は adapter が届かず未検査、PR は台帳記録を種に観測)。worktree_tidy の回収は
  gh / glab の番号のまま
- pane の agent status は中立 4 値 (running / idle / exited / gone) と backend の生の値
  (`agent_status_raw`) を両方返す。`blocked` (人間宛の問い合わせ待ち) が中立語彙では
  running に潰れるので、送信可否は raw を見て判断する
- worktree 系 tool (observe_worktrees / worktree_tidy / worktree_sweep) の `repo_root` は
  任意。未指定なら server プロセスの clone で挙動不変。別 clone に切った worktree は
  その clone の root を渡した呼び出しでしか観測も回収もされない — **どの clone を
  何回回るかは server が決めない** (台帳 entry の `repo` / `agent.worktree` を読んで
  列挙するのは呼び出し側)
- resolve は台帳が記録した worktree パスも見に行くので、別 clone のツリーが「一覧に無い」
  だけで消失扱いにはならない。逆に、同じ番号でも記録と別パスのツリーは
  `worktree_path_mismatch` で返る (番号は clone をまたぐと一意でない)
- 紐づく PR の突合は (repo, ref) — `repo` を持たない台帳記録は別 repo の同番号 PR と
  区別できず `pr_ref_ambiguous`、記録の repo に一致する PR が無ければ `pr_repo_mismatch`
- **issue 置き場と PR 置き場は別に解く**。issue が Jira で PR が GitLab のような構成でも
  MR は観測される。PR 置き場の識別子は resolve の `pr_repo` (未指定なら `repo` へ倒す)
- cross-tracker では issue から PR を引く経路が無いので、**台帳の `prs[]` に `ref` /
  `role` / `repo` を記録した PR だけ**が観測され merge 検知に乗る。記録が無い entry は
  `stores.prs.errors` に理由が載り `checked.prs` は false。記録の `role` が誤っていれば
  mention の merged も `pr_merged` として出る (役割を知っているのが台帳しかないため)
- 中立 ref へ写せなかった観測は黙って落とさない: PR の closing reference は
  `closes_unmappable`、issue 置き場が番号体系を持たない tracker のときの `i<N>` 観測は
  resolve の `unmappable_observations`、台帳記録のうち PR 置き場で引けないものは
  `unmappable_prs`
- worktree_tidy の回収と `cleaned` への遷移は、台帳の `agent.worktree` と同じパスの
  worktree にだけ効く (記録が無い entry は issue slug だけで回収する)
- worktree_tidy の保護対象 (phase ${protected_phases}) と回収対象 (phase ${reclaim_phases}) は台帳から
  自動導出する。駐機 worktree を回収したいなら先に done へ遷移させる
- 導出の鍵は **issue slug** (`i386` / `swatcf-14`) で、番号を持たない tracker (jira) の entry も
  tidy / sweep の両方で保護・回収される。照合は綴りの分類ではなく台帳由来 slug 集合への
  membership なので、台帳に無い `feature-2` のような branch は掃除の対象に取り込まれない
- worktree_tidy は dispatch 領域 (台帳の issue slug) だけを掃除する。台帳が見られない木
  (harness の agent-* / 人間命名 / dirty で見送られ続けた木) は worktree_sweep が
  lock の pid 生存と最終活動時刻で回収する。E2BIG (worktree 蓄積で全 Bash が起動不能に
  なる障害) の予防はこちらが担う
"""

server = MCPServer(
    name=SERVER_NAME,
    version=SERVER_VERSION,
    instructions=vocabulary.render_doc(INSTRUCTIONS),
)


@dataclass
class Ports:
    """server が使う port 群の container (プロセス内で 1 つ、生成は lazy)。

    field は解決済み port の置き場で、`get_*` は未解決のときだけ 1 回組み立てる。**生成を
    ここへ集約する** — port を module 変数で持つと注入経路が module への `setattr` しか
    無くなり、キャッシュの無効化 (None 代入) までテスト側の手続きに漏れる。テストは fake を
    詰めた container を `get_ports` へ差し込む。
    """

    ledger: ledger_mod.Ledger | None = None
    pane: pane_mod.PanePort | None = None
    adapter: tracker_mod.TrackerPort | None = None
    pr_adapter: tracker_mod.TrackerPort | None = None
    declaration: dict[str, Any] | None = None
    # clone root → WorktreePort。key は**呼び出し側が渡した値そのもの** (未指定は None) で、
    # 既定 root の解決を 1 回に保つ。project の実装 repo は複数ありうる (ADR 0036) ので port も
    # 1 つに固定できないが、**server は clone の集合を持たない** — 作るのは渡された root の分だけ
    worktrees: dict[str | None, worktree_mod.WorktreePort] = field(default_factory=dict)

    def get_declaration(self) -> dict[str, Any]:
        """project の宣言 (issue 置き場 / PR 置き場) を解決する (プロセス内で 1 回だけ)。

        解決の実装は `project` module 1 箇所。**config は台帳ディレクトリ側**
        (`<台帳>/dispatch-project.toml`) にあり、台帳と同じ解決順 (`ledger.resolve_ledger_dir`) で
        解くので、別 clone で走る worker も dispatcher と同じ宣言に収束する (ADR 0036 の追補)。

        **プロセス内で cache する。** config を編集したら server を再起動しないと反映されない —
        反映されていないように見えたら、まず再起動を疑う。
        """
        if self.declaration is None:
            self.declaration = project_mod.resolve_declaration(
                repo_key_mod.main_worktree_root(Path.cwd())
            )
        return self.declaration

    def issue_tracker_name(self) -> str:
        """issue 置き場の tracker 名 (adapter を作れなくても判る)。

        adapter を組み立て済みならその tracker を採る — 名前と adapter が別々に判定されて
        食い違う経路を作らないため。
        """
        if self.adapter is not None:
            return self.adapter.tracker
        name = self.get_declaration()["issue"]["tracker"]
        if name is None:
            root = repo_key_mod.main_worktree_root(Path.cwd())
            raise tracker_mod.TrackerError(
                f"tracker を判定できない ({root}: 宣言 (config / 散文 doc) 無し + remote host 不明)"
            )
        return name

    def get_adapter(self) -> tracker_mod.TrackerPort:
        """issue 置き場の adapter を返す (プロセス内で 1 回だけ組み立てる)。"""
        if self.adapter is None:
            self.adapter = tracker_mod.get_adapter(self.issue_tracker_name())
        return self.adapter

    def get_adapter_optional(self) -> tracker_mod.TrackerPort | None:
        """issue 置き場の adapter。**未実装 tracker (Jira) では None** を返す (#576)。

        `resolve` だけがこちらを使う。issue の現況を引けないことと、pane / worktree / PR を
        突き合わせられないことは別の問い — adapter が無いだけで join ごと落とすと、Jira project の
        dispatch は台帳と現実の食い違いを 1 件も見られなくなる。
        """
        try:
            return self.get_adapter()
        except tracker_mod.TrackerError:
            return None

    def get_pr_adapter(self) -> tracker_mod.TrackerPort:
        """**PR 置き場**の adapter を返す (プロセス内で 1 回だけ組み立てる)。

        宣言の `[pr]` が第一正。無ければ issue 置き場が gh / glab のときはそれと同じ adapter
        (挙動は変わらない)、issue 置き場が PR を持たない tracker (Jira) のときだけ git remote の
        host から解く — 解決は `project.resolve_declaration` 1 箇所。
        """
        if self.pr_adapter is None:
            name = self.get_declaration()["pr"]["tracker"]
            if name is None:
                root = repo_key_mod.main_worktree_root(Path.cwd())
                raise tracker_mod.TrackerError(
                    f"PR 置き場の tracker を判定できない ({root}: 宣言の [pr] 無し + remote host 不明)"
                )
            self.pr_adapter = tracker_mod.get_adapter(name)
        return self.pr_adapter

    def get_pr_adapter_optional(self) -> tracker_mod.TrackerPort | None:
        """PR 置き場の adapter。判定できない / 未実装なら None (`resolve` 用)。"""
        try:
            return self.get_pr_adapter()
        except tracker_mod.TrackerError:
            return None

    def get_ledger(self) -> ledger_mod.Ledger:
        """台帳を開く (プロセス内で 1 回だけ repo-key を導出する)。

        server プロセスの cwd は起動後に変わらない前提。導出に失敗したら握り潰さず
        例外を上げる — 別 repo の台帳へ書くより、tool 呼び出しが失敗するほうが安い。
        """
        if self.ledger is None:
            self.ledger = ledger_mod.open_ledger()
        return self.ledger

    def get_pane(self) -> pane_mod.PanePort:
        """pane adapter を返す (プロセス内で 1 回だけ組み立てる)。

        backend の前提検査 (herdr session 内か / hook が現行か / socket に届くか) は
        adapter 側が最初の pane 操作で行う — 検査に落ちた状態を覚え込ませないため。
        """
        if self.pane is None:
            self.pane = pane_mod.get_adapter(PANE_BACKEND)
        return self.pane

    def get_worktrees(self, repo_root: str | None = None) -> worktree_mod.WorktreePort:
        """clone root ごとの WorktreePort を返す (root あたり 1 回だけ解決する)。

        未指定なら server プロセスの clone。台帳と同じ root を基準にするので、pane (linked
        worktree の中) と dispatcher (repo root) が同じ worktree 集合を見る。

        渡された root の正規化 (main worktree root への解決) は `require_clone_root` が行う。
        """
        if repo_root not in self.worktrees:
            root = (
                require_clone_root(repo_root)
                if repo_root
                else repo_key_mod.main_worktree_root(Path.cwd())
            )
            self.worktrees[repo_root] = worktree_mod.GitWorktrees(root)
        return self.worktrees[repo_root]


_ports = None


def get_ports() -> Ports:
    """port 群の container を返す (プロセス内で 1 つ)。

    **tool 層から port へ届く唯一の継ぎ目**。tool 関数は SDK が tool 引数だけで呼ぶので port を
    引数で受け取れず、差し替えはここに寄る。テストは fake を詰めた `Ports` を差し込む
    (port を 1 つずつ module 変数として突き回さない)。
    """
    global _ports
    if _ports is None:
        _ports = Ports()
    return _ports


def issue_repo(repo):
    """issue 系 tool の `repo` の実効値。**明示引数 > 宣言 > 未指定 (CLI の cwd 推論)**。

    宣言を既定値に使うのは「渡し忘れると観測・claim・label が全部よその repo へ向く」経路を
    機械的に塞ぐため (ADR 0036 の追補)。明示引数を残すのは、宣言と違う repo を 1 回だけ見る
    判断 (関連 repo の確認等) を呼び出し側から奪わないため。
    """
    if repo is not None:
        return repo
    ports = get_ports()
    return injectable_repo(
        ports.get_declaration()["issue"]["repo"], ports.get_adapter_optional()
    )


def pr_repo_default(repo):
    """PR 系 tool の `repo` の実効値。issue 置き場とは別軸で解く (#576)。"""
    if repo is not None:
        return repo
    ports = get_ports()
    return injectable_repo(
        ports.get_declaration()["pr"]["repo"], ports.get_pr_adapter_optional()
    )


def injectable_repo(declared, adapter):
    """宣言の repo 識別子を既定注入してよいか確かめてから返す。

    明示 repo scope 未対応の adapter へ宣言値を注入すると、tracker 系 tool が CLI を起動する
    前に全滅する (#620 = glab で起きた退行)。**注入をやめて CLI の cwd 推論へ倒すことはしない**
    — それは宣言が効いていない状態であり、#589 が塞いだ穴 (関連 repo で走る worker が別の
    置き場を黙って観測する) がそのまま開く。

    代わりに、識別子の出所が宣言であることを名指しして落とす。既定注入か明示引数かを知って
    いるのはこの層だけで、port 側の `require_repo_scope` は両者を区別できない — 区別が無いと
    読み手が原因を宣言 config 側に求め、宣言を消すという**穴を開ける方向の修正**へ向かう。
    """
    if declared is not None and adapter is not None and not adapter.supports_repo_scope:
        raise tracker_mod.TrackerError(
            f"宣言 (dispatch-project.toml) の repo {declared!r} を既定注入できない — "
            f"{adapter.tracker} adapter が明示 repo scope 未実装。**宣言から repo を消して"
            "回避しない** (消すと CLI の cwd 推論へ倒れ、置き場の宣言が効かないまま観測・"
            "claim・label することになる)。adapter 側に repo scope を実装する"
        )
    return declared


def default_repo_root():
    """pane を起動する既定の cwd (server プロセスの clone の main worktree root)。

    server プロセスの cwd が linked worktree の中でも main worktree へ解決する — 台帳の
    repo-key と同じ基準にすることで、dispatcher がどこから起動されても同じツリーを指す。

    呼び出し側が `pane_spawn` に `repo_root` を渡したときは、そちらが基準になる
    (project の実装 repo は cwd の clone とは限らない — ADR 0036)。
    """
    return str(repo_key_mod.main_worktree_root(Path.cwd()))


class CloneRootError(ValueError):
    """渡された clone root が実在しない (pane 起動と worktree 掃除で共通)。"""


def require_clone_root(path):
    """渡された clone root を **main worktree root の絶対パス**へ正規化して返す。

    実在しないときは失敗させる — server は clone しない (policy-free。どの clone を使うか・
    無いときにどうするかは呼び出し側の判断で、ここで `git clone` を走らせると
    「見つからないから作った」が観測不能な副作用になる)。

    正規化を pane 起動と worktree 掃除で共通にするのは、**同じ clone を指す 2 表記が別々の
    基準になるのを防ぐ**ため。linked worktree のパスを起動側だけ生で使うと、そこに切られた
    作業ツリーは掃除側の観測窓 (main worktree root 配下) から外れて回収されない。
    """
    root = Path(path).expanduser()
    if not root.is_dir():
        raise CloneRootError(
            f"clone root が無い: {root} (server は clone しない。既存 clone のパスを渡すか、"
            "先に clone してから dispatch する)"
        )
    try:
        return str(repo_key_mod.main_worktree_root(root))
    except repo_key_mod.RepoKeyError as exc:
        raise CloneRootError(f"clone root が git repo でない: {root} ({exc})") from exc


def ledger_anchor_env():
    """起動プロセスへ渡す台帳 anchor (`{DIR 環境変数: project の台帳ディレクトリ}`)。

    worker は関連 repo の clone で走ることがあり、そこで cwd から repo-key を導出させると
    project の台帳ではなく関連 repo の台帳を新設して書く (ADR 0036 の壊れ点 1)。tool 引数や
    prompt 契約ではなく env で渡すのは、**worker の LLM が忘れても壊れない機械経路**に
    するため。

    渡す前にディレクトリを実体化する — 受け取る側は実在しない DIR を fail-closed で撥ねるので、
    まだ 1 件も記帳していない台帳を配ると worker が起動直後に失敗する。

    label 起動 (observer 等) にも同じく渡す — observer も台帳を読み書きする。
    """
    return {ledger_mod.LEDGER_DIR_ENV: get_ports().get_ledger().ensure_directory()}


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
        repo: **この issue を実装する repo** の識別子 (例: swat9013/swat-skills)。issue
            置き場とは別軸で、置き場が 1 つでも実装 repo は issue ごとに変わりうる
            (ADR 0036)。server は読まない自由記述で、次セッションの再入と worktree 回収が
            「どの clone で作業したか」をここから読む
        agent: pane_backend / pane_id / pane_label / worktree / branch / model / effort。
            `worktree` は cwd と別 clone でも辿れるよう**絶対パス**で書く
        prs: [{"ref": "gh!401", "role": "closes"|"mention", "last_seen_status": ...,
            "repo": "o/other"}]。`repo` は**その PR が居る repo** で、省くと別 repo の
            同番号 PR と区別が付かず `resolve` の突合が一意に決まらない
        note: 判断の文脈 (自由記述)
        actor: 記帳の主体 (既定 dispatcher = orchestrator)。observer は `observer` を渡す

    同じ issue_ref が既に非終端 phase で記帳済みなら失敗する。再 dispatch は先に
    終端 phase (${terminal_phases}) へ遷移させてから記帳する。
    """
    return get_ports().get_ledger().record(
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
        prs: 配列全体を置き換える (要素の形は `ledger_record` と同じ。`repo` を入れると
            別 repo の同番号 PR と取り違えずに突合できる)
        actor: 遷移の主体 (既定 dispatcher = orchestrator)。observer は `observer` を渡す

    合法な遷移は ${transitions}。終端 phase からは遷移しない。**phase が動かないまま
    note だけ更新したいなら `ledger_annotate`** (同一 phase への遷移は非合法のまま)。
    """
    return get_ports().get_ledger().transition(
        issue_ref, phase, note=note, agent=agent, prs=prs, actor=actor
    )


@server.tool()
def ledger_annotate(
    issue_ref: str,
    note: str,
    actor: str = ledger_mod.DEFAULT_ACTOR,
) -> dict[str, Any]:
    """phase を変えずに note を更新する (引き継ぎの更新)。

    Args:
        issue_ref: 中立 issue ref
        note: 今その entry で何が起きているか / 次に何を待っているか (自由記述)
        actor: 記帳の主体 (既定 dispatcher = orchestrator)。observer は `observer` を渡す

    phase は動かない (遷移が要るなら `ledger_transition`)。**active のまま状況だけが
    動いた entry** — 質問の回答待ち・裏取り中で停止ではない、といった文脈の置き場で、
    書き換えは置換 (追記ではない)。events.jsonl に `annotate` として残る。

    note が空文字なら失敗する。台帳に entry が無いときも失敗する (先に `ledger_record`)。
    """
    return get_ports().get_ledger().annotate(issue_ref, note, actor=actor)


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
    return get_ports().get_ledger().report_outcome(
        issue_ref, outcome, summary=summary, actor=actor
    )


@server.tool()
def ledger_list(phases: list[str] | None = None) -> dict[str, Any]:
    """記帳済み dispatch の一覧 (再入時の状態復元に使う)。

    Args:
        phases: 絞り込む phase の配列。未指定なら全件を返す
    """
    return get_ports().get_ledger().list_view(phases)


@server.tool()
def ledger_get(issue_ref: str) -> dict[str, Any]:
    """1 件の dispatch entry を取り出す。

    Args:
        issue_ref: 中立 issue ref
    """
    return get_ports().get_ledger().get(issue_ref)


@server.tool()
def resolve(
    issue_ref: str | None = None,
    include_prs: bool = True,
    repo: str | None = None,
    pr_repo: str | None = None,
) -> dict[str, Any]:
    """台帳と外部 store (tracker / pane / git) を join した現況と drift 一覧を返す。

    Args:
        issue_ref: 中立 issue ref。渡すとその issue だけを突き合わせる (台帳に無い pane /
            worktree の検出もその issue に限る)。省略すると台帳の全 entry を見る
        include_prs: 紐づく PR も観測する (既定 on)。off にすると PR 由来の drift は
            出ない — 「PR に変化なし」ではなく「見ていない」であり、`stores.prs.observed`
            が false で返る
        repo: **issue 置き場**の repo 識別子。**未指定なら宣言 (`observe_project`) の値**、
            宣言が無ければ CLI の cwd 推論。宣言と違う repo を 1 回だけ見るときに渡す
        pr_repo: **PR 置き場**の repo (GitLab なら project) 識別子。**未指定なら宣言の
            `[pr]`**、無ければ `repo` へ倒す

    外部 store が「現実」、台帳は「意図と記録」。**drift をどう解消するかは判断しない** —
    台帳を現実に合わせるか (`ledger_transition`)、現実を直すか (`pane_spawn` で再入 /
    `worktree_tidy` で回収) は毎回呼び出し側が決める。

    観測できなかった store は `stores.<name>.observed` が false で返り、その store 由来の
    drift は 1 件も出さない (`checked` が false の側面は判定していない)。**null を「無かった」
    と読まない** — pane を観測できていないのに「pane 消失」と読むのが最も高くつく誤読。

    ## `current[].derived` (決定的な畳み方の押し下げ)

    観測列だけで評価できる規則は entry ごとに畳んで返す。生データ (`observed` の `prs[]` union
    等) は従来どおり併記されるので、必要なら現実のまま読み直せる。

    - `closes_same_repo` / `closes_other_repo`: 紐づく PR の union を `role == "closes"` と
      **台帳 entry の `repo` (dispatch した実装 repo) との一致**で割った列。前者だけが自分の
      worker の成果で、後者 (fork や第三者 repo からの closing reference) は集約 `status` には
      効いているが自分の成果ではない — **番号と repo を添えて人の判断へ残す側**
    - `unresolved_review_threads`: **駐機中 (`parked`) の entry の closes PR に付いた未解決の
      review thread** (`ref` / `repo` / `thread_id`)。対応するのは駐機ツリーへ再入した worker で、
      返信して PR を直したうえで `review_thread_resolve` で閉じる (ADR 0039)。
      **`[]` と null を潰さない** — `[]` は「観測して未解決 0 件」、null は判定していない
      (`parked` 以外 / PR 置き場の adapter が review thread 未対応 / 1 回で取り切れなかった)。
      集約 `status` の梯子には段を足していない (1 語へ潰す設計なので、足すと conflict /
      merged / review が互いを隠す) し、drift でもない (台帳側に期待が無く、突合する相手が
      居ないため)
    - `mechanical_done`: `done` を記帳してよいかの機械的前提の評価
      (`satisfied` / `rule_fired` / `open_predicates` / `evidence`)。rule は 2 本で、どちらも
      **pane 不在** (`exited` / `gone` / pane 自体が無い) が前提:
      `closes_merged_in_entry_repo` (自分の repo の closes PR が merged) と `issue_closed`

    **`satisfied` は 3 値。`False` (確定して満たさない) と `null` (観測が足りず決められない) を
    潰さない。** null の理由は `open_predicates` に名前で出る — `pane_unchecked` /
    `prs_unchecked` / `issue_state_unchecked` (cross-tracker では issue 側が恒久的にこれ) /
    `entry_repo_unrecorded` (台帳に `repo` が無く closes の帰属が機械では決まらない)。
    同じ理由で `closes_same_repo` も未検査なら `[]` ではなく null で返る。

    **`satisfied: true` は「記帳せよ」ではない。** 記帳するかは呼び出し側の判断で、server は
    phase を提案しない (出すのは候補と導出過程と未判定条件まで — ADR 0032)。

    **`drift` の `pr_merged` は repo で絞らない** (駐機中に merge されたことを台帳記録なしでも
    拾う経路なので、closes なら repo を問わず出る)。**帰属の根拠にするのは `derived` の側** —
    `closes_other_repo` にしか merged が居ない entry の `satisfied` は `false` のままになる。

    join の鍵は中立 issue ref なので、**番号を持たない ref (jira) の entry も現況に出る**。
    issue の現況は server の adapter が届かず `checked.issue` が false のまま返る (issue 由来の
    drift は出ない) — それを見るのは呼び出し側 (Rovo MCP 等)。`unjoinable` に落ちるのは番号を持つ
    別 tracker の entry — 番号だけで照合すると同番号の `i<N>` worktree / pane を取り違えるため。

    **PR は issue 置き場と別の tracker として観測する** (#576)。issue 置き場と同じ tracker の
    entry は issue から PR を引き、別 tracker の entry (issue = Jira / PR = GitLab) は **台帳が
    記録した `prs[]` の (repo, ref) を種に**直接引く。したがって cross-tracker では
    `ledger_record` / `ledger_transition` で `ref` / `role` / `repo` を記録した PR しか観測
    されず、記録が無い entry は `stores.prs.errors` に理由が載って `checked.prs` が false になる。
    記録の `role` が誤っていれば mention の merged も `pr_merged` として出る (役割を知っているのが
    台帳しかないため)。

    issue 置き場が番号体系を持たない tracker (Jira) の project では、`i<N>` の worktree / pane を
    中立 ref へ写せない。**それは黙って落とさず `unmappable_observations` に載る** (drift ではなく
    「鍵を作れなかった観測」)。

    worktree は **server プロセスの clone の一覧 + 台帳が記録したパス**で照合する。別 clone に
    切った worktree も `agent.worktree` を記録してあれば見に行くので、一覧に無いことが
    `worktree_missing` にはならない (記録が無い entry は元から worktree を期待しない)。
    記録パスを観測できなかった entry は `stores.worktrees.errors` に載り、その entry の
    `checked.worktree` が false になる。観測した `i<N>` が記録と別のパスなら
    `worktree_path_mismatch` — 番号が同じでも別 clone のツリーなので「在る」と読まない。

    紐づく PR の突合は **(repo, ref)** で行う。`repo` を持たない台帳記録は ref だけで引くので、
    別 repo に同番号の PR が居ると一意に決まらず `pr_ref_ambiguous` で返る (突合先を当てずっぽう
    に選ばない)。記録の `repo` に一致する PR が観測に無く、同じ ref の PR が別 repo に居るなら
    `pr_repo_mismatch` (status が変わったのではなく綴りが違う)。どちらも解消は
    `ledger_transition` の `prs` に正しい `repo` 付きで記録し直すこと。

    review thread の問い合わせは **`parked` の entry の closes PR** にだけ走る (ADR 0039)。
    他 phase では撃たないので `checked.review_threads` は false のまま — 「未解決 0 件」では
    ない。`include_prs: false` でも撃たない (観測の種になる PR を見ていないため)。

    tracker への問い合わせは非終端 phase の entry 1 件あたり CLI を約 4 回、直列で起動する。
    追跡数が増えた状態の引数なし呼び出しは 2 分を超えて自動 background 化されうる (spec §4.5)
    ので、終端 entry を溜めない (`worktree_tidy` で `cleaned` まで送る) か `issue_ref` で絞る。
    """
    ports = get_ports()
    return resolve_mod.resolve(
        ports.get_ledger().list_entries(),
        issue_tracker=ports.issue_tracker_name(),
        tracker_port=ports.get_adapter_optional(),
        pr_port=ports.get_pr_adapter_optional(),
        pane_port=ports.get_pane(),
        worktree_port=ports.get_worktrees(),
        scope_ref=issue_ref,
        include_prs=include_prs,
        repo=issue_repo(repo),
        pr_repo=pr_repo_default(pr_repo),
    )


@server.tool()
def observe_worktrees(repo_root: str | None = None) -> dict[str, Any]:
    """`i<N>` 規約の dispatch worktree 一覧と dirty 状態を返す。

    Args:
        repo_root: 観測する clone の root。**未指定なら server プロセスの clone** で、
            既定挙動は変わらない。別 clone で実装させた worktree を見るときに渡す —
            **どの clone を回るかは server が決めない** (台帳 entry の `repo` /
            `agent.worktree` から呼び出し側が列挙する)。実在しないパスは error

    規約外の worktree (main / 手動作成のツリー) は含まない。`dirty: null` は「検査できなかった」
    であって「clean」ではない — `dirty_error` に理由が入る。返り値の `root` で、意図した clone
    を見たかを確かめる。
    """
    return get_ports().get_worktrees(repo_root).observe()


@server.tool()
@vocabulary.with_rendered_doc
def worktree_tidy(repo_root: str | None = None) -> dict[str, Any]:
    """merged branch と回収対象 worktree を安全規則に従って掃除する。

    Args:
        repo_root: 掃除する clone の root。**未指定なら server プロセスの clone**。
            別 clone に切った worktree はその clone の root を渡した呼び出しでしか回収され
            ない — **root ごとに呼ぶのは呼び出し側**で、server は clone を列挙しない
            (台帳 entry の `repo` / `agent.worktree` が列挙の材料)。実在しないパスは error

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

    保護 / 回収の導出は **root によらず台帳全体から**行う。別 clone の entry まで保護側に
    数えるので、保護は過剰側 (安全側) に倒れる。回収が効くのは「その root に実在し、かつ
    **台帳の `agent.worktree` と同じパス**の worktree」だけ — issue slug は clone をまたぐ
    と一意でないので、パスが違うものは別 clone のツリーとして `excluded`
    (`recorded-path-mismatch`) に載せる。記録が無い entry は従来どおり slug だけで回収する。

    照合の鍵は **issue slug** (`i386` / `swatcf-14`) で、台帳から導出した slug 集合への
    membership で決まる。番号を持たない tracker (jira) の worktree もこれで保護・回収される
    (#621)。slug を作れなかった entry は `ledger.unmappable` に載る (黙って落とさない)。

    entry を `cleaned` へ送るのも記録パスと一致した回収だけ。一致しない回収は
    `ledger.unattributed` に載り、その entry は回収対象のまま残る (取り違えて `cleaned` に
    すると、本物のツリーが二度と回収されない)。

    **merged branch 経路は記録パスを見ない** — その clone で default branch に merge 済みの
    ツリーはその clone の掃除対象であって、台帳の記録とは独立した hygiene だから (保護 phase
    の slug はこの経路でも避ける)。台帳 entry の slug と一致しても、記録パスが違えば遷移はせず
    `ledger.unattributed` に載る。
    """
    ports = get_ports()
    return worktree_mod.tidy_dispatches(ports.get_worktrees(repo_root), ports.get_ledger())


@server.tool()
@vocabulary.with_rendered_doc
def worktree_sweep(
    grace_hours: float = worktree_mod.SWEEP_DEFAULT_GRACE_HOURS,
    max_age_hours: float = worktree_mod.SWEEP_DEFAULT_MAX_AGE_HOURS,
    dry_run: bool = False,
    repo_root: str | None = None,
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

    `repo_root` は掃除する clone の root で、**未指定なら server プロセスの clone**。E2BIG の
    予防は clone ごとに要るので、実装 repo が複数ある project では root ごとに呼ぶ (どの clone
    を回るかは呼び出し側の判断。実在しないパスは error)。
    """
    ports = get_ports()
    return worktree_mod.sweep_dispatches(
        ports.get_worktrees(repo_root),
        ports.get_ledger(),
        grace_hours=grace_hours,
        max_age_hours=max_age_hours,
        dry_run=dry_run,
    )


@server.tool()
def observe_panes() -> dict[str, Any]:
    """pane 一覧と agent の状態を返す。

    追跡対象 (issue slug label) に絞らず**全 pane**を返す。空き slot をいくつと見るか・
    どの pane を無視するかは呼び出し側の判断で、server は `tracked` (issue 由来の
    label か) と `is_self` (dispatcher 自身か) の印だけを付ける。

    issue 由来の pane がどの issue のものかは、tracker で返り方が分かれる:
    gh / glab (`i386`) は `issue_number` に番号だけ (tracker は label に無いので
    `resolve` が補う)、jira (`swatcf-14`) は `issue_ref` に `jira:SWATCF-14`。
    **`tracked` は「issue 由来か」であって「`issue_number` を持つか」ではない** —
    稼働数を数えるなら `tracked` を、番号で突き合わせるなら `issue_number` を見る。

    各 pane の `agent_status` は中立 4 値、`agent_status_raw` は backend の生の値。
    `blocked` (permission 待ち・質問待ち) は中立語彙では running に潰れるので、
    「今このセッションへ送っていいか」は raw を見て判断する。
    """
    return get_ports().get_pane().observe_panes()


@server.tool()
async def pane_watch(
    context: Context,
    timeout_sec: int = pane_mod.WATCH_DEFAULT_TIMEOUT_SEC,
    interval_sec: int = pane_mod.WATCH_DEFAULT_INTERVAL_SEC,
) -> dict[str, Any]:
    """追跡 pane (issue slug label・自 pane 除く) の変化か timeout まで待つ。

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

    return await get_ports().get_pane().pane_watch(
        timeout_sec=timeout_sec, interval_sec=interval_sec, progress=report
    )


@server.tool()
def observe_project() -> dict[str, Any]:
    """project の宣言 (issue 置き場 / PR 置き場) を解決して返す。

    返り値::

        {"config_path": "<台帳ディレクトリ>/dispatch-project.toml" | null,
         "issue": {"tracker": "gh", "repo": "owner/name" | null, "source": "config"|"remote"},
         "pr":    {"tracker": "gh", "repo": "owner/name" | null, "source": "config"|"issue"|"remote"}}

    **この値は tracker 系 tool の `repo` / `pr_repo` の既定値として server が自動で使う。**
    宣言どおりに観測するために毎回渡す必要は無い (渡すのは宣言と違う repo を 1 回だけ見るとき)。
    この tool を呼ぶのは、置き場を報告する / cross-tracker の構成を確かめる / 観測先が想定と
    違うときに宣言の中身を見る場面。

    `source` は宣言をどこから読んだか。**宣言の源は `config` (構造化 config) 1 つだけ**で、
    `remote` = git remote の host からの**推測** (宣言が無い)、`issue` (PR 側のみ) = issue 置き場を
    継いだ。**`repo` が埋まるのは config 経由のときだけ**で、それ以外は null (= CLI の cwd 推論)。
    `remote` が返せるのは gh / glab だけなので、**Jira 置き場は config を置かない限り成立しない**。

    `config_path` は**効いた config の絶対パス**。config は台帳と同じディレクトリに置く
    (version 管理の外なので、環境ごとに置く / doctor が生成する)。`repo` が null で宣言した
    つもりなら、まず config がその path に在るかを見る。

    宣言が正しい repo を指しているかは server では検証できない (綴りの誤りは observe_issues の
    `issues[].url` に現れる)。config を編集したら **server の再起動**が要る (プロセス内 cache)。
    """
    return get_ports().get_declaration()


@server.tool()
def project_doctor() -> dict[str, Any]:
    """dispatch の前提を機械検査して**不足項目を逐語で**返す (導入時 / 挙動が怪しいとき)。

    返り値::

        {"ok": bool,                       # missing が 0 件か (unknown は落とさない)
         "summary": {"ok": N, "missing": N, "unknown": N},
         "missing": ["settings", ...],     # 落ちた検査の id
         "root": "<clone の main worktree root>", "cwd": "...",
         "checks": [{"id", "title", "status", "visibility", "detail", "remedy",
                     "items", "related"}, ...]}

    `status` は `ok` / `missing` / `unknown` の 3 値。**`unknown` は「検査できなかった」**で、
    不成立とは別 (materials が読めない層がある)。`items` は不足の逐語 — settings なら
    そのまま `.claude/settings.local.json` へ写せる entry 文字列が入る。

    `visibility` は**不成立時の見え方**。`silent` の 2 件 (宣言 config / plugin 名) が最も
    高くつく前提で、誤った置き場を黙って観測し続ける経路になる。

    検査するのは前提の充足だけで、**直しはしない**: settings の書き込みは
    `/apply-swat-settings` の責務、宣言 config の生成は `project_setup`、herdr / uv / CLI の
    install は導入者の作業。`remedy` にどれへ向かうかが入っている。

    宣言 config は `observe_project` の cache を経由せず**その場で読み直す**ので、
    `project_setup` の直後に呼んでも結果は最新になる (tracker 系 tool の既定値へ反映するには
    server の再起動が要る、という契約は変わらない)。
    """
    return doctor_mod.run_checks()


@server.tool()
def project_setup(
    issue_tracker: str,
    issue_repo: str,
    pr_tracker: str | None = None,
    pr_repo: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """置き場の宣言 config を台帳ディレクトリ直下へ生成する (未設定 project の初期設定)。

    Args:
        issue_tracker: issue 置き場の tracker (gh / glab / jira)
        issue_repo: issue 置き場の識別子 (GitHub は owner/name、GitLab は group/project、
            Jira は project key)
        pr_tracker: PR 置き場の tracker。**PR 置き場が issue 置き場と違うときだけ**渡す
            (省略すると issue 側を継ぐ)
        pr_repo: PR 置き場の識別子。`pr_tracker` を渡すなら必須
        overwrite: 既存 config を置き直す。既定では既存があれば失敗する

    **何を宣言するかは決めない** — tracker と識別子は呼び出し側 (人間の承認を得た LLM) が
    決めて渡す。server がやるのは書式の生成と検証、台帳ディレクトリの実体化だけ。

    書く前に生成物を解析し直して検証するので、書式違反の config がディスクに残ることは無い。
    既存 config は `overwrite` を明示しない限り上書きしない (宣言は version 管理の外にあり、
    上書きすると元の置き場は復元できない)。

    返り値の `restart_required` は常に true — 宣言はプロセス内 cache に載るので、
    **tracker 系 tool の既定値へ反映するには server の再起動 (`/mcp` の reconnect) が要る**。
    `project_doctor` は config を直読みするので、確認だけなら再起動前でもできる。
    """
    ports = get_ports()
    pr = None
    if pr_tracker is not None or pr_repo is not None:
        pr = {"tracker": pr_tracker, "repo": pr_repo}
    written = project_mod.write_config(
        # 宣言は台帳と同じディレクトリに置く (ADR 0036 の追補)。置き先を独立に解決せず台帳から
        # 受け取るので、「台帳を作った場所」と「宣言を置いた場所」がずれる経路が構造的に無い
        ports.get_ledger().ensure_directory(),
        {"tracker": issue_tracker, "repo": issue_repo},
        pr=pr,
        overwrite=overwrite,
    )
    return {
        **written,
        "restart_required": True,
        "note": "宣言はプロセス内 cache に載る。tracker 系 tool の既定値へ反映するには "
        "server を再起動する (`/mcp` の reconnect)。置き場の綴りが正しいかは "
        "`observe_issues` の `issues[].url` で確かめる",
    }


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
    repo: str | None = None,
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
        repo: 観測先 repo の識別子 (例: `swat9013/swat-skills`)。**未指定なら宣言
            (`observe_project` の `issue.repo`)**、宣言が無ければ CLI の cwd 推論。
            宣言と違う repo を 1 回だけ見るときに渡す

    未検査の blocker は `blocked: null` で返る。`blocked: false` (検査して blocker 無し)
    と混同しない — 検査していないものを「着手可」と読むと誤 dispatch になる。

    返り値の `repo` は**実際に使った識別子** (明示引数か宣言の値、どちらも無ければ null)。
    宣言が正しい repo を指しているかは `issues[].url` で確かめる — 綴りの誤りは server では
    検出できない。
    """
    return get_ports().get_adapter().observe_issues(
        state=state,
        limit=limit,
        labels_any=labels_any,
        labels_none=labels_none,
        assignee=assignee,
        updated_since=updated_since,
        ordering=ordering,
        descending=descending,
        include_blocked=include_blocked,
        repo=issue_repo(repo),
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
    repo_root: str | None = None,
    remote_control: bool = False,
) -> dict[str, Any]:
    """pane を割って agent セッションを起動する。

    Args:
        prompt: 起動セッションへ渡す初期指示 (**文面は呼び出し側が決める**)
        issue_ref: 中立 issue ref (gh#386 / glab#12 / jira:PROJ-9)。pane label
            (issue slug) の由来になる — gh / glab は `i386`、jira は key を小文字化した
            `proj-9`
        label: issue に紐づかない起動での pane label (issue_ref とは排他)。
            英小文字・数字・ハイフンのみ。issue slug 表記 (`i<N>` / `<project>-<N>`) は
            予約なので弾く (issue 由来の起動は issue_ref で渡す)
        worktree: 新しい作業ツリーを作って隔離する場合の名前 (例: `i386`)
        cwd: 既存の駐機 worktree へ再入する場合のパス (worktree とは排他)
        model: 起動モデル。未指定なら session default を継承
        effort: reasoning effort。未指定なら session default を継承
        repo_root: 作業させる clone のパス。**未指定なら server プロセスの clone** で、
            既定挙動は変わらない。project の実装 repo が cwd と別 clone のときに渡す —
            `worktree` を伴う起動では、作業ツリーがこの clone の下に切られる。渡した値は
            **main worktree root へ正規化**され (worktree 系 tool と同じ基準に揃える)、
            返り値の `repo_root` にはその正規化後のパスが載る。**実在しないパスは error**
            (server は clone しない。どの clone を使うか・無いときにどうするかは呼び出し側
            の判断)
        remote_control: 起動セッションで Remote Control を有効化するか (既定 `false`)。
            真なら `--remote-control <pane label>` を組む — **セッション名 = pane label**
            なので、remote 一覧で探す名前は返り値の `label` そのもの。**有効化するかは
            呼び出し側の判断**で、server は真偽値を解釈も検証もしない (label の綴りや
            issue 由来かどうかでも分岐しない)。セッション名を server が label から与えるのは、
            `--remote-control` の引数省略で初期 prompt がセッション名として食われる事故を
            構造的に塞ぐため (ADR 0044)

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

    起動プロセスには **project の台帳ディレクトリ**が環境変数で渡る (返り値の `env`)。
    別 clone で走る worker の `ledger_report_outcome` も project の台帳に着地し、関連 repo
    側に台帳が新設されない。どの clone・どの mode で起動したかは返り値の `repo_root` /
    `cwd` / `mode` で確認する。
    """
    return get_ports().get_pane().pane_spawn(
        prompt,
        require_clone_root(repo_root) if repo_root else default_repo_root(),
        issue_ref=issue_ref,
        label=label,
        worktree=worktree,
        cwd=cwd,
        model=model,
        effort=effort,
        env=ledger_anchor_env(),
        remote_control=remote_control,
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
    return get_ports().get_pane().pane_close(pane_id)


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
    ports = get_ports()
    return pane_mod.send_and_log(
        ports.get_pane(), ports.get_ledger(), pane_id, text, issue_ref=issue_ref
    )


@server.tool()
def observe_prs(
    issue_ref: str | None = None,
    limit: int = tracker_mod.DEFAULT_PR_LIMIT,
    repo: str | None = None,
) -> dict[str, Any]:
    """PR を観測する (role + status)。

    Args:
        issue_ref: 中立 issue ref。渡すとその issue に紐づく PR と 1 語の集約 status を返す。
            省略すると repo の open PR を closing issue ref 付きで返す (集約 status は
            issue 文脈が無いので null)
        limit: repo 全体を見るときの取得上限。応答の `truncated` が true なら取り残しがある
        repo: 観測先 repo の識別子。**未指定なら宣言の `pr.repo`** (`observe_project`)、
            宣言が無ければ CLI の cwd 推論。issue 置き場のものではなく **PR 置き場**の識別子

    観測先は **PR 置き場**の tracker。宣言の `[pr]` が第一正で、無ければ issue 置き場が
    gh / glab のときはそれと同じ (挙動は変わらない)、issue 置き場が PR を持たない tracker
    (Jira) のときだけ git remote の host から解く。その
    構成では repo 全体の一覧に載る closing reference を中立 issue ref へ写せない (PR 置き場の
    番号空間なので) ため、`closes_issues` に混ぜず `closes_unmappable` へ落とす (#576)。

    status は open / conflict / checking / merged / closed / none。conflict を merged より
    先に見る梯子なので、stacked PR でも人手が要る側が消えない。

    **`status` は `role: closes` の PR だけの集約、`count` / `prs[]` は closes + mention の
    union。** mention しか無い issue は `status: "none"` かつ `count: 1` になる (矛盾ではない)。
    「この issue を閉じる PR はどこまで進んだか」に `status` が答え、mention の merged は
    `prs[]` を読んで報告に添える。

    **その `status` の母集団は `closes` に列として併記される** — union を role で filter し直す
    必要は無い。`closes` が null なのは role が定義されない経路 (`issue_ref` 省略の repo 全体の
    一覧) だけで、`[]` (closes が 1 件も無い) とは別物。

    **紐づく PR は repo で絞らない**。関連 repo の worker が出した cross-repo の closes を
    落とさないため (ADR 0036)。各 PR がどこに居るかは `prs[].repo` で読む — トップレベルの
    `repo` (問い合わせ先の echo) とは別物。fork から張られた `Closes` も closes として
    集約 `status` に効くので、**根拠に採る前に `prs[].repo` を確かめる**。
    """
    ports = get_ports()
    return ports.get_pr_adapter().observe_prs(
        issue_ref,
        limit=limit,
        repo=pr_repo_default(repo),
        issue_tracker=ports.issue_tracker_name(),
    )


@server.tool()
def issue_claim(issue_ref: str, repo: str | None = None) -> dict[str, Any]:
    """issue の assignee を自分に設定する。

    Args:
        issue_ref: 中立 issue ref (gh#386 / glab#12)
        repo: 対象 repo の識別子。**未指定なら宣言の `issue.repo`** (`observe_project`)
    """
    return get_ports().get_adapter().issue_claim(issue_ref, repo=issue_repo(repo))


@server.tool()
def issue_unclaim(issue_ref: str, repo: str | None = None) -> dict[str, Any]:
    """issue の assignee を解除して候補プールへ返す。

    Args:
        issue_ref: 中立 issue ref
        repo: 対象 repo の識別子。**未指定なら宣言の `issue.repo`** (`observe_project`)
    """
    return get_ports().get_adapter().issue_unclaim(issue_ref, repo=issue_repo(repo))


@server.tool()
def issue_comment(issue_ref: str, body: str, repo: str | None = None) -> dict[str, Any]:
    """issue にコメントを投稿する。

    Args:
        issue_ref: 中立 issue ref
        body: 投稿する本文
        repo: 対象 repo の識別子。**未指定なら宣言の `issue.repo`** (`observe_project`)
    """
    return get_ports().get_adapter().issue_comment(issue_ref, body, repo=issue_repo(repo))


@server.tool()
def review_thread_resolve(thread_id: str) -> dict[str, Any]:
    """PR の review thread を resolve する (指摘へ対応した worker 自身が閉じる)。

    Args:
        thread_id: `resolve` の `current[].derived.unresolved_review_threads[].thread_id`
            (PR 置き場の tracker が発行する thread の識別子)

    観測先は **PR 置き場**の tracker。review thread を実装していない adapter は名指しで
    失敗する — 未対応を「閉じた」で覆わない。

    **閉じる前に指摘へ返信し、対応を PR へ反映してあること** (ADR 0039)。resolve は人間が
    再オープンできる可逆操作だが、返信のない resolve は閉じた根拠を履歴に残さない。
    同意できない指摘は **PR 上で反論せず** orchestrator 経由で user へ上げる。

    返る `resolved` は操作後に tracker が返した状態で、「呼び出しが成功した」とは別物。
    """
    return get_ports().get_pr_adapter().review_thread_resolve(thread_id)


@server.tool()
def issue_label(
    issue_ref: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """issue の label を付け外しする。

    Args:
        issue_ref: 中立 issue ref
        add: 付ける label
        remove: 外す label
        repo: 対象 repo の識別子。**未指定なら宣言の `issue.repo`** (`observe_project`)

    どの label が何を意味するかは環境ごとの運用で、server は解釈しない。stage 遷移や
    除外の表現は呼び出し側がこの tool で組み立てる。
    """
    return get_ports().get_adapter().issue_label(
        issue_ref, add=add, remove=remove, repo=issue_repo(repo)
    )


def main():
    server.run("stdio")


if __name__ == "__main__":
    main()
