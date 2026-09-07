---
name: dispatch-setup
disable-model-invocation: true
description: dispatch v1 (`dispatch-ops`) を新しい project で使えるようにする初期設定ステップ。前提を機械検査して不足を逐語で報告し、置き場の宣言 config を生成する。settings の書き込みは行わず /apply-swat-settings へ渡す。v2 の宣言 config は `skills/util/orchestrator/README.md` の手順で手で置く。
---

# dispatch-setup

dispatch 機構の**導入 1 回分**を通す skill。前提を機械検査 (doctor) して、足りないものを不足項目つきで報告し、宣言 config だけをその場で生成する。

**直すのは宣言 config だけ。** settings は `/apply-swat-settings` が、herdr / uv / tracker CLI の install は人間が担当する — writer を 2 つにしない。

前提の説明そのもの (なぜ要るか・不成立時に何が起きるか) は [`../orchestrator/README.md`](../orchestrator/README.md) が正本。本書は手順だけを持つ。

観測と生成は dispatch-ops MCP server の tool (`mcp__plugin_swat-skills_dispatch-ops__<tool>`) で行う。tool が一覧に無ければ ToolSearch で schema を取ってから使う (`select:project_doctor,project_setup` のように名指しする)。

## 手順

```
1. project_doctor          前提を一括検査 (1 つ落ちても後続は走る)
2. 報告                    status ごとに不足を逐語で出す
3. 宣言 config             missing なら候補を提示 → 人間の承認 → project_setup
4. 残りの不足              settings は /apply-swat-settings、それ以外は人間の作業として渡す
5. 再検査                  project_doctor をもう一度 → 残った不足だけを報告して終わる
```

### 1. project_doctor

引数なしで呼ぶ。**検査はこの tool だけで行う** — 検査を server の中に置いてあるのは sandbox を通らないためで、Bash 経由 (script 化・subprocess) では `herdr status` と `gh auth status` が settings とは無関係に失敗し、不足の切り分けが壊れる。

返り値の `checks[]` が検査 1 件ずつで、`status` は 3 値:

| status | 意味 | 扱い |
|---|---|---|
| `ok` | 宣言が在るところまで検査して成立 | 報告に件数だけ。置き場が正しいかは手順 3 の `observe_issues` の url で確かめる |
| `missing` | 検査して不成立 | `items` を**逐語で**出す。丸めない |
| `unknown` | 検査できなかった | 不足として報告しない。「何が読めなかったか」を `detail` から伝える |

**`unknown` を「不足」に読み替えない。** 既に足りている設定を人間に編集させる方向へ誘導する。

### 2. 報告

`missing` を上に、`visibility` が `silent` のものを最優先で並べる。silent = 不成立でもエラーが出ず、**誤った置き場を黙って観測し続ける**種類 (宣言 config / plugin 名)。

各行に `detail` (何が観測されたか) と `remedy` (誰が何をするか) を添える。`items` はそのまま写せる形 (settings なら entry 文字列) なので、加工せず引用する。

### 3. 宣言 config の生成

`project_config` が `missing` のときだけ行う。

1. 候補を導出する — `git remote -v` の URL から `owner/name` を、host から tracker (`github.com` → `gh` / gitlab → `glab`) を読む
2. **人間に確認する**。issue 置き場は cwd の repo とは限らない (関連 repo の issue で回す project・issue は Jira / PR は GitLab の構成がある)。確認するのは 3 点だけ:
   - issue 置き場 (tracker + 識別子)
   - PR 置き場が issue 置き場と違うか (違うときだけ `pr_tracker` / `pr_repo` を渡す)
   - **候補プール (AFK-ready) を表す triage label の綴り** — その環境の triage 体系から人間に答えてもらう。**既定の `ready-for-agent` で通すのも回答**で、違う綴りなら `issue_ready_label` に渡す。この 1 つだけは server が未宣言時の既定を持たないので、宣言しないと候補プールの観測が止まる。**issue 置き場が `jira` なら聞かない** (Jira の AFK-ready は status で表すので、この宣言を読む経路が無い)
3. **置き場が違うと答えたときだけ 4 点目を確認する** — merge 後に orchestrator が issue を閉じてよいか (`issue_close_on_merge`)。**closing reference は置き場をまたげない**ので、この構成では closes PR が merged になっても issue は open のまま残る。issue 置き場が `jira` なら遷移先 status 名 (`issue_done_status`) も要る。置き場が同じ project では tracker が既に閉じるので聞かない
4. `project_setup(issue_tracker=…, issue_repo=…)` を呼ぶ。既存 config があれば失敗するので、置き直すと決めたときだけ `overwrite: true` を足す
5. **issue 置き場が `gh` なら claim label と AFK-ready label が置き場に実在するかを確かめる** (`gh label list -R <issue.repo>` の 1 回で両方見る)。GitHub は存在しない label を `--add-label` で撃つと失敗するので、claim label が無いまま dispatch すると**全候補の claim が撃つたびに落ち**、AFK-ready label が無いと**候補が 1 件も返らないまま dispatch が止まる**。無いほうを人間の承認のもとで作る — `gh label create "<claim label>" -R <issue.repo> --description "dispatch 機構の AI が着手中"` / `gh label create "<AFK-ready label>" -R <issue.repo> --description "AFK agent が着手してよい"`。綴りは `observe_project` の `issue.claim_label` / `issue.ready_label`。**GitLab は `--label` で存在しない label を暗黙に作るので確認は要らず、Jira の label は自由記述なので同じく要らない**

**識別子を推測で埋めない。** 綴りの誤りは server では検出できず、実在する別 repo を指していると誤った置き場を観測し続ける (最も高くつく誤り)。

生成後、`observe_issues` を 1 度呼んで `issues[].url` が意図した置き場かを目視する。**この確認は宣言を server の既定値へ反映した後でないと意味が無い** — 宣言は server のプロセス内 cache に載るので、反映には server の再起動 (`/mcp` の reconnect) が要る。再起動前なら「置いた」ことだけを報告し、確認は再起動後に回す。

### 4. 残りの不足を渡す

| 検査 | 渡し先 |
|---|---|
| `settings` | `/apply-swat-settings` を適用先 project で起動するよう伝える。**この skill は settings を書かない** |
| `herdr_daemon` / `herdr_integration` / `uv` / `tracker_cli` | 人間の作業。`remedy` のコマンドをそのまま渡す |
| `messaging` / `herdr_session` | 起動し直しが要る。現行 binary で**新規起動**した Claude Code を herdr session 内で立てるよう伝える |
| `plugin_name` | publisher 側の宣言の問題。install 側では直せないので報告に留める。doctor が見るのは配布物の宣言 (`.claude-plugin/plugin.json`) までで、harness が実際に登録した名前は `/plugin list` で確かめるよう案内する |

### 5. 再検査

`project_setup` を呼んだなら `project_doctor` をもう一度回す (config は直読みなので再起動前でも最新が出る)。残った不足だけを並べて終了する。**全 green まで粘らない** — 人間の作業が残るのは正常で、この skill の成果物は「次に誰が何をするか」が確定した一覧。
