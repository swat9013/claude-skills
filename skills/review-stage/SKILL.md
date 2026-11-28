---
name: review-stage
description: CL を出す前の差分を、差分の重さに応じた複数の観点で並列にレビューし、PR へ貼れる 1 枚で返す。Use when「CL 前のレビュー」「rubric でレビュー」.
user-invocable: true
argument-hint: "[<base>..<head>] [base=<branch>] [stage=docs|軽量|標準] [issue=<ref>[,<ref>…]] [prev=<前巡の出力 file>] [pr-body=<PR・MR 本文の file>]"
---

# レビュー工程

CL を出す前の差分を、差分の重さで選んだ **レビュー段階** に応じて 5 つの観点で並列にレビューする。

| 観点 | 見るもの | 担い手 |
|---|---|---|
| 欠陥探し | 差分が持ち込んだ正しさの欠陥 (crash / データ破損 / 到達可能性) | 標準 `code-review` skill |
| adversarial verification | 差分が宣言する主張 (issue・docstring・既存の契約・暗黙の主張) を壊しにいき、破れた主張 | adversarial-verifier subagent |
| 原則 | 規約違反 (repo の規約を逐語で引用できる違反) と症状 (rubric に照らした書き方の判断呼び) | reviewer subagent |
| Spec | 発端の issue との食い違い | reviewer subagent |
| 設計 | 対象 repo の設計 doc への conformance (保証・設計規則・境界に反していないか) と design drift (設計 doc が先に直っていない構造の変更) | reviewer subagent |

## 進行の置き場

範囲・段階・Spec 源の確定と入力の準備 ([references/runner.md](references/runner.md)) は 1 巡ごとに立てる **進行役** (subagent) が進め、手順 2 の担い手の起動から手順 5 までは進行役を起動した呼び出し元が進める。範囲と入力の準備は呼び出し元の作業の経過を使わないので、呼び出し元の context で進めると、その経過を tool 呼び出しのたびに読み直すことになる。担い手は呼び出し元が起動する — 進行役が起動すると、担い手の報告を進行役が書き写して返すことになり、長い書き写しは途中で止められることがある。区分には作業の文脈が要るので呼び出し元に残す。

args に `runner` があれば、自分が進行役なので [references/runner.md](references/runner.md) を Read して「範囲」から「入力をそろえる」までを進め、手順 2 の「進行役の報告」を返して終える (担い手は起動しない)。無ければ自分が呼び出し元なので、PR・MR 本文が既にあれば file に置いて args に `pr-body=<file>` を足し (進行役は PR の有無を知らない)、`Agent` tool で `subagent_type: general-purpose` を指名して進行役を 1 体起動する。`name` を付けず (結果を受け取るだけの役割)、`isolation` を付けず (進行役は呼び出し元の作業ツリーの commit を読む)、`model` / `effort` を渡さず、prompt は次の 1 行だけにする — 作業の経緯を足すと、担い手へ渡らないはずの作者の意図が、進行役が組み立てる入力へ漏れる。

```
Skill tool で `swat-skills:review-stage` を args `runner <受け取った args>` で invoke し、references/runner.md の「範囲」から「入力をそろえる」までを進めて手順 2 の「進行役の報告」を返す。
```

進行役の最終報告の本文は、手順 5 の停止出力か「進行役の報告」の形をとる。本文の確かめ方と待ち方は、手順 2 の「全観点の待ち合わせと停止」の観点の報告と同じにする。

- 手順 5 の停止出力が届いたら、それを本 skill の出力にして終える
- 「進行役の報告」の形が届いたら、手順 2 の担い手の起動へ進む
- 上の 2 つのどちらにも当たらないとき (起動できなかった・途中経過の文で終わった・完了通知が届いた後に tool の戻り・`<result>`・別の message のどれにも本文が無く走っている tool 呼び出しも残っていない) は、[references/runner.md](references/runner.md) を Read してその「範囲」から「入力をそろえる」までを自分の context で進めて担い手の起動へ進み、手順 5 の全節の注記の先頭に `進行役 未完了: <起きたこと>` を足す。その巡が止まるとき (「範囲」で止まる巡と、手順 2 の「全観点の待ち合わせと停止」で止まる巡) は、停止出力を組む `render-output.py` に `--runner-incomplete <起きたこと>` を足す (`停止理由:` の値の頭に `進行役 未完了: <起きたこと>;` が付く) — 費用が増えても、レビューを落とさない側を取る

