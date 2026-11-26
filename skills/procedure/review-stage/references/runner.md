# 進行役の手順

進行役 (args に `runner` がある invoke) と、進行役が報告を返さなかったときの呼び出し元の代行 (SKILL.md 節「進行の置き場」) が Read する。SKILL.md 手順 1「args」の args を受けて「範囲」から「入力をそろえる」までを順に進め、進行役は SKILL.md 手順 2 の「進行役の報告」を返して終える。

## 範囲

初回か再レビューか・この巡が読む範囲 (`base` / `head`)・止まるかは、script が git の状態と前巡の出力 file から決める。args の範囲・`base=`・`prev=` のうち渡されたものだけを、対応する option へ写して起動する。

```bash
${CLAUDE_SKILL_DIR}/scripts/resolve-range.py [--range <base>..<head>] [--base-branch <branch>] [--prev <file>]
```

stdout は次の key を持つ JSON 1 つ。

| key | 中身 |
|---|---|
| `mode` | `初回` / `再レビュー`。再レビューになるのは、`prev=` の file の `再レビュー:` 行が `要` で始まるときだけ |
| `base` / `head` | この巡が読む範囲の SHA |
| `reviewed` | 出力の `reviewed:` 行にそのまま載せる値 |
| `round` | 「入力をそろえる」の `--round` に渡す値 (`first` / `re-review` / `re-review-widened`) |
| `log_base` | collect の `--log-base` に渡す値。増分に絞れた再レビューの巡だけ前巡の `reviewed:` 行の左側で、それ以外は `null` |
| `prev_stage` | 再レビューの巡の段階 (前巡の段階。args の段階の指定によらずこの値に決まる)。初回は `null` |
| `prev_critical_areas` | 前巡の `段階:` 行の `(重要領域: <名前>)` の名前。無ければ `null` |
| `stop` | 止まる巡なら `reason` (`未 commit` / `その他: <自由文>`) と `detail`。止まらなければ `null` |
| `notes` | 出力の注記へそのまま積む行 |

`stop` が埋まっていたら、観点を起動せずに `${CLAUDE_SKILL_DIR}/scripts/render-output.py stop --reviewed <reviewed> --reason=<stop の reason> --detail=<stop の detail>` (値が `-` で始まっても option と読まれないよう `=` でつなぐ) の出力を返して終える。script が exit 非 0 で JSON を返さなかったときは、`${CLAUDE_SKILL_DIR}/scripts/render-output.py failed --script resolve-range --stderr=<stderr>` に、resolve-range へ渡した `--range` / `--base-branch` / `--prev` をそのまま足した出力を返す。

再レビューでは `prev=` の file を `<prev>` として読む。`notes` のうち、次の 2 つは以降の手順を変える。

- `増分なし:` — 前巡の head から差分が無い。下の差分の確定の後、「入力をそろえる」を行わずに SKILL.md 手順 2 の「進行役の報告」へ進む (`観点:` 行は なし。「段階」の表で再レビューに走らせない観点の節には `走らせず: <理由>` (理由は段階と再レビューであること) を積む)
- `増分に絞れず:` — amend / rebase で前巡の head が祖先でなくなり、`base` を起点 branch との merge-base へ広げた巡。`git diff <base>...<head>` は増分ではなく起点 branch からの全差分になる。段階・`<prev>`・`再レビュー:` 行・前巡の引き継ぎは再レビューのまま扱う

差分と commit 一覧を巡ごとに 1 度確定する。`--log-base` は resolve-range の `log_base` が `null` でないときだけ渡す — 渡さないと commit 本文が前巡の head から始まり、攻撃役が前巡までの commit message から挙げた主張を、反証者が出どころに無いと読み違える。`--pr-body` は args に `pr-body=` があるときだけ、その file を渡す。

```bash
${CLAUDE_SKILL_DIR}/scripts/prepare-inputs.py collect --base <base> --head <head> [--log-base <log_base>] [--pr-body <PR・MR 本文の file>]
```

