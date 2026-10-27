# 原則 rubric

すべての症状がすべての変更に当てはまるわけではない。判断して使う。

各症状は「判断呼び」であって違反の断定ではない。差分に現れた形だけを根拠にする。正しさの欠陥 (crash / データ破損 / 到達可能性) と、repo の規約を逐語で引用できる違反は本 rubric の対象外 (前者は欠陥探し、後者は規約違反として別に報告する)。

各症状の末尾は元 leaf の名前。保守用の対応表なので、レビュー中に開く必要はない — 本 rubric がこの review で使う原則の全量。

## 構造

- **同じ知識 (同じ理由で変わるロジック) が複数の hunk / file に同じ形で現れる** — 集約先があるか。異なる理由で変わる似たコードの重複を排除するのは、状態・結合・複雑性のどれも増やさないときだけ。`principle-structure-simplicity` / `principle-state-coupling-complexity-code`
- **1 つの file / module が無関係な複数の理由で編集されている** (Divergent Change) — 責務ごとに分かれる形か。`principle-structure-simplicity`
- **同じ型に対する switch / if カスケードが複数箇所で繰り返される** — 1 つの写像か多態に寄せられるか。`principle-structure-simplicity`
- **関数内で抽象レベルが混在する** (方針の記述と細部の操作が同じ関数に同居) — SLAP に照らして層を割れるか。`principle-structure-simplicity`
- **委譲するだけの関数 / クラスが足されている** (Middle Man) — 呼び出し側が実物を直接呼べるか。`principle-structure-simplicity`
- **1 つの論理変更が多数の file の散在した編集を強いている** (Shotgun Surgery) — 一緒に変わるものが 1 箇所に集まっていない兆候。`principle-state-coupling-complexity-code`
- **呼び出し元が 1 箇所しかない抽象・パラメータ・hook が新設されている** — 早すぎる抽象化と過剰分割はこの原則の違反側。`principle-state-coupling-complexity-code`
- **可変状態 (field・フラグ・キャッシュ・インメモリの queue) が足されている** — 差分が小さくても状態が増える形。同じ振る舞いをステートレスに書けるか、足した状態を読む経路ごとに型を分けるか専用のコンポーネントへ任せて、手元の状態を減らせるか。`principle-state-coupling-complexity-code`
- **メソッドが自分のデータより他オブジェクトのデータを多く触る** (Feature Envy) — 触っているデータの側へ移せるか。`principle-balance-coupling`
- **`a.b().c().d()` 形の連鎖で他オブジェクトの内部構造に依存している** (Message Chains) — 変更頻度の高い側との結合を強めていないか。`principle-balance-coupling`
- **継承した / 実装した契約の大半を無視・上書きしている** (Refused Bequest) — 継承より合成で表せるか。`principle-balance-coupling`
- **仕様が要求していない拡張点・将来用の分岐が足されている** (Speculative Generality) — 捨てやすさを削っていないか。`principle-deletability`
- **旧経路を残したまま新経路が足されている** (dual path) — 呼び出し元を移して旧経路を同じ波で消せるか。`principle-deletability`
- **不可逆な決定 (永続化形式・外部 API の公開形・依存の追加) が、必要になる前に固定されている** — 制約から導出された結果か、好みからの先取りか。`principle-design-derivation-order`
- **1 箇所で条件の組み合わせを 1 件ずつ並べて仕様にしている (分岐・表の行・箇条。同じ判別子の分岐が複数箇所で繰り返される形は上の switch / if の症状)** — 条件そのものを減らせないかを先に見たうえで、並びを生む直交する規則を仕様にしているか。取りうる場合が閉じていて増えないなら羅列のままでよい。テスト・受け入れ条件でケースを並べるのは対象外。`principle-orthogonal-rules`
- **依存を足せば既製品で満たせる処理が、数行では書けないのに自前実装されている** — 既製品では満たせない要件が言語化されているか。`principle-build-vs-buy`
- **差分の外に既にある処理 (同じ理由で変わるもの)、または標準ライブラリ・プラットフォーム・導入済みの依存の機能と同じ処理が新しく書かれている** — 既存のものを呼べるか。似ているだけで異なる理由で変わるなら、別に書くのが正解。`principle-least-code`

