---
name: review-stage
description: CL を出す前の差分を、差分の重さに応じた複数の観点で並列にレビューし、PR へ貼れる 1 枚で返す。Use when「CL 前のレビュー」「rubric でレビュー」.
user-invocable: true
argument-hint: "[<base>..<head>] [base=<branch>] [stage=docs|軽量|標準] [issue=<ref>[,<ref>…]] [prev=<前巡の出力 file>]"
---

# レビュー工程

CL を出す前の差分を、差分の重さで選んだ **レビュー段階** に応じて 5 つの観点で並列にレビューする。

| 観点 | 見るもの | 担い手 |
|---|---|---|
| 欠陥探し | 差分が持ち込んだ正しさの欠陥 (crash / データ破損 / 到達可能性) | 標準 `code-review` skill |
| adversarial verification | 差分が宣言する主張 (issue・docstring・既存の契約・暗黙の主張) を壊しにいき、破れた主張 | adversarial-verifier subagent |
| 原則 | 規約違反 (repo の規約を逐語で引用できる違反) と症状 (rubric に照らした書き方の判断呼び) | reviewer subagent |
| Spec | 発端の issue との食い違い | reviewer subagent |
| 設計 | 対象 repo の設計 doc への conformance (保証・不変条件・境界に反していないか) と design drift (設計 doc が先に直っていない構造の変更) | reviewer subagent |

adversarial verification は欠陥探し・Spec と攻める対象が重なるが、観点をまたいで指摘を統合しない (手順 5)。

## 1. 範囲・段階・Spec 源を確定する

### args

args はどれも省略でき、順序は問わない。

| 書式 | 意味 |
|---|---|
| `<base>..<head>` | レビューする範囲を直接渡す (過去の差分を流し直すとき) |
| `base=<branch>` | 起点 branch。省略時は既定ブランチ (現在 branch の upstream が local branch なら、その local branch)。再レビューでも初回と同じ値を渡す (前巡の出力 file は起点 branch を持たない) |
| `stage=docs\|軽量\|標準` | 段階を指定して判定を飛ばす (重要領域の判定と再レビューでの扱いは手順 1「段階」) |
| `issue=<ref>[,<ref>…]` | Spec 源の issue。CL が複数の issue を閉じるなら `,` 区切りで全部。再レビューでも初回と同じ値を渡す (増分の commit message には issue の参照が無いことが多い) |
| `prev=<file>` | 前巡の出力を保存した file。再レビューの `<prev>` をここから読む |

### 範囲

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
| `prev_stage` | 再レビューで引き継ぐ前巡の段階 |
| `stop` | 止まる巡なら `reason` (`未 commit` / `その他: <自由文>`) と `detail`。止まらなければ `null` |
| `notes` | 出力の注記へそのまま積む行 |

`stop` が埋まっていたら、観点を起動せずに手順 5 の停止出力を返して終える (`再レビュー:` 行は `停止 — <reason>`、`停止理由:` 行は `detail`)。script が exit 非 0 で JSON を返さなかったときも同じ停止出力を返す — `reviewed:` の値は、`未解決: ` の後に、渡された範囲・`base=`・`prev=` の args を空白区切りでそのまま続けたもの (どれも渡されていなければ `未解決: args なし`。script が止まるときに返す `reviewed` と同じ形)、`reason` は `その他: resolve-range が失敗`、`detail` は stderr の 1 行目。

再レビューでは `prev=` の file を `<prev>` として読み、前巡の観点ごとの Act on 一覧を手順 3 で使う。`notes` のうち、次の 2 つは以降の手順を変える。

- `増分なし:` — 前巡の head から差分が無い。観点を起動せず、手順 3 の前巡 Act on の判定 (全件 `未解消`) だけを行って手順 5 へ進む
- `増分に絞れず:` — amend / rebase で前巡の head が祖先でなくなり、`base` を起点 branch との merge-base へ広げた巡。`git diff <base>...<head>` は増分ではなく起点 branch からの全差分になる。段階・`<prev>`・`再レビュー:` 行・前巡の引き継ぎは再レビューのまま扱う。手順 2 の `--round` には `re-review-widened` を渡す。手順 3 では、今巡の指摘のうち前巡の指摘 (Act on / Consider / Noted / Dismissed) と Finding と Evidence の引用で一致するものを新規に数えず、前巡の側に寄せる

