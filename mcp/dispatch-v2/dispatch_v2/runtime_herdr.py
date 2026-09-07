"""herdr adapter — SessionRuntime port の herdr CLI 実装。

`herdr` は AI agent 向けの terminal multiplexer。本 module が持つのは **CLI の呼び出しと応答の
写像だけ**で、認証も接続も CLI に委ねる。v1 (`mcp/dispatch-ops/pane_herdr.py`) と同じ CLI を
叩くが、**コードは共有せず copy した** — v1 / v2 併走中に結合を作らないため (issue #850)。

CLI の失敗 (非 0 exit / 非 JSON) は `SessionRuntimeError` として即座に表面化させる。
「CLI の実行失敗」を「session が居ない」と読ませると、生きている worker を全部終わったことに
してしまう (v1 が同じ判断をしている)。

環境変数 (`HERDR_ENV` / `HERDR_PANE_ID` / `HERDR_WORKSPACE_ID`) は daemon プロセスが継承した
ものを読む。**daemon はマシンに 1 プロセスで長命なので、継承した pane は先に死にうる** —
そのため割り元は `launch` の `anchor` (呼び出し側が観測した生きた pane) を優先し、env の
pane は anchor を観測していないときの縮退先として残す (gh#932)。どちらの pane も死んで
いれば失敗は `herdr pane split` の非 0 exit として loud に出る (黙って別 pane を探さない)。
"""

import json
import os
import shutil
import subprocess
import sys

from dispatch_v2 import session_runtime, session_vocabulary

BACKEND = "herdr"

SUBPROCESS_TIMEOUT_SEC = 60

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

# 「その pane は無い」を表す herdr の error code。失敗応答は **stdout に JSON で**返り
# (`{"error":{"code":"pane_not_found",...}}`)、exit code は 1 で他の失敗と区別が付かない
PANE_NOT_FOUND_CODE = "pane_not_found"


