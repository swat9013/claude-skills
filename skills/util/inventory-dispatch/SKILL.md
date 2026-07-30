---
name: inventory-dispatch
disable-model-invocation: true
argument-hint: "[targets]"
description: herdr (AI agent 向け terminal multiplexer) session 内で、inventory 系 3 skill (inventory-permissions / inventory-claude-md / inventory-skill-mcp) をそれぞれ独立した Claude Code セッション (分割 pane) として並列起動し、レポート完成を監視して要約を user に提示、承認された候補の適用指示を pane へ送る一括棚卸し dispatcher。Use when「棚卸しを一括」「inventory をまとめて実行」「全部まとめて棚卸し」「一括棚卸し」「inventory-dispatch」.
---

# inventory-dispatch

herdr session 内で inventory 系 3 skill (`inventory-permissions` / `inventory-claude-md` / `inventory-skill-mcp`) をそれぞれ独立した Claude Code セッションとして起動する dispatcher。展開先は**この skill を呼び出したセッションが居る herdr workspace の分割 pane** — user は同じ workspace 内で全セッションを見渡し、pane 移動で直接介入できる。各 pane はレポート生成まで自走し、dispatcher はレポート完成を監視 → 要約を AskUserQuestion で user に諮り → 承認された候補の適用指示を pane へ送信 → pane 側が worktree + PR 作成まで進める。

**承認判定は常に人間** (ADR 0011)。dispatcher はレポートの要約と選択肢提示のみを行い、候補の適用判断・pane 内 AskUserQuestion への代答は一切しない (責務境界を参照)。

## args

`/inventory-dispatch [targets]` — targets は対象 subset の指定 (space / comma 区切り、`permissions` / `claude-md` / `skill-mcp`)。省略時は 3 target 全部。

## script 起動規約

決定論部分 (前提検査 / pane 起動 / レポート監視 / テキスト送信 / pane 回収) は 1 script に外出ししてある。生の `herdr` を手で叩かず script を使う (例外は手順 4 の `herdr pane read` のみ)。初期 prompt template・event 分類・submit 方式の規則は script の docstring とテストが正本 — 本書には書かない:

- [`scripts/inventory_herd_ops.py`](scripts/inventory_herd_ops.py) — herdr 操作 (preflight / spawn / watch / send / close)。テスト: repo root の `tests/test_inventory_herd_ops.py`

**起動は必ず「literal な tilde path を command 名にした単文」で行う。1 Bash 呼び出し = 1 script 起動:**

```bash
~/.claude/skills/swat-skills/skills/util/inventory-dispatch/scripts/inventory_herd_ops.py preflight
```

sandbox の `excludedCommands` 照合は command 文字列のテキスト一致であり、登録 entry (tilde 表記) と別形の起動 — `${CLAUDE_SKILL_DIR}` 展開 (絶対 path になる) / 変数間接 / compound command (for loop・`VAR=x` 前置・`&&` 連結) — は照合されず sandbox 内に落ちる。落ちると subprocess の herdr は socket connect deny で即死する (fail-closed なので誤 dispatch には進まないが、dispatch は不能)。issue-dispatch と同一の制約。

## 手順

### 0. 前提確認

```bash
~/.claude/skills/swat-skills/skills/util/inventory-dispatch/scripts/inventory_herd_ops.py preflight
```

`.ok` が false なら `.failed` に応じて user に修正依頼して終了する:

| `.failed` | 案内 |
|---|---|
| `herdr_env` | herdr session 外。herdr session 内でセッションを起動し直してもらう (herdr 以外の terminal multiplexer は非対応) |
| `hook` | `herdr integration install claude` の実行を依頼 (`not installed` / stale 版とも同扱い)。hook は session identity を herdr に報告して pane と Claude session を 1:1 対応させる。無いと agent field が populate されず起動確認が壊れる |
| `socket` | `.claude/settings.local.json` の `sandbox.excludedCommands` に `"herdr:*"` の追加を依頼 (settings の自己編集は拒否されうるため user に依頼)。socket は存在するが sandbox が connect を `PermissionDenied` で遮断している状態 |

preflight は自 pane の残骸 label (`inv-<target>`) を `inv-dispatch` へ自動 rename する — dispatcher 自身の pane が inventory label のままだと当該 target が稼働中と誤判定され、spawn の duplicate 検査が誤爆する。`.self.renamed_from` が非 null なら手順 3 の報告に添える。

### 1. 対象決定