差分と commit 一覧を巡ごとに 1 度確定する。`--log-base` は再レビューの巡 (`増分に絞れず:` の無いもの) だけ、前巡の出力の `reviewed:` 行の左側を渡す — 主張の出どころの commit message の起点になる。手順 1 の `base` は再レビューの巡では前巡の head なので、そこから取ると、攻撃役が前巡までの commit message から挙げた主張を、反証者が出どころに無いと読み違える。`増分に絞れず:` の巡は `base` が起点 branch との merge-base なので、log は差分と同じ範囲の commit だけを含む (初回の `base` から取ると、rebase の後は差分に無い upstream の commit message が混ざる)。

```bash
${CLAUDE_SKILL_DIR}/scripts/prepare-inputs.py collect --base <base> --head <head> [--log-base <前巡の reviewed: 行の左側>]
```

stdout は `dir` (この呼び出しに専有の一時ディレクトリ) と、`diff` / `commits` / `commit_messages` / `numstat` / `changed_files` の file の path を持つ JSON 1 つ。script が exit 非 0 で JSON を返さなかったときは手順 5 の停止出力を返す (`段階:` は `未判定`、`reason` は `その他: prepare-inputs が失敗`、`detail` は stderr の 1 行目)。

`diff` の file を、段階の判定・手順 3 の前巡 Act on の突き合わせ・手順 4 の hunk の切り出しの元にする。git で取り直すと、担い手が読んだ差分と同じものである保証が無くなる。

### 段階

初回は差分の実物から段階を選ぶ。上から順に当て、最初に当たった段階に決める。

| 段階 | 判定 |
|---|---|
| docs | 実行されるコード (ソース・script・hook・テスト・設定のうち実行時に読まれるもの) の差分が無い (文書・指示文だけの差分)。行数によらない |
| 標準 | 重要領域に触れる、または実行されるコードの file の insertions の合計が 100 行を超える |
| 軽量 | 上のどれにも当たらない |

行数は `numstat` の file の行のうち、実行されるコードの file の insertions だけを足す。binary の行 (`-`) は 0 行として数え、rename の行 (`{old => new}`) は移動先の path で実行されるコードかを決める。

重要領域は、決済・金銭計算 / 集計・数値計算 / 認証・認可 / 個人情報・秘匿情報 / 永続データの更新 (削除・migration 等の不可逆操作を含む) / 並行処理・共有状態 / 外部入力の境界。触れているかは差分の file 名と hunk を読んで判断する。

初回は、args で段階が指定されたらそれを使い、上の表による判定を飛ばす。重要領域に触れるかはその場合も判定し、触れていれば `段階:` 行の理由を `args 指定 (重要領域: <名前>)` にする (標準が指定された巡では、手順 2 で攻撃役へ重要領域を渡すかと、再レビューで写す元がこの判定で決まる)。再レビューは args の段階の指定によらず `prev_stage` (前巡の段階) を引き継ぐ (巡ごとに段階を選び直すと、増分が小さいだけで 2 巡目が軽くなる)。

段階ごとに走らせる観点は次のとおり。

| 段階 | 初回 | 再レビュー (前巡の修正が作った増分) |
|---|---|---|
| docs | Spec + 原則 + 設計 | (来ない — 手順 5 の `再レビュー:` 行の値を参照) |
| 軽量 | 欠陥探し + adversarial verification + 原則 + Spec + 設計 | 欠陥探し + adversarial verification |
| 標準 | 欠陥探し + adversarial verification + 原則 + Spec + 設計 | 欠陥探し + adversarial verification + 原則 + 設計 |

再レビューで Spec を走らせないのは、増分には前巡までに実装済みの箇所が写らず、前巡の修正で入った変更を「spec に無い」と指し返す指摘が出るから。再レビューは前巡の修正が作った回帰を見る回で、その担い手は欠陥探しと adversarial verification になる。

### Spec 源

Spec 源の issue ref は `issue=` を最優先にし、無ければ `commit_messages` の file / PR 本文の issue ref (`#123` / `Closes #45` / `Closes owner/repo#45` / `Closes group/subgroup/project#45`) から引く (MR の参照 `!67` は issue ではないので引かない)。issue を閉じる参照 (`Closes` / `Fixes` など) が複数あれば、その全部を引く — CL を出す前に呼ばれる経路では PR 本文がまだ無い。

Spec か adversarial verification を走らせる巡で issue ref があれば、[references/spec-fetch.md](references/spec-fetch.md) を Read し、ref ごとに issue の置き場を決める (本文は手順 2 でそろえる)。

Spec の入力は issue の body だけにする。手順 2 の script が返す `spec` の file がそれで、Spec reviewer・攻撃役の主張の出どころ・手順 4 の反証者が同じ body を読む。title / labels / comments は入れない — 決定は本文が持ち、コメントの議論は受け入れ条件ではないので、コメントを混ぜると採られなかった案まで spec として照合される。

