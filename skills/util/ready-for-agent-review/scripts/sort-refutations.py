#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""refuter の返答を候補ごとに振り分け、手順 5 の雛形のヘッダの件数と、置く節を issue ごとに JSON で stdout へ出す。

入力は manifest の JSON file 1 本 (引数で path を渡す):

    {"issues": [<N>, ...],
     "candidates": [{"id": "<候補の識別子>", "issue": <N>, "axis": "1" | "2" | "3",
                     "design_division": <bool。軸 ① で設計 doc の区分を付けた候補だけ true>,
                     "ruled": <bool。軸 ③ を裁定した候補だけ true>,
                     "from_norm_disagreement": <bool。規範どうしの食い違いから軸 ③ に回した候補だけ true>,
                     "reply": "<refuter の返答を逐語で保存した file の path>" | null}]}

`issues` は入力の issue 番号 (整数) の全件で、必須。候補の無い issue も件数 0 で出力に載る。候補の `issue` は `issues` のどれか。
`reply` が null の候補は、反証の結果を得られなかったものとして扱う。理由は任意の `reply_error` (文字列) に書き、
無ければ「subagent が error を返した」と読む。
省いた bool は false と読む。

振り分け (disposition) は次の値のどれか:

- `落とす`: `### 落とした候補` に置く
- `残す`: `### 指摘` に置く
- `未反証`: `### 未反証 — 承認材料にしない` に置く
- `裁定しない形へ替える`: 軸 ③ の候補を、裁定しない形 (深掘り対話へ回す) に替えて `### 指摘` に置く
- `軸 ① へ替える`: その読み方に照らした軸 ① の候補に替え、反証を当て直す (当て直した返答でもう一度この script を通す)
- `区分を替える`: 反証が示した区分 (修正案で直す / 深掘り対話へ回す) の形に替えて `### 指摘` に置く

規則は上から順に当てる:

1. 返答の形が崩れている (file が無い・読めない・`refuted` が true / false で無い・具体物の項が無いか空・
   反証の種類を返す候補 (軸 ③ と設計 doc の区分を付けた軸 ①) の `refuted: true` で反証の種類が雛形の値でない) → 未反証
2. `refuted: false`、または具体物が「なし」(「なし」の直後が句読点・空白・括弧 (閉じ括弧を含む) で続く値を含む) → 残す
3. 軸 ③:
   - 裁定しない候補に「裁定の根拠」か「AFK 境界」 → 未反証 (崩す裁定が無い)
   - 「両立する読み方」 → 規範どうしの食い違いから回した候補なら軸 ① へ替える、ほかは落とす
   - 「裁定の根拠」か「AFK 境界」 → 裁定しない形へ替える (衝突は残っている)
4. 設計 doc の区分を付けた軸 ①: 「適合」→ 落とす、「区分」→ 区分を替える
5. ほか (軸 ②・区分を付けない軸 ①) → 落とす

出力の `counts` は、反証対象 (候補の数)・落とした件数 (落とす)・未反証 (未反証) の件数。`sections` は
置く節の見出しを雛形の順で並べ、候補の無い `### 落とした候補` と `### 未反証 — 承認材料にしない` は省く
(`### 指摘` は 0 件でも残し、呼び出し元が「該当なし」と書く)。

反証の種類の値と節の見出しは SKILL.md 手順 4・5 の雛形の写しで、tests が雛形と突き合わせる。

invariant:

1. 返答の形が崩れた候補は落とさず、未反証にする
2. 具体物が「なし」の `refuted: true` は落とさない
3. 種類は返答の値だけで決め、説明の文から推さない
4. manifest が読めない・形が違う (必須の鍵が無い・axis が 1 / 2 / 3 でない・bool の鍵が bool でない・issue 番号が整数でないか `issues` に無い・
   `reply` が文字列か null でない・`reply_error` が文字列か null でない) ときは exit 1 で何も出さない
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DROP = "落とす"
KEEP = "残す"
UNREFUTED = "未反証"
TO_UNRULED = "裁定しない形へ替える"
TO_AXIS1 = "軸 ① へ替える"
SWITCH_DIVISION = "区分を替える"

SECTION_FINDINGS = "### 指摘"
SECTION_DROPPED = "### 落とした候補"
SECTION_UNREFUTED = "### 未反証 — 承認材料にしない"

KINDS_AXIS3 = {"両立する読み方", "裁定の根拠", "AFK 境界"}
KINDS_DESIGN = {"適合", "区分"}
KEY_LINE = re.compile(r"^\s*(?:[-*]\s+)?[`*]*(refuted|具体物|反証の種類|説明)[`*]*\s*[:：]\s*(.*)$")
DECOR = " \t`*「」\"'"
NO_EVIDENCE = re.compile(r"なし(?:[。、.,:：\s(（」）)]|$)")


BOOL_KEYS = ("design_division", "ruled", "from_norm_disagreement")


class ManifestError(Exception):
    pass


