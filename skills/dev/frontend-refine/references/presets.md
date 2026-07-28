# Presets カタログ

frontend-refine の Prep phase で Claude が借り物 preset を選ぶための一次リファレンス。すべて MIT/ISC/Apache 2.0 で商用利用可。

## Color palette

### Radix Colors (default for Vanilla stack)

- URL: https://www.radix-ui.com/colors
- 特徴: 12 step × 16 色 + alpha、step 1-2 background / 3-5 component bg / 6-8 border / 9-10 solid / 11-12 text の意味付け、自動 dark mode
- License: MIT (WorkOS)
- Install: `<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@radix-ui/colors@3.0.0/blue.css">` or `npm install @radix-ui/colors`
- 使い方: `var(--blue-9)` (solid), `var(--blue-12)` (text)

### Tailwind CSS v4 default palette (default for React + Tailwind)

- URL: https://tailwindcss.com/docs/colors
- 特徴: OKLCH 化、22 色 × 11 shade (50-950)、gray に Slate/Gray/Zinc/Neutral/Stone の 5 色相
- License: MIT
- Install: `@import "tailwindcss";` + `@theme { --color-*: ... }`
- 使い方: `bg-blue-500`, `text-slate-900`

### shadcn/ui default theme (default for shadcn stack)

- URL: https://ui.shadcn.com/themes
- 特徴: Semantic CSS variables (`--background` / `--foreground` / `--primary` / `--muted` / `--accent` / `--destructive` / `--border` / `--ring` の 8 種)
- License: MIT
- Install: `npx shadcn-ui@latest init`

### Open Props (alternative: full token system が欲しいとき)

- URL: https://open-props.style/
- 特徴: 300+ token 全部入り (color / font / size / gradient / shadow / animation 統合)。Color は `--{color}-0` (最も明るい) 〜 `--{color}-12` (最も濃い) の 13 step。CDN 一発導入で dark mode 判定は自前実装が必要 (Radix Colors のような auto dark mode 機構は無い)
- License: MIT
- Install: `@import "https://unpkg.com/open-props";` or `npm install open-props` → `@import "open-props/style.min.css";`
- 使い方: `background: var(--blue-2); color: var(--blue-12);`

### Material 3 Theme Builder (default for Material Design 系)

- URL: https://m3.material.io/theme-builder (実体は https://material-foundation.github.io/material-theme-builder/)
- 特徴: Baseline color から Dynamic Color (Material You) 準拠のフルパレットを生成。UI 上で Export → "Web (CSS)" を選ぶと `--md-sys-color-*` 命名の CSS custom properties 一式 (light/dark 両対応) をダウンロードできる
- License: Apache 2.0
- Install: Web UI (https://material-foundation.github.io/material-theme-builder/) で配色を確定 → Export → Web (CSS) → ダウンロードした CSS を `@import`
- 使い方: `background: var(--md-sys-color-primary); color: var(--md-sys-color-on-primary);`

## Font stack

### System font canonical (recommended default)

```
font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
             "Segoe UI Variable", Roboto, "Helvetica Neue", sans-serif,
             "Apple Color Emoji", "Segoe UI Emoji";
```

外部フォント load なしでパフォーマンスと FOUT リスクを回避できるため、frontend-refine の Prep では default とする。

### modernfontstacks.com — 15 分類

用途別に system font stack を切り替えたい場合は以下から選ぶ (System UI 以外は非 system font を含むため FOUT リスクとのトレードオフに注意):

- System UI (上記)
- Geometric Humanist
- Classical Humanist
- Neo-Grotesque
- Monospace Code
- Slab Serif
- (残り 9 分類は https://modernfontstacks.com/ を参照)

## Icon set

### Lucide (default for React)

- URL: https://lucide.dev/
- 特徴: 318+ icons、24px base、stroke-width 1.5、tree-shakable
- License: ISC (MIT 互換)
- Install: `npm install lucide-react`

### Heroicons (default for Vanilla HTML)

- URL: https://heroicons.com/
- 特徴: 292 icons、3 variants (24 outline / 20 solid / 16 mini)、Tailwind Labs 公式
- License: MIT
- Install: SVG inline (単発) or `npm install @heroicons/react`

### Radix Icons (default for Radix Primitives 併用時)

- URL: https://www.radix-ui.com/icons
- 特徴: 318+ icons、15×15px 固定グリッド、Radix Primitives とのデザイン言語統一が前提
- License: MIT
- Install: `npm install @radix-ui/react-icons`

### Phosphor (default for weight/variant を選び分けたいとき)

- URL: https://phosphoricons.com/
- 特徴: 7000+ icons、6 weight (Thin/Light/Regular/Bold/Fill/Duotone) から選択可能
- License: MIT
- Install: `npm install @phosphor-icons/react`

## 推奨組み合わせ 3 パターン

### Minimal (LP / prototype / demo, 3 分セットアップ)

- Palette: Radix Colors (CDN import)
- Type scale: 1.25
- Font: System font canonical
- Icon: Heroicons (SVG inline)

### Full-featured (React + 少し規模ある app, 5 分)

- Palette: Radix Colors via Tailwind `@theme`
- Type scale: 1.25 (mobile では 1.20)
- Font: System font canonical
- Icon: Lucide
- Stack: Tailwind CSS v4 + Radix Primitives

### shadcn (Next.js + copy-paste, 2 分)

- Palette: shadcn/ui default theme
- Type scale: 1.25
- Font: System font canonical
- Icon: Lucide
- Stack: shadcn/ui + Tailwind v4
