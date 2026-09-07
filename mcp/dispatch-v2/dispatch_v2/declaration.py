"""project の宣言 config (`dispatch-project.toml`) — **人が宣言する運用方針だけ**を置く file。

置き場は台帳ディレクトリ直下。**観測された事実 (稼働 clone の path 等) はここに書かない** —
あれは MCP server が cwd から解決して daemon へ渡す観測値で、正本は events.jsonl 側
(`principle-validate-at-boundaries` の「人の宣言」と「境界で受けた値」の分離)。

宣言するのは 2 系統:

- **置き場** (`[issue]` / `[pr]`) — どの repo を観測するか。**reconciler は決めない**、宣言が
  正本 (ADR 0036 の趣旨、`principle-mechanism-not-policy`)。cwd の remote から推測しない
- **機械作用の可否** (`[rules]`) — rule id → true / false。id の正本は rule catalog なので
  外から `known_rule_ids` で受け取る (declaration が catalog を import すると、catalog が
  declaration を読む向きと合わせて循環する)

書式は v1 (`docs/agents/issue-tracker.md` §「置き場の宣言」) と**同じにした**。理由は 2 つ:
運用者が 2 つ目の書式を覚えなくてよいこと、v1 / v2 併走中に「同じ project の宣言」を目で
対応付けられること。**file は共有しない** — v2 の宣言は v2 の台帳ディレクトリ
(`<v2 root>/<project key>/dispatch-project.toml`) に置く。台帳と宣言の identity 軸を揃えると、
別の clone で走る worker が自分の clone 側の宣言を読む経路が構造的に無くなる。コードも v1 と
共有しない (issue #850 の方針)。

**読むのは今使う key だけ** (`[issue]` の tracker / repo / ready_label、`[pr]` の tracker / repo、
`[rules]` の rule id)。v1 の `close_on_merge` / `done_status` / `claim_label` / `[worker]` は v2 の
観測経路がまだ消費しないので受け取らない。**未知の table / key / rule は名指しで loud に落とす**
— 綴りを間違えた宣言が黙って既定値のまま通ると、「off にしたつもりの機械作用が動き続ける」
「読まない宣言が効いていると誤読する」という最も危ない読み違いが起きる。v1 の宣言を丸ごと
copy して置いた環境はここで落ちる。

**2 系統は独立している**。`[rules]` だけを置いた project は合法で、置き場を宣言していない
だけ — 片方の欠落をもう片方の error にすると、rule を 1 つ off にするために無関係な置き場の
宣言を強いることになる。

宣言が無いこと (`load` が `None`)、宣言はあるが `[issue]` が無いこと (`issue` が `None`) は
どちらも異常ではないが、**その project の置き場は観測されない**。呼び出し側が「未観測」として
表に出す (`observe_candidates` の `candidates: null` + `reason`)。機械作用の可否は
`enabled_rules` が catalog の既定へ倒す。

table を足すのは `TABLE_VALIDATORS` へ entry を 1 つ足すだけで済む (`parse` は表を回すので、
検証の呼び出しを書き足す必要が無い = 足し忘れた table が素通りしない)。
"""

import tomllib
from pathlib import Path

from dispatch_v2 import adapters

DECLARATION_FILENAME = "dispatch-project.toml"

ISSUE_TABLE = "issue"
CL_TABLE = "pr"
RULES_TABLE = "rules"

ISSUE_KEYS = frozenset({"tracker", "repo", "ready_label"})

# `[pr] tracker` は **issue 置き場と同じ値でも別の値でもよい** (別にできるのは issue 置き場が
# 紐づきを引かない tracker のときだけ — `_require_discoverable_links`)。同じ値を明示しただけの
# 宣言まで未知 key で落とさないよう、key 自体は常に残す。書式は v1 と共有しているので
# (本 module の docstring)、こちらの都合で欠けさせない
CL_KEYS = frozenset({"tracker", "repo"})

# issue 置き場として宣言できる tracker。宣言に書けても adapter が無ければ観測できないので、
# ここで落とす。**登録表からの導出**で、宣言側に第 2 の表を持たない — 割れると「宣言は通るが
# adapter が無い」「adapter はあるが宣言が拒まれる」がどちらも起こる (`adapters` module 参照)
SUPPORTED_TRACKERS = adapters.SUPPORTED_TRACKERS

# CL 置き場として宣言できる tracker。**issue 置き場より狭い** — Jira は issue 置き場になれるが
# 変更置き場ではないので、`[pr]` に書けない (書けると、存在しない MR 置き場を観測しに行く)
CL_TRACKERS = adapters.CL_TRACKERS


class DeclarationError(RuntimeError):
    """宣言 config を読めない / 書式が規約に合わない。"""


def declaration_path(ledger_dir):
    """project の宣言 file の path (台帳ディレクトリ直下)。"""
    return Path(ledger_dir) / DECLARATION_FILENAME