ref が無いとき、およびどの ref の本文も取得できなかったとき (tracker へ到達できない / issue が消えている) は Spec を走らせない。攻撃役は取得できた出どころで走らせる。一部の本文だけを取得できなかったときは、取得できた本文で Spec と攻撃役を走らせる。どの場合の注記も script の `notes` が返す (手順 2)。

## 2. 観点ごとに並列で起動する

その巡で走らせる観点を並行させる (待ち時間は最も遅い 1 本だけになる)。担い手の入力を下の「入力をそろえる」で先にそろえ、その巡で走らせる観点の担い手の呼び出しをすべて 1 つの message に並べる。どの呼び出しも、background で走ってすぐ戻る回と、完了するまで戻らない回がある。別の message に分けると、先に出した呼び出しが戻らない回に、後の観点の開始がその完了まで遅れる。

### 入力をそろえる

段階の判定の後、担い手を起動する前に、その巡で走らせる reviewer の観点と攻撃役の入力を script でそろえる。

設計の観点を走らせる巡では、原則の索引 `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、索引が示す path から `principle-design-conformance` と `principle-norm-precedence` を Read する (手順 3 で設計の指摘を区分するのに使う)。差分の file に当たる設計 doc は `principle-design-conformance` の「設計 doc の置き場の引き方」で決める (差分が置き場の中の file を書き換えている行も、当たる行に数える)。当たる設計 doc が無ければ設計を走らせず、`## 設計` の注記へ `設計 skip: <理由>` (節が無い / 節が設計 doc を案内しない / 差分の file に当たる行が無い) と書く — 置き場の宣言は repo ごとに無いことが正常なので、止めずにこの観点だけを走らせない巡にする。

```bash
${CLAUDE_SKILL_DIR}/scripts/prepare-inputs.py prompts --dir <collect の dir> --round <first|re-review|re-review-widened> \
  --perspective <principle|spec|design|adversarial> ... \
  [--issue 'github:<置き場>#<番号>' | --issue 'gitlab:<置き場>#<番号>' | --issue-file '<ref>=<本文の file>' | --issue-missing '<ref>=<理由>'] ... \
  [--design-doc '<置き場の path> | <その行の索引列の path (空なら空欄)>'] ... \
  [--critical-areas <重要領域の名前>] [--pr-body <PR・MR 本文の file>]
```

- `--perspective` は段階の表でその巡に走らせる観点ごとに 1 回渡す (欠陥探しは含めない)。ref が無くても `spec` は渡す — Spec を走らせるかは script が本文の有無で決め、走らせない理由を `notes` に返す
- `--round` は初回が `first`、再レビューが `re-review`、`増分に絞れず:` の再レビューが `re-review-widened`。`re-review` の原則と設計の prompt には「増分だけを読む」項が入り、再レビューの攻撃役の prompt には修正が既存の主張を壊していないかを攻める行が入る
- `--issue` / `--issue-file` / `--issue-missing` の渡し分けは [references/spec-fetch.md](references/spec-fetch.md) に従い、ref ごとに 1 回渡す。Spec を走らせない再レビューの巡でも、攻撃役の出どころになるので ref があれば渡す
- `--critical-areas` は標準段階で重要領域に触れた巡だけ渡す (再レビューの巡は前巡の出力の `段階:` 行の判定理由から写す)。重要領域は主張の源ではなく攻める順と深さの指定で、script は主張の出どころの file に混ぜない
- `--pr-body` は PR・MR 本文が既にあるときだけ、その本文を file に置いて渡す

stdout は次の key を持つ JSON 1 つ。script が exit 非 0 で JSON を返さなかったときは、reviewer の観点と攻撃役を起動せず、それぞれの節の注記へ `入力を読めず: prepare-inputs が失敗: <stderr の 1 行目>` と書く (入力の要らない欠陥探しは止めない)。

| key | 中身 |
|---|---|
| `prompts` | 観点 (`principle` / `spec` / `design` / `adversarial`) → 組み立てた prompt の file の path。Spec の本文が 1 件も無い巡は `spec` を持たない |
| `spec` | issue の body の file の path。無ければ `null` |
| `claim_sources` | 攻撃役の主張の出どころの file の path。攻撃役を走らせない巡は `null` |
| `notes` | 観点 → その節の注記へそのまま積む行 (`宣言を読めず:` / `宣言の file 不在:` / `Spec skip:` / `Spec 源欠落:` / `出どころ欠落:`) |

