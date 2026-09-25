---
name: fitness-function-audit
disable-model-invocation: true
description: 対象 repo を scan し、導入・改善すべきアーキテクチャ適応度関数 (fitness function) を HTML report で提案し、選んだ候補の閾値と運用を対話で確定する。
---

# fitness-function-audit

**fitness function** = アーキテクチャ特性 (architectural characteristic) の客観的な整合性評価を継続実行する検査 (Ford / Parsons / Kua『Building Evolutionary Architectures』)。対象 repo を scan して「どの特性の劣化を、どの検査で機械検知すべきか」を card 形式の HTML report で提案し、ユーザーが選んだ card の閾値・運用方針を対話で確定して**導入仕様**として渡す。検査コードの実装はしない — 仕様の実装は呼び出し元のフローに委ねる。

report に載せるのは、**検知する劣化シナリオが書け、機械で継続検証できる検査**に落ちる提案だけ。設計・結合そのものの良し悪しの所見は対象外で、検査に落とせない指摘は口頭でも出さない — 検証不能な所見が混ざると「report の全 card は機械検証可能」という保証が崩れるため。対象 repo へは書き込まない — 成果物は OS temp の report と、会話上の導入仕様のみ。

## 入力

- 引数なし → cwd の repo を対象
- `<path>` → 指定 path の repo を対象
- 方向の指定 (「テスト運用だけ」「依存構造だけ」等) があれば scan をそこへ絞る

## 手順

### 1. Scan する

対象 repo を Read / Grep / Bash (git log 等) で調べ、2 つを把握する:

1. **既にある検査**: CI 設定・pre-commit・lint/test 設定・自作 verify script。それぞれ「どの特性を守っているか」「確かに落ちる負の fixture を持つか」を確かめる (負の fixture が無い検査は空回りしていても気づけない — 改善 card の主要な種)
2. **守られていない特性**: commit 履歴の hot spot・repo の構成・ドキュメント群から、劣化しつつあるのに検査が無い特性を探す。特性は「制約 → アーキテクチャ特性 → 構造」の導出順で、その repo に影響度の高い -ilities だけを選抜する — 全特性を測ろうとしない。観点と言語別ツールは [references/tools.md](references/tools.md) の 5 カテゴリ表を使う

repo 規模が大きければ scan は read-only subagent に分担させてよい。

candidate はこの段では確度で落とさず全件挙げる (確度は手順 2 の推奨度 badge で表現する)。落とすのは手順 2 の 7 項目判定だけ。

### 2. Report を書く

自己完結の HTML 1 file を OS temp (`$TMPDIR`、無ければ `/tmp`) の `fitness-function-audit-<timestamp>.html` に書き、`open` (macOS) / `xdg-open` (Linux) で開いて絶対 path を伝える。

candidate ごとに card を 1 枚。**7 項目すべて埋まらない candidate は載せない**:

1. **対象特性**: 守る architectural characteristic (-ility)
2. **劣化シナリオ**: この検査が無いとき、どの変更がどう壊すか (具体の repo 内 path で)
3. **実行可能な検査**: ツール + 設定/コード断片 ([references/tools.md](references/tools.md) から引き当てる) + 実行契機 (pre-commit / CI / 定期)
4. **閾値と ratchet 方針**: 固定閾値か、回帰下限 (現状値から下げない) か。カバレッジ系の数値は負の指標かつ回帰下限として扱う — 下げない床であって上げる目標ではない (数値の最大化を目標にすると assertion のないテストでも上がる)
5. **分類 tag**: atomic/holistic・triggered/continual・static/dynamic (定義は [references/tools.md](references/tools.md) §分類軸)
6. **負の fixture**: この検査が「確かに落ちる」ことをどう確かめるか (既存検査の改善 card では現状の有無を判定)
7. **推奨度 badge**: Strong / Worth exploring / Speculative。自動化は痛みの実測後 — 実際に起きた・起きかけた劣化に根ざす candidate を Strong 側へ、理屈だけの candidate を Speculative 側へ倒す

report 末尾に **Top recommendation** (最初に入れるべき 1 枚とその理由) を置き、「どの card を進めますか」で止まる。

### 3. 選ばれた card を詰める

ユーザーが選んだ card について、閾値の初期値・ratchet の刻み・false positive の逃し方 (許容 list / 凍結 rule)・実行契機・落ちたとき誰が直すか、を 1 論点ずつ質問して確定する。全論点が確定したら card を**導入仕様** (検査の宣言 + 閾値 + 運用) として markdown で提示して終了する。実装と配置は呼び出し元のフローに委ねる。
