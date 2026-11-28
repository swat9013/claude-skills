# CLAUDE.md のレビュー観点

## いつこの doc を Read するか

`CLAUDE.md` (global / project / project-local) を新設・編集する前に Read する。1 回限りの指示は対象外。本文を rules に書く条件 (下の「載せる基準と段階開示」) を満たす細則は [rules](./rules.md) 側へ。

## 責務

CLAUDE.md は Claude Code がセッション開始時に常時読み込む指示ファイル。

- **目的**: 触る file も作業の種類も問わず判断を変える規則 (不変の規範 / プロジェクト固有制約 / よく忘れる注意点) と、場面ごとの文書へ導く索引行を、毎セッション自動的に注入する。
- **対象外**: 1 回限りの指示 (それは prompt で十分)、自動実行 (それは hook の責務 → [hook](../../../claude-config-reference/references/hook.md))、特定タスク手順 (それは skill の責務 → [skill](./skill.md))、特定ファイル編集時のみ必要な細則 (それは rules の責務 → [rules](./rules.md))、機械的に検査可能な制約 (それは linter / settings の責務 → [settings](../../../claude-config-reference/references/settings.md))。

## 仕様

### memory 階層

Claude Code の memory は最大 4 層が階層的にロードされる。

| 層 | パス | 用途 |
|---|---|---|
| enterprise | プラットフォーム依存の管理パス | 組織横断ポリシー |
| user (global) | `~/.claude/CLAUDE.md` | ユーザー個人のグローバル規範 |
| project | `<repo>/CLAUDE.md` (cd 下のサブディレクトリ CLAUDE.md も含む) | リポジトリ共有規範 |
| project-local | `<repo>/CLAUDE.local.md` | VCS 管理外の個人 override |

### import 構文

本文中の `@path/to/file` は該当ファイルをロード時に展開する公式手段。編集単位をファイルで分けたいときに使う。

```
@.claude/rules/coding-style.md
```

**import は分割であってロード量の削減ではない**。展開結果は毎セッション常時ロードされるので、コンテキストを節約する目的 (= 段階開示) の手段には選べない → 後述「載せる基準と段階開示」。発火条件付きで細則をロードしたい場合は import ではなく rules ファイル (frontmatter `paths:`) を使う → [rules](./rules.md)。

### ロードコストと長さ

- markdown 形式。frontmatter は不要 (rules と異なる)。
- セッション開始時に全行がコンテキストにロードされるため、量がそのままコスト・ノイズに直結する → [models](../models.md)。
- **重要規範ほど先頭側に置く**。具体: セッション開始直後に判断する規範 (言語選定 / asking policy 等) は最初の 2 セクション以内に配置。逆例: `Revision Policy` のようなメタ規約は末尾に置く。モデルの先頭バイアス特性は [models](../models.md)。

### 載せる基準と段階開示

常時ロード枠は有限で、1 行足すたびに他の規範の信号が薄まる。**載せるのは「無いと判断が変わるもの」だけに絞る** — 「あると便利」「網羅的で正確」は基準を満たさない。

基準を満たさない知見は本文から外し、**索引行** (どの場面の前にどの文書を Read するかを書いた 1 行) へ逃がす。常時ロードされるのは索引行だけで、本体は必要としたセッションだけが読む。

逃がし先は「誰がロードを起動するか」で選ぶ。

| 逃がし先 | ロードの起動条件 | 向く内容 |
|---|---|---|
| rules (`paths:`) | file を tool で触ったときに自動 → [rules](./rules.md) | 下の置き場の規則を満たす細則 |
| 文書 + CLAUDE.md の索引行 | model が索引行を読み、作業種別から判断して Read | 作業に着手する前に読めば足りる知見 (全体像 / 構造 / 手順書) |
| skill | trigger 語彙 or 明示呼び出し → [skill](./skill.md) | 反復するタスク手順・チェックリスト |

