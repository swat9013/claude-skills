---
name: skill-usage-audit
disable-model-invocation: true
argument-hint: "<skill-name> [transcript数]"
description: 指定 skill の実呼出 transcript を一次証拠に、SKILL.md 記述と実挙動の乖離および工程の消費構造 (時間 / token) を監査し、記述起因の欠陥だけを最小差分で改善して最適化候補を報告する監査ループ。
---

# skill-usage-audit

指定 skill の「書かれた仕様」と「実行時の挙動」の乖離を、実呼出 transcript を一次証拠として検証する監査ループ。逸脱の原因を分類し、**skill 記述起因のものだけ**を SKILL.md の改善として反映する。

乖離と並んで**工程の消費構造** (時間 / token / subagent 数) も測り、仕様どおりだが高コストな工程を最適化候補として報告する。

## args

`/skill-usage-audit <skill-name> [n]` — n は監査する transcript 数の上限 (省略時 3)。

## 手順

### 1. 正本の読込と監査基準の抽出

**対象は skill を定義している repo に限る** — 手順 5 で SKILL.md を編集するので、編集権のある repo でなければ監査が完結しない。既定は cwd repo。cwd が対象 skill を持たない場合は、その skill の定義元 repo を user に確認してから進める (install 済み plugin の cache ディレクトリは対象にしない — そこへの編集は上流に還らない)。以降、その repo root を `<repo-root>` と書く。

args に plugin prefix (`swat-skills:` 等) が付いていれば除去し、**配布 skill と repo-local skill の両方**を glob で解決する — `skills/*/<skill-name>/SKILL.md` と `.claude/skills/<skill-name>/SKILL.md` (後者は plugin 配布外の project-local 配置)。解決した repo 相対 path を以降 `<skill-path>` として手順 4 の版差分コマンドにもそのまま渡す (category を決め打ちで組み立て直さない — 実在しない path を `git log` に渡すと空ログになり、版差分確認が silent に skip される)。見つからない場合と、以下のいずれかに該当する場合は、監査対象外である旨を報告して終了する:

- **vendor 配置の skill** (上流 repo から取り込んだもの。上流 diff 最小化のため SKILL.md を編集しない)。swat-skills では `skills/third/` 配下
- **SKILL.md 単独編集が片肺更新になる skill** — 「正本 → SKILL.md へ蒸留」のような 2 段更新規約を持つもの。swat-skills では `principle-index` が該当し (一覧は leaf の frontmatter からの生成物)、直すのは leaf 側

解決した SKILL.md を Read し、以下を checklist 化して user に提示する (提示は報告のみで、承認待ちはしない。完了条件 1 はこの提示を指す):

- **目的**: skill が何を成果物とするか
- **成功条件**: 完了条件・報告要件 (明示されていなければ手順の終端から導出)
- **制約条件**: 実行順序・除外規則・禁止事項・責務境界 (「やらないこと」)

この checklist が手順 3 の監査基準になる。曖昧で checklist 化できない項目は、それ自体を「記述起因の欠陥候補」として記録する。

### 2. 実呼出 transcript の特定

`mcp__plugin_swat-skills_transcript-ops__find_invocations` を `skill` (対象 skill 名) と `limit`(= n) で呼ぶ。**自前で grep しない** — マーカーの完全一致・queue-operation の二重記録・wrapper transcript・built-in slash の echo はすべて tool 側が record 構造で処理済みで、行 grep で再現すると必ずどれかを踏む。

