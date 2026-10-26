---
name: stacked-implement
disable-model-invocation: true
argument-hint: "<issue 番号… | 分割計画 file | 仕様> 例: 14-16 / 14 15 16 / plan.md"
description: 名指しした実装 (issue 集合・分割計画・単一の仕様) を CL 計画に分け、CL ごとに CL エージェントを並列に起動して実装させ、merge まで追従する。
---

# stacked-implement

このセッション (親セッション) は CL 計画を立て、CL 1 本につき CL エージェント (`swat-skills:cl-agent`) を 1 体、worktree 付きで並列に起動する。CL エージェント同士は担当範囲に沿って依頼し合い、決着しない事項と質問だけが親へ上がる。親はそれをユーザーに問う。

親は自分では実装しない — 実装している間は、CL エージェントからの依頼と質問の中継が止まる。merge するかと issue close はユーザーが決める。ユーザーが merge を明示に頼んだときだけ、親が手順 6 の推奨順で merge を代行し (source branch は消さない)、それ以外は merge の合図を待つ。親は merge の後に後始末 (target の付け替え・conflict 解消の指示・merge 済みの branch の削除・worktree の片付け) をする。

## args

| 形 | 計画の作り方 |
|---|---|
| issue 番号の範囲 (`14-16`) か列挙 (`14 15 16`) | 範囲は両端を含む連番へ展開する。手順 1 で対象に残った issue のうち、本文の `## 範囲` 節が同じ変更の範囲だけを挙げる issue 同士は、合わせて 1 CL で直せる大きさに収まる限り 1 CL にまとめ、残りは 1 issue = 1 CL にする — 実装の手順とレビュー工程の費用は CL ごとにかかり、差分の大きさにはほとんど比例しないので、同じ範囲を直す issue を別々の CL にすると、その費用を issue の数だけ払う。`## 範囲` 節の無い issue はまとめない |
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
| issue 参照 | 1 issue = 1 CL なら `Closes <issue 置き場>#<N>`、複数の issue をまとめた CL ならまとめた issue ごとに `Closes <issue 置き場>#<N>` を 1 行ずつ、1 issue を複数 CL に割ったなら全 CL に `Refs <issue 置き場>#<N>` (merge 順が読めないので close はユーザーに任せる)、issue が無ければ無し。`<issue 置き場>` は GitHub なら `owner/repo`、GitLab なら project の full path (`group/project`。subgroup があれば含む) で、置き場が CL の repo と同じでも付ける — 素の `#<N>` は CL の repo の番号として解決されるので、置き場が別 repo だと別の issue を指す |

## 最終報告の 1 行目

CL エージェントの最終報告の 1 行目は、次の 3 つの形のどれかにする。親はこの行で報告を振り分ける。この書式は手順 5 の起動 prompt で CL エージェントへ渡す。

| 形 | 意味 |
|---|---|
| `CL <名前> → <参照> <URL>` | CL を出した (`<参照>` は GitHub なら `#<番号>`、GitLab なら `!<番号>`) |
| `返事待ち <名前>: <待っている相手と用件>` | 親か他の CL エージェントの返事なしでは何も進まない。返事を SendMessage で送れば再開する |
| `見送り <名前>: <理由>` | 人手でしか進まない理由で CL を出せない。または、計画の修正で行が外れて作業を止めた |

## 手順

途中の報告 (手順 1・2 の除外、手順 4 の計画、手順 5 の起動と CL 到達・見送り) は、次の作業の最初の tool 呼び出しと同じメッセージに書く。turn を終えるのは、ユーザーへの質問、CL エージェントか triage の subagent の通知待ち、手順 6 の完了報告のときだけ。通知待ちで turn を終えても、CL エージェントの最終報告と SendMessage がこのセッションを再開させる。