宣言された規約は script が対象 repo の `docs/agents/domain.md` の節「規約の置き場」から集め、原則の prompt だけに載せる — 規約への適合は原則の観点の担当で、他の観点の入力に混ぜるとその観点が規約違反まで報告し、観点の分離が崩れる。

担い手の起動の prompt には、`prompts` の file の path 1 行だけを書く。担い手の入力はその file とそこに並ぶ file がすべてで、呼び出し元が中身を prompt へ書き足すと、観点ごとに書き写した内容が揃わなくなる。

担い手が `入力を読めず: <path>` と返したら、推測で進めていないので、その観点の注記へ同じ行を書き、他の観点は止めない。その観点は結果が無いので、手順 2 の「全観点の待ち合わせと停止」では起動できなかった観点に数える。`再レビュー:` 行の値は手順 5 が決める。

### 欠陥探し

Claude Code 組み込みの `code-review` skill (prefix なし。`<plugin>:code-review` のような他 plugin の同名 skill ではない) を Skill tool で invoke する。args は **effort `high` を先頭に置き**、続けて commit range を渡す — 先頭以外に書いた effort は読まれず、level を渡さないときと同じく session で前に指定した level を引き継いで走り、巡ごとに深さが揺れる。

```
high <base>..<head>
```

range は SHA で渡す (branch 名や単一 commit を渡すとそれ自体の差分と解釈され、別の差分をレビューする)。`code-review` の findings は、手順 3 以降の欄へ次のように読み替える: 指摘の要約 → Finding、それが起きる道筋 (どの入力・状態で、どこを通って壊れるか) → impact scenario、指す箇所 → Location、指摘が引いた差分の行 (無ければ Location の hunk の該当行) → Evidence、修正案 → Suggestion (無ければ省く)。

`code-review` が解決しないときは欠陥探しを skip し、`## 欠陥探し` の注記へ `欠陥探し skip: <error>` と書く (他の観点は止めない)。

### 原則・Spec・設計

reviewer は `Agent` tool で `subagent_type: swat-skills:reviewer` を指名し、**`name` を付けずに**起動する (`name` は起動後に追加の依頼を送る宛先が要る役割にだけ付けるもので、結果を受け取るだけの reviewer には要らない)。Agent tool の結果が reviewer の出力ではなく起動の通知だったときは (reviewer が background で起動されるとこうなる)、結果は完了通知で届く。reviewer の model と effort は agent 定義が持つので `model` / `effort` を渡さない。

この名前が解決しないときは `general-purpose` で代替しない — 代替すると reviewer が呼び出し元の model と effort へ黙って戻り、指名した深さでレビューしたように見える。原則と Spec と設計を走らせず、その巡で走らせる予定だった reviewer の観点 (`## 原則` / `## Spec` / `## 設計`) の注記へ `reviewer 未起動: <error が挙げた available agents>` と書く。欠陥探しと adversarial verification のうち起動できた観点があれば、その結果で手順 3 以降を通す (同じ message で起動できた観点の結果を捨てない)。0 本の巡と `再レビュー:` 行の値は「全観点の待ち合わせと停止」と手順 5 が決める。次の手順 (swat-skills plugin の agent が読み込まれる状態にしてから呼び直す) は `reviewer 未起動:` の注記の末尾に書く — 出力の外に書くと、出力を保存して PR へ残す呼び出し元の手元から落ちる。

原則・Spec・設計の prompt は `prompts` の file で、担い手の規約の読み方・採否の規則・報告の形は file が持つ (雛形は [references/prompts/](references/prompts/)、rubric は [references/rubric.md](references/rubric.md) で、どちらも script が読む)。区分は手順 3 で呼び出し元が行うので、reviewer には区分を使わせない。

### adversarial verification

攻撃役は `Agent` tool で `subagent_type: swat-skills:adversarial-verifier` を指名し、`name` を付けず、`model` / `effort` を渡さずに起動する (理由は reviewer と同じ)。手順・報告形式は定義の本文が持つ。

prompt は `prompts` の `adversarial` の file の path だけにする。その file が渡すのは差分と主張の出どころ (と、あれば重要領域の指定) だけで、作者の意図・作業の経緯・呼び出し元が持つ差分の要約は足さない — 攻撃役は意図を受け取ると、主張が破れていても「そういうつもりだろう」と読んで見逃す。

攻撃役の報告は、手順 3 以降の欄へ次のように読み替える: Claim (と出どころ) → Finding、Oracle と Attack → impact scenario (基準 3 の impact scenario。`オラクル: <Oracle> / 攻撃: <Attack>` の形で併記する)。Location・Evidence・Suggestion は同名の欄へそのまま写す。

