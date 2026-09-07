"""記帳の帰属 (`actor`) の語彙。

event / escalation の `actor` 欄は台帳の読み手が「誰が書いたか」を判断する唯一の材料で、
綴りが揺れると orchestrator の記帳と worker の自己申告と機械の観測が混ざる。**server は値を
検証しない** (v1 から踏襲した policy-free の線) ので、綴りの正本を 1 箇所に置いて内部の
書き手はここを参照する。外から来た `actor` はそのまま記帳する — 名乗りは呼び出し側の責任。
"""

# 機械 (reconciler daemon) 自身の記帳。観測から導かれた事実はすべてこれ
RECONCILER = "reconciler"

# orchestrator (LLM) の指示による記帳。MCP tool の既定値
ORCHESTRATOR = "orchestrator"

# worker (LLM) 自身の申告。outcome の自己申告に使う
WORKER = "worker"
