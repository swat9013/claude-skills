#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""指示文の決定的な検査と、書き直しの前後の記号の差を stdout へ出す。model を呼ばない。

    check-instruction-text.py lint <file>...        曖昧語・強調語と、長い文を挙げる
    check-instruction-text.py diff <前> <後>        片側にだけ出る記号と、個数が変わった語を挙げる

出力は 1 件 1 行の tab 区切り。

- lint: <path>:<行>  曖昧語|強調語  <当たった語>  /  <path>:<行>  文長  <字数>
- diff: <種類>  前だけ|後だけ  <記号>  /  <種類>  <語>  <前の個数>-><後の個数>

lint は frontmatter と code fence の中を見ない。曖昧語と強調語は、inline code と「」で引いた語には当てない (語への言及は指示ではない)。
強調語は、全大文字の `CRITICAL` / `IMPORTANT` / `MUST` / `NEVER` / `ALWAYS` (英字に続かない単語) と「念のため」。
文は行 (表は cell) を「。」で切ったもので、link の先は字数に数えない。
diff の「前だけ・後だけ」は、数値・inline code・link の先・内容語 (2 字以上の漢字・カタカナ・英字の並び) の集合の差。
否定・義務と禁止と許可・条件と例外の語は、出現数が前後で変わったものを挙げる (これらの語は書き直しの後にも
どこかに残るので、有無だけでは落とした否定や条件が見えない)。長い語を先に当て、「なければならない」の中の
「ない」を別に数えない。語は辞書と正規表現で拾い、形態素解析をしないので、活用や言い換えは拾い漏らす。
出力は候補で、合否ではない。

invariant:

1. 入力の file が読めなければ stderr へ 1 行出して exit 1
2. 検査が当たっても exit 0 (当たりを合否にしない)
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

MAX_SENTENCE_CHARS = 100
# 終わったかを見分けられなくする語。足すときは、指示文で具体的な条件に言い換えられる語に限る
VAGUE_WORDS = (
    "適切に", "適切な", "適宜", "必要に応じて", "状況に応じて", "場合によって", "なるべく", "できるだけ",
    "可能な限り", "極力", "十分に", "柔軟に", "臨機応変", "しっかり", "きちんと", "ちゃんと", "丁寧に",
    "うまく", "いい感じ", "基本的に", "慎重に", "徹底",
)
WORD_KINDS = {
    "否定": ("ない", "せず", "無い", "なし"),
    "義務・禁止・許可": ("必ず", "必須", "なければならない", "禁止", "禁じ", "てはならない", "てよい", "できる", "構わない"),
    "条件・例外": ("場合", "とき", "なら", "れば", "限り", "ただし", "除く", "除き", "以外", "例外", "のみ", "だけ"),
}
SYMBOL_PATTERNS = {
    "inline code": re.compile(r"`([^`\n]+)`"),
    "link の先": re.compile(r"\]\(([^)\s]+)\)"),
    "数値": re.compile(r"\d+(?:[.,]\d+)*%?"),
    "内容語": re.compile(r"[一-鿿々]{2,}|[ァ-ヴー]{2,}|[A-Za-z][A-Za-z0-9_-]+"),
}
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
MENTION = re.compile(r"(`+)[^`\n]*?\1|「[^」\n]*」")
KIND_OF_WORD = {word: kind for kind, words in WORD_KINDS.items() for word in words}
COUNTED_WORD = re.compile("|".join(sorted(map(re.escape, KIND_OF_WORD), key=len, reverse=True)))
CELL_BREAK = re.compile(r"(?<!\\)\|")
# 予防的な強調。全大文字の英単語だけを当て、文中の must / Never は拾わない
EMPHATIC_WORD = re.compile(r"(?<![A-Za-z])(?:CRITICAL|IMPORTANT|MUST|NEVER|ALWAYS)(?![A-Za-z])|念のため")


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SystemExit(f"check-instruction-text: {path} を読めない: {exc}") from exc


def _prose_lines(text: str) -> list[tuple[int, str]]:
    """frontmatter と code fence を除いた (行番号, 行)。"""
    lines = text.splitlines()
    out: list[tuple[int, str]] = []
    fence = ""  # 開いた fence の記号。同じ文字で同じ長さ以上の行でだけ閉じる
    in_frontmatter = bool(lines) and lines[0].strip() == "---"
    for number, line in enumerate(lines, start=1):
        if in_frontmatter:
            if number > 1 and line.strip() == "---":
                in_frontmatter = False
            continue
        marker = FENCE.match(line)
        if marker and not fence:
            fence = marker.group(1)
        elif marker and marker.group(1).startswith(fence) and not marker.group(2).strip():
            fence = ""
        elif not fence:
            out.append((number, line))
    return out


def lint(paths: list[str]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        for number, line in _prose_lines(_read(path)):
            stated = MENTION.sub("", line)
            for word in VAGUE_WORDS:
                if word in stated:
                    findings.append(f"{path}:{number}\t曖昧語\t{word}")
            findings += [f"{path}:{number}\t強調語\t{word}" for word in EMPHATIC_WORD.findall(stated)]
            cells = CELL_BREAK.split(line) if line.lstrip().startswith("|") else [line]
            for cell in cells:
                for sentence in LINK.sub(r"\1", cell).split("。"):
                    length = len(sentence.strip())
                    if length > MAX_SENTENCE_CHARS:
                        findings.append(f"{path}:{number}\t文長\t{length}")
    return findings


def diff(before_path: str, after_path: str) -> list[str]:
    before, after = _read(before_path), _read(after_path)
    findings: list[str] = []
    for kind, pattern in SYMBOL_PATTERNS.items():
        found_before, found_after = set(pattern.findall(before)), set(pattern.findall(after))
        findings += [f"{kind}\t前だけ\t{symbol}" for symbol in sorted(found_before - found_after)]
        findings += [f"{kind}\t後だけ\t{symbol}" for symbol in sorted(found_after - found_before)]
    count_before, count_after = Counter(COUNTED_WORD.findall(before)), Counter(COUNTED_WORD.findall(after))
    findings += [
        f"{kind}\t{word}\t{count_before[word]}->{count_after[word]}"
        for word, kind in KIND_OF_WORD.items() if count_before[word] != count_after[word]
    ]
    return findings


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "lint":
        findings = lint(argv[1:])
    elif len(argv) == 3 and argv[0] == "diff":
        findings = diff(argv[1], argv[2])
    else:
        print(__doc__, file=sys.stderr)
        return 1
    if findings:
        print("\n".join(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