## 名前とシグネチャ

- **名前が中身を言っていない** (Mysterious Name) — 正直な名前が出てこないなら設計が曖昧な兆候。`principle-intention-revealing-names`
- **記号・略号・番号 (`α` / `S-1` / `tmp2`) が識別子や概念名に使われている** — 分野で定着した記法でないなら、意図を言い当てる語へ置けるか。略号なら省略前の名前が先に定義されているか。`principle-intention-revealing-names`
- **定着した語 (leading word) がある概念に、新しく作った語を当てている** — 既存の語で呼べるか。`principle-intention-revealing-names`
- **ドメイン概念が primitive や string のまま持ち回られている** (Primitive Obsession) — 名前を持つ小さな型を与える余地があるか。値を扱う箇所が 1 つなら primitive のままでよい。`principle-intention-revealing-names`
- **振る舞いを切り替える boolean 引数 (flag argument) が足されている** — 呼び出し側に意図を書かせる形か。呼び出し側が実行時の値を受け渡すだけのとき (設定値・test の parametrize) と、値そのものとして渡す boolean (`set_enabled(True)`) は対象外。`principle-signature-intent`
- **同じ数個の引数の組が複数の関数のシグネチャに繰り返し現れる** (Data Clumps) — 1 つの型にまとめられるか。組を受け取る関数が 1 つなら、引数のまま残すのが正解。`principle-signature-intent`
- **省略時の既定が大半の呼び出しで正しいとは言えないオプショナル引数が足されている** — 呼び出しごとに選ぶ値を必須にして、意図を呼び出し側に書かせる形か。`principle-signature-intent`

## 失敗と運用

- **例外を握りつぶす / 失敗時に黙って既定値へ落ちる経路が足されている** — 仕様化された fail-open / fail-closed か、沈黙の失敗か。`principle-fail-loudly`
- **retry・特殊ケース分岐・条件の追加で、観測された失敗だけを消している** — 根本原因に到達しているか。根本原因を残すと差分のコメントか変更の説明文が述べている回避策は、記録の有無を問わず、この行ではなく「変更の作り方」の負債の行で判定する。`principle-root-cause-and-hierarchy-of-controls`
- **新しい失敗経路にログ・観測点が無い** — 運用者が起きたことを事後に読めるか。`principle-operability-first`
- **新しく足した log の出力呼び出し (format 文字列・構造化 log の field) に発生時刻・発生源の識別子が無い / 1 つの事象を複数の出力呼び出しに分けて書いている** — 観測点の有無ではなく行の中身として、1 行が 1 事象を単独で語れるか。`principle-log-design`
- **認証情報・token・PII が log へ書き出されている / 同じ事象が複数 file に二重に書かれている** — 行に載せてよい中身か、書く先が 1 事象につき 1 箇所か。`principle-log-design`
- **新しい外部入力経路に検査が無い / secret や資格情報がコードに直書きされている** — 検査を前倒しできる箇所か。`principle-risk-based-security`
- **副作用のある操作が無条件に許可されている** — 許可と禁止が副作用の有無で切れていて、権限範囲と隔離で効いているか。`principle-blast-radius`
- **簡単な抜け道のある操作単位の禁止が足されている** — 防御に数えられない禁止を積み増していないか。守る必要があるなら権限範囲か隔離で守る形か。`principle-blast-radius`
- **停止時の引き渡し (理由・そこまでの成果・次の手順) が無い** — 人間に返すときの形式が決まっているか。`principle-ai-delegation-boundary`
- **既存の規範で決まる判断を人間に回している / 自律で下した判断と理由を残していない** — 人間に返す判断が種類で絞れていて、自律の判断が事後に覆せるか。`principle-ai-delegation-boundary`

## 変更の作り方

