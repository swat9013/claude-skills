---
name: grill-with-docs
description: 計画・設計を共有理解に至るまで問い詰め、決まった用語を用語集に、戻しにくい決定を ADR に、同じ種類の判断で再び使える決定を規範に、設計を変える決定を設計 doc に書き残す。用語は既知の語を優先して選ぶ。
disable-model-invocation: true
---

# grill-with-docs

Skill tool で `swat-skills:grilling` と `swat-skills:domain-modeling` を呼び、両方の手順で対話を進める。用語の提案と用語集への採否は、domain-modeling の本文と用語集の書式より「用語の見出し語は leading word から選ぶ」節を優先する。

## 設計 doc から始める

最初のラウンドの前に、原則 leaf `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/references/leaves/principle-design-conformance.md` の「設計 doc の置き場の引き方」を Read し、問い詰める対象が触れる file に適用される設計 doc を決める。当たる設計 doc があればそれを Read し、設問を設計 doc の保証・不変条件・境界・状態の変更として立てる。当たる設計 doc が無ければ本節は当たらない — skill や規範の文面から始めると、設計 doc が既に決めた境界と食い違う案を出し、設問が設計の外で回る。

## 原則を当てる

最初のラウンドの前に `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` を Read し、索引の読み方に従って問い詰める対象に当たる leaf を読む。以降のラウンドでも、設問がまだ読んでいない leaf の適用条件に当たれば、その leaf を読んでから推奨を出す — 決定木が広がると当たる leaf も変わるので、最初の Read だけでは後から出た枝に届かない。

推奨を決めた leaf があれば、その leaf の名前と当てた規則を推奨に 1 句で添える。user が推奨の根拠をその場で確かめられ、推奨を覆したときに節「決まった判断を規範へ codify する」がどの規範と食い違う判断かを特定できる。leaf が答えを決める設問も、user に問う設問として出す — leaf は他の project とも共有する一般則で、この project が別の決め方を選ぶことがある。対象 repo の規範が leaf と違う決め方をしていれば、repo の規範で推奨を決め、推奨には leaf の名前と repo の規範の path と見出しを添える — repo の規範はこの project が選んだ決め方で、leaf は他の project とも共有する一般則なので、両方を添えると、一般則と違う決め方で推奨していることを user がその場で確かめられる。

## 層を行き来する

問題への対処か、構造・前提を変える決定を問う設問では、問い詰める対象が iceberg model のどの層にあるかを見て、層を行き来する。層の定義と、どこまで降りるか (直す層の既定) は、原則 leaf `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/references/leaves/principle-root-cause-and-hierarchy-of-controls.md` を Read して従う。

- **降りる**: user の入力が events 層か patterns 層にある (起きたこと・繰り返すこと) なら、繰り返すか・繰り返すと見込めるかを先に問い、leaf の既定が求める層まで降りた設問を立てる — 入力の層で設問を閉じると、出来事ごとの手当てを決めて終わり、繰り返しを生む仕組みと前提が問われないまま残る
- **昇る**: 決定が structures 層か mental models 層を変えるなら、その決定で消えるはずの patterns 層の繰り返しを設問で特定し、効いたかを確かめる観測として決定に添える。添えた観測は節「決まった判断を規範へ codify する」の文案に含め、同節の手順で出して承認を得てから書く。設計 doc の見直し条件に書くときは同節が Read させる見直し条件の規約に従い、その規約で観測できない繰り返しは issue の判定条件に置く (判定条件は解けたかを確かめる条件で、決定の書き直しには当たらない) — 観測を添えないと、変えた構造や前提が効いたかを後から確かめる手がかりが残らない

## 用語の見出し語は leading word から選ぶ

用語を新設・改名するときは、正準の語を提案する前に、原則 leaf `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/references/leaves/principle-intention-revealing-names.md` を Read し、その規則で語を選ぶ。用語集への載せ方と設問での出し方は次に従う。

- 候補は grilling の設問で推奨の語として出す。複数あれば並べ、推奨を 1 つ選ぶ
- 用語集が既に採っている語があれば、一致を測る分野はその分野に揃える — 分野が混ざると、読み手は語ごとに違う分野の意味で読む。用語集の既存の語と衝突する語は当てはまらない語として扱う
- ずれる語・衝突する語・広すぎる語と退けた作語は、`_Avoid_` に理由付きで書く (例: `_Avoid_: 再請求フロー (作語), collection (債権回収の意味に定着), retry (再試行全般に広がり、催促と間隔を呼び出さない)`)
- leaf が求める初出の日本語の注記は、用語集の項では定義文が兼ねる
- 既知の語を選んでも、この project でその意味を絞るなら用語集に項を立てる — 用語集の書式が除く General programming concepts は、project での絞りが無い語だけを指す
- 語を作ったら、作ったことを設問で user に告げる。leaf の言う定義の 1 か所は用語集の項にする