def parse_reply(text: str) -> dict[str, str]:
    """返答の項目 (refuted・具体物・反証の種類・説明) の値。値は次の項目の行までの文を畳んで持つ。"""
    items: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        m = KEY_LINE.match(line)
        if m and m.group(1) not in items:
            current = m.group(1)
            items[current] = [m.group(2)]
        elif current is not None:
            items[current].append(line)
    return {k: "\n".join(v).strip().strip(DECOR) for k, v in items.items()}


def _needs_kind(c: dict) -> bool:
    return c["axis"] == "3" or (c["axis"] == "1" and c["design_division"])


def sort_candidate(c: dict) -> tuple[str, str]:
    """(振り分け, 理由)。"""
    if c["reply"] is None:
        return UNREFUTED, f"反証の結果が無い ({c.get('reply_error') or 'subagent が error を返した'})"
    try:
        reply = parse_reply(Path(c["reply"]).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        return UNREFUTED, f"返答の file を読めない ({e})"
    refuted = reply.get("refuted", "").lower()
    if refuted not in ("true", "false"):
        return UNREFUTED, "refuted が true / false で返らなかった"
    evidence = reply.get("具体物", "")
    if not evidence:
        return UNREFUTED, "具体物の項が返らなかった"
    kind = reply.get("反証の種類", "")
    if refuted == "true" and _needs_kind(c):
        allowed = KINDS_AXIS3 if c["axis"] == "3" else KINDS_DESIGN
        if kind not in allowed:
            return UNREFUTED, f"反証の種類が雛形の値 ({' / '.join(sorted(allowed))}) で返らなかった"
    if refuted == "false" or NO_EVIDENCE.match(evidence):
        return KEEP, "反証されなかった"
    if c["axis"] == "3":
        if kind in ("裁定の根拠", "AFK 境界"):
            if not c["ruled"]:
                return UNREFUTED, f"裁定しない形の候補に「{kind}」が返った (崩す裁定が無い)"
            return TO_UNRULED, f"「{kind}」: 裁定は崩れたが衝突は残る"
        if c["from_norm_disagreement"]:
            return TO_AXIS1, "「両立する読み方」: 規範どうしの食い違いから回した候補"
        return DROP, "「両立する読み方」: 衝突が無い"
    if c["axis"] == "1" and c["design_division"]:
        if kind == "区分":
            return SWITCH_DIVISION, "「区分」: 反証が示した区分へ替える"
        return DROP, "「適合」"
    return DROP, "具体物で反証された"


def load_manifest(path: Path) -> tuple[list, list[dict]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ManifestError(f"manifest を読めない ({path}: {e})") from e
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise ManifestError("manifest に candidates の配列が無い")
    issues = data.get("issues")
    if not isinstance(issues, list) or not all(type(n) is int for n in issues):
        raise ManifestError("manifest の issues が issue 番号 (整数) の配列でない")
    out = []
    for i, c in enumerate(data["candidates"]):
        if not isinstance(c, dict) or not {"id", "issue", "axis", "reply"} <= c.keys():
            raise ManifestError(f"candidates[{i}] に id / issue / axis / reply のどれかが無い")
        if c["axis"] not in ("1", "2", "3"):
            raise ManifestError(f"candidates[{i}] の axis {c['axis']!r} が 1 / 2 / 3 でない")
        for flag in BOOL_KEYS:
            if not isinstance(c.get(flag, False), bool):
                raise ManifestError(f"candidates[{i}] の {flag} {c[flag]!r} が bool でない")
        if not (c["reply"] is None or isinstance(c["reply"], str)) or not isinstance(c.get("reply_error") or "", str):
            raise ManifestError(f"candidates[{i}] の reply が path の文字列か null でないか、reply_error が文字列でない")
        if c["issue"] not in issues or type(c["issue"]) is not int:
            raise ManifestError(f"candidates[{i}] の issue {c['issue']!r} が issues に無い")
        out.append(dict.fromkeys(BOOL_KEYS, False) | c)
    return issues, out


def sort_all(issues: list, candidates: list[dict]) -> dict:
    by_issue: dict[object, list[dict]] = {issue: [] for issue in issues}
    for c in candidates:
        disposition, reason = sort_candidate(c)
        by_issue.setdefault(c["issue"], []).append({"id": c["id"], "disposition": disposition, "reason": reason})
    out = []
    for issue, rows in by_issue.items():
        dropped = sum(r["disposition"] == DROP for r in rows)
        unrefuted = sum(r["disposition"] == UNREFUTED for r in rows)
        sections = [SECTION_FINDINGS]
        if dropped:
            sections.append(SECTION_DROPPED)
        if unrefuted:
            sections.append(SECTION_UNREFUTED)
        out.append({
            "issue": issue,
            "counts": {"反証対象": len(rows), "落とした件数": dropped, "未反証": unrefuted},
            "sections": sections,
            "candidates": rows,
        })
    return {"issues": out}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("manifest", type=Path)
    args = p.parse_args(argv)
    try:
        issues, candidates = load_manifest(args.manifest)
    except ManifestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(sort_all(issues, candidates), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