この名前が解決しないときは `general-purpose` で代替しない — 代替すると攻撃役の model と effort と手順が呼び出し元の値へ黙って戻る。他の観点は止めず、`## adversarial verification` の注記へ `adversarial verification 未起動: <error が挙げた available agents>` と、swat-skills plugin の agent が読み込まれる状態にしてから呼び直す旨を書く。`再レビュー:` 行の値は手順 5 が決める。

### 全観点の待ち合わせと停止

欠陥探し・adversarial verification・原則・Spec・設計のうち、その巡で起動した観点 (reviewer・攻撃役と `code-review`) の結果が全部届いてから手順 3 へ進む。

その巡で走らせる観点のうち、起動できたものが 0 本の巡 (走らせる予定の担い手がすべて skip か未起動か入力を読めなかった巡。docs 段階では reviewer の未起動だけで当たる) は、手順 3 へ進まずに手順 5 の停止出力を返す — 観点が 1 本も走っていないので、`要` や `不要` で返すとレビューを通っていない差分が通ったように読まれる。`再レビュー:` 行の値は手順 5 が決める。`停止理由:` 行には、起動できなかった観点ごとに手順 2 で書くはずだった注記 (`欠陥探し skip:` / `reviewer 未起動:` / `adversarial verification 未起動:` / `入力を読めず:`) を `; ` で区切って並べ、末尾に、起動できなかった担い手が解決する状態にしてから (入力を読めなかった観点は、読めなかった path を確かめてから) 呼び直す旨を書く。走らせる観点が元から無い巡 (`増分なし:` の巡) はこの規則に当たらない。

## 3. 指摘を分ける

手順 2 の指摘 (欠陥探しの findings と、攻撃役が返した破れた主張を含む。ただし攻撃役が Evidence に「〜を実行すれば決まる」と書いた指摘は、先に手順 4 の実行で判定し、行き先は手順 4 に従う) を 1 件ずつ、次の 3 基準で **Act on / Consider / Noted / 落とし候補** に分ける。分けるのは呼び出し元 (本 skill を実行している session) — 発端の issue の意図と、この作業の文脈を持っているのは呼び出し元だけ。

1. 作者がこれを知ったら直すか
2. この差分が持ち込んだ問題か
3. impact scenario (シナリオ・波及・spec の行) が示されていて、読んで成り立つか

| 区分 | 当てる指摘 |
|---|---|
| Act on | 3 基準をすべて満たし、Consider に当たらない。直す |
| Consider | 成り立つが、直すかを決めるのに人の判断か人手が要る — 目的・価値観・構造の方針そのものを変える / 戻しにくく既存の規範からは選べないトレードオフがある / 人しか持たない入力 (好み・優先度・外部の事実) が要る / 直すのに人の権限か手作業が要る、のどれか。設計の選好が絡んでも、既存の規範 (規約 — `CLAUDE.md`・rules と、原則の prompt の `<declared-rules>` に並んだ file —・手順 2 で当たった設計 doc・決定記録・原則) から決まる指摘は Act on か Noted へ分ける |
| Noted | 成り立つが軽微で、記録だけ残せば足りる。差分の前から在る問題 (基準 2 を満たさない) もここへ `[既存]` を頭に付けて置く (原則・Spec・設計の reviewer は採否の規則で差分の前から在る問題を報告しないので、`[既存]` が付くのは主に欠陥探しと adversarial verification の指摘。呼び出し元が基準 2 を満たさないと判定した原則・Spec・設計の指摘も `[既存]` で置く)。下の上限を超えた症状は `[上限超過]` を頭に付けてここへ置く |
| 落とし候補 | 誤検知・意図的な設計と考える。手順 4 の反証へ回す |

