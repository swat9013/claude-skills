---
name: principle-log-design
description: log・監査記録・実行痕跡を新しく書き出す処理を設計するとき、既存の log に項目を足すか判断するとき、log の書式・置き場・保持を決めるときに適用する。行の自己完結・書式・書き先・正常痕跡・秘匿・保持の判断規則。
---

# log は読む場面から設計する

- **1 行 = 1 事象で自己完結させる**。発生時刻 (timezone 付き)・発生源 (project / 処理の識別子)・事象種別・結果を同じ行に載せる。前後の行や別 file を読まないと意味が取れない行は、障害時にその突き合わせを人がやることになる
- **構造化 (jsonl) を既定にする**。grep / jq で機械的に切れる書式が調査の速度を決める。素の stderr を append する形を選ぶなら、最低限先頭に時刻と識別子を前置する
- **書く先は 1 事象につき 1 箇所**。複数 file に書くなら正本を 1 つ決め、もう片方は正本への参照に留める (二重書きは更新が片側にだけ入って黙って食い違う)
- **正常も痕跡に残す**。無音だと「今も動いているか」「いつから落ちていたか」を log から読めない。残さない設計を選ぶなら「無音 = 正常」を置き場の仕様として明記する
- **認証情報・token・PII を書かない**。log は成果物より広く読まれ長く残る
- **append-only の file は保持を書いた時点で決める**。「いつ切るか」か、切らないなら「誰が読んで捨てるか」のどちらかを決めておく

可観測性への投資順序は `principle-operability-first`、失敗の可視化は `principle-fail-loudly` が持つ。

出典: The Twelve-Factor App XI. Logs / Future Architect「ログ設計ガイドライン」 (<https://future-architect.github.io/arch-guidelines/documents/forLog/log_guidelines.html>) を翻案。
