# Review Checklist (51 項目)

frontend-refine の Review phase で使う self-review checklist。

- 判定者は `script` (review-static.py で自動検出) か `Claude` (主観判定) のいずれか
- Red Flag が 1 件でもあれば「未達」判定 → 必ず修正
- Green Light ≥ 40/51 (78%) & Red Flag = 0 → 「洗練済み」判定

## A. Dieter Rams 10 principles (判定者: Claude 主観)

### A1. 新しい問題解決の視点があるか?
- Green Light: 陳腐なテンプレコピペではない
- Red Flag: 見たことある LP そのまま

### A2. 機能満足 + 感情的満足の両立
- Green Light: 機能面の使いやすさに加え、視覚的な満足感がある
- Red Flag: 動作はするが無機質で心が動かない

### A3. 色/形/typo/space が統一世界観
- Green Light: token 化されたスケールに沿って一貫している
- Red Flag: 場当たり的な値が混在している (arbitrary color / spacing)

### A4. 説明なしで使い方が分かる
- Green Light: 初見でも操作方法が推測できる
- Red Flag: tooltip や説明文がないと使えない

### A5. 見た目と実質が一致 (誇大表現なし)
- Green Light: 見た目が機能や内容を正確に表現している
- Red Flag: 装飾が実態以上に見せかけている (誇大な演出)

### A6. 装飾が content を邪魔していない
- Green Light: 装飾は content を引き立てる役に徹している
- Red Flag: 装飾が読解や操作の妨げになっている

### A7. 3-5 年後も陳腐化しない設計 (今年のトレンド依存禁止)
- Green Light: 普遍的な原則に基づく設計になっている
- Red Flag: 今年流行りの装飾・演出に強く依存している

### A8. すべての詳細に一貫した rule (token 化されている)
- Green Light: 色 / spacing / typo が token 経由で決まっている
- Red Flag: 個別に値を直書きしている箇所がある

### A9. リソース消費が最小 (重い animation / 巨大 image 依存禁止)
- Green Light: 軽量な実装 (最小限の animation / 適切な image 最適化)
- Red Flag: 重い animation や巨大な image に依存している

### A10. 引き算できたか (削除で欠けるものが無いなら削除)
- Green Light: 不要な要素は削除済み
- Red Flag: 消しても支障のない要素が残っている

## B. Nielsen 10 usability heuristics (判定者: Claude 主観)

### B1. Loading / 処理中の状態が可視化
- Green Light: loading state (skeleton / spinner / progress) が定義されている
- Red Flag: 処理中に何も表示されない (blank screen)

### B2. ユーザーの日常言語で書かれている (業界用語 / 略語禁止)
- Green Light: 平易な言葉で書かれている
- Red Flag: 業界用語・略語がそのまま使われている

### B3. Undo / Back / Cancel が常時可能
- Green Light: 操作の取り消し・後退経路が用意されている
- Red Flag: 一方通行で後戻りできない

### B4. 同じアクションは同じ場所・同じ見た目 (一貫性)
- Green Light: 同種操作の位置・見た目が画面全体で統一されている
- Red Flag: 同じ操作が場所ごとに異なる見た目・位置で提示される

### B5. 危険操作に確認 / 検証がある
- Green Light: 破壊的操作に確認ダイアログ等がある
- Red Flag: 削除等の危険操作が確認なしで即実行される

### B6. 選択肢は見えて選べる (記憶に頼らない)
- Green Light: 選択肢が画面上に可視化されている
- Red Flag: ユーザーの記憶に依存する導線になっている

### B7. ショートカットが初心者と上級者両方に対応
- Green Light: 初心者向け導線と上級者向け効率化が両立している
- Red Flag: 上級者向け効率化のみ、または初心者導線のみに偏っている

### B8. 余分な情報 / 装飾なし (ミニマリズム)
- Green Light: 必要な情報のみで構成されている
- Red Flag: 不要な情報・装飾が目的達成を妨げている

### B9. エラーメッセージが「何が」「なぜ」「どう直す」
- Green Light: エラーメッセージが原因と解決策を含む
- Red Flag: 「エラーが発生しました」のみで原因不明

### B10. Help / documentation が届くところにある
- Green Light: ヘルプ・説明への導線がある
- Red Flag: 迷ったときに参照できる情報がない

## C. Apple HIG (Clarity / Deference / Depth) (判定者: Claude 主観)

### C1. Clarity: 12px 以上、contrast 4.5:1 以上、icon 意図が明確
- Green Light: 文字サイズ・contrast・icon の意図がすべて明瞭
- Red Flag: 12px 未満の文字や意図不明な icon がある

### C2. Deference: UI 装飾が content を主役から外していない
- Green Light: UI は content を支える役に徹している
- Red Flag: 装飾が content より目立っている

### C3. Depth: shadow / opacity / layer で関係性が視覚化
- Green Light: shadow / opacity / layer で要素間の階層が伝わる
- Red Flag: 全要素が同一平面に見え階層が不明瞭

## D. Material 3 (Personal / Adaptive / Expressive) (判定者: Claude 主観)

