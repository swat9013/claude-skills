---
name: frontend-refine
user-invocable: true
argument-hint: "[stack] [target-path]"
description: HTML/フロントエンドを作成・改善するとき、デザインシステムの規範 (トークン 3 層 / Refactoring UI tactics / WCAG AA / Rams・Nielsen heuristics) に照らして、Prep (骨格トークン生成) → Build (tactics 参照) → Review (51 項目 self-review + 静的検査) の 3 フェーズで洗練を担保する。Use when「LP を作って」「この画面を洗練させて」「HTML デザインをレビューして」「デザイントークンを引き当てて」「Refactoring UI 準拠で見直して」「デザインシステム的にレビューして」。
---

# frontend-refine

## Overview

HTML/フロントエンドを作成・改善するとき、デザインシステムの規範を借りて洗練させる skill。

- **Prep** — 骨格の確定 (stack / palette / type scale / spacing / font / icon / semantic tokens)。成果物: `.ai/design/design-tokens.css` + `.ai/design/design-decisions.md`
- **Build** — 実装は user or 他 skill が担当。frontend-refine は `references/tactics.md` を提供して tactics 適用と Component state 網羅を規範化
- **Review** — 完成物に 14 rule static check + 37 項目主観 checklist を適用、`.ai/design/design-review.md` 出力 + stdout に Red Flag サマリ

すべての成果物は `${PWD}/.ai/design/` に出力 (gitignore 済み前提)。

## When to use

- User が「LP を作って」「この画面を洗練させて」「HTML デザインをレビューして」等 HTML/フロントエンド作成・改善を依頼
- User が「デザイントークンを引き当てて」「Refactoring UI 準拠で見直して」「デザインシステム的にレビューして」等 skill 名を直接 or 目的を明示
- 他 skill (`knowledge/single-file-html` 等) が実装前に Prep tokens 生成 / 実装後に Review 実行のため参照

## When NOT to use

- 組織横断のデザインシステムをゼロから構築するプロセス — 別スコープ (`.ai/research/2026-07-09-174127-design-system-fundamentals.md` を参照)
- 高レベル architecture 設計 — `third/design` (High-Level Design) を使う
- HTML/CSS の syntax verify や browser 表示検証 — `knowledge/single-file-html` の verify (Tier 1/2) を使う

## Phase 1: Prep

### Stack auto-detect

`$PWD` を検査して stack を推定:

| 検出信号 | 判定 |
|---------|------|
| `package.json` に `next` / `react` + `tailwindcss` v4 | React + Tailwind v4 |
| `package.json` に `next` / `react` (Tailwind なし) | React + CSS Modules |
| `package.json` なし、既存 `.html` あり or 依頼が「LP / HTML」明示 | Vanilla HTML + CDN |
| `components.json` (shadcn の) 存在 | shadcn/ui |

### Default 選定

| 項目 | Vanilla | React + Tailwind | shadcn |
|------|---------|-----------------|--------|
| Palette | Radix Colors (CDN) | Radix Colors via `@theme` | shadcn default (Neutral) |
| Type scale | 1.25 | 1.25 | 1.25 |
| Base font | 16px | 16px | 16px |
| Font stack | System font | System font | System font |
| Spacing | 8pt (scale-templates/spacing-8pt.css) | Tailwind default (8pt 一致) | Tailwind default |
| Icon | Heroicons (SVG inline) | Lucide | Lucide |
| Semantic tokens | shadcn 8 種 (`--background`/`--foreground`/`--primary`/`--muted`/`--accent`/`--destructive`/`--border`/`--ring`) | 同 | shadcn 提供のを流用 |

Palette hue は User 依頼のトーン (calm / trustworthy → Blue、warm → Amber、fresh → Green、bold → Red 等) から選定。迷ったら Blue。

### 成果物生成

1. `references/scale-templates/type-scale-1.25.css` (or 1.20 / 1.333)、`spacing-8pt.css`、`layout-12col.css` を `cat` で連結
2. 先頭に Radix Colors CDN の `@import` を追加 (Vanilla) or Tailwind `@theme` block に変換 (React + Tailwind)
3. Semantic tokens block (`--background = var(--blue-2)` 等の 8 種) を追加
4. `${PWD}/.ai/design/design-tokens.css` として保存
5. `${PWD}/.ai/design/design-decisions.md` に選定内容と理由の表を書く

### 提示ステップ

Prep 完了後、stdout に **一度だけ**:

```
[frontend-refine Prep 確定]
Stack: <detected stack>
Palette: Radix <hue>
Type scale: 1.25 (Major Third)
Font: System font stack
Icon: <selected icon set>
Semantic tokens: shadcn 8 種

成果物: .ai/design/design-tokens.css + .ai/design/design-decisions.md
変更希望あれば教えて (「Palette を Green で」等)、なければ Build に進みます。
```

User override があれば該当項目のみ差し替えて再度提示。

## Phase 2: Build

frontend-refine は **実装を担当しない**。Build は user or 他 skill が担当。frontend-refine は「参照される規範」の立場。

### Build 時の Claude の行動 4 項目

1. 先に `${PWD}/.ai/design/design-tokens.css` を実装ファイルに import / paste する
2. 実装中は `references/tactics.md` の [S] tactic のみ能動 self-check (36 tactics 中 25 個)
3. Component 実装時は下記 state 網羅表を確認
4. 実装が終わったら Phase 3 (Review) を起動

### Component state 網羅表

| Component | 必須 state |
|-----------|-----------|
| Button | default / hover / focus-visible / active / disabled / loading |
| Input | default / hover / focus / error / disabled / read-only |
| Link | default / hover / focus-visible / visited |
| Checkbox / Radio | default / hover / focus-visible / checked / indeterminate / disabled |
| Modal | closed / opening / open / closing |
| Alert | info / success / warning / error |

## Phase 3: Review

### 処理順

1. `uv run ${CLAUDE_SKILL_DIR}/scripts/review-static.py <target-html> [<target-css>]` を実行 → JSON on stdout
2. `references/checklist.md` を Read
3. 51 項目のうち Claude 主観 37 項目を Y/N 判定
4. script 出力 + 主観判定を統合して `${PWD}/.ai/design/design-review.md` に書き出し
5. stdout に Red Flag 件数と優先修正 3 件までのサマリ

### 判定閾値

- Red Flag = 0 & Green Light ≥ 40/51 → 「洗練済み」
- Red Flag > 0 → 「未達」(Red Flag が最優先修正)
- Green Light < 40 → 「洗練不足」(Rams/Nielsen 主観項目を見直す推奨)

### stdout サマリ形式

```
[frontend-refine Review]
Red Flag: <N> 件
  R1. <file>:<line> - <rule 概要>
  R2. ...
Green Light: <M>/51 (<pct>%)
洗練度判定: <verdict>
詳細: .ai/design/design-review.md
```

## 呼び出し規約

### パターン 1: 明示 flow (user 依頼)

user 依頼 → frontend-refine が Model-invoke → Prep 実行 → 提示 → Claude が実装 (or 他 skill 呼ぶ) → Review 実行

### パターン 2: 他 skill から参照

他 skill (single-file-html 等) の SKILL.md 内で「デザイントークンは frontend-refine の Prep phase を先に走らせて確定」の指示がある場合、他 skill の workflow に frontend-refine の Prep phase を組み込む。

## 参考文書

- references/: `presets.md`, `tactics.md`, `checklist.md`, `scale-templates/`

規範の出典 (Refactoring UI tactics / WCAG AA / Rams・Nielsen heuristics) は `references/tactics.md` と `references/checklist.md` の各項目に併記してある。