**置き場は、規範ごとに要る場面と届き方を対にして決める。** 本文は 1 か所に置き、要る場面のそれぞれに、その場面で漏れない届き方を 1 つ以上当てる。

- CLAUDE.md に置くのは、触る file も作業の種類も問わず判断を変える規則と、索引行だけ。残す行ごとに、触る file も作業の種類も問わず効く理由を言えるかをレビューで確かめ、言えない行は外へ出す (理由を CLAUDE.md に書き足すわけではない)
- rules に本文を書くのは、その path の file を tool で触る場面でしか要らず、compaction の後に次に file を触るまで消えていても困らない規範だけ
- 上の 2 つを満たさない規範は文書に置き、要る場面ごとに索引行か、skill の手順が Read させる行を足す。場面別の規範を CLAUDE.md の本文や、`paths:` の無い rules へ戻さない
- 届き方に強さの順は付けない。優劣を測った一次の資料が無く、漏れる条件が場面ごとに違う

届き方ごとの漏れる条件は次の表が正本で、他の file には写さずこの表を指す。host が載せる届き方は、載る契機 (常時 / file を tool で触ったとき) で 2 行に分ける。届き方の種類は増やさない。

| 届き方 | 置き場の例 | 漏れる条件 | compaction の後 |
|---|---|---|---|
| host が載せる (常時) | CLAUDE.md・`paths:` の無い rules | 漏れない。行が増えた分だけ全 session の費用になる | 読み直される |
| host が載せる (file を tool で触ったとき) | `paths:` つきの rules | file を触らない工程 (設計・計画・レビューの議論)。Bash による書き込み | 次に file を触るまで消える |
| hook が注入する | hook の注入文 | event が起きない場面。1 回の上限を超えた分 | 要約に溶ける。compaction の後に発火する hook で入れ直せる |
| path で Read する | 索引行が指す文書・skill の手順が Read させる文書 | model が Read しない。索引行が読む場面を言い当てていない。skill の手順は、その skill を通らない作業に届かない | 読んだ本文は要約される |
| 渡した本文にだけ載る | subagent への委譲の prompt | 渡す側が書かなければ載らない | subagent の context の中で完結する |

表は Claude Code の公式 doc (memory・context window・hooks・sub-agents) の 2026-11-18 時点の記述による。Bash による書き込みで載らないことだけは本 plugin の作者が測った挙動で、公式の記述ではない。

**段階開示の手段にならない記法**: `@path` import (ロード時に展開される) / `paths:` の無い rules (CLAUDE.md 直書きと等価)。どちらもファイルは分かれるがロード量は変わらないので、この目的では選ばない。

### 機械検査の委譲

行数 / 索引一貫性 / 単純重複など、定量化できる制約は linter (CI / pre-commit) に逃がす。CLAUDE.md 本体に「~ 行を超えないこと」と書いても確率的にしか守られず、コストとノイズだけが残る。チェックリストには人間判断が必要な項目のみを残す。

## チェックリスト

CLAUDE.md を新設・編集する前に確認する。**機械検査で済む項目は前節「機械検査の委譲」に従って linter に逃がし、ここには載せない**。

