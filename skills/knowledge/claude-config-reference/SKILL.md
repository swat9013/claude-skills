---
name: claude-config-reference
description: Claude Code の Computational harness (settings / permission / hook script) の仕様・レビュー観点と、仕様を一次情報で確かめるときの情報源の順を持つ参照知識。Use when「permission pattern の広さや allow / deny の置き場を判断する」「hook の event・matcher・fail-posture を決める」「settings / hook の仕様の鮮度を一次情報で確かめる」.
user-invocable: false
---

# claude-config-reference

Claude Code の Computational harness (settings / permission / hook script) を編集・新設・レビューするときに引く参照知識。中身は Claude Code の仕様と汎用ベストプラクティス。

Inferential 側 (CLAUDE.md / rules / SKILL.md 指示文 / hook 注入文 / agent 定義) の文面規範は対象外で、呼び出し元のフローに委ねる。ただし一次情報源の引き先 ([sources](references/sources.md)) は Inferential 側の照合でも共用する。

## 索引

| 種別 | ファイル | いつ Read するか |
|---|---|---|
| コンポーネント | [settings](references/settings.md) | settings.json / settings.local.json を編集する前、`permissions` / `hooks` の宣言場所に迷ったとき |
| コンポーネント | [hook](references/hook.md) | hook の登録・script を新設 / 変更する前、hook をどの event に置くか判断するとき |
| 共通 | [sources](references/sources.md) | 仕様が不明・古い・矛盾しているとき、公式のベストプラクティスと照らすとき (一次情報源とエスカレーション順) |

プロジェクト固有の価値観文書 (CLAUDE.md と、対象 repo の `docs/agents/domain.md` が案内する用語集・ADR) があれば併用し、食い違ったらプロジェクト側を優先する。
