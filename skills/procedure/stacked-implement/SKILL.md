---
name: stacked-implement
disable-model-invocation: true
argument-hint: "<issue 番号… | 分割計画 file | 仕様> 例: 14-16 / 14 15 16 / plan.md"
description: 名指しした実装 (issue 集合・分割計画・単一の仕様) を CL 計画に分け、CL ごとに CL エージェントを並列に起動して実装させ、merge まで追従する。
---

# stacked-implement

このセッション (親セッション) は CL 計画を立て、CL 1 本につき CL エージェント (`swat-skills:cl-agent`) を 1 体、worktree 付きで並列に起動する。CL エージェント同士は担当範囲に沿って依頼し合い、決着しない事項と質問だけが親へ上がる。親はそれをユーザーに問う。

親は自分では実装しない — 実装している間は、CL エージェントからの依頼と質問の中継が止まる。merge と issue close はユーザーの領分で、親は merge の合図を受けて後始末 (conflict 解消の指示・target の確認・worktree の片付け) をする。

## args

| 形 | 計画の作り方 |
|---|---|
| issue 番号の範囲 (`14-16`) か列挙 (`14 15 16`) | 1 issue = 1 CL。範囲は両端を含む連番へ展開する |
| 分割計画の file (CL ごとに題・担当範囲・依存を書いたもの) | 書かれたとおりに CL 計画へ写す |
| 単一の仕様 (分割を求められた issue 1 件、または計画の形をしていない仕様 file) | 親が CL への分割案を作る |

## CL 計画

CL 計画の 1 行は次の欄を持つ。CL エージェントは自分の行だけでなく全行を受け取り、担当範囲の外の変更を誰に依頼するかを全行から決める。

| 欄 | 中身 |
|---|---|
| 名前 | `cl-<識別子>`。識別子は ASCII の英数字とハイフンで、`cl-` を含めて 64 字以内 — そのまま Agent tool の `name` と SendMessage の宛先になる |
| branch | この CL の branch 名。親が計画の段階で決める (後続の分岐元と target になるので、起動前に確定している必要がある) |
| 担当範囲 | この CL が何を変え、何を変えないかを文章で書く。ある変更をどの CL に載せるかは、この記述に照らして決まる |
| 依存先 | 前提にする CL の名前 (無ければ空) |
| 後続の前提 | この CL を依存先にする行が、この CL の何を前提にするか。前段の CL エージェントは、これを commit して push した時点で親へ知らせる |
| 分岐元 / target | 依存先が無ければ既定ブランチ。あれば依存先の branch |
| issue 参照 | 1 issue = 1 CL なら `Closes <issue 置き場>#<N>`、1 issue を複数 CL に割ったなら全 CL に `Refs <issue 置き場>#<N>` (merge 順が読めないので close はユーザーに任せる)、issue が無ければ無し。`<issue 置き場>` は GitHub なら `owner/repo`、GitLab なら project の full path (`group/project`。subgroup があれば含む) で、置き場が CL の repo と同じでも付ける — 素の `#<N>` は CL の repo の番号として解決されるので、置き場が別 repo だと別の issue を指す |

## 最終報告の 1 行目

CL エージェントの最終報告の 1 行目は、次の 3 つの形のどれかにする。親はこの行で報告を振り分ける。この書式は手順 5 の起動 prompt で CL エージェントへ渡す。

| 形 | 意味 |
|---|---|
| `CL <名前> → <参照> <URL>` | CL を出した (`<参照>` は GitHub なら `#<番号>`、GitLab なら `!<番号>`) |
| `返事待ち <名前>: <待っている相手と用件>` | 親か他の CL エージェントの返事なしでは何も進まない。返事を SendMessage で送れば再開する |
| `見送り <名前>: <理由>` | 人手でしか進まない理由で CL を出せない。または、計画の修正で行が外れて作業を止めた |

## 手順

途中の報告 (手順 1・2 の除外、手順 4 の計画、手順 5 の起動と CL 到達・見送り) は、次の作業の最初の tool 呼び出しと同じメッセージに書く。turn を終えるのは、ユーザーへの質問、CL エージェントの通知待ち、手順 6 の完了報告のときだけ。通知待ちで turn を終えても、CL エージェントの最終報告と SendMessage がこのセッションを再開させる。

