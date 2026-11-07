---
name: transcript-query
user-invocable: true
description: Claude Code の transcript を読み取り専用で問い、出力先の path と件数と劣化の meta を返す観測の呼び出し口。Use when「観測窓の中の利用を named query で集計する」「過去の session で skill や tool がどれだけ使われたかを一度きり調べる」.
---

# transcript-query

`~/.claude/projects/` の transcript 全体 (lake) を DuckDB で直接読み、成果物 (`contract` と `rows` を持つ JSON) を `/tmp/<consumer>/` に書いて、path と件数 meta だけを返す。本体は読む段階で path から Read する。この skill を呼ぶ手順を書くときは `${CLAUDE_SKILL_DIR}/references/caller-contract.md` を読む。

`uv` が前提で、初回の起動で `uv` が `duckdb` (約 49 MB) を取得する。network が要る。`uv` が無いか取得に失敗したら、その理由をそのまま呼び出し元へ返して止まる。

stdout は 1 個の JSON で、exit 0 は答えた、exit 1 は止まった。`stopped` の JSON が無いまま非 0 で終わったら起動自体の失敗で、答えは無い。stderr をそのまま呼び出し元へ返す。観測窓は ISO 8601 で渡し、offset を省くと UTC として読む。

## named query

集計や切り出しは、次の一覧の問いを呼ぶ。一覧に無い問いは「ad-hoc の問い」へ進む。

| 名前 | 答える問い |
|---|---|
| `find-invocations` | 名指しした skill は実際にどの transcript のどこで呼ばれたか (呼出しの位置つきの slice) |
| `sum-stage-usage` | 名指しした transcript の工程ごとの所要・subagent 数・token はいくつか (工程の境界は呼び出し元が渡す) |
| `permissions` | permission entry・hook 設定と tool の実績の突き合わせ (条件: `section`・`transcript_kind`・`repo_root`) |
| `invocations` | skill・agent・MCP・plugin は窓の中でどれだけ提示され、呼ばれたか。token 経済と未使用の候補を含む。条件は `repo_root` (分母を読む project。既定は cwd) |
| `scan-prompts` | 観測窓の中で人間が手入力した prompt は何か (全 project 横断の mart) |
| `select-prompt-candidates` | 手入力 prompt のうち読むべき候補はどれか (長さ・repo・正規形で絞った slice。`repo` の既定は実行中の dir の repo で、`all` で全 repo) |
| `overhead` | cwd の repo の session に載った静的コンテキストの量と、memory file が注入された session 数と、compaction で捨てた token |

```bash
${CLAUDE_SKILL_DIR}/scripts/transcript_query.py named <名前> [--since <ISO 8601>] [--until <ISO 8601>] --consumer <出力 dir 名> [--param <条件の名前>=<値> ...]
```

`--param` は問いが定める条件で、条件ごとに繰り返す。問いごとの条件は呼び出し元の手順が渡す。

## ad-hoc の問い

どの named query も答えない一度きりの問いは、SQL を渡す。

```bash
${CLAUDE_SKILL_DIR}/scripts/transcript_query.py ad-hoc --limit <行数の上限> --consumer <出力 dir 名> --sql '<SELECT 文>' [--since <ISO 8601>] [--until <ISO 8601>]
```

- SQL を書く前に `${CLAUDE_SKILL_DIR}/views.sql` を Read し、`lake_` で始まる view から選ぶ。view に無い事実だけ、`lake_line` の `line` の生の JSON へ書く
- 問いは 1 本の SELECT に限る。結果は出力先に書かれるので、書き出しの文は要らない。lake の外は読めない
- SQL の文字列 literal の `'` は shell の単一引用符を閉じるので、`--sql` の値は `'\''` で書くか、heredoc で変数に入れて渡す
- 母集団は main と subagent の全部で、`transcript_kind` 列で絞れる。列を持たない view は `lake_file` を `path` で join して絞る。subagent の record は親の session id を持つので、session を数える問いは `transcript_kind = 'main'` で絞る
- repo で絞る問いは `lake_cwd_repo` (`cwd`・`repo`・`unresolved_cause`) を join する
- `truncated` が true なら、結果は `--limit` で打ち切られている
- `--since` / `--until` は形式の照合の窓で、問いの絞り込みは SQL に書く。期間を絞る問いには同じ期間を渡す — 渡さないと全期間で照合し、変化の前の record で通る
- ad-hoc の問いは形式の食い違いで止まらず、返り値の `missing_paths` に出す。空でなければ view の結果は黙って 0 件や NULL になっているので、`lake_line` の `line` を少数行 SELECT して今の形を見て、その問いの SQL だけを書き換える。`views.sql` と `schema-snapshot.json` はこの skill の配布物なので手元で直さない
- 同じ問いが繰り返し要るとわかったら、手順に埋めずに named query の追加を plugin の保守者へ提案する。ad-hoc の問いは `contract` を持たないので、読み方が手順ごとに割れる

## 返り値の読み方

- `degradation` の `broken_lines`・`ts_missing_records`・`undecodable_files`・`unreadable_files`・`unreadable_dirs`・`unrecognized_nested_files` は観測の劣化の件数で、0 でない値があれば、その成果物の 0 件を「使われていない」の証拠に使わず、件数を添えて呼び出し元へ返す。`files_read`・`records`・`workflow_journal_files` は劣化ではない
- 劣化の件数は窓で絞らない。named query では成果物の母集団全体の値で、`unrecognized_nested_files` は母集団を問わない。ad-hoc では SQL の絞り込みに連動しない
- `undecodable_files` は読めない行を含む file の数で、その file の行の番号は読めない行の分だけずれる
- `absent_optional_paths` は窓に現れなかった任意の path で、その機能を使っていなければ現れないのが正常な path を並べる。問いが読む path がここにあれば、その 0 件は機能の不使用と形式の変化を区別できない。subagent の meta の path は窓を問わず全 meta で見るので、窓の外の meta が持てばここに並ばない
- `meta` は行に載らない値で、持つ問いの返り値にだけある。成果物にも同じ値が載り、読み方は成果物の `contract` が持つ
- `snapshot_not_checked` があれば ad-hoc の問いは形式を照合しておらず、`missing_paths` の key も無い。値は照合を止めた種類で、その問いの 0 件は形式の変化と区別できないので、値を添えて呼び出し元へ返す
- `stopped` があれば答えは無い
  - `schema_mismatch`・`lake_missing`・`lake_empty` は答えなしで止まり、返り値をそのまま呼び出し元へ返す。`schema_mismatch` を 0 件へ読み替えない
  - `permission`・`syntax`・`memory`・`query_failed`・`bad_argument`・`output_rejected` は渡した値の失敗で、`next_action` に従って SQL や引数を直して呼び直す。`next_action` が保守者への報告なら、`error_type` ごと呼び出し元へ返す
  - `empty_window`・`unknown_query` は問いの選び直しで、窓を広げるか一覧の名前で呼び直す。窓を決める手順がある呼び出し元は、その手順へ返す
