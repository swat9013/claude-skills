---
name: dispatch-dashboard
user-invocable: true
description: dispatch v2 の現況を人間が 1 枚で読む read-only な dashboard の開き方と読み方。Use when「dispatch の現況を dashboard で見たい」「dashboard が出ない」.
---

# dispatch-dashboard

dispatch v2 の現況を**人間が**読む画面。read-only で、記帳も Session 操作もしない。

本 skill が持つのは画面への導線と読み方だけ。画面の中身 (投影・未観測の扱い) は daemon の実装が持ち、
判断は読んだ人間が持つ。

## 開く

`dashboard_open` を呼ぶ。画面がこのマシンの既定 browser で開き、daemon が居なければ tool 側が起こす。
**URL を文字で渡すだけでよいとき (別画面で既に見ている / 窓を出したくない) は `dashboard_url` を呼ぶ** —
こちらは URL を答えるだけで browser を撃たない。

`open_reason` が埋まっているとき (開けなかった / 渡したが成否を確かめられなかった)、tool が error を
返したときは、`open_reason` / `reason` / message を逐語で人間へ渡す。原因は message が名乗るので、こちらで言い当てない。いずれも
**画面が開けないだけで、daemon の本務 (台帳と観測) は動いている** — 開けないことを dispatch の停止と
読み替えない。開けない状態の復旧は人間が決めるところなので、渡した時点でこの skill の仕事は終わる。

## 読み方

- **主語は WorkOrder** (issue ではない)。1 行 = WorkOrder 1 件で、その daemon が抱える project が
  横断で並ぶ
- **空欄には 3 種類ある。** 理由付きの「未観測」は観測が届いていない欄。観測して値が無い欄は
  「Session 無し」「紐づく CL 無し」「観測して 0 件」と語で出る。`—` は台帳にその値が無い欄
  (判断待ち 0 件 / worktree 未作成)。**「未観測」を「無い」と読まない**
- **行の外にも読む欄がある。** WorkOrder に紐づかない判断待ちは project の panel に、投影できなかった
  project は理由付きで一覧の外に並ぶ。**行が無いことを「その project は無い」と読まない**
- **画面は投影の写しなので遅れる。** 遅れの幅は欄ごとに違う (記帳は次の投影、tracker / CL の観測は
  最も長い周期)。どれだけ古いかは project ごとの投影時刻と観測時刻に出ているので、そこを見て読む

## 何をしない skill か

- **URL と、開けなかった理由を人間へ渡すところまでを行う。** WorkOrder の遷移・Session の起こし直し・
  worktree の回収・判断待ちの解消は行わず、呼び出し元のフローへ返す。画面から台帳は書けない
  (接続が read-only、handler が GET しか持たない)
- **LLM の入力にしない。** 台帳と観測を LLM に読ませるなら `wo_list` / `observe_*` / `inbox_read` を
  呼ぶ。画面の payload は人間が読むための整形を含む
- **起動も停止も手順に持たない。** 画面を上げるのは daemon で、`dashboard_open` / `dashboard_url` が
  不在なら起こす
