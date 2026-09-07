# 構造レビューの判断規則

## いつこの doc を Read するか

write-for-harness の俯瞰 (構造) reviewer として dispatch されたときに Read する。単一 component の文面レビュー (検証 subagent) では読まない。

## 課題領域

構造レビューが扱うのは cross-component の 3 領域のみ:

- **配置**: その規制・知識がいまの層 (skill / hook / CLAUDE.md / settings / rules) にあるべきか、別の層へ移すべきか
- **統廃合**: skill / rules の分割・統合。重複した正本の一本化
- **統治構造**: その規制を guide (事前) と sensor (事後)、Inferential (散文規範) と Computational (hook / settings / linter) のどちらで強制すべきか

ファイル**内**の文面品質 (節構成・冗長・frontmatter の妥当性) は検証 subagent の領域なので扱わない。同じ事象でも「この節をこのファイル内で直す」は検証 subagent、「この節を別 component へ移す」は構造レビューに属する。

## 配置の判断枠

- 規制がどの slot (Computational / Inferential × Guide / Sensor) に属するかは [architecture](./architecture.md) の 4 slot 表と Slot 選択フローで決める
- 常時ロード層 (CLAUDE.md) と条件ロード層 (rules / docs 索引 / skill) の振り分けは [claude-md](./components/claude-md.md) の「載せる基準と段階開示」の表で決める — 載せる基準は「無いと判断が変わるか」、逃がし先は「誰がロードを起動するか」
- 現在地と判断枠上の正位置が食い違うファイル・節が、配置提案の候補になる

## 分割・統合の判断規則

分割してよい軸は**課題領域の独立性**のみ。次のいずれかを満たすときだけ分割を提案する:

- **観測範囲・入力が異なる**: 2 つの用途が別の分母・別の入力を観測しており、同居させると分母の取り違え (一方の文脈でしか成立しない規範をもう一方へ持ち込む事故) が起きる
- **正本の分担を宣言できる**: 「A は X の正本、B は Y の正本」と双方の本文に境界を明文化でき、利用者がどちらを引くか迷わない

分割には固定費がある。提案時は必ず対価を勘定に入れる:

- 呼び出し入口が二重になる (trigger 語彙の競合、利用者がどちらを起動するか選ぶ認知コスト)
- 選択を誤ったときのコスト (誤った側の規則だけ読んで作業が進む)

「内容が 2 種に分類できる」という事後の分類だけを理由に分割しない — 分類できることと、別実体である必要があることは別。逆に、trigger 語彙が重複する・正本の分担を宣言できないまま併存している 2 実体は統合の候補になる。

## 偽陽性抑制

- **移動・統合の提案は、移動先の実態を Read で確認してから出す**。「移動先にはこういう節があるはず」という推測のままの提案は、移動先に同名の別内容があったり移動先自体が肥大していたりする現実を見落とす
- 確認できなかった (Read しなかった・path が実在しなかった) 提案は出さない。件数を確保するために未確認の提案を混ぜない
- diff 対象と無関係な構造課題は、一覧と Read で実態確認できた場合のみ出す

## impact / migration_cost の 2 軸

構造提案には severity を使わない。壊れているかではなく「割に合うか」を判断させる 2 軸で評価する:

| 軸 | high | medium | low |
|---|---|---|---|
| **impact** (提案が生む効果) | 誤動作・分母の取り違え・二重管理 drift を構造的に断つ | 重複ロード・保守コストが目に見えて減る | 整理としては正しいが挙動は変わらない |
| **migration_cost** (移行の費用) | 多数ファイルの変更 + 参照更新 + 利用者の起動経路が変わる | 数ファイルの変更と参照更新で閉じる | 1〜2 ファイルの移動・追記で閉じ、外部参照が壊れない |

- **impact** は、その構造変更が防ぐ誤動作・削る重複・下げる保守コストの大きさで測る
- **migration_cost** は、変更ファイル数・追随が必要な参照 (link / permission entry / gate / 索引) の数・利用者に見える変化 (起動名・配置) で測る
- 採否の目安: impact が migration_cost を上回る提案が採用候補。low impact × high cost は原則出さない (ノイズになる)

## 参照

- 共通: [architecture](./architecture.md) (4 slot 表と選択フロー — 配置判断の枠) / [claude-md](./components/claude-md.md) (常時ロード層の載せる基準と段階開示 — 層間振り分けの枠) / [models](./models.md) (context 肥大と分割の判断材料)
- 文面品質の領域境界: [skill](./components/skill.md) / [rules](./components/rules.md) / [hook-inject](./components/hook-inject.md) / [settings](../../../knowledge/claude-config-review/references/settings.md) / [hook](../../../knowledge/claude-config-review/references/hook.md) (ファイル内の観点はこれら component 別 reference の領域)
