# 一次情報源とエスカレーション戦略

## このドキュメントの目的

Claude Code の仕様が不明 / 古い / 矛盾しているときに、どの順で情報源を当たるかを定める。メインコンテキストに untrusted な生 web 内容を流入させないためのフィルタ戦略も含む。

## 一次情報源

| 種別 | URL | カバー範囲 |
|---|---|---|
| Claude API ドキュメント | https://platform.claude.com/docs | Claude API 全般 (Messages API / tool use / 価格など)。旧 `docs.anthropic.com` はこのホストへ 301 |
| Claude Code ドキュメント | https://code.claude.com/docs | Claude Code CLI 全般 (skill / hook / settings) |
| Claude Code GitHub | https://github.com/anthropics/claude-code | changelog / issues / PR |
| Claude Agent SDK | https://github.com/anthropics/claude-agent-sdk | Agent SDK |

## エスカレーション戦略

仕様調査は以下の順で頼る。下に行くほどコストが高い / メインコンテキスト汚染リスクが上がる。

1. **第 1 候補: `claude-code-guide` subagent**
   - 対象: CLI / Agent SDK / Claude API 全般の質問。
   - 利点: 公式情報源を内部的に参照済み。回答が簡潔。
   - 限界: 新情報の取り込みにラグがある可能性 (下記シグナル参照)。

2. **第 2 候補: `web-research` subagent (Task tool 経由)**
   - 対象: 横断的な記事比較 / 周辺情報 / 第 1 候補で答えが不確実だったとき。
   - 利点: subagent 内で web 内容を消費するので、メインコンテキストが汚染されない。

3. **第 3 候補: 公式 URL を直接 `WebFetch`**
   - 対象: 単一 URL の最終確認 (例: changelog の特定エントリを引用したい)。
   - 注意: 結果がメインコンテキストに直接入るので、要約を自分で短くまとめてから記録する。

メインコンテキストで生 web 内容を直接吸わない。常に subagent でフィルタする (3 番目の例外を除く)。

## `claude-code-guide` subagent で答えが出ないシグナル

以下のいずれかが出たら `web-research` に escalate する。

- 回答が古い (例: 半年以上前の changelog しか引いていない)
- 出典が曖昧 (「公式 docs に書いてある」と言うが URL なし)
- 矛盾する 2 案が同居している
- 「I don't know」「unable to determine」相当の回答

## 既知の罠

- **古い記事のサンプル**: deprecated API 呼び出しを今もコピペで紹介する第三者記事が多い。公式 docs と日付を必ず照合する。
- **撤回されたモデル ID を引用する記事**: モデル ID は時々変わる ([models](./models.md))。記事の例より公式 docs を信じる。
- **非公式ブログのベストプラクティス**: 公式 prompt engineering ガイドと矛盾するケースがある。公式優先。
- **changelog の解釈ミス**: GitHub の release notes には「破壊的変更」と書かれていない破壊的変更が混じることがある。怪しいときは issues を一度検索する。

## 更新トリガー

以下のいずれかで本ドキュメントを更新する。

- URL が 404 になった
- 公式ドキュメントが新ドメインに移行した
- SDK 名称・パッケージ名が変更された (例: `claude-agent-sdk` → `agent-sdk` のような名称変更)
- エスカレーション順序の変更が必要になる新 agent / tool が追加された