## 1. 範囲・段階・Spec 源を確定する

### args

args はどれも省略でき、順序は問わない。

| 書式 | 意味 |
|---|---|
| `<base>..<head>` | レビューする範囲を直接渡す (過去の差分を流し直すとき) |
| `base=<branch>` | 起点 branch。省略時は既定ブランチ (現在 branch の upstream が local branch なら、その local branch。その local branch に upstream があれば、その upstream)。再レビューでも初回と同じ値を渡す (前巡の出力 file は起点 branch を持たない) |
| `stage=docs\|軽量\|標準` | 段階を指定して判定を飛ばす (重要領域の判定と再レビューでの扱いは [references/runner.md](references/runner.md)「段階」) |
| `issue=<ref>[,<ref>…]` | Spec 源の issue。CL が複数の issue を閉じるなら `,` 区切りで全部。再レビューでも初回と同じ値を渡す (増分の commit message には issue の参照が無いことが多い) |
| `prev=<file>` | 前巡の出力を保存した file。再レビューの `<prev>` をここから読む |
| `pr-body=<file>` | 既にある PR・MR の本文を置いた file。Spec 源と攻撃役の出どころになる |
| `runner` | 自分が進行役である印 (節「進行の置き場」)。進行役の起動の prompt だけが付け、本 skill を呼ぶ側は書かない |

範囲・段階・Spec 源を確定する手順は [references/runner.md](references/runner.md) の「範囲」「段階」「Spec 源」が持ち、進行役と代行の枝だけが Read する。その手順が起動する script は `${CLAUDE_SKILL_DIR}/scripts/resolve-range.py`・`${CLAUDE_SKILL_DIR}/scripts/prepare-inputs.py` (`collect` / `prompts`)・`${CLAUDE_SKILL_DIR}/scripts/render-output.py` の 3 本 — 配布先の permission の提案は SKILL.md 本文から script を列挙するので、runner.md だけに置くと進行役が script を呼ぶたびに permission の確認で止まる。

## 2. 観点ごとに並列で起動する

呼び出し元は、「進行役の報告」の規則で起動する担い手の呼び出しをすべて 1 つの message に並べる — 呼び出しは完了するまで戻らない回があり、別の message に分けると、後の観点の開始がその完了まで遅れる。

### 進行役の報告

進行役の最終報告は、手順 5 の停止出力か次の形のどちらかだけにする — 「準備した」「これから起動する」といった途中経過を最終報告にすると、そこで進行役が終わり、巡が止まる。進行役は担い手を起動せず、この報告を返して終える。

```markdown
mode: <「範囲」の mode>
段階: <手順 5 の `段階:` 行の値>
reviewed: <「範囲」の reviewed>
paths: dir=<path> diff=<path> commit_messages=<path> spec=<path か なし> claim_sources=<path か なし>
design-doc: <「入力をそろえる」で `--design-doc` に渡した行。行ごとに `design-doc:` を繰り返す。無ければ なし>
観点: <「入力をそろえる」の `perspectives_line` (script が失敗した巡は、runner.md「段階」の表でこの巡に走らせる観点)。`増分なし:` の巡は なし>
次巡: <「入力をそろえる」の `next_round_line` (script が失敗した巡は、同じ表の次巡の観点)。`増分なし:` の巡は なし>
prompts: principle=<path か なし> spec=<path か なし> design=<path か なし> adversarial=<path か なし>

## 欠陥探し

<「範囲」「段階」「入力をそろえる」でその節へ積む注記。無ければ なし>

## adversarial verification

(同じ形)

## 原則

(同じ形)

## Spec

(同じ形)

## 設計

(同じ形)
```

