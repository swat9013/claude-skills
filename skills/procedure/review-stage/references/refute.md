# 反証者の prompt

反証者へ渡す prompt の雛形と、欄の埋め方。

`<hunk>` 欄には、SKILL.md 手順 1 で保持した diff から Location を含む hunk を逐語で切り出して埋める (`diff --git` 行と `@@` 行まで含め、file 全体の差分は貼らない)。指摘が複数箇所を指すならその全部を並べる。Location が差分に無いとき (Spec の「未実装」、差分の外を指す指摘) は `該当 hunk なし: <理由>` と 1 行書く — 空欄にすると、差分に無かったのか渡し忘れたのかが反証者から区別できない。切り出しに手間取ったときは保持した出力から切り出し直す (diff を取り直すと、reviewer が読んだ差分と同じものである保証が無くなる)。

`<claim-sources>` 欄は adversarial verification の指摘のときだけ、SKILL.md 手順 2 で攻撃役に渡した `<claim-sources>` の中身を逐語で埋める。他の観点では欄ごと落とす。

`<spec>` 欄は Spec の指摘のときだけ、SKILL.md 手順 1 の「Spec 源」が決めたとおり、取得した issue の body だけを逐語で埋める (Spec reviewer に渡したものと同じ)。他の観点では欄ごと落とす。

```
- 観点: <欠陥探し / adversarial verification / 原則 / Spec>
- Location: <file:line>
- Finding: <指摘の 1 文。adversarial verification では攻撃役の Claim と出どころ>
- Evidence: <指摘が挙げた引用>
- Oracle / Attack: <adversarial verification の指摘だけ。攻撃役が書いた破れの条件と攻撃の経路。他の観点では行ごと落とす>

<hunk>
<LOCATION_HUNKS_VERBATIM>
</hunk>

<spec>
<ISSUE_BODY_FULL_TEXT>
</spec>

<claim-sources>
<CLAIM_SOURCES_VERBATIM>
</claim-sources>

<hunk> は Location を含む差分の該当箇所を、確定済みの diff から切り出したもの (差分に該当箇所が無ければ `該当 hunk なし:` の 1 行)。<spec> は Spec の指摘のときだけ入る、発端の issue の本文。<claim-sources> は adversarial verification の指摘のときだけ入る、攻撃役が攻めた主張の出どころ。Oracle / Attack 行は攻撃役が書いた破れの条件と攻撃の経路で、攻撃の経路を弾く箇所を探す手がかりに使う。

この観点で具体物として認めるもの。
- 欠陥探し: 欠陥の発生を弾いている箇所 (検査・型・caller の前提) の file:line
- adversarial verification: 攻撃の経路を弾いている箇所 (検査・型・caller の前提) の file:line / 出どころを issue・commit message・PR 本文・docstring と名指しした主張について、そこに宣言されていないことを示す <claim-sources> か差分の該当行 (暗黙の主張と既存の契約は、出どころに書かれていなくても攻める主張なので、この種類の具体物では反証しない)
- 原則: file:line / 弾いている caller
- Spec: spec を満たしている実装の file:line
```
