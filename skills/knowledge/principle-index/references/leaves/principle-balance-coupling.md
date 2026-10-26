---
name: principle-balance-coupling
description: モジュール / サービス境界の結合を評価するとき、境界を分割するか統合するか判断するときに適用する。integration strength / distance / volatility の 3 次元。
---

# 結合は排除ではなくバランスさせる

- integration strength (共有する知識の量)・distance (同じ module か別 service かといった距離)・volatility (変更頻度) の 3 次元で評価する (Khononov『Balancing Coupling in Software Design』)
- strength と distance を逆向きにそろえるのを既定にする: 多くの知識を共有するものは近くに置き (統合)、共有の少ないものは離してよい (分割)。強い結合を遠くに置くと 1 つの変更が距離を越えて波及し、弱い結合を近くに置くと無関係な変更が同じ場所に集まる
- volatility がこの向きの強さを変える: 変更頻度の低い側との結合なら、strength と distance がそろっていなくても許す — 変わらないものへの結合は波及を起こさない
