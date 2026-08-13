"""PanePort — pane backend (herdr) への観測・操作の継ぎ目。

spec §2: **adapter の責務は語彙の写像のみ**。「この pane を降ろすべきか」は返さない。
本 module は port (中立 API) と backend 非依存の判定 (`classify_watch` の梯子・agent
status の写像・起動 command の組み立て) を持ち、backend 固有の CLI 呼び出しは
`pane_herdr` が実装する。

tracker.py と同じく語彙の層が 2 つある:

- **内部語彙** (`working` / `idle` / `done` / `blocked` / `unknown`): backend が返す生の
  agent_status。既存 `herdr_ops.py` から実証済みの判定を移植するにあたり、この層を
  書き換えないことで「実機で検証済み」という移植の価値を保つ
- **中立語彙** (`vocabulary.AGENT_STATUSES` = running / idle / exited / gone): tool 境界を
  越える値。4 値しかないので `blocked` (permission 待ち = 人間宛の問いかけ中) と
  `unknown` (判定不能) が `running` に潰れる。潰れたままでは「送っていい pane か」を
  LLM が判断できないので、**中立値と生の値を両方載せて返す** (`agent_status` /
  `agent_status_raw`)

**policy は持ち込まない**: prompt template・stage → model 対応・slot 上限・「conflict
指示だけ送る」といった旧 `herdr_ops.py` の規則はすべて呼び出し側 (LLM / SKILL.md) の
領分で、本 module は prompt / model / effort / text を受け取って実行するだけ。

herdr CLI 境界は skill 側の共有 module と**共有しない**判断を採った (ADR 0013 基準 /
spec §8) — 共有すると「片方の都合で他方が壊れる」結合を、当時まだ併存していた旧 CLI の
撤去のためだけに作ることになるため。その共有 module は撤去済み。
"""

import asyncio
import os
import shlex
from pathlib import Path

import refs
import vocabulary

# pane で起動する agent。model / effort は Claude Code の flag なので、本 port が
# 起動対象として想定するのは Claude Code セッション (spec §4.4 の pane_spawn 引数)
AGENT_BIN = "claude"

# 自セッション (server プロセスを起動した Claude Code セッション) の受信 socket。
# cross-session messaging で worker からのイベントが届く先で、未設定のセッションは
# 送信はできても受信できない (ADR 0033 / 検証は docs/research/2026-08-11-*.md)
MESSAGING_SOCKET_ENV = "CLAUDE_CODE_MESSAGING_SOCKET"

# --- 内部語彙 (backend の生の agent_status) ------------------------------------

# 「pane 内で作業が進んでいない」状態。駐機 (pane を降ろして worktree を温存) の契機に
# なる。working / blocked (permission 待ち) / unknown は含めない
IDLE_RAW_STATUSES = frozenset({"idle", "done"})
UNKNOWN_RAW_STATUS = "unknown"

# --- 中立語彙 (vocabulary.AGENT_STATUSES が正本) ---------------------------------

STATUS_RUNNING, STATUS_IDLE, STATUS_EXITED, STATUS_GONE = vocabulary.AGENT_STATUSES

# pane_watch が返す event。どれでも呼び出し側の照合手順は同じで、event は「何が変わって
# 返ってきたか」の報告 (完了判定は issue / PR の観測が正)
WATCH_EVENTS = ("agent_exited", "pane_gone", "agent_idle", "timeout", "no_panes")

# spec §4.5: main conversation の MCP 呼び出しは 2 分で自動 background 化されるので、
# 監視ループの「結果を見て次を決める」同期フローを保つために 110 秒を超えさせない
WATCH_DEFAULT_TIMEOUT_SEC = 90
WATCH_MAX_TIMEOUT_SEC = 110
WATCH_DEFAULT_INTERVAL_SEC = 10

# pane_spawn の隔離モード。返り値に載せて「どちらを実行したか」を呼び出し側に明示する
MODE_PLAIN, MODE_CREATED, MODE_REENTERED = "plain", "created", "reentered"

