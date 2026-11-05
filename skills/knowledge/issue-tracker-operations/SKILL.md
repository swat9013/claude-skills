---
name: issue-tracker-operations
user-invocable: false
description: issue tracker への操作 (本文の書き換え・状態の label の付け替え・close / reopen・親子の関係の記録) の手順と、issue 置き場・CL 置き場の解決順の正本。Use when「issue 本文を書き換える」「issue の label を付け替える」「issue を close / reopen する」「子 issue を親に結ぶ」「issue 置き場・CL 置き場を決める」.
---

# issue-tracker-operations

skill の手順が issue tracker に行う操作の手順と、操作を撃つ先 (置き場) の解決順。どの skill から操作するときも、操作ごとに下の file を Read して従う。手順は、外す label・付ける label・止めたときの宛先といった呼び出し元が決める値を受ける形で書いてあり、呼び出し元はその値だけを持つ。

| file | いつ Read するか |
|---|---|
| [placement](references/placement.md) | issue か CL へのコマンドを撃つ前に、置き場を決めるとき・文面に置き場をまたぐ issue の参照を書くか読むとき |
| [body-rewrite](references/body-rewrite.md) | issue の本文を書き換えるとき |
| [state-label](references/state-label.md) | issue の状態の label を付け替えるとき |
| [close-reopen](references/close-reopen.md) | issue を close / reopen するとき |
| [parent-child](references/parent-child.md) | 子 issue を親 issue に結ぶとき・issue を子に分けたとき |
| [filing-criteria-defaults](references/filing-criteria-defaults.md) | 対象 repo の「follow-up issue の起票」節の基準の箇条を、既定の値の見出し語と照らすとき |

issue の起票と自由文のコメントの手続きは持たない。起票は対象 repo の issue の規範に従う。持つのは、節から欠けた基準の箇条の既定の値だけ。
