# hook のレビュー観点

## いつこの doc を Read するか

hook の登録を編集する (settings の `hooks` セクション / plugin の `hooks/hooks.json`。両者の違いは「仕様」節) / hook script を新設 or 変更する / hook を**どの event slot に置くか**判断するときに Read する。ops 寄りのログ整形やフォーマッタの変更だけで完結するなら本 doc は不要。

## 責務

hook は Claude Code のイベント (tool 実行前後 / セッション開始 / 停止など) をフックして、外部スクリプトを起動する仕組み。Claude 本体ではなくハーネス側で実行される。

- **目的**: 自動的に発火させたい振る舞い (許可 / 拒否 / 観測 / 注入 / 書き換え) を、Claude のメインコンテキストを汚染せずに実装する。
- **対象外**: Claude に「やってもらいたい」手順 (それは skill の責務 → [skill](./skill.md))。

## 仕様

各 hook は event と matcher を指定し、ヒット時にコマンドを実行する。登録先は 2 経路あり、**構造が違う**ので取り違えない:

| 経路 | 登録先 | 構造 |
|---|---|---|
| settings 経由 (global / project) | `~/.claude/settings.json` / `<repo>/.claude/settings.json` の `hooks` セクション | settings の中の `hooks` キー配下に event を並べる |
| plugin 配布 | plugin root の `hooks/hooks.json` (自動 discovery。plugin.json に `hooks` を書かない) | ファイル最上位に **外側 `"hooks"` wrapper が必須**。wrapper 無しは silently invisible で hook が不発になる |

plugin 経路は settings のトップレベル event 構造とは別物。どちらに置くかを先に決めてから書く。

### event 種別 (主なもの)

| event | 発火タイミング | 主な用途 |
|---|---|---|
| `PreToolUse` | tool 実行前 | 拒否 / 書き換え / 引数検証 |
| `PostToolUse` | tool 実行後 | ログ取得 / 副次処理 / 観測 |
| `SessionStart` | セッション開始時 | 初期 context 注入 / 環境チェック |
| `Stop` | セッション停止時 | 通知 / 後片付け |
| `UserPromptSubmit` | ユーザー送信前 | プロンプト書き換え / 注入 |

### 役割の物理分離

hook スクリプトは目的別に 2 ディレクトリへ物理分離する。

- **harness hook**: Claude の振る舞いそのものを制御する (deny / 書き換え / 注入)。
- **ops hook**: 副次処理 (ログ / 通知 / メトリクス)。

物理分離することで、ops 系の変更が harness 系を巻き込まなくなる。ディレクトリ名 (例: `harness/` / `ops/`) を規約化するかはプロジェクト側の判断で、規約が無い場合も「1 script に harness と ops を混ぜない」ところまでは守る。

## チェックリスト

hook を新設・編集する前に確認する。

- [ ] event 種別が目的に合うか (時系列的に正しい挿入点か)
- [ ] matcher が広すぎないか (× `.*` / ○ 具体的な tool 名)
- [ ] hook の役割が deny / warning / 書き換え / 注入 / 観測 のどれかに明確化されているか
- [ ] ops 系の副次処理 (ログ / 通知 / メトリクス) を harness script に混ぜていないか — script 本文を読めば判定できる。ディレクトリ命名規約を持たないプロジェクトでも、この観点自体は検証できる
- [ ] エラー時に Claude セッションを破壊しないか (× `set -e` で軽微エラーも exit 1 → tool 拒否扱い / ○ 検査系は stdout に warning を出して exit 0、deny したい時だけ意図して exit 1)
- [ ] 副作用が冪等か (同じ event が複数回発火しても安全か)
- [ ] 実行時間が短いか (重い処理は別プロセスで非同期化)
- [ ] hook の出力 (stdout/stderr) が Claude にどう見えるかを確認したか

## アンチパターン

- **重い同期処理**: hook 内で長時間ブロックすると Claude セッション全体が遅くなる。非同期化 (バックグラウンド起動) を検討する。
- **広すぎる matcher**: `PreToolUse` で `.*` matcher は事故源。具体的な tool 名で絞る。
- **非 0 終了の暴発**: hook の細かいエラーで non-zero 終了させると Claude が tool 呼び出し失敗と誤解する。意図しない deny を生む。
- **harness と ops の混在**: 同一ディレクトリに置くと、ops の変更で harness が壊れるリスクが上がる。
- **冪等性の欠如**: 同じ event が複数回発火する状況 (リトライなど) で副作用が累積する設計は壊れる。
- **hook 内から Claude に追加プロンプトを注入しすぎる**: コンテキストが膨らみ、本来のタスクが押し出される。注入は最小限。

## 参照

- 共通: [architecture](./architecture.md) (hook が埋める slot: C/Guide = SessionStart 注入 / C/Sensor = PreToolUse deny / I/Sensor 連動 = Stop 起点の skill 連携) / [models](./models.md) (hook 内で Claude を呼び出す場合のモデル選定) / [sources](./sources.md) (公式 event 一覧と payload 仕様)
- 関連: [settings](./settings.md) (`hooks` セクションの event / matcher 登録) / [skill](./skill.md) (hook から起動する手順本体)
- 公式: Claude Code hooks ドキュメント (URL は [sources](./sources.md) 経由で確認)
