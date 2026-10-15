# follow-up issue の深さ

follow-up issue は、ある issue の作業 (実装・レビュー) の中で見つかり、その issue から切り出された issue。発端の issue から follow-up issue が何段連なったかを深さと呼ぶ。計画的に分けた作業の子 issue は数えない。agent が起票した issue を agent が着手可にする流れは、深さで止めないと起票が起票を呼んで自己増殖する。そこで着手可の label を付ける直前に深さを測り、上限以内のときだけ付ける。

親子の表し方は tracker ごとに違う (sub-issue で持つ tracker、本文の 1 行で表す tracker、file の配置で表す tracker がある) ので、測り方は対象 repo の tracker の文書 `docs/agents/issue-tracker.md` の「follow-up issue の深さ」節が宣言する。

## 宣言の形

```markdown
## follow-up issue の深さ

- 測り方: <測り方>
- 上限: <整数>
- 計画的な分解の親の label: <label の綴り | なし>
```

| 測り方 | 意味 |
|---|---|
| `github-sub-issue` | GitHub の sub-issue の木を親へ辿って測る。本 skill 同梱の script を使う |
| `command: <コマンド>` | repo が用意したコマンドで測る。コマンドの中の `<N>` を issue 番号に、`<上限>` を宣言の上限に置き換えて撃つ。exit 0 は上限以内、exit 3 は上限超過、それ以外は失敗 |
| `測れない` | この tracker では深さを測れない。着手可は自動では付けない |

計画的な分解の親の label は、その label を持つ親から子への辺を深さに数えないためのもの。`なし` なら、すべての親子の辺を follow-up issue の辺として数える。

## 書く (宣言が無い repo に置くとき)

宣言は repo の初期設定を行う側 (setup の手順か人) が置く。triage は宣言を読むだけで、書かない。tracker に合わせて次の値で書く。上限は 2。計画的な分解の親の label は、`docs/agents/triage-labels.md` に計画的な分解の親を表す label があればその綴り、無ければ `なし`。

| tracker | 測り方 |
|---|---|
| GitHub | `github-sub-issue` |
| それ以外 (GitLab・local の file 等) | `測れない` |

## 測る (着手可の label を付ける直前)

宣言の節を読み、測り方に応じて撃つ。

- `github-sub-issue`: `${CLAUDE_SKILL_DIR}/scripts/follow-up-depth-width.py --repo <issue 置き場> --limit <上限> --planned-split-label <label> depth <N>`。label が `なし` なら `--planned-split-label` を外す
- `command: <コマンド>`: `<N>` と `<上限>` を置き換えて撃つ

着手可の label を付けてよいのは exit 0 (上限以内) のときだけ。次のときは着手可を付けず、その issue を呼び出し元へ返す。確かめられない深さを上限以内と読むと、歯止めが黙って外れる。

| 結果 | 返すときに添えるもの |
|---|---|
| exit 3 (上限超過) | stdout。人が次にやること: 着手させるなら着手可の label を直接付ける / 要らなければ close する (triage 待ちに戻しても木は変わらず、同じ停止に戻る) |
| exit 0・3 以外 (失敗) | stdout と stderr。人が次にやること: stderr の原因 (認証・tracker のデータ・API の対応) を直して triage をやり直すか、着手させるなら着手可の label を直接付ける |
| `測れない` | 人が次にやること: 着手させるかを人が決め、着手させるなら着手可の label を直接付ける |
| 節が無い・読み取れない (tracker の文書そのものが無いときを含む) | 人が次にやること: tracker の文書に「follow-up issue の深さ」節を置く (上の「書く」) |
