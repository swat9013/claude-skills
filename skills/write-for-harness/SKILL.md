---
name: write-for-harness
# model からも起動させるのは、harness の指示文を書く作業が他 skill の作業中に
# 発生するため (disable-model-invocation を付けるとその経路で規範が読まれないまま書かれる)
user-invocable: true
argument-hint: "[<path> | 新規: <component> <目的>]"
description: CLAUDE.md / .claude/rules/ / SKILL.md 指示文 / hook 注入文 / agent 定義 (Inferential harness) を規範に照らして書き、検証 subagent と構造 reviewer の指摘まで反映する手順。settings / hook script (Computational) は対象外。Use when「CLAUDE.md に規範を足す・直す」「rules を書く・直す」「skill の指示文を書く・直す」「hook の注入文を書く・直す」「agent 定義を書く・直す・subagent を切り出す」「変更を決めずに指示文の file 全体を見直す」.
---

# write-for-harness

Inferential harness (CLAUDE.md / `.claude/rules/` / SKILL.md 指示文 / hook 注入文 / agent 定義) を書く手順。決定論的に動く Computational 側 (settings / permission / hook script / gate) は本 skill の対象外。Computational を書く規範は呼び出し元のフローに委ね、レビュー手順は `claude-config-review` が持つ。

呼び出し元向けの契約 (渡す args・検証の範囲の宣言・反映を終えられなかったときの返し方、戻った後に通す変更 path の照合など) は `${CLAUDE_SKILL_DIR}/references/caller-contract.md` に置いてある。他の skill からでも自分の session から直接でも、同じ file を同じ変更のまとまり (CL を切る運用ならその CL) でもう一度 invoke するときは、invoke の前に契約の「検証の範囲」を Read して `省く` を宣言するかを決める — 本 skill は前の invoke を数えないので、宣言が無いと毎回検証が走る。人が文面を承認した変更を書かせるときも、invoke の前に同節を Read して `文面は承認済み` を宣言する。

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

候補を引数に並べて次の script を起動し、stdout の `<path> - <component>` と stderr の `分類外: <path>` で振り分ける。path のパターンで component を決める正本はこの script で、args の path も同じ script に通す。args の path が `分類外:` になり、下の hook の script にも当たらなければ、呼び出し元が invoke 直前の同じ turn で宣言した component を使い、宣言が無ければ分類外であることを告げて止まる (返し方は手順 5 の「反映を終えられなかったとき」)。

```bash
${CLAUDE_SKILL_DIR}/scripts/list-harness-inventory.py <path>...
```

`skill` / `claude-md` / `rules` / `agent` と出た file を対象にする。`hook` と出た file と、`分類外:` と出た file のうち hook の設定 (`hooks.json`・settings の `hooks`) の command が指す script は中身を読み、hook が Claude へ届ける注入文を持つ (届く経路は問わない) なら hook-inject として対象にする。`settings` と出た file と、注入文を持たない hook script は Computational なので対象から外し、「Computational は対象外」と、冒頭の段落にある書く規範とレビューの行き先を添えて告げてから残りを進める。args なしで合致 0 件なら「対象なし」で正常終了する (args の file が外れたときは手順 5 の「反映を終えられなかったとき」の形で返す)。上の 2 つの script のどちらかが exit 非 0 で終わったときは列挙の失敗なので、対象なしとせずに止まる (返し方は同じ)。

**書き換える対象は広げない** — 編集するのは args か、上の script の出力から出た file だけに閉じる (手順 4 の構造 reviewer は harness 全体を俯瞰するが、俯瞰は提案の入力であって編集対象ではない)。例外は外出しのパッチで、対象 skill の `references/` に新しく作る file だけを足してよい。その file は元の SKILL.md と同じ component (skill) として手順 4 のレビュー対象に含める。

### 2. 文書構成の正本を読む

`Skill(swat-skills:writing-for-agents)` を invoke する。手順 2・3 の規範は、`省く` の宣言に関わらず invoke ごとに context に揃える — 宣言が狭めるのは手順 4 の検証だけ。同じ session の前の invoke で得た invoke・Read の結果が今の context に見えていれば、読み直さずにそれを使い、手順 5 の報告に `規範の Read: 前の invoke の結果を使用` と書く (読み直しても同じ内容が context に二重に載るだけ)。compaction で要約されているか、前の invoke の後にその reference を編集していれば、invoke・Read し直す (要約は中身を欠き、編集の前の結果は古い)。

