# agent 定義のレビュー観点

## いつこの doc を Read するか

agent 定義 (`.claude/agents/<name>.md` / plugin の `agents/<name>.md`) を新設・編集する前、または skill の手順に subagent を起こす箇所を書く・直す前に Read する。**inline subagent のままにするか、agent 定義に切り出すか**をここで決める。

## 責務

agent 定義は、名前で指名される subagent の本文 (system prompt) と実行条件 (`tools` / `model` / `effort`) をまとめた markdown ファイル。呼び出し元は Agent tool の `subagent_type` に名前を渡すだけで、役割の手順と深さは定義側が持つ。

- **inline subagent との違い**: inline subagent は呼び出し元が prompt に手順を書く。model は環境変数 `CLAUDE_CODE_SUBAGENT_MODEL` があればその値、無ければ呼び出し元のセッションの model になり、effort は呼び出し元のセッションから継承する。agent 定義は手順・tool・model・effort を 1 箇所に固定する。
- **定義に書かないもの**: 起動の作法 (同時に何体起こすか・指名が解決しないときの扱い・結果の使い方) は呼び出し元 skill の本文に書く。書き方の規範は本書の「起動の仕方」が持つ。

## 切り出す条件

inline subagent を agent 定義に切り出すのは、次の**どれか 1 つ**に当たるとき。どれにも当たらなければ inline のままにする — 定義を増やすと、呼び出し元を読むだけでは手順が見えなくなり、保守する file も増える。

1. **呼び出し元のセッションと違う model / effort が要る**。skill frontmatter の `model` / `effort` は skill が active な turn にしか効かず、その skill が起こす subagent には届かない。
2. **tool を絞る必要がある**。未信頼の入力 (外部 Web・vendor したファイル) を読む役割や、作業ツリーを書き換えさせたくない役割。
3. **同じ役割の prompt が 2 つ以上の呼び出し元にある**。手順を呼び出し元ごとに持つと、片方だけ直って食い違う。

## model と effort の置き場

- **定義の frontmatter に `model` と `effort` を両方書く (例外は下の `model: inherit` の役割)。** 既定値と同じでも書く — 既定はモデルごとに違い、書いていないと定義を読んでも深さが決まらない。選び方は [models](../models.md) の「`model:` frontmatter 選定基準」と「effort の使いどころ」。
- **呼び出し元は Agent tool の `model` / `effort` 引数を渡さない。** `model` 引数は frontmatter より優先される。渡すと定義に固定した値が呼び出し元ごとに書き換わり、深さの置き場が散る。
- **環境変数 `CLAUDE_CODE_EFFORT_LEVEL` は frontmatter の `effort` より優先される。** 設定されている環境では、定義ごとに変えた effort が全部その値に揃う。
- **`model: inherit` は、呼び出し元のセッションと同じ深さで動くこと自体が役割の要件であるものに限る。** この役割では `effort` を書かず、セッションの effort で動かす。並列に作業を分け持つ役割でも、役割に要る深さを定義の側で決められるなら、`model` と `effort` を固定する — inherit にすると、人が対話のために選んだ effort が並列の数だけ費用に掛かる。

## prompt cache の TTL

- **呼び出し元や他の agent の返事を 5 分以上待つことがある役割 (起動後に SendMessage で再開される役割を含む) は、frontmatter に次の形で書く。** subagent の prompt cache は既定で 5 分で切れ、待った後の最初の呼び出しが context 全体を書き直す。待たずに終わる役割には書かない — 1 時間の TTL は cache の書き込み単価を上げるので、待たない役割では費用が増えるだけになる。

```yaml
experimental:
  cacheTtl: 1h
```

## haiku に任せる条件

haiku を選ぶのは、次の **4 つをすべて**満たす仕事だけ。1 つでも欠けるなら sonnet 以上に置く。

- 判断を含まない機械的な仕事 (検索・抽出・分類・決まった形式への整形)
- 読み取りだけで済む
- 出力が決まった形式で、呼び出し元が形式を検証できる
- 間違えても後段 (反証・検査・人間の確認) で検出される

**haiku の `effort: low` は、検索や複数手順を伴う役割に付けない** (理由は [models](../models.md) の Haiku 節)。その種の役割は `medium` に置く。

## tools

- **役割に要る tool だけを列挙する。** `tools` を書かないと全 tool を継承する。
- **読み取りだけの役割は `Read` / `Grep` / `Glob` に絞る。** `Bash` を残すと書き込みの経路も残り、その経路は本文の文面でしか抑えられない。コマンドの実行が要る役割 (`git diff` の実行や大きな file の grep 等) だけ `Bash` を足す。
- **plugin の agent 定義は `hooks` / `mcpServers` / `permissionMode` を持てない。** tool の細かな制限を hook で掛ける設計は、plugin で配る定義には使えない。

## 本文と呼び出し元の分担

| 置く場所 | 置くもの |
|---|---|
| 定義の本文 | 役割の手順・判定の仕方・報告形式 (どの呼び出しでも同じもの) |
| 呼び出し元の prompt | その回の入力 (対象・観点・判定の軸)。報告形式を変えたいときだけ、その形式 |
| 呼び出し元 skill の本文 | 起動の作法 (同時に何体・name の有無・指名が解決しないときの扱い) |