args の subset (省略時 3 target 全部) を会話内台帳の追跡対象にする。台帳は会話内で保持する (状態ファイルは持たない — 真実源は pane label とレポートファイル)。target ごとに状態 {起動待ち → レポート待ち → 適用指示済み → PR 作成済み / 見送り / 死亡} を追う。

### 2. spawn loop

target ごとに 1 コマンドずつ起動する (起動規約どおり単文 — shell の for loop に畳まない):

```bash
~/.claude/skills/swat-skills/skills/util/inventory-dispatch/scripts/inventory_herd_ops.py spawn permissions --cwd /path/to/repo
```

- `--cwd` は棚卸し対象 repo の root を literal で渡す (省略時 cwd)
- `.ok: true` → 台帳を「レポート待ち」にし、`.ts` (spawn 時 epoch) を控える。**watch の `--since` 初期値は全 spawn の `.ts` の最小値** (手順 4.0)
- `.reason: "duplicate"` (exit 1) → 当該 target は**既に稼働中**。エラーではなく `.pane_id` をそのまま監視対象に取り込む (`.ts` も同様に控える)
- `.ok: false` で agent 未検出 → 起動失敗として `.pane_id` を添えて報告し、当該 target は死亡として台帳記録 (再 spawn は user 判断)

### 3. 初回報告

以下 3 点を user に提示してから監視ループ (手順 4) に入る:

1. 起動した pane 一覧 (target / pane_id / 新規・既存稼働の別)
2. 起動失敗した target とその理由
3. 参加方法 — 同じ workspace 内に展開済み。herdr の keybinding (`prefix+...` — bind は user config 依存) で pane 移動 / zoom できる。**各 pane 内の inventory skill は高確度候補を pane 内の AskUserQuestion で提案する — これには user 自身が当該 pane に移動して応答する** (dispatcher は代答しない。dispatcher 側で扱うのはレポート完成後の候補選択のみ)

### 4. 監視ループ

初回報告の後は終了せず、レポート回収と適用指示・pane 回収を回す。会話内台帳の `since` (初期値 = 手順 2 の `.ts` 最小値) を使う。

1. **待機**:

   ```bash
   ~/.claude/skills/swat-skills/skills/util/inventory-dispatch/scripts/inventory_herd_ops.py watch --since 1700000000 --timeout-sec 240
   ```

   この Bash 呼び出しは **tool の timeout を 300000ms 以上に明示指定する** — 指定しないと default 120 秒で watch (既定 240 秒) が途中終了させられ、event が返らない (issue-dispatch の監視ループの待機ステップと同じ罠)。どの event でも `.panes` (現在の追跡 pane) と `.reports` (target ごとの newest report path) が返る — 照合は event に依らず台帳と突合して行う。

2. **event 別処理**:

   - **`report_ready`**: `.reports` の非 null target のうち台帳が「レポート待ち」のものを処理する (同 cycle に複数あれば全部)。レポートファイルを Read → 候補 (低確度 bucket + 高確度候補のセッション内提案結果) を要約し、**AskUserQuestion (multiSelect) で適用する候補を user に選択させる** (「すべて見送り」相当の選択肢を必ず含める)。
     - 承認候補あり → script send で当該 pane に適用指示を送り、台帳を「適用指示済み」へ。指示文は次の template (承認済み候補の列挙部分だけ具体化する):

       ```bash
       ~/.claude/skills/swat-skills/skills/util/inventory-dispatch/scripts/inventory_herd_ops.py send permissions --text '<report path> のレポートのうち、user 承認済みの候補 <番号と対象の列挙> を適用する。repo 規約 (worktree 必須・gate) に従い適用し、PR 作成まで進め、PR URL を報告する。global scope の項目は適用せず人間側操作の手順提示に留める'
       ```

     - 承認ゼロ (すべて見送り) → script close で pane を回収し、台帳を「見送り」へ
     - 処理した target ごとに、台帳の `since` を当該 report の `.mtime` を超える値 (mtime + 1) へ進める — 進め忘れると次の watch が同じレポートで即 report_ready を返し続ける (未処理 target の新規レポートは常に未来の mtime を持つため、進めても取りこぼさない)
   - **`agent_exited` / `pane_gone`**: レポート未出のまま claude 終了 / pane 消失した target は死亡として報告・台帳記録し、script close で残 pane を回収する (再 spawn は user 判断)。適用指示済み pane の消失は下記 3 の PR 確認を先に試す
   - **`timeout`**: 停滞 pane を報告してループ継続。停滞は「pane 内の高確度候補 AskUserQuestion の承認待ち」「permission 待ち」の可能性が高い — **user に pane 移動での確認を促す** (dispatcher からは介入しない)
   - **`no_panes`**: 追跡 pane が消滅。台帳に未終了 target が残っていれば死亡として記録する

