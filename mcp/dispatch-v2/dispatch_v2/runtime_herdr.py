"""herdr adapter — SessionRuntime port の herdr CLI 実装。

`herdr` は AI agent 向けの terminal multiplexer。本 module が持つのは **CLI の呼び出しと応答の
写像だけ**で、認証も接続も CLI に委ねる。v1 (`mcp/dispatch-ops/pane_herdr.py`) と同じ CLI を
叩くが、**コードは共有せず copy した** — v1 / v2 併走中に結合を作らないため (issue #850)。

CLI の失敗 (非 0 exit / 非 JSON) は `SessionRuntimeError` として即座に表面化させる。
「CLI の実行失敗」を「session が居ない」と読ませると、生きている worker を全部終わったことに
してしまう (v1 が同じ判断をしている)。**唯一の例外は herdr が `pane_not_found` を名乗ったとき**
で、これだけは「その pane はもう無い」の一次情報なので gone として読む (gh#972)。code を
名乗らない失敗も別 code の失敗も従来どおり上げる — 「消えた」と「観測できない」は別物。

環境変数 (`HERDR_ENV` / `HERDR_WORKSPACE_ID`) は daemon プロセスが継承したものを読む。
**割り元 (anchor) は env から採らない** — daemon はマシンに 1 プロセスで長命なので、継承した
pane は先に死ぬし (gh#932)、生きていても呼び出し元とは無関係な workspace の pane でありうる
(gh#952)。割り元は `launch` の `anchor` (呼び出し側が観測した生きた pane) だけで、名乗らない
spawn は `LaunchFailed` として loud に落とす。env の workspace は観測 (`sight_all`) の既定の
窓としてだけ残る。
"""

import json
import os
import shutil
import sys

from dispatch_v2 import proc, session_runtime, session_vocabulary

BACKEND = "herdr"

# 1 回の CLI 起動に許す上限秒。**tracker CLI と同じ 15 秒に揃える**
# (`gh_adapter.SUBPROCESS_TIMEOUT_SEC` が理由の正本) — herdr は同じマシンの multiplexer への
# RPC で実測は 1 桁ミリ秒なので、15 秒は「返らない」の打ち切りであって期待値ではない。
# 60 秒のままだと 1 回の hang が client の応答待ち (20 秒) を単独で超え、daemon が
# 台帳の門を掴んだまま全 tool を 503 にする (gh#956)
SUBPROCESS_TIMEOUT_SEC = 15

# herdr の `AgentStatus` (`herdr api schema --json` の $defs.AgentStatus = idle / working /
# blocked / done / unknown) → 中立 3 値。**blocked を独立させるのが v1 との差** — v1 は
# idle / done 以外を running に寄せており、permission 待ちの worker が「動いている」に
# 化けていた (設計 決定 17)。`unknown` は写さず None のまま返す
ACTIVITY_BY_AGENT_STATUS = {
    "working": "running",
    "idle": "idle",
    "done": "idle",
    "blocked": "blocked",
}

# 生 status を写せなかったことを示す herdr 側の値。表に載せずに未分類へ倒す
UNKNOWN_AGENT_STATUS = "unknown"

# 「その pane は無い」を表す herdr の error code。**失敗応答は stderr に JSON で**返り
# (実測 2026-09-08: rc=1 / stdout 空 / stderr に `{"error":{"code":"pane_not_found",...}}`)、
# exit code は 1 で他の失敗と区別が付かない。**成功応答は stdout** なので流し先が入れ替わる —
# stdout だけを読んでいたあいだ code が読めず、「消えた pane」が「観測できない」に化けて
# session_close が 502 で拒み続けた (gh#972)
PANE_NOT_FOUND_CODE = "pane_not_found"


class HerdrFailure(session_runtime.SessionRuntimeError):
    """herdr が非 0 で返した失敗。`code` は error 封筒の code (名乗らなければ None)。

    code を例外に載せるのは、**「その pane は無い」だけを他の失敗と区別して読む**ため。
    port 側 (`SessionRuntimeError`) に持たせないのは、code の語彙が herdr 固有だから。
    """

    def __init__(self, message, *, code):
        super().__init__(message)
        self.code = code


