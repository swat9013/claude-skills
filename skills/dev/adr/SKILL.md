---
name: adr
description: >-
  Architecture Decision Record を docs/adr/NNNN-<slug>.md に追加する。
  MADR 変種テンプレを埋め、番号採番と兄弟 ADR / docs/architecture.md への
  cross-link 更新箇所を提示する。既存 ADR の Superseded / Deprecated 遷移もガイド。
  Use when「ADR を書く」「アーキテクチャ決定を残す」「決定を文書化」「trade-off の選択を記録」「棄却した代替案を残す」「ADR を Superseded にする」「ADR 作成」.
user-invocable: false
---

# adr

Architecture Decision Record (MADR 変種) を `docs/adr/NNNN-<slug>.md` に追加する。**commit/push は責務外** — 完了後は `/contextual-commits` を起動すること。

呼び出し元 LLM がテンプレを埋めて Write する。この skill は **フォーマット仕様と cross-link 更新箇所の checklist** を提供する (script なし、判断と実行は LLM 側)。

## 前提と責務外

**前提**:

- 対象 repo に `docs/adr/` が存在し、既存 ADR は `NNNN-<slug>.md` (4 桁 zero-pad + kebab-case slug) 命名
- 既存 ADR が MADR 変種フォーマットを踏襲している (Header / Context / Decision / Consequences / Alternatives considered / Related の 5 節構成、日本語基調)
- LLM が Context / Decision / Alternatives / Consequences の中身を組み立てられる程度に判断材料を持っている

**責務外**:

- git add / commit / push (→ `/contextual-commits`)
- ADR の内容自体の質評価・grill 対話 (→ `grill-with-docs`)
- 意思決定そのもの (→ `brainstorming` 等の別 skill)
- 番号採番の並列衝突回避の完全自動化 (worktree 並行時は commit 前に再確認、下記手順参照)

## MADR 変種フォーマット (本 repo の慣習)

既存 `docs/adr/0001-*.md` 〜 `0004-*.md` に完全準拠。特に `0003` と `0004` が最新様式。

### Header

```
# ADR NNNN: <topic 1 行>

- Status: Accepted
- Date: YYYY-MM-DD
```

