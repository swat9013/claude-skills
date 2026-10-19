---
name: backlog-triage
description: open issue 全件を issue-triage に通し、AFK 境界の外側に残った判定と止まったものを、人間と設問のラウンドで決めて tracker と規範へ反映する対話 triage。
disable-model-invocation: true
---

# backlog-triage

open issue を全件 `swat-skills:issue-triage` に通す。issue-triage は AFK 境界の内側と判定したもの (`ready-for-agent`・close・`wontfix`) を承認を待たずに反映し、外側の判定と止まったものを返す。ここで user に問うのは、その返ってきたものだけ — 内側の判断は user が報告を見て事後に覆す。深掘り (grilling) はここでは行わず、`needs-grilling` として返す — 1 セッションに深掘りを積むと triage が終わらない。issue を根本課題ごとに束ね直す再構成もここでは行わない。束ね直していない backlog なら、先に user が `/swat-skills:issue-restructure` を回すと、close される issue に判断を費やさずに済む。

置き場は `-R` で明示する: issue は `-R <issue 置き場>`、PR は `-R <CL 置き場>` (issue の変更が入る repo。issue 置き場と別のことがある)。置き場と CLI 規約・label 語彙は対象 repo の `docs/agents/issue-tracker.md` / `docs/agents/triage-labels.md` を Read して決める。本文は label を役の名前 (既定の役と Repo-local の役) で書き、`triage-labels.md` が別の綴りを宣言していればその綴りで付け外しする (コマンドに書いた label と、label で絞る検索も同じ)。宣言を読み取れなければ cwd の repo を両方の置き場とし、その前提を報告に書く。`triage-labels.md` が `needs-grilling` の役を宣言していない repo では、区分 4 を label でなく報告で返す。コマンド例は GitHub (`gh`) の綴りで、他の tracker なら読み替える。

## 調査

- open issue の番号を `gh issue list -R <issue 置き場> --state open --limit 1000 --json number,labels` で全件引き、返った件数が `--limit` に届いていたら上げて引き直す (`gh` の既定は 30 件で、黙って打ち切られる)。`tracking-issue` に当たる label が付いた issue は除く — 直接は実装しないので、`ready-for-agent` にも close にもしない。issue-triage は `ready-for-agent` の候補ごとに検査の subagent を立てるので、費用は件数に比例する。1 セッションで回し切れない件数なら、絞り方 (label・作成日など) だけを先に user に聞く
- 残りを `swat-skills:issue-triage` に Skill tool で 1 回で渡す。args は issue 置き場と `#<N>` の見出しの列。issue ごとの結果 (区分・反映した操作・基準・呼び出し元に渡すもの) が返る

## 承認のラウンド

user には番号付きの設問をラウンドで出す。最初のラウンドの冒頭に全 issue の一覧表 (issue / 区分 / 結果 / 根拠) を置く。issue-triage が反映済みの issue も載せる — user が一覧を見て内側の判断を覆せるようにする。設問にするのは、結果が反映済み以外の issue だけ。

| 結果 | 設問に出すもの (文面は逐語) | 推奨 |
|---|---|---|
| 区分 2 | 規範を書き込む先と文案。同じ規範を待つ issue が返ったら、その issue の設問を指す | 文案の承認。見送るなら、`規範の文案:` の行で始まるコメントを投稿して `ready-for-human` にする |
| 区分 4 | 論点と選択肢、`needs-grilling` にするときに投稿する論点のコメントと、issue-triage が返した手順コメント (「手順コメントあり」なら出さない) | `needs-grilling`。user がその場で決めたら、決定を書き込んだ本文の書き換え案にする |
| `needs-info` | 報告者へ聞くコメント | コメントして `needs-info` にする |
| 返った区分が、既に付いている待ちの状態 (`needs-info`・`needs-grilling`・`ready-for-human`) と同じで、前回の待ちのコメント以降に新しい情報が無い | 設問にしない。一覧表で「据え置き (待ち継続)」とする。`needs-grilling` で issue-triage が手順コメントを返した (「手順コメントあり」でない) ときは、手順コメントだけを設問に出す | 論点のコメントを投稿し直さない。手順コメントは承認されたら投稿する |
| 停止 | issue-triage が添えた停止理由・人が次にやること | 返ったものに従う |
| 書き込まず (open CL) | 判定と反映する操作の文面 (書き換え案・投稿するコメント)、CL への差し戻しコメントか CL を close すること | 判定の操作を反映し、本文の書き換え・label なら CL を差し戻し、close・`wontfix` なら CL を close する / 据え置き。CL は書き換える前の本文を実装していて、閉じた issue の CL は実装する先が無い |

