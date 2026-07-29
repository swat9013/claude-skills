---
name: skill-usage-audit
disable-model-invocation: true
argument-hint: "<skill-name> [transcript数]"
description: 指定 skill が実際に呼び出された直近 transcript を特定し、SKILL.md の目的・成功条件・制約と実挙動を突合して逸脱を検証、skill 記述起因の欠陥を worktree + PR で改善する監査ループ。Use when「skill を監査」「skill usage audit」「transcript から skill を検証」「skill が仕様どおり使われているか」「実呼出を振り返って skill を改善」.
---

# skill-usage-audit

指定 skill の「書かれた仕様」と「実行時の挙動」の乖離を、実呼出 transcript を一次証拠として検証する監査ループ。逸脱の原因を分類し、**skill 記述起因のものだけ**を SKILL.md の改善として worktree + PR で反映する。

## args

`/skill-usage-audit <skill-name> [n]` — n は監査する transcript 数の上限 (省略時 3)。

## 手順

### 1. 正本の読込と監査基準の抽出

対象は本 repo (`~/.claude/skills/swat-skills`) の skill に限る。args に plugin prefix (`swat-skills:`) が付いていれば除去し、`skills/*/<skill-name>/SKILL.md` を glob で解決する。見つからない場合と、以下のいずれかに該当する場合は、監査対象外である旨を報告して終了する:

- `skills/third/` 配下 (vendor skill — 上流 diff 最小化のため SKILL.md を編集しない)
- 判断規則集系 knowledge skill (`engineering-judgment` / `coding-principles` / `test-strategy` / `pr-quality`) — 内容の改善は `inventory-project-values` の領分。engineering-judgment には「正本 references → SKILL.md 蒸留」の 2 段更新規約があり、本 skill が SKILL.md だけを編集すると片肺更新になる

解決した SKILL.md を Read し、以下を checklist 化して user に提示する (提示は報告のみで、承認待ちはしない。完了条件 1 はこの提示を指す):

- **目的**: skill が何を成果物とするか
- **成功条件**: 完了条件・報告要件 (明示されていなければ手順の終端から導出)
- **制約条件**: 実行順序・除外規則・禁止事項・責務境界 (「やらないこと」)

この checklist が手順 3 の監査基準になる。曖昧で checklist 化できない項目は、それ自体を「記述起因の欠陥候補」として記録する。

### 2. 実呼出 transcript の特定

`~/.claude/projects/` 配下の**全 project** の `*.jsonl` を **invocation マーカー**で grep する (呼出元 repo は複数ありうる)。skill 名の単純 grep は禁止 — ファイル一覧・言及・SKILL.md 本文の注入を大量に拾う。

```bash
# SKILLNAME だけを対象 skill 名に置換する。<command-name> は transcript 内の literal マーカーなので置換しない。
# 閉じタグ / 閉じ " まで含めた完全一致にする — 類似名 sibling skill (例: foo と foo-bar) の混入を防ぐ
grep -l -E '<command-name>/?([a-z0-9-]+:)?SKILLNAME</command-name>|"skill": ?"([a-z0-9-]+:)?SKILLNAME"' \
  ~/.claude/projects/*/*.jsonl
```

hit 0 件なら「実呼出なし・監査不能」を報告フォーマットで報告して終了する。hit が n 件未満ならある分だけで進め、その旨を報告に明記する。

ヒットした file を mtime 降順に並べ (`grep -l ... | xargs ls -t` 等)、n 件選定する。呼出元 repo が複数あれば各 repo の最新 1 件を先に取って環境差 (GitHub/GitLab、tracker 等) を確保し、残り slot は全体の新しい順で埋める。選定時の注意:

- **hit 記録数をそのまま呼出回数と即断しない**: 同一呼出が queue-operation record と user turn record に二重記録される。「2 回呼出」に見えて実質 1 回が典型
- **wrapper transcript を除外**: 別セッション (corrections 抽出等) が対象セッションを `<transcript-data>` として内包している file は実呼出ではない。生セッションの jsonl を特定し直してそちらを一次証拠にする

### 3. 各 transcript の監査 (subagent 並列委譲)

transcript 1 件 = subagent 1 体で並列に委譲する。subagent は Bash / Read を持つ general-purpose 型を **name 付き**で起動する (name が無いと後述の報告催促が送れない)。プロンプトには対象 transcript の**絶対 path**、手順 1 の監査基準 checklist、以下の報告フォーマットを埋め込む:

