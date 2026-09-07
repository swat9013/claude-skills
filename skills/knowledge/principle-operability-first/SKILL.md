---
name: principle-operability-first
description: 運用性 (可観測性・デプロイ容易性) を設計するとき、監視と observability の投資順序を決めるとき、信頼性の余力をどう使うか決めるときに適用する。作った者が運用する前提で初期設計に運用性を組み込む。
user-invocable: false
---

# 作った者が運用する前提で設計する

- **You Build It, You Run It** (Vogels, ACM Queue 2006)。運用性 (可観測性・デプロイ容易性) を初期設計に組み込む
- **投資順序は Monitoring が先** (known-unknowns の閾値検知)、**Observability が次** (unknown-unknowns の探索的診断)
- **Error budget は挑戦を許容する予算** (Google SRE Book)。予算が残っているなら、それを使う攻めの判断を提案してよい
