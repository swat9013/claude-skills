---
name: backlog-triage
description: open issue 全件を issue-triage に通し、AFK 境界の外側に残った判定と止まったものを、人間と設問のラウンドで決めて tracker と規範へ反映する対話 triage。
disable-model-invocation: true
---

# backlog-triage

open issue を全件 `swat-skills:issue-triage` に通す。issue-triage は AFK 境界の内側と判定したもの (`ready-for-agent`・close・`wontfix`) を承認を待たずに反映し、外側の判定と止まったものを返す。ここで user に問うのは、その返ってきたものだけ — 内側の判断は user が報告を見て事後に覆す。深掘り (grilling) はここでは行わず、`needs-grilling` として返す — 1 セッションに深掘りを積むと triage が終わらない。issue を根本課題ごとに束ね直す再構成もここでは行わない。束ね直していない backlog なら、先に user が `/swat-skills:issue-restructure` を回すと、close される issue に判断を費やさずに済む — issue-restructure は反映の後に本書を Read して、この手順を続ける。

issue 置き場と CL 置き場は `${CLAUDE_SKILL_DIR}/../issue-tracker-operations/references/placement.md` を Read して決め、コマンドには同書のとおり `-R` で明示する。同書が止まるとしたときは issue-triage を呼ばずに止まり、同書が渡すとしたものを user へ報告する。CLI 規約・label 語彙は対象 repo の `docs/agents/issue-tracker.md` / `docs/agents/triage-labels.md` を Read して決める。本文は label を役の名前 (既定の役と Repo-local の役) で書き、`triage-labels.md` が別の綴りを宣言していればその綴りで付け外しする (コマンドに書いた label と、label で絞る検索も同じ)。`triage-labels.md` が `needs-grilling` の役を宣言していない repo では、区分 4 (判断) を label でなく報告で返す。コマンド例は GitHub (`gh`) の綴りで、他の tracker なら読み替える。

## 調査

- open issue の番号を `gh issue list -R <issue 置き場> --state open --limit 1000 --json number,labels` で全件引き、返った件数が `--limit` に届いていたら上げて引き直す (`gh` の既定は 30 件で、黙って打ち切られる)。`tracking-issue` に当たる label が付いた issue は除く — 直接は実装しないので、`ready-for-agent` にも close にもしない。issue-triage は `ready-for-agent` の候補ごとに検査の subagent を立てるので、費用は件数に比例する。1 セッションで回し切れない件数なら、絞り方 (label・作成日など) だけを先に user に聞く
- 残りを `swat-skills:issue-triage` に Skill tool で 1 回で渡す。args は issue 置き場と `#<N>` の見出しの列。issue ごとの結果 (区分・反映した操作・基準・呼び出し元に渡すもの) が返る

## 承認のラウンド

user には番号付きの設問をラウンドで出す。最初のラウンドの冒頭に全 issue の一覧表 (issue / 区分 / 結果 / 根拠) を置く。issue-triage が反映済みの issue も載せる — user が一覧を見て内側の判断を覆せるようにする。設問にするのは、結果が反映済み以外の issue だけ。

| 結果 | 設問に出すもの (文面は逐語) | 推奨 |
|---|---|---|
| 区分 2 | 規範を書き込む先と文案。同じ規範を待つ issue が返ったら、その issue の設問を指す | 文案の承認。見送るなら、`規範の文案:` の行で始まるコメントを投稿して `ready-for-human` にする |
| 区分 4 (判断) | 論点と選択肢、`needs-grilling` にするときに投稿する論点のコメントと、issue-triage が返した手順コメント (「手順コメントあり」なら出さない) | `needs-grilling`。user がその場で決めたら、決定を書き込んだ本文の書き換え案にする |
| 区分 4 (人手) | issue-triage が返した人手の作業のコメント | コメントを投稿して `ready-for-human` にする。深掘りの手順コメントは付けない |
| `needs-info` | 報告者へ聞くコメント | コメントして `needs-info` にする |
| 返った区分が、既に付いている待ちの状態 (`needs-info`・`needs-grilling`・`ready-for-human`) と同じで、前回の待ちのコメント以降に新しい情報が無い | 設問にしない。一覧表で「据え置き (待ち継続)」とする。`needs-grilling` で issue-triage が手順コメントを返した (「手順コメントあり」でない) ときは、手順コメントだけを設問に出す | 論点のコメントを投稿し直さない。手順コメントは承認されたら投稿する |
| 停止 | issue-triage が添えた停止理由・人が次にやること | 返ったものに従う |
| 書き込まず (open CL) | 判定と反映する操作の文面 (書き換え案・投稿するコメント)。本文の書き換え・label なら、issue を閉じるその CL を先に close して反映するか、close せずに書き換えも label も反映しないまま差し戻すかを併せて問い、差し戻しのコメントは本文の書き換え無しで成り立つ文面で出す。close・`wontfix` なら CL を close すること | 本文の書き換え・label は CL を close せずに差し戻す、close・`wontfix` は CL を close してから反映する / 据え置き。CL は書き換える前の本文を実装していて、閉じる CL が open の間は本文の書き換えの手順が止まり、閉じた issue の CL は実装する先が無い |

```
❓ **Q1** - **<見出し>**: <本文。投稿するコメント・書き換え後の本文・規範の文案は逐語で>

➡️ <推奨する回答 (選択肢なら 1 つを名指す) と根拠>

---

❓ **Q2** - ...
```

同じラウンドの他の Q の答えに依存する Q と、回答で新たに生まれた判断は、次のラウンドに出す。「推奨でOK」のような一括の回答は、そのラウンドの全 Q を推奨どおりに承認する。読み取れない回答は聞き直す。

