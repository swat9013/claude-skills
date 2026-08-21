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

# issue を「短い識別子」で指す表記 (issue slug)。pane label (#404) と worktree ディレクトリ名
# (#405) が同じ綴りを使うので、両者が別定義を持って drift しないよう本 module に置く。
# 綴りは番号体系の有無で 2 通りに分かれる:
#
# - number slug (`i386`): gh / glab。**tracker を持てない** のが ref との違い — 逆写像
#   (slug → ref) には tracker を補う必要があり、それは「1 repo = 1 tracker」を知っている
#   呼び出し側 (server) の責務
# - key slug (`swatcf-14`): jira。番号体系が無く key が識別子なので、key を小文字化した
#   ものを綴りにする。ref の key は `[A-Z][A-Z0-9]*` なので小文字化は可逆で、**slug 単体で
#   ref へ戻せる** (tracker を補う必要が無い)
NUMBER_SLUG = re.compile(r"^i(?P<number>[1-9][0-9]*)$")
KEY_SLUG = re.compile(r"^(?P<project>[a-z][a-z0-9]*)-(?P<number>[1-9][0-9]*)$")

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
    """issue ref → issue slug (pane label / worktree ディレクトリ名の共通表記)。

    番号を持つ ref (gh / glab) は `i<N>`、key で識別する ref (jira) は key を小文字化した
    `<project>-<N>`。tracker ごとに綴りを分けるのは、番号体系の無い tracker に `i<何か>` を
    当てると slug から番号を読み戻す経路が破綻するため。
    """
    parsed = parse_issue_ref(issue_ref)
    if parsed["number"] is not None:
        return f"i{parsed['number']}"
    return parsed["key"].lower()


def parse_number_slug(slug):
    """`i<N>` → 番号。形が合わなければ None。

    例外にしないのは、pane label / branch 名の一覧から追跡対象を篩う用途 (label は
    `dispatch` や `editor` など任意の文字列でありうる) が主だから。

    key slug (jira) は**読まない** — 本関数の返り値は tracker を持たない番号であり、
    別 tracker の issue を同じ番号空間に混ぜると `i<N>` の worktree / pane と取り違える。
    両方の綴りを読みたい呼び出し側は `parse_issue_label` を使う。
    """
    matched = NUMBER_SLUG.match(slug or "")
    return int(matched.group("number")) if matched else None


def parse_issue_label(label):
    """pane label → issue 由来なら `{"slug", "number", "ref"}`、issue 由来でなければ None。

    `number` と `ref` はどちらか一方だけが非 None — slug の 2 綴りが「何を復元できるか」で
    分かれているのをそのまま返す:

    - number slug (`i386`) → `number` のみ。tracker を持たないので ref へは戻せない
      (持ち上げは「1 repo = 1 tracker」を知っている `resolve` の責務)
    - key slug (`swatcf-14`) → `ref` のみ (`jira:SWATCF-14`)。番号は他 tracker の番号空間と
      混ざるので**返さない** (混ぜると同番号の `i<N>` pane / worktree と取り違える)
    """
    slug = label or ""
    number = parse_number_slug(slug)
    if number is not None:
        return {"slug": slug, "number": number, "ref": None}
    matched = KEY_SLUG.match(slug)
    if matched is None:
        return None
    return {"slug": slug, "number": None, "ref": f"jira:{slug.upper()}"}


def slug_is_self_describing(issue_ref):
    """slug 単体から ref を復元できる ref か (tracker を補わずに join の鍵を作れるか)。

    key slug (`swatcf-14`) は tracker を綴りに含むので、pane label のような **tracker を
    持てない場所**から ref へ戻せる。number slug (`i386`) は戻せず、逆写像には「1 repo =
    1 tracker」を知っている呼び出し側が tracker を補う必要がある (`resolve` の責務)。

    `number is None` と書かずに往復で確かめるのは、slug の綴りを増やしたときに本関数が
    自動で追随するため — 綴りと判定を別々に書くと、片方だけ増えて join が黙って壊れる。
    """
    parsed = parse_issue_label(format_issue_slug(issue_ref))
    return parsed is not None and parsed["ref"] == parse_issue_ref(issue_ref)["ref"]


def require_free_label(label):
    """issue に紐づかない pane label を検証して返す (issue slug 表記は予約)。

    issue slug を許すと `parse_issue_label` がそれを issue 由来と読み戻し、issue 由来で
    ない pane が追跡対象・worktree 回収対象として扱われる (追跡 pane には agent field の
    fail-closed 検査も掛かるので、素の shell pane が 1 つ混じると観測が丸ごと落ちる)。
    綴りを絞るのも同じ理由で、空白や大文字混じりの label は backend 側の一覧照合で同一性が
    崩れる。
    """
    if not isinstance(label, str) or not FREE_LABEL.match(label):
        raise RefError(
            f"不正な pane label: {label!r} (形式: 英小文字・数字・ハイフン、先頭は英数字)"
        )
    if parse_issue_label(label) is not None:
        raise RefError(
            f"{label!r} は issue slug 表記 (`i<N>` / `<project>-<N>`) なので label には"
            "使えない (issue 由来の起動なら issue_ref を渡す)"
        )
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
