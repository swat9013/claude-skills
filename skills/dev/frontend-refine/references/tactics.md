# Refactoring UI Tactics ([T]/[S] 分類)

各 tactic は次のラベルを持つ:

- **[T] トークン化済み** — Prep phase の `design-tokens.css` で既に強制済み。Build 中に能動的な self-check は不要
- **[S] 画面ごと判断** — Build 中に Claude が能動的に self-check して適用する

Claude が Build phase に入ったら **[S] tactic のみ**を能動的に self-check する。

## Hierarchy

### [S] color × size × weight の 3 軸で hierarchy を作る

- **Why:** weight だけの変化は視覚的差が乏しく、hierarchy が弱くなる。color と size を組み合わせることで階層が一目で伝わる
- **Bad:** 見出しも本文も同じ color、weight だけ 700 vs 400 で差をつける
- **Good:** 見出しは color `#1a202c` weight 600 size 1.5rem、本文は color `#4a5568` weight 400 size 1rem
- **CSS 例:**
  ```css
  h2 { color: #1a202c; font-weight: 600; font-size: 1.5rem; }
  p  { color: #4a5568; font-weight: 400; font-size: 1rem; }
  ```

### [S] 重要度低の要素を目立たなくすることで主役を浮かせる

- **Why:** 主役だけを強調するより、脇役を de-emphasize するほうが相対的なコントラストが生まれ hierarchy が明確になる
- **Bad:** primary button も secondary button も同じ濃さの塗りつぶし
- **Good:** secondary は transparent + border のみ、primary だけ solid fill にする
- **CSS 例:**
  ```css
  .btn-primary   { background: #3b82f6; color: #fff; border: none; }
  .btn-secondary { background: transparent; border: 1px solid #cbd5e0; color: #4a5568; }
  ```

### [T] Font size は modular ratio で段階化。arbitrary サイズ禁止

- **Why:** 都度決め打ちの font-size は一貫性を欠き、hierarchy の飛び幅がバラつく。scale から選ぶことで階層が予測可能になる
- **Bad:** `font-size: 17px;` `font-size: 23px;` のような根拠のない値
- **Good:** 1.25 (Major Third) など modular scale から選択する
- **段階の値:** Prep phase が生成する `scale-templates/type-scale-{1.20,1.25,1.333}.css` が正本。ここに値を再掲しない (再掲すると同じ token 名が 2 つの値を持つ)。実装側は `font-size: var(--text-lg)` の形で参照する

### [T] Font weight 400 未満は禁止

- **Why:** 300 以下の light weight は小さいサイズだと antialiasing が弱く文字が薄く見え、低解像度・低コントラスト環境で可読性が落ちる
- **Bad:** `font-weight: 300;` を本文の説明文などに使う
- **Good:** `font-weight: 400` を最小値とする
- **CSS 例:**
  ```css
  body { font-weight: 400; } /* 300 以下は使用しない */
  ```

### [S] 濃い色背景に gray text を置かない

- **Why:** 固定の gray (`#4a5568` など) は背景の暗さによっては contrast 比が WCAG AA (4.5:1) を割り込む。white ベースに opacity で調整すると背景に応じて自然に馴染みつつコントラストを保てる
- **Bad:** `background: #1a202c; color: #718096;` (gray text はコントラスト不足)
- **Good:** `background: #1a202c; color: rgba(255,255,255,0.7);`
- **CSS 例:**
  ```css
  .dark-panel { background: #1a202c; }
  .dark-panel .muted-text { color: rgba(255, 255, 255, 0.7); }
  ```

## Whitespace

### [S] 要素を縮めるが先、間隔を広げるは後

- **Why:** 余白を広げる前にコンポーネント自体の内部密度を詰めることで、無駄に間延びしたレイアウトを避けられる
- **Bad:** `padding: 32px; margin: 32px;` のように両方に大きい値を積み重ねる (over-spaced)
- **Good:** 要素を tight に詰めてから、必要な間隔だけ広げる
- **CSS 例:**
  ```css
  /* Bad */
  .card { padding: 32px; margin: 32px; }
  /* Good: まず要素を締める、必要な余白だけ足す */
  .card { padding: 16px; margin-block: 24px; }
  ```

### [S] 関連要素を close に、無関係を離す (Gestalt proximity)

