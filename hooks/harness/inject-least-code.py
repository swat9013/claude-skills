#!/usr/bin/env python3
"""inject-least-code.py — SessionStart / SubagentStart: 書く前に確かめる順序 (注入文) を context へ載せる。

Why: 「要るか → 既にあるもので済むか → 1 行で済むか」を順に確かめて最小の実装に止める手順は、playbook を
    通るかに依らず当てるので、playbook の step では届け切れない。SessionStart の注入は Agent tool で起動した
    subagent に届かないので、SubagentStart にも登録して subagent にも載せる (設計は docs/design/hooks/system.md §1)。

役割: inject 専用。permission の判定は出さない。手順の正本は本 script の `INJECTED_TEXT` で、手順の理由と
    例外は原則 leaf `principle-least-code` が持つ。注入文は leaf の path だけを指し、leaf の本文は載せない —
    理由は注入文が名指す判断 (既存を広げるか・削ってよいか・新しい依存を足すか) のときに読めば足り、
    毎回載せると常時ロードの量が増える。

invariant:
    1. 発火した event (`hook_event_name`) と同じ `hookEventName` の envelope で、注入文を `additionalContext` に載せる
    2. 注入文は原則 leaf の絶対 path を持つ (注入を受けた読み手は索引を読んでいないので、名前だけでは
       届かない。`principle-least-code` が名指す他の leaf は同じ置き場にあると添える)
    3. payload を読めないとき・対象外の event のときは注入せず、理由を stderr に出して exit 0 で終わる

fail-posture: 縮退時は注入せずに終わる (passthrough で固定)。deny 権能を持たないので ADR 0046 の
    fail-closed 原則の対象外。脅威モデル: 素通しの損害は「手順が context に載らない」だけで、
    セッションと subagent の起動を止める害のほうが大きい。

Registration: **plugin の hooks/hooks.json** SessionStart (matcher `"*"` = startup / resume / clear /
    compact / fork の全 source。guard-worktree-escape.py と同じ group) と SubagentStart (matcher なし =
    全 agent)。規範が全 repo に共通なので、根拠は他 repo でも成り立つ (不変条件 (5))。compact の後にも
    載せるのは、compact で注入した context が残るかを host の仕様が定めていないため。
    量: 注入文 1 本 (上限は tests/test_inject_least_code.py) が、セッションの開始と subagent の起動のたびに
    1 回載る。コードを書かない subagent にも載せるのは、対象の agent の一覧を持つと agent を足すたびに
    ずれるため (注入文の 1 行目が当てる場面を絞っている)。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _prelude import read_stdin_text  # noqa: E402

LEAF = (
    Path(__file__).resolve().parents[2]
    / "skills" / "knowledge" / "principle-index" / "references" / "leaves" / "principle-least-code.md"
)
INJECTED_EVENTS = ("SessionStart", "SubagentStart")
INJECTED_TEXT = (
    "コード・設定・script を書く・直す前に、次の順で確かめ、最初に足りた段で止まる:\n"
    "1. そもそも要るか — 作るのは依頼が求めるものだけ。依頼に無い機能・オプション・設定項目・拡張点、1 回きりの helper・抽象層は含めない\n"
    "2. 同じ codebase に、依頼の処理をそのまま担うものが既にあるか — あれば呼ぶ\n"
    "3. 標準ライブラリ・プラットフォームの機能で済むか\n"
    "4. 導入済みの依存で済むか\n"
    "5. 一目で読める 1 行で済むか\n"
    "6. 残りは既存を広げるか・新しい依存か・自前かを決め、書くなら動く最小にする。広げるのは依頼の処理と同じ知識 (DRY: 同じ理由で変わる) を持つものだけ\n"
    "最小にするのは解法で、変えた箇所の呼び出し側・テスト・設定の追従と、安全のための処理は削らない。"
    f"既存を広げるか・削ってよいか・新しい依存を足すかを決めるときは、原則 leaf `principle-least-code` ({LEAF}) を"
    " Read する。そこが名指す `principle-*` も同じ置き場にある。\n"
    "コードを書いた作業の完了報告に、要りそうでも作らなかったものと、動かしていない経路を添える"
    " (形式の指定があればその中に収める)。"
    " [inject-least-code.py]"
)


class Skip(Exception):
    """注入せずに終わる理由を main() へ運ぶ。"""


def event_name(stdin_text: str) -> str:
    try:
        payload = json.loads(stdin_text)
    except ValueError as error:
        raise Skip(f"payload を JSON として読めない: {error}") from error
    event = payload.get("hook_event_name") if isinstance(payload, dict) else None
    if event not in INJECTED_EVENTS:
        raise Skip(f"注入しない event: {event!r}")
    return event


def main(stdin_text: str) -> tuple[int, str]:
    """hook の入口。(exit code, stdout 本文) を返す。注入しない理由は stderr に書く。"""
    try:
        event = event_name(stdin_text)
    except Skip as skip:
        sys.stderr.write(f"inject-least-code.py: 注入しない — {skip}\n")
        return 0, ""
    envelope = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": INJECTED_TEXT}}
    return 0, json.dumps(envelope, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    exit_code, stdout_text = main(read_stdin_text())
    sys.stdout.write(stdout_text)
    sys.exit(exit_code)