1. **入力を読む。** issue は title と本文を、置き場を常に明示して引く (`gh issue view <N> -R <issue 置き場> --json title,body,state,url,blockedBy` / `glab issue view <N> -R <issue 置き場>`) — cwd 推論に任せると、cwd と置き場が別 repo の構成で同じ番号の別 issue を読む。issue 置き場は、呼び出し元が指定していればそれを使う。指定が無ければ対象 repo の `docs/agents/issue-tracker.md` の宣言に従う。宣言が別の file を正本として指すなら、その file が置き場として宣言する値を使う。指された file が無いか値を読み取れなければ、対象 repo 自身へ落とさず、止まって、指された file の path と読めなかった理由を挙げて置き場をユーザーに問う — 正本が別 repo を宣言していると、同じ番号の別の issue を読むことになる。`docs/agents/issue-tracker.md` が無いか置き場の宣言を読み取れなければ対象 repo 自身を置き場とする。対象 repo は cwd の repo、呼び出し元の指定は起動時の依頼文による置き場の repo の名指しを指す。closed の issue・PR の番号・読めない issue は対象から外し、どれに当たるかを付けて報告へ載せる。範囲の展開は PR の番号も拾い、`gh issue view` はそれも返す — url が `/pull/` を含めば PR として closed と分ける。file は Read する。