- **Why:** 近接の原則 (Gestalt proximity) により、間隔の大小そのものが「これらは関連している」という情報を伝える
- **Bad:** label と input の間隔も、field 同士の間隔も同じ 16px
- **Good:** label-input 間は 4-8px、field 同士は 24px と差をつける
- **CSS 例:**
  ```css
  .field { display: flex; flex-direction: column; gap: 6px; }
  .form  { display: flex; flex-direction: column; gap: 24px; }
  ```

### [T] Line-height は行の長さに反比例

- **Why:** 行が長いほど次の行の先頭を見失いやすいため、行間を広げて視線の移動を助ける必要がある
- **Bad:** すべてのテキストに `line-height: 1.5` を機械的に適用する
- **Good:** short line 1.3 / body 1.5 / long article 1.75 と用途ごとに変える
- **CSS 例:**
  ```css
  .caption { line-height: 1.3; }
  body     { line-height: 1.5; }
  article  { line-height: 1.75; }
  ```

### [T] 大 font ほど tight な line-height / letter-spacing

- **Why:** フォントサイズが大きくなるほど字間・行間の絶対量も比例して大きくなるため、相対値を締めないと間延びして見える
- **Bad:** `h1 { font-size: 3rem; line-height: 1.5; }` のように本文と同じ line-height を使う
- **Good:** `h1 { font-size: 3rem; line-height: 1.1; letter-spacing: -0.02em; }`
- **CSS 例:**
  ```css
  h1 { font-size: 3rem;   line-height: 1.1; letter-spacing: -0.02em; }
  h2 { font-size: 1.5rem; line-height: 1.2; letter-spacing: -0.01em; }
  ```

### [S] Padding は内側、Margin は外側。相殺回避のため gap を優先

- **Why:** margin は隣接要素同士で相殺 (collapse) が起き意図した余白にならないことがある。Flexbox/Grid の gap は相殺が起きず予測可能
- **Bad:** 各子要素に `margin-bottom: 16px;` を個別付与する (最後の要素だけ余分な余白が残る)
- **Good:** 親要素に `display: flex; gap` で余白を統一管理する
- **CSS 例:**
  ```css
  /* Bad */
  .item { margin-bottom: 16px; }
  /* Good */
  .list { display: flex; flex-direction: column; gap: 16px; }
  ```

## Color

### [S] Saturation を下げるより brightness を調整

- **Why:** 彩度を落としただけの gray は死んだ色に見える。わずかに hue を残した cool/warm gray の方が上品で馴染みやすい
- **Bad:** `color: #808080;` (純粋な無彩色 gray)
- **Good:** `color: hsl(210, 10%, 60%);` (わずかに青みがかった cool gray)
- **CSS 例:**
  ```css
  --gray-500: hsl(210, 10%, 60%);
  --gray-700: hsl(210, 12%, 35%);
  ```

### [S] 早期に色を投入。grayscale で完成させてから色を足すと clash する

- **Why:** グレースケールでレイアウトを完成させた後に色を足すと、hierarchy 設計時の前提と衝突し配色が破綻しやすい。色は設計初期から検討する
- **Bad:** モックをすべて grayscale で仕上げてから最後に色を当てはめる
- **Good:** hierarchy 設計と同時に primary/accent color を仮当てして検証する
- **CSS 例:**
  ```css
  /* Prep 段階で仮の accent を当てておく */
  :root { --accent: #3b82f6; }
  ```

### [S] Semantic color は context に依存する

- **Why:** 色の意味づけはドメインごとに慣習が異なる。汎用ガイドライン (red = error) をそのまま適用すると業界特有の期待とズレる (金融では red = loss、game では red = power-up)
- **Bad:** 金融ダッシュボードで慣習を確認せず汎用ガイドラインのまま色を割り当てる
- **Good:** ドメインの慣習を確認した上で semantic token を割り当てる
- **CSS 例:**
  ```css
  /* 金融: red = loss, green = gain (業界慣習に合わせる) */
  --color-loss: #dc2626;
  --color-gain: #16a34a;
  ```

### [S] Overlay で色を作る

