# pr-quality の出典と帰属表示 (CC-BY 3.0)

[`../SKILL.md`](../SKILL.md) の決定規則は、下記原典からの蒸留 (改変を含む再利用) である。原典は CC-BY 3.0 であり、**帰属表示 (原典・作成者・ライセンス・改変の明示) が license 上必須**のため、本ファイルにそれを一元集約する。

## 帰属表示 (Attribution — 必須)

- **原典**: Google Engineering Practices Documentation
- **作成者 (クレジット)**: Google LLC
- **原典 URL**: <https://github.com/google/eng-practices> (レンダリング版: <https://google.github.io/eng-practices/>)
- **ライセンス**: Creative Commons Attribution 3.0 (CC-BY 3.0) — <https://creativecommons.org/licenses/by/3.0/>
- **改変の明示**: 本 skill は原典 `review/` 配下を日本語で要約・再編し、決定規則の形式に蒸留したもの。原文そのままの複製ではなく、swat9013 による改変を含む。改変責任は本 repo にあり、Google LLC は本蒸留物を推奨・保証しない。
- **上流の状態**: 原典 repo は 2025-11 に archived (read-only)。参照した内容は archive 時点のもの。

## 出典対応 (Traceability)

SKILL.md の各節がどの原典 md に由来するかの対応表。原文の正確な文言が必要なら原典 URL を辿る。

| SKILL.md の節 | 原典 md (`review/` 配下) | 蒸留した中核主張 |
|---|---|---|
| メタ規則 2-3 | `reviewer/standard.md`, `reviewer/comments.md` | レビューの主目的はコード健全性の継続的向上 / 事実・データ・原則を好みより優先 |
| §2 CL 分割戦略 | `developer/small-cls.md` | 1 PR = 1 自己完結変更・~100 行目安 / 小 PR の利点 / 分割軸 / リファクタリングと機能追加の分離 / テスト改善を先行 |
| §3 PR 説明文 | `developer/cl-descriptions.md` | 1 行目命令形の要約 + 本文に理由・背景 / 「Fix bug」は NG / 説明文は永続ドキュメント |
| §4 レビュアー基準 8 次元 | `reviewer/standard.md` + `reviewer/looking-for.md` | 「健全性を確実に向上させるなら未完璧でも承認」原則 / 設計・機能・複雑性・テスト・命名・コメント・スタイル・ドキュメントの 8 次元 + 全行確認・文脈・称賛 |
| §5 レビュー手順 | `reviewer/navigate.md` | (1) 説明文で俯瞰 → (2) メイン変更 → (3) 残りを文脈順の 3 ステップ |
| §2 末尾 2 bullet (新規機能付随テストの同一 PR / 1 課題 = 1 branch での commit 粒度縮約) | (原典外) | 本 repo 独自の意味調整。原典の「テスト改善を先行」を TDD 運用と噛み合わせるための補足で、原典に対応記述は無い |

## 蒸留方針 (原典との差)

- **CL → PR の読み替え**: 原典の changelist (CL) を GitHub の PR に読み替えた。両者は「レビュー対象の 1 変更単位」として等価に扱える。
- **スコープの限定**: 原典 `review/` には `speed.md` (レビュー速度) / `emergencies.md` (緊急対応) / `pushback.md` (指摘への反発対応) 等も含むが、**自己レビューと PR レビューの観点定義** に直接効く 4 領域に絞って蒸留した。速度・緊急・反発対応は観点定義の外 (レビュー運用の領分) にあるため本 skill には取り込まない。
- **他 skill との接続**: テスト観点は test-strategy、設計・リリース・エラー処理等の一般判断は engineering-judgment に委譲し、本 skill は PR アーティファクト固有の規則のみ保持する (重複を避ける)。
