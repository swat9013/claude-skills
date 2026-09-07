---
name: principle-isolate-shared-writes
description: 並行する書き手が同じ書き込み先 (ファイル・キー・状態オブジェクト) へ触れうる設計をするときに適用する。共有をまず解消し、残った共有だけを構造で直列化する。
user-invocable: false
---

# 並行する書き手は書き込み先を分ける

- **まず共有そのものを解消する**: 並行する書き手に「本当に 1 つの可変オブジェクトが要るのか、それぞれ独立した事実を書いているだけか」を問う。後者なら書き手ごとに専有の書き込み先を与え、読み取り側で統合する
- **1 つの状態オブジェクトに書き手ごとのフィールドを足すのは分離ではない**。分離とは書き込み先そのものを分けること
- **共有が本当に不変条件のときだけ、直列化を構造で保証する** (排他印・逐次フェーズ・単一書き手・compare-and-swap)。「順番に書くこと」という取り決めは並行制御にならない (`swat-skills:principle-automate-when-it-hurts` の帰結)
- **「ロックが要る」は設計の見直し合図として扱う**。既定の答えにしない

競合による不整合は再現しにくく、事後の調査コストが跳ね上がる。残った共有を再実行に耐える形へ収束させる側は `swat-skills:principle-idempotent-operations` が扱う。本原則は**書き込み先を分けるかどうか**を決める。

出典: pstack `principle-separate-before-serializing-shared-state` (cursor/plugins@b9ddc83) を本 repo の価値観へ翻案。