3. **PR 確認** (適用指示済み pane がある cycle で毎回):

   ```bash
   herdr pane read <pane_id> --source recent --lines 40
   ```

   出力 tail に PR URL (`https://github.com/.../pull/<番号>`) が現れたら完了 — script close で pane を回収し、台帳を「PR 作成済み」(URL 記録) へ。

4. **終了判定**: 全 target が {PR 作成済み / 見送り / 死亡} → 手順 5 の最終報告へ。それ以外は 1 へ戻る。

### 5. 最終報告

以下を提示して終了する:

1. PR URL 一覧 (target 別)
2. 見送りにした候補 (レポート path を添える — レポートは `/tmp` に残るため後から人間が適用できる)
3. 死亡・停滞のまま残った target / pane (再実行は `/inventory-dispatch <target>` で個別に可能)
4. close した pane 一覧

## 責務境界

dispatch → 監視 → 承認仲介 → 全回収で止まる。以下を**構造的に守る**:

- **承認判定は常に人間** (ADR 0011)。dispatcher が行うのはレポートの要約と AskUserQuestion での選択肢提示のみで、候補の採否を dispatcher 自身が判断しない
- **pane 内の AskUserQuestion へ send-text で代答することを禁止する**。script send を使ってよいのは「レポート完成後、dispatcher 側 AskUserQuestion で user 承認を得た適用指示」の 1 種のみ。pane が承認待ち・permission 待ちで停滞していたら user に pane 移動を案内する (代わりに答えない)
- 無人 commit なし — commit / PR 作成は各 pane 側が repo 規約 (worktree 必須・gate) の下で行う
- inventory 各 skill の手順の中身 (観測 script / bucket 分類 / 高確度候補の扱い) は各 pane の領分 — dispatcher は関与しない
- global scope 候補の適用は行わない (適用指示 template で pane 側にも「人間側操作の手順提示に留める」と明示する)

## 罠

| 症状 | 原因 | 対応 |
|---|---|---|
| script が `herdr ... PermissionDenied` を返す (settings 反映済みなのに) | 起動形が `excludedCommands` の tilde entry とテキスト一致しない: `${CLAUDE_SKILL_DIR}` 展開・絶対 path・変数間接・for loop 等の compound command 経由 | 起動規約どおり literal tilde path の単文で起動し直す |
| watch が event を返さず Bash が中断される | Bash tool の default timeout (120 秒) が watch の既定 240 秒より短い | watch の Bash 呼び出しに timeout 300000ms 以上を明示する |
| 同じレポートで report_ready が即時に返り続ける | 処理済みレポートの mtime が `--since` 以上のまま | 処理のたびに台帳の `since` を report `.mtime` + 1 へ進める (script は stateless — 既読管理は台帳の責務) |
| pane が停滞して watch が timeout を繰り返す | pane 内の高確度候補 AskUserQuestion の承認待ち / permission 待ち | user に pane 移動での応答を促す。**send-text での代答はしない** (人間承認の迂回になる) |
| spawn が `.reason: "duplicate"` を返す | 同 target の pane が既に稼働中 (前回実行の残り等) | エラーではない — 返却された `.pane_id` を監視対象に取り込む。preflight を飛ばすと自 pane の残骸 label でも誤爆する |
| 3 pane が同一 repo に並列で apply して衝突しそうに見える | 同一 repo への並列書き込みの懸念 | 衝突しない — 各 inventory skill の適用は repo 規約により pane ごとに独立の worktree を切って行われる (main working tree は直接編集されない) |
| spawn が `.ok: false` (agent が null のまま) | poll (2 秒 × 5 回) 内に screen manifest 未検出 / pane の shell が command line で exit | `herdr pane read <pane_id> --source recent --lines 40` で pane 内を調査。再 spawn は user 判断 |

## 参照

- 各 inventory skill の手順正本: `skills/steering/inventory-permissions/SKILL.md` / `skills/steering/inventory-claude-md/SKILL.md` / `skills/steering/inventory-skill-mcp/SKILL.md`
- steering 運用正本 (統治対象マップ / ADR 0011 の 3 層分離): `docs/steering.md`
- 先行実装 (構成・script 設計の参照元): `skills/util/issue-dispatch/SKILL.md`