def run_herdr_command(args):
    """herdr CLI を起動して (rc, stdout, stderr) を返す (テストが差し替える継ぎ目)。

    起動は `proc.run_bounded` を通す — herdr も子を持つので、上限を過ぎたときに倒す相手は
    直の子ではなく process group (gh#967)。
    """
    try:
        completed = proc.run_bounded(["herdr", *args], timeout_sec=SUBPROCESS_TIMEOUT_SEC)
    except proc.CommandTimedOut as exc:
        raise session_runtime.SessionRuntimeError(str(exc)) from exc
    except OSError as exc:
        raise session_runtime.SessionRuntimeError(f"herdr を起動できない: {exc}") from exc
    return completed.returncode, completed.stdout, completed.stderr


class HerdrRuntime(session_runtime.SessionRuntime):
    """herdr の pane を worker の実行単位として使う adapter。"""

    backend = BACKEND

    def __init__(self, *, run_command=run_herdr_command, environ=None, which=shutil.which):
        self._run_command = run_command
        self._environ = os.environ if environ is None else environ
        self._which = which
        # **成功だけを覚える** — 失敗を覚えると、herdr を再起動すれば直る種類の失敗が
        # daemon の寿命のあいだ固定される
        self._ready = None
        # 割った先の workspace。**split を撃つ前に覚える** — 応答を取りこぼした split の残骸は
        # 台帳のどこからも指されないので、ここに残さないと観測窓がその workspace へ届かない
        self._split_workspaces = set()

    # --- 前提検査 -------------------------------------------------------------

    def ensure_ready(self):
        """herdr session の中で動いていること + agent を起動できることを検査する。

        **`HERDR_PANE_ID` は見ない** — daemon が継承した pane は割り元に使わなくなったので
        (gh#952)、要求すると「使わない前提」を検査することになる。`HERDR_WORKSPACE_ID` は
        観測 (`sight_all`) の既定の窓として今も要る。
        """
        if self._ready is not None:
            return self._ready
        if self._environ.get("HERDR_ENV") != "1":
            raise session_runtime.SessionRuntimeError(
                "HERDR_ENV=1 でない (herdr session の外で daemon が起動している。"
                "herdr session 内から起動し直す)"
            )
        workspace = self._require_env("HERDR_WORKSPACE_ID")
        agent_bin = self._which(session_runtime.AGENT_BIN)
        if not agent_bin:
            raise session_runtime.SessionRuntimeError(
                f"{session_runtime.AGENT_BIN} が PATH に無い (worker を起動できない)"
            )
        self._ready = {
            "backend": self.backend,
            "workspace": workspace,
            "agent_bin": agent_bin,
        }
        return self._ready

    # --- 継ぎ目 ---------------------------------------------------------------

    def launch(self, *, command, cwd, label, anchor):
        """pane を割り → label を付け → command を走らせ、pane_id を返す。

        割り元に `--current` ではなく pane id を明示して渡すのは、`--current` の解決先が
        UI フォーカス中の pane に落ちるため (v1 の実測)。**pane はその id が属する workspace
        に開く**ので、呼び出し元の pane を渡せば worker はそこへ並ぶ。
        """
        self.ensure_ready()
        anchor_handle = _require_anchor(anchor)
        self._split_workspaces.add(anchor.workspace)
        try:
            created = self._pane(
                self._run(
                    [
                        "pane",
                        "split",
                        "--pane",
                        anchor_handle,
                        "--direction",
                        "right",
                        "--no-focus",
                        "--cwd",
                        str(cwd),
                    ]
                )
            )
            handle = created["pane_id"]
        except session_runtime.SessionRuntimeError as exc:
            raise session_runtime.LaunchFailed(str(exc)) from exc
        # split の後は pane が既に在る。**以降の失敗は handle を載せて上げる** — pane を消さず
        # handle も捨てると、生きている pane が台帳のどこからも指されない残骸になる
        try:
            self._run(["pane", "rename", handle, label])
            self._run(["pane", "run", handle, command])
        except session_runtime.SessionRuntimeError as exc:
            raise session_runtime.LaunchFailed(str(exc), handle=handle) from exc
        return handle

    def send(self, handle, text):
        """テキスト送出 + Enter で submit する。

        `send-text` 単体には submit 手段が無いので Enter を別に送る (送りっぱなしだと
        入力欄に文字が残ったまま agent は動かない)。
        """
        self._run(["pane", "send-text", handle, text])
        self._run(["pane", "send-keys", handle, "enter"])

    def notify(self, handle):
        """空 nudge を届ける。

        **herdr には「内容を運ばずに agent を起こす」経路が無い** (`pane send-text` +
        `send-keys enter` が agent を動かす唯一の手) ので、`send` と同じ経路に
        `session_runtime.NUDGE_TEXT` だけを流す。運ぶ文字列が固定であることが「内容を
        運ばない」の実体で、`herdr notification show` を使わないのは、あれが起こすのが
        agent ではなく画面を見ている人間だから。
        """
        self.send(handle, session_runtime.NUDGE_TEXT)

    def close(self, handle):
        """pane を閉じる。**既に無ければ閉じ終わったものとして返る**。

        観測してから閉じるまでの隙に pane が消えることがあり、そこで落とすと「片付いたのに
        失敗を報告する」経路になる (再実行しても同じ終状態へ収束させる)。`pane_not_found`
        以外の失敗は従来どおり上げる — 「消えた」と「届かない」を混同しない。
        """
        try:
            self._run(["pane", "close", handle])
        except HerdrFailure as exc:
            if exc.code != PANE_NOT_FOUND_CODE:
                raise

    def sight(self, handle):
        """pane 1 件の観測。pane が無ければ None (= gone)。

        「無い」の判定に **herdr が返す error code (`pane_not_found`) を使う** — message の
        綴りで判定すると文言の変更で黙って壊れ、生きている pane を gone と読む。
        """
        try:
            payload = self._run(["pane", "get", handle])
        except HerdrFailure as exc:
            if exc.code == PANE_NOT_FOUND_CODE:
                return None
            raise
        return _sighting_of(self._pane(payload))

    def sight_all(self, *, scope_handles):
        """観測窓の中の pane を列挙する。

        窓は **daemon 自身の workspace + `scope_handles` が居る workspace + この daemon が
        割った先の workspace**。全 workspace をそのまま返さないのは、別 project の pane や
        人間自身の pane を自分の追跡対象として拾うため (v1 の実測)。逆に daemon の workspace
        だけに閉じると、呼び出し元の workspace へ割った worker (gh#952) とその周りの残骸が
        窓の外に出る。

        列挙そのものは `pane list` 1 回で済ませて手元で絞る (`--workspace` は 1 つしか取れず、
        窓の数だけ CLI を起動すると周期観測のたびに回数が増える)。**`scope_handles` の居場所も
        この応答から引く** — id の綴りから読むと、窓を作る綴りと突合する綴りの出所が 2 つに
        なり、herdr が名乗りを変えたときに例外にならないまま窓が空になる。
        """
        panes = self._list_panes()
        window = self._observation_window(scope_handles, panes)
        return [_sighting_of(pane) for pane in panes if pane.get("workspace_id") in window]

    def _list_panes(self):
        """`pane list` の応答のうち、workspace を名乗る pane だけ。

        名乗らない pane は**窓の内外を判定できない**ので数えず、そのことを log へ出す。落とさ
        ないのは、この列挙が全 workspace を舐める以上、無関係な 1 pane の異常で報告経路
        (`orphan_sessions` / `stranded_sessions`) ごと止まるほうが害が大きいため。
        """
        payload = self._run(["pane", "list"])
        panes = (payload or {}).get("result", {}).get("panes", [])
        placed = [pane for pane in panes if pane.get("workspace_id")]
        if len(placed) != len(panes):
            print(
                f"[dispatch-v2] herdr の pane list に workspace_id を持たない pane が "
                f"{len(panes) - len(placed)} 件あるので観測から外した",
                file=sys.stderr,
            )
        return placed

    def _observation_window(self, scope_handles, panes):
        """列挙を絞る workspace の集合。"""
        return (
            {self.ensure_ready()["workspace"]}
            | {pane["workspace_id"] for pane in panes if pane.get("pane_id") in scope_handles}
            | self._split_workspaces
        )

    def classify_activity(self, activity_raw):
        """herdr の agent_status を中立 3 値へ写す。表に無い値は未分類 (None) のまま返す。"""
        return ACTIVITY_BY_AGENT_STATUS.get(
            activity_raw, session_vocabulary.UNCLASSIFIED_ACTIVITY
        )

    # --- CLI の呼び出し -------------------------------------------------------

    def _run(self, args):
        """成功なら応答本文を返し、非 0 なら `HerdrFailure` を上げる。

        **成功と失敗で応答の流し先が違う** (成功は stdout / 失敗は stderr) ので、
        本文の厳密な decode は成功経路だけに当てる — 失敗の出力を「JSON でない」で落とすと、
        失敗の説明が例外にすり替わって元の失敗が読めなくなる。
        """
        rc, stdout, stderr = self._run_command(args)
        if rc != 0:
            raise _failure(" ".join(args), rc, stdout, stderr)
        return _decode(stdout, " ".join(args))

    @staticmethod
    def _pane(payload):
        pane = (payload or {}).get("result", {}).get("pane")
        if not pane or not pane.get("pane_id"):
            raise session_runtime.SessionRuntimeError(
                f"herdr の応答に pane_id が無い: {payload!r}"
            )
        return pane

    def _require_env(self, name):
        value = self._environ.get(name)
        if not value:
            raise session_runtime.SessionRuntimeError(
                f"{name} が未設定 (herdr session の外で daemon が起動している)"
            )
        return value


