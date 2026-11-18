---
name: principle-data-structures-before-logic
description: 実装でデータの形・型・状態の持ち方を決めるとき、同じ状態を見る分岐やフラグが増えてきたときに適用する。ロジックより先にデータ構造を決め、増える分岐はデータ構造 (状態機械・判別共用体・表) へ畳む。
---

# ロジックより先にデータ構造を決め、分岐はデータ構造へ畳む (data dominates / make illegal states unrepresentable)

- **ユースケースが決まった後、ロジックを書く前に、使われ方をなぞってデータの形と中核の型を決める** (Pike "Data dominates")。読む・書く・探す経路をユースケースからなぞり、その経路が素直に書ける形を選ぶ — データ構造が合っていればロジックは短く自明になり、合っていなければロジックが形のずれを分岐で埋め続ける。型は最小で始め、実装で得た知見で直す。先に決めるのは作り込むためではなく、ロジックが上に積まれる前なら型の誤りを安く直せるから
- **分岐やフラグが増えたら、データ構造 (状態機械・判別共用体・表) に畳むのを既定にする** (Minsky "make illegal states unrepresentable")。同期が要る複数のフラグは起こり得ない組み合わせまで表せてしまい、その組み合わせを防ぐ検査が使う側の各所に散る。取りうる状態だけを表せる型にすれば検査が要らなくなり、状態を足したときに直す箇所を型が示す。値ごとの振る舞いの違いは、条件分岐を足す代わりに表の行として持つ
- **畳まないのは、形が明らかで、局所的で、増える見込みが無い分岐**。1 つの関数に閉じた 2 択を型へ持ち上げると、読み手が追う部品だけが増える。見る軸は、その分岐と同期して変わる箇所が他にあるかと、状態が今後増えるか

扱わない範囲: モデルを何から導くかは `principle-design-derivation-order` (ユースケース駆動) が持ち、本原則は実装の中で型とロジックのどちらを先に書くかだけを決める。`principle-structure-simplicity` の SRP は変更理由の数を答え、分岐をデータへ移すかは答えない。`principle-validate-at-boundaries` は境界での型への変換だけを持ち、内側の型の形は本原則が決める。呼び出し側が引数で振る舞いを切り替えるフラグは `principle-signature-intent` が持ち、本原則が持つのは値・状態として保持するフラグの形。

出典: pstack の <https://github.com/cursor/plugins/blob/93b916d/pstack/skills/principle-foundational-thinking/SKILL.md> と <https://github.com/cursor/plugins/blob/93b916d/pstack/skills/principle-model-the-domain/SKILL.md> を翻案。
