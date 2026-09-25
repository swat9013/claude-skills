---
name: ui-design
user-invocable: true
argument-hint: "[設計名または設計したい画面群の説明]"
description: >-
  UI 設計ドキュメント一式を 画面インベントリ → 画面遷移 → 領域/レイアウト →
  component 分解 → デザイントークン の順の対話的セッションで作成・更新する。
  実装とその検査は対象外。
  Use when「UI 設計 (画面設計) のドキュメントを作る・更新する」「画面遷移を設計する」
  「component を洗い出す」「デザイントークンを決める」.
---

# ui-design

設計対象について、画面インベントリ (何の画面があるか) → 画面遷移 (どう移るか) → 領域/レイアウト (画面の中の構造) → component 分解 + state 網羅 (再利用の単位) → デザイントークン (見た目の規範値) の順に質問して決定を固め、UI 設計ドキュメント一式を書く。順序が漏れを炙り出す: 画面を決めずに切った component はどの画面にも現れず、画面の要求を見ずに決めたトークンは実装で上書きされる。

成果物の書式はすべて [references/artifacts.md](references/artifacts.md)、検証の手順は [references/verification.md](references/verification.md) が持つ。書く段階になったら該当ファイルを Read する。

## 1. 置き場を決める

- 既定は `docs/design/<name>/`。対象リポジトリに設計ドキュメントの置き場が既にあるならそちらに従う
- `<name>` は設計対象の名前 (kebab-case)。既存ディレクトリと重複したら、それが同じ対象の旧設計か別対象かをユーザーに確かめる — 同じ対象なら「5. 既存設計を更新する」の側
- 同じ対象の `sud-design` 成果物 (`usecases.md` / `domain.puml`) が既にあれば Read して入力にする。**無くても進める** — ユースケースは本 skill が画面インベントリの段で聞き出す

## 2. 設計セッションを進める

各段は、下記の確定項目が埋まるまで質問を続けてから次の段へ進む。質問はまとめて出し、ユーザーの回答が要らない事実 (既存コード・既存 doc・stack の実態) は自分で調べて埋める。決定するのはユーザー、事実を集めるのは自分。

**画面インベントリ** — 確定項目:

- 画面の一覧と、画面ごとの目的 (誰が何をしに来るか)。`usecases.md` があるならユースケースと画面の対応
- 画面ごとに扱う情報要素 (表示するもの・入力するもの)
- 画面に見えて画面でないもの (modal / drawer / toast) の区別。これらは遷移の宛先になるかで決まる

**画面遷移** — 確定項目:

- 遷移の起点・宛先・きっかけ (操作名)。きっかけの無い自動遷移はその条件
- 入口 (初期画面) と出口。認証・権限で入口が分岐するならその分岐
- 遷移しない組合せのうち、意図して未定義にしたもの (理由つき)

**領域/レイアウト** — 確定項目:

- 画面ごとの領域構成 (header / nav / main / aside 等) と、領域をまたいで共有される領域
- breakpoint ごとの領域の畳み方 (どの領域が消えるか・積み替わるか)

**component 分解 + state 網羅** — 確定項目:

- component の一覧と、どの画面のどの領域に現れるか。**どの画面からも参照されない component は理由を確かめる** (根拠が無ければ落とす)
- component ごとの必須 state (下表)。表に無い component は必要な state を列挙する
- component が受け取る情報 (props 相当) の概要。実装の型は書かない

| component | 必須 state |
|-----------|-----------|
| Button | default / hover / focus-visible / active / disabled / loading |
| Input | default / hover / focus / error / disabled / read-only |
| Link | default / hover / focus-visible / visited |
| Checkbox / Radio | default / hover / focus-visible / checked / indeterminate / disabled |
| Modal | closed / opening / open / closing |
| Alert | info / success / warning / error |

**デザイントークン** — 確定項目:

- 3 層 (primitive / semantic / component) のうち、primitive と semantic の確定値
- palette / type scale / spacing / radius の選定と**その理由** (画面インベントリで出た要求に紐づける)
- 対象 stack で実装する形式 (CSS custom properties / Tailwind `@theme` / shadcn の semantic tokens)

**既定値** — ユーザーが名指ししない限りこれを提案し、確定した理由を `tokens.md` に書く:

