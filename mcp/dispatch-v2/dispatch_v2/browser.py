"""dashboard の URL を人間の browser へ渡す (設計 `docs/design/dispatch-v2/system.md` 決定 27)。

**撃つのは MCP server プロセス**で daemon ではないこと、**`webbrowser.open` を server プロセスの
中では呼ばない**ことは、どちらも決定 27 が理由ごと持つ。ここが持つのは実装の要点だけ:

- 起動先の選択は stdlib の `webbrowser` に委ねる (`principle-build-vs-buy`) — OS ごとの
  `open` / `xdg-open` の分岐を自前で持たない
- それを撃つのは別プロセス (`browser_launcher`) で、stdio を切り、成否を exit code で受ける
- 待ち切れなかったときは**成功とも失敗とも言わない** (前面で走る browser の正常形なので
  失敗にはできず、確かめてもいないので成功とも言えない)
"""

import subprocess
import sys
from collections import namedtuple
from pathlib import Path

from dispatch_v2 import browser_launcher

#: 子として走らせる file。**import して解決する** — 綴りを間違えたら module の import で落ちる
LAUNCHER_PATH = Path(browser_launcher.__file__).resolve()

#: 子の終了を待つ上限。**超えても失敗にしない** — 端末内で走る browser は表示している間
#: 返らないので、待ち切れないことは「渡せなかった」の証拠にならない
HANDOFF_TIMEOUT_SEC = 5

#: 起こせなかった理由。**原因を言い当てない** — `webbrowser.open` の False は「起動先が
#: 1 つも無い」と「見つけた起動先が全部失敗した」の両方で返る (`GenericBrowser` は
#: `OSError` を握って False にする)。片方を名指すと、人間は無い問題を探しに行く
LAUNCH_FAILURE = "既定 browser を起こせなかった (起動先が無いか、起動に失敗した)"

UNCONFIRMED = (
    f"既定 browser へ渡したが、{HANDOFF_TIMEOUT_SEC} 秒では起動の成否を確かめられていない "
    "(前面で走る browser は表示している間ずっと終わらない)"
)

#: 引き渡しの結果。`confirmed` が false なら `launched` は**観測結果ではなく既定の扱い**
Handoff = namedtuple("Handoff", "launched confirmed")


def hand_off_to_browser(url, *, env=None, timeout=HANDOFF_TIMEOUT_SEC):
    """別プロセスで `webbrowser.open` を撃ち、渡せたかどうかを返す。

    `env` / `timeout` を受けるのはテストが `$BROWSER` と待ち時間を固定するため
    (`principle-test-double-boundary`: browser は OS 設定で決まる unmanaged dependency)。
    `env` が None なら子はこのプロセスの環境を継ぐ。
    """
    child = subprocess.Popen(
        [sys.executable, str(LAUNCHER_PATH), url],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        return Handoff(launched=child.wait(timeout=timeout) == 0, confirmed=True)
    except subprocess.TimeoutExpired:
        # 子の stdio は切ってあるので、走らせたまま答えて構わない (待ち続けると tool 呼び出しが
        # 返らなくなる)。成否は観測できていないので `confirmed` で分かるようにする
        return Handoff(launched=True, confirmed=False)


def open_for_human(facts):
    """bind できている dashboard を browser で開き、何が起きたかまで併せて答える。

    引数の `facts` は `Dashboard.facts()` (= `/health` の `dashboard`) と同じ形。返り値は
    それに `opened` と `open_reason` を足したもので、**確かめられた成功のときだけ
    `open_reason` が空になる** (開けなかったときも、渡したが確かめられていないときも埋まる)。

    bind できていなければ **browser を撃たない** — 塞がった port を開くと人間には browser の
    エラー画面しか届かず、「開いたのに中身が無い」と読まれる (`principle-fail-loudly`)。

    差し替え点は module 属性の `hand_off_to_browser` (テストはここを patch する)。
    """
    if not facts.get("bound"):
        return _answer(facts, opened=False, open_reason="dashboard が bind できていないので開かない")
    try:
        handoff = hand_off_to_browser(facts["url"])
    except OSError as exc:
        return _answer(facts, opened=False, open_reason=f"browser を起こす子プロセスが作れない: {exc}")
    if not handoff.launched:
        return _answer(facts, opened=False, open_reason=LAUNCH_FAILURE)
    if not handoff.confirmed:
        return _answer(facts, opened=True, open_reason=UNCONFIRMED)
    return _answer(facts, opened=True, open_reason=None)


def _answer(facts, *, opened, open_reason):
    return {**facts, "opened": opened, "open_reason": open_reason}
