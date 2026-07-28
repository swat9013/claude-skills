---
name: claude-config-review
user-invocable: true
description: Claude Code 設定 (skill / hook / CLAUDE.md / settings / rules) を 5 種コンポーネント別の subagent に分散してレビューする。args で path 指定 or args なしで git diff 自動検出。AI 可読 JSON で出力。Use when 「Claude Code 設定レビュー」「config レビュー」「skill / hook / CLAUDE.md / settings / rules を見て」「harness レビュー」.
---

# claude-config-review

Claude Code 5 種コンポーネントのレビュー skill。reference は本 skill の `references/` 配下を Source of truth とする。

## 入力

- `/swat-skills:claude-config-review` (args なし) → 後述「自動検出」で対象抽出
- `/swat-skills:claude-config-review path1 path2 ...` → 明示 path リスト (相対 / 絶対 / `~/` どれでも可)

`~/` で始まる path は main agent が `$HOME` に展開してから後述の type 判定にかける。

検出件数 0 のときは「対象なし」を伝え正常終了。

## 自動検出 (args なし時)

以下 2 コマンドの出力を union し、後述の path パターンに合致するものだけを対象化する。

```bash
git status --porcelain | awk '{print $2}'
git diff --name-only HEAD
```

注: rename 表記 (`R  old -> new`) や path に space を含むファイルでは `awk '{print $2}'` での分割が破綻する。確実性を要する場合は args で path を明示渡しすること。

## ファイル種類判定 (path パターン)

| パターン (glob) | type | type-specific reference |
|---|---|---|
| `**/SKILL.md` | skill | references/skill.md |
| `**/hooks/hooks.json`, `**/hooks/*.sh` | hook | references/hook.md |
| `**/CLAUDE.md`, `~/.claude/CLAUDE.md` | claude-md | references/claude-md.md |
| `**/settings*.json` (含む `~/.claude/settings.json`) | settings | references/settings.md |
| `**/.claude/rules/*.md`, `**/rules/*.md` | rules | references/rules.md |
| 上記以外 | (skip + warn) | — |

判定衝突時は CLAUDE.md / SKILL.md / hooks*.json / rules/ のファイル名固有パターンを優先し、settings*.json は汎用 fallback。

自己レビューについて: 本 skill 自身の `skills/knowledge/claude-config-review/SKILL.md` も `**/SKILL.md` に合致し対象化される (意図通り)。`references/*.md` はいずれの type パターンにも合致しないので skip される。

全 type 共通参照 (絶対 path):

- `~/.claude/skills/swat-skills/skills/knowledge/claude-config-review/references/architecture.md`
- `~/.claude/skills/swat-skills/skills/knowledge/claude-config-review/references/models.md`
- `~/.claude/skills/swat-skills/skills/knowledge/claude-config-review/references/sources.md`
- `~/.claude/skills/swat-skills/skills/knowledge/claude-config-review/references/references.md`

## 実行フロー (per-type ad-hoc dispatch)

1. main agent が args / git 自動検出から path リストを生成
2. 各 path を type に分類 (skill / hook / claude-md / settings / rules / skipped)
3. skipped は warn ログを残し続行
4. 残り bucket それぞれを Agent tool で **並列** dispatch (同一 message 内で複数 tool use)
   - `subagent_type: general-purpose` (read-only 制約は prompt で担保)
   - prompt は下記 template
   - 1 bucket = 1 subagent (bucket 内ファイルは同 subagent が連続レビュー)
5. 全 subagent 完了後、main agent が結果 JSON を merge → 最終 JSON を 1 つの fenced code block (```json) で stdout に書く。merge 時は各 subagent 返答の top-level `type` を、その subagent の各 finding に `type` フィールドとして付与してから配列に concat する。

### subagent prompt template (per-type)

```
あなたは Claude Code の <TYPE> コンポーネントのレビュアーです。

参照する reference (絶対 path):
- 共通: ~/.claude/skills/swat-skills/skills/knowledge/claude-config-review/references/architecture.md
- 共通: ~/.claude/skills/swat-skills/skills/knowledge/claude-config-review/references/models.md
- 共通: ~/.claude/skills/swat-skills/skills/knowledge/claude-config-review/references/sources.md
- 共通: ~/.claude/skills/swat-skills/skills/knowledge/claude-config-review/references/references.md
- type 別: ~/.claude/skills/swat-skills/skills/knowledge/claude-config-review/references/<TYPE>.md

レビュー対象 (絶対 path):
- <PATH_1>
- <PATH_2>
- ...

手順:
1. 上記 reference を Read で全部読む (architecture.md → type 別 → 共通の順)
2. 各対象ファイルを Read
3. reference に照らして違反 / 改善余地を抽出
4. 以下の JSON schema に厳密に従い、結果のみを 1 つの JSON object として返す (前後にテキスト不要)

{
  "type": "<TYPE>",
  "findings": [
    {
      "file": "absolute or repo-relative path",
      "severity": "critical|major|minor|info",
      "category": "frontmatter|description|body|consistency|permission|model|...",
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

## 出力 schema (main agent 統合後)

```json
{
  "schema_version": "1",
  "summary": {
    "total_files": 3,
    "by_type": { "skill": 1, "settings": 2 },
    "findings_count": 7,
    "skipped": []
  },
  "findings": [
    {
      "file": "skills/dev/foo/SKILL.md",
      "type": "skill",
      "severity": "major",
      "category": "frontmatter",
      "summary": "frontmatter name が kebab-case ではない (foo_bar)",
      "evidence": "skills/dev/foo/SKILL.md:2 — `name: foo_bar`",
      "recommendation": "name: foo-bar に変更し、補完候補に出るようにする",
      "applied_refs": ["references/skill.md#frontmatter"]
    }
  ]
}
```

severity 基準:

- critical: skill 動作不能・hook 不発・permission 漏れ等の機能影響
- major: 仕様逸脱・補完外れ・誤動作リスク
- minor: 規約逸脱・命名揺れ・冗長
- info: 改善提案・将来のリスク