```
❓ **Q1** - **<見出し>**: <本文。投稿するコメント・書き換え後の本文・規範の文案は逐語で>

➡️ <推奨する回答 (選択肢なら 1 つを名指す) と根拠>

---

❓ **Q2** - ...
```

同じラウンドの他の Q の答えに依存する Q と、回答で新たに生まれた判断は、次のラウンドに出す。「推奨でOK」のような一括の回答は、そのラウンドの全 Q を推奨どおりに承認する。読み取れない回答は聞き直す。

user が下した判断と、user が issue-triage の反映を覆した判断は、規範へ codify できるかを判定する。定義・判定・文案の出し方・反映・issue 本文からの引き方は `${CLAUDE_SKILL_DIR}/../../design/grill-with-docs/SKILL.md` の「決まった判断を規範へ codify する」節を Read して従う。覆した判断は、issue-triage が反映した操作ごとに戻す操作も同じ Q に出す: label は元の状態へ戻す、close は reopen して状態の label を付け直す、`wontfix` は reopen して `wontfix` を外し状態の label を付け直す、本文は旧本文の記録のコメントから書き戻す案、起票した follow-up issue は close する案。文案を添えた issue は、規範を引く本文の書き換え案も作る。

聞くことが無くなったら、反映する操作の一覧を出して「この一覧どおりに反映するか」を確かめてから書く。修正が返ったら該当する Q を次のラウンドへ戻し、一覧を出し直す。

## 反映と報告

外側の判断と、open CL で書き込まなかった判断は、承認された操作だけを承認された文面のまま反映する — 人に返した判断を agent が黙って確定させない。文面は `mktemp` で作業ツリーの外に作った file に書き、`--body-file` で渡す。

- label: `gh issue edit <N> -R <issue 置き場> --add-label <行き先> --remove-label <付いている状態の label>` の 1 回で付け替える。行き先は、区分 2 を待たせるなら `ready-for-human`、区分 4 は `needs-grilling`、`needs-info` は `needs-info`
- コメント: `gh issue comment <N> -R <issue 置き場> --body-file <file>`。`needs-grilling` にする issue には、論点のコメントとは別のコメントとして、承認された手順コメントも投稿する (`needs-grilling` に当たる label が無い repo では投稿せず、最後の報告に逐語で書く)。区分 2 を待たせるコメントは、issue-triage が返した `規範の文案: <path>` の行を 1 行目に置く (次の triage が同じ文案を待つ issue を探す目印)
- close / `wontfix` / reopen: コメントを投稿してから `gh issue close` / `gh issue reopen`。`wontfix` は close の前に `wontfix` を付ける。close したら状態の label (`wontfix` は含まない) を外し、reopen したら `needs-triage` を付ける
- CL の差し戻し: 承認された文面のコメントを `gh pr comment <N> -R <CL 置き場> --body-file <file>` で投稿する。CL の close は、承認されたときだけ `gh pr close <N> -R <CL 置き場>` で行う
- 規範の codify: 上で Read した codify の節の反映の手順で、その規範を引く issue の本文の書き換えより先に反映する
- 本文の書き換え: 承認された書き換え案を持つ issue は、その案と issue-triage が返した基準の path を見出しの下に添えて `swat-skills:issue-triage` に渡し直す。内側に入れば issue-triage が本文の書き戻しと `ready-for-agent` までを反映し、外側に残れば次のラウンドの Q にする。open CL がある issue は issue-triage が書かないので、`${CLAUDE_SKILL_DIR}/../issue-triage/references/body-rewrite.md` の「書き戻し」の順で自分で書き戻してから CL を差し戻す (手順 1 で外す label は `ready-for-agent`。止めたら user へ報告し、label は止めた時点のまま残す)

最後に次を報告する: issue-triage が反映した操作 (issue ごと)・user が承認して反映した操作・起票された follow-up issue と発端の issue の対応・承認されず据え置いた issue・`needs-grilling` (今回付けた issue の一覧と既存の残数)・codify の節が終わりに伝えるとした規範と issue の対応・user に残る作業。
