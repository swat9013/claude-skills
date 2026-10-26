---
name: write-for-harness
# steering 初の user + model 両用。model からも起動させるのは、harness の指示文を
# 書く作業が他 skill の作業中に発生するため (disable-model-invocation を付けると
# その経路で規範が読まれないまま書かれる)
user-invocable: true
argument-hint: "[<path> | 新規: <component> <目的>]"
description: CLAUDE.md / .claude/rules/ / SKILL.md 指示文 / hook 注入文 / agent 定義 (Inferential harness) を規範に照らして書き、検証 subagent と構造 reviewer の指摘まで反映する手順。settings / hook script (Computational) は対象外。Use when「CLAUDE.md に規範を足す」「rules を書く」「skill の指示文を書く・直す」「hook の注入文を書く」「agent 定義を書く・subagent を切り出す」「harness の指示文を見直す」.
---

# write-for-harness

Inferential harness (CLAUDE.md / `.claude/rules/` / SKILL.md 指示文 / hook 注入文 / agent 定義) を書く手順。決定論的に動く Computational 側 (settings / permission / hook script / gate) は本 skill の対象外。Computational を書く規範は呼び出し元のフローに委ね、レビュー手順は `claude-config-review` が持つ。

本 skill を呼び出すときの呼び出し口 (渡す args・invoke の回数・検証の範囲の宣言・反映を終えられなかったときの返し方・委譲後の変更 path の照合) は、呼び出し元向けに `${CLAUDE_SKILL_DIR}/references/caller-contract.md` に置いてある。同じ file を同じ変更のまとまり (CL を切る運用ならその CL) でもう一度 invoke するときは、他の skill からでも自分の session から直接でも、invoke の前にこの契約の「検証の範囲」を Read して `省く` を宣言するかを決める — 本 skill は検証の範囲を決めるときに前の invoke を数えないので、宣言が無いと毎回検証が走る。

## 手順

### 1. 対象を決める

| args | 対象 |
|---|---|
| path (相対 / 絶対 / `~/`) | その file。`~/` は `$HOME` へ展開する |
| `新規: <component> <目的>` | 新規作成。`<component>` は skill / claude-md / rules / hook-inject / agent のいずれか |
| なし | 下記の自動検出 |

args なしのときは、次の script が出す絶対 path (1 行 1 件) を候補にする。出力には作業ツリーの変更に加えて、branch で commit 済みの変更も入る。

```bash
${CLAUDE_SKILL_DIR}/scripts/list-changed-files.py
```

候補を引数に並べて次の script を起動し、stdout の `<path> - <component>` と stderr の `分類外: <path>` で振り分ける。path のパターンで component を決める正本はこの script で、args の path も同じ script に通す。args の path が `分類外:` になり、下の hook の script にも当たらなければ、呼び出し元が invoke 直前の同じ turn で宣言した component を使い、宣言が無ければ分類外であることを告げて止まる。

```bash
${CLAUDE_SKILL_DIR}/scripts/list-harness-inventory.py <path>...
```

`skill` / `claude-md` / `rules` / `agent` と出た file を対象にする。`hook` と出た file と、`分類外:` と出た file のうち hook の設定 (`hooks.json`・settings の `hooks`) の command が指す script は中身を読み、hook が Claude へ届ける注入文を持つ (届く経路は問わない) なら hook-inject として対象にする。`settings` と出た file と、注入文を持たない hook script は Computational なので対象から外し、「Computational は対象外」と、冒頭の段落にある書く規範とレビューの行き先を添えて告げてから残りを進める。合致 0 件なら「対象なし」で正常終了する。上の 2 つの script のどちらかが exit 非 0 で終わったときは列挙の失敗なので、対象なしとせずに止まる。

**書き換える対象は広げない** — 編集するのは args か、上の script の出力から出た file だけに閉じる (手順 4 の構造 reviewer は harness 全体を俯瞰するが、俯瞰は提案の入力であって編集対象ではない)。例外は外出しのパッチで、対象 skill の `references/` に新しく作る file だけを足してよい。その file は元の SKILL.md と同じ component (skill) として手順 4 のレビュー対象に含める。

### 2. 文書構成の正本を読む

