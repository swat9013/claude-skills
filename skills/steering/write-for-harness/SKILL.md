---
name: write-for-harness
# steering 初の user + model 両用。model からも起動させるのは、harness の指示文を
# 書く作業が他 skill の作業中に発生するため (disable-model-invocation を付けると
# その経路で規範が読まれないまま書かれる)
user-invocable: true
argument-hint: "[<path> | 新規: <component> <目的>]"
description: CLAUDE.md / .claude/rules/ / SKILL.md 指示文 / hook 注入文 (Inferential harness) を規範に照らして書き、検証 subagent と構造 reviewer の指摘まで反映する手順。settings / hook script (Computational) は対象外。Use when「CLAUDE.md に規範を足す」「rules を書く」「skill の指示文を書く・直す」「hook の注入文を書く」「harness の指示文を見直す」.
---

# write-for-harness

Inferential harness (CLAUDE.md / `.claude/rules/` / SKILL.md 指示文 / hook 注入文) を書く手順。決定論的に動く Computational 側 (settings / permission / hook script / gate) は本 skill の対象外で、そちらの規範は `claude-config-review` が持つ。

## 手順

### 1. 対象を決める

| args | 対象 |
|---|---|
| path (相対 / 絶対 / `~/`) | その file。`~/` は `$HOME` へ展開する |
| `新規: <component> <目的>` | 新規作成。`<component>` は skill / claude-md / rules / hook-inject のいずれか |
| なし | 下記の自動検出 |

args なしのときは、以下 2 コマンドの出力を union し、Inferential 4 種のパターンに合致するものだけを対象にする。

```bash
git status --porcelain | awk '{print $2}'
git diff --name-only HEAD
```

union が 0 件でも即終了しない — commit 済みの worktree では両方とも空になる。branch が main より進んでいるなら `git diff --name-only $(git merge-base origin/main HEAD)..HEAD` (`origin/main` が無ければ `main`) を対象にする。

| パターン (glob) | component |
|---|---|
| `**/SKILL.md` | skill |
| `**/CLAUDE.md` | claude-md |
| `**/.claude/rules/*.md`, `**/rules/*.md` | rules |
| hook が stdout / `additionalContext` へ出す注入文を持つ script | hook-inject |

`**/settings*.json` と、注入文を持たない hook script は Computational なので対象から外し、「Computational は対象外なので `claude-config-review` へ回す」と告げてから残りを進める。合致 0 件なら「対象なし」で正常終了する。

**書き換える対象は広げない** — 編集するのは args か直近の差分から出た file だけに閉じる (手順 4 の構造 reviewer は harness 全体を俯瞰するが、俯瞰は提案の入力であって編集対象ではない)。

### 2. 文書構成の正本を読む

`Skill(mattpocock-skills:writing-for-agents)` を invoke する。

invoke が失敗したら (plugin 未導入 / skill 名が解決できない)、そこで停止して次の 1 行だけを返して終了する:

> `mattpocock-skills` plugin が未導入のため write-for-harness を実行できません。`/plugin install mattpocock-skills@claude-plugins-official` を実行してから再度呼び出してください。

構成の正本を読めないまま手順 3 以降へ進まない。規範が欠けた状態で書いた文面は、欠けたことが誰にも見えないまま harness に残り続ける。

### 3. 規範を読む

component の責務 reference を Read する。

| component | Read する reference |
|---|---|
| skill | `${CLAUDE_SKILL_DIR}/references/components/skill.md` |
| claude-md | `${CLAUDE_SKILL_DIR}/references/components/claude-md.md` |
| rules | `${CLAUDE_SKILL_DIR}/references/components/rules.md` |
| hook-inject | `${CLAUDE_SKILL_DIR}/references/components/hook-inject.md` |

加えて文面規範 `${CLAUDE_SKILL_DIR}/references/writing.md` を Read する。

対象モデルが確定しているときだけ `${CLAUDE_SKILL_DIR}/references/models/` の該当 1 本 (`fable-5.md` / `opus-5.md` / `sonnet-5.md`) まで降りる。モデルは skill なら frontmatter の `model:`、他 component はセッションモデル。未確定なら writing.md の共通原則で止める — 差分を先に読むと、そのモデルにしか効かない対策を全体へ適用してしまう。モデル選定そのもの (能力帯・単価比・モデル ID) は `${CLAUDE_SKILL_DIR}/references/models.md` が正本。

新規作成で「どの機構に載せるか」から決めるなら、4 slot 表 (`guide` / `sensor` × `Computational` / `Inferential`) を `${CLAUDE_SKILL_DIR}/references/architecture.md` で引く。

変更の届け方は実行 project の運用に従う。

### 4. 検証 subagent と構造 reviewer を並列で起動する

`Agent` tool で `subagent_type: general-purpose` を 2 つ、**`name` を付けずに**同一 message 内で起動する (name 付きは background 型になり、結果が tool_result で返らない)。

- **検証 subagent**: 書いた file の文面を component 別 reference に照らす。`model` は指定せずセッション継承にする
- **構造 reviewer**: 書いた内容の**配置**が正しいかを harness 全体の中で見る。**対象が 1 ファイルでも常に 1 つ起動する**。`model: opus` を明示する (配置・統廃合の判断は harness 全体を俯瞰する多段推論のため)

