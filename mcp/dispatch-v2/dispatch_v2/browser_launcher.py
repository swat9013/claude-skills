"""子プロセス本体。URL を既定 browser へ渡し、成否を exit code で返す。

親 (`browser.hand_off_to_browser`) が `sys.executable` でこの file を直に走らせる。
**stdlib の `python -m webbrowser` では代われない** — あちらは `webbrowser.open` の戻り値を
捨てて常に 0 で終わるので、起こせなかったことが親から見えなくなる。
"""

import sys
import webbrowser


def main(argv):
    return 0 if webbrowser.open(argv[1]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
