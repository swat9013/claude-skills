---
name: principle-signature-intent
description: 関数・メソッドのシグネチャと引数を設計するとき、boolean 引数やオプショナル引数を足したくなったときに適用する。呼び出し側に意図を表現させる原則。
user-invocable: false
---

# シグネチャで意図を表現させる

- boolean 引数は禁止。パラメータで振る舞いを分岐させない (production コード対象。test の parametrize 目的の keyword-only boolean は対象外)
- 安易なオプショナルは避ける。呼び出し側に意図を表現させる