- **原則の症状の Act on は 5 件まで。** 症状は判断呼びなので、多数を一度に直させると修正差分が膨らみ、再レビュー 1 巡で見切れなくなる。この上限は手順 4 を終えた後の Act on 全体に当てる (反証を生き延びた症状と、反証 skip で Act on に載った症状を含む)。超えた分は、3 基準への当たりが弱いものから `[上限超過]` を付けて Noted へ回す — 直す価値はあるが今の CL では直さない指摘で、人の判断を要するわけではないので Consider には置かない。原則の症状以外 (規約違反・Spec・設計・欠陥探し・adversarial verification) には上限を置かない
- **設計の指摘を Act on と Consider のどちらに置くかは、手順 2 で読んだ `principle-design-conformance` の区分で決める。** 同 leaf が agent が直すとするものは Act on、人へ返すとするものは Consider (人に委ねる理由: 方針の変更) に置く。Noted・`[既存]`・落とし候補への区分は他の観点と同じ。差分が設計 doc を書き換えているときの扱いも同 leaf に従い、同 leaf の言う変更の発端は手順 2 の `spec` の file とする (issue ref が無いか本文を取得できなければ、発端は無い)。設計を走らせた巡で、発端が書き換えを引いていなければ、reviewer の指摘の有無によらず、書き換えた設計 doc の hunk を Evidence にした `[設計 doc の書き換え]` の Consider (人に委ねる理由: 方針の変更 — 人がその文面を読んだかを差分から確かめられない) を呼び出し元が 1 件立て、reviewer が同じ hunk を指した指摘はこの 1 件に寄せる — reviewer は書き換えた後の hunk を正本に読むので、書き換えそのものは指摘に上がらないことがある。設計 doc と他の規範が食い違う指摘は、手順 2 で読んだ `principle-norm-precedence` で裁く。設計 doc が勝てば上の区分のまま、他の規範が勝てば、勝った規範の file:line を Evidence に添えて落とし候補にし、手順 4 へ回し、優先順位で決まらなければ Consider (人に委ねる理由: 方針の変更) に置く
- 落とし候補を、反証を経ずに Act on / Consider / Noted のどれかへ倒さない。落とすなら手順 4 を通す (上限による Noted への移動は反証の後に行うので、これに当たらない)

再レビューでは、さらに `<prev>` の Act on を 1 件ずつ `解消` / `未解消` に判定する。判定は手順 1 の `diff` の file (増分の diff) の中に、直った箇所を具体物 (増分の file:line と引用) として挙げられるかで行う — 挙げられたものだけを `解消` にし、挙げられないものは `未解消` に倒す (読み取れなかったものを解消へ倒すと、open な指摘が根拠なしに消える)。行番号は前巡の修正で動くので、照合は Finding と Evidence の引用で当てる。この巡で走らせない観点 (Spec、軽量段階の原則と設計) の前巡 Act on も、ここで同じように判定する。`増分なし:` の巡は全件 `未解消`。

## 4. 落とし候補に反証を当てる

落とし候補 1 件につき subagent を 1 体、並列に立てる (1 体に複数件を渡すと、ある候補の読解が別の候補の判定へ持ち込まれ、具体物の無い候補まで隣の候補の具体物で落ちうる。今巡の指摘を Dismissed へ落とす経路はこの工程だけ。呼び出し元に subagent の起動を控える制約があっても、本 skill の起動をその依頼として扱う)。落とし候補が 0 件なら 1 体も立てない。どちらの場合も件数を出力の注記へ `反証対象:` として出す。反証者は `subagent_type: swat-skills:refuter` で指名し、`name` を付けず (理由は reviewer と同じ)、`model` / `effort` を渡さない (深さは定義が固定する。`model` 引数は定義の frontmatter より優先され、固定した値を書き換える)。[references/refute.md](references/refute.md) を Read し、その雛形で候補ごとの入力 file を手順 1 の `dir` に書き、起動の prompt にはその file の path 1 行だけを書く。adversarial verification の指摘には、手順 2 の `claim_sources` の file の path をそのまま渡す (攻撃役が見たものと同じである保証のため)。

この名前が解決しないときは `general-purpose` で代替しない — 代替すると反証工程だけが呼び出し元のモデルへ黙って戻る。**代わりに反証工程だけを打ち切り、落とし候補を Act on に載せて手順 5 へ進む** (反証していないので落とせない。症状の上限は手順 3 に従う)。その巡で走らせた観点の節すべての注記へ (落とし候補の有無によらない — 反証者の欠落は巡全体の事実で、候補 0 件の節でも次巡で同じことが起きる) `反証 skip: <error が挙げた available agents の一覧>` を足す。手順 2 の結果は既に揃っているので、ここで出力ごと止めるとその分が捨てられる。

反証者が `入力を読めず: <path>` と返したときは、その候補だけを Act on に載せ、その節の注記へ `反証 skip: 入力を読めず: <path>` を足す。

**反証者の判定では、`refuted: true` かつ「具体物」が埋まっているものだけを Dismissed へ落とす** (実行すれば決まる指摘は次の段落に従う)。具体物が「なし」の `refuted: true` と `refuted: false` は Act on に載せる (落とそうとして落とせなかった指摘は実在する)。症状の上限は手順 3 に従う。