- **Why:** 固定色は背景色が変わるたびに個別調整が必要だが、white (または black) に opacity をかけた overlay は背景に自動で馴染み一貫性が保たれる
- **Bad:** `background: #1a202c; color: #4a5568;` のように背景ごとに固定色を再設計する
- **Good:** `background: #1a202c; color: rgba(255,255,255,0.9);`
- **CSS 例:**
  ```css
  .on-dark       { color: rgba(255, 255, 255, 0.9); }
  .on-dark-muted { color: rgba(255, 255, 255, 0.6); }
  ```

### [S] Accent color は small area に限定する

- **Why:** accent color は面積が小さいほど目を引く効果が保たれる。広い面積に使うと視覚的な圧迫感が生まれ、主役が埋もれる
- **Bad:** `background: #7c3aed;` のように section 全体を強い accent 色で塗る
- **Good:** accent は button / icon / badge など小面積のみに使用する
- **CSS 例:**
  ```css
  .cta-button { background: var(--accent); }      /* 小面積 */
  .section    { background: var(--neutral-50); }  /* 大面積は neutral */
  ```

## Typography

### [T] Base 16px。mobile も 16-18px

- **Why:** 16px 未満の base font size はモバイルで読みにくいだけでなく、iOS Safari では input focus 時に意図しない自動ズームを誘発する
- **Bad:** `font-size: 13px;` (本文サイズが小さすぎる)
- **Good:** `font-size: 16px;` を base とする
- **CSS 例:**
  ```css
  html { font-size: 16px; }
  body { font-size: 1rem; } /* 16px */
  ```

### [T] System font stack を default にする

- **Why:** web font の外部読み込みは追加の network round trip を発生させ、読み込み中の FOUT/FOIT や CLS の原因になる。system font は即座に描画され OS ネイティブな質感も得られる
- **Bad:** `@import url('https://fonts.googleapis.com/css2?family=...');` を都度追加する
- **Good:** OS 標準の system font stack を使う
- **CSS 例:**
  ```css
  body {
    font-family: system-ui, -apple-system, BlinkMacSystemFont,
      "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  }
  ```

### [T] Display font と body font を混ぜない

- **Why:** 3 種類以上のフォントファミリーを混在させると読み手に一貫した書体の印象を与えられず、統一感が崩れる
- **Bad:** 見出しに Playfair Display、本文に Roboto、caption に別の serif、と 3 系統以上混在させる
- **Good:** display font は heading のみに限定するか、全体を 1 つの sans-serif に統一する
- **CSS 例:**
  ```css
  h1, h2   { font-family: "Playfair Display", serif; }
  body, p  { font-family: system-ui, sans-serif; } /* 2 系統までに抑える */
  ```

### [S] Line length 45-75 characters

- **Why:** 1 行が長すぎると次の行の先頭を見失いやすく、短すぎると視線移動が頻発して読むリズムが崩れる。45-75 文字が読みやすさの生理学的最適域
- **Bad:** `article { max-width: 100%; }` (画面幅いっぱいにテキストが伸びる)
- **Good:** `article { max-width: 65ch; }`
- **CSS 例:**
  ```css
  article, .prose { max-width: 65ch; }
  ```

### [T] 文中で font family を切り替えない

- **Why:** 文章の途中でフォントファミリーが切り替わると視覚的なノイズになる。ただし数値の桁揃え (tabular figures) は例外として許容される
- **Bad:** 本文中の一部単語だけ別フォントに切り替える
- **Good:** `font-variant-numeric: tabular-nums;` で数字の幅だけ揃える (フォント自体は変えない)
- **CSS 例:**
  ```css
  .price, .table-cell--numeric { font-variant-numeric: tabular-nums; }
  ```

## Depth

### [T] Elevation shadow は 2-3 層で合成する

- **Why:** 単一の shadow は不自然でのっぺりした印象になる。実世界の光は近接光と環境光が合成されるため、tight + ambient の複数層で合成すると自然な浮遊感が出る
- **Bad:** `box-shadow: 0 4px 10px rgba(0,0,0,0.3);` (単層の強い shadow)
- **Good:** tight (`0 1px 2px rgba(0,0,0,0.05)`) + ambient (`0 4px 6px rgba(0,0,0,0.1)`) の 2 層を重ねる
- **CSS 例:**
  ```css
  .card {
    box-shadow:
      0 1px 2px rgba(0, 0, 0, 0.05),
      0 4px 6px rgba(0, 0, 0, 0.1);
  }
  ```

### [T] 光源方向を統一する

