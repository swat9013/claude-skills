---
name: ready-for-agent-review
description: issue 本文と triage コメントを対象 repo の規範と照らし、規範違反の指定・本文の要素の未閉鎖・規範同士の衝突を反証済みの修正提案として issue ごとに返す (issue を N 件まとめて受け、tracker へは書き込まない). Use when「着手可 (`ready-for-agent`) にする前に issue を規範と照合する」「深掘りした issue を着手可 (`ready-for-agent`) にする」「issue 番号を渡して本文を再レビューする」.
user-invocable: true
---

# ready-for-agent-review

issue N 件 (1 件以上) の本文と triage コメントを、対象 repo の規範と照らして検査する。反証を通った指摘だけを、issue ごとの markdown の提案として返す。AFK (away from keyboard — 人が張り付かずに進める) agent は issue を正として実装し、実装後のレビューも issue を正として照合する。そのため issue 自体が規範に反していると、欠陥は着手前のこの時点でしか捕まらない。

- **自分の保証**: tracker への書き込み (label 操作・コメント投稿・本文編集) はゼロ。提案をどう反映するか (本文を書き換えるか、label を動かすか) は呼び出し元の手順が決める — 人間の承認を通す呼び出し元も、AFK 境界の内側で自分で反映する呼び出し元もある。follow-up issue の文案を起票するかとその手順も呼び出し元が決める
- **やらないこと**: 深掘り対話に回す判断 (裁定しない衝突・設計 doc の変更が要る指定・design drift) の裁定と修正案、triage の分類そのもの、実装差分のレビュー
- 入口は 3 つ: triage 手順からの invoke、深掘り (対話) で論点を確定した後・`ready-for-agent` にする前の invoke、人が issue 番号を渡して行う再レビュー。どの入口でも手順は同じで、triage 以外の入口は N=1 で呼んでよい。同じ会話で書き換え案を検査し直す再 invoke も手順 1〜5 を実行し (各手順の中で何を省けるかは、その手順が書く条件だけで決まる)、手順 5 のブロックを返して終える — 再 invoke ではこの SKILL.md が再掲されないので、結論だけを自由文で返すと、検査を通っていない書き換え案が指摘 0 件として反映される
- **対象 repo** とは、issue の変更が入る repo (cwd の repo) を指す。issue 置き場とは別の repo のことがある。規範は対象 repo から読み、issue 置き場は issue の取得だけに使う

## 1. issue を読む

入力は issue 番号の列 (1 件以上。件数の上限は置かない)。issue 置き場は `${CLAUDE_SKILL_DIR}/../issue-tracker-operations/references/placement.md` を Read して決め、取得コマンドには同書のとおり `-R` で明示する。同書が止まるとしたときは、issue ごとに手順 5 の停止の形で、同書が渡すとしたものを停止理由に書いて返す。

issue ごとに番号・題名・本文・label・全コメントを逐語で読む。取得結果は次のとおり得て読む。先に tracker を、`fetch-result-keys.md` (下の 4 番目の項の path) を Read して同書の決め方で決める。gh / glab なら 1〜3 番目の項に、それ以外なら 4 番目の項に従う。

- 呼び出し元が見出しの直下の 1 行に `取得結果: <path>` を置いた issue は、その file (呼び出し元が取得の JSON 出力をそのまま保存したもの) を `${CLAUDE_SKILL_DIR}/../issue-tracker-operations/scripts/issue-fetch.py read <path> --number <N>` に通す — `--number` で見出しと file の対応を確かめないと、別の issue の本文を検査したまま見出しの issue へ結果を返す。この行は書き換え案に含めない
- この行が無い issue と、`read` が exit 2 で `取り直す:` を返した issue は取得する。`--tracker` には先に決めた tracker を渡す。`issue-fetch.py command --tracker <gh|glab> --repo <issue 置き場> <N>...` が出したコマンドを 1 本ずつ Bash で撃ち、出力を `mktemp` で作業ツリーの外に作った file に保存して `read` に通す
- 以降は `read` の出力 (共通の鍵の JSON) を読む。出力は手順 4 で使うので、作業ツリーの外の file に保存する。旧本文を残したコメントは `read` が `comments` から外し、`old_body_records` に分けてある
- tracker が gh / glab のどちらでもなければ、`${CLAUDE_SKILL_DIR}/../issue-tracker-operations/references/fetch-result-keys.md` を Read し、`取得結果:` の file があればそれを、無ければ同書のとおりに取得して、同書の表の鍵で読む (script には通さない)。file を使うのは、番号の鍵が見出しの `<N>` と一致し、同書の表の鍵がそろうときだけで、外れたら同書のとおりに取得し直す。1 行目が `旧本文の記録 (現行の仕様ではない)` のコメントは読まない。手順 4 の `--issue` には、読んだ値を JSON に書いて渡す。鍵は `read` の出力と同じ共通の鍵 (`issue-fetch.py --help` が並べる鍵のうち `title`・`labels`・`body`・`comments`) にする