- 返り値の `path` に slice JSON が出るので Read する (**本体は返らない**)
- `meta.matched_files` が 0 なら「実呼出なし・監査不能」を報告フォーマットで報告して終了する
- `meta.emitted_files` が n 未満ならある分だけで進め、その旨を報告に明記する
- **`meta.total_invocations` が呼出回数、`matched_files` は file 数**。両者を混同しない
- `transcripts[]` は読む順に並ぶ (scope ごとの最新 1 件を先取り → 残りを mtime 降順)。環境差 (GitHub/GitLab、tracker 等) を確保するための順序なので、上から使う
- `meta.excluded` は「マーカーは在るが実呼出しではない」hit の内訳。**呼出回数には入らない**。ここが大きいときは監査対象 skill が自分の transcript に頻繁に引用されている (SKILL.md 本文の提示等) というだけで、逸脱の証拠ではない
- 各 transcript の `invocations[]` (呼出ごとの `timestamp` / `uuid` / `channel` (`skill_tool` / `command`) / `args` / `is_sidechain`) は、手順 3 の subagent に渡す anchor になる。`is_sidechain: true` は subagent 側からの呼出し

### 3. 各 transcript の監査 (subagent 並列委譲)

transcript 1 件 = subagent 1 体で、`subagent_type: swat-skills:transcript-auditor` を同じ message で並列に起動する。`name` は付けず、Agent tool の `model` / `effort` 引数も渡さない — 報告は各 subagent の最終メッセージで届き、深さは agent 定義が固定している。prompt に渡すのは次の 4 つだけにする:

- 対象 transcript の**絶対 path**
- 手順 2 で得たその transcript の `invocations[]` (呼出の anchor)
- 手順 1 の監査基準 checklist
- 対象 skill の手順の一覧 — 手順 1 で Read した SKILL.md の手順を番号と見出し (見出しの無い手順は先頭文) で並べたもの。監査 subagent はこれを工程の単位にするので、全 subagent に同じものを渡す (transcript ごとの消費を同じ区切りで突き合わせるため)

報告の項目・定量値の拾い方・transcript の読み方は agent 定義が持つので、prompt に書き写さない (写すと 2 箇所の指示が食い違ったときにどちらが効くか決まらない)。

`swat-skills:transcript-auditor` の名前が解決しないときは、`general-purpose` で代わりに起こさない — 代わりに起こすと model・effort・tools が呼出元の値へ黙って戻り、指名した深さで監査したように見える。手順 4 以降はすべて監査 subagent の報告に依存するので、次の 3 つを返して終了する: 停止理由 (Agent tool の error が挙げた available agents)・手順 1 の checklist と手順 2 の slice の path・次の手順 (swat-skills plugin の agent が読み込まれる状態にしてから再実行する)。

### 4. 総合判定

手順 3 で起動した全 subagent の終了 (完了またはエラー) が届いてから判定に入る。報告を返さずに終わった subagent (エラーで終わった・項目が欠けた・呼出が見つからず監査不能と返した) の transcript は、「報告なし」か欠けた項目を明記した上で残りで判定する — 欠落を伏せると、その transcript の逸脱が無かったことになる。

全報告を突合し、逸脱ごとに原因を 3 分類する:

| 分類 | 例 | 扱い |
|---|---|---|
| (a) skill 記述起因 | 手順の曖昧さ・落とし穴が手順に書かれていない・template のバグ | **改善対象** |
| (b) executor の不遵守 | 警告を読んだ上で禁止パターンを実行 | 記述強化 (警告文 → NG/OK code block 化等) で抑止できる場合のみ改善対象 |
| (c) 外的要因 | user の中断・pivot、同一セッション内の別 skill の所作 | 逸脱に計上しない。是非の評価も書かず、判定は対象 skill の挙動に閉じる |

判定前に**版差分を確認する** (監査セッションの cwd は任意なので `-C` で repo を明示する):

```bash
git -C <repo-root> log --format='%h %ci %s' -- <skill-path>
```

各 transcript の実行日時 (監査 subagent の報告にある呼出時の実行日時) と突合して実行時点の版を特定し、旧版準拠の挙動を現行仕様違反と誤判定しない。旧版本文が要るときは `git -C ... show <hash>:<path>` で取得する。log が空 (未 commit の新設 skill 等) なら版差分確認は skip し、現行版のみで評価する。現行版で是正済みの逸脱は「是正の実証」として記録するに留める。

