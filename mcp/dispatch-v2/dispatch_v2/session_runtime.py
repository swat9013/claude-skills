"""SessionRuntime port — worker の実行環境との継ぎ目 (設計 §2 の 4 port のうち 1 つ)。

**port が扱うのは runtime handle だけで、Session の identity (ULID) は台帳側にある**
(設計 決定 13)。port の実装 (adapter) は台帳を知らず、台帳は runtime の綴りを知らない。

port を切った理由は volatility — runtime は 4 port の中で最も入れ替わりやすい (herdr →
tmux → SDK)。逆に git worktree は代替が現実的でないので port にしていない (決定 5)。

観測面が 2 つあるのは、**Session の終わり方 4 値のうち `exited` と `gone` が別の観測だから**:

```
sight_all()  … runtime に今在る handle の列挙  → 台帳の handle が居なければ gone
sight(handle) … handle 1 件の詳細               → agent が居なければ exited
```

片方しか持たない port を書くと 2 値が 1 値に潰れる (v1 が `agent_exited` / `pane_gone` を
別 event として持っていたのと同じ理由)。
"""

import shlex
from collections import namedtuple

# runtime から観測した 1 handle。**生の activity をそのまま運ぶ** — 中立 3 値への写像は
# adapter が持ち、写せなかった生値は `None` のまま台帳へ届く (running へ寄せない)
RuntimeSighting = namedtuple("RuntimeSighting", "handle label agent_present activity_raw")

# 呼び出し側 (MCP server) が観測した割り元 = 新しい実行単位をどこから割るか。**daemon の
# 環境ではなく呼び出し側が持つ** — daemon はマシンに 1 プロセスで長命なので、起動時に継承した
# 実行単位が先に死ぬと以後の launch が全部落ちる (gh#932)。呼び出し側は orchestrator セッション
# の子プロセスなので、その観測値は常に今生きている実行単位を指す。
# `workspace` を一緒に運ぶのは、**割った worker がどこに並ぶかを呼び出し側が名乗るため** —
# adapter は割った先として覚え、観測窓に加える。handle から読めば足りるように見えるが、その
# 綴りは runtime 固有で、割り元が使えなかったときに残る残骸を探す窓もここから来る (gh#952)
RuntimeAnchor = namedtuple("RuntimeAnchor", "handle workspace")

# 起動する agent。model / prompt を解釈しないのは v1 と同じ (policy-free)
AGENT_BIN = "claude"

# 空 nudge が運ぶ唯一の文字列。**escalation の中身は 1 文字も入らない** — 受け手は inbox を
# pull して読むので、ここに事象を書くと配送経路が 2 つになり、nudge が落ちた分だけ情報が
# 欠ける (設計 決定 11「pull 正 + 空 nudge」)。定数を port 側に置いてあるのは、「内容を
# 運ばない」が adapter ごとの実装ではなく port の性質だから
NUDGE_TEXT = "dispatch-v2: inbox"


class SessionRuntimeError(RuntimeError):
    """runtime との通信に失敗した / 前提が成立していない。

    **「runtime が答えない」を「session が居ない」と読ませない**ため、観測失敗はこの例外で
    上げる。混同すると、CLI が一時的に落ちただけで全 Session を終わったことにしてしまう。
    """


class LaunchFailed(SessionRuntimeError):
    """起動そのものが失敗した (Session は ended(launch_error) として記帳される)。

    `handle` は **失敗した時点で runtime に実行単位が残っているならその handle**。実行単位を
    作った後の手順 (label 付け / command 実行) で落ちたときに handle を捨てると、生きている
    実行単位が台帳のどこからも指されない残骸になる (自分で orphan を作ってしまう)。
    """

    def __init__(self, message, *, handle=None):
        self.handle = handle
        super().__init__(message)