`Skill(swat-skills:writing-for-agents)` を invoke する。手順 2・3 の規範は、`省く` の宣言に関わらず invoke ごとに context に揃える — 宣言が狭めるのは手順 4 の検証だけ。同じ session の前の invoke で得た invoke・Read の結果が今の context に見えていれば、読み直さずにそれを使い、手順 5 の報告に `規範の Read: 前の invoke の結果を使用` と書く (読み直しても同じ内容が context に二重に載るだけ)。compaction で要約されているか、前の invoke の後にその reference を編集していれば、invoke・Read し直す (要約は中身を欠き、編集の前の結果は古い)。

構成の正本を読めないまま手順 3 以降へ進まない。規範が欠けた状態で書いた文面は、欠けたことが誰にも見えないまま harness に残り続ける。

### 3. 規範を読む

component の責務 reference を Read する。

| component | Read する reference |
|---|---|
| skill | `${CLAUDE_SKILL_DIR}/references/components/skill.md` |
| claude-md | `${CLAUDE_SKILL_DIR}/references/components/claude-md.md` |
| rules | `${CLAUDE_SKILL_DIR}/references/components/rules.md` |
| hook-inject | `${CLAUDE_SKILL_DIR}/references/components/hook-inject.md` |
| agent | `${CLAUDE_SKILL_DIR}/references/components/agent.md` |

加えて文面規範 `${CLAUDE_SKILL_DIR}/references/writing.md` を Read する。

続けて対象モデルの差分を読む (どの file かは writing.md 末尾の表)。対象モデルは、実行モデルを宣言している指示文 (skill の `model:` 等) ならそのモデル、宣言の無い指示文は `${CLAUDE_SKILL_DIR}/references/models.md` の「文面の基準モデル」 — 決め方の正本は同節。モデル選定そのもの (能力帯・単価比・モデル ID) は `${CLAUDE_SKILL_DIR}/references/models.md` が正本。

書く前に `${CLAUDE_SKILL_DIR}/references/patches.md` を Read し、変更をパッチ 6 種 (追加 / 書き換え / 削除 / 統合 / 外出し / 分割) の候補から選ぶ。診断する行は、依頼が名指しした変更と、その変更を吸収する行までにする — 呼び出し元が承認したのはその変更だけなので、file 全体の見直しを依頼されたときだけ全行を診断する。patches.md が報告だけにするとしたパッチ、診断の範囲外の行へのパッチ、手順 1 の対象外の file への吸収は編集せず、手順 5 で報告する。no-op の削除は、patches.md の診断表が求める確認 (suite か micro-test) を書く前に通したもの (呼び出し元が通したと宣言したものを含む) だけを当て、通していないものは報告に回す。パッチは 1 つずつ当て、その直前と直後の対象 file の文字数を `wc -m` で数える — 手順 5 の報告で前後差を書く。

新規作成で「どの機構に載せるか」から決めるなら、4 slot 表 (`guide` / `sensor` × `Computational` / `Inferential`) を `${CLAUDE_SKILL_DIR}/references/architecture.md` で引く。

変更の届け方は実行 project の運用に従う。

### 4. 検証 subagent と構造 reviewer を並列で起動する

呼び出し元が invoke 直前の同じ turn で `省く` と宣言していれば、どちらも起動せずに手順 5 へ進み、手順 5 の報告に `検証省略: 呼び出し元の宣言` と書く。宣言が無ければ、下の各 subagent の条件で起動する。

起動するものは `Agent` tool で同一 message 内で起動する。どちらも結果を受け取るだけの役割なので `name` を付けず、報告は各 subagent の最終メッセージで受け取る (SendMessage での報告は求めない)。model・effort・手順・返答の schema は agent 定義が持つので、Agent tool の `model` / `effort` 引数は渡さず、prompt には下の雛形の入力だけを渡す。

- **検証 subagent**: 書いた file の文面を component 別 reference に照らし、当たる設計 doc があればその設計 doc にも照らす。条件なしで起動する。`subagent_type: swat-skills:harness-component-reviewer` で指名する
- **構造 reviewer**: 書いた内容の**配置**が正しいかを harness 全体の中で見る。この invoke が新規作成か、当てたパッチに追加・外出しがあれば、対象が 1 ファイルでも 1 つ起動する。無ければ起動せず、手順 5 の報告に `構造レビュー省略: 配置が新しく決まるパッチなし` と書く — 書き換え・削除・統合と、報告だけにしたパッチ (分割を含む) は既存の行の置き場を変えないので、配置を見る対象が無い。`subagent_type: swat-skills:harness-structure-reviewer` で指名する