stdout は `dir` (この呼び出しに専有の一時ディレクトリ) と、`diff` / `commits` / `commit_messages` / `numstat` / `changed_files` の file の path と、`insertions` (file → insertions の行数) と、`issue_refs` (commit message と PR 本文から引いた issue ref と、その出どころ `commit_message` / `pr_body`) と、`notes` を持つ JSON 1 つ。対象 repo の `.gitattributes` が `linguist-generated` か `gitlab-generated` で生成物と宣言した file は `diff` の file から除き、除いた path を `notes` の `生成物を除外:` の行に返す (`numstat` と `changed_files` には残す。除いた path は「入力をそろえる」の script が観点ごとの prompt に載せる)。この `notes` は `diff` の file を読む観点 (欠陥探しを除く 4 つ) の節の注記へ積む。script が exit 非 0 で JSON を返さなかったときは `${CLAUDE_SKILL_DIR}/scripts/render-output.py failed --script prepare-inputs --reviewed <reviewed> --stderr=<stderr>` の出力を返す。

`diff` の file を段階の判定の元にする。git で取り直すと、担い手が読んだ差分と同じものである保証が無くなる。

## 段階

初回は差分の実物から段階を選ぶ。上から順に当て、最初に当たった段階に決める。

| 段階 | 判定 |
|---|---|
| docs | 実行されるコード (ソース・script・hook・テスト・設定のうち実行時に読まれるもの) の差分が無い (文書・指示文だけの差分)。行数によらない |
| 標準 | 重要領域に触れる、または実行されるコードの file の insertions の合計が 100 行を超える |
| 軽量 | 上のどれにも当たらない |

行数は collect の `insertions` のうち、実行されるコードの file の値だけを足す。

重要領域は、決済・金銭計算 / 集計・数値計算 / 認証・認可 / 個人情報・秘匿情報 / 永続データの更新 (削除・migration 等の不可逆操作を含む) / 並行処理・共有状態 / 外部入力の境界。触れているかは差分の file 名と hunk を読んで判断する。

初回は、args で段階が指定されたらそれを使い、上の表による判定を飛ばす。重要領域に触れるかはその場合も判定し、触れていれば `段階:` 行の理由を `args 指定 (重要領域: <名前>)` にする。表の判定で重要領域に触れた巡も、判定理由の末尾に `(重要領域: <名前>)` を付ける (標準の巡では、「入力をそろえる」で攻撃役へ重要領域を渡すかと、再レビューの巡が `prev_critical_areas` で引き継ぐ値がこの判定で決まる)。再レビューの段階は resolve-range の `prev_stage` にする (巡ごとに段階を選び直すと、増分が小さいだけで 2 巡目が軽くなる)。

段階ごとに走らせる観点は次のとおり。「入力をそろえる」の script が `--stage` と `--round` からこの表を引いて、この巡と次巡の観点を返し、走らせない観点の節の `走らせず: <理由>` を `notes` に返す。

| 段階 | 初回 | 再レビュー (前巡の修正が作った増分) |
|---|---|---|
| docs | Spec + 原則 + 設計 | (来ない — SKILL.md 手順 5 の `再レビュー:` 行の値を参照) |
| 軽量 | 欠陥探し + adversarial verification + 原則 + Spec + 設計 | 欠陥探し + adversarial verification |
| 標準 | 欠陥探し + adversarial verification + 原則 + Spec + 設計 | 欠陥探し + adversarial verification + 原則 + 設計 |

## Spec 源

Spec 源の issue ref は `issue=` を最優先にし、無ければ collect の `issue_refs` の全部を使う — CL を出す前に呼ばれる経路では PR 本文がまだ無い。

Spec か adversarial verification を走らせる巡で issue ref があれば、[spec-fetch.md](spec-fetch.md) を Read し、ref ごとに issue の置き場を決める (本文は「入力をそろえる」でそろえる)。

Spec の入力は issue の body だけにする。「入力をそろえる」の script が返す `spec` の file がそれで、Spec reviewer・攻撃役の主張の出どころ・SKILL.md 手順 4 の反証者が同じ body を読む。title / labels / comments は入れない — 決定は本文が持ち、コメントの議論は受け入れ条件ではないので、コメントを混ぜると採られなかった案まで spec として照合される。

