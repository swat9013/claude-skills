"""中立 ref 形式 (`gh#386` / `glab#12` / `jira:PROJ-9` / `gh!401`) の parse と format。

spec §3.2 の「中立 ref 形式が対応付けの生命線」に対応する。台帳の key・tool 引数・
event log はすべてこの形式だけを使い、tracker 固有の番号体系 (GitHub の number /
GitLab の iid / Jira の key) を直接持ち回らない。

tracker 語彙の追加はここ 1 箇所で閉じる: ISSUE_PATTERNS / PR_PATTERNS に entry を足すと
import 時の整合検査が TRACKERS との対応を強制する。
"""

import re

TRACKERS = ("gh", "glab", "jira")

# tracker → issue ref の正規表現。名前付き group で number / key を取り出す。
# gh / glab は `<tracker>#<正の整数>`、jira は `jira:<KEY>-<正の整数>`
ISSUE_PATTERNS = {
    "gh": re.compile(r"^gh#(?P<number>[1-9][0-9]*)$"),
    "glab": re.compile(r"^glab#(?P<number>[1-9][0-9]*)$"),
    "jira": re.compile(r"^jira:(?P<key>[A-Z][A-Z0-9]*-[1-9][0-9]*)$"),
}

# tracker → PR (merge request) ref の正規表現。`<tracker>!<正の整数>`。
# jira は PR を持たない (課題管理のみ) ので entry を置かない — spec §4.3 の表どおり
PR_PATTERNS = {
    "gh": re.compile(r"^gh!(?P<number>[1-9][0-9]*)$"),
    "glab": re.compile(r"^glab!(?P<number>[1-9][0-9]*)$"),
}

# issue を「短い識別子」で指す表記 (`i386`)。pane label (#404) と worktree ディレクトリ名
# (#405) が同じ綴りを使うので、両者が別定義を持って drift しないよう本 module に置く。
# **tracker を持てない** のが ref との違い — 逆写像 (slug → ref) には tracker を補う必要が
# あり、それは「1 repo = 1 tracker」を知っている呼び出し側 (server) の責務。
ISSUE_SLUG = re.compile(r"^i(?P<number>[1-9][0-9]*)$")

# issue に紐づかない pane を指す label (`inv-permissions` 等)。issue slug と同じ場所
# (pane label) を使うので、両者が衝突しない綴りかをここで一括して決める
FREE_LABEL = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class RefError(ValueError):
    """ref の書式が中立形式に合わない。"""


def parse_issue_ref(ref):
    """issue ref を {"ref", "tracker", "number"|"key"} に分解する。

    number は gh / glab、key は jira。どちらか一方だけが非 None になる。
    """
    if not isinstance(ref, str) or not ref:
        raise RefError("issue ref が空")
    for tracker, pattern in ISSUE_PATTERNS.items():
        matched = pattern.match(ref)
        if matched is None:
            continue
        groups = matched.groupdict()
        return {
            "ref": ref,
            "tracker": tracker,
            "number": int(groups["number"]) if groups.get("number") else None,
            "key": groups.get("key"),
        }
    raise RefError(
        f"不正な issue ref: {ref!r} (形式: gh#<N> / glab#<N> / jira:<KEY>-<N>)"
    )


def parse_pr_ref(ref):
    """PR ref を {"ref", "tracker", "number"} に分解する。"""
    if not isinstance(ref, str) or not ref:
        raise RefError("PR ref が空")
    for tracker, pattern in PR_PATTERNS.items():
        matched = pattern.match(ref)
        if matched is None:
            continue
        return {
            "ref": ref,
            "tracker": tracker,
            "number": int(matched.group("number")),
        }
    raise RefError(f"不正な PR ref: {ref!r} (形式: gh!<N> / glab!<N>)")


def format_issue_ref(tracker, number=None, key=None):
    """tracker + number / key から issue ref を組み立てる (往復で parse できる形)。"""
    if tracker not in ISSUE_PATTERNS:
        raise RefError(f"未知の tracker: {tracker!r} (候補: {', '.join(TRACKERS)})")
    if tracker == "jira":
        if not key:
            raise RefError("jira の issue ref には key が要る")
        ref = f"jira:{key}"
    else:
        if number is None:
            raise RefError(f"{tracker} の issue ref には number が要る")
        ref = f"{tracker}#{number}"
    return parse_issue_ref(ref)["ref"]


def format_pr_ref(tracker, number):
    """tracker + number から PR ref を組み立てる。"""
    if tracker not in PR_PATTERNS:
        raise RefError(f"PR ref を持たない tracker: {tracker!r}")
    if number is None:
        raise RefError(f"{tracker} の PR ref には number が要る")
    return parse_pr_ref(f"{tracker}!{number}")["ref"]


def format_issue_slug(issue_ref):
    """issue ref → `i<N>` (pane label / worktree ディレクトリ名の共通表記)。

    番号を持たない ref (jira) は写せないので RefError。番号体系の無い tracker に
    `i<何か>` を当てると slug から番号を読み戻す経路が破綻する。
    """
    parsed = parse_issue_ref(issue_ref)
    if parsed["number"] is None:
        raise RefError(f"{parsed['ref']} は番号を持たないので i<N> 表記に写せない")
    return f"i{parsed['number']}"


def parse_issue_slug(slug):
    """`i<N>` → 番号。形が合わなければ None。

    例外にしないのは、pane label の一覧から追跡対象を篩う用途 (label は `dispatch` や
    `editor` など任意の文字列でありうる) が主だから。
    """
    matched = ISSUE_SLUG.match(slug or "")
    return int(matched.group("number")) if matched else None


def require_free_label(label):
    """issue に紐づかない pane label を検証して返す (`i<N>` 表記は予約)。

    `i<N>` を許すと `parse_issue_slug` がそれを issue 番号として読み戻し、issue 由来で
    ない pane が追跡対象・worktree 回収対象として扱われる。綴りを絞るのも同じ理由で、
    空白や大文字混じりの label は backend 側の一覧照合で同一性が崩れる。
    """
    if not isinstance(label, str) or not FREE_LABEL.match(label):
        raise RefError(
            f"不正な pane label: {label!r} (形式: 英小文字・数字・ハイフン、先頭は英数字)"
        )
    if parse_issue_slug(label) is not None:
        raise RefError(f"{label!r} は issue slug 表記 (`i<N>`) なので label には使えない")
    return label


def _require_import_time_consistency():
    """ref 語彙の整合を import 時に検証する。"""
    missing = set(TRACKERS) - set(ISSUE_PATTERNS)
    extra = set(ISSUE_PATTERNS) - set(TRACKERS)
    if missing or extra:
        raise ValueError(
            "refs.ISSUE_PATTERNS: tracker 語彙が TRACKERS と不一致 "
            f"(missing={sorted(missing)}, extra={sorted(extra)})"
        )
    unknown = set(PR_PATTERNS) - set(TRACKERS)
    if unknown:
        raise ValueError(f"refs.PR_PATTERNS: 未知の tracker {sorted(unknown)}")


_require_import_time_consistency()