class SessionRuntime:
    """worker 実行環境の継ぎ目。adapter が実装するのは以下の method だけ。"""

    backend = None

    def ensure_ready(self):
        """起動前提を検査する (成立しなければ SessionRuntimeError)。"""
        raise NotImplementedError

    def launch(self, *, command, cwd, label, anchor):
        """command を走らせる実行単位を作り、その runtime handle を返す。

        `anchor` は呼び出し側が観測した `RuntimeAnchor`。**観測していないときだけ `None`**
        で、省略はできない — 既定値を持たせると、呼び出し側が渡し忘れた経路が「観測して
        いない」と区別できなくなる。`None` を渡された adapter は**起動せずに落とす**
        (縮退先を持たない): 新しい実行単位が呼び出し元の隣に並ばないと、人はそれを探せない
        (gh#952)。
        """
        raise NotImplementedError

    def send(self, handle, text):
        """走っている agent へテキストを届ける。"""
        raise NotImplementedError

    def notify(self, handle):
        """agent を**起こすだけ**の合図を届ける (空 nudge)。

        `send` と分けてあるのは、運ぶものと失敗の扱いが逆だから:

        | | 何を運ぶか | 失敗したら |
        |---|---|---|
        | `send` | orchestrator が書いた内容 | 呼び出し側へ上げる (届かなければ用が済まない) |
        | `notify` | **何も運ばない** (`NUDGE_TEXT` 固定) | 呼び出し側が握って log へ出す (届かなくても escalation は inbox に残る) |

        配送の正は inbox の pull で、これは「見に来い」以上の意味を持たない (設計 決定 11)。
        内容を運ばせないので、**nudge が落ちても escalation は 1 つも消えない**。
        """
        raise NotImplementedError

    def close(self, handle):
        """実行単位を閉じる。"""
        raise NotImplementedError

    def sight(self, handle):
        """handle 1 件の観測。runtime に無ければ None。"""
        raise NotImplementedError

    def sight_all(self, *, scope_handles):
        """観測窓の中に今在る handle の列挙 (台帳外 session の検出にも使う)。

        `scope_handles` は台帳が既に知っている handle。**既定値を持たせない** — 省略できると、
        渡し忘れた呼び出しが gh#952 以前の狭い窓へ黙って落ちる。**runtime 全体を列挙しない**
        のは、別 project の実行単位や人間自身の実行単位を自分の追跡対象として拾わないため。
        窓の綴り (workspace 等) は runtime 固有なので adapter が持ち、呼び出し側は「自分が
        知っている handle の周り」とだけ言う。
        """
        raise NotImplementedError

    def classify_activity(self, activity_raw):
        """runtime の生 status を中立 3 値へ写す。写せなければ None。

        **表は adapter が持つ** — 生の語彙は runtime ごとに違うので、port 側に置くと
        runtime を差し替えたときに中立語彙の module が書き換わる。
        """
        raise NotImplementedError


def ended_reason_for(sighting):
    """観測から Session の終わり方を読む。まだ終わっていなければ None。

    - handle が runtime に無い → `gone`
    - handle は在るが agent が居ない → `exited`

    **`alive` だった Session にだけ当てられる写像**。起動直後 (`starting`) は agent がまだ
    現れていないのが正常なので、この写像をそのまま当てると生きている worker が exited に
    化ける (呼び出し側 `worker_sessions` が猶予を持つ)。
    """
    if sighting is None:
        return "gone"
    if not sighting.agent_present:
        return "exited"
    return None


def build_agent_command(prompt, *, name, model=None, agent_bin=AGENT_BIN):
    """pane 内で走らせる agent の command 文字列を組み立てる。

    `name` はセッション名 (`--name`)。cross-session messaging の相手発見が名前でしか行えない
    経路があるので、runtime 側の label と同じ文字列を渡して見出しとセッション名を揃える。

    **prompt / model の中身は解釈しない** (policy-free)。`shlex.quote` で組むのは、prompt に
    単引用符が日常的に含まれ、quote が崩れると pane の shell が引数を誤解釈するため。
    """
    parts = [agent_bin]
    if model:
        parts += ["--model", model]
    parts += ["--name", name, prompt]
    return " ".join(shlex.quote(part) for part in parts)