- **Why:** 光源の方向が要素ごとにバラバラだと、同一画面内で物理的な整合性が崩れ不自然に見える
- **Bad:** 一部の要素だけ `box-shadow: 0 -4px 6px ...;` のように上向きの shadow を使う
- **Good:** すべての shadow の y-offset を正の値 (下向き) に統一する
- **CSS 例:**
  ```css
  /* すべて y-offset は正の値で統一 */
  .card  { box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); }
  .modal { box-shadow: 0 10px 20px rgba(0, 0, 0, 0.15); }
  ```

### [S] Inset shadow と outer shadow を用途で使い分ける

- **Why:** inset (内側) shadow は「へこんでいる」印象を与え、outer shadow は「浮いている」印象を与える。要素の意味 (入力欄 vs カード) に合わせて使い分けないと知覚と実態が矛盾する
- **Bad:** `input { box-shadow: 0 4px 6px rgba(0,0,0,0.1); }` (浮いて見える input)
- **Good:** input には inset shadow、card には outer shadow を使う
- **CSS 例:**
  ```css
  input { box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.08); }
  .card { box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); }
  ```

### [S] Dark mode では shadow より surface lightness で depth 表現

- **Why:** 暗い背景の上では黒系の shadow がほぼ視認できず depth の手がかりにならない。代わりに surface の明度差 (elevation ごとに少しずつ明るくする) で depth を表現する
- **Bad:** dark mode でも light mode と同じ `box-shadow: 0 4px 6px rgba(0,0,0,0.1);` を使う
- **Good:** elevation level ごとに background lightness を段階的に上げる
- **CSS 例:**
  ```css
  [data-theme="dark"] {
    --surface-0: #18181b;
    --surface-1: #232326; /* elevation 1 */
    --surface-2: #2c2c30; /* elevation 2 */
  }
  ```

## State

### [S] Hover は "brighter" ではなく明確な変化にする

- **Why:** 単に明るさを変えるだけの hover は変化が微妙すぎて気付かれにくい。underline や shade の変更など知覚しやすい変化にする
- **Bad:** `a:hover { filter: brightness(1.1); }` (変化が分かりにくい)
- **Good:** `a:hover { text-decoration: underline; }`
- **CSS 例:**
  ```css
  a:hover   { text-decoration: underline; }
  .btn:hover { background: var(--primary-600); } /* shade を 1 段階変える */
  ```

### [S] Disabled は opacity 0.5 だけで表現しない

- **Why:** opacity だけの disabled は色覚特性によっては判別しづらく、カーソル形状の変化がないと操作可否がわかりにくい
- **Bad:** `.btn:disabled { opacity: 0.5; }`
- **Good:** opacity + 色調変更 + cursor を組み合わせる
- **CSS 例:**
  ```css
  .btn:disabled {
    opacity: 0.6;
    background: var(--gray-200);
    color: var(--gray-400);
    cursor: not-allowed;
  }
  ```

### [S] Empty state は icon + headline + description + CTA の 4 要素セット

- **Why:** 空の画面をそのまま見せるとユーザーは「壊れている」のか「まだ何もない」のか判断できない。4 要素セットで状況説明と次の行動を提示する
- **Bad:** 空の table をそのまま何も表示せず見せる
- **Good:** icon + 「タスクはまだありません」+ 説明文 + 「最初のタスクを作成」ボタン
- **CSS 例:**
  ```css
  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    padding: 48px 24px;
    text-align: center;
  }
  ```

### [S] Loading state は skeleton / spinner / progress で表現する

- **Why:** 読み込み中に何も表示されない blank screen はユーザーに「反応していない」印象を与え、離脱や再操作を招く
- **Bad:** データ取得中は何も描画しない (blank screen)
- **Good:** レイアウト形状を保った skeleton を表示する
- **CSS 例:**
  ```css
  .skeleton {
    background: linear-gradient(90deg, #e2e8f0 25%, #edf2f7 37%, #e2e8f0 63%);
    background-size: 400% 100%;
    animation: skeleton-loading 1.4s ease infinite;
  }
  ```

### [S] Error state は 問題 + 原因 + 解決策 + action の構造にする (Nielsen #9)