構造 reviewer の入力になる「harness 全体一覧 (path + type)」は、起動前に main agent が決定論的コマンドで作る:

```bash
git ls-files
```

の出力を手順 1 の component パターン + `**/settings*.json` で濾し、合致した path に component 名を付けた一覧にする (skip 落ちは一覧にも載せない)。git 管理外の `~/.claude/` 系 path は、args で対象として渡された場合のみ一覧へ加える。一覧は prompt へ全件埋め込む — 各ファイルの全文 Read は reviewer が必要と判断したものだけが行う。

検証 subagent の prompt:

```
あなたは Claude Code の <COMPONENT> コンポーネントのレビュアーです。

参照する reference (絶対 path):
- 文面: <WRITING_REF>
- component 別: <COMPONENT_REF>
- モデル別 (対象モデル確定時のみ): <MODEL_REF>

レビュー対象 (絶対 path):
- <PATH_1>
- ...

手順:
1. 上記 reference を Read で全部読む (component 別 → 文面 → モデル別 の順)
2. 各対象ファイルを Read
3. reference に照らして違反 / 改善余地を抽出する。重要度で件数を絞らず全件出す。他 component への移動・統廃合の提案は書かない
4. 以下の JSON schema に厳密に従い、結果のみを 1 つの JSON object として返す (前後にテキスト不要)

{
  "type": "<COMPONENT>",
  "findings": [
    {
      "file": "absolute or repo-relative path",
      "severity": "critical|major|minor|info",
      "category": "frontmatter|description|body|consistency|model|...",
      "summary": "1 文要約",
      "evidence": "path:line — 該当箇所の quote",
      "recommendation": "具体的な修正方針",
      "applied_refs": ["reference の path#section", ...]
    }
  ]
}
```

Edit / Write は使わない。Read のみで分析を完結させる。

`<COMPONENT>` / `<PATH_*>` / 各 `*_REF` は起動時に埋める。対象モデルが未確定ならモデル別の 1 行を落とす。

構造 reviewer の prompt:

```
あなたは Claude Code harness の構造 (配置・統廃合・統治構造) のレビュアーです。個々のファイル内の文面品質は扱いません — それは検証 subagent の担当です。

参照する reference (絶対 path):
- 判断規則: ${CLAUDE_SKILL_DIR}/references/structure.md
- 共通: ${CLAUDE_SKILL_DIR}/references/architecture.md
- 共通: ${CLAUDE_SKILL_DIR}/references/models.md
- 共通: ${CLAUDE_SKILL_DIR}/../../knowledge/claude-config-review/references/sources.md

レビュー対象 (今回書いた file、絶対 path):
- <PATH_1>
- ...

harness 全体一覧 (path + component):
<INVENTORY>

手順:
1. 上記 reference を Read で全部読む (architecture.md → structure.md → 共通の順)
2. 対象ファイルを Read
3. 一覧から、配置・統廃合・統治構造の判断に必要なファイルだけを選んで Read する (一覧の全ファイルを読む必要はない)
4. structure.md の判断規則に照らして構造提案を抽出する。移動・統合の提案は、移動先・統合先の実態を Read で確認したものだけを出す — 確認していない提案は出さない。提案が無ければ空配列でよい
5. 以下の JSON schema に厳密に従い、結果のみを 1 つの JSON object として返す (前後にテキスト不要)

{
  "type": "structure",
  "structural_proposals": [
    {
      "scope": ["関与する path", "..."],
      "current": "現状配置の要約",
      "proposed": "提案配置",
      "impact": "high|medium|low",
      "migration_cost": "high|medium|low",
      "rationale": "適用した判断規則",
      "evidence": "確認した実態 (path 引用)",
      "migration_outline": "移行手順の概略",
      "applied_refs": ["references/structure.md#section", ...]
    }
  ]
}
```

Edit / Write は使わない。Read のみで分析を完結させる。

`<PATH_*>` と `<INVENTORY>` (一覧の全件) は起動時に埋める。

**この SKILL.md をロードした時点で `${CLAUDE_SKILL_DIR}` は絶対 path へ置換済み**なので、どちらの prompt も本文に見えている path をそのまま写す — 変数表記を復元して渡すと subagent 側では展開されず、reference を 1 本も読まないまま「reference なしのレビュー」が黙って成立する。送信前に prompt へ `${` が残っていないことを確かめる。

### 5. 指摘を反映して終える

findings のうち採用したものを対象ファイルへ反映し、**反映後に diff を提示する** (呼び出し元が人間でも skill でも、反映を人間の目に載せる唯一の面がここになる)。採用しなかったものは理由付きで報告する。`structural_proposals` は findings へ混ぜず別立てで報告する (severity と impact / migration_cost は別の評価軸で、採否の目安は `references/structure.md` の「impact / migration_cost の 2 軸」)。**検証は 1 ラウンドで終える** — 反映後に subagent を起動し直す再検証ループは回さない。

## 根拠

- 出典一覧 (`sources.md`) は `claude-config-review` の `references/` が 1 正本。本 skill は複製せず参照する
- 分割軸の決定理由は [ADR 0050](../../../docs/adr/0050-harness-skill-inferential-computational.md)