構成の正本を読めなければ手順 3 以降へ進まず、手順 5 の「反映を終えられなかったとき」の形で返して終える。規範が欠けた状態で書いた文面は、欠けたことが誰にも見えないまま harness に残り続ける。

### 3. 規範を読む

component の責務 reference を Read する。

| component | Read する reference |
|---|---|
| skill | `${CLAUDE_SKILL_DIR}/references/components/skill.md` |
| claude-md | `${CLAUDE_SKILL_DIR}/references/components/claude-md.md` |
| rules | `${CLAUDE_SKILL_DIR}/references/components/rules.md` |
| hook-inject | `${CLAUDE_SKILL_DIR}/references/components/hook-inject.md` |
| agent | `${CLAUDE_SKILL_DIR}/references/components/agent.md` |

component が skill で、手順が subagent を起こすなら (新規作成を含む)、agent の reference も Read する — 起動の規範は agent の reference が持つので、skill の reference だけでは照らせない。

加えて文面規範 `${CLAUDE_SKILL_DIR}/references/writing.md` を Read する。

続けて対象モデルの差分を読む。対象 file を次の script に渡し、`reference` 行の file を Read する。`判断` 行が出たときと、新規作成で file がまだ無いときは、`${CLAUDE_SKILL_DIR}/references/models.md` の「文面の基準モデル」節で対象モデルを決める。読む file は writing.md 末尾の表で引く。script が exit 非 0 で終わったときも、同じく models.md の節で決める。モデル選定そのもの (能力帯・単価比・モデル ID) も models.md が正本。

```bash
${CLAUDE_SKILL_DIR}/scripts/resolve-target-model.py <対象 file>
```

書く前に `${CLAUDE_SKILL_DIR}/references/patches.md` を Read し、変更をパッチ 7 種の候補から選ぶ。7 種は、追加 / 書き換え / 削除 / 統合 / 外出し / 分割 / Computational 化。パッチの診断と当て方は次の規則に従う。

- **診断する行**: 依頼が名指しした変更と、その変更を吸収する行までにする — 呼び出し元が承認したのはその変更だけなので、file 全体の見直しを依頼されたときだけ全行を診断する
- **節の書き直し**: 呼び出し元が invoke 直前の同じ turn で、本文の見直しとして節の書き直しを宣言したときだけ当てる。宣言が無ければ項目単位の差分にする
- **報告に回すパッチ**: 次の 4 つは編集せず、手順 5 で報告する。
  - patches.md が報告だけにするとしたパッチ
  - 診断の範囲外の行へのパッチ
  - 手順 1 の対象外の file への吸収
  - 項目単位に割れない、宣言の無い節の書き直し

  報告に回したものが宣言された変更 (定義は手順 5 の「反映を終えられなかったとき」) に当たれば、手順 5 の「反映を終えられなかったとき」の形でも返す
- **控え**: 最初のパッチを当てる前に、対象 file の中身を作業ツリーの外の file へ控える。`省く` の宣言があれば取らない — 手順 4 が起動しないので使わず、呼び出し元が 1 組の検証のために取った控えを上書きしうる。手順 4 は、この控えからの diff を検証に渡す (invoke より前の未 commit の変更を diff に混ぜない)。対象 file が git repo の中にあれば `--repo <その repo root>` も渡す。`--keep` に渡していない file (外出しで新しく作る file) は、控えた HEAD の中身 (無ければ空) が diff の起点になる

  ```bash
  ${CLAUDE_SKILL_DIR}/scripts/snapshot-changes.py take <控え file> --keep <対象 file>... [--repo <repo root>]
  ```

- **文字数の計測**: パッチ (手順 5 で findings を反映する変更を含む) は 1 つずつ当て、その直前と直後の対象 file の文字数を `wc -m` で数える。複数の Edit を 1 つの message や 1 回の script にまとめて当てない (前後差が混ざり、手順 5 の報告でパッチ単位の前後差を書けない)。新規作成の file は、書く前を 0 とする。
- **決定的な検査**: 書き終えたら、対象 file に下の script を当てる。出力のうち、自分が書いた文に当たった行を直してから、手順 4 へ進む。次の 3 つは直さず、当たりを手順 5 の報告に `検査の当たり:` の行で載せる: 既存の文の語を替えただけの行 / 直すと意味が変わる行 / `文面は承認済み` と宣言された文面