検査対象は本文と triage コメント。triage コメントとは、分類・置き場・範囲・選択肢の採否を決めたり絞ったりしたコメントを指す。それ以外の議論コメントは、文脈として読むだけにする。旧本文を残したコメントは検査対象にも文脈にもしない — 現行の仕様は本文だけが持つ。

本文の末尾に、上の本文より優先すると宣言した追記の節 (`## 確定事項` 等) があれば、その宣言どおりに読む。その節が上の本文を絞った箇所は、食い違いではなく決定として読む。呼び出し元は issue ごとに `#<N>` の見出しを置き、書き換え案を添える issue は見出しの下 (取得結果の行があればその行の下) に書き換え後の本文全体の逐語を置く。書き換え案が添えられたら、tracker 上の本文の代わりにその文面を検査対象にする — tracker へ反映する前に検査するための入力で、tracker 上の本文はまだ変わっていない。取得に失敗した issue (取り直した file も `read` が `取り直す:` を返したものを含む) は手順 5 の停止の形で返し、残りの issue の検査を続ける。

## 2. 規範を揃える

規範は N 件分をまとめて 1 回だけ揃える。毎回読むものは issue に依らず同じで、issue の変更対象から引くものは全 issue の path を合わせて読む。

指摘の根拠にできる出典は 2 種類に限る: 対象 repo に書かれた規範の file:line と、この手順が名指しで読む plugin 同梱の基準 (principle leaf・component reference・本文の要素) の file:line。

**同じ会話での再 invoke**: 同じ会話の前回の invoke の後に呼ばれたら、読み始める前に `${CLAUDE_SKILL_DIR}/references/reinvoke.md` を Read し、そのとおりに前回読んだ規範を使い回すかを決める。

**毎回読むもの**:

- 対象 repo の `CLAUDE.md`。加えて、そこから索引されている構成の正本 (アーキテクチャ doc 等) のうち、カテゴリ分けと配置規則を定める節
- 依存方向・層の規則: 構成の正本のうち依存の向きを定める節と、それを検査する gate (`scripts/` 等の gate / lint 置き場にある script)。層の順位が gate の中にしか書かれていないこともあるので、doc だけで探し終えない
- 本文の要素 `${CLAUDE_SKILL_DIR}/../issue-tracker-operations/references/body-elements.md` (軸 ① と軸 ② の判定基準)
- 原則の索引 `${CLAUDE_SKILL_DIR}/../principle-index/SKILL.md` を Read し、索引が示す path から `principle-ai-delegation-boundary` (軸 ③ の AFK 境界) と `principle-norm-precedence` (軸 ③ の優先順位) と `principle-design-conformance` (軸 ① で設計 doc に照らすときの検査・区分と、設計 doc の置き場の引き方) を Read する

**issue の変更対象から引くもの**: issue が変更・新設する path を本文とコメントから抜き出す。path が明示されていなければ、主題語で対象 repo を grep して変更先の候補 path を特定する。path ごとに次を読む。

