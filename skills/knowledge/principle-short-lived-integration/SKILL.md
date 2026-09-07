---
name: principle-short-lived-integration
description: branch の寿命・リリース単位・展開方法を決めるとき、作業の土台をいつ最新化するか決めるときに適用する。差分も土台も短命に保つ側へ倒す。
user-invocable: false
---

# 差分も土台も短命に保つ

- **短命ブランチ (1-2 日) で小さく統合する** (scaled trunk-based development — trunkbaseddevelopment.com)。長命 feature branch を作らない (= 作った差分をどれだけ短命に保つか)
- **土台は常に主幹の最新にする**: 着手時に最新を取り込み、安定版を狙って取り込む時期をずらさない。最新で問題が出たらその場で直す (見送って古い土台に留まらない)。fetch / pull のコストは払う前提で組む (= 土台をどれだけ新しく保つか)
- **Ship small, ship often**。リリース単位は最小の独立した変更
- **展開は段階的 (canary release / feature toggle) が理想**。低リスクシステム (社内ツール等) は continuous deployment でよい。feature toggle は短命に保つ (Hodgson — long-lived toggle は複雑化リスク)