ref が無いとき、およびどの ref の本文も取得できなかったとき (tracker へ到達できない / issue が消えている) は Spec を走らせない。攻撃役は取得できた出どころで走らせる。一部の本文だけを取得できなかったときは、取得できた本文で Spec と攻撃役を走らせる。どの場合の注記も script の `notes` が返す (「入力をそろえる」)。

## 入力をそろえる

段階の判定の後、担い手を起動する前に、その巡で走らせる reviewer の観点と攻撃役の入力を script でそろえる。

設計の観点を走らせる巡 (「段階」の表) では、原則の索引 `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、索引が示す path から `principle-design-conformance` を Read する。差分の file に当たる設計 doc は `principle-design-conformance` の「設計 doc の置き場の引き方」で決める (差分が置き場の中の file を書き換えている行も、当たる行に数える)。当たる設計 doc が無ければ `--design-doc` の代わりに `--design-skip '<理由>'` (節が無い / 節が設計 doc を案内しない / 差分の file に当たる行が無い) を渡す (script は設計の prompt を作らず、`設計 skip:` を `notes` に返す) — 置き場の宣言は repo ごとに無いことが正常なので、止めずにこの観点だけを走らせない巡にする。

```bash
${CLAUDE_SKILL_DIR}/scripts/prepare-inputs.py prompts --dir <collect の dir> --stage <docs|軽量|標準> --round <resolve-range の round> \
  [--issue '[github:|gitlab:]<置き場>#<番号>' | --issue-file '<ref>=<本文の file>' | --issue-missing '<ref>=<理由>'] ... \
  [--design-doc '<置き場の path> | <その行の索引列の path (空なら空欄)>' ... | --design-skip '<理由>'] \
  [--critical-areas <重要領域の名前>] [--pr-body <PR・MR 本文の file>]
```

- `--issue` / `--issue-file` / `--issue-missing` の渡し分けは [spec-fetch.md](spec-fetch.md) に従い、ref ごとに 1 回渡す。Spec を走らせない再レビューの巡でも、攻撃役の出どころになるので ref があれば渡す
- `--critical-areas` は標準段階で重要領域に触れた巡だけ渡す (再レビューの巡は resolve-range の `prev_critical_areas`)。重要領域は主張の源ではなく攻める順と深さの指定で、script は主張の出どころの file に混ぜない
- `--pr-body` は args に `pr-body=` があるときだけ、その file を渡す

stdout は次の key を持つ JSON 1 つ。script が exit 非 0 で JSON を返さなかったときは、`prompts:` 行の reviewer の観点と `adversarial` を なし にし、それぞれの節の注記へ `入力を読めず: prepare-inputs が失敗: <stderr の 1 行目>` と書く (入力の要らない欠陥探しは止めない)。「段階」の表でこの巡に走らせない観点の節には、script の代わりに `走らせず: <理由>` (理由は段階と初回か再レビューか) を積む。

| key | 中身 |
|---|---|
| `perspectives_line` / `next_round_line` | 進行役の報告の `観点:` / `次巡:` 行の値 |
| `prompts` | 観点 (`principle` / `spec` / `design` / `adversarial`) → 組み立てた prompt の file の path。この巡で走らせない観点と、Spec の本文が 1 件も無い巡の `spec` と、`--design-skip` の巡の `design` は持たない |
| `spec` | issue の body の file の path。無ければ `null` |
| `claim_sources` | 攻撃役の主張の出どころの file の path。攻撃役を走らせない巡は `null` |
| `notes` | 観点 (上の 4 つと欠陥探しの `defect`) → その節の注記へそのまま積む行 (`走らせず:` / `設計 skip:` / `paths を読めず:` / `宣言を読めず:` / `宣言の file 不在:` / `Spec skip:` / `Spec 源欠落:` / `出どころ欠落:`) |

規約 (repo root と差分の file の祖先 dir の `CLAUDE.md`、`paths` が差分の file に当たるか `paths` を持たない `.claude/rules/`、対象 repo の `docs/agents/domain.md` の節「規約の置き場」が宣言した file) は script が集め、原則の prompt だけに載せる。

---

ここまで進めたら、進行役は SKILL.md 手順 2 の「進行役の報告」の形で返して終え、代行の呼び出し元はその値で担い手の起動へ進む。
