# 原則 rubric

すべての症状がすべての変更に当てはまるわけではない。判断して使う。

各症状は「判断呼び」であって違反の断定ではない。差分に現れた形だけを根拠にする。正しさの欠陥 (crash / データ破損 / 到達可能性) は本 rubric の対象外。

各症状の末尾は元 leaf の名前。保守用の対応表なので、レビュー中に開く必要はない — 本 rubric がこの review で使う原則の全量。

## 構造

- **同じ責務のロジックが複数の hunk / file に同じ形で現れる** — 集約先があるか。異なる理由で変わる似たコードなら重複のまま残すのが正解。`principle-structure-simplicity`
- **1 つの file / module が無関係な複数の理由で編集されている** (Divergent Change) — 責務ごとに分かれる形か。`principle-structure-simplicity`
- **同じ型に対する switch / if カスケードが複数箇所で繰り返される** — 1 つの写像か多態に寄せられるか。`principle-structure-simplicity`
- **関数内で抽象レベルが混在する** (方針の記述と細部の操作が同じ関数に同居) — SLAP に照らして層を割れるか。`principle-structure-simplicity`
- **委譲するだけの関数 / クラスが足されている** (Middle Man) — 呼び出し側が実物を直接呼べるか。`principle-structure-simplicity`
- **1 つの論理変更が多数の file の散在した編集を強いている** (Shotgun Surgery) — 一緒に変わるものが 1 箇所に集まっていない兆候。`principle-localize-change-impact`
- **呼び出し元が 1 箇所しかない抽象・パラメータ・hook が新設されている** — 早すぎる抽象化と過剰分割はこの原則の違反側。`principle-localize-change-impact`
- **メソッドが自分のデータより他オブジェクトのデータを多く触る** (Feature Envy) — 触っているデータの側へ移せるか。`principle-balance-coupling`
- **`a.b().c().d()` 形の連鎖で他オブジェクトの内部構造に依存している** (Message Chains) — 変更頻度の高い側との結合を強めていないか。`principle-balance-coupling`
- **継承した / 実装した契約の大半を無視・上書きしている** (Refused Bequest) — 継承より合成で表せるか。`principle-balance-coupling`
- **仕様が要求していない拡張点・将来用の分岐が足されている** (Speculative Generality) — 捨てやすさを削っていないか。`principle-deletability`
- **旧経路を残したまま新経路が足されている** (dual path) — 呼び出し元を移して旧経路を同じ波で消せるか。`principle-deletability`
- **不可逆な決定 (永続化形式・外部 API の公開形・依存の追加) が、必要になる前に固定されている** — 制約から導出された結果か、好みからの先取りか。`principle-design-derivation-order`
- **既製品で満たせる処理が自前実装されている** — 既製品では満たせない要件が言語化されているか。`principle-build-vs-buy`

## 名前とシグネチャ

- **名前が中身を言っていない** (Mysterious Name) — 正直な名前が出てこないなら設計が曖昧な兆候。`principle-ubiquitous-naming`
- **記号・略号・番号 (`α` / `S-1` / `tmp2`) が識別子や概念名に使われている** — ドメイン用語へ置けるか。`principle-ubiquitous-naming`
- **ドメイン概念が primitive や string のまま持ち回られている** (Primitive Obsession) — 小さな型を与える余地があるか。`principle-ubiquitous-naming`
- **boolean 引数が足されている / 同じ数個の引数が常に一緒に旅している** (Data Clumps) — 呼び出し側に意図を書かせる形か。`principle-signature-intent`
- **オプショナル引数・既定値が積み増されている** — 分岐が呼び出し側の意図として表れているか。`principle-signature-intent`

## 失敗と運用

- **例外を握りつぶす / 失敗時に黙って既定値へ落ちる経路が足されている** — 仕様化された fail-open / fail-closed か、沈黙の失敗か。`principle-fail-loudly`
- **retry・特殊ケース分岐・条件の追加で症状だけを抑えている** — 根本原因に到達しているか。`principle-fix-root-causes`
- **新しい失敗経路にログ・観測点が無い** — 運用者が起きたことを事後に読めるか。`principle-operability-first`
- **新しい外部入力経路に検査が無い / secret や資格情報がコードに直書きされている** — 検査を前倒しできる箇所か。`principle-risk-based-security`
- **副作用のある操作が無条件に許可されている / 停止時の引き渡し (理由・そこまでの成果・次の手順) が無い** — 委譲の境界が副作用で切れているか。`principle-ai-delegation-boundary`

## 変更の作り方

- **規約の遵守を散文の注意書きに頼っている** — 決定的に判定できる制約なら lint / hook / 型で守らせられる。`principle-automate-when-it-hurts`
- **TODO / FIXME が所在・悪化条件・返済トリガー無しで残されている** — 記録の無い負債は許容範囲の外。`principle-debt-quadrant`
- **1 つの差分に無関係な複数の目的が混ざっている / 長寿命前提の feature toggle が足されている** — 独立に出せる単位へ割れるか。`principle-short-lived-integration`
- **デバッグ用の出力・一時的な迂回・未完了の断片が残っている** — シニアエンジニアが承認する状態か。`principle-review-ready-bar`

## 記述と記録

- **名前やシグネチャで自明な WHAT をコメントが反復している / 非自明な workaround に理由が無い** — 意図の 4 側面のどれをどこへ書くかで判断する。`principle-comment-intent`
- **代替案を退けた設計判断がコード内コメントにだけ残っている** — WHY の置き場は ADR。`principle-collective-ownership`
- **文書が成果物の役割と違う規範で書かれている** (README に作り手の経緯、実行時の指示書に改訂の背景、PR 説明文が会話の経緯前提) — 役割ごとに規範は逆になる。`principle-artifact-register`

## テスト

- **振る舞いの追加・修正にテストが伴っていない / テストが実装を写経しているだけ** — 設計フィードバックとリグレッション防御が得られているか。`principle-tdd-rhythm`
- **テスト名がメソッド名の写経 / 1 テストに複数の振る舞い / Act が複数行** — 仕様として読めるか。`principle-test-as-spec`
- **private 状態・内部メソッドの呼び出し順・中間データ構造を assert している** — 振る舞い不変のリファクタリングで壊れる形。`principle-observable-behavior`
- **自アプリだけが使う依存 (DB 等) を mock している / 実時刻・sleep に依存している** — mock を許す境界は unmanaged dependency だけ。`principle-test-double-boundary`
- **委譲だけの glue code に単体テストが足されている / 網羅が e2e に寄っている** — 投資が統合レベルへ寄っているか。`principle-test-level-allocation`
- **カバレッジ閾値が下げられている / assertion の無いテストで数値だけ動いている** — 床は下げない。`principle-coverage-as-floor`
- **再実行前提の retry や条件付き skip がテストに足されている** — flaky は隔離して修理か削除。`principle-quarantine-flaky`
- **テストが削除されているのに、限界価値ゼロの根拠 (covered 文・mutation・より観測点に近いテストの存在) が示されていない** — 剪定の条件を満たしているか。`principle-test-pruning`