# 監視ループの sleep 継ぎ目 (テストが差し替える)。subprocess は同期のままなので、
# await するのは待ち時間だけ
_sleep = asyncio.sleep


class PaneError(RuntimeError):
    """pane backend の失敗、または pane 操作の前提不成立。"""


# --- 語彙の写像 -------------------------------------------------------------------


def neutral_agent_status(agent, raw_status):
    """(agent, 生の agent_status) → 中立 4 値。

    agent が null = セッションが終了した (`exited`)。生きている pane のうち
    IDLE_RAW_STATUSES は `idle`、それ以外は `running` に寄せる。pane 自体の消失
    (`gone`) はこの写像では出ない — 一覧に居ないことでしか観測できないので、
    watch 側が pane_gone event として扱う。
    """
    if agent is None:
        return STATUS_EXITED
    return STATUS_IDLE if raw_status in IDLE_RAW_STATUSES else STATUS_RUNNING


def classify_watch(baseline, current):
    """スナップショット (baseline / 現在) → 返却イベント。変化無しは None (poll 継続)。

    agent_exited > pane_gone > agent_idle の順に見る。前 2 つは呼び出し側の照合手順が
    同一なので優先順は報告の分かりやすさのためだけにあるが、agent_idle を最後に置くのは
    「死んだ pane」と「生きたまま作業を止めた pane」を報告で取り違えないため。

    agent_idle は **baseline からの遷移** でのみ発火させる。baseline 時点で既に idle な
    pane でも発火させると、質問待ちで止まった pane が 1 つある間ずっと watch が即 return
    し、監視ループが tracker API を叩き続ける busy loop になる。
    """
    if any(entry["agent"] is None for entry in current):
        return "agent_exited"
    if {entry["pane_id"] for entry in baseline} - {entry["pane_id"] for entry in current}:
        return "pane_gone"
    settled = {
        entry["pane_id"]
        for entry in baseline
        if entry.get("agent_status_raw") in IDLE_RAW_STATUSES
    }
    if any(
        entry.get("agent_status_raw") in IDLE_RAW_STATUSES and entry["pane_id"] not in settled
        for entry in current
    ):
        return "agent_idle"
    return None


def build_command(agent_bin, prompt, model=None, effort=None, worktree=None, name=None):
    """pane 内で起動する command 文字列を組み立てる。

    `name` は起動セッションの表示名 (`--name`)。cross-session messaging の相手発見が
    名前でしか行えない経路 (ListAgents) のために付ける — pane label と同じ文字列を渡す
    ので、pane の見出しとセッション名が食い違わない (ADR 0033)。

    shlex.quote で組むのは、prompt の quote 崩れで pane の shell が引数を誤解釈する
    事故を塞ぐため (単引用符を含む prompt は日常的に来る)。
    """
    parts = [agent_bin]
    if model:
        parts += ["--model", model]
    if effort:
        parts += ["--effort", effort]
    if worktree:
        parts += ["--worktree", worktree]
    if name:
        parts += ["--name", name]
    parts.append(prompt)
    return " ".join(shlex.quote(part) for part in parts)


def require_messaging_receivable():
    """自セッションが worker からのメッセージを受信できることを検査する (fail-closed)。

    backend に依らない検査なので port 側に置き、adapter の前提検査から呼ぶ。

    旧 binary の resume セッションは**送信できても受信できない**ことが実測されており、
    その差は本 env の有無に出る (docs/research/2026-08-11-cross-session-messaging-
    verification.md)。受信できないセッションから dispatch すると、worker は節目
    (質問 / PR 到達 / 完了) を送るのに誰も受け取らず、監視が無言で止まる — 起動前に
    止めるほうが安い。
    """
    socket = os.environ.get(MESSAGING_SOCKET_ENV)
    if not socket:
        raise PaneError(
            f"{MESSAGING_SOCKET_ENV} が未設定 (worker からのイベントを受信できない"
            "セッションで dispatch しようとしている。現行 binary で新規起動した "
            "Claude Code セッションから起動し直す)"
        )
    return socket


# --- port ---------------------------------------------------------------------------