def _require_anchor(anchor):
    """割り元 (anchor) を名乗らない spawn は落とす。

    **縮退先を持たない** — daemon が継承した pane から割ると、その pane が属する workspace
    (呼び出し元とは無関係で、往々にして最後に focus された project のもの) に worker が開き、
    人間が pane を探せなくなる (gh#952)。誰の隣に並べるかを知っているのは呼び出し側だけなので、
    名乗られなければ起こさない。
    """
    if anchor is None:
        raise session_runtime.LaunchFailed(
            "呼び出し側が割り元 (anchor) を名乗らないので worker を起こさない "
            "(herdr session の中から dispatch する。daemon の割り元へ縮退すると、"
            "呼び出し元と無関係な workspace に worker が開く)"
        )
    return anchor.handle


def _decode(stdout, source):
    """herdr の JSON 応答を読む。body が空の subcommand (pane run 等) は None。"""
    if not stdout.strip():
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise session_runtime.SessionRuntimeError(
            f"herdr {source} の stdout が JSON でない: {exc}"
        ) from exc


def _sighting_of(pane):
    """herdr の pane 表現 → port の観測値。

    `agent` 欄の有無が「agent が居るか」で、これが `exited` の唯一の観測源。欠落を
    「居る」と読むと、終了した worker が生きているままになる。
    """
    return session_runtime.RuntimeSighting(
        handle=pane.get("pane_id"),
        label=pane.get("label"),
        agent_present=bool(pane.get("agent")),
        activity_raw=pane.get("agent_status") or UNKNOWN_AGENT_STATUS,
    )


def _error_envelope(stderr):
    """herdr の失敗応答が名乗る `{"code", "message"}`。名乗っていなければ None。

    **読むのは stderr だけ** (gh#972)。封筒でない出力 — usage 文や herdr 以前で落ちた失敗 —
    もここへ来るので、JSON でなければ封筒が無いものとして扱い落とさない。封筒を読めなければ
    code の無い失敗として loud に上がるので、herdr が流し先を変えたら黙って gone に化ける
    のではなくその場で見える。
    """
    try:
        payload = json.loads(stderr)
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, dict) else None


def _failure(source, rc, stdout, stderr):
    """失敗の説明。**herdr が返した message を優先する** (素の出力は綴りが揺れる)。

    封筒を読むのは stderr だけだが、**説明に使う素の出力は両方から拾う** — 落ちたことだけ
    伝わって中身が『出力なし』になると、loud に落ちても運用者が原因を読めない。
    """
    error = _error_envelope(stderr) or {}
    detail = error.get("message") or stderr.strip() or stdout.strip() or "出力なし"
    return HerdrFailure(f"herdr {source} が失敗 (exit {rc}): {detail}", code=error.get("code"))
