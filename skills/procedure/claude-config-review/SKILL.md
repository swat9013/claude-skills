---
name: claude-config-review
user-invocable: true
description: Claude Code の Computational harness (settings / permission / hook script) を component 別の subagent に分散してレビューする。args で path 指定、args なしなら git diff から自動検出。Inferential (CLAUDE.md / rules / SKILL.md 指示文 / hook 注入文) は対象外。Use when 「settings をレビュー」「permission を見て」「hook script をレビュー」「Computational harness をレビュー」.
---

# claude-config-review

Computational harness (settings / permission / hook script) の review 手順。決定論的に動かない Inferential 側 (CLAUDE.md / `.claude/rules/` / SKILL.md 指示文 / hook 注入文) は本 skill の対象外で、そちらの規範と authoring 手順は `write-for-harness` が持つ。

レビュー観点の reference は本 skill の `references/` 配下を Source of truth とする (索引は [references/README.md](references/README.md))。

## 入力と対象の抽出

- `/swat-skills:claude-config-review path1 path2 ...` → 明示 path リスト (相対 / 絶対 / `~/` どれでも可)。`~/` で始まる path は main agent が `$HOME` に展開してから次節の type 判定にかける
- `/swat-skills:claude-config-review` (args なし) → 以下の自動検出で対象抽出

検出件数 0 のときは「対象なし」を伝え正常終了。

args なし時は、以下 2 コマンドの出力を union し、次節の path パターンに合致するものだけを対象化する。

```bash
git status --porcelain | awk '{print $2}'
git diff --name-only HEAD
```

注: rename 表記 (`R  old -> new`) や path に space を含むファイルでは `awk '{print $2}'` での分割が破綻する。確実性を要する場合は args で path を明示渡しすること。

union が 0 件でも即「対象なし」で終えない — 差分を commit 済みの worktree / branch では上記 2 コマンドはどちらも空になる。branch が main より進んでいる場合は `git diff --name-only $(git merge-base origin/main HEAD)..HEAD` (origin/main が無ければ main) の branch diff を対象化する。

## ファイル種類判定 (path パターン)

| パターン (glob) | type | type-specific reference |
|---|---|---|
| `**/hooks/hooks.json`, `**/hooks/**/*.sh`, `**/hooks/**/*.py` | hook | references/hook.md |
| `**/settings*.json` (含む `~/.claude/settings.json`) | settings | references/settings.md |
| 上記以外 | (skip + warn) | — |

判定衝突時は `hooks/hooks.json` のファイル名固有パターンを優先し、`settings*.json` は汎用 fallback (settings の `hooks` セクションは settings 型として扱う — 登録の妥当性は settings.md の観点で見る)。

`**/SKILL.md` / `**/CLAUDE.md` / `**/.claude/rules/*.md` と、注入文を持つ hook script は Inferential なので skip し、「Inferential は対象外なので `write-for-harness` へ回す」と告げてから残りを進める。

hook パターンの補足:

- `**` は 0 階層も含めて解釈する (`hooks/foo.sh` も `hooks/harness/foo.sh` も hook)。hook script の配置階層は repo ごとに違い、階層固定の pattern は実配置を取りこぼす
- hook script は shell (`.sh`) と python (`.py`) の両方がある。片方だけを pattern に持つと、もう片方が丸ごと未レビューになる
- `**/hooks/**/tests/**` は hook script の自動テストであり hook 設定そのものではないので skip する (hook 編集の diff には高確率で同居するが、hook.md の観点は適用できない)

## 実行フロー (per-type の並列 dispatch)

1. main agent が args / git 自動検出から path リストを生成
2. 各 path を type に分類 (hook / settings / skipped)
3. skipped は warn ログを残し続行
4. 残り bucket それぞれを Agent tool で **並列** dispatch (同一 message 内で複数 tool use)
   - `subagent_type: general-purpose` (read-only 制約は prompt で担保)
   - **`name` を付けずに起動する** — name 付きは background (mailbox) 型になり、最終報告が tool_result で返らず idle 通知しか届かない (merge できないまま orphan agent が残る)。無名 dispatch なら結果を tool_result で同期回収できる。それでも結果が得られない場合は、main agent が reference を読んで代行レビューし、その旨を最終出力に明記する
   - prompt は下記「subagent prompt template」。1 bucket = 1 subagent (bucket 内ファイルは同 subagent が連続レビュー)。`model` は指定しない (セッション継承)