class PanePort:
    """pane backend への中立 API。backend 固有の呼び出しは下の継ぎ目 method が担う。

    公開 method (`observe_panes` / `pane_*`) は中立語彙だけを受け渡しし、継ぎ目 method
    (`list_panes` / `get_pane` / `launch_pane` 等) は backend の生の値を扱う。新しい
    backend (tmux) を足すときに実装するのは継ぎ目 method だけ — ただし本効力では
    実装しない (spec §8: port 境界の設計まで)。
    """

    backend = None

    # --- 継ぎ目 (adapter が実装する) -------------------------------------------

    def ensure_ready(self):
        """backend が使える状態かを検査する (失敗は PaneError)。

        返り値は `{"workspace", "self_pane_id", ...}` の診断 dict。
        """
        raise NotImplementedError

    def list_panes(self):
        """pane 一覧を `[{"pane_id", "label"}]` で返す (自 pane を含む)。"""
        raise NotImplementedError

    def get_pane(self, pane_id):
        """pane 1 件 → `{"pane_id", "label", "agent", "agent_status_raw"}`。

        **agent field の欠落をここで例外にしない** — 欠落の検知は port 側
        (`_require_agent_field`) が行う。adapter が投げる PaneError は「その pane を
        取得できなかった」だけを意味させ、race (list と get の間の pane 消失) と
        「取れたが応答が壊れている」を呼び分けられるようにする。
        """
        raise NotImplementedError

    def launch_pane(self, command, cwd, label):
        """pane を割って command を起動し `(pane_id, agent)` を返す。"""
        raise NotImplementedError

    def close_pane(self, pane_id):
        """pane を閉じる。"""
        raise NotImplementedError

    def send_text(self, pane_id, text):
        """pane へテキストを送って submit する。"""
        raise NotImplementedError

    def resolve_agent_bin(self):
        """pane 内で起動する agent の絶対パス (見つからなければ None)。"""
        raise NotImplementedError

    def self_pane_id(self):
        """自分 (server プロセスを起動したセッション) の pane id。不明なら None。"""
        raise NotImplementedError

    # --- observe ------------------------------------------------------------------

    def observe_panes(self):
        """pane 一覧を中立 schema で返す (spec §4.4)。

        **全 pane を返す** — `i<N>` label の付いた追跡対象だけに絞らない。空き slot を
        いくつと見るか・どの pane を無視するかは LLM の判断で、server は一覧と
        「追跡対象か (`tracked`)」「自分か (`is_self`)」の機械的な印だけを付ける。

        追跡外の pane は**観測するだけで検証しない**。agent を起動していない素の shell
        pane は応答に agent field を持たないが、それは異常ではなく現況なので、
        `agent: null` (中立値では `exited`) のまま載せる。追跡 pane の欠落だけは従来
        どおり観測失敗として raise する (`_snapshot`)。
        """
        ready = self.ensure_ready()
        panes = self._snapshot(self.list_panes())
        return {
            "backend": self.backend,
            "workspace": ready.get("workspace"),
            "self_pane_id": self.self_pane_id(),
            "count": len(panes),
            "panes": panes,
        }

    async def pane_watch(
        self,
        timeout_sec=WATCH_DEFAULT_TIMEOUT_SEC,
        interval_sec=WATCH_DEFAULT_INTERVAL_SEC,
        progress=None,
    ):
        """追跡 pane (`i<N>` label・自 pane 除く) の変化か timeout まで待つ (spec §4.4)。

        Args:
            timeout_sec: 最大待ち秒数 (既定 90 / 上限 110)
            interval_sec: poll 間隔秒数
            progress: `await progress(経過秒, timeout_sec)` で呼ばれる任意の callback。
                MCP の progress notification へ繋ぐための継ぎ目 (spec §4.5)

        絞り込みを `i<N>` label に閉じるのは policy ではない — その label を付けたのは
        `pane_spawn` 自身なので、「自分が起動した pane の変化」を見る機構になる。

        timeout は失敗ではない (変化が無かったという観測)。長い監視は呼び出し側が本
        tool を反復して組む (spec §4.5)。

        await するのは待ち時間だけで、backend への問い合わせは同期の subprocess のまま。
        poll の実行中はこの server プロセスが他の tool 呼び出しに応じられない — dispatcher
        は 1 度に 1 つの tool しか呼ばないので実害は無いが、監視中に別経路から叩く設計に
        するなら先にここを非同期化する必要がある。
        """
        timeout_sec = _require_timeout(timeout_sec)
        interval_sec = _require_interval(interval_sec, timeout_sec)

        baseline = self._snapshot(self._tracked_panes())
        if not baseline:
            return self._watch_result("no_panes", [], timeout_sec, interval_sec, 0)
        current = baseline
        event = classify_watch(baseline, current)
        waited = 0
        while event is None and waited + interval_sec <= timeout_sec:
            await _sleep(interval_sec)
            waited += interval_sec
            if progress is not None:
                await progress(waited, timeout_sec)
            current = self._snapshot(self._tracked_panes())
            event = classify_watch(baseline, current)
        return self._watch_result(
            event or "timeout", current, timeout_sec, interval_sec, waited
        )

    # --- operate --------------------------------------------------------------------

    def pane_spawn(
        self,
        prompt,
        repo_root,
        issue_ref=None,
        label=None,
        worktree=None,
        cwd=None,
        model=None,
        effort=None,
    ):
        """pane を割って agent セッションを起動する (spec §4.4)。

        Args:
            prompt: セッションの初期 prompt (**文面は呼び出し側が決める**)
            repo_root: 隔離しない / 新規隔離するときの cwd (main worktree root)
            issue_ref: 中立 issue ref。pane label `i<N>` の由来になる
            label: issue に紐づかない起動での pane label (`issue_ref` と排他)
            worktree: Claude Code 側 `--worktree` に渡す名前。新しい作業ツリーを作る
            cwd: 起動セッションの cwd。既存の駐機 worktree へ再入するときに渡す
            model / effort: 起動 flag。未指定なら session default を継承する

        `issue_ref` と `label` はどちらか一方だけを渡す。issue を持たない起動 (棚卸し
        skill の並列実行など) のために label を直接受けるが、`i<N>` 表記は予約で弾く —
        許すと issue 由来でない pane が追跡・worktree 回収の対象に混ざる。

        起動セッションには pane label と同じ文字列を `--name` で与える。名前でしか相手を
        指せない経路 (ListAgents) から worker を引けるようにするためで、issue 由来かどうか
        で分岐しない — pane の見出しとセッション名を 1 つの文字列に保つ (ADR 0033)。

        `worktree` と `cwd` は排他 (mode は返り値の `mode` で明示する):

        | 引数 | mode | 起動 |
        |---|---|---|
        | どちらも無し | `plain` | cwd=repo_root、`--worktree` なし |
        | `worktree` | `created` | cwd=repo_root、`--worktree <名前>` |
        | `cwd` | `reentered` | cwd=その既存ツリー、`--worktree` なし |

        再入で `--worktree` を付けないのは、駐機 worktree の中に更に worktree を作ると
        直したい成果と別のツリーで作業することになるため。作業ツリーの作成は Claude
        Code 側に任せる (backend の worktree 機能を使うと二重管理になる)。

        同じ label の pane が既にあるときは起動しない — 同じ作業対象に 2 セッションを
        入れると同じ作業ツリーへ並列書き込みして成果が壊れる。

        **agent を検出できなかった (`ok: false`) ときは pane が残る。** 残った pane は
        label を占有するので、後始末をしないとその label の再 dispatch が以後ずっと
        「既にある」で撥ねられる。呼び出し側が `pane_close(pane_id)` してから
        (issue 由来なら `ledger_transition(issue_ref, "spawn_failed")` も) 始末する。
        本 port が勝手に閉じないのは、失敗した pane の中身が起動失敗の唯一の診断材料
        だから — 捨てるかどうかは判断であって機構ではない。
        """
        if (issue_ref is None) == (label is None):
            raise PaneError(
                "issue_ref (issue 由来の起動) と label (issue に紐づかない起動) は"
                "どちらか一方だけを渡す"
            )
        if issue_ref is not None:
            issue = refs.parse_issue_ref(issue_ref)["ref"]
            pane_label = refs.format_issue_slug(issue)
        else:
            issue = None
            pane_label = refs.require_free_label(label)
        if not isinstance(prompt, str) or not prompt.strip():
            raise PaneError("prompt が空 (起動セッションへ渡す初期指示を文字列で渡す)")
        if worktree and cwd:
            raise PaneError(
                "worktree (新規隔離の名前) と cwd (既存ツリーへの再入先) は同時に渡せない"
            )
        self.ensure_ready()

        existing = self._find_by_label(pane_label)
        if existing is not None:
            raise PaneError(
                f"label {pane_label} の pane が既にある (pane_id={existing['pane_id']})。"
                "同じ作業対象に 2 セッションを入れると作業ツリーへ並列書き込みする"
            )

        agent_bin = self.resolve_agent_bin()
        if not agent_bin:
            raise PaneError(f"{AGENT_BIN} が PATH に見つからない")

        if cwd:
            launch_cwd = Path(cwd)
            if not launch_cwd.is_absolute():
                launch_cwd = Path(repo_root) / launch_cwd
            if not launch_cwd.is_dir():
                raise PaneError(f"再入先の worktree が無い: {launch_cwd}")
            mode, worktree_flag = MODE_REENTERED, None
        else:
            launch_cwd = Path(repo_root)
            mode = MODE_CREATED if worktree else MODE_PLAIN
            worktree_flag = worktree or None

        command = build_command(
            agent_bin, prompt, model, effort, worktree_flag, name=pane_label
        )
        pane_id, agent = self.launch_pane(command, str(launch_cwd), pane_label)
        return {
            "backend": self.backend,
            "issue_ref": issue,
            "pane_id": pane_id,
            "label": pane_label,
            "mode": mode,
            "cwd": str(launch_cwd),
            "worktree": worktree_flag,
            "agent": agent,
            "agent_status": neutral_agent_status(agent, UNKNOWN_RAW_STATUS),
            # 起動直後は agent の検出待ちがあるので、検出できなかったことを失敗にせず
            # 事実として返す (pane は既に在るので pane_id を診断と後始末に使える)
            "ok": agent == AGENT_BIN,
            "command": command,
        }

    def pane_close(self, pane_id):
        """pane を閉じる (spec §4.4)。

        既に消えているのは close の期待結果と同値なので失敗にしない (`closed: false` +
        `reason: not_found`)。自 pane だけは拒む — 閉じると dispatcher 自身が死ぬ。
        """
        if pane_id and pane_id == self.self_pane_id():
            raise PaneError(f"{pane_id} は自分の pane (閉じると dispatcher 自身が死ぬ)")
        self.ensure_ready()
        found = next(
            (pane for pane in self.list_panes() if pane.get("pane_id") == pane_id), None
        )
        if found is None:
            return {
                "backend": self.backend,
                "pane_id": pane_id,
                "closed": False,
                "reason": "not_found",
                # pane 自体の消失は agent status の写像では出ない (一覧に居ないことで
                # しか観測できない)。中立語彙の `gone` が立つ唯一の経路がここ
                "agent_status": STATUS_GONE,
            }
        self.close_pane(pane_id)
        return {
            "backend": self.backend,
            "pane_id": pane_id,
            "label": found.get("label"),
            "closed": True,
            "reason": "closed",
        }

    def pane_send(self, pane_id, text):
        """稼働中の pane へ自由テキストを送る (spec §4.4)。

        送る**内容**の規範は持たない (旧 `send-conflict` の固定文面は廃止)。ただし
        送れない前提が成立しないときは拒む:

        - pane が無い / 自 pane (自分自身へ打ち込む)
        - agent が終了している (受け手が居ない)

        agent_status が `blocked` (permission 待ち・質問待ち) の pane へ送ると、人間宛の
        問い合わせへ代答してしまう。これは「送るべきか」の判断なので server は止めず、
        送信前に観測した status を返り値に載せる — 判断材料を返して判断は呼び出し側に
        置く (SKILL.md の指針の領分)。
        """
        if not isinstance(text, str) or not text.strip():
            raise PaneError("text が空 (pane へ送る本文を文字列で渡す)")
        if pane_id and pane_id == self.self_pane_id():
            raise PaneError(f"{pane_id} は自分の pane (自分自身へ送っても意味がない)")
        self.ensure_ready()

        found = next(
            (pane for pane in self.list_panes() if pane.get("pane_id") == pane_id), None
        )
        if found is None:
            raise PaneError(f"pane が無い: {pane_id} (observe_panes で現況を取り直す)")
        raw_detail = self._require_agent_field(pane_id, self.get_pane(pane_id))
        detail = self._neutral_pane({**found, **raw_detail})
        if detail["agent"] is None:
            raise PaneError(
                f"{pane_id} の agent は終了している (受け手が居ない。pane_close の対象)"
            )
        self.send_text(pane_id, text)
        return {
            "backend": self.backend,
            "pane_id": pane_id,
            "label": detail["label"],
            "sent": True,
            "text": text,
            # 送信前に観測した status。blocked へ送ったことを事後に読み取れるようにする
            "agent_status": detail["agent_status"],
            "agent_status_raw": detail["agent_status_raw"],
        }

    # --- 中立化 / 内部 -------------------------------------------------------------

    def _neutral_pane(self, pane):
        """backend の pane dict → 中立 schema。"""
        label = pane.get("label") or ""
        number = refs.parse_issue_slug(label)
        raw = pane.get("agent_status_raw") or UNKNOWN_RAW_STATUS
        agent = pane.get("agent")
        return {
            "pane_id": pane.get("pane_id"),
            "label": label or None,
            # slug は tracker を持てないので ref へは戻さない (join は resolve の責務)
            "issue_number": number,
            "tracked": number is not None,
            "is_self": pane.get("pane_id") == self.self_pane_id(),
            "agent": agent,
            "agent_status": neutral_agent_status(agent, raw),
            "agent_status_raw": raw,
        }

    def _tracked_panes(self):
        """追跡対象 (`i<N>` label かつ自 pane でない) の pane 一覧。"""
        self_id = self.self_pane_id()
        return [
            pane
            for pane in self.list_panes()
            if pane.get("pane_id") != self_id
            and refs.parse_issue_slug(pane.get("label") or "") is not None
        ]

    def _find_by_label(self, label):
        """label 一致の pane を 1 件返す (自 pane も含めて探す)。

        自 pane を除外しないのは、前回の残骸 label が自分に残っている状態を「その label
        で存在する」とそのまま見せるため — 除外すると同じ label の pane を二重に作る。
        """
        return next(
            (pane for pane in self.list_panes() if (pane.get("label") or "") == label),
            None,
        )

    def _snapshot(self, panes):
        """pane 一覧 → agent 込みの中立 snapshot。

        list と get の間に pane が消える race では get が失敗する。再 list で不在が
        確定したら「消えた」として除外する (期待イベントであり異常ではない)。在るのに
        get が失敗するのは backend 側の異常なので fail-closed で再送出する。

        agent field の検査は**追跡 pane (`tracked`) にだけ**掛ける。workspace には
        claude を起動していない素の shell pane が同居し、その応答には agent key ごと
        無い。全 pane に掛けると、そういう pane が 1 つ混じるだけで観測が丸ごと失敗する
        (追跡 pane は 1 つも壊れていないのに空き slot すら読めなくなる)。
        """
        observed = []
        for pane in panes:
            pane_id = pane.get("pane_id")
            try:
                detail = self.get_pane(pane_id)
            except PaneError:
                if any(alive.get("pane_id") == pane_id for alive in self.list_panes()):
                    raise
                continue
            # 例外の外で検査する — race の except 節に入れると「応答が壊れている」を
            # 「消えた」に丸めてしまう
            neutral = self._neutral_pane({**pane, **detail})
            # 検査の範囲を返り値の `tracked` と同じ式から取る — 別々に書くと、追跡の
            # 印と fail-closed の対象が後からずれる
            if neutral["tracked"]:
                self._require_agent_field(pane_id, detail)
            observed.append(neutral)
        return observed

    @staticmethod
    def _require_agent_field(pane_id, detail):
        """応答に agent field があることを確かめる。

        欠落を null (= セッション終了) と誤読すると、追跡 pane を誤って回収し assignee
        まで外すことになる。読めなかったときは観測失敗として即座に表面化させる。

        どの pane に掛けるかは呼び出し側が決める — 誤読が高くつくのは台帳と繋がる
        追跡 pane だけで、追跡外の pane の欠落は観測結果にそのまま載せてよい。
        """
        if "agent" not in detail:
            raise PaneError(f"pane {pane_id} の応答に agent field が無い")
        return detail

    def _watch_result(self, event, panes, timeout_sec, interval_sec, waited_sec):
        return {
            "backend": self.backend,
            "event": event,
            "timeout_sec": timeout_sec,
            "interval_sec": interval_sec,
            "waited_sec": waited_sec,
            "count": len(panes),
            "panes": panes,
        }


