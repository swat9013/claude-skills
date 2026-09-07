"""dispatch v2 の台帳コア + reconciler daemon + MCP 薄 client。

設計正本: `docs/design/dispatch-v2/` / ADR 0055 (ドメインモデル) / ADR 0056 (reconciler) /
ADR 0057 (永続化)。**v1 (`mcp/dispatch-ops/`) の隣に新設し、v1 は稼働したまま切り替える**
(設計 system.md「10. 移行方針」)。v1 とはコードを共有しない — 併走中の結合を作らないため、流用は copy で行う。
"""
