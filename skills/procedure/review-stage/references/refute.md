# 反証者の prompt

反証者へ渡す入力 file の雛形と、欄の埋め方。落とし候補ごとに下の雛形を埋めた file を、SKILL.md 手順 1 の `dir` に `refute-<番号>.md` で書き、起動の prompt にはその file の path 1 行だけを書く。

`<hunk>` 欄には、SKILL.md 手順 1 の `diff` の file から Location を含む hunk を逐語で切り出して埋める (`diff --git` 行と `@@` 行まで含め、file 全体の差分は貼らない)。指摘が複数箇所を指すならその全部を並べる。Location が差分に無いとき (Spec の「未実装」、差分の外を指す指摘) は `該当 hunk なし: <理由>` と 1 行書く — 空欄にすると、差分に無かったのか渡し忘れたのかが反証者から区別できない。切り出しに手間取ったときも同じ file から切り出し直す (diff を取り直すと、reviewer が読んだ差分と同じものである保証が無くなる)。

`<claim-sources>` 欄は adversarial verification の指摘のときだけ、SKILL.md 手順 2 の `claim_sources` の file の path を埋める。他の観点では欄ごと落とす。

`<commit-messages>` 欄は原則の指摘のときだけ、SKILL.md 手順 1 の `commit_messages` の file の path を埋める (原則の reviewer が読んだものと同じ。反証者は git を実行できないので、渡さないと commit 本文に書かれた根拠を読めず、誤検知が Act on に残る)。他の観点では欄ごと落とす。

`<spec>` 欄は Spec の指摘のときだけ、SKILL.md 手順 2 の `spec` の file の path を埋める (Spec reviewer に渡したものと同じ)。他の観点では欄ごと落とす。

`<design-docs>` 欄は設計の指摘のときだけ、SKILL.md 手順 2 で `--design-doc` に渡した行を埋める。他の観点では欄ごと落とす。

```
- 観点: <欠陥探し / adversarial verification / 原則 / Spec / 設計>
- Location: <file:line>
- Finding: <指摘の 1 文。adversarial verification では攻撃役の Claim と出どころ>
- Evidence: <指摘が挙げた引用>
- Oracle / Attack: <adversarial verification の指摘だけ。攻撃役が書いた破れの条件と攻撃の経路。他の観点では行ごと落とす>

<hunk>
<LOCATION_HUNKS_VERBATIM>
</hunk>

<spec>
<SPEC_FILE_PATH>
</spec>

<claim-sources>
<CLAIM_SOURCES_FILE_PATH>
</claim-sources>

<commit-messages>
<COMMIT_MESSAGES_FILE_PATH>
</commit-messages>

<design-docs>
<DESIGN_DOCS>
</design-docs>

<hunk> は Location を含む差分の該当箇所を、確定済みの diff から切り出したもの (差分に該当箇所が無ければ `該当 hunk なし:` の 1 行)。<spec> は Spec の指摘のときだけ入る、発端の issue の本文の file の path。<claim-sources> は adversarial verification の指摘のときだけ入る、攻撃役が攻めた主張の出どころの file の path。<commit-messages> は原則の指摘のときだけ入る、この CL の commit の本文つき log (再レビューの巡では前巡までの commit も含む) の file の path。<design-docs> は設計の指摘のときだけ入る、照らした設計 doc の置き場と索引の path (1 行に `<置き場> | <索引>`。置き場はディレクトリか file で、索引があれば索引から読む file を決め、空なら置き場の全体を読む)。Oracle / Attack 行は攻撃役が書いた破れの条件と攻撃の経路で、攻撃の経路を弾く箇所を探す手がかりに使う。

この観点で具体物として認めるもの。
- 欠陥探し: 欠陥の発生を弾いている箇所 (検査・型・caller の前提) の file:line
- adversarial verification: 攻撃の経路を弾いている箇所 (検査・型・caller の前提) の file:line / 出どころを issue・commit message・PR 本文・docstring と名指しした主張について、そこに宣言されていないことを示す <claim-sources> か差分の該当行 (暗黙の主張と既存の契約は、出どころに書かれていなくても攻める主張なので、この種類の具体物では反証しない)
- 原則: file:line / 弾いている caller / 変更そのものの根拠を問う指摘について、指摘が求める根拠を書いた <commit-messages> の commit と本文の引用 (理由や記録をコード・文書の中に残すことを問う指摘には認めない)
- Spec: spec を満たしている実装の file:line
- 設計: 差分が設計 doc の保証・設計規則・境界に沿っていることを示す設計 doc の file:line / 差分が変えた構造を設計 doc が既に記述していることを示す設計 doc の file:line / Evidence に添えられた、設計 doc に優先する規範に差分が沿っていることを示すその規範の file:line
```