5. 全 subagent 完了後、main agent が結果 JSON を merge → 最終 JSON を 1 つの fenced code block (```json) で stdout に書く。merge 時は各 subagent 返答の top-level `type` を、その subagent の各 finding に `type` フィールドとして付与してから `findings` 配列に concat する。最終 JSON は対話文脈でも必ず出力し、findings 配列を JSON block 内に置く (markdown 表で代替しない)。人間向け要約は、JSON block の後に分離して書く。

### subagent prompt template

```
あなたは Claude Code の <TYPE> コンポーネントのレビュアーです。

参照する reference (絶対 path):
- 共通: ${CLAUDE_SKILL_DIR}/../../steering/write-for-harness/references/architecture.md
- 共通: ${CLAUDE_SKILL_DIR}/references/sources.md
- type 別: ${CLAUDE_SKILL_DIR}/references/<TYPE>.md

レビュー対象 (絶対 path):
- <PATH_1>
- <PATH_2>
- ...

手順:
1. 上記 reference を Read で全部読む (architecture.md → type 別 → sources.md の順)
2. 各対象ファイルを Read
3. reference に照らして違反 / 改善余地を抽出する。加えて、対象ファイル単体としてのあるべき姿 (節構成・冗長な記述の削除) の再設計提案も出す。他 component への移動・統廃合の提案は書かない
4. 以下の JSON schema に厳密に従い、結果のみを 1 つの JSON object として返す (前後にテキスト不要)

{
  "type": "<TYPE>",
  "findings": [
    {
      "file": "absolute or repo-relative path",
      "severity": "critical|major|minor|info",
      "category": "permission|event|matcher|fail-posture|consistency|...",
      "summary": "1 文要約",
      "evidence": "path:line — 該当箇所の quote",
      "recommendation": "具体的な修正方針",
      "applied_refs": ["references/<TYPE>.md#section", ...]
    }
  ]
}
```

Edit / Write は使わない。Read のみで分析を完結させる。

`<TYPE>` と `<PATH_*>` は main agent が dispatch 時に埋めること。

対象の変更文脈 (diff の要点等) は prompt 末尾に追記してよい。ただし追記は command 出力等で検証済みの事実に限る — 未検証の推測を書くと誤事実が reviewer の判断に混入する。reference リストと手順 4 段は改変しない。

**`${CLAUDE_SKILL_DIR}` は本 SKILL.md をロードした時点で skill ディレクトリの絶対 path へ置換済み**なので (install 形態が symlink でも marketplace plugin でも解決される)、main agent が読んでいる template には既に実 path が入っている。それを**そのまま**写す — 展開後の絶対 path を本文へ書き戻したり、変数表記を復元して渡したりしない (subagent の prompt は string substitution を受けないので、`${CLAUDE_SKILL_DIR}` の literal を渡すと reference が 1 本も読めないまま「reference なしのレビュー」が黙って成立してしまう)。写した後の prompt に `${` が残っていないことを送信前に確認する。

## 出力 schema (main agent 統合後)

```json
{
  "schema_version": "1",
  "summary": {
    "total_files": 3,
    "by_type": { "settings": 2, "hook": 1 },
    "findings_count": 7,
    "skipped": []
  },
  "findings": [
    {
      "file": "settings/settings.local.json",
      "type": "settings",
      "severity": "major",
      "category": "permission",
      "summary": "permissions.allow の pattern が広すぎる (Bash(git:*))",
      "evidence": "settings/settings.local.json:12 — `\"Bash(git:*)\"`",
      "recommendation": "サブコマンド単位 (`Bash(git diff:*)`) へ絞る",
      "applied_refs": ["references/settings.md#アンチパターン"]
    }
  ]
}
```

severity 基準:

- critical: hook 不発・permission 漏れ等の機能影響
- major: 仕様逸脱・誤動作リスク
- minor: 規約逸脱・命名揺れ・冗長
- info: 改善提案・将来のリスク
