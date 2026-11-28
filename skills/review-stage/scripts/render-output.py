#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""review-stage の出力のうち、条件から一意に決まる行を組み立てて stdout へ出す。

subcommand ごとに出すもの:

- `stop`: 「範囲」で止まる巡 (resolve-range の `stop`) の 4 行
- `failed`: resolve-range か prepare-inputs collect が JSON を返さなかった巡の 4 行
- `halted`: 担い手を 1 本も起動できなかった巡の 4 行 (`停止理由:` は起動できなかった観点の注記から組む)
- `rereview`: 観点が走った巡の `再レビュー:` 行
- `refute-count`: `反証対象:` の注記行

指摘の区分と、どの観点が起動できなかったかの判定は呼び出し元が持ち、本 script は持たない。

invariant:

1. 止まる巡の 4 行は `段階:` 行が `— 停止` で終わり、notes を含まない
2. `再レビュー:` 行の `停止` の理由は `未 commit` / `reviewer 未起動` / `その他: <自由文>` の 3 つに閉じる
   (呼び出し元はこの理由で、commit して呼び直すか止まるかを分ける)
3. 入力の形が違えば exit 非 0 で、stderr の 1 行目に要約を出す
"""

from __future__ import annotations

import argparse
import sys

REREVIEW_LINE = "再レビュー: "
REQUIRED = "要"
STOPPED = "停止"
STOP_UNCOMMITTED = "未 commit"
STOP_REVIEWER_UNSTARTED = "reviewer 未起動"
STOP_OTHER = "その他"
NOT_REQUIRED_NO_ACT_ON = "不要 — Act on なし"
NOT_REQUIRED_DOCS = "不要 — docs 段階"
NOT_REQUIRED_REREVIEWED = "不要 — 再レビュー済み"
STAGES = ("docs", "軽量", "標準")
UNDECIDED_STAGE = "未判定"
RUNNER_INCOMPLETE = "進行役 未完了:"
# 起動できなかった観点の注記の頭 (SKILL.md 手順 2 がその観点の節に書く行)
UNSTARTED_NOTE_PREFIXES = ("欠陥探し skip:", "reviewer 未起動:", "adversarial verification 未起動:", "入力を読めず:")
RETRY_TAIL = "起動できなかった担い手が解決する状態にしてから (入力を読めなかった観点は、読めなかった path を確かめてから) 呼び直す"
REVIEWER_PERSPECTIVES = ("principle", "spec", "design")
SCRIPT_FAILED_REASON = {"resolve-range": "resolve-range が失敗", "prepare-inputs": "prepare-inputs が失敗"}


def _stopped(stage: str, reviewed: str, reason: str, detail: str, runner_incomplete: str | None) -> str:
    if reason not in (STOP_UNCOMMITTED, STOP_REVIEWER_UNSTARTED) and not reason.startswith(f"{STOP_OTHER}: "):
        raise ValueError(f"停止の理由が 3 つのどれでもない: {reason} ({STOP_UNCOMMITTED} / {STOP_REVIEWER_UNSTARTED} / {STOP_OTHER}: <自由文>)")
    if runner_incomplete:
        detail = f"{RUNNER_INCOMPLETE} {runner_incomplete}; {detail}"
    return (
        f"段階: {stage} — {STOPPED}\n"
        f"reviewed: {reviewed}\n"
        f"{REREVIEW_LINE}{STOPPED} — {reason}\n"
        f"停止理由: {detail}\n"
    )


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "出力なし")


def unresolved_reviewed(range_arg: str | None, base_branch: str | None, prev: str | None) -> str:
    """範囲を解決できなかった巡の `reviewed:` の値。渡された args をそのまま並べる。

    resolve-range.py が止まるときもこの関数を読み込んで使う。正本を本 script に置くのは、resolve-range.py が
    読み込めない巡 (構文の誤りなど) でも `failed` が 4 行を組めるようにするため
    """
    passed = [a for a in (range_arg, base_branch and f"base={base_branch}", prev and f"prev={prev}") if a]
    return f"未解決: {' '.join(passed) if passed else 'args なし'}"


def halted(stage: str, reviewed: str, notes: list[str], runner_incomplete: str | None) -> str:
    if not notes:
        raise ValueError("起動できなかった観点の注記 (--note) が無い")
    if bad := [n for n in notes if not n.startswith(UNSTARTED_NOTE_PREFIXES)]:
        raise ValueError(f"起動できなかった観点の注記でない: {bad[0]} ({' / '.join(UNSTARTED_NOTE_PREFIXES)} で始める)")
    reviewer_unstarted = any(n.startswith("reviewer 未起動:") for n in notes)
    reason = STOP_REVIEWER_UNSTARTED if reviewer_unstarted else f"{STOP_OTHER}: 起動できた観点が 0 本"
    return _stopped(stage, reviewed, reason, f"{'; '.join(notes)} — {RETRY_TAIL}", runner_incomplete)


def rereview(mode: str, stage: str, act_on: int, next_round: str | None, unreadable: list[str], reviewer_unstarted: bool) -> str:
    # reviewer 未起動と入力を読めずは、Act on の件数によらず止める。どちらも呼び直しを初回として回させる
    # (agent の読み込みは session の中で変わらず、要で始めると同じ結果を繰り返す。再レビューは増分だけを
    # 読むので、要で進むと初回の差分が読めなかった観点を通らない)。攻撃役だけの未起動・入力を読めずでは
    # 値を変えない (起動できた観点が同じ範囲を見ている)
    if reviewer_unstarted:
        value = f"{STOPPED} — {STOP_REVIEWER_UNSTARTED}"
    elif any(p in REVIEWER_PERSPECTIVES for p in unreadable):
        value = f"{STOPPED} — {STOP_OTHER}: 入力を読めず"
    elif stage == "docs":
        value = NOT_REQUIRED_DOCS
    elif mode == "再レビュー":
        value = NOT_REQUIRED_REREVIEWED
    elif act_on == 0:
        value = NOT_REQUIRED_NO_ACT_ON  # 直す修正が無いので増分が生まれない
    else:
        if not next_round or next_round == "なし":
            raise ValueError("初回で Act on があるのに次巡の観点 (--next) が無い")
        value = f"{REQUIRED} — 次巡は {next_round}"
    return f"{REREVIEW_LINE}{value}\n"


def refute_count(candidates: int, findings: int) -> str:
    if not 0 <= candidates <= findings:
        raise ValueError(f"落とし候補 {candidates} 件が今巡の指摘 {findings} 件の範囲に無い")
    return f"反証対象: 落とし候補 {candidates} 件 (今巡の指摘 {findings} 件中)\n"


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # 呼び出し元は stderr の 1 行目を読むので、usage は出さず原因だけを出す
        self.exit(2, f"{self.prog}: {message}\n")


def main(argv: list[str]) -> int:
    ap = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("stop", help="「範囲」で止まる巡の 4 行")
    s.add_argument("--reviewed", required=True, help="resolve-range の reviewed")
    s.add_argument("--reason", required=True, help="resolve-range の stop.reason")
    s.add_argument("--detail", required=True, help="resolve-range の stop.detail")
    s.add_argument("--runner-incomplete", help="進行役が報告を返さず、呼び出し元が代行した巡の起きたこと")

    f = sub.add_parser("failed", help="script が JSON を返さなかった巡の 4 行")
    f.add_argument("--script", required=True, choices=tuple(SCRIPT_FAILED_REASON))
    f.add_argument("--stderr", required=True, help="失敗した script の stderr (1 行目を使う)")
    f.add_argument("--reviewed", help="prepare-inputs の失敗: resolve-range の reviewed")
    f.add_argument("--range", dest="range_arg", help="resolve-range の失敗: 渡した範囲の args")
    f.add_argument("--base-branch", help="resolve-range の失敗: 渡した base=")
    f.add_argument("--prev", help="resolve-range の失敗: 渡した prev=")
    f.add_argument("--runner-incomplete")

    h = sub.add_parser("halted", help="担い手を 1 本も起動できなかった巡の 4 行")
    h.add_argument("--stage", required=True, choices=STAGES)
    h.add_argument("--reviewed", required=True)
    h.add_argument("--note", action="append", default=[], help="起動できなかった観点ごとの注記 (手順 2 が節に書く行)")
    h.add_argument("--runner-incomplete")

    r = sub.add_parser("rereview", help="観点が走った巡の再レビュー: 行")
    r.add_argument("--mode", required=True, choices=("初回", "再レビュー"))
    r.add_argument("--stage", required=True, choices=STAGES)
    r.add_argument("--act-on", required=True, type=int, help="全観点の Act on の件数")
    r.add_argument("--next", dest="next_round", help="進行役の報告の次巡: 行")
    r.add_argument("--unreadable", action="append", default=[], choices=(*REVIEWER_PERSPECTIVES, "adversarial"),
                   help="入力を読めず: を返した観点")
    r.add_argument("--reviewer-unstarted", action="store_true", help="reviewer 未起動: の注記がある巡")

    c = sub.add_parser("refute-count", help="反証対象: の注記行")
    c.add_argument("--candidates", required=True, type=int)
    c.add_argument("--findings", required=True, type=int)

    args = ap.parse_args(argv)
    try:
        if args.command == "stop":
            out = _stopped(UNDECIDED_STAGE, args.reviewed, args.reason, args.detail, args.runner_incomplete)
        elif args.command == "failed":
            if args.script == "prepare-inputs":
                if not args.reviewed:
                    raise ValueError("prepare-inputs の失敗には --reviewed (resolve-range の reviewed) が要る")
                reviewed = args.reviewed
            else:
                reviewed = unresolved_reviewed(args.range_arg, args.base_branch, args.prev)
            out = _stopped(
                UNDECIDED_STAGE, reviewed, f"{STOP_OTHER}: {SCRIPT_FAILED_REASON[args.script]}",
                _first_line(args.stderr), args.runner_incomplete,
            )
        elif args.command == "halted":
            out = halted(args.stage, args.reviewed, args.note, args.runner_incomplete)
        elif args.command == "rereview":
            out = rereview(args.mode, args.stage, args.act_on, args.next_round, args.unreadable, args.reviewer_unstarted)
        else:
            out = refute_count(args.candidates, args.findings)
    except ValueError as e:
        print(f"ValueError: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