# --- 台帳を伴う送信 (tool `pane_send` の実体) -----------------------------------------


def send_and_log(port, ledger, pane_id, text, issue_ref=None):
    """pane へ送り、`issue_ref` を渡された呼び出しだけ台帳の events へ記録する。

    port と ledger をどちらも引数で受ける (生成は tool 層の責務)。記録するかどうかは
    引数だけで決まる — 何を割り込ませたかを履歴に残すかは呼び出し側の判断であって、
    本 module は台帳の中身を読まない。
    """
    result = port.pane_send(pane_id, text)
    if issue_ref:
        ledger.log_event(
            issue_ref, "pane_send", fields={"pane_id": pane_id, "text": text}
        )
    result["logged"] = bool(issue_ref)
    return result


# --- 引数の検証 -----------------------------------------------------------------------


def _require_timeout(timeout_sec):
    timeout_sec = int(timeout_sec)
    if timeout_sec < 1:
        raise PaneError(f"timeout_sec は 1 以上で渡す (受け取った値: {timeout_sec})")
    if timeout_sec > WATCH_MAX_TIMEOUT_SEC:
        raise PaneError(
            f"timeout_sec の上限は {WATCH_MAX_TIMEOUT_SEC} 秒 (受け取った値: {timeout_sec})。"
            "2 分を超える呼び出しは自動 background 化され、結果を見て次を決める同期"
            "フローが崩れる。長い監視は本 tool の反復で組む"
        )
    return timeout_sec