def load(ledger_dir, *, known_rule_ids):
    """宣言を読んで検証済みの dict を返す。file が無ければ `None`。

    返す形:

    ```
    {"issue": {"tracker", "repo", "ready_label"} または None,
     "cl":    {"tracker", "repo"} または None,
     "rules": {"<rule id>": True / False},
     "path":  "<読んだ file>"}
    ```

    `[issue]` を省いた宣言は合法だが置き場は観測されない (`issue` / `cl` が `None`)。
    `[pr]` を省くと CL 置き場は issue 置き場をそのまま継ぐ — ただし**継げるのは変更置き場に
    なれる tracker だけ**で、Jira 置き場の project は `[pr]` が必須になる。**空の table は
    error** — 識別子を落とすと CLI の cwd 推論へ倒れ、別 repo の CL を黙って観測する。
    """
    path = declaration_path(ledger_dir)
    if not path.exists():
        return None
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise DeclarationError(f"{path} を読めない: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise DeclarationError(f"{path} が TOML として読めない: {exc}") from exc
    return {
        **parse(raw, source=str(path), known_rule_ids=known_rule_ids),
        "path": str(path),
    }


def parse(raw, *, source, known_rule_ids):
    """読み込んだ TOML の中身を検証して中立形へ均す (file I/O を持たない)。"""
    _require_known_tables(raw, source=source)
    for table, validate in TABLE_VALIDATORS.items():
        validate(raw, source=source, known_rule_ids=known_rule_ids)
    issue = raw.get(ISSUE_TABLE)
    if issue is None:
        # 置き場を宣言していない project。`[rules]` だけの宣言はここで通り、観測側が
        # 「置き場が無いので観測しない」を理由付きで返す
        return {"issue": None, "cl": None, "rules": dict(raw.get(RULES_TABLE, {}))}
    issue_tracker = _require_tracker(
        issue.get("tracker"), table=ISSUE_TABLE, allowed=SUPPORTED_TRACKERS, source=source
    )
    issue_repo = _require_repo(issue.get("repo"), table=ISSUE_TABLE, source=source)
    cl = raw.get(CL_TABLE)
    if cl is None:
        cl_tracker, cl_repo = _require_cl_host(issue_tracker, source=source), issue_repo
    else:
        cl_repo = _require_repo(cl.get("repo"), table=CL_TABLE, source=source)
        cl_tracker = (
            _require_tracker(
                cl["tracker"], table=CL_TABLE, allowed=CL_TRACKERS, source=source
            )
            if "tracker" in cl
            else _require_cl_host(issue_tracker, source=source)
        )
    _require_discoverable_links(issue_tracker, cl_tracker, source=source)
    return {
        "issue": {
            "tracker": issue_tracker,
            "repo": issue_repo,
            # 候補プールを表す label。**server は既定を配らない** — 配ると「その label を
            # 使う運用」を plugin が押し付けることになる (ADR 0026 の趣旨)。未宣言なら絞らない
            "ready_label": issue.get("ready_label"),
        },
        "cl": {"tracker": cl_tracker, "repo": cl_repo},
        "rules": dict(raw.get(RULES_TABLE, {})),
    }


def enabled_rules(declaration, catalog):
    """catalog の既定に宣言を重ねた「今有効な rule id」の集合。

    宣言が無い project (`load` が `None`) は catalog の既定がそのまま効く。
    """
    declared = (declaration or {}).get(RULES_TABLE, {})
    return {rule.rule_id for rule in catalog if declared.get(rule.rule_id, rule.default_enabled)}


def _require_known_tables(raw, *, source):
    unknown = sorted(set(raw) - set(TABLE_VALIDATORS))
    if unknown:
        raise DeclarationError(
            f"{source}: 未知の table {unknown} (書けるのは {', '.join(sorted(TABLE_VALIDATORS))})"
        )


def _require_issue_table(raw, *, source, known_rule_ids):
    """`[issue]` = 観測する issue 置き場。省いてよい (その project の置き場は観測されない)。"""
    table = raw.get(ISSUE_TABLE)
    if table is None:
        return
    if not table:
        raise DeclarationError(
            f"{source}: [{ISSUE_TABLE}] が空。観測しないなら table ごと省く "
            "(空の table は識別子を落とし、CLI の cwd 推論へ倒れる)"
        )
    _require_known_keys(table, ISSUE_KEYS, table=ISSUE_TABLE, source=source)


def _require_cl_table(raw, *, source, known_rule_ids):
    """`[pr]` = CL 置き場。issue 置き場と同じなら table ごと省く。"""
    table = raw.get(CL_TABLE)
    if table is None:
        return
    if not table:
        raise DeclarationError(
            f"{source}: [{CL_TABLE}] が空。CL 置き場が issue 置き場と同じなら table ごと省く "
            "(空の table は識別子を落とし、CLI の cwd 推論へ倒れる)"
        )
    _require_known_keys(table, CL_KEYS, table=CL_TABLE, source=source)


def _require_rules_table(raw, *, source, known_rule_ids):
    """`[rules]` = rule id → true / false。id の正本は catalog なので外から受け取る。"""
    rules = raw.get(RULES_TABLE, {})
    if not isinstance(rules, dict):
        raise DeclarationError(f"{source}: [{RULES_TABLE}] は table でなければならない")
    unknown = sorted(set(rules) - set(known_rule_ids))
    if unknown:
        raise DeclarationError(
            f"{source}: [{RULES_TABLE}] に未知の rule {unknown} "
            f"(catalog: {', '.join(sorted(known_rule_ids))})"
        )
    not_boolean = sorted(key for key, value in rules.items() if not isinstance(value, bool))
    if not_boolean:
        raise DeclarationError(
            f"{source}: [{RULES_TABLE}] の {not_boolean} は true / false で書く"
        )


def _require_known_keys(contents, allowed, *, table, source):
    unknown = sorted(set(contents) - set(allowed))
    if unknown:
        raise DeclarationError(
            f"{source}: [{table}] の未知の key {unknown} "
            f"(v2 が読むのは {', '.join(sorted(allowed))})"
        )


def _require_tracker(value, *, table, allowed, source):
    """その table に書ける tracker であることを検証して返す。

    **合法値は table ごとに違う** — issue 置き場になれる tracker (`SUPPORTED_TRACKERS`) の
    ほうが CL 置き場になれる tracker (`CL_TRACKERS`) より広い。文面が並べる候補も table ごとに
    変わるので、集合を引数で受ける (片方の候補をもう片方の error に並べると、書けるはずの
    tracker が「書けない」と読まれる)。
    """
    if value not in allowed:
        raise DeclarationError(
            f"{source}: [{table}] tracker = {value!r} は観測できない "
            f"(adapter があるのは {', '.join(allowed)})"
        )
    return value


def _require_cl_host(tracker, *, source):
    """CL 置き場を継ぐ tracker が変更置き場になれることを要求する。

    `[pr]` を省くと CL 置き場は issue 置き場を継ぐが、**継げるのは変更置き場になれる tracker
    だけ**。Jira は issue 置き場になれても変更置き場ではない (`refs.CL_PATTERNS` に jira が
    無い) ので、Jira 置き場の project は `[pr]` の宣言が必須になる。

    黙って継がせると、存在しない Jira の MR 置き場を観測しに行く adapter を組もうとして
    tick が落ちる。**宣言の時点で、何を書けば直るかを名指しで返す**。
    """
    if tracker in CL_TRACKERS:
        return tracker
    raise DeclarationError(
        f"{source}: [{ISSUE_TABLE}] tracker = {tracker!r} は変更置き場ではないので "
        f"CL 置き場を継げない ([{CL_TABLE}] に tracker と repo を宣言する。"
        f"CL 置き場になれるのは {', '.join(CL_TRACKERS)})"
    )


def _require_discoverable_links(issue_tracker, cl_tracker, *, source):
    """issue 置き場と CL 置き場の組合せで、紐づきが供給されることを要求する。

    紐づき (issue → CL) の源は 2 つあり、**issue 置き場の tracker がどちらかを決める**:

    - **tracker から引く** (gh / glab) — その adapter は `CLLinkPort` を実装し、closing
      reference から CL を列挙する。ただし**自分の tracker の CL ref しか組めない** (gh なら
      `gh!N` だけ) ので、別 tracker の CL 置き場を宣言すると紐づきが 1 件も現れない
    - **台帳から引く** (jira) — その adapter は `CLLinkPort` を実装せず、`wo_record_cl` で
      記録した紐づきが源になる (ADR 0040 決定 1 / ADR 0059)。CL ref は記録した綴りのまま
      なので、**別 tracker の CL 置き場でも紐づく**

    落とすのは前者で cross-tracker を宣言した場合だけ。**通すと片方向は沈黙のまま完了済みの
    作業を terminal で誤分類する**: issue = gh / CL = glab では、gh 側の紐づき観測が「観測して
    0 件」(`[]`) を返すため、GitLab の MR が merge されて閉じた issue が `abandoned` へ落ちる。
    terminal は不変なので取り返しがつかない。

    `[pr] tracker` の key を残す理由は `CL_KEYS` の定義に置いてある。
    """
    if issue_tracker == cl_tracker or issue_tracker not in adapters.CL_LINK_TRACKERS:
        return
    raise DeclarationError(
        f"{source}: [{CL_TABLE}] tracker = {cl_tracker!r} は [{ISSUE_TABLE}] の "
        f"{issue_tracker!r} と違う置き場を指しているが、{issue_tracker} は自分の tracker の "
        "CL ref しか組めないので、その置き場の CL は紐づきとして 1 件も現れない。通すと "
        "merge 済みの CL が 1 件も見つからず、完了した issue が abandoned で terminal に落ちる"
    )


def _require_repo(value, *, table, source):
    if not isinstance(value, str) or not value.strip():
        raise DeclarationError(f"{source}: [{table}] repo が空 (置き場の識別子は必須)")
    return value.strip()


# table 名 → その table の検証。**table を足すのは entry 1 行**で、`parse` は表を回すだけ
# (検証の呼び出しを本文へ書き足す形にすると、足し忘れた table が素通りする)
TABLE_VALIDATORS = {
    ISSUE_TABLE: _require_issue_table,
    CL_TABLE: _require_cl_table,
    RULES_TABLE: _require_rules_table,
}