| 項目 | 既定 | 備考 |
|---|---|---|
| palette | Radix Colors | hue は画面の目的から選ぶ (calm / trustworthy → Blue、warm → Amber、fresh → Green、bold → Red)。迷ったら Blue |
| type scale | 1.25 (Major Third) | 見出しが 4 階層以上なら 1.20、hero を強く見せるなら 1.333 |
| base font | 16px | 下げない (WCAG の可読性) |
| font stack | System font | 外部フォント load 無しで FOUT を回避できる |
| spacing | 8pt grid | `references/scale-templates/spacing-8pt.css` |
| icon | Heroicons (vanilla) / Lucide (React 系) | |
| semantic token | shadcn の 8 種 (`--background` / `--foreground` / `--primary` / `--muted` / `--accent` / `--destructive` / `--border` / `--ring`) | 足りなければ足す。減らさない |

実装形式は対象リポジトリの stack から決める — `package.json` に `tailwindcss` v4 があれば `@theme`、`components.json` (shadcn) があればその semantic token を流用、いずれも無ければ CSS custom properties。

**既定から外れるとき** (ユーザーが Material 3 / Open Props / 別 icon set / 用途別 font stack を名指し、依頼が特定デザイン言語に寄る等) のみ [references/presets.md](references/presets.md) を Read し、URL / License / install 手順をそこから引く。既定のままなら Read 不要。骨格の CSS は `references/scale-templates/` にある。

README / screens.md / navigation.puml / components.md / tokens.md の 5 成果物は既定で全部作る。設計の規模に対して不要な成果物は省いてよいが、**何を省きなぜ不要かを README に 1 行残す** (省略の判断も設計判断として読める形にする)。

## 3. 書く・検証する・描く

1. [references/artifacts.md](references/artifacts.md) を Read し、確定した内容を各成果物へ書く
2. [references/verification.md](references/verification.md) を Read し、検証する:
   - 操作シナリオ 2〜3 本 (必須)。表現できない点が出たらモデル側を直す
   - 成果物間の相互参照を検査する:

     ```bash
     uv run ${CLAUDE_SKILL_DIR}/scripts/check-ui-design.py docs/design/<name>/
     ```

   - 画面遷移の網羅を検査する (`navigation.puml` を持つ設計のみ)。exit 0 になるまで、遷移の追加か理由つき uncovered 宣言で埋める:

     ```bash
     uv run ${CLAUDE_SKILL_DIR}/../sud-design/scripts/check-statechart.py docs/design/<name>/navigation.puml
     ```

3. `.puml` を描画する。対象リポジトリに PlantUML の描画 gate (再描画コマンド) があればその生成側を使い、無ければ:

   ```bash
   uv run ${CLAUDE_SKILL_DIR}/../sud-design/scripts/render-puml.py docs/design/<name>/
   ```

   `.svg` は `.puml` と必ず対で更新する (source だけ直すと README が古い図を貼り続ける)
4. セッション中の決定のうち根拠と却下肢を残す価値があるものを `decision/NNNN-<slug>.md` に記録する (基準と様式は artifacts.md)

## 4. 実装する agent へ届ける

**agent が自動でロードする UI 設計ドキュメントの形式は存在しない** (2026-09 時点。Figma / Storybook / shadcn / DESIGN.md を含む first-party 成果物のいずれも「読ませるのは利用者の仕事」と一次資料が明言する)。したがって届ける経路は明示的に作る:

1. `docs/design/<name>/README.md` を索引として置く (「3.」で作る)
2. 対象リポジトリの `CLAUDE.md` / `AGENTS.md` へ**索引 1 行の追記を提案する**。文面は例えば「UI 実装時は `docs/design/<name>/README.md` を先に読む」。**提案までで、追記するかはユーザーが決める** — 書かなくても本 skill の成果物は成立する

## 5. 既存設計を更新する

設計済みの対象に構造変更 (画面の増減・遷移・component・state・トークンの変更) が入るときは、**設計ドキュメントを先に直してから実装する**。実装が先に着地すると、設計 doc は実装の後追い要約に落ちて正本でなくなる。

- どの変更がどの file の正本かは README の索引表で引く
- `navigation.puml` を直したら再描画と網羅検査を回す (手順は「3.」と同じ)
- 過去の決定を覆すときは decision/ の旧 file を書き換えず、新しい決定 file を起こして旧 file を Superseded にする

## 何をしない skill か

- **実装しない**: 設計ドキュメントの完成が終点。実装は呼び出し元のフローに委ねる
- **実装物を検査しない**: 実装済み HTML/CSS への static 検査と 51 項目 self-review は `frontend-review` skill が持つ
- **描画の検証をしない**: HTML/CSS の syntax verify や browser 表示検証は呼び出し元のフローに委ねる
- **ドメインの構造を扱わない**: 境界・ユースケース・entity は `sud-design` skill の対象。本 skill は画面から先
- **リポジトリ全体の ADR を起票しない**: decision/ からの昇格基準 (artifacts.md) に当たる決定は、対象リポジトリの ADR 運用に従うようユーザーに提案する
