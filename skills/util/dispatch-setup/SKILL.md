---
name: dispatch-setup
disable-model-invocation: true
description: dispatch 機構 (orchestrator / observer / dispatch-ops) を新しい project で使えるようにする初期設定ステップ。前提を機械検査して不足を逐語で報告し、置き場の宣言 config を生成する。settings の書き込みは行わず /apply-swat-settings へ渡す。Use when「dispatch を導入」「dispatch の初期設定」「dispatch が動くか確認」「置き場の宣言を置く」「dispatch-project.toml を作る」「dispatch の前提を検査」「dispatch doctor」.
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

引数なしで呼ぶ。返り値の `checks[]` が検査 1 件ずつで、`status` は 3 値:

| status | 意味 | 扱い |
|---|---|---|
| `ok` | 検査して成立 | 報告に件数だけ |
| `missing` | 検査して不成立 | `items` を**逐語で**出す。丸めない |
| `unknown` | 検査できなかった | 不足として報告しない。「何が読めなかったか」を `detail` から伝える |

**`unknown` を「不足」に読み替えない。** 既に足りている設定を人間に編集させる方向へ誘導する。

### 2. 報告

`missing` を上に、`visibility` が `silent` のものを最優先で並べる。silent = 不成立でもエラーが出ず、**誤った置き場を黙って観測し続ける**種類 (宣言 config / plugin 名)。

各行に `detail` (何が観測されたか) と `remedy` (誰が何をするか) を添える。`items` はそのまま写せる形 (settings なら entry 文字列) なので、加工せず引用する。

### 3. 宣言 config の生成

`project_config` が `missing` のときだけ行う。

1. 候補を導出する — `git remote -v` の URL から `owner/name` を、host から tracker (`github.com` → `gh` / gitlab → `glab`) を読む
2. **人間に確認する**。issue 置き場は cwd の repo とは限らない (関連 repo の issue で回す project・issue は Jira / PR は GitLab の構成がある)。確認するのは 2 点だけ:
   - issue 置き場 (tracker + 識別子)
   - PR 置き場が issue 置き場と違うか (違うときだけ `pr_tracker` / `pr_repo` を渡す)
3. `project_setup(issue_tracker=…, issue_repo=…)` を呼ぶ。既存 config があれば失敗するので、置き直すと決めたときだけ `overwrite: true` を足す

**識別子を推測で埋めない。** 綴りの誤りは server では検出できず、実在する別 repo を指していると誤った置き場を観測し続ける (最も高くつく誤り)。

生成後、`observe_issues` を 1 度呼んで `issues[].url` が意図した置き場かを目視する。**この確認は宣言を server の既定値へ反映した後でないと意味が無い** — 反映には server の再起動 (`/mcp` の reconnect) が要る。再起動前なら「置いた」ことだけを報告し、確認は再起動後に回す。

### 4. 残りの不足を渡す

| 検査 | 渡し先 |
|---|---|
| `settings` | `/apply-swat-settings` を適用先 project で起動するよう伝える。**この skill は settings を書かない** |
| `herdr_daemon` / `herdr_integration` / `uv` / `tracker_cli` | 人間の作業。`remedy` のコマンドをそのまま渡す |
| `messaging` / `herdr_session` | 起動し直しが要る。現行 binary で**新規起動**した Claude Code を herdr session 内で立てるよう伝える |
| `plugin_name` | publisher 側の宣言の問題。install 側では直せないので、報告に留める |

### 5. 再検査

`project_setup` を呼んだなら `project_doctor` をもう一度回す (config は直読みなので再起動前でも最新が出る)。残った不足だけを並べて終了する。**全 green まで粘らない** — 人間の作業が残るのは正常で、この skill の成果物は「次に誰が何をするか」が確定した一覧。

## 罠

- **doctor を Bash から走らせ直さない。** 検査を server の中に置いてあるのは sandbox を通らないためで、Bash 経由 (script 化・subprocess) では `herdr status` と `gh auth status` が settings とは無関係に失敗し、**不足の切り分けが壊れる**
- **`ok` が「置き場が正しい」を意味しない。** doctor が見るのは宣言が在るかまで。綴りが実在する別 repo を指す誤りは `observe_issues` の url でしか露見しない
- **config を編集しても即座には効かない。** 宣言は server のプロセス内 cache に載る。tool の既定値へ反映するには再起動が要る
- plugin 名の検査は**配布物の宣言** (`.claude-plugin/plugin.json`) を見ている。harness が実際に登録した名前は server から見えないので、確かめるなら `/plugin list`