### D1. Personal: dark mode / text size / color の user 選択が可能
- Green Light: dark mode 等の個人設定に対応している (実装対象範囲内で)
- Red Flag: user 設定の余地がなく固定表示のみ

### D2. Adaptive: mobile / tablet / desktop で layout が最適化
- Green Light: breakpoint ごとに layout が最適化されている
- Red Flag: 全画面幅で同一 layout のまま崩れている

### D3. Expressive: transition / animation が反応的で意味を持つ
- Green Light: transition / animation が状態変化を意味的に伝える
- Red Flag: animation が過剰、または意味のない装飾になっている

## E. WCAG 2.1 AA (判定者: script E1-E5 / Claude E6-E7)

### E1. Text contrast ≥ 4.5:1 [script: rule E1]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に E1 finding
- [judge: script]

### E2. UI component contrast ≥ 3:1 [script: rule E2]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に E2 finding
- [judge: script]

### E3. Heading order (H1 単一, H1→H2→H3) [script: rule E3]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に E3 finding
- [judge: script]

### E4. Form label 有無 [script: rule E4]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に E4 finding
- [judge: script]

### E5. Alt text 有無 [script: rule E5]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に E5 finding
- [judge: script]

### E6. Keyboard で全機能操作可能
- Green Light: Tab + Enter + 矢印キーで全機能に到達・操作できる
- Red Flag: マウス操作前提で keyboard から到達できない機能がある
- [judge: Claude]

### E7. Focus indicator 見える
- Green Light: 全 interactive 要素で focus 時に視認可能な indicator がある
- Red Flag: focus しても indicator が見えない箇所がある
- [judge: Claude (script rule G2 の逆判定と併用)]

## F. Refactoring UI heuristics (判定者: script F1-F3 / Claude F4-F8)

### F1. 8pt spacing に統一 [script: rule F1]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に F1 finding
- [judge: script]

### F2. Multi-layer shadow [script: rule F2]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に F2 finding
- [judge: script]

### F3. Semantic token 経由の色 (生 hex 直書き禁止) [script: rule F3]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に F3 finding
- [judge: script]

### F4. グレースケール test
- Green Light: 白黒に変換しても hierarchy が判別できる
- Red Flag: 色に依存しないと要素の重要度が分からない
- [judge: Claude]

### F5. 主役は強調 + 脇役は de-emphasize
- Green Light: 主役要素の強調と脇役要素の抑制が両方行われている
- Red Flag: 全要素が同じ強さで主張し合っている
- [judge: Claude]

### F6. Button hierarchy 明確
- Green Light: primary / secondary / tertiary が視覚的に区別できる
- Red Flag: すべての button が同じ見た目になっている
- [judge: Claude]

### F7. Typography hierarchy 明確
- Green Light: weight × size の組合せで階層が明確になっている
- Red Flag: 見出しと本文の区別がサイズのみ、または不明瞭
- [judge: Claude]

### F8. Container 分離は shadow + 余白で
- Green Light: shadow + 余白で container が分離されている
- Red Flag: border 線のみで区切っている
- [judge: Claude]

## G. Do-Not list (Red Flag) (判定者: script G1-G6 / Claude G7-G10)

### G1. Pure #000 text 禁止 [script: rule G1]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に G1 finding
- [judge: script]

### G2. `outline: none` 単独禁止 [script: rule G2]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に G2 finding
- [judge: script]

### G3. Disabled は opacity 単独禁止 [script: rule G3]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に G3 finding
- [judge: script]

### G4. Shadow spam (全 card に shadow) 禁止 [script: rule G4]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に G4 finding
- [judge: script]

### G5. `text-align: justify` 禁止 [script: rule G5]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に G5 finding
- [judge: script]

### G6. 非 link 要素の border-bottom 禁止 [script: rule G6]
- Green Light: script が違反を検出しない
- Red Flag: script 出力に G6 finding
- [judge: script]

### G7. Placeholder を label 代わりに使わない
- Green Light: 全 input に `<label for>` があり placeholder は補助情報に留まる
- Red Flag: placeholder のみで label が存在しない input がある
- [judge: Claude, E4 の script 検出とも重複]

### G8. 装飾的な glow / gradient を状態表現に使わない
- Green Light: 状態表現は色 / 形 / 文言など明確な手段で行われている
- Red Flag: glow / gradient のみで状態 (成功 / エラー等) を表現している
- [judge: Claude]

### G9. CTA vs decoration の hierarchy 逆転禁止 (squint test)
- Green Light: 目を細めて見ても CTA が最も目立つ
- Red Flag: 装飾要素が CTA より視覚的に強い
- [judge: Claude]

### G10. Gray text on gray bg の低 contrast 禁止
- Green Light: gray 系配色でも contrast 基準を満たしている
- Red Flag: gray text と gray background の組み合わせで視認性が低い
- [judge: Claude, E1 の script 検出とも重複]

## 集計

- Red Flag 数: __ / 51 (0 が目標)
- Green Light 数: __ / 51 (40+ で洗練済み)
- 洗練度判定: 洗練済み / 未達 / 洗練不足
