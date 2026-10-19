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

本 skill を呼び出すときの呼び出し口 (渡す args・invoke の回数・検証の範囲の宣言・反映を終えられなかったときの返し方) は、呼び出し元向けに `${CLAUDE_SKILL_DIR}/references/caller-contract.md` に置いてある。同じ file を同じ変更のまとまり (CL を切る運用ならその CL) でもう一度 invoke するときは、他の skill からでも自分の session から直接でも、invoke の前にこの契約の「検証の範囲」を Read して宣言を選ぶ — 本 skill は前の invoke を数えないので、宣言が無いと毎回 `全体` の検証が走る。

## 手順

### 1. 対象を決める

| args | 対象 |
|---|---|
| path (相対 / 絶対 / `~/`) | その file。`~/` は `$HOME` へ展開する |
| `新規: <component> <目的>` | 新規作成。`<component>` は skill / claude-md / rules / hook-inject / agent のいずれか |
| なし | 下記の自動検出 |

args なしのときは、次の script が出す絶対 path (1 行 1 件) のうち、Inferential 5 種のパターンに合致するものだけを対象にする。出力には作業ツリーの変更に加えて、branch で commit 済みの変更も入る。

```bash
${CLAUDE_SKILL_DIR}/scripts/list-changed-files.py
```

| パターン (glob) | component |
|---|---|
| `**/SKILL.md` | skill |
| `**/CLAUDE.md` | claude-md |
| `**/.claude/rules/*.md`, `**/rules/*.md` | rules |
| hook が Claude へ届ける注入文を持つ script (届く経路は問わない。中身を読んで決める) | hook-inject |
| `**/.claude/agents/*.md`, plugin root (`.claude-plugin/` を持つディレクトリ) 直下の `agents/*.md` | agent |

`**/settings*.json` と、注入文を持たない hook script は Computational なので対象から外し、「Computational は対象外」と、冒頭の段落にある書く規範とレビューの行き先を添えて告げてから残りを進める。合致 0 件なら「対象なし」で正常終了する。script が exit 非 0 で終わったときは列挙の失敗なので、対象なしとせずに止まる。

**書き換える対象は広げない** — 編集するのは args か、上の script の出力から出た file だけに閉じる (手順 4 の構造 reviewer は harness 全体を俯瞰するが、俯瞰は提案の入力であって編集対象ではない)。例外は外出しのパッチで、対象 skill の `references/` に新しく作る file だけを足してよい。その file は元の SKILL.md と同じ component (skill) として手順 4 のレビュー対象に含める。

### 2. 文書構成の正本を読む

`Skill(swat-skills:writing-for-agents)` を invoke する。

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

書く前に `${CLAUDE_SKILL_DIR}/references/patches.md` を Read し、変更をパッチ 6 種 (追加 / 書き換え / 削除 / 統合 / 外出し / 分割) の候補から選ぶ。診断する行は、依頼が名指しした変更と、その変更を吸収する行までにする — 呼び出し元が承認したのはその変更だけなので、file 全体の見直しを依頼されたときだけ全行を診断する。patches.md が報告だけにするとしたパッチ、診断の範囲外の行へのパッチ、手順 1 の対象外の file への吸収は編集せず、手順 5 で報告する。no-op の削除は、patches.md の診断表が求める確認 (suite か micro-test) を書く前に通したもの (呼び出し元が通したと宣言したものを含む) だけを当て、通していないものは報告に回す。パッチを当てるたびに、当てる前と後の対象 file の文字数を `wc -m` で数える — 手順 5 の報告で前後差を書く。

新規作成で「どの機構に載せるか」から決めるなら、4 slot 表 (`guide` / `sensor` × `Computational` / `Inferential`) を `${CLAUDE_SKILL_DIR}/references/architecture.md` で引く。

変更の届け方は実行 project の運用に従う。

### 4. 検証 subagent と構造 reviewer を並列で起動する

呼び出し元が invoke 直前の同じ turn で検証の範囲を宣言していれば、それに従う。宣言が無ければ `全体` とする。

| 宣言 | 起動するもの |
|---|---|
| `全体` | 検証 subagent と構造 reviewer の両方 (以下の手順どおり) |
| `増分` | 検証 subagent だけ。prompt の末尾に「レビューするのは、次の行範囲と、その範囲が他の行と食い違いを生む箇所だけ: <この invoke で変更した行範囲>」を足す。手順 5 の報告に `構造レビュー省略: 呼び出し元の宣言 (増分)` と書く |
| `省く` | どちらも起動せず、手順 5 へ進む。手順 5 の報告に `検証省略: 呼び出し元の宣言` と書く |

