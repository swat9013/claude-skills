---
name: principle-competing-prototypes
description: 同じ codebase に前例の無い UI の操作やアーキテクチャの判断を下すときに適用する。競合する試作を 2〜3 個作り、並べて比べてから決める。
---

# 前例の無い設計判断は、競合する試作を並べて比べてから決める

- **前例の無い判断は、試作を並べて比べる**: 同じ codebase に前例の無い UI の操作やアーキテクチャの判断は、競合する試作を 2〜3 個作り、並べて比べてから 1 つに決める — 前例が無いと、案の良し悪しを動かす前に見積もる材料が無く、最初に思いついた案が比べられないまま固定される。前例の無い判断のうち、使い心地が論理ではなく感触で決まるものは、とくに動かして比べる。並べた試作は、使う者が目的を果たすまでの道筋をなぞって比べる (`principle-quality-in-use-over-implementer-effort`)
- **前例がある判断は、試作を並べずに手本に従う**: 向きを変える軸は前例の有無で、同じ codebase に手本があれば、並べても比べる費用だけが掛かる
- **後から作り直す費用が小さい判断は、並べずに 1 案で進め、動かしてから直す** — 誤っても覆す費用が、試作を並べる費用より小さい
- **試作は使い捨てにし、本流に採るのは選んだ 1 つだけにする**: 試作は成果物ではなく判断の材料なので、探索目的の使い捨てと明示し、本流に採る時点の扱いは `principle-design-derivation-order` に従う

比べた結果を誰が決めるかは `principle-ai-delegation-boundary` が、構造を何から導くかは `principle-design-derivation-order` が扱う。本原則は、そのどちらも答えない**前例の無い判断で、案をどう比べるか**を決める。

出典: pstack の <https://github.com/cursor/plugins/blob/93b916d/pstack/skills/principle-exhaust-the-design-space/SKILL.md> を翻案。