1. **入力を読む。** issue は title と本文を、置き場を常に明示して引く (`gh issue view <N> -R <issue 置き場> --json title,body,state,url,blockedBy` / `glab issue view <N> -R <issue 置き場>`) — cwd 推論に任せると、cwd と置き場が別 repo の構成で同じ番号の別 issue を読む。issue 置き場は、呼び出し元が指定していればそれを使う。指定が無ければ対象 repo の `docs/agents/issue-tracker.md` の宣言に従う。宣言が別の file を正本として指すなら、その file が置き場として宣言する値を使う。指された file が無いか値を読み取れなければ、対象 repo 自身へ落とさず、止まって、指された file の path と読めなかった理由を挙げて置き場をユーザーに問う — 正本が別 repo を宣言していると、同じ番号の別の issue を読むことになる。`docs/agents/issue-tracker.md` が無いか置き場の宣言を読み取れなければ対象 repo 自身を置き場とする。対象 repo は cwd の repo、呼び出し元の指定は起動時の依頼文による置き場の repo の名指しを指す。closed の issue・PR の番号・読めない issue は対象から外し、どれに当たるかを付けて報告へ載せる。範囲の展開は PR の番号も拾い、`gh issue view` はそれも返す — url が `/pull/` を含めば PR として closed と分ける。file は Read する。