名前が解決しないときは `general-purpose` で代替しない — 代替すると model・effort・tools が呼び出し元の値へ黙って戻り、指名した深さで見たように見える。2 つの結果は互いに独立しているので、解決した側の結果だけで手順 5 へ進む。解決しなかった側の結果を報告する位置 (検証 subagent なら findings、構造 reviewer なら `structural_proposals`) に、代わりに `<検証|構造>レビュー未実施: <error が挙げた available agents>` と次の手順 (swat-skills plugin の agent が読み込まれる状態にしてからレビューをやり直す) を書く。

構造 reviewer の入力になる「harness 全体一覧 (path + component)」は、起動前に main agent が手順 1 の分類の script を引数なしで起動し、stdout (git 管理下の file の `<path> - <component>`) をそのまま使う:

```bash
${CLAUDE_SKILL_DIR}/scripts/list-harness-inventory.py
```

git 管理外の `~/.claude/` 系 path は、args で対象として渡された場合のみ、同じ script にその path を渡して出た行を一覧へ加える。script が exit 非 0 で終わったら構造 reviewer を起動せず、その結果の位置に `構造レビュー未実施: 一覧を作れなかった (<stderr の 1 行>)` と次の手順 (stderr の原因を直してから構造 reviewer だけをやり直す) を書く。一覧は prompt へ全件埋め込む — 各ファイルの全文 Read は reviewer が必要と判断したものだけが行う。

検証 subagent の prompt (入力):

```
component: <COMPONENT>

参照する reference (絶対 path):
- <COMPONENT_REF>
- <WRITING_REF>
- <MODEL_REF>
- <CONFORMANCE_LEAF>

レビュー対象 (絶対 path):
- <PATH_1>
- ...

reference が公式 docs の確認を求めていても、外部の確認を試みず reference に書かれた内容の範囲で判定する。

<SCOPE_LINE>

照らす設計 doc (1 行に `<置き場> | <索引> | <照らすレビュー対象>`。索引があれば索引から読む file を決め、空なら置き場の全体を読む):
<DESIGN_DOCS>

レビュー対象に当てたパッチの diff (2 つの検査は、この diff が変えた箇所にだけ当てる):
<PATCH_DIFF>

各設計 doc を、その行に並べたレビュー対象にだけ照らし、reference の principle-design-conformance の 2 つの検査を当てる。その指摘は category を `conformance` か `design drift` にし、evidence に設計 doc の行を file:line で添える。
```

`<COMPONENT>` / `<PATH_*>` / 各 `*_REF` / `<SCOPE_LINE>` / `<CONFORMANCE_LEAF>` / `<DESIGN_DOCS>` / `<PATCH_DIFF>` は起動時に埋める (後の 3 つは下の段落)。`<SCOPE_LINE>` は「レビューするのは、次の行範囲と、その範囲が他の行と食い違いを生む箇所だけ: <手順 3 で診断した行範囲>」で埋める。行範囲はパッチを当てた後の file の行番号で書き、レビュー対象が複数あれば file ごとに並べる。手順 3 で全行を診断した file (file 全体の見直しの依頼・新規作成) は範囲に並べず、全 file がそうなら行ごと落とす — 診断していない行への指摘は手順 5 で Noted にしかならず、人が読む量だけを増やす。`<COMPONENT_REF>` と `<WRITING_REF>` は手順 3 で Read した component 別 reference と文面規範。`<MODEL_REF>` は手順 3 で決めた対象モデルの差分 file で、writing.md の表が 2 本を挙げるモデルでは両方を並べる。返答で期待する JSON の鍵は `findings` で、未実施行は findings の位置に置く (返答の読み方とやり直しは下の「JSON が得られないとき」)。

