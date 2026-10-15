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

他の skill から承認済みの変更を書かせるときの呼び出し口 (渡す args・invoke の回数・反映を終えられなかったときの返し方) は、呼び出し元向けに `${CLAUDE_SKILL_DIR}/references/caller-contract.md` に置いてある。

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

`Agent` tool で次の 2 つを同一 message 内で起動する。どちらも結果を受け取るだけの役割なので `name` を付けず、報告は各 subagent の最終メッセージで受け取る (SendMessage での報告は求めない)。model・effort・手順・返答の schema は agent 定義が持つので、Agent tool の `model` / `effort` 引数は渡さず、prompt には下の雛形の入力だけを渡す。

- **検証 subagent**: 書いた file の文面を component 別 reference に照らす。`subagent_type: swat-skills:harness-component-reviewer` で指名する
- **構造 reviewer**: 書いた内容の**配置**が正しいかを harness 全体の中で見る。**対象が 1 ファイルでも常に 1 つ起動する**。`subagent_type: swat-skills:harness-structure-reviewer` で指名する

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

`<COMPONENT>` / `<PATH_*>` / 各 `*_REF` は起動時に埋める。`<COMPONENT_REF>` と `<WRITING_REF>` は手順 3 で Read した component 別 reference と文面規範。`<MODEL_REF>` は手順 3 で決めた対象モデルの差分 file で、writing.md の表が 2 本を挙げるモデルでは両方を並べる。返答の前後に文が付いていたら外して JSON を読む。`findings` を持つ JSON が無いとき (`unread_refs` だけを返した・空) は起動し直さず、findings の位置に `検証レビュー未実施: <理由>` と次の手順を書く — 次の手順は、`unread_refs` なら「prompt に `${` が残っていないかを確かめ、`*_REF` を手順 3 で実際に Read した path で埋め直してやり直す」、それ以外は「検証 subagent だけをやり直す」。

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

`<PATH_*>` と `<INVENTORY>` (一覧の全件) は起動時に埋める。返答の前後に文が付いていたら外して JSON を読む。`structural_proposals` を持つ JSON が無いとき (`unread_refs` だけを返した・空) は、構造 reviewer だけを 1 回やり直す — `unread_refs` なら、先に prompt に `${` が残っていないかを確かめ、参照 path を上の prompt どおりに埋め直す。やり直しても得られなければ起動し直さず、`structural_proposals` の位置に `構造レビュー未実施: <理由>` と次の手順 (`unread_refs` なら「挙がった path が実在するかを確かめてから構造 reviewer をやり直す」、それ以外は「構造 reviewer だけをやり直す」) を書く。

**この SKILL.md をロードした時点で `${CLAUDE_SKILL_DIR}` は絶対 path へ置換済み**なので、どちらの prompt も本文に見えている path をそのまま写す — 変数表記を復元して渡すと subagent 側では展開されず、reference を 1 本も読めない (どちらの subagent も `unread_refs` を返して止まり、レビューは行われない)。送信前に prompt へ `${` が残っていないことを確かめる。

### 5. 指摘を反映して終える

findings のうち採用したものを対象ファイルへ反映し、**反映後に diff を提示する** (呼び出し元が人間でも skill でも、反映を人間の目に載せる唯一の面がここになる)。採用しなかったものは理由付きで報告する。検証 subagent の返答の `unread_targets` に挙がった file は、未レビューとして別に挙げる — findings が 0 件でも、その file は検証を通っていない。

diff の提示に続けて、パッチごとに 1 行ずつ「種別・対象 file・対象 file の文字数の前後差」を書く (findings を反映した変更もパッチとして含め、反映の前後で `wc -m` を数える) — 人間が指示文の増え方を採否の材料にできるようにするため。追加には、どの既存概念にも吸収できなかった理由を添える。報告だけにしたパッチは前後差の代わりに案を書く (分割は切り出す範囲と leading word)。

起動できなかった側と JSON を得られなかった側は、その結果の位置に手順 4 で決めた `<検証|構造>レビュー未実施:` 行を置く。`structural_proposals` は findings へ混ぜず別立てで報告する (severity と impact / migration_cost は別の評価軸で、採否の目安は `references/structure.md` の「impact / migration_cost の 2 軸」)。**検証は 1 ラウンドで終える** — 反映後に subagent を起動し直す再検証ループは回さない。

## 根拠

- 出典一覧 (`sources.md`) は `claude-config-reference` の `references/` が 1 正本。本 skill は複製せず参照する