user が下した判断と、user が issue-triage の反映を覆した判断は、規範へ codify できるかを判定する。定義・判定・文案の出し方・反映・issue 本文からの引き方は `${CLAUDE_SKILL_DIR}/../grill-with-docs/SKILL.md` の「決まった判断を規範へ codify する」節を Read して従う。覆した判断は、issue-triage が反映した操作ごとに戻す操作も同じ Q に出す: label は反映する前の状態の label (調査で引いた label。状態の label が無かったときと、`ready-for-agent` だったときは `needs-triage` — `ready-for-agent` は検査を通した本文に issue-triage だけが付ける) へ戻す、close・`wontfix` は reopen してその label を付け直す、本文は旧本文の記録のコメントから書き戻す案、起票した follow-up issue は close する案と理由のコメントの逐語。`needs-triage` に揃えて戻さない — 揃えると、無人の triage が user の覆したばかりの判断を下し直す。あわせて、1 行目を `覆した判断: <覆した反映の操作>`、2 行目を `人の判断: <user の判断と理由>` にしたコメントの文面も出す — 次の triage はこのコメントを前回の判定として読む。戻す操作は issue-triage に渡さず、「反映と報告」で自分で撃つ — issue-triage は渡された issue を振り分け直すので、覆した判断を承認なしに付け直しうる。文案を添えた issue は、規範を引く本文の書き換え案も作る。書き換え案はどれも `${CLAUDE_SKILL_DIR}/../issue-tracker-operations/references/body-rewrite.md` の「書き換え案に残す行」の形で組む。

聞くことが無くなったら、反映する操作の一覧を出して「この一覧どおりに反映するか」を確かめてから書く。修正が返ったら該当する Q を次のラウンドへ戻し、一覧を出し直す。

## 反映と報告

外側の判断と、open CL で書き込まなかった判断は、承認された操作だけを承認された文面のまま反映する — 人に返した判断を agent が黙って確定させない。文面は `mktemp` で作業ツリーの外に作った file に書き、`--body-file` で渡す。

- label: `${CLAUDE_SKILL_DIR}/../issue-tracker-operations/references/state-label.md` を Read し、その付け替え方で付け替える (外す label は付いている状態の label、止めたときの宛先は user)。書き込まず (open CL) で CL を close してから反映すると承認された issue のうち、判定が区分 1・3 のものはこの箇条で label を付けず、書き換え案があれば下の「本文の書き換え」の箇条で書き戻してから、無ければそのまま、CL の close の後に `swat-skills:issue-triage` へ渡し直す — `ready-for-agent` を付けるのは検査を通す issue-triage だけ。行き先は、覆した判断なら「承認のラウンド」の覆した判断の段が決める label、区分 2 を待たせるなら `ready-for-human`、区分 4 (判断) は `needs-grilling`、区分 4 (人手) は `ready-for-human`、`needs-info` は `needs-info`
- コメント: `gh issue comment <N> -R <issue 置き場> --body-file <file>`。`needs-grilling` にする issue には、論点のコメントとは別のコメントとして、承認された手順コメントも投稿する (`needs-grilling` に当たる label が無い repo では投稿せず、最後の報告に逐語で書く)。区分 4 (人手) の issue には、返った人手の作業のコメントを逐語で投稿する。区分 2 を待たせるコメントは、issue-triage が返した `規範の文案: <path>` の行を 1 行目に置く (次の triage が同じ文案を待つ issue を探す目印)。覆した判断の issue には、承認された `覆した判断:` のコメントを、戻す操作より先に投稿する — 戻す操作の途中で止まっても、覆したことは issue に残る
- close / `wontfix` / reopen: `${CLAUDE_SKILL_DIR}/../issue-tracker-operations/references/close-reopen.md` を Read し、その順で撃つ (止めたときの宛先は user)。reopen で付ける状態の label は、覆した判断の reopen なら「承認のラウンド」の覆した判断の段が決める label、それ以外の reopen なら `needs-triage`
- CL の差し戻し: 承認された文面のコメントを `gh pr comment <N> -R <CL 置き場> --body-file <file>` で投稿する。CL の close は、承認されたときだけ `gh pr close <N> -R <CL 置き場>` で行う。承認された CL の close は、その issue の本文・label・close の操作より先に撃つ
- 規範の codify: 上で Read した codify の節の反映の手順で、その規範を引く issue の本文の書き換えより先に反映する
- 本文の書き換え: 承認された書き換え案は、issue-triage に渡し直さず、`${CLAUDE_SKILL_DIR}/../issue-tracker-operations/references/body-rewrite.md` を Read してその「書き戻し」の順で自分で書き戻す。基準は issue-triage が返した基準の file。ただし覆した判断の書き戻しでは、issue-triage が書き戻した後の本文が今の本文なので、同書の「基準の保存」で今の本文を保存し直して基準にする。付け替える label は、覆した判断の書き戻しなら「承認のラウンド」の覆した判断の段が決める label、それ以外は同書の「書き換えた後の状態」が決める label。止めたら (「open CL の確かめ」を含む)、その issue の以降の操作をせず、同書の「停止の扱い」が渡すとしたものを user へ報告する。覆した判断の書き戻しを除き、`needs-triage` で書き戻した issue はまとめて `swat-skills:issue-triage` に渡し、書き戻した本文の検査と `ready-for-agent` の付与を任せる (返ったものは次のラウンドの Q にする)。`ready-for-human` で書き戻した issue は渡さない — 本文が引く規範が既定 branch に無く、issue-triage は渡されても止まる

最後に次を報告する: issue-triage が反映した操作 (issue ごと)・user が承認して反映した操作・起票された follow-up issue と発端の issue の対応・承認されず据え置いた issue・`needs-grilling` (今回付けた issue の一覧と既存の残数)・codify の節が終わりに伝えるとした規範と issue の対応・user に残る作業。