def run_herdr_command(args):
    """herdr CLI を起動して (rc, stdout, stderr) を返す (テストが差し替える継ぎ目)。"""
    try:
        completed = subprocess.run(
            ["herdr", *args], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC
        )
    except (OSError, subprocess.SubprocessError) as exc:
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

    # --- 前提検査 -------------------------------------------------------------

    def ensure_ready(self):
        """herdr session の中で動いていること + 割り元 (anchor) pane が在ることを検査する。"""
        if self._ready is not None:
            return self._ready
        if self._environ.get("HERDR_ENV") != "1":
            raise session_runtime.SessionRuntimeError(
                "HERDR_ENV=1 でない (herdr session の外で daemon が起動している。"
                "herdr session 内から起動し直す)"
            )
        inherited_anchor = self._require_env("HERDR_PANE_ID")
        workspace = self._require_env("HERDR_WORKSPACE_ID")
        agent_bin = self._which(session_runtime.AGENT_BIN)
        if not agent_bin:
            raise session_runtime.SessionRuntimeError(
                f"{session_runtime.AGENT_BIN} が PATH に無い (worker を起動できない)"
            )
        self._ready = {
            "backend": self.backend,
            # daemon が起動時に継承した anchor。呼び出し側が anchor を名乗らないときの縮退先
            "anchor_handle": inherited_anchor,
            "workspace": workspace,
            "agent_bin": agent_bin,
        }
        return self._ready

    # --- 継ぎ目 ---------------------------------------------------------------

    def launch(self, *, command, cwd, label, anchor):
        """pane を割り → label を付け → command を走らせ、pane_id を返す。

        割り元に `--current` ではなく pane id を明示して渡すのは、`--current` の解決先が
        UI フォーカス中の pane に落ちることがあり、別 workspace の pane から割ると
        `pane list --workspace` の観測窓の外へ worker が出るため (v1 の実測)。
        """
        ready = self.ensure_ready()
        anchor_handle, rejection = self._anchor_choice(anchor, ready)
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
            # **採らなかった anchor があれば失敗にそれを載せる** — 呼び出し側から見えるのは
            # この message だけなので、載せないと「anchor を渡したのに死んだ pane から割られた」
            # ことが gh#932 と同じ `pane_not_found` にしか見えない
            raise session_runtime.LaunchFailed(
                str(exc) if rejection is None else f"{exc} / {rejection}"
            ) from exc
        # split の後は pane が既に在る。**以降の失敗は handle を載せて上げる** — pane を消さず
        # handle も捨てると、生きている pane が台帳のどこからも指されない残骸になる
        try:
            self._run(["pane", "rename", handle, label])
            self._run(["pane", "run", handle, command])
        except session_runtime.SessionRuntimeError as exc:
            raise session_runtime.LaunchFailed(str(exc), handle=handle) from exc
        return handle

    def _anchor_choice(self, anchor, ready):
        """割り元 (anchor) pane と、採らなかった anchor の理由を返す。

        呼び出し側の anchor を優先する。env の pane は daemon が起動時に継承した化石で、その
        pane が閉じられると以後の `pane split` が `pane_not_found` で落ち続ける (gh#932 の故障)。
        呼び出し側 (MCP server) は orchestrator セッションの子プロセスなので、渡ってくる anchor
        は今生きている pane。

        **別 workspace の anchor は採らない** — そこへ割った worker は `sight_all`
        (`pane list --workspace`) の観測窓の外に出て、生きたまま台帳から見えなくなる。この
        制約のぶん、daemon と別 workspace に居る呼び出し側は依然として daemon が継承した pane
        に頼る (gh#932 の故障がその範囲に残る)。だから採らなかった理由を 2 通り返す — daemon の
        log と、split が落ちたときの失敗メッセージの両方に出す。
        """
        if anchor is None:
            return ready["anchor_handle"], None
        if anchor.workspace != ready["workspace"]:
            rejection = (
                f"呼び出し側の anchor {anchor.handle} は workspace {anchor.workspace} に属して"
                f"いて daemon の観測窓 ({ready['workspace']}) の外なので割り元に採らなかった"
            )
            print(f"[dispatch-v2] {rejection}", file=sys.stderr)
            return ready["anchor_handle"], rejection
        return anchor.handle, None

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
        self._run(["pane", "close", handle])

    def sight(self, handle):
        """pane 1 件の観測。pane が無ければ None (= gone)。

        「無い」の判定に **herdr が返す error code (`pane_not_found`) を使う** — message の
        綴りで判定すると文言の変更で黙って壊れ、生きている pane を gone と読む。
        """
        rc, stdout, stderr = self._run_command(["pane", "get", handle])
        payload = _decode(stdout, "pane get")
        if rc != 0:
            if _error_code(payload) == PANE_NOT_FOUND_CODE:
                return None
            raise session_runtime.SessionRuntimeError(_failure_text("pane get", rc, payload, stderr))
        return _sighting_of(self._pane(payload))

    def sight_all(self):
        """自 workspace の pane を列挙する。

        `--workspace` を明示するのは、省くと全 workspace が返り、別 project の label と
        衝突した pane を自分の追跡対象として拾うため (v1 の実測)。
        """
        workspace = self.ensure_ready()["workspace"]
        payload = self._run(["pane", "list", "--workspace", workspace])
        panes = (payload or {}).get("result", {}).get("panes", [])
        return [_sighting_of(pane) for pane in panes]

    def classify_activity(self, activity_raw):
        """herdr の agent_status を中立 3 値へ写す。表に無い値は未分類 (None) のまま返す。"""
        return ACTIVITY_BY_AGENT_STATUS.get(
            activity_raw, session_vocabulary.UNCLASSIFIED_ACTIVITY
        )

    # --- CLI の呼び出し -------------------------------------------------------

    def _run(self, args):
        rc, stdout, stderr = self._run_command(args)
        payload = _decode(stdout, " ".join(args))
        if rc != 0:
            raise session_runtime.SessionRuntimeError(
                _failure_text(" ".join(args), rc, payload, stderr)
            )
        return payload

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


def _error_code(payload):
    """herdr の失敗応答が持つ error code (`{"error": {"code": ...}}`)。無ければ None。"""
    error = (payload or {}).get("error")
    return error.get("code") if isinstance(error, dict) else None


def _failure_text(source, rc, payload, stderr):
    """失敗の説明。**herdr が返した message を優先する** (stderr は空のことがある)。"""
    error = (payload or {}).get("error")
    detail = error.get("message") if isinstance(error, dict) else None
    return f"herdr {source} が失敗 (exit {rc}): {detail or stderr.strip() or '出力なし'}"