## 決まった判断を規範へ codify する

判断が決まるたびに、その判断を codify できるかを判定する。codify とは、人の決定を、次に同じ種類の判断が来たとき agent が自分で下せる規範 (CLAUDE.md・rules・skill の指示文・ADR など、agent が次の判断で読むもの) へ書き起こすこと — 計画や issue に書いた決定はその 1 件にしか効かず、同じ種類の判断が次も人に回る。本節は backlog-triage skill と、`needs-grilling` の issue に投稿された手順コメントに従って issue を深掘りする別セッションからも Read されるので、問い詰めの外でも通る手順として書く。Read で開いて本文の path に展開されていない変数が残っていれば、その変数を本 file のディレクトリと読み替える。

- 判断を「どんな場面で・どう決める」の 1 本の規則に言い直せ、その件固有の事実に頼らず、その判断を左右する場面が今後の変更でも再び現れ、既存の規範 (より一般的な規範を当てはめれば決まる場合を含む。原則 leaf も含め、索引 `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/SKILL.md` から当たる leaf を読んで照らす。対象 repo の規範が決めていれば、leaf と食い違っても決まっているものとして扱う) がまだ決めていないか決めた内容と食い違うなら codify できる — 規範は読まれるたびにコストを払うので、まれにしか当たらない規則と一般規範の写しは、読む側のコストと食い違いの源だけを積む
- user が agent の推奨や、agent が自律で下して記録した判断 (CL の説明文などに残したもの) を覆したときも、その判断を導いた規範を同じ判定にかけ、規範を直す文案を出す — 規範を直さないと、同じ種類の判断で同じ誤りが繰り返される
- codify できるなら、書き込む先と規範の文案を、その判断を問う設問に推奨と並べて逐語で出す — user が読んでいない文面を規範に書かない。規則は対象 repo の中の指示文 (その領域を既に扱う file があればその file、無ければ新しい file) に書く。対象 repo の外にある規範 (plugin の原則 leaf など) は直さず、対象 repo の規範に project の判断として書く — 外の規範は他の project とも共有されるので、この project の判断で書き換えると他の project の判断まで変わる。ADR にするのは、戻しにくく・文脈なしでは意外で・実際の代替案から選んだ決定だけにする
- 承認された文案は、その規範を引く issue 本文の書き換えより先に反映する — 反映できなかった規範を本文が引くと、実在しない規範を指す参照が残る。問い詰めの中では、その設問で承認された時点で反映する (用語集の更新と同じ)。指示文は `${CLAUDE_SKILL_DIR}/../../steering/write-for-harness/references/caller-contract.md` を Read し、その契約に従って `swat-skills:write-for-harness` を Skill tool で invoke する。ADR は write-for-harness が扱わないので、対象 repo の ADR の規約に従って書く (問い詰めの中なら domain-modeling の ADR の手順)。反映できなかったら、その規範を引く issue 本文の書き換えと label の変更を保留し、止まった操作と理由を user へ伝える (write-for-harness が止まったときに伝えるものは契約に従う)
- 設計 doc の記述 (保証・不変条件・境界・状態をはじめ、設計 doc が持つ構造) を変える決定は、codify できるかによらず設計 doc へ書く — 書かずに閉じると、設計 doc が決定と食い違ったまま残る。書く前に `${CLAUDE_SKILL_DIR}/../sud-design/SKILL.md` の「1. 置き場を決める」のうち設計 doc を束ねるディレクトリの README の規約を読む項目と「4. 既存設計を更新する」、`${CLAUDE_SKILL_DIR}/../sud-design/references/artifacts.md` の節「決定の見直し条件」と却下肢を該当節に織り込む段を Read し、その手順と書き方に従う。文面は決定に根拠・退けた案・見直し条件を添え、書き込む先 (設計 doc の file と節) と並べて、その決定を問う設問に逐語で出し、承認された文面だけをそのまま書く — user が読んでいない文面を書くと、設計 doc が人の決定ではなく agent の要約になる。反映の時点・issue 本文の書き換えより先に反映すること・反映できなかったときの保留は、上の規範の反映と同じにする
- 対象が issue なら、本文には決定を書き直さず、codify した規範・書いた設計 doc、または判断を決めた既存の規範の path と見出しを引く。本文に書き換える箇所が他に無くても、引く 1 行を足す書き換えにする
- 終わりに、反映した規範と書いた設計 doc の path とそれを引く issue を挙げ、それらが既定 branch に届くまでその issue を実装する側からは読めないことを user へ伝える
