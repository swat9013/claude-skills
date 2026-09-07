# hook 注入文のレビュー観点

## いつこの doc を Read するか

hook script が Claude へ届ける注入文 (SessionStart の初期 context / UserPromptSubmit の追記 / PreToolUse の warning) を書く・見直すときに Read する。同じ script でも event / matcher / exit code / fail-posture といった機構側の作りは [hook](../../../../knowledge/claude-config-review/references/hook.md) が正本。

## 責務

注入文は hook 機構が運ぶ Inferential な指示文。読み手は Claude で、届き方 (いつ・何回) は hook の event が決める。

- **目的**: 自動的に効かせたい規範・文脈を、人手の prompt を待たずに注入する。
- **対象外**: 注入を運ぶ機構そのもの (それは [hook](../../../../knowledge/claude-config-review/references/hook.md) の責務)、全セッションで必要な規範 (それは [claude-md](./claude-md.md) の責務)、特定ファイル編集時だけ要る細則 (それは [rules](./rules.md) の責務)。

## 仕様

**届き方が量の上限を決める。** 同じ文面でも event が変われば context への負荷が変わる。

| event | 届き方 | 量の制約 |
|---|---|---|
| `SessionStart` | セッション開始時に 1 回、以後 context に残る | CLAUDE.md と同じ常時ロード枠を食う |
| `UserPromptSubmit` | ユーザー送信のたび | ターンごとに加算される。最短にする |
| `PreToolUse` / `PostToolUse` | 該当 tool の呼び出しのたび | 発火頻度がそのまま増分になる |

文面そのものの規則 (何をどう書くか / 既定で足りるので書かないもの) は [writing](../writing.md) が正本。対象モデルは注入先のセッションモデルなので、確定しているときだけ [models/](../models/) の該当 1 本まで降りる。

## チェックリスト

注入文を新設・編集する前に確認する。

- [ ] 文面が [writing](../writing.md) の規則に沿っているか (強調語の盛り / 予防的な指示 / 既定で足りる指示が入っていないか)
- [ ] hook の出力 (stdout / stderr / `additionalContext`) が Claude にどう見えるかを実際に確認したか — 見え方を確かめずに書いた注入文は届かないまま成立する
- [ ] 注入量が最小か。**event の発火頻度 × 文面の長さ**で見積もったか
- [ ] 同じ規範を CLAUDE.md / rules と二重に注入していないか (二重は片方しか更新されず drift する)
- [ ] 決定論的に止められる規制を注入文の「気をつけろ」で運んでいないか — 止められるなら hook の deny 側 (C/Sensor) へ寄せる → [architecture](../architecture.md)

## アンチパターン

- **注入しすぎる**: hook から追加プロンプトを厚く注入すると、コンテキストが膨らみ本来のタスクが押し出される。注入は最小限にする。
- **常時ロードで足りる規範を SessionStart 注入で運ぶ**: CLAUDE.md と二重管理になり、どちらが正本か判断依存になる。全セッションで必要なら CLAUDE.md へ置く。

## 参照

- 共通: [architecture](../architecture.md) (注入は C/Guide。deny は C/Sensor) / [writing](../writing.md) (文面規則の正本) / [models](../models.md) (注入量と context 制約)
- 関連: [hook](../../../../knowledge/claude-config-review/references/hook.md) (注入を運ぶ script 側の event / matcher / fail-posture)
- 公式: Claude Code hooks ドキュメント (URL は [sources](../../../../knowledge/claude-config-review/references/sources.md) 経由で確認)