```bash
${CLAUDE_SKILL_DIR}/scripts/check-instruction-text.py lint <対象 file>
```

新規作成で「どの機構に載せるか」から決めるなら、4 slot 表 (`guide` / `sensor` × `Computational` / `Inferential`) を `${CLAUDE_SKILL_DIR}/references/architecture.md` で引く。

変更の届け方は実行 project の運用に従う。

### 4. 検証 subagent と構造 reviewer を並列で起動する

呼び出し元が invoke 直前の同じ turn で `省く` と宣言していれば、どちらも起動せずに手順 5 へ進み、手順 5 の報告に `検証省略: 呼び出し元の宣言` と書く。宣言が無ければ、下の各 subagent の条件で起動する。

起動するものは `Agent` tool で同一 message 内で起動し、報告は各 subagent の最終メッセージで受け取る。どちらも結果を受け取るだけの役割なので `name` を付けない。深さは agent 定義が持つので `model` / `effort` 引数は渡さない。`name` と `model` / `effort` が外れた呼び出しには、Agent の PreToolUse hook が案内を返す。手順・返答の schema も agent 定義が持つので、prompt には下の script が組み立てた stdout だけを渡す。

- **検証 subagent**: 書いた file の文面を component 別 reference に照らし、当たる設計 doc があれば (`設計 doc は後段` の宣言が無いとき) その設計 doc にも照らす。条件なしで起動する。`subagent_type: swat-skills:harness-component-reviewer` で指名する
- **構造 reviewer**: 書いた内容の**配置**が正しいかを harness 全体の中で見る。この invoke が新規作成か、当てたパッチに追加・外出しがあれば、対象が 1 ファイルでも 1 つ起動する。無ければ起動せず、手順 5 の報告に `構造レビュー省略: 配置が新しく決まるパッチなし` と書く — 書き換え・削除・統合と、報告だけにしたパッチ (分割を含む) は既存の行の置き場を変えないので、配置を見る対象が無い。`subagent_type: swat-skills:harness-structure-reviewer` で指名する

名前が解決しないときは `general-purpose` で代替しない — 代替すると model・effort・tools が呼び出し元の値へ黙って戻り、指名した深さで見たように見える。2 つの結果は互いに独立しているので、解決した側の結果だけで手順 5 へ進む。解決しなかった側の結果を報告する位置 (検証 subagent なら findings、構造 reviewer なら `structural_proposals`) に、代わりに `<検証|構造>レビュー未実施: <error が挙げた available agents>` と次の手順 (swat-skills plugin の agent が読み込まれる状態にしてからレビューをやり直す) を書く。

2 つの prompt は次の script で組み立て、stdout をそのまま prompt にする。script が exit 非 0 で終わったら、その subagent は起動しない。結果の位置には `<検証|構造>レビュー未実施: prompt を作れなかった (<stderr の 1 行>)` を書く。次の手順は、stderr の原因を直してから、その subagent だけをやり直すこと。

```bash
${CLAUDE_SKILL_DIR}/scripts/build-review-prompts.py verify --component <component> --target <対象 file>... --snapshot <手順 3 の控え file> --model-ref <手順 3 で Read した対象モデルの差分 file>... [--agent-ref] [--range <file>=<行範囲>...] [--design-later | --design-doc <置き場> <索引> <照らす対象>...]
${CLAUDE_SKILL_DIR}/scripts/build-review-prompts.py structure --target <対象 file>...
```

- `--agent-ref`: 手順 3 の条件で agent の reference も Read したとき
- `--range`: 手順 3 で診断した行範囲 (パッチを当てた後の file の行番号。例: `<file>=64-68,81-86`。空白を入れるなら値ごと引用する)。file 全体の見直しの依頼・新規作成で全行を診断した file には渡さない — 診断していない行への指摘は手順 5 で Noted にしかならず、人が読む量だけを増やす
- `--design-later` / `--design-doc`: 下の、`設計 doc は後段` の宣言を扱う段落で決める
- structure の一覧 (path + component) は script が全件埋めるので、手で足さない。今回新しく書いた未追跡の file と git 管理外の `~/.claude/` 系 path も、`--target` に渡せば一覧に入る

返答で期待する JSON の鍵は、検証 subagent が `findings`、構造 reviewer が `structural_proposals`。未実施行はその鍵の位置に置く (読み方とやり直しは下の「返答の読み方」)。

