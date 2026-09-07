# 手動レビュー観点 (機械判定不能)

## いつこの doc を Read するか

`repo-agent-maturity` の Step 4 でレポートを書くとき、**ユーザーが手動レビュー観点を明示的に求めた場合のみ** Read する。

## 観点

以下は repo 内のファイルだけでは判定できないため 20 checkpoint に含まれない。

- CLAUDE.md が索引 → リンク → 詳細に分離されているか (30-60 行目安)
- rule が肯定形かつ検証可能な述語で書かれているか
- hook が同期 100ms 制約を守っているか / matcher が広すぎないか
- Skill description が trigger として機能しているか (呼ばれない / 呼ばれすぎがないか)
- MCP tool の context コストが 10 以下の目安に収まっているか
- OWASP Agentic Top 10 各項 (認証情報継承・supply chain 検証・memory poisoning 等)
