# 検査カテゴリと言語別ツール

fitness function の候補を探す 5 カテゴリと、card の「実行可能な検査」欄へ引き当てるツール。本書はツールの選定までを持ち、設定の書き方は card を書くときに各出典 (公式 docs) を引く — 設定断片をでっち上げない。

## 分類軸

card の分類 tag に使う 3 軸 (原典 2nd ed. Ch.2 の 6 軸から、card の設計判断に効く 3 つ):

| 軸 | 値 | 判定 |
|---|---|---|
| Scope | **atomic** / holistic | 単一 context で 1 側面だけ検証するか、複数側面の組合せ (例: security × scalability) か |
| Cadence | **triggered** / continual | 事象 (commit・pipeline) 契機か、常時検証 (production monitoring) か |
| Result | **static** / dynamic | 合否基準が固定 (閾値・二値) か、文脈 (負荷など) で動くか |

太字が本 skill の提案の主戦場 (atomic × triggered × static)。それ以外の象限に落ちる candidate は、検査の実行基盤 (monitoring 等) の有無を card の「実行可能な検査」欄で確かめてから載せる。

## 1. 構造・依存

レイヤ違反・循環依存・モジュール境界の破れ。ツールはどれも同型 — 「A 層は B 層に依存しない」「循環禁止」を rule として宣言し、テストまたは CLI が CI で fail させる:

```
rule: modules in <layer A> shall not depend on <layer B>   → 違反したら CI が落ちる
```

| 言語 | ツール | 宣言の形式 |
|---|---|---|
| Java | [ArchUnit](https://github.com/TNG/ArchUnit) | unit test 内の fluent API。frozen rules (既存違反を凍結し新規違反だけ落とす) = ratchet の実装形 |
| C# | [NetArchTest](https://github.com/BenMorris/NetArchTest) | unit test 内の fluent API (ArchUnit の移植) |
| TS / JS | [ts-arch](https://github.com/ts-arch/ts-arch) / [dependency-cruiser](https://github.com/sverweij/dependency-cruiser) | ts-arch は test 内 (循環は `beFreeOfCycles`)。dependency-cruiser は宣言 config + CLI で、既定 rule に循環・orphan・package.json に無い依存の検出が入る |
| Python | [import-linter](https://github.com/seddonym/import-linter) / [pytest-archon](https://github.com/jwbargsten/pytest-archon) | import-linter は contract 宣言 (layers / forbidden / independence 等) + CLI。pytest-archon は test 内、既定で transitive import まで検査 |

## 2. コード品質 metrics

複雑度・ファイル長・重複・不要コードの劣化を回帰下限で止める。

| 対象 | ツール |
|---|---|
| 循環的複雑度 | radon / lizard (多言語) / eslint `complexity` rule / rubocop Metrics |
| ファイル長・関数長 | eslint `max-lines` / rubocop Metrics / lizard |
| 重複 | jscpd (多言語) |
| 未使用 export・孤児ファイル・dead code | [Knip](https://knip.dev/guides/using-knip-in-ci) (JS/TS、CI は `npx knip`) / [Vulture](https://github.com/jendrikseipp/vulture) (Python、pre-commit 統合可)。ts-prune は archived で README が Knip への移行を案内 |

閾値は「現状の最大値を初期値にして下げていく」ratchet が既定 — 固定の理想値を最初から課すと既存違反の山で導入が止まる (ArchUnit の frozen rules と同じ考え方)。

## 3. テスト

- **カバレッジの回帰下限**: 各 coverage tool の threshold 設定 (jest `coverageThreshold` / pytest-cov `--cov-fail-under` / simplecov `minimum_coverage` 等) を CI に置く
- **mutation score**: テストスイートの実効性検査。Stryker (JS/TS/C#) / mutmut (Python) / pitest (Java)。全面適用はコストが高いため重要経路から

## 4. ドキュメント

- **リンク切れ**: lychee / markdown-link-check を CI で
- **索引・規約と実体の突合**: 生成物と手書きの同期、命名規約、番号の一意性、規約 doc に書いたコマンド例の実在などは自作 verify script が主戦場になる (汎用ツールが無い領域)。pre-commit / CI に置く単機能 script として書き、「確かに落ちる負の fixture」(検出すべき違反を含む合成入力で script が実際に fail するテスト) を必ず対にする — fixture の無い検査は壊れて素通ししていても誰も気づけない

## 5. 運用・供給

- **依存の鮮度 (dependency drift)**: 依存ライブラリの古さを release pipeline で検証する ([Tech Radar: Dependency drift fitness function](https://www.thoughtworks.com/radar/techniques/dependency-drift-fitness-function))
- **依存の実在・供給元 (slopsquatting)**: 存在しない・typo の package が lockfile に入る事故を止める。[Socket](https://github.com/marketplace/actions/socket-security-action) (GitHub Action / CLI)
- **secret / credential の混入**: [gitleaks](https://github.com/gitleaks/gitleaks/blob/master/.pre-commit-hooks.yaml) / [TruffleHog](https://github.com/trufflesecurity/trufflehog/blob/main/PreCommit.md) — どちらも公式の pre-commit hook 定義を持つ。TruffleHog は verified 判定のみ fail にできる
- **セキュリティ静的解析**: [Semgrep](https://docs.semgrep.dev/guardian) を `semgrep ci` で。LLM を扱うコードなら Guardian rule set (prompt injection / unrestricted tool use) が対象になる
- **実行コスト**: run cost を fitness function として継続監視 ([Tech Radar: Run cost as architecture fitness function](https://www.thoughtworks.com/radar/techniques/run-cost-as-architecture-fitness-function))
- **build / CI 時間**: CI duration の回帰下限。閾値 gate の具体設定は CI サービスごとに引く (一次資料に汎用の設定断片は無い)
