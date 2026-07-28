#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "beautifulsoup4>=4.12",
#     "tinycss2>=1.3",
# ]
# ///
"""frontend-refine Review phase — 14 rule 静的検査。

Usage:
    review-static.py <html_path> [<css_path>...]

Output:
    JSON on stdout (schema: findings + summary)

Exit codes:
    0 — 検査完了 (findings 有無に関わらず)
    2 — 入力エラー (ファイル不在 / parse fail)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
import tinycss2


CHECKED_RULES = 14  # E1-E5 (5) + F1-F3 (3) + G1-G6 (6)


@dataclass
class Finding:
    id: str
    category: str  # "WCAG" | "Refactoring UI" | "Do-Not"
    severity: str  # "red_flag"
    rule: str
    location: str  # e.g., "index.html:42"
    detail: str
    fix_hint: str


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="frontend-refine static review")
    parser.add_argument("html", type=Path, help="HTML file path")
    parser.add_argument("css", type=Path, nargs="*", help="CSS file paths (optional; extracted from HTML if omitted)")
    return parser.parse_args(argv)


def load_html(path: Path) -> BeautifulSoup:
    return BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")


def load_css_rules(css_paths: list[Path], html: BeautifulSoup, html_path: Path) -> list[tuple[Path, list[Any]]]:
    """Return list of (path, tinycss2 rules) tuples."""
    results: list[tuple[Path, list[Any]]] = []
    for path in css_paths:
        rules = tinycss2.parse_stylesheet(
            path.read_text(encoding="utf-8"), skip_whitespace=True, skip_comments=True
        )
        results.append((path, rules))
    for style_tag in html.find_all("style"):
        css_text = style_tag.get_text()
        rules = tinycss2.parse_stylesheet(css_text, skip_whitespace=True, skip_comments=True)
        results.append((html_path, rules))
    return results


def parse_color(css_value: str) -> tuple[int, int, int] | None:
    """CSS color value を (R, G, B) 0-255 に。#rgb, #rrggbb, rgb()/rgba(), named の一部に対応。"""
    v = css_value.strip().lower()
    if v.startswith("#"):
        h = v[1:]
        if len(h) == 3:
            return (int(h[0]*2, 16), int(h[1]*2, 16), int(h[2]*2, 16))
        if len(h) == 6:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    if v.startswith("rgb"):
        import re
        m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", v)
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    named = {"black": (0,0,0), "white": (255,255,255), "red": (255,0,0), "gray": (128,128,128)}
    return named.get(v)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: float) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def wcag_contrast_ratio(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _extract_declarations(rules: list[Any]) -> list[tuple[str, dict[str, str]]]:
    """tinycss2 の QualifiedRule から (selector, {prop: value}) を抽出。"""
    out = []
    for rule in rules:
        if rule.type != "qualified-rule":
            continue
        selector = tinycss2.serialize(rule.prelude).strip()
        decls = tinycss2.parse_declaration_list(rule.content, skip_whitespace=True, skip_comments=True)
        props: dict[str, str] = {}
        for d in decls:
            if d.type == "declaration":
                props[d.lower_name] = tinycss2.serialize(d.value).strip()
        out.append((selector, props))
    return out


def check_E1_text_contrast(html: BeautifulSoup, css: list[tuple[Path, list[Any]]], html_path: Path) -> list[Finding]:
    """body の bg と各 selector の color を突き合わせて text contrast < 4.5 を検出。

    シンプル化: :root で bg 定義があればそれを basis、なければ body の bg or #ffffff を basis とする。
    """
    findings: list[Finding] = []
    bg_rgb = (255, 255, 255)  # default
    color_rules: list[tuple[Path, str, str]] = []  # (path, selector, color)

    for path, rules in css:
        for selector, props in _extract_declarations(rules):
            if selector in {"body", ":root"} and "background" in props:
                c = parse_color(props["background"])
                if c:
                    bg_rgb = c
            if selector in {"body", ":root"} and "background-color" in props:
                c = parse_color(props["background-color"])
                if c:
                    bg_rgb = c
            if "color" in props:
                color_rules.append((path, selector, props["color"]))

    for path, selector, color in color_rules:
        fg = parse_color(color)
        if not fg:
            continue
        ratio = wcag_contrast_ratio(fg, bg_rgb)
        if ratio < 4.5:
            findings.append(Finding(
                id="E1",
                category="WCAG",
                severity="red_flag",
                rule="text contrast >= 4.5:1 (WCAG 2.1 AA 1.4.3)",
                location=f"{path.name}:{selector}",
                detail=f"color: {color} on background: rgb{bg_rgb}, ratio {ratio:.2f}:1",
                fix_hint="text は Semantic token (--foreground / --muted-foreground) 経由の高コントラスト色に変更",
            ))
    return findings


def check_E2_ui_contrast(html: BeautifulSoup, css: list[tuple[Path, list[Any]]], html_path: Path) -> list[Finding]:
    """button / input / .btn / .card 等の UI component の border color と bg の contrast < 3 を検出。"""
    findings: list[Finding] = []
    ui_selectors = {"button", "input", ".btn", ".button", ".card", "[role='button']"}

    for path, rules in css:
        for selector, props in _extract_declarations(rules):
            if not any(sel in selector for sel in ui_selectors):
                continue
            bg_value = props.get("background") or props.get("background-color")
            border_value = props.get("border")
            if not (bg_value and border_value):
                continue
            bg = parse_color(bg_value)
            # border shorthand から color 抽出 (簡易)
            border_color = None
            for token in border_value.split():
                c = parse_color(token)
                if c:
                    border_color = c
                    break
            if not (bg and border_color):
                continue
            ratio = wcag_contrast_ratio(border_color, bg)
            if ratio < 3.0:
                findings.append(Finding(
                    id="E2",
                    category="WCAG",
                    severity="red_flag",
                    rule="UI component contrast >= 3:1 (WCAG 2.1 AA 1.4.11)",
                    location=f"{path.name}:{selector}",
                    detail=f"border rgb{border_color} on bg rgb{bg}, ratio {ratio:.2f}:1",
                    fix_hint="border 色を Semantic token (--border) 経由、または bg との contrast 3:1 以上に",
                ))
    return findings


def check_E3_heading_order(html: BeautifulSoup, html_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    headings = html.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
    h1_count = sum(1 for h in headings if h.name == "h1")
    if h1_count > 1:
        findings.append(Finding(
            id="E3",
            category="WCAG",
            severity="red_flag",
            rule="H1 は 1 page 1 個 (WCAG 2.1 AA 1.3.1)",
            location=f"{html_path.name}",
            detail=f"H1 が {h1_count} 個検出された",
            fix_hint="page の主 heading は 1 つに絞り、他は H2 以降に変更",
        ))
    prev_level = 0
    for h in headings:
        level = int(h.name[1])
        if prev_level and level > prev_level + 1:
            findings.append(Finding(
                id="E3",
                category="WCAG",
                severity="red_flag",
                rule="heading level を飛ばさない (WCAG 2.1 AA 1.3.1)",
                location=f"{html_path.name}:<{h.name}>",
                detail=f"H{prev_level} の次に H{level} が来ている (H{prev_level+1} を挟むべき)",
                fix_hint=f"間に H{prev_level+1} を追加、または本 heading を H{prev_level+1} に変更",
            ))
        prev_level = level
    return findings


def check_E4_label(html: BeautifulSoup, html_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for inp in html.find_all("input"):
        input_type = inp.get("type", "text")
        if input_type in {"hidden", "submit", "button", "image", "reset"}:
            continue
        input_id = inp.get("id")
        aria_label = inp.get("aria-label")
        aria_labelledby = inp.get("aria-labelledby")
        wrapping_label = inp.find_parent("label")
        has_label_for = input_id and html.find("label", attrs={"for": input_id})
        if not (has_label_for or wrapping_label or aria_label or aria_labelledby):
            findings.append(Finding(
                id="E4",
                category="WCAG",
                severity="red_flag",
                rule="form input には <label for> or aria-label が必要 (WCAG 2.1 AA 3.3.2)",
                location=f"{html_path.name}:<input type='{input_type}'>",
                detail=f"input (id={input_id or 'none'}) に対応する label / aria-label なし。placeholder は代わりにならない",
                fix_hint="<label for='<id>'>...</label> を追加、または aria-label 属性を追加",
            ))
    return findings


def check_E5_alt_text(html: BeautifulSoup, html_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    MEANINGLESS_ALTS = {"", "image", "img", "photo", "picture", "icon"}
    for img in html.find_all("img"):
        if "alt" not in img.attrs:
            findings.append(Finding(
                id="E5",
                category="WCAG",
                severity="red_flag",
                rule="<img> に alt 属性が必要 (WCAG 2.1 AA 1.1.1)",
                location=f"{html_path.name}:<img src='{img.get('src', '?')}'>",
                detail="alt 属性欠落",
                fix_hint="内容を説明する alt='...' を追加。装飾なら alt='' + role='presentation'",
            ))
        elif img.get("alt", "").strip().lower() in MEANINGLESS_ALTS:
            # 空 alt は装飾画像なら OK だが、意味のある画像 (aria-hidden なし) では問題
            aria_hidden = img.get("aria-hidden") == "true"
            role_pres = img.get("role") in {"presentation", "none"}
            if not (aria_hidden or role_pres):
                findings.append(Finding(
                    id="E5",
                    category="WCAG",
                    severity="red_flag",
                    rule="意味ある画像に空 or 定型 alt は NG (WCAG 2.1 AA 1.1.1)",
                    location=f"{html_path.name}:<img src='{img.get('src', '?')}'>",
                    detail=f"alt='{img.get('alt', '')}' — 意味のある内容を書くか、装飾なら role='presentation' を明示",
                    fix_hint="画像の内容を説明する alt を書く。装飾なら role='presentation' + alt='' を明示",
                ))
    return findings


VALID_8PT_PX = {0, 4, 8, 12, 16, 20, 24, 32, 40, 48, 56, 64, 80, 96, 128}
# 20/40/56/80 は 4 の倍数として許容 (4pt sub-grid)。20 は spacing-0-5 (4px) の 5 倍として妥当
SPACING_PROPS = {"padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
                 "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
                 "gap", "row-gap", "column-gap"}
GRID_EXEMPT_PROPS = {"letter-spacing", "border-width", "border-radius"}


def _to_px(value: str) -> float | None:
    """CSS 長さを px に。rem は 16x, em は 16x (近似), px はそのまま。"""
    v = value.strip().lower()
    import re
    m = re.match(r"(-?[\d.]+)(px|rem|em)?$", v)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2) or "px"
    if unit == "px":
        return num
    if unit in {"rem", "em"}:
        return num * 16
    return None


def check_F1_8pt_grid(css: list[tuple[Path, list[Any]]], html_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path, rules in css:
        for selector, props in _extract_declarations(rules):
            if selector == ":root":
                continue  # token 定義層は spacing scale の canonical
            for prop, value in props.items():
                if prop in GRID_EXEMPT_PROPS:
                    continue
                if prop not in SPACING_PROPS:
                    continue
                for token in value.split():
                    px = _to_px(token)
                    if px is None:
                        continue
                    if int(px) not in VALID_8PT_PX:
                        findings.append(Finding(
                            id="F1",
                            category="Refactoring UI",
                            severity="red_flag",
                            rule="spacing は 8pt (or 4pt sub-grid) canonical scale から選ぶ",
                            location=f"{path.name}:{selector}",
                            detail=f"{prop}: {value} — {int(px)}px は 8pt scale 外れ",
                            fix_hint="var(--space-*) を使うか、8 の倍数 (4pt sub-grid 除き) に丸める",
                        ))
                        break
    return findings


def _count_top_level_commas(value: str) -> int:
    depth = 0
    count = 0
    for c in value:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            count += 1
    return count


def check_F2_shadow_layers(css: list[tuple[Path, list[Any]]], html_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path, rules in css:
        for selector, props in _extract_declarations(rules):
            if selector == ":root":
                continue  # token 定義は許容
            if "box-shadow" not in props:
                continue
            value = props["box-shadow"]
            # 単一層 = ',' で shadow 定義が区切られていない (inset や var() は除外)
            # var() 展開は解析不能なので skip
            if "var(" in value:
                continue
            # comma を top-level で数える (rgba() 内の comma は除外)
            top_level_commas = _count_top_level_commas(value)
            if top_level_commas == 0:
                findings.append(Finding(
                    id="F2",
                    category="Refactoring UI",
                    severity="red_flag",
                    rule="elevation shadow は 2-3 層で合成 (tight + ambient)",
                    location=f"{path.name}:{selector}",
                    detail=f"box-shadow: {value} — 単一層",
                    fix_hint="2 層目に ambient shadow を追加 (var(--shadow-md) 等を使うのが推奨)",
                ))
    return findings


def check_F3_raw_hex(css: list[tuple[Path, list[Any]]], html_path: Path) -> list[Finding]:
    """:root 以外で hex/rgb 直書き検出。Semantic token 経由してない印。"""
    findings: list[Finding] = []
    COLOR_PROPS = {"color", "background", "background-color", "border", "border-color",
                   "outline", "outline-color", "fill", "stroke"}
    import re
    HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    RGB_RE = re.compile(r"\brgba?\s*\(")

    for path, rules in css:
        for selector, props in _extract_declarations(rules):
            if selector == ":root":
                continue
            for prop, value in props.items():
                if prop not in COLOR_PROPS:
                    continue
                if HEX_RE.search(value) or RGB_RE.search(value):
                    findings.append(Finding(
                        id="F3",
                        category="Refactoring UI",
                        severity="red_flag",
                        rule="色は Semantic token 経由 (:root で --* を定義し var(--*) を使う)",
                        location=f"{path.name}:{selector}",
                        detail=f"{prop}: {value} — 生 hex/rgb 直書き",
                        fix_hint=":root に semantic token を追加 (--primary 等) し、var(--*) で参照",
                    ))
    return findings


def check_G1_pure_black(css: list[tuple[Path, list[Any]]], html_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path, rules in css:
        for selector, props in _extract_declarations(rules):
            if "color" not in props:
                continue
            value = props["color"].strip().lower()
            if value in {"#000", "#000000", "black", "rgb(0,0,0)", "rgb(0, 0, 0)"}:
                findings.append(Finding(
                    id="G1",
                    category="Do-Not",
                    severity="red_flag",
                    rule="pure #000 text は目の疲労を招くため禁止",
                    location=f"{path.name}:{selector}",
                    detail=f"color: {value}",
                    fix_hint="#1a1a1a or #282828 (charcoal) に置換、または var(--foreground) を使う",
                ))
    return findings


def check_G2_outline_none(css: list[tuple[Path, list[Any]]], html_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    outline_none_selectors: list[tuple[Path, str]] = []
    focus_visible_selectors: set[str] = set()

    for path, rules in css:
        for selector, props in _extract_declarations(rules):
            outline = props.get("outline", "").strip().lower()
            if outline in {"none", "0", "0 none"}:
                if ":focus-visible" not in selector:
                    outline_none_selectors.append((path, selector))
            if ":focus-visible" in selector and "outline" in props:
                base = selector.split(":focus-visible")[0].strip() or "*"
                focus_visible_selectors.add(base)

    for path, selector in outline_none_selectors:
        base = selector.split(":focus")[0].split(":hover")[0].strip()
        if base not in focus_visible_selectors and "*" not in focus_visible_selectors:
            findings.append(Finding(
                id="G2",
                category="Do-Not",
                severity="red_flag",
                rule="outline: none 単独禁止 (keyboard user が focus を見失う)",
                location=f"{path.name}:{selector}",
                detail="outline: none がある一方、:focus-visible での再定義が同じ selector 系で見つからない",
                fix_hint=f"{base}:focus-visible {{ outline: 2px solid var(--ring); outline-offset: 2px; }} を追加",
            ))
    return findings


def check_G3_disabled_opacity(css: list[tuple[Path, list[Any]]], html_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path, rules in css:
        for selector, props in _extract_declarations(rules):
            if not ("disabled" in selector.lower() or "[disabled]" in selector):
                continue
            has_opacity = "opacity" in props
            has_cursor = "cursor" in props
            has_bg = "background" in props or "background-color" in props
            if has_opacity and not (has_cursor or has_bg):
                findings.append(Finding(
                    id="G3",
                    category="Do-Not",
                    severity="red_flag",
                    rule="disabled は opacity 単独では NG (cursor: not-allowed + background 変更も必須)",
                    location=f"{path.name}:{selector}",
                    detail=f"opacity: {props['opacity']} のみ、cursor / background の変更なし",
                    fix_hint="cursor: not-allowed; background: var(--muted); を追加",
                ))
    return findings


SHADOW_SPAM_THRESHOLD = 3


def check_G4_shadow_spam(css: list[tuple[Path, list[Any]]], html_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    selectors_with_shadow: list[tuple[Path, str]] = []
    for path, rules in css:
        for selector, props in _extract_declarations(rules):
            if selector == ":root":
                continue
            if "box-shadow" in props and props["box-shadow"].strip().lower() not in {"none", "unset"}:
                selectors_with_shadow.append((path, selector))
    if len(selectors_with_shadow) >= SHADOW_SPAM_THRESHOLD:
        joined = ", ".join(s for _, s in selectors_with_shadow[:5])
        findings.append(Finding(
            id="G4",
            category="Do-Not",
            severity="red_flag",
            rule=f"shadow spam ({SHADOW_SPAM_THRESHOLD} 個以上の要素に box-shadow) 禁止",
            location=f"{selectors_with_shadow[0][0].name}",
            detail=f"box-shadow を持つ selector: {joined} ({len(selectors_with_shadow)} 個)",
            fix_hint="shadow は interactive elements (hover) or elevated content (modal) に限定",
        ))
    return findings


def check_G5_justify(css: list[tuple[Path, list[Any]]], html_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path, rules in css:
        for selector, props in _extract_declarations(rules):
            if props.get("text-align", "").strip().lower() == "justify":
                findings.append(Finding(
                    id="G5",
                    category="Do-Not",
                    severity="red_flag",
                    rule="text-align: justify は word spacing 不規則で読みにくい",
                    location=f"{path.name}:{selector}",
                    detail="text-align: justify",
                    fix_hint="text-align: left (ragged edge) に変更",
                ))
    return findings


def check_G6_non_link_border_bottom(html: BeautifulSoup, css: list[tuple[Path, list[Any]]], html_path: Path) -> list[Finding]:
    """`p`, `span`, `div`, `h*`, `li` などに border-bottom がある場合、link 予約と衝突。

    selector (tag / class / id 問わず) を html に対して実際に select し、マッチした
    要素の tag が NON_LINK_TAGS に含まれ、かつ <a> の内部でない場合に検出する。
    selector 文字列だけを構文解析する方式 (例: `.highlight` を単純に "." split する) では
    class selector が実際にどのタグへ適用されているか判定できないため、BeautifulSoup の
    CSS selector 解決 (`html.select`) を用いる。
    """
    findings: list[Finding] = []
    NON_LINK_TAGS = {"p", "span", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li"}
    for path, rules in css:
        for selector, props in _extract_declarations(rules):
            if "border-bottom" not in props and "border" not in props:
                continue
            bb_value = props.get("border-bottom") or props.get("border", "")
            if bb_value.strip().lower() in {"none", "0", "unset"}:
                continue
            try:
                matched = html.select(selector)
            except Exception:
                matched = []
            flagged_tags: list[str] = []
            for el in matched:
                if el.name == "a" or el.find_parent("a"):
                    continue
                if el.name in NON_LINK_TAGS:
                    flagged_tags.append(el.name)
            if flagged_tags:
                findings.append(Finding(
                    id="G6",
                    category="Do-Not",
                    severity="red_flag",
                    rule="非 link 要素の border-bottom は link 予約と衝突",
                    location=f"{path.name}:{selector}",
                    detail=f"{flagged_tags[0]} に border-bottom あり",
                    fix_hint="下線が必要なら background-image で underline を代替、または要素を <a> に変更",
                ))
    return findings


def run_all_checks(html: BeautifulSoup, css: list[tuple[Path, list[Any]]], html_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_E1_text_contrast(html, css, html_path))
    findings.extend(check_E2_ui_contrast(html, css, html_path))
    findings.extend(check_E3_heading_order(html, html_path))
    findings.extend(check_E4_label(html, html_path))
    findings.extend(check_E5_alt_text(html, html_path))
    findings.extend(check_F1_8pt_grid(css, html_path))
    findings.extend(check_F2_shadow_layers(css, html_path))
    findings.extend(check_F3_raw_hex(css, html_path))
    findings.extend(check_G1_pure_black(css, html_path))
    findings.extend(check_G2_outline_none(css, html_path))
    findings.extend(check_G3_disabled_opacity(css, html_path))
    findings.extend(check_G4_shadow_spam(css, html_path))
    findings.extend(check_G5_justify(css, html_path))
    findings.extend(check_G6_non_link_border_bottom(html, css, html_path))
    return findings


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        if not args.html.exists():
            print(f"error: HTML file not found: {args.html}", file=sys.stderr)
            return 2
        html = load_html(args.html)

        if args.css:
            css_paths = args.css
        else:
            css_paths = [
                args.html.parent / link.get("href")
                for link in html.find_all("link", rel="stylesheet")
                if link.get("href") and not link.get("href").startswith("http")
            ]
            css_paths = [p for p in css_paths if p.exists()]

        css = load_css_rules(css_paths, html, args.html)
        findings = run_all_checks(html, css, args.html)
        output = {
            "target": [str(args.html)] + [str(p) for p in css_paths],
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "findings": [asdict(f) for f in findings],
            "summary": {
                "red_flag_count": sum(1 for f in findings if f.severity == "red_flag"),
                "checked_rules": CHECKED_RULES,
            },
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