呼び出し元は、`観点:` 行の観点のうち、欠陥探しと、`prompts:` 行に path がある観点の担い手を起動する。`prompts:` 行に path が無い観点は起動しない (理由はその節の注記にある)。

`増分なし:` の巡 (`観点:` 行が なし) は担い手を起動せず、手順 3 の前巡 Act on の判定 (全件 `未解消`) だけを行って手順 5 へ進む。

担い手の起動の prompt には、`prompts:` 行の file の path 1 行だけを書く。担い手の入力はその file とそこに並ぶ file がすべてで、起動する側が中身を prompt へ書き足すと、観点ごとに書き写した内容が揃わなくなる。

担い手が `入力を読めず: <path>` と返したら、推測で進めていないので、その観点の注記へ同じ行を書き、他の観点は止めない。その観点は結果が無いので、手順 2 の「全観点の待ち合わせと停止」では起動できなかった観点に数える。`再レビュー:` 行の値は手順 5 が決める。

### 欠陥探し

`Agent` tool で `subagent_type: general-purpose` を指名し、`name` を付けず、`isolation` を付けず (呼び出し元の作業ツリーの commit を読む)、`model` / `effort` を渡さずに subagent を 1 体起動する — 呼び出し元の context で `code-review` を invoke すると、その tool 呼び出しが作業の経過を毎回読み直す。prompt は次の 1 行だけにする。

```
Skill tool で Claude Code 組み込みの `code-review` skill (prefix なし。`<plugin>:code-review` のような他 plugin の同名 skill ではない) を args `high <base>..<head>` で invoke し、findings を省かずに最終報告として返す。file は書き換えない。invoke できなければ、error をそのまま返す。
```

args は **effort `high` を先頭に置き**、続けて commit range を渡す — 先頭以外に書いた effort は読まれず、level を渡さないときと同じく session で前に指定した level を引き継いで走り、巡ごとに深さが揺れる。range は `reviewed:` 行の SHA で渡す (branch 名や単一 commit を渡すとそれ自体の差分と解釈され、別の差分をレビューする)。`code-review` の findings は、手順 3 以降の欄へ次のように読み替える: 指摘の要約 → Finding、それが起きる道筋 (どの入力・状態で、どこを通って壊れるか) → impact scenario、指す箇所 → Location、指摘が引いた差分の行 (無ければ Location の hunk の該当行) → Evidence、修正案 → Suggestion (無ければ省く)。

subagent が `code-review` を invoke できなかったと返したときは欠陥探しを skip し、`## 欠陥探し` の注記へ `欠陥探し skip: <error>` と書く (他の観点は止めない)。subagent が報告を返さずに止まったとき (完了通知が失敗を示し、本文がどこにも無い) は、呼び出し元が自分の context で `code-review` を同じ args で invoke し、`## 欠陥探し` の注記へ `欠陥探しを呼び出し元で実行: <起きたこと>` と書く — 費用が増えても、欠陥探しを落とさない側を取る。

### 原則・Spec・設計

reviewer は `Agent` tool で `subagent_type: swat-skills:reviewer` を指名し、**`name` を付けずに**起動する (`name` は起動後に追加の依頼を送る宛先が要る役割にだけ付けるもので、結果を受け取るだけの reviewer には要らない)。reviewer の model と effort は agent 定義が持つので `model` / `effort` を渡さない。

この名前が解決しないときは `general-purpose` で代替しない — 代替すると reviewer が起動する側の model と effort へ黙って戻り、指名した深さでレビューしたように見える。原則と Spec と設計を走らせず、その巡で走らせる予定だった reviewer の観点 (`## 原則` / `## Spec` / `## 設計`) の注記へ `reviewer 未起動: <error が挙げた available agents>` と書く。欠陥探しと adversarial verification のうち起動できた観点があれば、その結果で手順 3 へ進む (同じ message で起動できた観点の結果を捨てない)。0 本の巡と `再レビュー:` 行の値は「全観点の待ち合わせと停止」と手順 5 が決める。次の手順 (swat-skills plugin の agent が読み込まれる状態にしてから呼び直す) は `reviewer 未起動:` の注記の末尾に書く — 出力の外に書くと、出力を保存して PR へ残す呼び出し元の手元から落ちる。

