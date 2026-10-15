# hook のレビュー観点

## いつこの doc を Read するか

hook の登録を編集する (settings の `hooks` セクション / plugin の `hooks/hooks.json`。両者の違いは「仕様」節) / hook script を新設 or 変更する / hook を**どの event slot に置くか**判断するときに Read する。ops 寄りのログ整形やフォーマッタの変更だけで完結するなら本 doc は不要。

script が Claude へ届ける**注入文の文面**は Inferential なので本 doc の対象外。本 doc が見るのは注入を運ぶ機構 (event / matcher / exit code / fail-posture) まで。

## 責務

hook は Claude Code のイベント (tool 実行前後 / セッション開始 / 停止など) をフックして、外部スクリプトを起動する仕組み。Claude 本体ではなくハーネス側で実行される。

- **目的**: 自動的に発火させたい振る舞い (許可 / 拒否 / 観測 / 注入 / 書き換え) を、Claude のメインコンテキストを汚染せずに実装する。
- **対象外**: Claude に「やってもらいたい」手順 (それは skill の責務)。

## 仕様

各 hook は event と matcher を指定し、ヒット時にコマンドを実行する。登録先は 2 経路あり、**構造が違う**ので取り違えない:

| 経路 | 登録先 | 構造 |
|---|---|---|
| settings 経由 (global / project) | `~/.claude/settings.json` / `<repo>/.claude/settings.json` の `hooks` セクション | settings の中の `hooks` キー配下に event を並べる |
| plugin 配布 | plugin root の `hooks/hooks.json` (自動 discovery。plugin.json に `hooks` を書かない) | ファイル最上位に **外側 `"hooks"` wrapper が必須**。wrapper 無しは silently invisible で hook が不発になる |

plugin 経路は settings のトップレベル event 構造とは別物。どちらに置くかを先に決めてから書く。

### event 種別 (主なもの)

| event | 発火タイミング | 主な用途 | block の経路 | Claude へ文を届ける経路 |
|---|---|---|---|---|
| `PreToolUse` | tool 実行前 | 拒否 / 書き換え / 引数検証 | exit 2 か `hookSpecificOutput.permissionDecision: "deny"` (stderr / `permissionDecisionReason` が拒否理由として Claude に届く) | `hookSpecificOutput.additionalContext` |
| `PostToolUse` | tool 実行後 | ログ取得 / 副次処理 / 観測 | tool は実行済みで止められない。`decision: "block"` は `reason` を tool 結果の隣に添え、exit 2 は stderr を Claude に見せるだけ | `decision: "block"` の `reason` / `hookSpecificOutput.additionalContext` |
| `SessionStart` | セッション開始時 | 初期 context 注入 / 環境チェック | block できない | exit 0 の stdout / `hookSpecificOutput.additionalContext` |
| `Stop` | main agent が応答を終えるたび | 停止の block (応答を続けさせる) / 通知 | exit 2 か `decision: "block"` (stderr / `reason` が続ける理由として Claude に届く) | `hookSpecificOutput.additionalContext` |
| `SubagentStop` | subagent が応答を終えるたび | subagent の停止の block | exit 2 か `decision: "block"` (`reason` が subagent への次の指示になる) | `hookSpecificOutput.additionalContext` |
| `SessionEnd` | セッション終了時 | 後片付け / ログ送出 | block できない | なし |
| `UserPromptSubmit` | ユーザー送信後、Claude に届く前 | プロンプトの block / context の追加 | exit 2 か `decision: "block"` (stderr / `reason` はユーザーに出て、context には入らない) | exit 0 の stdout / `hookSpecificOutput.additionalContext` |

「Claude へ文を届ける経路」列は exit 0 で返す経路だけを挙げる。JSON の出力 (`decision` / `hookSpecificOutput`) は exit 0 で stdout に出す。exit 0 の plain な stdout が context に入るのは表の event では `SessionStart` と `UserPromptSubmit` だけで、ほかの event では debug log に落ちて Claude に届かない。

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
- [ ] exit code と出力が意図どおりか (block は event 表の「block の経路」列の方法で返す。0 と 2 以外の非 0 はほとんどの event で block しないので、script が異常終了しても処理は続く = fail-open。検査系が Claude に警告するなら、event 表の「Claude へ文を届ける経路」列から選んで exit 0 にする — plain な stdout が Claude に届く event は表の直下の段落のとおり限られる。deny 目的の hook は、異常終了時に fail-open を許すか、`trap` 等で exit 2 に倒して fail-closed にするかを決める)
- [ ] 副作用が冪等か (同じ event が複数回発火しても安全か)
- [ ] 実行時間が短いか (重い処理は別プロセスで非同期化)

## アンチパターン

- **重い同期処理**: hook 内で長時間ブロックすると Claude セッション全体が遅くなる。非同期化 (バックグラウンド起動) を検討する。
- **広すぎる matcher**: `PreToolUse` で `.*` matcher は事故源。具体的な tool 名で絞る。
- **exit code の取り違え**: deny のつもりで exit 1 を返すと、ほとんどの event で block されず素通りする。逆に `set -e` 下で exit 2 を返すコマンド (`grep` のエラー等) が落ちると、意図しない block を生む。block は event 表の「block の経路」列の方法 (exit 2 か、event に応じた JSON の `decision: "block"` / `permissionDecision: "deny"`) で意図して返す。
- **harness と ops の混在**: 同一ディレクトリに置くと、ops の変更で harness が壊れるリスクが上がる。
- **冪等性の欠如**: 同じ event が複数回発火する状況 (リトライなど) で副作用が累積する設計は壊れる。

## 参照

- 共通: [sources](./sources.md) (公式 event 一覧と payload 仕様)
- 関連: [settings](./settings.md) (`hooks` セクションの event / matcher 登録)
- 公式: Claude Code hooks ドキュメント (URL は [sources](./sources.md) 経由で確認)
