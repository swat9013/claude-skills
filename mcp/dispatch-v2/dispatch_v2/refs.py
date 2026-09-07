"""中立 ref (issue `gh#386` / CL `gh!401`) の検証と分解。

台帳の key・tool 引数・event log はこの形式だけを使い、tracker 固有の番号体系 (GitHub の
number / GitLab の iid / Jira の key) を持ち回らない。v1 (`mcp/dispatch-ops/refs.py`) から
**書式の正規表現だけを copy** した — v1 とコードを共有しないのは併走中の結合を作らないため
(issue #850)。slug / pane label は v2 でまだ使う経路が無いので持ってこない。

**分解 (`split_issue_ref` / `split_cl_ref`) を公開しているのは、tracker adapter が消費者として
実在するから** (issue #851)。adapter は CLI へ番号を渡す必要があり、ref を割れるのはここ 1 箇所
に閉じる。逆向きの組み立て (`format_cl_ref`) も adapter が観測結果を中立語彙へ写すのに要る。

tracker 語彙の追加はここ 1 箇所で閉じる: ISSUE_PATTERNS / CL_PATTERNS に entry を足せば通る
ref が増える (両表の tracker 集合が食い違わないことは import 時に検査する)。
"""

import re

# tracker → issue ref の正規表現。gh / glab は `<tracker>#<正の整数>`、jira は `jira:<KEY>-<N>`
ISSUE_PATTERNS = {
    "gh": re.compile(r"^gh#(?P<number>[1-9][0-9]*)$"),
    "glab": re.compile(r"^glab#(?P<number>[1-9][0-9]*)$"),
    "jira": re.compile(r"^jira:(?P<key>[A-Z][A-Z0-9]*-[1-9][0-9]*)$"),
}

# tracker → issue ref の組み立て書式 (観測結果を中立語彙へ写す向き)。**読む表とは別に要る** —
# gh / glab は `<tracker>#<番号>` だが jira は `jira:<KEY>` で、区切りも識別子の種類も違う。
# 1 つの書式で組むと jira の観測結果が `jira#SWATCF-14` になり、自分の正規表現に弾かれる
ISSUE_REF_FORMATS = {
    "gh": "gh#{}",
    "glab": "glab#{}",
    "jira": "jira:{}",
}

# tracker → CL (PR / MR) ref の正規表現。`<tracker>!<正の整数>`。**jira は持たない** —
# Jira は変更置き場ではないので、CL ref を持つ tracker は issue tracker の部分集合になる
CL_PATTERNS = {
    "gh": re.compile(r"^gh!(?P<number>[1-9][0-9]*)$"),
    "glab": re.compile(r"^glab!(?P<number>[1-9][0-9]*)$"),
}


class RefError(ValueError):
    """ref の書式が中立形式に合わない。"""


def slug_of(ref):
    """issue ref → 人が読める短い識別子 (worktree の dir 名 / runtime の label に使う)。

    `gh#852` → `i852` / `jira:PROJ-9` → `proj-9`。**番号体系そのものは返さない** — 返すのは
    「人が目で追える名前」で、tracker の番号として再利用できる形にはしない。

    `gh#12` と `glab#12` は同じ slug になるが、**1 project の issue 置き場は宣言で 1 つ**
    なので同じ台帳の中では衝突しない。
    """
    require_issue_ref(ref)
    for pattern in ISSUE_PATTERNS.values():
        matched = pattern.match(ref)
        if matched is None:
            continue
        groups = matched.groupdict()
        number = groups.get("number")
        return f"i{number}" if number else groups["key"].lower()
    raise RefError(f"slug を導出できない issue ref: {ref!r}")  # pragma: no cover — 上で検証済み


def require_issue_ref(ref):
    """issue ref を検証して返す (境界での検証用)。合わなければ RefError。"""
    split_issue_ref(ref)
    return ref