- 対象 repo の `.claude/rules/**/*.md` のうち、`${CLAUDE_SKILL_DIR}/../claude-config-reference/scripts/list-matching-rules.py --root <対象 repo の root> <repo root 相対の path>...` (全 issue の path を 1 回で渡す) が `rules` に返すもの (`paths` を持たない rules と、`paths` を読めず `notes` に挙がった rules も返る)
- harness の指示文の component の reference。全 issue の path を対象 repo の root からの絶対 path にし、ディレクトリは配下の該当 file (`SKILL.md`・`.claude/rules/*.md` など) の path に直して `${CLAUDE_SKILL_DIR}/../write-for-harness/scripts/list-harness-inventory.py <path>...` に渡す。stdout の `<path> - <component>` が `skill` / `claude-md` / `rules` / `agent` の path には `${CLAUDE_SKILL_DIR}/../write-for-harness/references/components/<component>.md` を読む。`hook` と出た path は中身を読み、Claude へ届ける注入文を持つなら同じ置き場の `hook-inject.md` を読む。`settings` と `分類外:` の path には component の reference を読まない。各 component の責務定義は、issue が置き場を指定するときの適合基準になる
- 対象 repo の `docs/agents/domain.md` が案内する ADR の置き場を、path・component 名・issue の主題語で grep して当たった ADR (案内が無ければ ADR は読まない)。根拠にするのは status が Superseded / Deprecated でないものに限る
- その path に適用される設計 doc。どの設計 doc が当たるかは、上の「毎回読むもの」で読んだ `principle-design-conformance` の「設計 doc の置き場の引き方」で決める (当たる設計 doc が無ければ読まない)

揃えたら、対象 repo で `git rev-parse HEAD` と `git status --porcelain -- <読んだ対象 repo の file> <規範の置き場>` を引く。規範の置き場は `.claude/rules/` と、`docs/agents/domain.md` が案内する ADR と設計 doc の置き場 — 前回の後に新しく書かれた規範 file も、ここに untracked として出る。同じ会話の次の invoke は、この 2 つの結果で`references/reinvoke.md` の条件を確かめる。

## 3. 3 軸で候補を出す

issue ごとに、3 軸すべてを、検査対象の本文 (書き換え案が添えられていればその文面) と triage コメントの全体に当て、当たったもののうち手順 2 の出典 (file:line) を示せるものを、重要度で絞らずに全件候補にする (示せない指摘は推測)。出した候補を落とす段は手順 4 の反証だけ。

- **① 規範違反の指定**: issue や triage コメントが示す選択肢・置き場・構造の指定のうち、その通りに実装すると手順 2 の規範に反するもの。たとえば、層規則で禁じた向きの参照を生む「双方向にする」という選択肢や、component の責務定義に反する置き場への絞り込み。issue に選択肢が並ぶ場合、1 つでも違反する選択肢があれば指摘する。worker はどれでも選びうるため。
  - **受け入れ条件に入った解き方**: 受け入れ条件が、解けたかの観測ではなく解き方 (書き込む節・文面の逐語) を指定している行。修正案は、その行を観測できる条件に言い直し、解き方を候補として受け入れ条件の外へ移す形にする。規範か設計 doc が解き方を決めていれば、受け入れ条件の外でその path と見出しを引く形にする — 受け入れ条件に残すと、実装とレビューがよりよい解き方を受け入れ条件への違反として退ける
  - **gate**: 手順 2 で開いた gate は走査対象を確かめ、gate が**見ていない範囲** (同じカテゴリ内の相互参照など) を重点的に見る
  - **設計 doc**: 手順 2 で読んだ設計 doc には、`principle-design-conformance` の 2 つの検査 (conformance と design drift) を当てる。issue が足す失敗時の振る舞いにも当てる — 作業項目の 1 行に見えても、実装の後に見つかると人の決定を待つことになる。同 leaf の「2 つの検査と区分」の表が人へ返すとする指定は、修正案を書かずに深掘り対話へ回し、conformance の側を「設計 doc の変更が要る」、design drift の側を「design drift」と呼ぶ。issue の検査では、設計 doc が既定 branch でまだ直っていなければ、issue 自身が設計 doc の書き換えを範囲に含めていても design drift に当てる — 構造を変える issue を `ready-for-agent` にすると、人が設計 doc を決める前に worker が実装を動かす