- **本文は呼び出しのたびに読み込まれる。** 一部の呼び出し元しか使わない手順を共用の定義に足すと、他の呼び出し元が毎回その分を払う。手順が呼び出し元ごとに分かれるなら、定義を役割ごとに分ける。
- **呼び出し元の prompt に、定義の本文と同じ手順を書き写さない。** 写すと 2 箇所の手順が食い違ったとき、どちらが効くかが読み手から決まらない。

## 起動の仕方

- **報告は subagent の最終メッセージで受け取る。** 最終メッセージは完了時に呼び出し元へ届くので、prompt に「SendMessage で報告を送れ」と書かない。書くと、報告を送らないまま止まった subagent への催促の往復が要る。並列にしたいときは同じ message で複数起こす。
- **`name` は、起動後に追加の依頼を送る宛先が要る役割にだけ付ける** (merge まで依頼を受け続ける等)。結果を受け取るだけの役割には付けない。
- **指名した名前が解決しないとき、`general-purpose` で代わりに起こさない。** 代わりに起こすと model・effort・tools が定義の値から黙って外れ (環境の既定か呼び出し元の値になる)、指名した深さで動いたように見える。名前が解決しなかったことを報告し、その agent の結果に依存する工程は進めない (独立に走った他の結果は使ってよい)。

## 仕様

| フィールド | 必須 | 意味 |
|---|---|---|
| `name` | ✓ | 指名に使う名前。file 名の stem と揃える。plugin の定義は `<plugin 名>:<name>` で指名する |
| `description` | ✓ | 何をする subagent か + どの工程から指名されるか (工程の種類で書き、呼び出し元の skill 名は書かない — 呼び出し元が改名・撤去されると腐る) |
| `tools` | - | 使える tool の列挙。省略すると全 tool を継承する |
| `model` | - | alias・モデル ID・`inherit`。選べる値は [models](../models.md) の表。省略すると環境変数 `CLAUDE_CODE_SUBAGENT_MODEL`、それも無ければ呼び出し元の model になる |
| `effort` | - | `low` / `medium` / `high` / `xhigh` / `max`。モデルが対応しない段を指定すると、指定以下で対応する最高の段で動く |
| `experimental` | - | 実験的な設定の入れ物。`cacheTtl` (`5m` / `1h`) で subagent の prompt cache の TTL を決める。書く条件は「prompt cache の TTL」 |

model の決まり方は、Agent tool の `model` 引数 → 定義の `model` → 環境変数 `CLAUDE_CODE_SUBAGENT_MODEL` → 呼び出し元の model の順。

## チェックリスト

agent 定義を新設・編集する前、または skill に subagent の起動を書く前に確認する。

- [ ] 切り出す条件のどれに当たるかを言えるか (言えないなら inline のままにする)
- [ ] frontmatter に `model` と `effort` を両方書いたか (`model: inherit` の役割は `effort` を書かない)
- [ ] 返事を 5 分以上待つ役割にだけ、`experimental` の `cacheTtl: 1h` を書いたか
- [ ] haiku を選んだなら、4 条件をすべて満たすか。検索や複数手順を伴う役割に `effort: low` を付けていないか
- [ ] `tools` が役割に要るものだけか。読み取りだけの役割から書き込み系 tool と `Bash` を外したか
- [ ] 本文に、一部の呼び出し元しか使わない手順を入れていないか
- [ ] 呼び出し元が Agent tool の `model` / `effort` 引数を渡していないか
- [ ] 呼び出し元の prompt が、定義の本文と同じ手順を書き写していないか
- [ ] 報告を最終メッセージで受け取る形か (prompt で SendMessage による報告を求めていないか)

## アンチパターン

- **`general-purpose` に毎回同じ役割を書く**: 同じ手順が呼び出し元の数だけ散り、model は環境の既定か呼び出し元、effort は呼び出し元のセッション任せになる。2 箇所目が出た時点で定義に切り出す。
- **共用の定義に手順を足し続ける**: 例として、決済差分のレビューと設定ファイルのレビューが 1 つの reviewer 定義を共有していて、片方の手順を定義の本文に足すと、もう片方の呼び出しが毎回それを読み込む。手順が分かれた時点で定義を分ける。
- **skill の frontmatter で subagent の深さを決めたつもりになる**: skill の `model` / `effort` は subagent に届かない。subagent の深さは agent 定義でしか固定できない。
- **判断の重い仕事を haiku に置く**: 指摘の反証や設計判断は、出力の形式が決まっていても中身の正しさを後段で検出しにくい。

## 参照

- 共通: [models](../models.md) (model と effort の選び方) / [writing](../writing.md) (定義の本文と依頼文の文面規則) / [sources](../../../../knowledge/claude-config-reference/references/sources.md) (公式仕様の引き方)
- 関連: [skill](./skill.md) (subagent を起こす側の skill 本文の観点)
- 公式: Claude Code subagents ドキュメント (URL は [sources](../../../../knowledge/claude-config-reference/references/sources.md) 経由で確認)