- [ ] **recurring failure の特定**: 新規ルールを追加する前に、対応する反復失敗事例を memory / incident 記録 / PR レビュー履歴のいずれかで 1 件以上特定する。特定できなければ aspirational ルール → 追加しない
- [ ] **責務分離 (global vs project)**: グローバルにプロジェクト固有制約を入れていないか / プロジェクトに環境横断規範を書いていないか
- [ ] **責務分離 (CLAUDE.md vs rules)**: 本文を rules に書く条件 (「載せる基準と段階開示」) を満たす規約は rules に移したか → [rules](./rules.md)。全セッションで必要な汎用規範のみ CLAUDE.md に残す
- [ ] **責務分離 (規範 vs 自動化)**: hook / settings / linter で決定論的に守らせられる項目を「気をつけろ」と書いていないか。書くなら自動化を実装する方が優先 ([hook](../../../claude-config-reference/references/hook.md) / [settings](../../../claude-config-reference/references/settings.md))
- [ ] **既存自動化との重複回避 (逆向きチェック)**: 追加しようとする規範が既に hook (PreToolUse / PostToolUse) / settings の `permissions` / 既存 rules で機械的に defend されていないか `grep -r` で確認したか。探す先はプロジェクトの hook 配置 (plugin なら `hooks/hooks.json` + script ディレクトリ、repo-local なら `.claude/`) と global (`~/.claude/`) の両方。defend 済みなら追加しない — feedforward 重複は確率的な再現と context bloat にしかならない → [models](../models.md)
- [ ] **モデル先頭バイアスへの配置**: 重要規範を先頭側に寄せたか → [models](../models.md)
- [ ] **載せる基準 (無いと判断が変わるか)**: 各項目を 1 つずつ「これが無いとモデルの判断が変わるか」で判定したか。変わらない項目は本文から外し、索引行 + Read 先の doc へ逃がしたか → 前節「載せる基準と段階開示」
- [ ] **届き方と漏れ**: 規範ごとに、要る場面に届き方の表の漏れる条件が当たらないか確かめたか。当たる場面には、その場面で漏れない別の届き方 (索引行・Read させる行・hook の注入など) を当てたか
- [ ] **逃がし先の記法**: 逃がした先が `@path` import / `paths:` の無い rules になっていないか (どちらも常時ロードのままでロード量が減らない)
- [ ] **古い情報の除去**: 既に解決した bug への暫定対処 / 撤去済み機能への参照が残っていないか

## アンチパターン

- **抽象的な aspirational ルール**: 「丁寧に書く」「ベストを尽くす」のような測定不能な指示はモデル挙動を変えない。具体的な行動規範 (「assumption を宣言してから書く」) に置き換える。
- **自動化可能な制約を「気をつけろ」で済ます**: モデルは確率的にしか守らない。決定論的に守らせたいなら hook / linter / settings deny で実装する。CLAUDE.md は「自動化で代替できないもの」専用。
- **プロジェクト固有制約をグローバルに混入**: 別プロジェクトに不要な制約まで毎セッションロードされる。
- **場面別の規約を CLAUDE.md 直書き**: その path の file を触る場面でしか要らない規約まで CLAUDE.md に置くと無関係なセッションで毎回ロードされ、high-signal が薄まる。
- **同一規約の二重管理**: 同種の規約を CLAUDE.md 直下と rules / import 先の両方に書くと、片方しか更新されず drift する。
- **`@path` import を「スリム化」と見なす**: import 先はロード時に展開されるので常時ロード枠は 1 行も減らない。減るのは CLAUDE.md の行数だけ。コンテキストを減らしたいなら索引行 + 必要時 Read か rules (`paths:`) へ移す。
- **網羅的で「正しい」情報の常時ロード**: skill 一覧 / ディレクトリ構造 / 用語集のような、読めば分かる・たまにしか要らない情報を本文に置くと、毎セッション全量ロードされて重要規範を薄める。索引行から Read させる。
- **解決済み問題の残骸**: 過去の bug 回避ルールが永久に残る。定期的な見直しが必要。

## 参照

- 共通: [models](../models.md) (context window とロード量 / 先頭バイアス) / [sources](../../../claude-config-reference/references/sources.md) (公式仕様の引き方)
- 関連: [rules](./rules.md) (CLAUDE.md から分割される細則ファイル) / [hook](../../../claude-config-reference/references/hook.md) (自動化への委譲先) / [settings](../../../claude-config-reference/references/settings.md) (permission / hook 登録)
- 公式: Claude Code memory ドキュメント (URL は [sources](../../../claude-config-reference/references/sources.md) 経由で確認)