反証者が `refuted: false` の説明で、実行すれば決まるコマンドを挙げた落とし候補と、攻撃役が Evidence に「〜を実行すれば決まる」と書いた指摘は、呼び出し元がその実行を行って判定する。行うのは既存の test / gate / script を作業ツリーを変えずに走らせる範囲だけで、file を書き換える probe や退避を伴う確認は行わない (作業ツリーは呼び出し元の作業の場なので、反証のために書き換えない)。破れが出なかった指摘は Dismissed へ落とし、`反証:` に実行したコマンドと結果を書く。それ以外 (破れが出た指摘と、この範囲で実行できない指摘) は、まだ区分していなければ手順 3 で区分し、区分済み (落とし候補) なら Act on に載せる。実行で破れが出た指摘は、誤検知の余地が無いので落とし候補には区分しない。実行で判定する攻撃役の指摘には反証者を立てず `反証対象:` にも数えないので、反証 skip の巡でもこの判定は行う。

## 5. 観点ごとに併記する

先頭に巡全体の 3 行を置き、続けて `## 欠陥探し` / `## adversarial verification` / `## 原則` / `## Spec` / `## 設計` の 5 節で併記する。観点をまたいで統合・rerank しない — 一方の観点の pass がもう一方の fail を隠さないための分離なので、総合順位も総合結論も付けない。その巡で走らせなかった観点の節も残し、注記に理由を書く (節ごと落とすと、走って 0 件だったのか走らなかったのかが読み手に伝わらない)。

各節は先頭に注記行を積み、続けて区分ごとの見出しを置く。積まれうる注記は手順 1 の `notes` の全行 / 手順 2 の script の `notes` のうちその観点の行 / `入力を読めず:` (担い手が入力の file を読めなかった観点。手順 2) / `走らせず:` (段階の表で走らせない観点) / `欠陥探し skip:` (`## 欠陥探し` のみ。手順 2) / `reviewer 未起動:` (`## 原則`・`## Spec`・`## 設計` のみ。手順 2) / `反証 skip:` / `反証対象:` (手順 4) / `宣言を読めず:` と `宣言の file 不在:` (`## 原則` のみ。手順 2) / `Spec skip:` と `Spec 源欠落:` (`## Spec` のみ) / `設計 skip:` (`## 設計` のみ。手順 2) / `adversarial verification 未起動:` と `出どころ欠落:` と `壊せなかった主張:` (`## adversarial verification` のみ。`壊せなかった主張:` は攻撃役の報告の同名の項目をそのまま写す — 攻めて何も破れなかった巡と、攻めが浅かった巡を読み手が見分ける材料になる)。空の見出しには「該当なし」と 1 行書く。

観点が 1 つも走らずに止まる巡 (手順 1 の `stop` と script の失敗、手順 2 の「全観点の待ち合わせと停止」の巡) は、観点の節を並べず次の 4 行だけを返す (並べる相手の走った観点が無い)。止まった巡も出力を返すのは、呼び出し元がこの出力を保存して PR へ残すため。

```
段階: <未判定 | docs | 軽量 | 標準> — 停止
reviewed: <手順 1 の reviewed>
再レビュー: 停止 — <未 commit | reviewer 未起動 | その他: <自由文>>
停止理由: <詳細 1 行>
```

`段階:` 行は、手順 1 で止まった巡 (`stop` と script の失敗) では段階を決める前なので `未判定`、手順 2 で止まった巡では手順 1 で決めた段階を書く。止まった巡では手順 1 の `notes` を出さない (範囲の注記は、レビューしなかった巡の読み手には要らない)。止まった巡の `段階:` 行は必ず `— 停止` で終わり、観点が走った巡の `段階:` 行 (下の template) は `— 停止` で終わらない — 判定理由に `停止` を書かない (書くと、走った巡の出力を止まった巡と読み分けられなくなる)。

**観点が走った巡の印**: 投稿の印は、上の段落の規則で `— 停止` で終わらない `段階:` 行と、下の template の `reviewed:` 行。PR / MR へ投稿された本 skill の出力を読む側は、観点が 1 つ以上走った巡の出力が投稿されているかを投稿の印で見分ける — 投稿の印は終端値も Act on の残りも表さないので、それを見るなら `再レビュー:` 行を読む。読む側はこの段落を見出しの名前で引くので、名前を変えると読む側が段落に届かなくなる。