呼び出し元が invoke 直前の同じ turn で `設計 doc は後段` と宣言していれば、設計 doc を決めずに `--design-later` を渡し、手順 5 の報告に対象の file ごとの `設計 skip: <file>: 呼び出し元の宣言` と書く。宣言が無ければ、起動前に main agent がレビュー対象の file ごとに当たる設計 doc を決める。file を持つ git repo の `docs/agents/domain.md` に節「設計 doc の置き場」があれば、原則 leaf `${CLAUDE_SKILL_DIR}/../principle-index/references/leaves/principle-design-conformance.md` を Read し、その「設計 doc の置き場の引き方」で当たる行を決める。当たる行が 1 つでもあれば、規範の優先順位の leaf `${CLAUDE_SKILL_DIR}/../principle-index/references/leaves/principle-norm-precedence.md` も Read する (2 つの leaf は手順 5 の区分に使う)。当たる行ごとに `--design-doc <置き場の絶対 path> <索引列の file の絶対 path (空なら '')> <その行の適用範囲に当たるレビュー対象の絶対 path (複数なら , 区切り)>` を渡す — 適用範囲の外の file に設計 doc を当てると、その file に当たらない指摘が出る。置き場か索引の file が無い行は渡さず、その行の適用範囲に当たる file ごとに手順 5 の報告へ `設計 skip: <file>: 設計 doc 不在: <path>` と書く。domain.md の行に 1 つも当たらない file は、手順 5 の報告に `設計 skip: <file>: <理由>` (git repo の外 / `docs/agents/domain.md` か節が無い / 節が設計 doc を案内しない / 当たる行が無い) と書く — 置き場の宣言は repo ごとに無いことが正常なので、止めずに文面の検証だけを走らせる。

**返答の読み方** (検証 subagent と構造 reviewer に共通): 最終メッセージを作業ツリーの外の file に書き、次の script に渡す。やり直した後の返答なら `--retried` を付ける。script は前後の文を外して JSON を読み、期待した鍵があるかを `判定` の行に出す。`判定` が `得た` 以外なら `${CLAUDE_SKILL_DIR}/references/review-retry.md` を Read し、その手順で、やり直すか `<検証|構造>レビュー未実施:` 行を書くかを決める。ほかの行は手順 5 で使う。

```bash
${CLAUDE_SKILL_DIR}/scripts/classify-review-reply.py <検証|構造> <最終メッセージの file> [--retried]
```

### 5. 指摘を反映して終える

findings を 1 件ずつ次の区分に分け、Act on だけを対象ファイルへ反映する。反映を終えたら、反映で書いた文に手順 3 の決定的な検査をもう一度当てる。分けるのは本 skill を実行している session。範囲内は、手順 3 で診断した行と、変更が他の行と生んだ食い違いを指す。呼び出し元が invoke 直前の同じ turn で不採用と宣言した指摘 (同じ箇所に同じ reference の行を当てた指摘) は、範囲の内外を問わず Noted に置く。対応がはっきりしない指摘は Consider に置き、理由を添える。

| 区分 | 当てる指摘 | 扱い |
|---|---|---|
| Act on | 範囲内の指摘で、reference の行を逐語で引ける規範違反 | 反映する。呼び出し元が invoke 直前の同じ turn で `文面は承認済み` と宣言していれば反映せず、区分名を `Act on (承認済みの文面のため反映せず)` と書いて返す — 人が承認した文面を書き手が直すと、人が読んでいない文面が書かれる |
| Consider | 範囲内の指摘のうち Act on に当たらないもの (reference を引けない改善余地・info・誤検知か意図的な設計とみた指摘) | 反映せず、理由を添えて返す。直すかは呼び出し元が決める |
| Noted | 範囲外の指摘と、不採用と宣言された指摘 | 反映せず、記録として返す |

設計 doc の指摘 (category が `conformance` / `design drift`) は、範囲内なら手順 4 で Read した principle-design-conformance の区分で置く。同 leaf が agent が直すとする指摘は Act on (引く reference の行は設計 doc の行) にし、人へ返すとする指摘は Consider に「方針の変更 — 人へ返す」を理由に添えて置く — 設計 doc を変えるのは人の決定なので、本 skill も呼び出し元も設計 doc を書き換えて解消しない。設計 doc と他の規範が食い違う指摘は principle-norm-precedence で裁き、設計 doc が勝たなければ Consider に置いて、勝った規範の行か、優先順位で決まらないことを理由に添える。