`<DESIGN_DOCS>` と `<CONFORMANCE_LEAF>` は、起動前に main agent がレビュー対象の file ごとに当たる設計 doc を決めて埋める。file を持つ git repo の `docs/agents/domain.md` に節「設計 doc の置き場」があれば、原則 leaf `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/references/leaves/principle-design-conformance.md` を Read し、その「設計 doc の置き場の引き方」で当たる行を決める。当たる行が 1 つでもあれば、規範の優先順位の leaf `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/references/leaves/principle-norm-precedence.md` も Read する (2 つの leaf は手順 5 の区分に使う)。`<DESIGN_DOCS>` には、当たる行を 1 行ずつ `<置き場の絶対 path> | <索引列の file の絶対 path (空なら空欄)> | <その行の適用範囲に当たるレビュー対象の絶対 path (複数なら , 区切り)>` で並べ、`<CONFORMANCE_LEAF>` は前者の leaf の path で埋める — 適用範囲の外の file に設計 doc を当てると、その file に当たらない指摘が出る。置き場か索引の file が無い行は並べず、その行の適用範囲に当たる file ごとに手順 5 の報告へ `設計 skip: <file>: 設計 doc 不在: <path>` と書く。domain.md の行に 1 つも当たらない file は、手順 5 の報告に `設計 skip: <file>: <理由>` (git repo の外 / `docs/agents/domain.md` か節が無い / 節が設計 doc を案内しない / 当たる行が無い) と書く — 置き場の宣言は repo ごとに無いことが正常なので、止めずに文面の検証だけを走らせる。並べる行が 1 つも無ければ、雛形の `<CONFORMANCE_LEAF>` の行と、「照らす設計 doc」の行から雛形の末尾までを落とす。`<PATCH_DIFF>` は、起動の時点でレビュー対象の file に当ててあるパッチ全部の diff を、省かずに埋める — 2 つの検査はどちらも変更に当てるもので、file だけを渡すと前からあったずれと今回のずれを分けられず、省いた hunk は検査から黙って抜ける。

構造 reviewer の prompt (入力):

```
参照する reference (絶対 path):
- 共通: ${CLAUDE_SKILL_DIR}/references/architecture.md
- 判断規則: ${CLAUDE_SKILL_DIR}/references/structure.md
- 共通: ${CLAUDE_SKILL_DIR}/references/models.md

レビュー対象 (今回書いた file、絶対 path):
- <PATH_1>
- ...

harness 全体一覧 (path + component):
<INVENTORY>
```

`<PATH_*>` と `<INVENTORY>` (一覧の全件) は起動時に埋める。返答で期待する JSON の鍵は `structural_proposals` で、未実施行は `structural_proposals` の位置に置く (返答の読み方とやり直しは下の「JSON が得られないとき」)。

**この SKILL.md をロードした時点で `${CLAUDE_SKILL_DIR}` は絶対 path へ置換済み**なので、どちらの prompt も本文に見えている path をそのまま写す — 変数表記を復元して渡すと subagent 側では展開されず、reference を 1 本も読めない (どちらの subagent も `unread_refs` を返して止まり、レビューは行われない)。送信前に prompt へ `${` が残っていないことを確かめる。

**JSON が得られないとき** (検証 subagent と構造 reviewer に共通): 返答の前後に文が付いていたら外して JSON を読む。期待した鍵を持つ JSON が無いとき (`unread_refs` だけを返した・空) は、その subagent だけを 1 回までやり直す。`unread_refs` なら、やり直す前に挙がった path が実在するかを確かめる (`${` を含む path は、変数を本文に見えている絶対 path へ置き換えてから確かめる)。実在しなければ、起動し直しても同じ `unread_refs` が返るだけなので、やり直さない。実在するなら、prompt に `${` が残っていないかを確かめ、参照 path を起動時と同じ埋め方 (各 prompt の雛形とその直後の段落) で埋め直してからやり直す。やり直しても得られないときと、path が実在せずやり直さなかったときは、その subagent の結果の位置に `<検証|構造>レビュー未実施: <理由>` と次の手順を書く。次の手順は、path が実在しなかったなら「挙がった path の欠落か書き誤りを直してから、その subagent だけをやり直す」、やり直しても `unread_refs` が返ったなら「挙がった path を Read できるかと、prompt に `${` が残っていないかを確かめてから、その subagent だけをやり直す」、それ以外は「その subagent だけをやり直す」。