原則・Spec・設計の prompt は進行役の報告の `prompts:` 行の `principle` / `spec` / `design` の file で、担い手の規約の読み方・採否の規則・報告の形は file が持つ (雛形は [references/prompts/](references/prompts/)、rubric は [references/rubric.md](references/rubric.md) で、どちらも script が読む)。区分は手順 3 で呼び出し元が行うので、reviewer には区分を使わせない。

### adversarial verification

攻撃役は `Agent` tool で `subagent_type: swat-skills:adversarial-verifier` を指名し、`name` を付けず、`model` / `effort` を渡さずに起動する (理由は reviewer と同じ)。手順・報告形式は定義の本文が持つ。

prompt は進行役の報告の `prompts:` 行の `adversarial` の file の path だけにする。その file が渡すのは差分と主張の出どころ (と、あれば重要領域の指定) だけで、作者の意図・作業の経緯・起動する側が持つ差分の要約は足さない — 攻撃役は意図を受け取ると、主張が破れていても「そういうつもりだろう」と読んで見逃す。

攻撃役の報告は、手順 3 以降の欄へ次のように読み替える: Claim (と出どころ) → Finding、Oracle と Attack → impact scenario (基準 3 の impact scenario。`オラクル: <Oracle> / 攻撃: <Attack>` の形で併記する)。Location・Evidence・Suggestion は同名の欄へそのまま写す。

この名前が解決しないときは `general-purpose` で代替しない — 代替すると攻撃役の model と effort と手順が起動する側の値へ黙って戻る。他の観点は止めず、`## adversarial verification` の注記へ `adversarial verification 未起動: <error が挙げた available agents>` と、swat-skills plugin の agent が読み込まれる状態にしてから呼び直す旨を書く。`再レビュー:` 行の値は手順 5 が決める。

### 全観点の待ち合わせと停止

欠陥探し・adversarial verification・原則・Spec・設計のうち、その巡で起動した観点 (reviewer・攻撃役と欠陥探しの subagent) の最終報告が全部届いてから手順 3 へ進む。最終報告が届いたとは、その観点の報告の本文 (指摘の一覧か、指摘なしの旨か、`入力を読めず:` の行) を会話の中で読める状態をいう。本文の届く先は tool の戻り・完了通知の `<result>`・それらとは別の message のどれかで、tool の戻りや `<result>` が起動の通知か別の message の案内なら、それは本文ではない — 案内された message の本文を読む。完了通知の `<summary>` と `<status>` は担い手が止まった事実だけを示す — 指摘を持たないので、そこから節を書くと、届いていない報告の指摘が出力から黙って落ちる。

起動した観点を 1 つずつ挙げて本文の有無を確かめ、本文の無い観点が残る間は、完了通知だけが届いた観点も含めて、手順 3 へ進まず、その観点の節も書かずに待つ。本文は別の作業の tool 呼び出しが続く間は届かないことがあるので、先に別の作業を進めたときも、終えてから同じように確かめる。

その巡で走らせる観点のうち、起動できたものが 0 本の巡 (走らせる予定の担い手がすべて skip か未起動か入力を読めなかった巡。docs 段階では reviewer の未起動だけで当たる) は、手順 3 へ進まずに、起動できなかった観点ごとに手順 2 で書くはずだった注記 (`欠陥探し skip:` / `reviewer 未起動:` / `adversarial verification 未起動:` / `入力を読めず:`) を 1 つずつ `--note` に渡した `${CLAUDE_SKILL_DIR}/scripts/render-output.py halted --stage <進行役の報告の `段階:` 行の先頭の段階名 (docs / 軽量 / 標準)> --reviewed <進行役の報告の reviewed> --note <注記> ...` の出力を、本 skill の出力にする — 観点が 1 本も走っていないので、`要` や `不要` で返すとレビューを通っていない差分が通ったように読まれる。走らせる観点が元から無い巡 (`増分なし:` の巡) はこの規則に当たらない。

