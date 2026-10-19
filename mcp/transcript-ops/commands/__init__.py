"""mart を持たない tool の実装 (tool 1 本 = module 1 本)。

- `find_invocations`: 直読み残置 (集計が無く slice に生 record を要するため store は
  索引にしか効かない。ADR 0031 で「揃えること自体を目的にしない」と確定)
- `query`: store への read-only ad-hoc query (観測契約を持たない任意の問い)
- `stage_usage`: 名指しした 1 transcript の直読み (subagent と起動元の対応
  `agent-<id>.meta.json` を store が持たず、名指しした 1 file を読むだけで lake を
  走査しないため。置き場の決定は ADR 0074 の 2026-10-15 追記)

観測契約を持つ tool は `marts/<name>/` (query.sql + udf.py + contract.py +
rules.py + present.py) に置く。
"""
