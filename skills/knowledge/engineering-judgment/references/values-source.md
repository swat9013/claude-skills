# エンジニアリング価値観 — 正本

SKILL.md の決定規則の正本 (canonical source)。各項目は「**価値** (規範文。ここから判断規則を復元できる) / **採用概念** (裏取り済みの一般用語と出典) / **検討メモ** (語彙の解釈違い・矛盾の精査と解消の履歴)」で構成する。

- 由来: `~/.dotfiles/.ai/research/2026-06-20-developer-values-questionnaire.md` (2026-06-20 回答、Q1-Q33)。原文の語彙は canonical 用語との突き合わせで精査済み (2026-07-04、解消内容は各検討メモ)
- Qn を持たない項目は questionnaire 以降の実際の判断から抽出し、人間が採否を判定して追加したもの。判断の再現に必要な文脈は **背景** として本書に書き切る (session ID 等の外部参照は残さない — transcript は消えるため本書が単独で完結しなくなる)
- 更新規約: 価値観が進化したら (Q31) まず本書を更新し、SKILL.md へ蒸留し直す。SKILL.md と本書が食い違ったら本書が勝つ
- 出典 URL は 2026-07-04 に web-research で canonical 確認済みのもののみ。未確認の書籍は書誌情報のみ

## 1. アーキテクチャ設計

### 設計の起点 (Q1)

**価値**: 設計は制約 (ビジネス・チーム規模・非機能要件) → アーキテクチャ特性の選抜 → 構造の順で導出する。構造を仮決めした後も、そこで見つかった観点で制約を問い直すループを回し続ける。目的関数は 3 つ: 最悪 (回復不能な構造) の回避 / 決定を可能な限り遅らせる / 少人数で効率よく運用できる。