- Status の初期値は `Accepted` (draft 状態は運用外)
- Superseded / Deprecated 遷移時は下記 [処理フロー — Superseded / Deprecated 遷移](#処理フロー--superseded--deprecated-遷移) 参照

### Section 順 (固定)

1. `## Context` — 現状の課題・制約・動機。「なぜこの決定が必要か」を書く
2. `## Decision` — 何を選んだか + 具体構成 (sub-heading 可)。「何を決めたか」を書く
3. `## Consequences` — 決定の帰結。以下 2 つの sub-heading を **必ず両方書く**:
   - `### 良い影響`
   - `### 悪い影響 / 制約` (該当なしなら「特になし」と明示、空欄禁止)
4. `## Alternatives considered` — 検討して棄却した案。**最低 1 案**、各案に**却下理由を必ず添える**
5. `## Related` — 兄弟 ADR / `docs/architecture.md` / 該当 skill / hook / workflow への相対 link
6. `## Follow-up tasks` (optional) — 実装 TODO checklist (実装が伴う ADR のみ、`0002` / `0004` が先例)

### 口調

- 日本語基調 (英語表記は技術用語のみ)
- 断定・簡潔。冗長な導入や修辞は避ける
- 表・code block を積極的に使う

## 処理フロー — 新規 ADR

新しい決定を残す標準フロー。既存 ADR を書き換える (Superseded / Deprecated) 場合は次節参照。

### 1. 番号採番

```bash
ls docs/adr/[0-9][0-9][0-9][0-9]-*.md | tail -1
```

出力例: `docs/adr/0004-todoist-driven-orchestrator.md` → basename の先頭 4 桁 = `0004` → 次番は `0005`。

**worktree 並行時の重複回避**: worktree で複数 branch が同時進行中の場合、他 branch でも同じ次番が採番されうる。commit 直前に **もう一度 `ls`** し、`main` 側で追加されていないか確認する (`git fetch origin main && ls docs/adr/` を推奨)。衝突が判明したら手動リネームで解消。

### 2. slug 決定

- kebab-case、英数と `-` のみ
- 40 字以内、topic を要約 (例: `apm-vendoring-and-categorization`)
- 既存 ADR の slug と重複しないこと

### 3. 本文作成 (テンプレ参照)

[テンプレ本体](#テンプレ本体) をベースに Context / Decision / Consequences / Alternatives considered / Related / (optional) Follow-up tasks を埋める。既存 ADR (特に `0003` / `0004`) を Read し、口調とセクション粒度を揃える。

### 4. Write

```
docs/adr/NNNN-<slug>.md
```

を Write ツールで新規作成。

### 5. Cross-link 更新

[Cross-link 更新 checklist](#cross-link-更新-checklist) を上から順に実行。

### 6. 完了報告

[完了報告フォーマット](#完了報告フォーマット) で変更 file と次のアクションを提示。

## 処理フロー — Superseded / Deprecated 遷移

既存 ADR の決定を覆す / 廃止する場合。

### Superseded (置き換え)

新しい ADR が旧 ADR を置き換える。

1. **新規 ADR を作成** (前節 1〜4)、Header に以下を追記:
   ```
   # ADR NNNN: <new topic>

   - Status: Accepted
   - Date: YYYY-MM-DD
   - Supersedes: ADR MMMM
   ```
2. **旧 ADR (MMMM) の Status を書き換え** (Edit ツール):
   ```
   - Status: Superseded by ADR NNNN
   ```
3. **相互 Related にリンク**:
   - 新 ADR の `## Related` に「ADR MMMM (superseded by this)」を追加
   - 旧 ADR の `## Related` に「ADR NNNN (supersedes this)」を追加
4. **`docs/architecture.md` 本文** で旧 ADR を参照している箇所を新 ADR に更新 (`grep -n "ADR MMMM" docs/architecture.md`)

### Deprecated (廃止のみ、代替なし)

決定を破棄するが代替案がない。

1. **旧 ADR の Status を書き換え**:
   ```
   - Status: Deprecated (YYYY-MM-DD)
   ```
2. **廃止理由を注記** — 旧 ADR の末尾に `## Deprecation note` セクションを追加し、廃止した日付と理由を 1 段落で記述
3. **代替 ADR は書かない** (Superseded との区別)
4. **`docs/architecture.md` 本文** で旧 ADR を参照している箇所を Deprecated 明示 or 削除

## Cross-link 更新 checklist

新規 ADR (または Superseded / Deprecated 遷移) の後、以下を **上から順に確認**。

### 必須

1. **`docs/architecture.md §9 参照` の ADR 一覧** に新規 ADR link を追記
   - 確認: `grep -n "ADR:" docs/architecture.md` (§9 の 1 行、`/` 区切りの末尾に追加)
   - **例外**: 追加しない ADR は無い。全 ADR がここに載る

2. **兄弟 ADR の `## Related` back-link 追加**
   - 新規 ADR が参照した既存 ADR を洗い出す: `grep -oE "ADR [0-9]{4}" docs/adr/NNNN-<slug>.md | sort -u`
   - 該当する既存 ADR ファイルを開き、`## Related` に new ADR への link を追加
   - **確認**: back-link 双方向性は ADR retrieval の要。片方向だけだと後続 session が旧 ADR から新 ADR へ辿れない

### 判断 (該当時のみ)

3. **`docs/architecture.md §4.1 skill 一覧 / §4.2 hook / §4.3 agent` の該当行**
   - 決定が skill / hook / agent を追加・変更するなら該当行の「主な skill」列などに追記
   - 例: 新 skill を追加する ADR → `§4.1` dev カテゴリ行に skill 名を追記

4. **`docs/architecture.md §5 skill mapping (A2 / A4 / A6)`**
   - 新 skill / agent が特定アクションで trigger されるべきなら該当行に追加
   - 判断基準: description trigger で自動起動されうる skill のみ。手動起動前提の skill は除外

5. **`docs/architecture.md §8 更新トリガー`**
   - 「今後この事象が起きたら本書更新」の類の項目が本 ADR の決定で発生・変化するなら追記

6. **skill 件数の期待値更新は不要 (廃止済み)**
   - 補完対象集合・件数の正本は各 SKILL.md frontmatter の invocability であり、CLAUDE.md / CONTRIBUTING.md に件数・skill 名を列挙しない方針 (drift 源にしない)。受け入れ確認は CONTRIBUTING.md「受け入れ確認 (Claude Code 再起動後)」を参照

7. **`.claude-plugin/plugin.json` の `skills` 配列**
   - 新 skill を追加する ADR で必要
   - **手順**: repo に skills 配列の再生成 script があればそれを 1 発実行する (SKILL.md をスキャンして再生成する形が手動編集より安全)。無ければ該当 1 行を追記する

### 対象外 (通常更新不要)

- `CLAUDE.md` 「決定理由は `docs/adr/`」の boilerplate 参照 — 汎用化されているため個別 ADR 追加では触らない
- `README.md` — 現状 ADR への直接参照なし
- `settings/settings.local.json` / `.claude/settings.json` — permission entry は script を持つ skill 追加時のみ (ADR skill 自体は script なし)

## テンプレ本体

`docs/adr/NNNN-<slug>.md` の骨子。**この code block を Write の出発点にする**。日本語基調・断定調・既存 ADR の粒度に揃える。

```markdown
# ADR NNNN: <topic を 1 行で>

- Status: Accepted
- Date: YYYY-MM-DD

## Context

<現状の課題・制約・動機を段落で。以下を含める:>

- 何が問題か (現状の痛みや gap)
- どんな制約下で判断するか (技術・組織・履歴)
- なぜ「今」この決定が必要か

## Decision

**<1 行で決定を宣言>**。

<具体構成を sub-heading + 表 + code block で。以下を含める:>

- 何を選んだか (具体的な構成・技術・パス)
- どう配置・運用するか
- スコープ外にした範囲 (責務分離のため)

## Consequences

### 良い影響

- **<効果 1>**: <説明>
- **<効果 2>**: <説明>

### 悪い影響 / 制約

- **<代償 1>**: <説明>
- **<代償 2>**: <説明>

<悪い影響が無い場合は「特になし (影響範囲が限定的で trade-off は顕在化しない)」と明示>

## Alternatives considered

### <案 1 の名前>

<案の概要 1-2 文>

- 却下理由: <明確な reject 理由>

### <案 2 の名前>

<案の概要 1-2 文>

- 却下理由: <明確な reject 理由>

## Related

- ADR NNNN: <相互参照する既存 ADR title> (<関係の 1 行説明>)
- [`docs/architecture.md`](../architecture.md) §N: <参照する節>
- <関連 skill / hook / workflow への相対 path>

## Follow-up tasks

<optional。実装 TODO があれば checklist で。0002 / 0004 が先例。>

- [ ] <task 1>
- [ ] <task 2>
```

## 失敗時のロールバック

Write 途中や cross-link 更新中の失敗は `git restore` で戻す。`docs/adr/NNNN-<slug>.md` が git 未追跡なら `rm` で削除。

| 失敗ステップ | ロールバック |
|---|---|
| 1-4. 番号採番 / 本文 / Write | 未追跡なら `rm docs/adr/NNNN-<slug>.md`、追跡済みなら `git restore --staged --worktree docs/adr/NNNN-<slug>.md` |
| 5. cross-link (兄弟 ADR back-link) | `git restore docs/adr/<触った既存 ADR>.md` |
| 5. cross-link (`docs/architecture.md`) | `git restore docs/architecture.md` |
| 5. cross-link (`CLAUDE.md`) | `git restore CLAUDE.md` |
| 5. cross-link (`.claude-plugin/plugin.json`) | `git restore .claude-plugin/plugin.json`、または skills 配列を再生成し直す |
| Superseded 遷移 (旧 ADR Status 書き換え) | `git restore docs/adr/<旧 ADR>.md` |

push やネットワーク操作は**絶対に**行わない。

## 完了報告フォーマット

```
✅ ADR NNNN を追加しました: <title>

変更ファイル:
- docs/adr/NNNN-<slug>.md (新規)
- docs/architecture.md (§9 参照リスト / §4.x / §5 / §8 のうち更新した箇所)
- docs/adr/<兄弟 ADR>.md (Related back-link)
- CLAUDE.md (skill 数期待値、該当時のみ)
- .claude-plugin/plugin.json (skill 追加 ADR のみ、regenerate 実行)

Superseded / Deprecated 遷移の場合:
- docs/adr/<旧 ADR>.md (Status → Superseded by / Deprecated)

次のアクション:
  git add docs/adr/NNNN-<slug>.md <他変更ファイル>
  /contextual-commits
```

worktree 並行時は commit 前に `ls docs/adr/[0-9][0-9][0-9][0-9]-*.md` で番号衝突がないか再確認する。