- **② 本文の要素の未閉鎖**: 解く課題が読み取れないか、受け入れ条件が完了を閉じていないもの。解けたと判断できる観測可能な条件が無い、撤去・置換の上限が無い、「必要に応じて」「適宜」のように worker の自律判断で作業が広がる語がある、などが該当する。worker が作業を広げうる具体的な箇所を引用して指摘する
- **③ 規範同士の衝突**: issue の要求を満たすと、手順 2 で読んだ 2 つの規範のどちらかに必ず反するもの。優先順位で決まる衝突は裁定し、決まらない衝突と AFK 境界の外の衝突は裁定せずに深掘り対話へ回す。
  - **優先順位**: 手順 2 で読んだ `principle-norm-precedence` で裁き、優先順位で決まるかどうかも同 leaf で判定する
  - **AFK 境界の外**: 裁定が、手順 2 で読んだ `principle-ai-delegation-boundary` のうち人間に返す判断の種類を定めた項に当たるとき (負ける規範が設計 doc のときを含む。揃えると設計 doc の変更になり、設計 doc の変更は `principle-design-conformance` が人へ返す) は、優先順位で決まっても裁定しない — ここに当たる衝突を裁定すると、人が見るべき判断が黙って agent に渡る
  - **裁定したとき**: 勝った規範に issue を合わせる修正案と、負けた規範を勝った規範に揃えて直す follow-up issue の文案 (タイトルと本文) を返す。負けた規範を残すと、次の issue で同じ衝突が出る。起票は呼び出し元が行う。本文には手順 5 の雛形の受け入れ条件を必ず埋める — 文案はそのまま起票されうるので、受け入れ条件の無い文案から起票した issue は、次の検査で軸 ② に当たって差し戻される

手順 2 で読んだ規範どうしが指定の論点ですでに食い違っているときは、片方だけに照らせば ① に見えても ③ として出す。① として修正案を書くと、食い違った片方の規範を優先順位の根拠も示さずに勝たせることになる。

## 4. 全候補に反証を当てる

手順 3 で全 issue から出た候補すべてに、1 件につき subagent を 1 体立て、並列に走らせる。`subagent_type: swat-skills:refuter` で指名し、`model` / `effort` を渡さない (深さは定義が固定する。`model` 引数は定義の frontmatter より優先され、固定した値を書き換える)。全件に当てるのは、提案がそのまま issue 本文へ反映されうるため。反証を経ない指摘が混ざると、誤った修正が本文に入るか、承認する人が指摘ごとに確からしさを確かめ直すことになる。

prompt は下の雛形の `- 軸:` から `- 対象 repo:` までの行の `<...>` を入力だけで埋め、`{{issue}}` 以降の行はそのまま残した文面を書く。この手順で作る file (この文面・書き換え案の逐語・`--out`) は、どれも `mktemp` で作業ツリーの外に作る。その file を `${CLAUDE_SKILL_DIR}/scripts/build-refuter-prompt.py <その file> --issue <手順 1 で保存した read の出力> [--rewrite <書き換え案の逐語の file>] --out <prompt の file>` に通す。起動の prompt には `--out` の file の絶対 path 1 行だけを書く。script は出典の欄の `<path>:<line>` に skill ディレクトリの変数が残っていれば絶対 path に展開し、`- 対象 repo:` の後の `{{issue}}` の行を `<issue>` ブロックに組む。script が exit 1 で終わったら、stderr の理由で直せる誤り (雛形の `{{issue}}` の欠け・`--issue` の鍵の欠け) を直して通し直す。直せなければ、その候補の refuter を立てずに manifest の `reply` を null、`reply_error` を stderr の理由にする。