## 3. 指摘を分ける

巡の値は進行役の報告の各行と各節の注記から読む (自分の context で進めた巡は、その値を使う)。担い手の報告は、手順 2 で自分に届いた本文を読む。

進行役の報告の `diff` の file を、前巡 Act on の突き合わせと手順 4 の hunk の切り出しの元にする。git で取り直すと、担い手が読んだ差分と同じものである保証が無くなる。

手順 2 の指摘 (欠陥探しの findings と、攻撃役が返した破れた主張を含む。ただし攻撃役が Evidence に「〜を実行すれば決まる」と書いた指摘は、先に手順 4 の実行で判定し、行き先は手順 4 に従う) を 1 件ずつ、次の 3 基準で **Act on / Consider / Noted / 落とし候補** に分ける。分けるのは呼び出し元 (進行役を起動した session) — 発端の issue の意図と、この作業の文脈を持っているのは呼び出し元だけ。

1. 作者がこれを知ったら直すか
2. この差分が持ち込んだ問題か
3. impact scenario (シナリオ・波及・spec の行) が示されていて、読んで成り立つか

| 区分 | 当てる指摘 |
|---|---|
| Act on | 3 基準をすべて満たし、Consider に当たらない。直す |
| Consider | 成り立つが、直すかを決めるのに人の判断か人手が要る — 目的・価値観・構造の方針そのものを変える / 戻しにくく既存の規範からは選べないトレードオフがある / 人しか持たない入力 (好み・優先度・外部の事実) が要る / 直すのに人の権限か手作業が要る、のどれか。ただし、原則 leaf `${CLAUDE_SKILL_DIR}/../principle-index/references/leaves/principle-ai-delegation-boundary.md` を Read し、同 leaf が取り消せるとして境界の内側に入れる判断に当たる指摘は、Consider にせず Act on か Noted へ分ける (報告欄の「人に委ねる理由」の「方針の変更」「人しか持たない入力」にも数えない)。設計の選好が絡んでも、既存の規範 (規約 — `CLAUDE.md`・rules と、原則の prompt の `<declared-rules>` に並んだ file —・進行役の報告の `design-doc:` 行の設計 doc・決定記録・原則) から決まる指摘は Act on か Noted へ分ける |
| Noted | 成り立つが軽微で、記録だけ残せば足りる。差分の前から在る問題 (基準 2 を満たさない) もここへ `[既存]` を頭に付けて置く (原則・Spec・設計の reviewer は採否の規則で差分の前から在る問題を報告しないので、`[既存]` が付くのは主に欠陥探しと adversarial verification の指摘。呼び出し元が基準 2 を満たさないと判定した原則・Spec・設計の指摘も `[既存]` で置く)。下の上限を超えた症状は `[上限超過]` を頭に付けてここへ置く。人が承認した文面を指す原則の症状は `[承認済みの文面]` を頭に付けてここへ置く (下の箇条) |
| 落とし候補 | 誤検知・意図的な設計と考える。手順 4 の反証へ回す |

