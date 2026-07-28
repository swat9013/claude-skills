---
name: single-file-html
user-invocable: false
description: Use when building a self-contained single-file HTML artifact (explainer doc, dashboard, report, graphical page with inline SVG) that must open standalone with zero external dependencies, when asked to create/build a one-file HTML page or embed diagrams as inline SVG, or when rendering and visually checking an HTML file in a browser. Not for converting a spec to readable HTML (use spec-to-readable-html) or exploring several UI variations (use prototype).
---

# Single-File HTML

## Overview

Build one `.html` file that opens standalone — all CSS, JS, and graphics embedded, **no external dependencies** (no CDN, no fonts, no `<img src>`). Then verify it actually renders, not just that the file was written.

Core principle: **the file is the deliverable, and "looks done" is not "verified done."** Always run the two-tier verification before claiming completion.

## When to use / NOT

- **Use** for: graphical explainer docs, dashboards, reports, standalone interactive pages, anything that must work offline by double-clicking.
- **NOT** for: converting a spec/requirements doc to readable HTML -> `spec-to-readable-html`. Exploring multiple toggleable UI mockups -> `prototype`.

## Core principles

1. **Self-contained.** Inline `<style>`, inline `<script>`, inline SVG. No network. If you reach for a CDN, stop — inline it or generate it.
2. **Vanilla.** Plain JS + CSS + SVG. No build step, no framework.
3. **Data-driven rendering.** Define the content as a data array/object, then render from it in one loop. One source of truth; counts and grouping derive from the data. This keeps edits surgical and verification easy.
4. **Escape untrusted text.** Escape (`& < > " '`) any string that isn't an inline literal you authored here — including agent-authored data arrays that external/runtime data might later replace. Pure inline literals you wrote need no escaping.
5. **Accessibility basics.** SVG 固有の規約のみここに置く:
   `role="img"` + `aria-label` when the figure *carries information*;
   `aria-hidden="true"` only when it's decorative and its meaning is duplicated in adjacent text.
   A status shape that is the sole signal is NOT decorative — label it.

   HTML/CSS 全般の A11y (contrast / heading order / label / focus indicator / alt text / keyboard nav) は
   `frontend-refine` skill の Review phase を Build 前後で走らせて担保する。

## Graphical / diagram principles

Earned from real review feedback — apply when adding any figure:

1. **Make the shape carry the meaning.** A figure whose information lives only in text labels is just text in a box. If a label says "value goes up", the geometry must show it going up. Drop labels the shape already conveys.
2. **Illustrate the specific mechanism, not a generic abstraction.** Draw the actual thing being checked/shown (e.g. app->lib arrows for a layering rule), not a reusable abstract chart reused for every item.
3. **Name by what it does/detects**, not by the tool or technique. "Detects deleted batch refs" beats "zeitwerk smoke check". Keep tool names in a secondary line.
4. **Cut redundancy and decoration.** Remove count cards, repeated captions, and anything that adds no information.

For inline-SVG builder patterns (a small `box()/arrow()/svg()` toolkit, staircase/scan/mapping/gauge examples) and the "test pure builders in Node" trick, see `references/svg-diagram-cookbook.md`.

## Authoring workflow

1. **Prep (`frontend-refine` skill の Phase 1)** — `.ai/design/design-tokens.css` と `design-decisions.md` を先に生成する。Vanilla stack で Radix Colors + System font + Heroicons が default。
2. Decide the data model first; write the data array.
3. Write the `<style>` and section skeleton (using `.ai/design/design-tokens.css` の tokens を `<style>` に import or paste).
4. Render from data in one pass. Add diagrams via small builder functions (cookbook).
5. Verify (below). Fix. Re-verify.

## Review (frontend-refine Phase 3)

Tier 1 (syntax) 通過後、Tier 2 (browser) の前に:

`uv run ${CLAUDE_SKILL_DIR}/../../dev/frontend-refine/scripts/review-static.py <file.html>`

Red Flag = 0 が verify 前の必須条件。詳細は `frontend-refine` の SKILL.md 参照。

## Verification (two tiers — both, in order)

**Tier 1 — static (ALWAYS, no browser needed):**

```
node references/verify.mjs <file.html>
```

(Run from the skill directory, or substitute your actual install path for `references/`.) It extracts every inline `<script>`, syntax-checks each, and runs structural sanity checks (DOCTYPE/html/head/body, balanced tags). Fix everything it reports before moving on.

Also unit-test SVG builders in Node **when any SVG uses computed coordinates** (arithmetic, loops, trig) — they are pure string functions, so run them headless and scan output for `NaN`/`undefined`. Skip this for fully static markup. Pattern and example in the cookbook.

**Tier 2 — browser visual:**

Actually render it and look. Three cases, in order of preference:
1. **Playwright MCP available:** render and inspect per `references/browser-verify.md`.
2. **Interactive, no MCP:** ask the user to run `! open <file.html>` and report back.
3. **Non-interactive (e.g. a subagent: no MCP, no user):** do NOT fake it. State that Tier 2 is outstanding and hand back the file path for a browser-capable agent/human to finish. Do not claim visual correctness.

**Never claim "done" / "renders correctly" without Tier 1 passing AND Tier 2 completed via case 1 or 2 (case 3 is an explicit hand-off, not a completion).**

## Common mistakes

| Mistake | Fix |
|---|---|
| "Wrote the file, looks complete" | Not verified. Run both tiers. |
| Pulled in a CDN/font/`<img src>` | Inline or generate it; keep zero deps. |
| Diagram explained only by text labels | Make geometry carry the meaning. |
| Reused one abstract chart for every item | Draw each item's specific mechanism. |
| Carried a stale "can't render" assumption | Re-verify the claim in the current environment (see browser-verify.md). |
| Hand-built data + markup duplicated per item | One data array, one render loop. |