2. **依存を判定する。** 判定は次の順で、上が勝つ:
   - tracker の依存関係 (GitHub の `blockedBy`。GitLab は引かずに本文だけで判定する) か、本文の明示参照 (`Blocked by #N` / `Depends on #N` / 「#N に依存」) が正。明示参照の番号は、置き場付きの参照 (GitHub は `owner/repo#N`、GitLab は project の full path を前置した `group/project#N`) なら書かれた置き場、素の `#N` なら issue 置き場の番号として読む (tracker は issue 本文の参照をその issue のある repo で解決する)。依存先の状態は、読んだ置き場を `-R` に渡して `gh issue view <N> -R <置き場> --json state` / `glab issue view <N> -R <置き場>` で引く — 置き場付きの参照を issue 置き場で引くと、同じ番号の無関係な issue の状態で依存を判定する。置き場が issue 置き場と違う依存先は名指しの集合の外にあるので、下の「依存先が対象の外にある CL」の規則で扱う
   - 明示が無くても、後の CL が前の CL の成果 (書き換え後の file の内容・同じ振る舞い) を前提にすると読めたら依存にする。前の CL が他の CL に制約を課す (gate の追加・言語や依存の版上げ) なら、制約を受ける CL をその後続にする — 後続がその制約の上で作られ、CI でも検査される
   - どちらとも読めなければ独立とする

   依存先が対象の外にある CL (依存先が手順 1 で除外されたか、名指しの集合の外にある) は: 依存先が closed なら、積む先の branch が無いので独立として扱う。依存先が読めない (手順 1 の「読めない」での除外を含む) なら、後続ごと対象から外して報告へ載せる。依存先が名指しの集合の外で open なら、既定ブランチから独立として積み、未解決の依存として計画に載せる。依存先が複数ある CL は、1 つを分岐元と target にし、残りは merge で取り込む (target 以外の前段の差分がその CL に混ざることを計画に書く)。複数の issue を 1 CL にまとめた行の依存先は、まとめた issue それぞれの依存先を合わせたものにする (まとめた issue 同士の依存は CL の中で閉じる)。まとめた結果で行どうしの依存が循環するなら、その組はまとめずに 1 issue = 1 CL に戻す。まとめた issue の依存先が読めないなら、外すのはその issue とその後続だけにし、残りの issue で行を組み直す。

3. **CL 計画を組む。** 単一の仕様を分割するときは、各 CL が単独でレビューでき、単独で CI を通る単位に切る。続けて、CL 同士が同じ file を触る見込みを洗い出す。依存の無い CL 同士が同じ箇所 (登録リスト・設定の列挙・共有の型定義など、各 CL が末尾に書き足す場所) を触るなら、その受け口を用意する作業を根の CL (分岐の起点になる CL) の担当範囲へ移し、他の CL をその後続にする — 各 CL が別々に書き足すと、merge が 1 本進むたびに残りの CL が conflict する。

4. **計画を提示する。** 次を応答テキストに書き、同じメッセージで手順 5 の起動を始める。承認は待たない — CL エージェントが CL を出すのは実装とレビューを終えた後なので、ユーザーがそれまでに割り込めば、誤りは CL エージェントの branch (後続の前提として push した commit を含む) に止まり、CL にも既定ブランチにも出ない。提示はその割り込みのための唯一の面で、起動の tool 呼び出しと同じメッセージに置くことで書き落とさずに済む。
   - CL 計画の全行 (表)
   - 1 CL にまとめた issue の組と、まとめた根拠 (同じ範囲を挙げた `## 範囲` 節)
   - 依存の判定根拠
   - 同じ file を触る見込みと、手順 3 で受け口を根の CL へ移した箇所
   - 本文が「前段の merge 後に着手」と求めているのに、stack に積んで先に着手する依存 (該当 issue 番号つき)
   - 起動する CL エージェントの数

   起動後の計画の修正は、手順 5 の中継で受ける。

5. **CL エージェントを起動して中継する。** 依存先の無い行の CL エージェントを 1 つの message にまとめて起動する。Agent tool で `subagent_type: swat-skills:cl-agent` を指名し、`name` に行の名前、`isolation: "worktree"` を付ける。CL エージェントの最終報告は完了通知で届く。起動の戻りに出た worktree の絶対 path を、行の名前と並べて応答テキストに書く (手順 7 の片付けで使う。会話の圧縮や再開の後も読めるように残す)。

   prompt には次を入れる: CL 計画の全行、自分の行の名前、分岐元と target の branch 名 (依存先が複数なら、merge で取り込む残りの前段 branch も)、issue 参照の書き方、依頼の原文 (issue 本文 — 複数の issue をまとめた行ならその全部 — か、分割計画 file・仕様 file のうちその行に当たる部分を逐語で)、親の宛先 `main`、上の「最終報告の 1 行目」の表、後続の前提の知らせの書式 (1 行目を `後続の前提: <branch> <commit>` にする)。`swat-skills:cl-agent` が解決しないときは、起動せずにその旨と、swat-skills plugin の agent が読み込まれる状態にしてから呼び直す手順を報告して止まる — 汎用の agent では CL エージェントの手順が届かない。

   起動した後は、届いた通知ごとに次のとおり中継する。
   - **後続の前提の push**: 前段から後続の前提の知らせ (1 行目が `後続の前提:` で始まるもの) が届いたら、その前段に依存する行の CL エージェントを起動する。依存先が複数ある行は、全部の依存先から届いてから起動する。後続が起動済みなら、前段 branch の更新を取り込むよう後続へ SendMessage で伝える。前段のそれ以外の push は後続へ伝えない — 後続のレビューは前段 branch を base にして自分の差分だけを読むので、取り込まなくても範囲は変わらず、伝えるたびに後続が再開して費用が掛かる。取り込まなかった前段の更新との conflict は、前段の merge 後に手順 7 の「merged の CL を target にしている open の CL」と「open で conflict している CL」の項で解消させる
   - **届かなかった依頼**: 宛先の CL エージェントがまだ起動していなければ、その依頼を手元に持ち、起動するときの prompt に加える。終了していれば SendMessage で再開させて渡す
   - **どの担当範囲にも載らない変更**: 変更が既存の行 (CL を出した行を含む) のどれかと同じ理由で変わり、その行の変更と合わせて 1 CL に収まるなら、親がその行に含めると決め、依頼元と含める先の CL エージェントへ返す — 1 CL に収まるかは CL の自己完結の判定で決まるので、ユーザーに問うと中継が人待ちで止まる。含める先から、CL が merged か closed の行は除く (含めても変更は既定ブランチに届かない)。新しい行が要るとき、または含め方が設計の方針の選択になるときだけ、推奨を付けてユーザーに問う。行を足したとき、または既存の行の担当範囲を広げたときは、次項の計画の修正として扱う
   - **計画の修正** (ユーザーの割り込み、または前項で行を足したか既存の行の担当範囲を広げたとき): 直した計画を手順 4 の形で提示し直し、起動済みの全 CL エージェントへ SendMessage で送る。足した行は、依存先の条件を満たしていればこの手順の形で起動する。外した行は見送りとし、その CL エージェントに作業を止めるよう伝える。外した行に push 済みの branch や出した CL があれば、残すか消すかをユーザーに問う
   - **CL エージェント同士で決着しない事項と、CL エージェントからの質問**: 親は裁定しない。双方の言い分 (質問なら選択肢と CL エージェントの推奨) を要約し、親の推奨を付けて AskUserQuestion で問う。答えを関係する CL エージェントへ SendMessage で返す — 親の裁定はユーザーの目に触れないまま CL に固まる
   - **依頼の完了の知らせ**: 応答テキストの報告に載せるだけでよい
   - **最終報告**: 1 行目の形で振り分ける。`CL` の行は、先にその CL にレビュー出力が投稿されているかを CL のコメントで確かめる (`gh pr view <番号> -R <CL の repo> --json comments` / `glab mr view <番号> -R <CL の repo> --comments`)。`${CLAUDE_SKILL_DIR}/../../procedure/review-stage/SKILL.md` の手順 5 にある「観点が走った巡の印」の段落 (`観点が走った巡の印` の grep で引く) と、段落が指す前後の template を、最初の `CL` の行を振り分ける前に Read し (会話の圧縮や再開の後は読み直す。段落が見つからなければ記憶の書式で判定せず、grep のコマンドと結果を添えて AskUserQuestion で問う)、投稿の印を持つコメントがあれば投稿済みとする (投稿の印は review-stage の出力書式と一緒に変わるので、記憶でなく正本で当てる) — 最終報告の「レビュー出力を CL へ投稿したか」の欄は CL エージェント自身の申告なので、それだけでは CL エージェント定義の手順 2 (実装工程) の省略を見つけられない。投稿済みなら `CL` の行をそのまま応答テキストに書き写す。無ければ、CL を draft に戻し (`gh pr ready <番号> -R <CL の repo> --undo` / `glab mr update <番号> -R <CL の repo> --draft` — ready のまま置くと、レビューを通っていない CL が merge されうる)、その CL エージェントへ SendMessage で、CL エージェント定義の手順 2 (`swat-skills:playbook-implementation` の invoke と、その中の review-stage) を通し直すよう指示する。指示には、PR を出す箇所は既存の CL への push とし (新しい CL を出さない)、レビュー出力をその CL へ投稿して ready に戻してから最終報告を返し直すことを添える。その行は CL 到達に数えず、CL の URL と「レビュー出力の投稿待ち」を応答テキストに書く。返し直された `CL` の最終報告も同じく確かめ、それでも投稿が無ければ通し直しを重ねず、確かめたコマンドと結果を添えて、見送るか待つかを AskUserQuestion で問う。`返事待ち` は、待っている相手へ用件を渡すか、ユーザーへ問うかして、返事を SendMessage で返す。`見送り` は、その行と、それに依存する後続の行を見送りとし、理由を報告する。起動済みの後続を止めるかはユーザーに問う。他の行は止めない。ただし計画の修正で外した行の `見送り` は後続へ広げない (後続の扱いは直した計画が決めている)。報告にある push 済みの branch と CL を、計画の修正の項の問いに使う
   - **起票された follow-up issue** (CL エージェントが `<issue 置き場>#<N>` で知らせてくる): `swat-skills:issue-triage` を invoke する background の subagent に、届いた issue を置き場ごとにまとめて渡す — 親が自分で triage する間は中継が止まる。`ready-for-agent` になった issue は、「どの担当範囲にも載らない変更」の項と同じ判定で既存の行に含めるか、手順 1〜3 で新しい行にして計画に足し、どちらも計画の修正として扱う。含める先から、CL が merged か closed の行は除く (含めても issue は閉じない)。既存の行に含めたときは、その行の issue 参照に `Closes <issue 置き場>#<N>` を 1 行足し、含めた先の CL エージェントへ「担当範囲への依頼」として issue 本文を逐語で、`Closes <issue 置き場>#<N>` と並べて渡し、実装させて CL の説明文にその参照を足させる — 説明文の issue 参照は起動した側が渡したものを書くので、含めると決めた親が渡す。その依頼の完了の知らせが届いたら、「最終報告」の項と同じ確かめ方で、依頼を渡した後に投稿された、投稿の印を持つコメントがあるかを確かめる (依頼の前からあるコメントは数えない)。無ければ、依頼分の範囲で手順 2 を通し直し、レビュー出力を投稿するよう、その CL エージェントへ伝える。それ以外の結果は label を動かさず、返ったブロックを逐語で応答テキストに写す。停止理由が計画の CL を前提に挙げた issue は、issue と CL の名前を対にして応答テキストに書く (手順 7 で使う。会話の圧縮や再開の後も読めるように残す)

6. **完了報告。** 全行が CL 到達か見送りになったら:
   - 全行を `CL <名前> → <参照> <URL>` の形で列挙し、stack 構造 (どの CL がどの branch を target にしているか) を添える
   - merge の推奨順: 前段から順に。依存の無い CL 同士では、他の CL と同じ file を触る数が少ないものから
   - 見送った行とその理由
   - ユーザーに残る作業: merge したら、親へ「<CL> を merge した」と一言伝える。親に merge を頼んでもよい (親が推奨順で代行する)。CL は source branch を消さずに merge する (`gh pr merge` に `--delete-branch` を付けない・GitLab は「source branch を削除」を外す)。merge を伴わない branch 削除もしない。branch は手順 7 で親が後続の target を付け替えてから消す — 付け替えより先に消すと、後続の CL は close されうる

7. **merge に追従する。** ユーザーから merge の合図が届くたびと、親が代行した merge を 1 本終えるたびに、全 CL の状態を引く (`gh pr view <番号> -R <CL の repo> --json state,baseRefName,mergeable` / `glab mr view <番号> -R <CL の repo>`)。
   - **親が代行する merge** (ユーザーが merge を頼んだとき): 頼まれた CL を手順 6 の推奨順に 1 本ずつ merge する。頼まれた時点で、CL ごとに head の SHA を控える (`gh pr view <番号> -R <CL の repo> --json headRefOid` / `glab mr view <番号> -R <CL の repo>` の SHA)。GitHub は `gh pr merge <番号> -R <CL の repo> --match-head-commit <控えた SHA>` (`--delete-branch` を付けない)、GitLab は MR に「source branch を削除」が付いていれば `glab mr update <番号> -R <CL の repo> --remove-source-branch` (付け外しを切り替える) で外してから `glab mr merge <番号> -R <CL の repo> --sha <控えた SHA>` — source branch を消すと、後続の CL は close されうる。次の CL を merge するのは、1 本ごとにこの手順の項を当て、状態を引き直して、その CL の target が既定ブランチ (か、merged の CL の target) へ付け替え済みで、`mergeable` が conflict なしと確定してからにする — 付け替える前に後続を merge すると、変更は merged の前段 branch に入るだけで既定ブランチに届かないのに、CL は merged と表示される。頼まれた順が依存に反するとき (前段より先に後続を頼まれたとき) は merge せず、推奨順を添えてユーザーに問う。merge の直前に head の SHA を引き直し、控えた SHA から commit が増えていたら、増えた commit がどれも、片方の親が現在の target の remote branch に含まれる (tip か祖先の) merge commit (base を取り込み、conflict があれば解消したもの) なら問わずに続けて控えを更新し、それ以外の commit が 1 つでもあれば (CL 自身の変更が増えた) その CL は merge せず、増えた差分を添えてユーザーに問い直す — conflict の解消は merge の決定を実行するのに要る作業で決定の中身を変えないが、CL 自身の変更は人が読んでいない差分なので。問い直した CL の後続は答えが届くまで merge せず、依存の無い残りの CL は推奨順のまま続ける
   - **open で conflict している CL**: その CL エージェントへ SendMessage で「既定ブランチへ merge が入ったので conflict を解消する」よう伝え、`${CLAUDE_SKILL_DIR}/../../procedure/playbook-conflict-resolution/SKILL.md` の絶対 path と、CL の番号・head (その CL の branch)・base (その CL の現在の target) を添える
   - **merged の CL を target にしていて closed になった CL** (ユーザーが先に branch を消した): 下の片付けより先に当てる。merged の CL の head の commit を引き (`gh pr view <番号> -R <CL の repo> --json headRefOid` / `glab mr view <番号> -R <CL の repo>` の SHA)、その commit で branch を push し直し (`git push <remote> <SHA>:refs/heads/<branch>`)、closed の CL を reopen して (`gh pr reopen <番号> -R <CL の repo>` / `glab mr reopen <番号> -R <CL の repo>`)、次の項の付け替えに回す
   - **merged の CL を target にしている open の CL**: 親が、merged の CL の target (`baseRefName`) へ付け替える (`gh pr edit <番号> -R <CL の repo> --base <付け替え先>` / `glab mr update <番号> -R <CL の repo> --target-branch <付け替え先>`)。CL エージェントは起こさない — 付け替えは 1 コマンドで CL エージェントの文脈を使わず、起こすと待ちと費用だけが増える。付け替えたら、手元の CL 計画のその行の target を付け替え先へ直し (分岐元は起動時の値のまま残し、付け替えた後の target は依存先から導き直さない)、状態を引き直して、conflict していれば「open で conflict している CL」の項に回す。計画の修正を送るときは直した target を送る (付け替えの知らせは送らない) — 古い target を送ると、CL エージェントが merged の前段 branch へ付け替え直す
   - **merged の CL の source branch**: その branch を target にしている open の CL が残っていなければ remote から消す (`git push <remote> --delete <branch>`)。付け替えより先に消すと、後続の CL は close されうる
   - **merged か closed の CL**: 手順 5 で書き残した worktree を片付ける。`git worktree unlock <worktree の絶対 path>` → `git worktree remove <worktree の絶対 path>` → その CL の branch と、worktree 作成時に作られた branch を `git branch -d` で消す。`-d` が「not fully merged」で拒否した branch (squash merge した CL や、closed の CL) は強制せず、残したことをユーザーに報告する。`git -C` で他の worktree を指すと、worktree に閉じたセッションでは拒否されるので、worktree は絶対 path で渡す
   - **既定ブランチへ merged になった CL を前提に、triage で停止した follow-up issue**: 手順 5 の follow-up issue の項と同じく triage にかけ直す

   SendMessage で CL エージェントが再開しないとき (このセッションが一度閉じられた後など) は、その CL の worktree に未 commit の変更が無いことを確かめて、worktree だけを片付ける (`git worktree unlock` → `git worktree remove`。CL の branch は残す)。そのうえで新しい CL エージェントを起動し、prompt には CL 計画の行・CL の番号・既存の branch 名・頼む作業と、手順 5 の後続の前提の知らせの書式を入れる。

   全 CL が merged か closed になり、triage の subagent が残っていなければ終える。
