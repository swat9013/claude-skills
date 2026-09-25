---
name: frontend-review
user-invocable: true
argument-hint: "[target-html] [target-css]"
description: >-
  実装済みの HTML/CSS を デザインシステムの規範 (Refactoring UI tactics / WCAG AA /
  Rams・Nielsen heuristics) に照らして検査する。静的検査 script + 51 項目 self-review で
  Red Flag と Green Light を出す。設計ドキュメントの作成と browser 表示検証は対象外。
  Use when「この画面をレビューして」「HTML デザインをレビューして」「実装が規範に沿うか見て」.
---

# frontend-review

実装済みのフロントエンド成果物 1 つを、静的検査 script と 51 項目の self-review にかけて洗練度を判定する。**設計判断はしない** — 何の画面があり component が何を持つかは `ui-design` skill が決め、本 skill はその実装が規範に沿うかだけを見る。

## 実装中に使う (レビュー前)

実装役が能動的に self-check するのは [references/tactics.md](references/tactics.md) の **[S] tactic のみ** (36 tactics 中 25 個)。実装が終わってから本 skill の「レビューする」へ進む。

対象リポジトリに `ui-design` の成果物 (`docs/design/<name>/`) があるなら、実装前に `tokens.md` の semantic token を実装ファイルへ落とし、`components.md` の state 一覧を満たす。

## レビューする

### 処理順

1. 静的検査を走らせる → JSON on stdout:

   ```bash
   uv run ${CLAUDE_SKILL_DIR}/scripts/review-static.py <target-html> [<target-css>]
   ```

2. [references/checklist.md](references/checklist.md) を Read する
3. 51 項目のうち Claude 主観 37 項目を Y/N 判定する
4. script 出力 + 主観判定を統合して `/tmp/frontend-review/design-review-<timestamp>.md` に書き出す (対象 repo には何も残さない)
5. stdout に Red Flag 件数と優先修正 3 件までのサマリを出す

### 判定閾値

- Red Flag = 0 & Green Light ≥ 40/51 → 「洗練済み」
- Red Flag > 0 → 「未達」(Red Flag が最優先修正)
- Green Light < 40 → 「洗練不足」(Rams / Nielsen の主観項目を見直す)

### stdout サマリ形式

```
[frontend-review]
Red Flag: <N> 件
  R1. <file>:<line> - <rule 概要>
  R2. ...
Green Light: <M>/51 (<pct>%)
洗練度判定: <verdict>
詳細: <出力先の絶対 path>
```

## 何をしない skill か

- **設計しない**: 画面・遷移・component・トークンの決定は `ui-design` skill の対象。本 skill は実装物だけを見る
- **描画を検証しない**: HTML/CSS の syntax verify や browser 表示検証は呼び出し元のフローに委ねる (本 skill が見るのは source の静的検査と rubric 判定だけ)
- **修正しない**: 指摘までが終点。直すかどうかと直し方は呼び出し元が決める
