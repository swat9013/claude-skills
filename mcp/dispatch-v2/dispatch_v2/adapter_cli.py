"""tracker adapter が共有する CLI 起動の機構 (**語彙は持たない**)。

adapter 2 実装目 (#857) で、tracker に依らない部分が逐語 copy になったので括り出した。ここに
あるのは「どの CLI を起こしても同じ形になる」3 つだけ:

- 待つ起動の runner を作る (`runner`)
- 起動して stdout を JSON として読む (`json_output`)
- 中立 ref がこの adapter のものかを検査して番号を取る (`issue_number` / `cl_number`)

**tracker 固有のものは 1 つも置かない**。CLI 引数の綴り・語彙の写像・endpoint の形は adapter
側にあり、それらは tracker ごとに違う理由で変わるので重複のまま各 adapter に残す
(`principle-localize-change-impact` の「異なる理由で変わる似たコードは重複のまま残す」)。
**timeout の値も adapter が決める** — 機構だけを配り、どれだけ待つかは使う側の方針。

判定は「adapter を 1 つ足したとき、ここへ書き足す必要があるか」。あるなら、それは adapter 側に
置くべきものがここへ漏れている。
"""

import json

from dispatch_v2 import ports, proc, refs


def runner(*, timeout_sec):
    """待つ起動の runner を作る。失敗は観測失敗 (`ObservationError`) へ束ねる。"""
    return proc.command_runner(error=ports.ObservationError, timeout_sec=timeout_sec)


def json_output(run, argv, *, cli):
    """CLI を起動して stdout を JSON として読む。非 0 exit / 非 JSON は ObservationError。

    Args:
        run: `argv → (returncode, stdout, stderr)` の callable
        argv: 起動する引数列
        cli: error 文面に出す CLI 名 (どちらの store が答えなかったかを人が読むため)
    """
    returncode, out, err = run(argv)
    if returncode != 0:
        raise ports.ObservationError(
            f"{cli} が失敗 (exit {returncode}): {' '.join(argv[:4])}: {err.strip()}"
        )
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise ports.ObservationError(f"{cli} の stdout が JSON でない: {exc}") from exc


def issue_number(issue_ref, *, tracker):
    """中立 issue ref → その tracker の issue 番号。別 tracker の ref は ObservationError。"""
    return _number(refs.split_issue_ref(issue_ref), issue_ref, tracker=tracker)


def cl_number(cl_ref, *, tracker):
    """中立 CL ref → その tracker の CL 番号。別 tracker の ref は ObservationError。

    **人が記録した ref が渡る経路で実際に効く** (ADR 0059)。紐づきを tracker から引く置き場
    (gh / glab) では ref を組むのも読むのも同じ adapter なので到達しないが、Jira 置き場では
    紐づきの源が `wo_record_cl` の記録なので、綴りは人が渡した値そのもの。宣言と違う tracker の
    ref を記録すると、ここが唯一の関門になる。

    **破れたときの失敗が回復不能**なのが置く理由。番号だけ読む実装は `glab!34` を GitHub の
    PR 34 として撃ち、**観測は成功したまま無関係な CL の status を返す** — 誤った status は
    機械遷移の根拠になり、terminal は不変。port の署名は ref を受けると宣言している以上、
    その ref が自分のものかは受け口で確かめる (`principle-validate-at-boundaries`)。
    """
    return _number(refs.split_cl_ref(cl_ref), cl_ref, tracker=tracker)


def _number(split, ref, *, tracker):
    if split["tracker"] != tracker:
        raise ports.ObservationError(
            f"{tracker} adapter に {split['tracker']} の ref が渡った: {ref!r}"
        )
    return split["number"]