**採用概念**:
- architecture characteristics — システムが満たすべき品質特性 (-ilities) のうち、そのシステムで影響度の高いものだけを選抜する。Richards & Ford『Fundamentals of Software Architecture』(O'Reilly, 2020。邦訳『ソフトウェアアーキテクチャの基礎』) — https://www.oreilly.com/library/view/fundamentals-of-software/9781492043447/
- last responsible moment — 不可逆な決定を、決定しないコストが決定するコストを上回る直前まで遅らせる。Mary & Tom Poppendieck『Lean Software Development』(2003) — http://poppendieck.com/

**背景**: 思想の原典として挙げたのは『ソフトウェアアーキテクチャの基礎』『Clean Architecture』(Robert C. Martin, 2017)、DDD (Eric Evans『Domain-Driven Design』, 2003)。

### 設計の進め方 (Q2)

**価値**: ユースケース洗い出し → ドメインモデル抽出 → 最小構造のアーキテクチャで実装、の順で進める。実装で得た知見はユースケース・ドメインモデル・アーキテクチャの全層に還流する。ユースケースは受け入れ条件でもあり、自動テストのコードとして表現する。進め方の選択自体は文脈依存 (プロジェクトのリスク・規模・不確実性) が前提。

**採用概念**: ユースケース駆動設計 — Robert C. Martin "Screaming Architecture" (2011) https://blog.cleancoder.com/uncle-bob/2011/09/30/Screaming-Architecture.html / 系譜の源流は Ivar Jacobson『Object-Oriented Software Engineering: A Use Case Driven Approach』(1992、書籍)。

### 原則の優先順位 (Q3)

**価値**: 最優先は**変更影響の局所化** — ある箇所の変更が他へ波及しないこと。次いで機能単位で捨てられること (deletability)。AI により機能実装のコストが下がったため、「作りやすさ・拡張しやすさ」より「波及のなさ・捨てやすさ」が効く。KISS / YAGNI / DRY / SOLID / 明示性と競合したら、変更影響の局所化を守る側を選ぶ。

**採用概念**:
- coupling / cohesion — W. Stevens, G. Myers, L. Constantine "Structured Design" (IBM Systems Journal, 1974) https://dl.acm.org/doi/10.1147/sj.132.0115
- Balanced Coupling — 結合は排除ではなく integration strength (知識共有度) / distance (距離) / volatility (変更頻度) の 3 次元でバランスさせる。Vladik Khononov『Balancing Coupling in Software Design』 — https://www.oreilly.com/library/view/balancing-coupling-in/9780137353514/
- deletability — 「削除しやすさ」に最適化する設計方針。tef "Write code that is easy to delete, not easy to extend" — https://programmingisterrible.com/post/139222674273/write-code-that-is-easy-to-delete-not-easy-to
- YAGNI — Martin Fowler "Yagni" — https://martinfowler.com/bliki/Yagni.html

**検討メモ (2026-07-04 解消)**:
- 原文「何よりも疎結合・高凝集」は、同じ回答で参照した Balanced Coupling と文字通りには矛盾する (Khononov は最小化ではなくバランスを主張)。精査の結果、価値の本体は**変更波及の抑制**であり結合最小化そのものではないと確定。常に最小結合へ倒すと過剰分割 (分散モノリス・早すぎる抽象化) を生み、それはこの価値の違反である。
- 原文「Disposability」は 12-Factor App の同名概念 (プロセスの高速起動・graceful shutdown — https://12factor.net/disposability) と衝突するため **deletability** を正式採用。
- 原典は書籍。補助教材として github.com/vladikk/modularity 配下の skill が公開されており、本 repo は Claude Code plugin (`modularity@vladikk-modularity`) 経由で参照する (ADR 0021 以前は `apm.yml` 経由で `skills/third/{balanced-coupling,design}` として vendor していた)。概念定義が食い違ったら書籍を正典とする。

### 自前実装 vs 既製品 (2026-07-28 追加)

**価値**: 自前機構と既製品 (サードパーティ) が拮抗したら既製品を選ぶ。自前は作り込むほど柔軟性を失い、直感的な操作性も落ちる。自前を選ぶなら「既製品では満たせない要件」を先に言語化する。

**採用概念**: build vs buy — 差別化に寄与しない領域は既製品へ寄せる判断軸。「巨人の肩に乗る」(standing on the shoulders of giants) はこの判断を指す言い回し。

**背景**: issue 駆動ループを自作の orchestrator (Todoist + SQLite + Python の独自ループ機構) として作り込んだが、機能を足すほど柔軟性を失い、直感的に理解できず動かしづらい状態になった。GitHub issue + サードパーティ skill (wayfinder) へ移行したところ、依存管理・PR 連携といった必要機能は既製品側で揃っており、結果としてシンプル・安定・高品質になった。この撤退経験からの一般化であり、「自作しない」ではなく「自作を選ぶなら既製品で満たせない要件を先に言語化する」が要点。

### 撤退・移行の作法 (2026-07-28 追加)

**価値**: 撤退・移行を決めたら削除して終わりにしない。(a) 戻れるように歴史・記録を残し、(b) 再検討する条件 (どうなったら戻すか) を明記する。deletability (捨てやすい構造) の対として「捨てるときに何を残すか」を定める。

**背景**: 2 件の撤退で同じ形が現れた。

- **orchestrator / inbox-triage / Todoist 連携の廃止**: GitHub issue 中心へ寄せる判断に伴い機能ごと削除したが、「過去の取り組みとして記録は残し、必要になったら引き出せる」状態を条件とした
- **tmux ラッパー `tm` の削除**: herdr (tmux 相当 + AI エージェントのビューパネル) が利用環境では上位互換と判断して移行したが、戻れるように歴史を残した。再検討トリガーとして「herdr に致命的な不具合が見つかる」「tmux へ全機能を移植可能になる」「より軽量な選択肢が現れる」の 3 条件を明記している

いずれも「消して終わり」にすると、判断が誤りだったときの復帰コストが青天井になる点が共通の理由。

### 技術選定の基準 (2026-07-28 追加)

**価値**: 言語・ミドルウェアの選定は機能の優位ではなく (a) テストが書ける (b) ユーザーが読める (c) 環境構築と実装が簡単、で選ぶ。

**背景**: orchestrator の実装言語と排他制御方式を決める場面での判断。言語は Python に統一 — 理由は性能や表現力ではなく「テストが書けて、自分が読める」こと。並行取得の競合回避はファイルによる排他制御を第一候補とし、それで無理なら SQLite、という順で「環境構築と実装が簡単な方」を優先した。機能比較で優位な選択肢があっても、保守できなければ意味がないという立場。

**検討メモ**: 原文は「私が読める言語」。属人的な基準のままでは LLM が適用できないため、「ユーザーが読める言語を優先し、不明なら確認する」へ読み替えた (2026-07-28、人間承認済み)。

## 2. 品質とトレードオフ

### 品質 vs 速度 (Q4)

**価値**: フェーズ依存で切り替える。探索フェーズは速度優先、安定フェーズは品質優先。フェーズが不明のまま品質判断をしない。

**採用概念** (選択肢の背景): Quality is Free — Philip Crosby『Quality is Free』(1979、書籍) / Good Enough Software — Ed Yourdon "When Good Enough Software Is Best" (IEEE Software, 1995)。どちらか一方に固定せず、フェーズで使い分けるのが本回答。

### 技術的負債の許容 (Q5)

**価値**: 負債は「既知でコントロール下」なら許容する。どこに負債があり、どういう状況で悪化するかを把握している状態は良い。負債を作っていると分かっていながら把握・管理せず放置するのは許容しない。負債に気づけないのはスキル不足の表れであり、規範では防げない (そのスキルではその品質しか作れない)。

**採用概念**: Technical Debt Quadrant — deliberate/inadvertent × prudent/reckless の 4 象限。Martin Fowler (2009) — https://martinfowler.com/bliki/TechnicalDebtQuadrant.html。許容できるのは deliberate & prudent 象限。inadvertent は規範ではなくスキル向上の問題として扱う。

### 返済 (Q6)

**価値**: Boy Scout Rule (触れたコードは checkout 時より綺麗に checkin) が基本。大きな構造変更はビジネス価値と照らし合わせて計画的に返済する。

**採用概念**: Boy Scout Rule — Robert C. Martin『Clean Code』(2008、書籍)。

## 3. テスト

### 位置づけとタイミング (Q7-Q8)

**価値**: テストの目的は変更に強くすること — システム挙動の「既知の既知」を明確にし、確かなものにする。開発は t-wada 流 TDD (Red-Green-Refactor) で進め、テストを設計フィードバックとリグレッション防御の両方に使う。テストは目的ではなく手段。

**採用概念**: TDD — Kent Beck『Test-Driven Development: By Example』(2002) https://www.oreilly.com/library/view/test-driven-development/0321146530/ / 和田卓人 (t-wada) の解説 — https://t-wada.hatenablog.jp/ (ブログ) / https://www.jasst.jp/symposium/jasst14hokkaido/pdf/S1.pdf (JASST 講演資料)。

**検討メモ (2026-07-04 解消)**: Q4「探索フェーズは速度優先」との適用範囲を精査。**スパイク (探索目的の使い捨てコード) は TDD 免除** — ただしスパイクであることを明示し、本流に採用する時点で TDD で書き直す。

### 投資配分 (Q9)

**価値**: Testing Trophy — 統合テストに最も投資する。ソフトウェアの実際の使われ方に似たテストほど高い信頼を与える。

**採用概念**: Testing Trophy — Kent C. Dodds "The Testing Trophy and Testing Classifications" — https://kentcdodds.com/blog/the-testing-trophy-and-testing-classifications。標語 "Write tests. Not too many. Mostly integration." は Guillermo Rauch (2016-12) — https://x.com/rauchg/status/807626710350839808

**検討メモ (2026-07-04 解消)**: Q8 の TDD との見かけの緊張 (TDD = 単体中心の印象 vs Trophy = 統合重視) は、**TDD は開発リズム (任意のテストレベルで Red-Green-Refactor を回せる)、Trophy は投資配分**と整理して両立。

### カバレッジ (Q10)

**価値**: カバレッジ数値は**回帰下限 (floor)** — 下げないための基準であって、最大化目標ではない。重要経路 (決済・認可・データ削除など) は床を高く設定する。

**検討メモ (2026-07-04 解消)**: 原文「数値 KPI」を到達目標と解釈すると、Trophy の "Not too many" と矛盾し単体テストの水増しを誘発する。精査の結果、意図は**回帰検知の床**であると確定。

## 4. 開発プロセス

### ブランチ戦略 (Q11)

**価値**: 短命ブランチ (1-2 日) で小さく主幹へ統合する。長命 feature branch を作らない。

**採用概念**: short-lived feature branches (scaled trunk-based development) — https://trunkbaseddevelopment.com/short-lived-feature-branches/

### リリース戦略 (Q12)

**価値**: 段階的展開 (canary release / feature toggle) が理想。ただしシステム規模とリスク影響度で調整し、社内ツールのような低リスクシステムは continuous deployment でよい。

**採用概念**: Continuous Delivery / Continuous Deployment の区別 — Martin Fowler "ContinuousDelivery" — https://martinfowler.com/bliki/ContinuousDelivery.html / Feature Toggles — Pete Hodgson — https://martinfowler.com/articles/feature-toggles.html

### リリース単位 (Q13)

**価値**: Ship small, ship often — 最小の独立した変更を高頻度でリリースする。

## 5. コラボレーション

### コードオーナーシップ (Q14)

**価値**: Collective Code Ownership — 全員が全コードに責任を持ち、誰でも変更できる。特定個人しか触れないコードを作らない。

**採用概念**: Kent Beck『Extreme Programming Explained: Embrace Change』(2000、書籍)。

### コードレビュー (Q15)

**価値**: 主目的は設計改善と知識共有。代替案の議論を通じて、メンバー間のスキルとドメイン知識を上げていく。

**検討メモ (2026-07-04 解消)**: 蒸留時に追加された「品質ゲートとして不合格判定するのではなく」という否定は原回答に存在しないため削除。レビューの合否機能を否定する意図はない (主目的の宣言のみ)。

### 協働スタイル (Q16)

**価値**: 複雑な実装は Solo — 個人で深く集中し、割り込みを最小化する。

### ドキュメンテーション (Q17)

**価値**: コードで WHAT、ADR で WHY。設計判断には rationale と棄却案を記録する。

**採用概念**: ADR — Michael Nygard "Documenting Architecture Decisions" (2011) — https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions

## 6. 運用

### 開発と運用の責任 (Q18)

**価値**: 開発チームが本番運用まですべてに責任を持つ。運用性 (可観測性・デプロイ容易性) を初期設計に組み込む。

**採用概念**: You Build It, You Run It — Werner Vogels "A Conversation with Werner Vogels" (ACM Queue, 2006) — https://queue.acm.org/detail.cfm?id=1142065

**検討メモ (2026-07-04 解消)**: 原文の語彙「DevOps」は文化・実践の総称であり、所有権モデルの回答としては広すぎる。この回答の canonical な概念は **You Build It, You Run It** と確定し、DevOps は背景文化として扱う。

### 可観測性 (Q19)

**価値**: 投資順序は Monitoring (既知の問題 = known-unknowns の閾値検知) が先、Observability (未知の問題 = unknown-unknowns の探索的診断) が次点。

### Error Budget (Q20)

**価値**: Error budget は挑戦 (innovation) を許容する予算 — 機会として捉える。予算が残っているなら攻める判断に使える。

**採用概念**: Google SRE Book "Embracing Risk" — https://sre.google/sre-book/embracing-risk/

## 7. セキュリティ

### 統合時点 (Q21)

**価値**: Shift left — セキュリティ検査を開発ライフサイクルの早期工程へ前倒しする (SAST / code review / pre-commit 段階での検出)。早期発見ほど修正コストが低い。

**検討メモ**: shift left の原義は QA/testing 文脈 (Larry Smith, 2001)。security への適用は後年の転用だが現在は確立した用法であり、そのまま採用する。

### 速度とのトレードオフ (Q22)

**価値**: リスクベース — 資産価値と脅威モデルに基づいて投資を配分する。一律の最大防御にしない。

## 8. 自動化・ツーリング

### 自動化の適用範囲 (Q23)

**価値**: Automate When It Hurts — 痛み (反復コスト) を実測してから自動化する。費用対効果で判断し、自動化を道徳的理想としない。

### 遵守の強制手段 (2026-07-28 追加)

**価値**: 規約の遵守は人の注意力ではなく機械 (lint / hook / 型・構造) で保証する。遵守が難しい表現形式 (手書きの TAB 区切り等) は最初から選ばない。Q23 が「いつ自動化するか」を扱うのに対し、本項は「守らせ方をどう選ぶか」を扱う (競合しない)。

**背景**: 4 つの独立した場面で同じ選択をしている。

- **CLAUDE.md の制約**: 行数・フォーマットなど決定的に判定できる制約は、本文に「〜行を超えない」と書くのではなく linter 化して hook に載せる
- **モジュール間の依存方向**: 設計ドキュメントの記述ではなく import-linter で検査する
- **公開 manifest の形式**: 手書きの TAB 区切り text は「遵守が難しい」として却下し、構造化フォーマットへ切り替えた。形式の選択そのものが遵守コストを決めるという判断
- **git identity**: 「間違えないよう気をつける」ではなく `includeIf` + `git config --local` でパス単位に強制する

共通するのは「守れと書いても確率的にしか守られない」という認識。守らせたい制約があるなら、書く前に機械化できないかを問う。

### 推論の決定化 (2026-07-28 追加)

**価値**: LLM の推論に任せている処理のうち決定的にできるものは script へ逐い出す — テスト可能になり、トークン消費も減る。実現手段は非標準の拡張より標準機能内での実現を優先する。Q23 の「痛みの実測」は満たしている前提 (トークン消費・反復失敗が観測されてからの判断) であり競合しない。

**背景**: 4 つの独立した場面で同じ選択をしている。

- **orchestrator ループのコスト削減**: ループの無駄とトークン消費を抑えるため、推論に任せていた箇所のうち決定的に書ける部分を特定して script へ移した
- **issue dispatch の GitHub / GitLab 操作**: 「多少煩雑だが決定的で、テストも書ける」ことを理由に script 化を選択。煩雑さは script 化を避ける理由にならない
- **transcript の集計**: 非標準の tool-signatures 機能に依存せず、標準の transcript を script で集計する方式を選んだ。標準機能内で実現できるなら非標準拡張を持ち込まない
- **`~/.claude.json` の肥大化対策**: 手動整理ではなく hook + script で決定的に実行する形を理想とした

判断軸は 3 つ — テスト可能になるか / トークンを減らせるか / 標準機能内に収まるか。

### 開発環境の統一度 (Q24)

**価値**: 両立 — プロジェクト依存は統一 (再現性)、エディタ・シェルは個人の自由 (職人的な成長)。

### CLI vs GUI (Q25)

**価値**: CLI-first — スクリプト化・自動化が容易なターミナル中心。

## 9. AI・LLM 活用

### AI への委譲範囲 (Q26)

**価値**: 補助的活用 — スケルトン生成・定型コード・調査に限定し、設計判断は人間が行う。

### AI-navigable codebase (Q27)

**価値**: 「AI が理解しやすいコードベース」を積極的に設計目標にする。AI に理解しやすいことは結局、人にも理解しやすいことになる。

### プロンプトエンジニアリング (Q28)

**価値**: ツール利用の一部 — 特別な中核スキルとは位置づけない。

## 10. エラーハンドリング

### エラー処理の哲学 (Q29)

**価値**: Fail fast + 契約による設計。事前条件・事後条件・不変条件を明示し、違反は即座に可視的に失敗させる。

**採用概念**:
- Fail Fast — Jim Shore (IEEE Software, 2004) — https://martinfowler.com/ieeeSoftware/failFast.pdf
- Design by Contract — Bertrand Meyer『Object-Oriented Software Construction』(2nd ed. 1997、書籍)。DbC は fail fast の実装手段の一つ (対立概念ではない)。

### 失敗の可視性 (Q30)

**価値**: 開発時は fail fast、本番は graceful degradation (コア機能を維持した段階的縮退)。沈黙の失敗 (握りつぶし・暗黙の自動回復) は最悪。

## 11. メタ (価値観についての価値観)

### 価値観の変化 (Q31)

**価値**: 価値観は進化する。本書がその正本 — 変更はまず本書に反映し、SKILL.md へ蒸留し直す。

### 価値観の衝突 (Q32)

**価値**: チーム・組織と衝突したら、事業との整合性がどちらにあるかを対話で探す。どちらでもよいなら、システムやチームメンバーが受け入れやすい方を選ぶ。この価値観は基準であって、常に最適なわけではない。

### 補足 (Q33)

**価値**: (追記なし — 原回答「なし」)