起動するものは `Agent` tool で同一 message 内で起動する。どちらも結果を受け取るだけの役割なので `name` を付けず、報告は各 subagent の最終メッセージで受け取る (SendMessage での報告は求めない)。model・effort・手順・返答の schema は agent 定義が持つので、Agent tool の `model` / `effort` 引数は渡さず、prompt には下の雛形の入力だけを渡す。

- **検証 subagent**: 書いた file の文面を component 別 reference に照らす。`subagent_type: swat-skills:harness-component-reviewer` で指名する
- **構造 reviewer**: 書いた内容の**配置**が正しいかを harness 全体の中で見る。`全体` では、**対象が 1 ファイルでも常に 1 つ起動する**。`subagent_type: swat-skills:harness-structure-reviewer` で指名する

名前が解決しないときは `general-purpose` で代替しない — 代替すると model・effort・tools が呼び出し元の値へ黙って戻り、指名した深さで見たように見える。2 つの結果は互いに独立しているので、解決した側の結果だけで手順 5 へ進む。解決しなかった側の結果を報告する位置 (検証 subagent なら findings、構造 reviewer なら `structural_proposals`) に、代わりに `<検証|構造>レビュー未実施: <error が挙げた available agents>` と次の手順 (swat-skills plugin の agent が読み込まれる状態にしてからレビューをやり直す) を書く。

構造 reviewer の入力になる「harness 全体一覧 (path + component)」は、起動前に main agent が決定論的コマンドで作る:

```bash
git ls-files
```

の出力を手順 1 の component パターン + `**/settings*.json` で濾し、合致した path に component 名を付けた一覧にする (skip 落ちは一覧にも載せない)。git 管理外の `~/.claude/` 系 path は、args で対象として渡された場合のみ一覧へ加える。一覧は prompt へ全件埋め込む — 各ファイルの全文 Read は reviewer が必要と判断したものだけが行う。

検証 subagent の prompt (入力):

```
component: <COMPONENT>

参照する reference (絶対 path):
- <COMPONENT_REF>
- <WRITING_REF>
- <MODEL_REF>

レビュー対象 (絶対 path):
- <PATH_1>
- ...

reference が公式 docs の確認を求めていても、外部の確認を試みず reference に書かれた内容の範囲で判定する。
```

`<COMPONENT>` / `<PATH_*>` / 各 `*_REF` は起動時に埋める。`<COMPONENT_REF>` と `<WRITING_REF>` は手順 3 で Read した component 別 reference と文面規範。`<MODEL_REF>` は手順 3 で決めた対象モデルの差分 file で、writing.md の表が 2 本を挙げるモデルでは両方を並べる。返答で期待する JSON の鍵は `findings` で、未実施行は findings の位置に置く (返答の読み方とやり直しは下の「JSON が得られないとき」)。

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

指摘はどれもこの 3 区分のどれかに置き、外したい指摘も Consider に理由を添えて置く — 指摘を落とせるのは反証だけで、本 skill は反証の工程を持たない。

**反映後に diff を提示する** (呼び出し元が人間でも skill でも、反映を人間の目に載せる唯一の面がここになる)。検証 subagent と構造 reviewer の返答の `unread_targets` に挙がった file は、どちらのレビューで読めなかったかを添えて未レビューとして別に挙げる — findings や `structural_proposals` が 0 件でも、その file はそのレビューを通っていない。構造 reviewer の `unread_inputs` に挙がった file は、構造提案の判断材料を欠いた file として未レビューとは分けて挙げる。

diff の提示に続けて、パッチごとに 1 行ずつ「種別・対象 file・対象 file の文字数の前後差」を書く (findings を反映した変更もパッチとして含め、反映の前後で `wc -m` を数える) — 人間が指示文の増え方を採否の材料にできるようにするため。追加には、どの既存概念にも吸収できなかった理由を添える。報告だけにしたパッチは前後差の代わりに案を書く (分割は切り出す範囲と leading word)。

起動できなかった側と JSON を得られなかった側は、手順 4 で書いた `<検証|構造>レビュー未実施:` 行をそのまま報告する (書式と置き場の正本は、JSON を得られなかった側は手順 4 の「JSON が得られないとき」、起動できなかった側は手順 4 の名前が解決しないときの段落)。`structural_proposals` は findings へ混ぜず別立てで報告し (severity と impact / migration_cost は別の評価軸)、提案ごとに `${CLAUDE_SKILL_DIR}/references/structure.md` の「impact / migration_cost の 2 軸」の採否の目安で採用候補かを添える — 構造 reviewer は目安で絞らずに全件返すので、採否はここで決める。**検証は 1 ラウンドで終える** — 反映後に subagent を起動し直す再検証ループは回さない。

## 根拠

- 出典一覧 (`sources.md`) は `claude-config-reference` の `references/` が 1 正本。本 skill は複製せず参照する
