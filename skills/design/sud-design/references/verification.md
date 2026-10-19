# ドメインモデルの検証

ドメインモデル図は描いた時点では仮説にすぎない。実装前に 3 段で検証する。段 1 は必須、段 2 は状態機械を持つ設計で必須、段 3 は opt-in。

## 段 1: 遷移列シナリオ (必須)

具体 ID・具体状態を入れた時系列の遷移列を **2〜3 本**手書きし、ドメインモデル図が矛盾なく表現できることを確かめる。README.md の「検証シナリオ」節に置く。

書き方:

- 実際に起こる代表的な流れを選ぶ。正常系 1 本 + 設計判断が効く異常系 1〜2 本 (再試行・失敗からの復帰・境界をまたぐ流れ)
- 各ステップは「具体 ID + 状態の変化」で書く (例: `wo:01K3B` の Session が `[S2, S3]` の 2 つになる)。抽象名のままだと多重度・系譜の矛盾が見えない
- **検査するのは組合せ検査 (段 2) が見ない側**: 多重度 (0..1 のはずの関連に 2 件目が要るシナリオは無いか) / 集約の境界 (1 遷移で 2 集約を同時更新していないか) / 系譜 (再試行・再発行が新旧どちらの実体に積まれるか)
- 末尾に検証の結論を 1 行書く (「N シナリオとも矛盾なく表現できる」/ 表現できなかった点と直したモデル)

**シナリオが表現できなかったらモデル側を直す。** シナリオを曲げて通すと、実装時に同じ矛盾へ戻ってくる。

ユースケースとは別物として書く: ユースケースはアクターの**意図**をステップにする規約 (具体インスタンスを書かない) で、シナリオは逆に具体インスタンスの遷移列でモデルを検査する装置。同じ流れを両方に書いてよい。

## 段 2: 状態×イベント組合せの網羅検査 (状態機械を持つ設計で必須)

状態機械の未定義遷移は、図の見た目からは「描き忘れ」と「設計判断としての未定義」が区別できない。`statechart.puml` に対して同梱 script を走らせ、全組合せを機械列挙して**未定義の組合せに理由の宣言を要求する**:

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/check-statechart.py <置き場>/statechart.puml
```

検査する項目・記法・exit code の正本は script の `--help`。

- exit 0 = 違反なし
- exit 1 の出力は違反か検査不能を名指しする。**未定義の組合せ (`[statechart uncovered]`) は、定義漏れなら遷移を足し、意図した未定義なら `' uncovered: <state> x <event> — <理由>` を statechart.puml に足す**。理由を書けない組合せは定義漏れの側。**自分以外へ出ていく遷移 (ラベル無しの自動遷移を含む) が無い状態 (`[statechart dead end]`) は、図全体の終わりにあたる状態なら `State --> [*]` で final state と宣言し、そうでなければ遷移を足す**。final state を 1 つでも宣言すると全状態に final state へ届くことが要求されるので、終わりの無い状態機械では行き止まりにも遷移を足す側で直す。**final state へ届かない状態 (`[statechart no termination]`) は、届く遷移を足す**。**同じ状態・同じイベント (イベント無しの自動遷移を含む) で遷移先が分かれる遷移 (`[statechart nondeterministic]`) は、guard で排他に分けるか、遷移を 1 本にする**。検査不能 (`[statechart empty]` など) なら、図を `--help` の対応記法の範囲で書き直す
- 宣言が残骸化した場合 (遷移を後から足した・改名した) も同じ script が落とすので、状態機械を変えたら再実行する。残骸の宣言は消すか、改名後の名前に直す

## 段 3: 不変条件の反例探索 (opt-in)

集約の不変条件 (「同一 issue の非終端実体は 0..1」のような、状態機械単体では表現できない制約) が 3 つ以上あるなら、Hypothesis の RuleBasedStateMachine で反例探索を足す価値がある。手書きシナリオが 2〜3 本の遷移列を固定で確かめるのに対し、こちらは遷移列を自動生成して不変条件の破れを探す。

- 実装は不要 — 型も value object も持たない 50 行程度の Python で、遷移を `@rule`、不変条件を `@invariant` として書く
- 置き場は設計ディレクトリ配下 (`<置き場>/model_test.py` 等)。設計段階のモデル検証であり、実装のテストではない
- 実行: `uv run --with pytest --with hypothesis pytest <file>`
- 骨格:

```python
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule


class DesignModel(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.orders: dict[str, str] = {}  # id -> phase

    @rule()
    def assign(self):
        ...  # 遷移の事前条件と効果を、設計 doc の状態機械のとおりに書く

    @invariant()
    def one_open_order_per_issue(self):
        ...  # 集約の不変条件


TestDesignModel = DesignModel.TestCase
```

違反が出たら Hypothesis が最小化した遷移列を出す — それを段 1 のシナリオとして README に書き足し、モデルを直す。