def require_cl_ref(ref):
    """CL ref を検証して返す (境界での検証用)。合わなければ RefError。"""
    split_cl_ref(ref)
    return ref


def split_issue_ref(ref):
    """issue ref を `{"tracker", "number", "key"}` に割る。

    `number` は gh / glab、`key` は jira のみが入り、他方は None。**両方を持つ形にしてある
    のは、tracker を見ずに片方だけ読む呼び出しを書けないようにするため** — 番号体系が違う
    tracker を同じ欄で受けると、Jira の key が 0 として CLI へ渡るような取り違えが起きる。
    """
    if not isinstance(ref, str) or not ref:
        raise RefError("issue ref が空")
    for tracker, pattern in ISSUE_PATTERNS.items():
        matched = pattern.match(ref)
        if matched is not None:
            fields = matched.groupdict()
            number = fields.get("number")
            return {
                "tracker": tracker,
                "number": int(number) if number is not None else None,
                "key": fields.get("key"),
            }
    raise RefError(f"不正な issue ref: {ref!r} (形式: gh#<N> / glab#<N> / jira:<KEY>-<N>)")


def split_cl_ref(ref):
    """CL ref を `{"tracker", "number"}` に割る。"""
    if not isinstance(ref, str) or not ref:
        raise RefError("CL ref が空")
    for tracker, pattern in CL_PATTERNS.items():
        matched = pattern.match(ref)
        if matched is not None:
            return {"tracker": tracker, "number": int(matched.group("number"))}
    raise RefError(f"不正な CL ref: {ref!r} (形式: gh!<N> / glab!<N>)")


def format_issue_ref(tracker, identifier):
    """tracker と識別子から issue ref を組み立てる (観測結果を中立語彙へ写す経路)。

    `identifier` は gh / glab なら番号、jira なら key (`SWATCF-14`)。**書式は tracker ごとに
    引く** (`ISSUE_REF_FORMATS`) — 番号体系が違う tracker を 1 つの書式で組むと、綴りが
    自分の正規表現に合わなくなる。
    """
    template = ISSUE_REF_FORMATS.get(tracker)
    if template is None:
        raise RefError(f"未知の tracker: {tracker!r} (候補: {', '.join(ISSUE_REF_FORMATS)})")
    return require_issue_ref(template.format(identifier))


def format_cl_ref(tracker, number):
    """tracker と番号から CL ref を組み立てる。"""
    if tracker not in CL_PATTERNS:
        raise RefError(f"CL ref を持たない tracker: {tracker!r} (候補: {', '.join(CL_PATTERNS)})")
    return require_cl_ref(f"{tracker}!{number}")


def _require_pattern_tables_agree(issue_patterns, cl_patterns, issue_formats):
    """tracker 語彙の 3 表が整合していることを検査する。

    - CL ref を持つ tracker は issue tracker の**部分集合**。片方の表にしか無い tracker を
      作ると、その tracker の CL を観測しても `format_cl_ref` が落ちる (= 観測できたのに
      中立語彙へ写せない) 経路が生まれる
    - 読める tracker と書ける tracker は**一致**。ずれると、adapter が観測に成功した issue を
      中立 ref へ写せないまま tick が落ちる
    """
    orphan = set(cl_patterns) - set(issue_patterns)
    if orphan:
        raise ValueError(f"refs.CL_PATTERNS: issue tracker に無い tracker {sorted(orphan)}")
    unwritable = set(issue_patterns) - set(issue_formats)
    unreadable = set(issue_formats) - set(issue_patterns)
    if unwritable or unreadable:
        raise ValueError(
            f"refs.ISSUE_REF_FORMATS: ISSUE_PATTERNS と不一致 "
            f"(書式が無い={sorted(unwritable)}, 読めない={sorted(unreadable)})"
        )


_require_pattern_tables_agree(ISSUE_PATTERNS, CL_PATTERNS, ISSUE_REF_FORMATS)