- **規約の遵守を散文の注意書きに頼っている** — 決定的に判定できる制約なら lint / hook / 型で守らせられる。`principle-when-to-automate`
- **TODO / FIXME、または根本原因を残した回避策が、所在・悪化条件・返済トリガー無しで残されている** — 記録の無い負債は許容範囲の外。`principle-debt-quadrant`
- **1 つの差分に無関係な複数の目的が混ざっている / 振る舞いを変えない変更と変える変更が同じ commit に混ざっている** — 自己完結した 1 目的の単位へ割れるか。`principle-self-contained-change`
- **長寿命前提の feature toggle が足されている** — toggle を短命に保てているか (撤去の時期・条件が決まっているか)。`principle-short-lived-integration`
- **デバッグ用の出力・一時的な迂回・未完了の断片が残っている** — シニアエンジニアが承認する状態か。`principle-review-ready-bar`

## 記述と記録

- **名前やシグネチャで自明な WHAT をコメント / docstring が反復している / WHAT とも WHY とも言い切れないコメントが触った箇所に残っている / 非自明な workaround に理由が無い** — 名前で読める WHAT のコメントは消し、非自明な workaround には理由を残しているか。WHAT とも WHY とも言い切れないコメントも、消して誤読が起きないなら消しているか。生成ドキュメントや IDE の表示で docstring だけが読まれる公開 API の WHAT docstring は対象外。`principle-intent-placement`
- **代替案を退けた設計判断がコード内コメントにだけ残っている** — 設計判断の WHY の置き場は ADR。`principle-intent-placement`
- **文書が成果物の役割と違う規範で書かれている** (README に作り手の経緯、実行時の指示書に改訂の背景、PR 説明文が会話の経緯前提) — 役割ごとに規範は逆になる。`principle-artifact-register`

## テスト

- **振る舞いの追加・修正にテストが伴っていない** — 薄すぎないか。書かない対象 (vendor したもの・宣言だけの設定・推論で効く文 (skill 指示文・agent 定義・hook 注入文・MCP tool の名前と説明文)・標準コマンドだけで完結し壊れれば目に見える単純な script) は除く。開発者しか使わない script は単純でも書く対象。`principle-test-thickness-by-property`
- **テスト名がメソッド名の写経 / 1 テストに複数の振る舞い / Act が複数行** — 仕様として読めるか。`principle-test-as-spec`
- **private 状態・内部メソッドの呼び出し順・中間データ構造を assert している** — 振る舞い不変のリファクタリングで壊れる形。`principle-observable-behavior`
- **期待値を実装と同じロジックで導いて実装を写経している** — 実装の誤りも期待値に写り、リグレッション防御が効かない形。`principle-observable-behavior`
- **自アプリだけが使う依存 (DB 等) を mock している / 実時刻・sleep に依存している** — mock を許す境界は unmanaged dependency だけ。`principle-test-double-boundary`
- **委譲だけの glue code に単体テストが足されている / 網羅が e2e に寄っている** — 投資が統合レベルへ寄っているか。`principle-test-level-allocation`
- **カバレッジ閾値が下げられている / assertion の無いテストで数値だけ動いている** — 床は下げない。`principle-coverage-as-floor`
- **再実行前提の retry や条件付き skip がテストに足されている** — flaky は隔離して修理か削除。`principle-quarantine-flaky`
- **標準以上の厚さで守る対象のテストが削除されているのに、限界価値ゼロの根拠 (covered 文・mutation・より観測点に近いテストの存在) が示されていない** — 剪定の条件を満たしているか。`principle-test-pruning`
- **推論で効く文の言い回しを assert するテストが足されている / vendor したもの・宣言だけの設定・標準コマンドだけで完結し壊れれば目に見える単純な script (開発者しか使わない script は除く) にテストが足されている / 開発者しか使わず欠陥が次の commit / push で表に出る対象のテストに境界値・書式揺れの網羅が足されている (取り消せない被害を防ぐ不変条件のテストは除く)** — 厚すぎないか。`principle-test-thickness-by-property`
- **利用者の環境で黙って壊れて取り消せない対象 (破壊的操作を止める検査の素通し・秘匿情報の出力) の変更に、不変条件と境界値のテストが伴っていない** — 薄すぎないか。`principle-test-thickness-by-property`