```
- 軸: <① 規範違反の指定 / ② 本文の要素の未閉鎖 / ③ 規範同士の衝突>
- 指摘: <指摘の 1 文>
- issue 側の引用: <本文またはコメントの該当箇所の逐語>
- 規範の出典: <絶対 path:line と該当箇所の逐語。軸 ② では本文の要素の該当行。軸 ③ では衝突する 2 本をそれぞれ。作業ツリーの外 (`git show <ref>:` 等) から読んだ規範は、反証者が開けず、作業ツリーの同じ path は別の版なので、`<ref>:<repo 相対 path>:line` で書き、引用行を含む節の全体を逐語で貼って「判定には貼った本文を使う」と添える>
- 設計 doc の区分 (軸 ① で設計 doc に照らした候補だけ): <修正案で直す / 深掘り対話へ回す (設計 doc の変更が要る / design drift)> と、当てた `principle-design-conformance` の行 (絶対 path:line と該当箇所の逐語)
- 裁定 (軸 ③ だけ): <勝った規範 > 負けた規範 (当てた基準と、それを書いた `principle-norm-precedence` の絶対 path:line と該当箇所の逐語)、または「裁定しない (<優先順位で決まらない / AFK 境界の外 — 当たった条件>)」>
- 対象 repo: <対象 repo の絶対 path>

{{issue}}

<issue> は指摘が出た issue の取得結果。本文の書き換え案 (未反映) があれば、それが本文を丸ごと置き換える。反映後の本文は書き換え案だけなので、issue 本文の引用は書き換え案から引き、旧本文からは引かない。

この軸で具体物として認めるもの。
- 軸 ①: 指定が規範 (設計 doc を含む) に適合していることを示す規範本文の file:line (規範の適用範囲外であることを示す箇所と、設計 doc がその構造を既に記述している箇所を含む)、または issue 本文の別の節がその指定を規範どおりに限定している箇所の引用。設計 doc の区分を付けた候補では、付けた区分の代わりに `principle-design-conformance` の別の区分の行が当たることを示す file:line も、区分への反証として認める
- 軸 ②: 要素を閉じている issue 本文の節の引用
- 軸 ③: 2 つの規範が両立する読み方を示す、いずれかの規範本文の file:line。裁定したときは、裁定に当てた基準 (`principle-norm-precedence` の行) が逆を指すことを示す file:line、または裁定が AFK 境界の外に当たることを示す箇所の引用も認める

返す項目 (軸 ③ と、設計 doc の区分を付けた軸 ① の候補だけ): `refuted` / 具体物 / 反証の種類 / 説明 の順。反証の種類は、`refuted: true` のとき、具体物が示したものを、軸 ③ では「両立する読み方」「裁定の根拠」「AFK 境界」、軸 ① では「適合」「区分」のどれか 1 つで書く。`refuted: false` なら「なし」と書く。
```

全候補の返答がそろったら、返答を候補ごとに逐語で作業ツリーの外の file に保存する (subagent が error を返した候補は保存せず、`reply` を null にする)。入力の issue 番号の全件と全 issue の候補の一覧を作業ツリーの外の manifest に書いて `${CLAUDE_SKILL_DIR}/scripts/sort-refutations.py <manifest>` に通す (候補が 0 件でも通す)。manifest の形と振り分けの規則は `sort-refutations.py --help` (docstring) で確かめる。出力の `disposition` ごとに次へ置く (`swat-skills:refuter` が解決できないときは通さず、次の段落の停止に従う)。

- `落とす`: 反証の具体物とともに `### 落とした候補` — 承認する人が、どの候補をどの具体物で落としたかを確かめられる
- `未反証`: `reason` を理由に、その issue のブロックの「未反証 — 承認材料にしない」の節 — 反証を経ない指摘が反証済みの指摘と並ぶと、承認する人がどれを確かめ直すべきか見分けられない
- `残す`: `### 指摘`。`裁定しない形へ替える` は深掘り対話へ回す形に、`区分を替える` は反証が示した区分の形に替えて `### 指摘`
- `軸 ① へ替える`: その読み方に照らした軸 ① の候補に替え、手順 4 の雛形で軸 ① として refuter を新しく立てて反証を当て直す。返答を新しい file に保存し、manifest のその候補の行を `axis: "1"`・`ruled` と `from_norm_disagreement` は false・`design_division` は設計 doc に照らす候補なら true・新しい返答の path に書き替えて、全候補をもう一度通す — 衝突が消えても、指定がその読み方に反するかの問いは残る

`swat-skills:refuter` が解決できないときは、どの issue にも提案を返さずに停止する。issue ごとに手順 5 の停止の形で、停止理由 (error が挙げた available agents)、その issue の候補一覧 (「未反証 — 承認材料にしない」と見出しに明記する)、次の手順 (swat-skills plugin の agent が読み込まれる状態にしてから再実行する) の 3 つを返す。`general-purpose` で代わりにすると、反証が指名した agent 定義の読み方を通らないまま、通ったように見える。

## 5. 提案を返す

