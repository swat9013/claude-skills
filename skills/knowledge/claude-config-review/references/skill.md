# skill のレビュー観点

## いつこの doc を Read するか

skill (`SKILL.md` / `skill.md`) を新設・編集する前、または既存 skill の `description` / `model` / `allowed-tools` / `disable-model-invocation` を変更する前に Read する。**proactive (description trigger で自動起動) / evaluative (手動 or hook 連動で事後評価) のどちらの用途か** をここで決める。

## 責務

skill は Claude Code が特定タスクに対して引き出すべき手順・知識・チェックリストをまとめた markdown ファイル。`Skill` tool 経由で本文がメインコンテキストにロードされる。

- **目的**: 高頻度の意思決定パターン / 仕様暗黙知 / 反復チェックを再利用可能な形で外出しする。
- **proactive (I/Guide)**: `description` の trigger 語彙から自動起動し、事前に手順をロードする用途。
- **evaluative (I/Sensor)**: `disable-model-invocation: true` + 手動呼び出し or hook 連動で、行動後に semantic 評価を返す用途。slot 判断の根拠は [architecture](./architecture.md) を参照。
- **並列実行を伴う skill の実装基盤**: skill 内で複数の skill を並列起動する設計 (例: `observe-and-reflect`) は Agent Teams (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) を前提とする。Task tool subagent は Skill ツールを invoke できず、`claude -p` は 2026-06-15 以降 subscription の separate monthly credit を消費する。Skill を介さない並列処理 (大量 grep 集計 / ファイル I/O fan-out 等) は引き続き Task tool subagent / `dispatching-parallel-agents` を使う。判断軸の根拠は 2026-05-27 `observe-and-reflect` PoC。
- **対象外**: 1 回限りのタスク (skill 化のオーバーヘッドが回収できない)、自動実行 (それは hook の責務 → [hook](./hook.md))。

## 仕様

skill ファイルは frontmatter + 本文の構成。

### frontmatter 主要フィールド

| フィールド | 必須 | 意味 |
|---|---|---|
| `name` | ✓ | skill 名 (一意。kebab-case 推奨) |
| `description` | ✓ | いつこの skill を起動すべきかの説明。trigger 語彙を含めると model が想起しやすい |
| `model` | - | 実行モデル指定。ID 一覧は [models](./models.md) の表が正本 (ここに再掲しない — 世代が上がると二重管理で腐る)。省略時は呼び出し元のモデルを継承 |
| `allowed-tools` | - | この skill 実行時に許可する tool のリスト。最小化が原則 |
| `disable-model-invocation` | - | true にすると model が自動で skill を起動しなくなる (ユーザー明示呼び出しのみ) |

### 配置

- **project-local**: `<repo>/.claude/skills/<name>/SKILL.md` (リポジトリで共有)
- **global**: `~/.claude/skills/<name>/SKILL.md` (ユーザー全環境で共有)

配置選好 (どちらをデフォルトにするか) はプロジェクト側 CLAUDE.md / rules で規定する。本 doc は仕様のみ扱う。

## チェックリスト

skill を新設・編集する前に確認する。

- [ ] `description` が trigger 語彙を含むか (× 「便利な機能」 / ○ 「commit message を書く / git commit / コミット作成」)
- [ ] `description` が過剰トリガーになっていないか (汎用語のみだと無関係な場面で起動する)
- [ ] proactive / evaluative どちらの用途か明確で、`disable-model-invocation` の有無と整合しているか
- [ ] `model:` がタスク負荷と整合か → [models](./models.md) の表で照合
- [ ] 本文の prompt スタイルが指定モデルに最適か ([prompting-principles](../../prompting-principles/SKILL.md) が文面規則の正本。共通原則で足りるか、対象モデル確定なら同 skill の references/ の差分まで見る)。`model:` 未指定の skill は呼び出し元継承なので Opus 5 を既定と仮定して照合する。haiku は同 skill に reference がなく [models](./models.md) の Haiku 節が正本
- [ ] `allowed-tools` が最小化されているか (× 全 tool / ○ 必要な tool のみ列挙)
- [ ] 配置場所が project-local vs global で妥当か (判断基準はプロジェクト CLAUDE.md / rules 側)
- [ ] 既存 skill との重複がないか (同様の trigger 語彙を持つ skill との競合)
- [ ] 並列実行を伴う skill の場合、実装基盤に Agent Teams を選んでいるか (Task tool subagent / `claude -p` を選んでいないか)
- [ ] 本文が参照する references / scripts ファイルは SKILL.md 本文から明示的にリンクされているか (SKILL.md から参照されない補助ファイルは load / 実行されず無視される)
- [ ] 本文に AC-xxx 等の冗長な acceptance-criteria ラベルを書いていないか (必要なければ排除)
- [ ] 編集後に type (discipline-enforcing / technique / pattern / reference) を分類してテスト強度を決めたか。discipline-enforcing skill は combined-pressure シナリオで境界遵守を検証する
- [ ] 本文様式 (ルールの列挙 / 原則の列挙) を type から意図して選んだか — discipline-enforcing は禁止則、出力の形が崩れる失敗は手順・雛形、判断の質を担保する対話・思考系は原則。判断軸の正本は [prompting-principles](../../prompting-principles/SKILL.md) の「本文様式の選び方」
- [ ] 原則の列挙を選んだ場合、圧力シナリオの subagent micro-test を指示なし control と比較して束縛力を確認したか (原則だけで逸脱が抑止されるかは書いた時点では分からない)