- **原則の症状の Act on は 5 件まで。** 症状は判断呼びなので、多数を一度に直させると修正差分が膨らみ、再レビュー 1 巡で見切れなくなる。この上限は手順 4 を終えた後の Act on 全体に当てる (反証を生き延びた症状と、反証 skip で Act on に載った症状を含む)。超えた分は、3 基準への当たりが弱いものから `[上限超過]` を付けて Noted へ回す — 直す価値はあるが今の CL では直さない指摘で、人の判断を要するわけではないので Consider には置かない。原則の症状以外 (規約違反・Spec・設計・欠陥探し・adversarial verification) には上限を置かない
- **設計の指摘を Act on と Consider のどちらに置くかは、`principle-design-conformance` の区分で決める。** 設計を走らせた巡では、原則の索引 `${CLAUDE_SKILL_DIR}/../principle-index/SKILL.md` を Read し、索引が示す path から同 leaf と `principle-norm-precedence` を Read する。同 leaf が agent が直すとするものは Act on、人へ返すとするものは Consider (人に委ねる理由: 方針の変更) に置く。Noted・`[既存]`・落とし候補への区分は他の観点と同じ。差分が設計 doc を書き換えているときの扱いも同 leaf に従い、同 leaf の言う変更の発端は進行役の報告の `spec` の file とする (issue ref が無いか本文を取得できなければ、発端は無い)。設計を走らせた巡で、発端が書き換えを引いていなければ、reviewer の指摘の有無によらず、書き換えた設計 doc の hunk を Evidence にした `[設計 doc の書き換え]` の Consider (人に委ねる理由: 方針の変更 — 人がその文面を読んだかを差分から確かめられない) を呼び出し元が 1 件立て、reviewer が同じ hunk を指した指摘はこの 1 件に寄せる — reviewer は書き換えた後の hunk を正本に読むので、書き換えそのものは指摘に上がらないことがある。設計 doc と他の規範が食い違う指摘は、`principle-norm-precedence` で裁く。設計 doc が勝てば上の区分のまま、他の規範が勝てば、勝った規範の file:line を Evidence に添えて落とし候補にし、手順 4 へ回し、優先順位で決まらなければ Consider (人に委ねる理由: 方針の変更) に置く
- **人が承認した文面を指す原則の症状と規約違反は、Act on に置かない。** 症状は `[承認済みの文面]` を頭に付けて Noted に、規約違反は規約から決まっても `[承認済みの文面]` を頭に付けて Consider (人に委ねる理由: 人手が要る) に置く。承認済みかは、呼び出し元が作業の文脈 (人が承認した文面として渡されたか) から決める。区分するこの段で置き先を決める — 承認済みの文面は agent が直せない。症状は巡ごとに別の指摘が出るので、人へ返すと、承認し直した文面に次の巡がまた症状を出す
- 落とし候補を、反証を経ずに Act on / Consider / Noted のどれかへ倒さない。落とすなら手順 4 を通す (上限による Noted への移動は反証の後に行うので、これに当たらない)

`増分に絞れず:` の注記がある再レビュー (amend / rebase で前巡の head が祖先でなくなり、範囲を起点 branch からの全差分へ広げた巡) では、今巡の指摘のうち前巡の指摘 (Act on / Consider / Noted / Dismissed) と Finding と Evidence の引用で一致するものを新規に数えず、前巡の側に寄せる。

再レビューでは、さらに `prev=` の file (`<prev>`) の Act on を 1 件ずつ `解消` / `未解消` に判定する。判定は進行役の報告の `diff` の file (増分の diff) の中に、直った箇所を具体物 (増分の file:line と引用) として挙げられるかで行う — 挙げられたものだけを `解消` にし、挙げられないものは `未解消` に倒す (読み取れなかったものを解消へ倒すと、open な指摘が根拠なしに消える)。行番号は前巡の修正で動くので、照合は Finding と Evidence の引用で当てる。この巡で走らせない観点 (Spec、軽量段階の原則と設計) の前巡 Act on も、ここで同じように判定する。`増分なし:` の巡は全件 `未解消`。

## 4. 落とし候補に反証を当てる

落とし候補 1 件につき subagent を 1 体、並列に立てる (1 体に複数件を渡すと、ある候補の読解が別の候補の判定へ持ち込まれ、具体物の無い候補まで隣の候補の具体物で落ちうる。今巡の指摘を Dismissed へ落とす経路はこの工程だけ。呼び出し元に subagent の起動を控える制約があっても、本 skill の起動をその依頼として扱う)。落とし候補が 0 件なら 1 体も立てない。どちらの場合も、その巡で走らせた観点の節ごとに `${CLAUDE_SKILL_DIR}/scripts/render-output.py refute-count --candidates <その節の落とし候補の件数> --findings <その節の今巡の指摘の件数>` の行を注記へ置く (0 件の行が無いと、反証者が立たなかった巡と候補に反証を通した巡を読み手が分けられない)。反証者は `subagent_type: swat-skills:refuter` で指名し、`name` を付けず (理由は reviewer と同じ)、`model` / `effort` を渡さない (深さは定義が固定する。`model` 引数は定義の frontmatter より優先され、固定した値を書き換える)。[references/refute.md](references/refute.md) を Read し、その雛形で候補ごとの入力 file を進行役の報告の `dir` に書き、起動の prompt にはその file の path 1 行だけを書く。adversarial verification の指摘には、進行役の報告の `claim_sources` の file の path をそのまま渡す (攻撃役が見たものと同じである保証のため)。