- **Why:** 「エラーが発生しました」だけのメッセージはユーザーが次に何をすべきか分からない。何が・なぜ・どう直すかを明示することで自己解決を促す
- **Bad:** 「エラーが発生しました」とだけ表示する
- **Good:** 「保存に失敗しました。ネットワーク接続を確認し、再試行してください。」+ 再試行ボタン
- **CSS 例:**
  ```css
  .error-state {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 16px;
    border: 1px solid var(--red-300);
    background: var(--red-50);
    color: var(--red-700);
  }
  ```

### [T] Focus state は必ず定義する

- **Why:** focus indicator が消えるとキーボードユーザーが現在の操作位置を見失い、WCAG 2.4.7 (Focus Visible) 違反になる。マウス操作時の見た目を保ちたい場合は `:focus-visible` で使い分ける
- **Bad:** `button:focus { outline: none; }` (focus indicator が完全に消える)
- **Good:** `button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }`
- **CSS 例:**
  ```css
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  ```

## Layout

### [S] Fixed width より max-width + margin auto + responsive padding

- **Why:** 固定幅は画面サイズによってはみ出しや過度な余白が生まれる。max-width + margin auto なら画面に応じて中央寄せしつつ最大幅を保てる
- **Bad:** `.container { width: 1200px; }`
- **Good:** `.container { max-width: 1200px; margin: 0 auto; padding: 0 1rem; }`
- **CSS 例:**
  ```css
  .container { max-width: 1200px; margin: 0 auto; padding: 0 1rem; }
  ```

### [S] Aspect-ratio で CLS 対策する

- **Why:** 画像や動画の読み込み前に領域が確保されていないと、読み込み完了時にレイアウトが飛ぶ (Cumulative Layout Shift)。aspect-ratio で事前に領域を確保する
- **Bad:** `<img>` に `width`/`height` も `aspect-ratio` も指定しない
- **Good:** `aspect-ratio` を指定して読み込み前から領域を確保する
- **CSS 例:**
  ```css
  .video-embed { aspect-ratio: 16 / 9; width: 100%; }
  img { aspect-ratio: attr(width) / attr(height); }
  ```

### [S] Flexbox / CSS Grid で alignment する

- **Why:** float や margin による手動配置は崩れやすく、レスポンシブ対応も複雑になる。Flexbox/Grid はネイティブな alignment API を持ち保守性が高い
- **Bad:** `float: left; margin-left: calc(50% - 100px);` のような手動計算
- **Good:** `display: flex; justify-content: center; align-items: center;`
- **CSS 例:**
  ```css
  .row { display: flex; align-items: center; justify-content: space-between; }
  ```

### [S] Icon + text alignment は flexbox + gap 8px で揃える

- **Why:** `vertical-align: middle` は inline/inline-block 文脈でのみ機能し、line-height の影響を受けて微妙にズレることが多い。flexbox の `align-items` なら常に確実に揃う
- **Bad:** `<span><svg style="vertical-align: middle">...</svg></span>` のような指定
- **Good:** `display: flex; align-items: center; gap: 8px;`
- **CSS 例:**
  ```css
  .icon-label { display: flex; align-items: center; gap: 8px; }
  ```

### [S] Container padding は responsive に変える

- **Why:** モバイルの狭い画面で desktop 同等の padding を使うとコンテンツ領域が圧迫される。逆に desktop で mobile 相当の padding だと余白不足で窮屈に見える
- **Bad:** `.container { padding: 32px; }` を全画面幅で固定する
- **Good:** breakpoint に応じて padding を変える (mobile 16px, desktop 32px)
- **CSS 例:**
  ```css
  .container { padding: 16px; }
  @media (min-width: 768px) {
    .container { padding: 32px; }
  }
  ```

### [S] Breakpoint は content-first で決める

- **Why:** 特定デバイスの画面幅に合わせて breakpoint を決めると、新しいデバイスサイズが登場するたびに対応漏れが起きる。コンテンツが窮屈になる幅で切ることで、どんなデバイスでも破綻しない
- **Bad:** `@media (max-width: 375px)` のように iPhone SE 幅に固定した breakpoint
- **Good:** grid column が cramped になり始める幅を実測して breakpoint にする
- **CSS 例:**
  ```css
  /* content-first: grid が窮屈になり始める実測値を使う */
  @media (max-width: 720px) {
    .grid { grid-template-columns: repeat(6, 1fr); }
  }
  ```
