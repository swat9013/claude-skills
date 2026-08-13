# transcript store

[ADR 0031](../../../docs/adr/0031-transcript-store-elt.md) の ELT 構成における
**E + L** (lake → SQLite store)。後続の mart 移行 (#497 / #498 / #499) が依存する
API と schema をここに固定する。

```
lake ~/.claude/projects/<project>/*.jsonl   (正本)
  │  ingest.py — on-disk 形式を知る唯一の層 (+ adapter/transcript.py)
  ▼
~/.cache/claude-transcript-ops/store-v<N>.sqlite3   (0700)
  │  query 層 — mart 1 つ = 1 ディレクトリ (marts/<name>/)
  ▼
presentation — /tmp/inventory-*/ へ mart / slice を書き path を返す
```

## 入口は 1 つ

```python
from store import ingest

conn, report = ingest.open_synced(transcripts_dir, cache_dir=None, now=None)
```

`open_synced` は store を開き、**差分 sync を自動で前置する**。鮮度は呼び出し側の
関心事にしない — 各 tool は「いつ時点の lake か」を判断せず、`SyncReport` の件数を
mart meta に転記するだけでよい (実測: full ingest 16.9 秒 / 差分 sync 0.29 秒)。

`cache_dir` は明示指定 > 環境変数 `TRANSCRIPT_OPS_CACHE_DIR` > 既定
(`~/.cache/claude-transcript-ops`) の順で決まる。テストは `tmp_path` を渡す
(`tests/conftest.py` が全テストで既定を tmp へ差し替える)。

store file 名は `store-v<N>-<lake digest>.sqlite3`。**1 lake = 1 store** —
`transcripts_dir` は引数で切り替わるため、1 つの store を別 lake で共有すると
「lake から消えた file の row を消す」規則が互いの row を消し合う。

`SyncReport`: `ingested_files` / `unchanged_files` / `removed_files` /
`unreadable_files` / `skipped_nested_files` / `records` / `broken_lines`。
**`unreadable_files` と `broken_lines` は 0 でない値が出ること自体が観測劣化の
シグナル**なので、mart meta へそのまま出す (silent skip の可視化)。

## schema

正本は [`schema.sql`](schema.sql)。要点だけ:

| 表 | 単位 | 用途 |
|---|---|---|
| `file` | transcript 1 file | fingerprint `(mtime_ns, size_bytes)` / `broken_lines` |
| `unreadable_file` | 読めなかった file | 「存在しない」と「読めない」を分ける |
| `record` (spine) | lake の 1 行 | 全 record type が 1 行入る (I4)。`ts_epoch` が窓判定の機構 |
| `tool_use` | assistant の tool_use block | tool 実行 + **ペア済みの結果** |
| `hook_firing` | hook 系 attachment | fire 実績 |

- **窓は `record.ts_epoch` で切る。`NULL` (ts 欠損) をどちらへ倒すかは各 mart の
  WHERE 句が書く** (ADR 0013 の線を SQL 側で維持)。実測では tool_use を載せる
  assistant record の ts 欠損は 0 件
- **`tool_use` の `outcome_base` は base 語彙** (`success` / `error` /
  `user-reject` / `unknown`)。mart 固有の細分 (permissions の 7 分類) は query 層で
  行う。base の判定は `adapter.transcript.classify_base_outcome` 一本
- **tool_use ↔ tool_result のペアリングは ingest で済ませてある**。`tool_use_id` の
  一意性は lake で保証されないため、SQL の join に委ねると重複 id が cross product に
  なって静かに二重計上する。結果側 4 列 (`outcome_base` / `denial_kind` /
  `result_text` / `paired`) は同じ行に畳んである
- **生の input JSON / prompt 本文は保存しない**。tool 入力は `command` /
  `target_path` / `input_excerpt` (200 字) の 3 列へ正規化する。集約キー
  (permissions の `command_head` 等) は本表からの導出なので query 層の UDF が作る

## 変更手順

**`schema.sql` を触ったら `store.STORE_VERSION` を上げる。** file 名が変わり、次回の
`open_synced` が full rebuild になる (migration は書かない)。

上げ忘れても壊れない — `connect` が `schema.sql` の digest (コメントを除いた本文) を
store の `store_meta` と突き合わせ、不一致なら drop & rebuild へ倒す。**規律ではなく
突合で守る**のは、列追加なら INSERT が `OperationalError` で落ちるのに対し、
**table 追加は file の fingerprint が一致するため再取得されず、新しい projection が
黙って 0 件になる**ため (silent zero)。version は「旧世代を残したまま切り替えたい」
ときの明示手段として残る。

projection を足すとき:

1. `schema.sql` に表を足す
2. `store.PROJECTION_TABLES` に足す (file 再取得時の全置換と、ts 欠損の劣化シグナルの
   分母がこの表を見る)
3. `ingest._parse_lines` に分岐を足す (読む on-disk key は
   `format_vocabulary.ON_DISK_NAMES` へ追記する — 追記漏れは gate の見逃しになる)
4. `store.STORE_VERSION` を上げる
5. `tests/test_store_ingest.py` の `DUMPED_TABLES` に足す (I1 / I2 の比較対象に入る)

**観測の劣化シグナル (`meta.store`) は 2 つ目の消費者が出たら store 側へ引き上げる。**
現在は `marts/permissions/query.sql` の `store_anomalies` + `SyncReport` を
`present.py` が組み立てている。どの mart も同じ数字を出すべきなので、#497 以降で
2 つ目が要るとわかった時点で store の関数へ寄せる (SQL をコピーしない)。

## 不変条件

| | 内容 | 検査 |
|---|---|---|
| I1 | ingest の冪等 (file 単位の全置換) | `tests/test_store_ingest.py` |
| I2 | 差分 sync == full rebuild | 同上 (canonical dump 比較) |
| I3 | format isolation (query 層 SQL に生 key 名を出さない) | `scripts/gate/verify-query-format-isolation.py` |
| I4 | 未知 record type も spine に入る | `tests/test_store_ingest.py` |
| I5 | store は lake の純関数 (schema drift / 旧世代 / 削除痕跡を残さない) | `tests/test_transcript_store_lifecycle.py` |

race 対策は検査ではなく**順序**で保証する: fingerprint は read の前に `stat` し、
読込完了後にのみ記録する。読込中に伸びた file は fingerprint が合わず次回必ず再取得。

プロセス間は `lock.sync_lock` で直列化する。**SQLite の `busy_timeout` では cold start
を賄えない** — WAL が分離するのは reader と writer で writer 同士は排他なので、実 lake の
full ingest (22.5 秒) の間に並んだ後続は 10 秒で `database is locked` に落ちていた。
後続は先行の完了を待ってから差分 sync に入り、待ち切れなければ `SyncLockTimeout` を
上げる (黙って観測を 0 件にしない)。

**lock file は store file 名の前方一致に置かない** (`<stem>.lock`)。schema drift の
drop & rebuild は lock を握ったまま走るので、前方一致だと store の回収 glob が
自分の lock file を巻き込み、unlink 済み inode を握った側が排他を失う。同じ理由で
**旧世代の lock file は回収しない** (0 byte / 世代なので放置してよい)。

削除は `PRAGMA secure_delete` 込みで行う。row を消しても内容は free page に残るため、
lake から消えた transcript の手入力 prompt が cache file のバイト列に残留する
(実測で確認済み)。**cache invariant を物理層まで通す**のがこの PRAGMA の役割。

## 既知の観測範囲外

`<project>/<session>/subagents/*.jsonl` (実測 789 file) は ingest しない。現行の
観測範囲 (project 直下) を移行で広げないためで、件数は `SyncReport.skipped_nested_files`
に出る。取り込みの是非は別 issue で判断する。