### 5. 指摘を反映して終える

findings を 1 件ずつ次の区分に分け、Act on だけを対象ファイルへ反映する。分けるのは本 skill を実行している session。範囲内は、手順 3 で診断した行と、変更が他の行と生んだ食い違いを指す。呼び出し元が invoke 直前の同じ turn で不採用と宣言した指摘 (同じ箇所に同じ reference の行を当てた指摘) は、範囲の内外を問わず Noted に置く。対応がはっきりしない指摘は Consider に置き、理由を添える。

| 区分 | 当てる指摘 | 扱い |
|---|---|---|
| Act on | 範囲内の指摘で、reference の行を逐語で引ける規範違反 | 反映する |
| Consider | 範囲内の指摘のうち Act on に当たらないもの (reference を引けない改善余地・info・誤検知か意図的な設計とみた指摘) | 反映せず、理由を添えて返す。直すかは呼び出し元が決める |
| Noted | 範囲外の指摘と、不採用と宣言された指摘 | 反映せず、記録として返す |

設計 doc の指摘 (category が `conformance` / `design drift`) は、範囲内なら手順 4 で Read した principle-design-conformance の区分で置く。同 leaf が agent が直すとする指摘は Act on (引く reference の行は設計 doc の行) にし、人へ返すとする指摘は Consider に「方針の変更 — 人へ返す」を理由に添えて置く — 設計 doc を変えるのは人の決定なので、本 skill も呼び出し元も設計 doc を書き換えて解消しない。設計 doc と他の規範が食い違う指摘は principle-norm-precedence で裁き、設計 doc が勝たなければ Consider に置いて、勝った規範の行か、優先順位で決まらないことを理由に添える。

指摘はどれもこの 3 区分のどれかに置き、外したい指摘も Consider に理由を添えて置く — 指摘を落とせるのは反証だけで、本 skill は反証の工程を持たない。

**反映後、本 skill を終える返答に diff と、区分名を付けて 1 件ずつ並べた findings を載せる** (呼び出し元が人間でも skill でも、反映を人間の目に載せる唯一の面がここになる。後の turn へ回すと、呼び出し元の作業に紛れて出ないまま終わる)。検証 subagent と構造 reviewer の返答の `unread_targets` に挙がった file は、どちらのレビューで読めなかったかを添えて未レビューとして別に挙げる — findings や `structural_proposals` が 0 件でも、その file はそのレビューを通っていない。構造 reviewer の `unread_inputs` に挙がった file は、構造提案の判断材料を欠いた file として未レビューとは分けて挙げる。

diff の提示に続けて、パッチごとに 1 行ずつ「種別・対象 file・対象 file の文字数の前後差」を書く (例: `書き換え / skills/payment/SKILL.md / 15227→15305 (+78)`。findings を反映した変更もパッチとして含め、反映の前後で `wc -m` を数える) — 人間が指示文の増え方を採否の材料にできるようにするため。追加には、どの既存概念にも吸収できなかった理由を添える。報告だけにしたパッチは前後差の代わりに案を書く (分割は切り出す範囲と leading word)。

起動できなかった側と JSON を得られなかった側は、手順 4 で書いた `<検証|構造>レビュー未実施:` 行をそのまま報告する (書式と置き場の正本は、JSON を得られなかった側は手順 4 の「JSON が得られないとき」、起動できなかった側は手順 4 の名前が解決しないときの段落)。`structural_proposals` は findings へ混ぜず別立てで報告し (severity と impact / migration_cost は別の評価軸)、提案ごとに `${CLAUDE_SKILL_DIR}/references/structure.md` の「impact / migration_cost の 2 軸」の採否の目安で採用候補かを添える — 構造 reviewer は目安で絞らずに全件返すので、採否はここで決める。採用候補とした提案も編集せず案として返す — 当てる先は手順 1 の書き換える対象の外にあり、反映は呼び出し元の判断になる。**検証は 1 ラウンドで終える** — 反映後に subagent を起動し直す再検証ループは回さない。

## 根拠

- 出典一覧 (`sources.md`) は `claude-config-reference` の `references/` が 1 正本。本 skill は複製せず参照する