入力の issue ごとに次の形のブロックを並べた markdown を返して終える。深掘り対話への回し先は、対象 repo の `docs/agents/triage-labels.md` の表 (Repo-local labels の表を含む) のうち、1 列目が役割の名前 (`needs-triage`・`needs-grilling` など) の行を label の宣言として読んで決める。宣言の行があれば、`needs-grilling` の行の 2 列目の綴りで書く。その行が無ければ (`needs-grilling` に当たる label が無い repo)、label を提案せず「深掘り対話が要る」と書く。ファイルが無いか開けないか、宣言の行が 1 つも無ければ (label の宣言が無い repo)、名前どおりの綴り `needs-grilling` で書く。1 列目が役割の名前なのに 2 列目の綴りを読み取れない行があれば、label を提案せず「深掘り対話が要る」と書き、「triage-labels.md の宣言を読み取れず」と添える。

```markdown
## ready-for-agent-review: #<N>

反証対象: <候補数> 件 / 落とした件数: <件数> / 未反証: <件数>
issue 置き場: <置き場> (<呼び出し元の指定 / 宣言 / 宣言が指す正本 (<file>) / 宣言なし (既定)>)
読んだ規範: <手順 2 で読んだ file の一覧。該当が無かった項目は「<項目>: 該当なし」と書く>

### 指摘

#### <軸番号> <指摘の 1 文>
- issue 側: <出典 (本文 / コメントの author と日時 / 書き換え案 (未反映))> — 「<逐語引用>」
- 規範: <file:line。作業ツリーの外から読んだ規範は手順 4 の雛形と同じ `<ref>:<repo 相対 path>:line`> — 「<逐語引用>」 (軸 ③ では衝突する 2 本をそれぞれ)
- 修正案: <issue 本文の書き換え案 (書き換える箇所と書き換え後の文面。指摘が書き換え案 (未反映) から出たときは、その書き換え案の差し替え文面)。軸 ③ を裁定したときは勝った規範に合わせる書き換え案 (issue がすでに勝った規範に沿っていれば「なし」)>
- 深掘り (軸 ③ を裁定しないときと、軸 ① で設計 doc に照らして深掘り対話へ回すとき、修正案の行の代わりに): <`needs-grilling` の綴り、または「深掘り対話が要る」> (<優先順位で決まらない / AFK 境界の外 / 設計 doc の変更が要る / design drift — 当たった条件>)
- 裁定 (軸 ③ を裁定したときだけ): <勝った規範の file:line> > <負けた規範の file:line> (<当てた `principle-norm-precedence` の基準>)
- follow-up issue の文案 (軸 ③ を裁定したときだけ): タイトル「<負けた規範をどう直すか>」/ 本文「<裁定とその根拠、負けた規範の file:line、解く課題 (負けた規範が勝った規範と食い違っていること)、受け入れ条件 (負けた規範が勝った規範に揃っていること)、候補の手段 (直し方)。独立した 1 行 `発端: #<この issue の番号>` を `${CLAUDE_SKILL_DIR}/../issue-tracker-operations/references/body-rewrite.md` の「書き換え案に残す行」の置き方で含める>」

### 落とした候補

- <軸番号> <指摘の 1 文> — issue 側: <出典 (本文 / コメントの author と日時 / 書き換え案 (未反映))>「<逐語引用>」/ 規範 <file:line>「<逐語引用>」 (候補が引く行をすべて) / 反証: <具体物の出典 (規範の file:line、または issue 側と同じ区別の出典とその節)>「<逐語引用>」 — <1 句>

### 未反証 — 承認材料にしない

#### <軸番号> <指摘の 1 文>
- issue 側 / 規範: <指摘と同じ形>
- 反証の結果を得られなかった理由: <error の要旨、または返った形>
- 次の手順: 同じ issue でこの skill を invoke し直す (反証は全候補にやり直される)
```

ヘッダの 3 つの件数は `sort-refutations.py` の最後の出力の `counts` を写し、節は `sections` に並んだ見出しだけを置く。指摘が 0 件なら `### 指摘` に「該当なし」と書く。

提案を返さずに止まる issue は、上の形の代わりに次の形で返す。停止は issue ごとで、止まった issue の停止は他の issue のブロックに影響しない。

```markdown
## ready-for-agent-review: #<N>

停止: <停止理由>

### 未反証 — 承認材料にしない

<手順 3 の候補一覧。候補を出す前に止まったなら「候補なし」>

次の手順: <停止を解く手順>
```