指摘はどれもこの 3 区分のどれかに置き、外したい指摘も Consider に理由を添えて置く — 指摘を落とせるのは反証だけで、本 skill は反証の工程を持たない。

**反映後、本 skill を終える返答に diff と、区分名を付けて 1 件ずつ並べた findings を載せる** (呼び出し元が人間でも skill でも、反映を人間の目に載せる唯一の面がここになる。後の turn へ回すと、呼び出し元の作業に紛れて出ないまま終わる)。返答の後も作業を続ける呼び出し元は、この返答を自分の最終報告へ逐語で運ぶ (契約の「返答を呼び出し元の報告へ運ぶ」) ので、契約 (`${CLAUDE_SKILL_DIR}/references/caller-contract.md`) の同節を Read し、同節が挙げる項目ごとに見出しを付けて別立てに並べる — 1 つの段落に混ぜると、写すときに項目ごと落ちる。`classify-review-reply.py` が `未レビュー` の行に出した file は、どちらのレビューで読めなかったかを添えて未レビューとして別に挙げる。findings や `structural_proposals` が 0 件でも、その file はそのレビューを通っていない。`判断材料を欠いた file` の行の file は、構造提案の判断材料を欠いた file として未レビューとは分けて挙げる。

diff の提示に続けて、パッチごとに 1 行ずつ「種別・対象 file・対象 file の文字数の前後差」を書く (例: `書き換え / skills/payment/SKILL.md / 15227→15305 (+78)`。数え方と数える対象は手順 3 の「文字数の計測」) — 人間が指示文の増え方を採否の材料にできるようにするため。追加には、どの既存概念にも吸収できなかった理由を添える。報告だけにしたパッチは、前後差の代わりに案を書く。分割と Computational 化の案には、patches.md の種別表が報告に求めるものを含める。直さなかった検査の当たりは、`検査の当たり:` の行で 1 件ずつ続ける。

起動できなかった側と JSON を得られなかった側は、手順 4 で書いた `<検証|構造>レビュー未実施:` 行をそのまま報告する。`structural_proposals` は findings へ混ぜず別立てで報告する (severity と impact / migration_cost は別の評価軸)。提案ごとに、`classify-review-reply.py` が `構造提案` の行に出した採用候補かの判定を添える。構造 reviewer は目安で絞らずに全件返すので、採否はここで決める。目安は `${CLAUDE_SKILL_DIR}/references/structure.md` の「impact / migration_cost の 2 軸」が持つ。`判定不能` の提案は、値が schema の外であることを添えて返す。採用候補とした提案も編集せず案として返す — 当てる先は手順 1 の書き換える対象の外にあり、反映は呼び出し元の判断になる。**検証は 1 ラウンドで終える** — 反映後に subagent を起動し直す再検証ループは回さない。

#### 反映を終えられなかったとき

宣言された変更 (呼び出し元が invoke 直前の同じ turn で宣言した変更。宣言が無ければ依頼が名指した変更) のうち、対象 file に書かなかったものが 1 つでもあれば、理由を問わず、本 skill を終える返答の先頭に次の形を置く。手順 1・2 で止まったときも、その場でこの形を返して終える。

```
未反映: <書かなかった変更 (宣言の文面で示す)> — <止まった手順と、止めた条件か報告に回した理由>
復旧: <次に行う手順>
```

書かなかった変更 1 つにつき `未反映:` 行を 1 行置き、`復旧:` 行は最後に 1 行置く。宣言された変更が無いまま止まったとき (args なしで列挙に失敗したなど) は、`<書かなかった変更>` に「自動検出の対象 (列挙できず不明)」と書く。たとえば、書かずに止まった (手順 1 の分類外・列挙の失敗・args の file が Computational で外れた、手順 2 の正本を読めない) ときと、書かずに報告に回した (手順 3 の、patches.md が報告だけにするパッチ・手順 1 の対象外の file への吸収・項目単位に割れない、宣言の無い節の書き直し) ときがこれに当たる。呼び出し元はこの行で、承認された変更が反映されなかったと判断する — 報告の中の案に埋もれさせると、承認された変更が黙って報告に化ける。

本 skill が自分の診断で見つけた宣言の外の行・file へのパッチを報告に回したことと、`<検証|構造>レビュー未実施:` 行は、この行に入れない — 宣言された変更は書かれており、それぞれ上の報告に別の行で載る。args なしの自動検出で「対象なし」になったときは宣言された変更が無いので、正常終了のままにする。