def _require_interval(interval_sec, timeout_sec):
    interval_sec = int(interval_sec)
    if interval_sec < 1:
        raise PaneError(f"interval_sec は 1 以上で渡す (受け取った値: {interval_sec})")
    if interval_sec > timeout_sec:
        raise PaneError(
            f"interval_sec ({interval_sec}) が timeout_sec ({timeout_sec}) より大きい "
            "(1 度も poll せずに返る)"
        )
    return interval_sec


def get_adapter(backend):
    """backend 名 → adapter。未実装 backend は継ぎ目であることを名指しで失敗させる。

    tmux が「未知」ではなく「継ぎ目だけ設計した」であることを実行時に反証可能な形で
    残す (spec §8)。
    """
    if backend == "herdr":
        import pane_herdr

        return pane_herdr.HerdrAdapter()
    if backend == "tmux":
        raise PaneError(
            "tmux adapter は未実装 — port 境界 (継ぎ目) のみを設計してある (spec §8)"
        )
    raise PaneError(f"未知の pane backend: {backend!r} (候補: herdr)")


def _require_import_time_consistency():
    """中立語彙の写像が vocabulary の正本と一致することを import 時に検証する。"""
    mapped = {STATUS_RUNNING, STATUS_IDLE, STATUS_EXITED, STATUS_GONE}
    if mapped != set(vocabulary.AGENT_STATUSES):
        raise ValueError(
            "pane: agent status の写像先が vocabulary.AGENT_STATUSES と不一致 "
            f"(mapped={sorted(mapped)})"
        )


_require_import_time_consistency()
