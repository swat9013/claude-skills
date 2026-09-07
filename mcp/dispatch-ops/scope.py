"""server process が作用する root・台帳・置き場 adapter の解決 (CONTEXT.md の **scope**)。

project (宣言の単位) を process 側から見た値。宣言の解決自体は持たず `project.resolve_declaration`
を呼ぶ (ADR 0036: 解決は project.py のみ)。

**root / 台帳 / 宣言は eager に 1 回解決して immutable**、adapter は lazy。process の cwd から
root を導出するのは `resolve_scope` 1 箇所で、以後は解決済みの `Scope.root` を配る — 導出が
散ると、同じ process の中で「どのツリーを指しているか」が呼び出し経路ごとに変わりうる。
adapter を lazy に残すのは、台帳しか触らない tool に tracker / pane CLI の存在確認を
払わせないため。

本 module は MCP SDK に依存しない — scope の振る舞い (既定値の優先順位・scope guard・
clone root の正規化・anchor env) は SDK 抜きのテストで検証できる。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ledger as ledger_mod
import pane as pane_mod
import project as project_mod
import repo_key as repo_key_mod
import tracker as tracker_mod
import worktree as worktree_mod

# 本 server が使う pane backend。tmux は port 境界だけを設計して実装しない (spec §8)
PANE_BACKEND = "herdr"


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


@dataclass
class Scope:
    """1 つの server process が作用する範囲。

    `root` / `ledger` / `declaration` は生成時に解決済みで、以後変わらない。adapter の field は
    解決済み port の置き場で、`get_*` は未解決のときだけ 1 回組み立てる。**生成をここへ集約
    する** — port を module 変数で持つと注入経路が module への `setattr` しか無くなり、
    キャッシュの無効化 (None 代入) までテスト側の手続きに漏れる。テストは fake を詰めた
    Scope を `set_scope` で差し込む。

    **宣言はここに載ったまま変わらない。** config を編集したら server を再起動しないと
    反映されない — 反映されていないように見えたら、まず再起動を疑う。
    """

    root: str
    ledger: ledger_mod.Ledger
    declaration: dict[str, Any]
    pane: pane_mod.PanePort | None = None
    adapter: tracker_mod.TrackerPort | None = None
    pr_adapter: tracker_mod.TrackerPort | None = None
    # clone root → WorktreePort。key は**呼び出し側が渡した値そのもの** (未指定は None) で、
    # 既定 root の解決を 1 回に保つ。project の実装 repo は複数ありうる (ADR 0036) ので port も
    # 1 つに固定できないが、**server は clone の集合を持たない** — 作るのは渡された root の分だけ
    worktrees: dict[str | None, worktree_mod.WorktreePort] = field(default_factory=dict)

    def issue_tracker_name(self) -> str:
        """issue 置き場の tracker 名 (adapter を作れなくても判る)。

        adapter を組み立て済みならその tracker を採る — 名前と adapter が別々に判定されて
        食い違う経路を作らないため。
        """
        if self.adapter is not None:
            return self.adapter.tracker
        name = self.declaration["issue"]["tracker"]
        if name is None:
            raise tracker_mod.TrackerError(
                f"tracker を判定できない ({self.root}: 宣言 (config / 散文 doc) 無し + "
                "remote host 不明)"
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
            name = self.declaration["pr"]["tracker"]
            if name is None:
                raise tracker_mod.TrackerError(
                    f"PR 置き場の tracker を判定できない ({self.root}: 宣言の [pr] 無し + "
                    "remote host 不明)"
                )
            self.pr_adapter = tracker_mod.get_adapter(name)
        return self.pr_adapter

    def get_pr_adapter_optional(self) -> tracker_mod.TrackerPort | None:
        """PR 置き場の adapter。判定できない / 未実装なら None (`resolve` 用)。"""
        try:
            return self.get_pr_adapter()
        except tracker_mod.TrackerError:
            return None

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
            root = require_clone_root(repo_root) if repo_root else self.root
            self.worktrees[repo_root] = worktree_mod.GitWorktrees(root)
        return self.worktrees[repo_root]

    def issue_repo(self, repo):
        """issue 系 tool の `repo` の実効値。**明示引数 > 宣言 > 未指定 (CLI の cwd 推論)**。

        宣言を既定値に使うのは「渡し忘れると観測・claim・label が全部よその repo へ向く」経路を
        機械的に塞ぐため (ADR 0036 の追補)。明示引数を残すのは、宣言と違う repo を 1 回だけ見る
        判断 (関連 repo の確認等) を呼び出し側から奪わないため。
        """
        if repo is not None:
            return repo
        return _injectable_repo(self.declaration["issue"]["repo"], self.get_adapter_optional())

    def claim_label(self):
        """AI の claim 信号に使う label の綴り (宣言の `issue.claim_label`)。

        **必ず値が返る** — 宣言が無い環境でも tracker 既定 (`project.default_claim_label`) が
        入っている。`_injectable_repo` を通さないのは、adapter の capability に依らない値
        だから (label の付け外しは全 adapter が持つ)。
        """
        return self.declaration["issue"]["claim_label"]

    def ready_label(self):
        """候補プール (AFK-ready) を表す triage label の綴り。**未宣言なら None**。

        `claim_label` と違って既定へ倒さない (`project._validate_ready_label`)。読み手は None を
        「絞る綴りが無い」として扱い、憶測の綴りで絞らない。
        """
        return self.declaration["issue"]["ready_label"]

    def pr_repo(self, repo):
        """PR 系 tool の `repo` の実効値。issue 置き場とは別軸で解く (#576)。"""
        if repo is not None:
            return repo
        return _injectable_repo(self.declaration["pr"]["repo"], self.get_pr_adapter_optional())

    def ledger_anchor_env(self):
        """起動プロセスへ渡す台帳 anchor (`{DIR 環境変数: project の台帳ディレクトリ}`)。

        worker は関連 repo の clone で走ることがあり、そこで cwd から repo-key を導出させると
        project の台帳ではなく関連 repo の台帳を新設して書く (ADR 0036 の壊れ点 1)。tool 引数や
        prompt 契約ではなく env で渡すのは、**worker の LLM が忘れても壊れない機械経路**に
        するため。

        渡す前にディレクトリを実体化する — 受け取る側は実在しない DIR を fail-closed で撥ねるので、
        まだ 1 件も記帳していない台帳を配ると worker が起動直後に失敗する。

        label 起動 (observer 等) にも同じく渡す — observer も台帳を読み書きする。
        """
        return {ledger_mod.LEDGER_DIR_ENV: self.ledger.ensure_directory()}


def _injectable_repo(declared, adapter):
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


def resolve_scope() -> Scope:
    """process の cwd から scope を解決する (**cwd 由来の root 導出はここ 1 箇所**)。

    cwd が linked worktree の中でも main worktree root へ解決する — 台帳の repo-key と同じ
    基準にすることで、dispatcher がどこから起動されても同じツリーを指す。server プロセスの
    cwd は起動後に変わらない前提。

    台帳を開く順は `ledger.resolve_ledger_dir` に委ねる (`ISSUE_DISPATCH_LEDGER_DIR` が
    cwd 由来の導出に勝つ)。ここで root を渡すと env より強くなり、`pane_spawn` が注入した
    anchor を無視して worker の記帳が迷子になる。

    導出に失敗したら握り潰さず例外を上げる — 別 repo の台帳へ書くより、tool 呼び出しが
    失敗するほうが安い。
    """
    root = repo_key_mod.main_worktree_root(Path.cwd())
    return Scope(
        root=str(root),
        ledger=ledger_mod.open_ledger(),
        declaration=project_mod.resolve_declaration(root),
    )


_scope = None


def get_scope() -> Scope:
    """process の scope を返す (プロセス内で 1 つ)。

    **tool 層から port へ届く唯一の継ぎ目**。tool 関数は SDK が tool 引数だけで呼ぶので scope を
    引数で受け取れず、差し替えはここに寄る。tool の signature に scope 引数を足さない
    (signature は skill が読む interface)。
    """
    global _scope
    if _scope is None:
        _scope = resolve_scope()
    return _scope


def set_scope(scope: Scope) -> None:
    """scope を差し込む (**テスト専用の継ぎ目**)。

    実運用の経路はここを呼ばない — server は `get_scope` の遅延解決だけで立ち上がる。
    テストは fake を詰めた Scope を差し込み、後片付けに `reset_scope` を呼ぶ。
    """
    global _scope
    _scope = scope


def reset_scope() -> None:
    """差し込んだ scope を捨てて未解決へ戻す (**テスト専用**)。

    差しっぱなしにすると、実解決を通すテストが前のテストの fake を掴む。
    """
    global _scope
    _scope = None