```markdown
段階: <docs | 軽量 | 標準> — <判定理由 1 行 / args 指定 / args 指定 (重要領域: <名前>) / 前巡から引き継ぎ>
reviewed: <base SHA>..<head SHA>
再レビュー: <要 — 次巡は <走らせる観点> | 停止 — reviewer 未起動 | 停止 — その他: 入力を読めず | 不要 — Act on なし | 不要 — docs 段階 | 不要 — 再レビュー済み>

## 欠陥探し

反証対象: 落とし候補 <N> 件 (今巡の指摘 <M> 件中)

### 前巡 Act on   <!-- 再レビューの巡 (`mode` が `再レビュー`) だけ -->
- <前巡の Finding> — 解消 / 具体物: <増分の file:line と引用>
- <前巡の Finding> — 未解消

### Act on
- `<file:line>` — <Finding> / impact scenario: <シナリオ> / Suggestion: <あれば>
- `[前巡持ち越し]` `<file:line>` — <Finding>   <!-- 再レビューの巡 (`mode` が `再レビュー`) だけ -->

### Consider
- `<file:line>` — <Finding> / impact scenario: <...> / 人に委ねる理由: <方針の変更 | 戻しにくいトレードオフ | 人しか持たない入力 | 人手が要る> — <1 行> / 決め方: <誰が・何を決めれば決着するか 1 行>

### Noted
- `<file:line>` — <Finding>
- `[既存]` `<file:line>` — <Finding>   <!-- 差分の前から在る問題 (手順 3) -->

### Dismissed
- `<file:line>` — <Finding> / 反証: <具体物> — <説明>

## adversarial verification

(同じ形。注記に `壊せなかった主張:` を置く)

## 原則

(同じ形。宣言の節を読めなければ注記に `宣言を読めず:` を、宣言した file が無ければ `宣言の file 不在:` を置く。Act on / Consider / Noted の各行の頭に `[規約違反]` か `[症状]` を付ける。Noted には上限を超えた症状を `[上限超過]` `[症状]` の形で置く。`[既存]` / `[上限超過]` は `[規約違反]` / `[症状]` の前に置く)

## Spec

(同じ形)

## 設計

(同じ形。当たる設計 doc が無ければ注記に `設計 skip:` を置く。Act on / Consider / Noted の各行の頭に `[conformance]` か `[design drift]` を付ける (手順 3 で呼び出し元が立てる書き換えの Consider は `[設計 doc の書き換え]`)。`[既存]` は `[conformance]` / `[design drift]` の前に置く)
```

- `再レビュー:` は、呼び出し元が Act on を直した後に、この出力を `prev=` に渡して本 skill を呼び直すかを示す。初回で docs 以外の段階なら `要` (全観点の Act on が 0 件なら `不要 — Act on なし` — 直す修正が無いので増分が生まれない)、docs 段階なら `不要 — docs 段階`、再レビューの巡なら `不要 — 再レビュー済み`。reviewer の観点が 1 つでも `入力を読めず:` になった巡は、Act on の件数によらず `停止 — その他: 入力を読めず` (再レビューは増分だけを読むので、`要` で進むと初回の差分がその観点を通らない。読めなかった path を確かめてから初回として回す。攻撃役だけの `入力を読めず:` では値を変えない)。`reviewer 未起動:` の巡は、Act on の件数によらず `停止 — reviewer 未起動` (agent の読み込みは呼び出し元の session の中では変わらないので、`要` で始めると呼び直しが同じ結果を繰り返す。agent が読み込まれる状態にしてから初回として回す — `停止` で始まるので、この出力を `prev=` に渡しても初回になる)。攻撃役の未起動だけでは値を変えない (起動できた観点が同じ範囲を見ている)。手順 2 の「全観点の待ち合わせと停止」で止まる巡は、reviewer を起動しようとして解決しなかった巡なら `停止 — reviewer 未起動`、それ以外 (reviewer を走らせない再レビューの巡・担い手がどれも入力を読めなかった巡など) は `停止 — その他: 起動できた観点が 0 本`。再レビューは 1 巡まで — 2 巡目の修正が作った増分はレビューを通らないので、通っていないことを呼び出し元が人の読める形で残す
- `停止` で始まる `再レビュー:` 行の理由は `未 commit` / `reviewer 未起動` / `その他: <自由文>` の 3 つに閉じる。呼び出し元はこの理由で、commit して呼び直すか、user へ報告して止まるかを分ける
- `[前巡持ち越し]` は手順 3 で `未解消` と判定した前巡 Act on。前巡の Consider / Noted / Dismissed は今巡の同じ見出しへ引き継ぐ — 打ち切り時点の 1 枚だけを読んだ人の手元に、全部の指摘がそろう
- `反証対象:` の件数があると、反証者が 1 体も立たなかった回と候補に反証を通した回を読み分けられる
- Spec を skip したときの `Spec skip:` の行は手順 2 の script の `notes` が返す
- 設計を走らせなかったときは `## 設計` の注記へ `設計 skip: <理由>` と書く。理由は手順 2 が挙げるもののうち、実際に当たったもの