### 自己完結性 (外部参照の腐敗)

skill は編集されないまま周囲が動くので、外部への参照は放置すると腐る。**本文が skill の外 (他 skill / 他ファイル / 決定記録 / 節番号) を指している箇所すべてを、現状と突き合わせてから確定する**。以下は 1 項目ずつ機械的に確かめられる:

- [ ] 名指しした skill 名 / path / script / 節番号は**実在するか** (`ls` / `git log` で確認。撤去・改名・移設で腐る箇所)
- [ ] 他 skill への `/<name>` 表記は**起動可能か** — `user-invocable: false` は Model-invoked 専用で slash 起動できない。`disable-model-invocation: true` 先は逆に slash が唯一の経路なので「依頼文を組み立てて user に `/<name>` の実行を提案する」形で書く
- [ ] 責務外リストに**同一 workflow 外の skill 名**を書いていないか — 責務外は「何をやらないか」+「呼び出し元のフローに委ねる」で足りる。名指しは相手が撤去・改名された時点で腐る。相互参照が双方の本文に明文化された対 (escalate 元/先、蒸留元/詳細版 等) のみ例外
- [ ] 特定ファイル番号・特定版への**ピン留め**をしていないか (「N 番が最新様式」「N〜M に完全準拠」「N が先例」)。対象が増えるたびに腐る — skill 内テンプレを唯一の正本と宣言するか、動的取得 (`ls` 等) へ置換する
- [ ] 引用した決定記録の **status は現行か** (Superseded / Deprecated な決定を手本や根拠に据えていないか)
- [ ] repo 事実の**断定が現状と一致するか** (「表の N 列に追記」「このディレクトリは空」等。存在しない編集先を指示していないか)
- [ ] 配布される skill の参照先は**配布先から到達できるか** (gitignore 済みの作業成果物 `.ai/` 等、repo 内部だけで意味を持つ path 表記は読者に届かない)

## アンチパターン

- **巨大な単一 skill**: 1 skill に多目的な手順を詰め込むと、`description` の trigger 精度が落ちる。タスクごとに分割する。
- **trigger 語彙の弱い description**: 「便利な機能」のような曖昧記述は呼ばれない / 呼ばれすぎのどちらかになる。
- **モデルとプロンプト形式の不整合**: haiku 指定 skill に opus 用の多段推論プロンプトを書く → 効かない ([models](./models.md) でモデルを選び、[prompting-principles](../../prompting-principles/SKILL.md) で文面を決める)。
- **`allowed-tools` 未指定で広範な権限**: 必要 tool だけ列挙する。「とりあえず全部」は事故源。
- **skill 内に絶対パスをハードコード**: 別環境で動かない。`~/<dotfiles>/` 形式のチルダ展開や動的解決を使う。
- **1 回限りのタスクを skill 化**: 再利用されない skill はノイズ。コマンド or プロンプトテンプレートで十分な場合は skill 化しない。
- **Skill 並列実行を Task tool subagent / `claude -p` で実装**: subagent は Skill ツールを invoke できず並列化が成立しない。`claude -p` は 2026-06-15 以降 separate monthly credit 消費。Skill を含む並列実行は Agent Teams を使う。
- **実装の詳細を skill に転記**: エイリアス一覧・キーバインド一覧・関数一覧・プラグイン一覧等、ソースを読めば分かる情報を skill に書かない。冗長で同期メンテコストも生む。記載すべきは「なぜそうなっているか」「どう判断するか」「踏みやすい罠」(設計判断基準 / 非自明な理由 / トラブルシューティング / gotcha) のみ。
- **撤去済み / 別 workflow の skill を責務外リストで名指し**: 相手が消えても本文は消えないので、参照だけが残って読者を存在しない skill へ誘導する。責務外は「やらないこと」の宣言で足り、引き継ぎ先の指定は呼び出し元のフローの仕事。
- **`disable-model-invocation: true` skill への委譲**: その skill は model の available skills 一覧に出ず、他 skill 本文に「`<name>` skill に渡す」と書いても Skill tool では起動できない。この skill への委譲は「依頼文を組み立ててユーザーに `/<name> <依頼文>` の実行を提案する」形で書く。逆に model-invocable な skill (`disable-model-invocation` 無し) は、main-loop で実行中の skill が `allowed-tools` に `Skill` を加えれば Skill tool で直接起動できる (実績: `wrapping-up` が `Skill contextual-commits` を起動)。Skill 起動が不可なのは ① subagent (Task tool) からの起動 ② `disable-model-invocation` 先への委譲 の 2 ケースのみで、「skill は skill を起動できない」と一般化しない。

## 参照

- 共通: [architecture](./architecture.md) (proactive=I/Guide / evaluative=I/Sensor の slot 配置根拠) / [models](./models.md) (モデル別 prompt 設計指針) / [sources](./sources.md) (公式仕様の引き方)
- 公式: Claude Code skills ドキュメント (URL は [sources](./sources.md) 経由で確認)