この名前が解決しないときは `general-purpose` で代替しない — 代替すると反証工程だけが呼び出し元のモデルへ黙って戻る。**代わりに反証工程だけを打ち切り、落とし候補を Act on に載せて手順 5 へ進む** (反証していないので落とせない。症状の上限は手順 3 に従う)。その巡で走らせた観点の節すべての注記へ (落とし候補の有無によらない — 反証者の欠落は巡全体の事実で、候補 0 件の節でも次巡で同じことが起きる) `反証 skip: <error が挙げた available agents の一覧>` を足す。手順 2 の結果は既に揃っているので、ここで出力ごと止めるとその分が捨てられる。

反証者が `入力を読めず: <path>` と返したときは、その候補だけを Act on に載せ、その節の注記へ `反証 skip: 入力を読めず: <path>` を足す。

**反証者の判定では、`refuted: true` かつ「具体物」が埋まっているものだけを Dismissed へ落とす** (実行すれば決まる指摘は次の段落に従う)。具体物が「なし」の `refuted: true` と `refuted: false` は Act on に載せる (落とそうとして落とせなかった指摘は実在する)。症状の上限は手順 3 に従う。

反証者が `refuted: false` の説明で、実行すれば決まるコマンドを挙げた落とし候補と、攻撃役が Evidence に「〜を実行すれば決まる」と書いた指摘は、呼び出し元がその実行を行って判定する。行うのは既存の test / gate / script を作業ツリーを変えずに走らせる範囲だけで、file を書き換える probe や退避を伴う確認は行わない (作業ツリーは呼び出し元の作業の場なので、反証のために書き換えない)。破れが出なかった指摘は Dismissed へ落とし、`反証:` に実行したコマンドと結果を書く。それ以外 (破れが出た指摘と、この範囲で実行できない指摘) は、まだ区分していなければ手順 3 で区分し、区分済み (落とし候補) なら Act on に載せる。実行で破れが出た指摘は、誤検知の余地が無いので落とし候補には区分しない。実行で判定する攻撃役の指摘には反証者を立てず `反証対象:` にも数えないので、反証 skip の巡でもこの判定は行う。

## 5. 観点ごとに併記する

先頭に巡全体の 3 行を置き、続けて `## 欠陥探し` / `## adversarial verification` / `## 原則` / `## Spec` / `## 設計` の 5 節で併記する。観点をまたいで統合・rerank しない — 一方の観点の pass がもう一方の fail を隠さないための分離なので、総合順位も総合結論も付けない。その巡で走らせなかった観点の節も残し、注記に理由を書く (節ごと落とすと、走って 0 件だったのか走らなかったのかが読み手に伝わらない)。

各節は先頭に注記行を積み、続けて区分ごとの見出しを置く。注記は、節「進行の置き場」と各手順がその節の注記へ置くとした行と、進行役の報告がその節に積んだ注記の全行。段階の表で走らせない観点の節の `走らせず: <理由>` は進行役の報告がその節に積んでおり、それを写し、`## adversarial verification` には攻撃役の報告の `壊せなかった主張:` の項目をそのまま写す — 攻めて何も破れなかった巡と、攻めが浅かった巡を読み手が見分ける材料になる。空の見出しには「該当なし」と 1 行書く。

