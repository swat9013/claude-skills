---
name: frontend-refine
user-invocable: true
argument-hint: "[stack] [target-path]"
description: HTML/フロントエンドの成果物 1 つを作成・改善するとき、デザインシステムの規範 (トークン 3 層 / Refactoring UI tactics / WCAG AA / Rams・Nielsen heuristics) に照らして Prep (骨格トークン生成) → Build (tactics 参照) → Review (51 項目 self-review + 静的検査) の 3 フェーズで洗練させる。Use when「LP を作って」「この画面を洗練させて」「HTML デザインをレビューして」「デザイントークンを引き当てて」。HTML/CSS の syntax verify や browser 表示検証は対象外 (single-file-html の verify を使う)。
---

# frontend-refine

出力先の `${PWD}/.ai/design/` は gitignore 済みである前提で書き出す。

他 skill の SKILL.md が特定 phase (Prep のトークン確定 / Review の検査) を名指しで呼んでいるときは、その phase だけを呼び出し元の workflow に組み込む。

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

上表の default から外れるとき (user が Material 3 / Open Props / 別 icon set / 用途別 font stack を名指し、依頼が特定デザイン言語に寄る等) のみ `references/presets.md` を Read し、URL / License / install 手順 / 使い方をそこから引く。default のままなら Read 不要。

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

Build の実装は user or 他 skill が担当し、**本 skill は Build に規範だけを提供する**。実装役が従うのは次の 4 項目:

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
