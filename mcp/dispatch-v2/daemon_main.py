#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""reconciler daemon の entry point。

**stdlib だけで動く** — MCP 薄 client からの lazy 起動 (`client._spawn_daemon`) が
`sys.executable` で直接叩けるようにするため。uv の cache が冷えている / offline の環境でも
起動が落ちない。

台帳 root は `DISPATCH_V2_ROOT` (未設定なら `~/.claude/dispatch-v2`)。root ごとに別プロセスに
なり、既定 root では**マシンに 1 プロセス**になる。
"""

import sys
from pathlib import Path

# uv run が PEP 723 script をどう起動しても sibling package を解決できるようにする
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dispatch_v2 import daemon, project  # noqa: E402


def main():
    root = project.default_root()
    try:
        daemon.run(root)
    except daemon.AlreadyRunning as exc:
        print(f"[dispatch-v2] 起動しない: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