1. 呼出時の args・実行日時 (transcript 内 timestamp。無ければ file mtime で代用と明記)・状況
2. 手順ごとの一致 / 逸脱 (逸脱には実行 command・assistant text の引用を証拠として添付)
3. 責務境界の逸脱有無
4. 成功条件 (完了条件) の充足
5. 既知の罠表に無い新しい罠
6. 逸脱リスト (severity: high=結果を壊す / medium=仕様不履行だが結果は妥当 / low=軽微) + SKILL.md 改善提案

解析ヒントも渡す: JSONL の構造 (`{type, message: {content}}`)、大 file は grep でアンカー特定 → `sed -n` で周辺読み、圧縮 transcript では tool_use command 本文が欠落し tool_result からしか挙動を判定できない旨。

subagent が報告本文を送らず idle 化したら、SendMessage で報告提出を要求する (idle 通知は完了報告ではない)。

### 4. 総合判定

全報告を突合し、逸脱ごとに原因を 3 分類する:

| 分類 | 例 | 扱い |
|---|---|---|
| (a) skill 記述起因 | 手順の曖昧さ・罠の未記載・template のバグ | **改善対象** |
| (b) executor の不遵守 | 警告を読んだ上で禁止パターンを実行 | 記述強化 (警告文 → NG/OK code block 化等) で抑止できる場合のみ改善対象 |
| (c) 外的要因 | user の中断・pivot、同一セッション内の別 skill の所作 | 逸脱に計上しない |

判定前に**版差分を確認する** (監査セッションの cwd は任意なので `-C` で repo を明示する):

```bash
git -C ~/.claude/skills/swat-skills log --format='%h %ci %s' -- skills/<category>/<skill-name>/SKILL.md
```

各 transcript の実行日時 (手順 3 報告の項目 1) と突合して実行時点の版を特定し、旧版準拠の挙動を現行仕様違反と誤判定しない。旧版本文が要るときは `git -C ... show <hash>:<path>` で取得する。log が空 (未 commit の新設 skill 等) なら版差分確認は skip し、現行版のみで評価する。現行版で是正済みの逸脱は「是正の実証」として記録するに留める。

### 5. 改善の適用

- (a) あり、または (b) のうち記述強化で抑止できるものあり → worktree を作成し、CLAUDE.md 規約どおり対応 reference を Read してから、**証拠に紐づく最小差分**で SKILL.md を編集 → gate 検証 → commit → PR。監査で観測していない推測ベースの改善を混ぜない
- それ以外 (逸脱ゼロ / (c) のみ / (b) だが記述強化で抑止不能) → worktree を作らず監査報告のみで終了 (改善しない根拠を明記)

## 報告フォーマット (完了条件)

以下 4 点を user に提示したら完了:

1. 監査基準 (目的 / 成功条件 / 制約) の整理
2. transcript ごとの判定サマリ表 (環境・判定・主要逸脱)
3. 逸脱リスト (severity・原因分類つき)
4. 適用した改善と PR URL (改善なしの場合はその根拠)

## 責務境界

監査と SKILL.md の改善までで止まる。以下は別の領分:

- executor 側の挙動改善 (rules 化・hook 化) — steering 系の別ループ
- 対象 skill の再設計・大規模 refactor — 監査報告を入力とする別タスク
- transcript に写った他 skill・user 判断の是非評価

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| 呼出 transcript が大量ヒット | skill 名の単純 grep が言及・ファイル一覧を拾う | invocation マーカー (`<command-name>` / `"skill":`) で grep する (手順 2 のとおり) |
| 類似名 skill の transcript が混入 | 前方一致 grep (例: `grill` 前方一致が `grilling` と `grill-with-docs` の両方にヒット) | `</command-name>` / 閉じ `"` まで含めた完全一致にする |
| 呼出回数が実際より多く見える | queue-operation と user turn の二重記録 | 呼出回数は turn 構造を確認してから確定する |
| 監査対象 file に生 command が無い | wrapper transcript (対象セッションを `<transcript-data>` 内包) or 圧縮形式で tool_use 本文欠落 | 生セッション jsonl を特定し直す。欠落項目は「逐語検証不能」と明記し、tool_result から挙動判定 |
| 現行仕様に照らすと逸脱だらけ | 実行時点の SKILL.md が旧版 | `git log -- <path>` で版を特定し、旧版基準で評価 + 現行是正済みかを併記 |
| 逸脱件数が過大 | user 中断・別 skill の所作を skill の逸脱に計上 | 原因 3 分類 (手順 4) を先に通す |
| subagent の監査結果が届かない | 報告を送らず idle 化 | SendMessage で報告フォーマットを再掲して提出を要求 |