2. **依存を判定する。** 判定は次の順で、上が勝つ:
   - tracker の依存関係 (GitHub の `blockedBy`。GitLab は引かずに本文だけで判定する) か、本文の明示参照 (`Blocked by #N` / `Depends on #N` / 「#N に依存」) が正。明示参照の素の `#N` は issue 置き場の番号として読み (tracker は issue 本文の参照をその issue のある repo で解決する)、依存先の状態は `gh issue view <N> -R <issue 置き場> --json state` / `glab issue view <N> -R <issue 置き場>` で引く
   - 明示が無くても、後の CL が前の CL の成果 (書き換え後の file の内容・同じ振る舞い) を前提にすると読めたら依存にする。前の CL が他の CL に制約を課す (gate の追加・言語や依存の版上げ) なら、制約を受ける CL をその後続にする — 後続がその制約の上で作られ、CI でも検査される
   - どちらとも読めなければ独立とする

   依存先が手順 1 で除外された CL は: 依存先が closed なら成果は既定ブランチに居る前提で独立として扱う。依存先が「読めない」で除外されたなら、後続ごと対象から外して報告へ載せる。依存先が名指しの集合の外で open なら、既定ブランチから独立として積み、未解決の依存として計画に載せる。依存先が複数ある CL は、1 つを分岐元と target にし、残りは merge で取り込む (target 以外の前段の差分がその CL に混ざることを計画に書く)。

3. **CL 計画を組む。** 単一の仕様を分割するときは、各 CL が単独でレビューでき、単独で CI を通る単位に切る。続けて、CL 同士が同じ file を触る見込みを洗い出す。依存の無い CL 同士が同じ箇所 (登録リスト・設定の列挙・共有の型定義など、各 CL が末尾に書き足す場所) を触るなら、その受け口を用意する作業を根の CL (分岐の起点になる CL) の担当範囲へ移し、他の CL をその後続にする — 各 CL が別々に書き足すと、merge が 1 本進むたびに残りの CL が conflict する。

4. **計画を提示する。** 次を応答テキストに書き、同じメッセージで手順 5 の起動を始める。承認は待たない — CL エージェントが CL を出すのは実装とレビューを終えた後なので、ユーザーがそれまでに割り込めば、誤りは CL エージェントの branch (後続の前提として push した commit を含む) に止まり、CL にも既定ブランチにも出ない。提示はその割り込みのための唯一の面で、起動の tool 呼び出しと同じメッセージに置くことで書き落とさずに済む。
   - CL 計画の全行 (表)
   - 依存の判定根拠
   - 同じ file を触る見込みと、手順 3 で受け口を根の CL へ移した箇所
   - 本文が「前段の merge 後に着手」と求めているのに、stack に積んで先に着手する依存 (該当 issue 番号つき)
   - 起動する CL エージェントの数

   起動後の計画の修正は、手順 5 の中継で受ける。

5. **CL エージェントを起動して中継する。** 依存先の無い行の CL エージェントを 1 つの message にまとめて起動する。Agent tool で `subagent_type: swat-skills:cl-agent` を指名し、`name` に行の名前、`isolation: "worktree"` を付ける。CL エージェントの最終報告は完了通知で届く。起動の戻りに出た worktree の絶対 path を、行の名前と並べて応答テキストに書く (手順 7 の片付けで使う。会話の圧縮や再開の後も読めるように残す)。

   prompt には次を入れる: CL 計画の全行、自分の行の名前、分岐元と target の branch 名 (依存先が複数なら、merge で取り込む残りの前段 branch も)、issue 参照の書き方、依頼の原文 (issue 本文か、分割計画 file・仕様 file のうちその行に当たる部分を逐語で)、親の宛先 `main`、上の「最終報告の 1 行目」の表。`swat-skills:cl-agent` が解決しないときは、起動せずにその旨と、swat-skills plugin の agent が読み込まれる状態にしてから呼び直す手順を報告して止まる — 汎用の agent では CL エージェントの手順が届かない。

   起動した後は、届いた通知ごとに次のとおり中継する。
   - **後続の前提の push**: 前段から branch 名と commit が届いたら、その前段に依存する行の CL エージェントを起動する。依存先が複数ある行は、全部の依存先から届いてから起動する。後続が起動済みなら、前段 branch の更新を取り込むよう後続へ SendMessage で伝える
   - **届かなかった依頼**: 宛先の CL エージェントがまだ起動していなければ、その依頼を手元に持ち、起動するときの prompt に加える。終了していれば SendMessage で再開させて渡す
   - **どの担当範囲にも載らない変更**: 新しい行を計画に足すか、依頼元の担当範囲に含めるかを、推奨を付けてユーザーに問う。答えを依頼元へ返す。行を足したら、次項の計画の修正として扱う
   - **計画の修正** (ユーザーの割り込み、または前項で行を足したとき): 直した計画を手順 4 の形で提示し直し、起動済みの全 CL エージェントへ SendMessage で送る。足した行は、依存先の条件を満たしていればこの手順の形で起動する。外した行は見送りとし、その CL エージェントに作業を止めるよう伝える。外した行に push 済みの branch や出した CL があれば、残すか消すかをユーザーに問う
   - **CL エージェント同士で決着しない事項と、CL エージェントからの質問**: 親は裁定しない。双方の言い分 (質問なら選択肢と CL エージェントの推奨) を要約し、親の推奨を付けて AskUserQuestion で問う。答えを関係する CL エージェントへ SendMessage で返す — 親の裁定はユーザーの目に触れないまま CL に固まる
   - **依頼の完了の知らせ**: 応答テキストの報告に載せるだけでよい
   - **最終報告**: 1 行目の形で振り分ける。`CL` の行はそのまま応答テキストに書き写す。`返事待ち` は、待っている相手へ用件を渡すか、ユーザーへ問うかして、返事を SendMessage で返す。`見送り` は、その行と、それに依存する後続の行を見送りとし、理由を報告する。起動済みの後続を止めるかはユーザーに問う。他の行は止めない。ただし計画の修正で外した行の `見送り` は後続へ広げない (後続の扱いは直した計画が決めている)。報告にある push 済みの branch と CL を、計画の修正の項の問いに使う

6. **完了報告。** 全行が CL 到達か見送りになったら:
   - 全行を `CL <名前> → <参照> <URL>` の形で列挙し、stack 構造 (どの CL がどの branch を target にしているか) を添える
   - merge の推奨順: 前段から順に。依存の無い CL 同士では、他の CL と同じ file を触る数が少ないものから
   - 見送った行とその理由
   - ユーザーに残る作業: merge したら、親へ「<CL> を merge した」と一言伝える。前段 CL を merge して source branch を削除すると、後続の target は自動で既定ブランチへ付け替わる (gh / glab とも。ただし環境依存があり、手順 7 で親が確かめる)。merge を伴わない branch 削除は後続の CL を close しうるので行わない

7. **merge に追従する。** ユーザーから merge の合図が届くたびに、全 CL の状態を引く (`gh pr view <番号> --json state,baseRefName,mergeable` / `glab mr view <番号>`)。
   - **open で conflict している CL**: その CL エージェントへ SendMessage で「既定ブランチへ merge が入ったので conflict を解消する」よう伝え、`${CLAUDE_SKILL_DIR}/../playbook-conflict-resolution/SKILL.md` の絶対 path と、CL の番号・head (その CL の branch)・base (その CL の現在の target) を添える
   - **target が削除済みの branch のまま残っている CL**: その CL エージェントへ SendMessage で target の付け替えを指示する
   - **merged か closed の CL**: 手順 5 で書き残した worktree を片付ける。`git worktree unlock <worktree の絶対 path>` → `git worktree remove <worktree の絶対 path>` → その CL の branch と、worktree 作成時に作られた branch を `git branch -d` で消す。`-d` が「not fully merged」で拒否した branch (squash merge した CL や、closed の CL) は強制せず、残したことをユーザーに報告する。`git -C` で他の worktree を指すと、worktree に閉じたセッションでは拒否されるので、worktree は絶対 path で渡す

   SendMessage で CL エージェントが再開しないとき (このセッションが一度閉じられた後など) は、その CL の worktree に未 commit の変更が無いことを確かめて、worktree だけを片付ける (`git worktree unlock` → `git worktree remove`。CL の branch は残す)。そのうえで新しい CL エージェントを起動し、prompt には CL 計画の行・CL の番号・既存の branch 名・頼む作業を入れる。

   全 CL が merged か closed になったら終える。