#### 最適化候補

逸脱の 3 分類とは別軸で、**仕様どおりだが高コスト**な工程を挙げる。監査 subagent の報告にある工程ごとの消費と手戻りを突合し、次の形に当たる工程を候補にする:

- 指摘数・対象数に比例して subagent が増える委譲
- 差分に絞れる工程での全量再走査
- 目的に対して過大な effort / model / レビュー回数
- 最も遅い 1 体の待ち合わせが全体の所要を決める並列

候補には消費の実測値 (所要 / subagent 数 / token 概算) を添える — 実測の無い候補は次の改善ループで順位を付けられない。候補が出なかったときは、消費構造のどこを見てそう判断したかを書く。

**最適化候補は手順 5 の改善対象に入れない。** 工程の作り替えは対象 skill の再設計にあたるので、報告に載せて別ループへ送る。

### 5. 改善の適用

- (a) あり、または (b) のうち記述強化で抑止できるものあり → `swat-skills:write-for-harness` を invoke (5-1) → **証拠に紐づく最小差分**で `<repo-root>` の SKILL.md を編集 → 対象 repo の gate 検証。変更の届け方は対象 repo の運用に従う。**成果物は対象 SKILL.md の差分のみ** — executor 側の挙動改善 (rules 化・hook 化) と対象 skill の再設計は、この監査報告を入力とする別ループへ送る
- それ以外 (逸脱ゼロ / (c) のみ / (b) だが記述強化で抑止不能) → SKILL.md を編集せず監査報告のみで終了 (改善しない根拠を明記)

#### 5-1. `write-for-harness` への委譲

SKILL.md 指示文は Inferential harness なので、**文面規範の Read と検証は `swat-skills:write-for-harness` に委ねる** — 本 skill は規範を持たない。invoke の前に `${CLAUDE_SKILL_DIR}/../write-for-harness/references/caller-contract.md` (呼び出し元向けの契約) を Read し、契約が定める事項は契約に従う。

- **args の path は `<repo-root>/<skill-path>` の絶対 path にする** (手順 1 の `<skill-path>` は repo 相対で、監査セッションの cwd は任意)。args なしだと委譲先が差分から対象を自動検出する経路に落ち、まだ編集していない作業ツリーでは差分が空なので「対象なし」で正常終了して規範が一度も読まれない
- **invoke は main agent 自身が行う** — 手順 3 の subagent には渡さない (手順 3 の subagent は transcript 1 件分の証拠しか持たず、採否を縛る手順 4 の総合判定と下記の委譲制約は main agent にしか無い)
- **差分の起草と反映は委譲先が行う** (委譲先は main agent の context で動く)
- 委譲先が反映を完了せずに終わったら、その改善候補を**判定済み・未反映**として報告フォーマットの 4 に残す

invoke の直前に、監査の証拠と次の制約を宣言する (委譲先は監査側の制約を知らない):

- **採るのは監査の証拠に紐づく最小差分だけ。** 委譲先の findings は重要度で絞らず全件出る建て付けなので、監査で観測していない指摘は採らない
- **構造提案 (`structural_proposals`) と、配置を変えるパッチ (外出し・分割) は反映しない。** 配置・統廃合は対象 skill の再設計にあたるので、報告フォーマットの 4 に載せて別ループへ送る

## 報告フォーマット (完了条件)

以下 5 点を user に提示したら完了:

1. 監査基準 (目的 / 成功条件 / 制約) の整理
2. transcript ごとの判定サマリ表 (環境・判定・主要逸脱・消費 (所要 / subagent 数 / token 概算))
3. 逸脱リスト (severity・原因分類つき)
4. 適用した改善の差分と、反映しなかった案 (構造提案と、委譲先が報告に回したパッチ。改善なしの場合はその根拠)
5. 最適化候補 (仕様どおりだが高コストな工程。実測値つき。候補が無ければその判断根拠)