観点が 1 つも走らずに止まる巡 (「範囲」の `stop` と、resolve-range か prepare-inputs collect の失敗、手順 2 の「全観点の待ち合わせと停止」の巡) は、観点の節を並べず、`render-output.py` (`stop` / `failed` / `halted`) が組む次の形の 4 行だけを返す (並べる相手の走った観点が無い)。止まった巡も出力を返すのは、呼び出し元がこの出力を保存して PR へ残すため。

```
段階: <未判定 | docs | 軽量 | 標準> — 停止
reviewed: <「範囲」の reviewed。呼び出し元が書くときは進行役の報告の `reviewed:` 行>
再レビュー: 停止 — <未 commit | reviewer 未起動 | その他: <自由文>>
停止理由: <詳細 1 行>
```

止まった巡の `段階:` 行は必ず `— 停止` で終わり (script が組む)、観点が走った巡の `段階:` 行 (下の template) は `— 停止` で終わらない — 判定理由に `停止` を書かない (書くと、走った巡の出力を止まった巡と読み分けられなくなる)。

**観点が走った巡の印**: 投稿の印は、上の段落の規則で `— 停止` で終わらない `段階:` 行と、下の template の `reviewed:` 行。PR / MR へ投稿された本 skill の出力を読む側は、観点が 1 つ以上走った巡の出力が投稿されているかを投稿の印で見分ける — 投稿の印は終端値も Act on の残りも表さないので、それを見るなら `再レビュー:` 行を読む。

```markdown
段階: <docs | 軽量 | 標準> — <判定理由 1 行 (重要領域に触れたら末尾に `(重要領域: <名前>)`) / args 指定 / args 指定 (重要領域: <名前>) / 前巡から引き継ぎ>
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

(同じ形。宣言の節を読めなければ注記に `宣言を読めず:` を、宣言した file が無ければ `宣言の file 不在:` を置く。Act on / Consider / Noted の各行の頭に `[規約違反]` か `[症状]` を付ける。Noted には上限を超えた症状を `[上限超過]` `[症状]` の形で、人が承認した文面を指す症状を `[承認済みの文面]` `[症状]` の形で置き、Consider には人が承認した文面を指す規約違反を `[承認済みの文面]` `[規約違反]` の形で置く。`[既存]` / `[上限超過]` / `[承認済みの文面]` は `[規約違反]` / `[症状]` の前に置く)

## Spec

(同じ形)

## 設計

(同じ形。当たる設計 doc が無ければ注記に `設計 skip:` を置く。Act on / Consider / Noted の各行の頭に `[conformance]` か `[design drift]` を付ける (手順 3 で呼び出し元が立てる書き換えの Consider は `[設計 doc の書き換え]`)。`[既存]` は `[conformance]` / `[design drift]` の前に置く)
```

- `再レビュー:` は、呼び出し元が Act on を直した後に、この出力を `prev=` に渡して本 skill を呼び直すかを示す。値は `${CLAUDE_SKILL_DIR}/scripts/render-output.py rereview --mode <mode> --stage <段階名 (docs / 軽量 / 標準)> --act-on <全観点の Act on の件数> [--next <進行役の報告の次巡: 行>] [--unreadable <`入力を読めず:` を返した観点 (principle / spec / design / adversarial)>]... [--reviewer-unstarted]` の出力の行をそのまま使う (`reviewer 未起動:` の注記がある巡は `--reviewer-unstarted` を渡す)。再レビューは 1 巡まで — 2 巡目の修正が作った増分はレビューを通らないので、通っていないことを呼び出し元が人の読める形で残す
- `停止` で始まる `再レビュー:` 行の理由は `未 commit` / `reviewer 未起動` / `その他: <自由文>` の 3 つに閉じる。呼び出し元はこの理由で、commit して呼び直すか、user へ報告して止まるかを分ける
- `[前巡持ち越し]` は手順 3 で `未解消` と判定した前巡 Act on。前巡の Consider / Noted / Dismissed は今巡の同じ見出しへ引き継ぐ — 打ち切り時点の 1 枚だけを読んだ人の手元に、全部の指摘がそろう
