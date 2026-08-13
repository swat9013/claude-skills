"""成果物 (mart / slice) の出力先を用意する (ADR 0031)。

全 tool は `/tmp` 配下へ mart / slice を書き、返すのは path だけ。出力には
**手入力 prompt の抜粋・個人 path・slash command の引数**が載るため、共有 /tmp に
world-readable で置かない。その規約を 1 箇所に置き、全 presentation 経路
(mart 4 本 + `find_invocations`) がここを通る。

置き場が server root なのは、mart 層 (`marts/`) にも on-disk 形式知識層
(`store/` `adapter/`) にも属さない presentation 共通の関心事だから。
"""

from __future__ import annotations

import os
from pathlib import Path

# 出力先の mode。store (`store.CACHE_DIR_MODE`) と同じ理由 (手入力 prompt 由来の
# 文字列を含む) だが、あちらは cache でこちらは PR 証拠として残す成果物なので、
# 定数は共有せず各層に置く。
OUTPUT_DIR_MODE = 0o700


def prepare_output_dir(path: str | Path) -> Path:
    """出力先を 0700 で用意して返す。

    **`mkdir(mode=)` は既存 dir には効かない**ので chmod も必ず行う。共有 /tmp では
    前回 umask 022 で作られた dir や、別ユーザーが先に作った同名 dir を引き継ぐ経路が
    現実にあり、作成時だけの mode 指定では塞げない。
    """
    resolved = Path(path)
    resolved.mkdir(parents=True, exist_ok=True, mode=OUTPUT_DIR_MODE)
    os.chmod(resolved, OUTPUT_DIR_MODE)
    return resolved
