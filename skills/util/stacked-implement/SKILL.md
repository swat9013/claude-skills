---
name: stacked-implement
disable-model-invocation: true
argument-hint: "<issue 番号… | 分割計画 file | 仕様> 例: 14-16 / 14 15 16 / plan.md"
description: 名指しした実装 (issue 集合・分割計画・単一の仕様) を CL 計画に分け、CL ごとに CL エージェントを並列に起動して実装させ、merge まで追従する。
---

# stacked-implement

このセッション (親セッション) は CL 計画を立て、CL 1 本につき CL エージェント (`swat-skills:cl-agent`) を 1 体、worktree 付きで並列に起動する。CL エージェント同士は担当範囲に沿って依頼し合い、AFK 境界の内側の判断は CL エージェントが自分で決める。判断について親へ上がるのは、自律で下した判断の知らせと、決着しない事項と境界の外の質問で、親は後の 2 つを改めて AFK 境界の内側か外側かに分け、内側なら裁定し、外側ならユーザーに問う。

親は自分では実装しない — 実装している間は、CL エージェントからの依頼と質問の中継が止まる。merge するかと issue close はユーザーが決める。ユーザーが merge を明示に頼んだときだけ、親が手順 6 の推奨順で merge を代行し (source branch の扱いは手順 6・7)、それ以外は merge の合図を待つ。親は merge の後に後始末 (target の付け替え・conflict 解消の指示・merge 済みの branch の削除・worktree の片付け) をする。

## args

| 形 | 計画の作り方 |
|---|---|
| issue 番号の範囲 (`14-16`) か列挙 (`14 15 16`) | `${CLAUDE_SKILL_DIR}/scripts/read-issues.py expand <args>` で番号の列へ展開する (入出力と exit code は `--help` が正本)。手順 1 で対象に残った issue のうち、本文の課題と受け入れ条件から見込んだ変更先が同じ file か、一緒に保守するまとまり (skill のディレクトリなど) の issue 同士は、合わせて 1 CL で直せる大きさに収まる限り 1 CL にまとめ、残りは 1 issue = 1 CL にする — 実装の手順とレビュー工程の費用には差分の大きさによらず CL ごとにかかる固定分があるので、同じ変更先を直す issue を別々の CL にすると、その固定分を issue の数だけ払う。変更先を見込めない issue はまとめない |
| 分割計画の file (CL ごとに題・担当範囲・依存を書いたもの) | 書かれたとおりに CL 計画へ写す |
| 単一の仕様 (分割を求められた issue 1 件、または計画の形をしていない仕様 file) | 親が CL への分割案を作る |

## CL 計画

CL 計画の 1 行は次の欄を持つ。CL エージェントは自分の行だけでなく全行を受け取り、担当範囲の外の変更を誰に依頼するかを全行から決める。

| 欄 | 中身 |
|---|---|
| 名前 | `cl-<識別子>` (字の規則は `cl-plan.py build` が検査する) — そのまま Agent tool の `name` と SendMessage の宛先になる |
| branch | この CL の branch 名。親が計画の段階で決める (後続の分岐元と target になるので、起動前に確定している必要がある) |
| 担当範囲 | この CL が何を変え、何を変えないかを文章で書く。ある変更をどの CL に載せるかは、この記述に照らして決まる |
| 依存先 | 前提にする CL の名前 (無ければ空) |
| 後続の前提 | この CL を依存先にする行が、この CL の何を前提にするか。前段の CL エージェントは、これを commit して push した時点で親へ知らせる |
| 分岐元 / target | `cl-plan.py build` が依存先から導く (`base_branch` と `target`。取り込む残りの前段は `merge_in`) |
| issue 参照 | `cl-plan.py build` が組み立てる (`issue_refs`。1 issue を複数 CL に割ったときの `Refs` は、merge 順が読めないので close をユーザーに任せる) |

## 最終報告の 1 行目

CL エージェントの最終報告の 1 行目は、次の 3 つの形のどれかにする。親はこの行で報告を振り分ける。

| 形 | 意味 |
|---|---|
| `CL <名前> → <参照> <URL>` | CL を出した (`<参照>` は GitHub なら `#<番号>`、GitLab なら `!<番号>`) |
| `返事待ち <名前>: <待っている相手と用件>` | 親か他の CL エージェントの返事なしでは何も進まない。返事を SendMessage で送れば再開する |
| `見送り <名前>: <理由>` | 人手でしか進まない理由で CL を出せない。または、計画の修正で行が外れて作業を止めた |

## 手順

途中の報告 (手順 1・3 の除外、手順 4 の計画、手順 5 の起動と CL 到達・見送り) は、次の作業の最初の tool 呼び出しと同じメッセージに書く。turn を終えるのは、ユーザーへの質問、CL エージェントか triage の subagent の通知待ち、手順 6 の完了報告のときだけ。通知待ちで turn を終えても、CL エージェントの最終報告と SendMessage がこのセッションを再開させる。

1. **入力を読む。** issue は、置き場を常に明示して `${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/scripts/issue-fetch.py` の `command --tracker <gh|glab> --repo <issue 置き場> <N>...` (gh は `--with-blocked-by` を付ける。`--tracker` は issue 置き場の tracker) が出すコマンドで引く — cwd 推論に任せると、cwd と置き場が別 repo の構成で同じ番号の別 issue を読む。issue 置き場は `${CLAUDE_SKILL_DIR}/../../knowledge/issue-tracker-operations/references/placement.md` を Read して決める。同書の呼び出し元の指定は、起動時の依頼文による置き場の repo の名指しを指す。同書が止まるとしたときは、同書が渡すとしたものを挙げて置き場をユーザーに問う。この skill が保存する file (取得結果・行の組・計画の記録・CL の状態) は作業ツリーの外に置く — 中に置くと、未追跡の file として commit に混ざりうる。取得結果は issue ごとに file へ保存し、同じ script の `read <取得結果の file> --number <N>` の stdout を別の file に保存する (取得に失敗したか `read` が exit 2 で終えた issue は空の file になる)。その file を `${CLAUDE_SKILL_DIR}/scripts/read-issues.py classify --repo <issue 置き場> <N>=<file>...` で分ける。`target` 以外 (`closed`・`pr`・`unreadable`) は対象から外し、どれに当たるかを付けて報告へ載せる。tracker が gh でも glab でもないときは、これらの script (issue-fetch.py と、classify・手順 2 の deps) を使わず、対象 repo の issue の規範が決める取得の出力を読んで、同じ区分と依存先を決める — issue-fetch.py は gh と glab の出力の形しか読めない。区分の順と依存先の抜き出し方は `read-issues.py --help` の `classify` と `deps` の項に従う。決めた依存先と状態は、`read-issues.py deps` の出力の形 (`issues`・`states`) の file に書いて手順 3 の `--deps` に渡す。この skill の script の `--repo` と行の組の `repo` には、同書の「置き場をまたぐ参照の書き方」で綴った issue 置き場を渡す — その値を casefold したもの (大文字小文字をそろえた綴り) が issue 参照の接頭辞になる。

2. **依存を判定する。** 判定は次の順で、上が勝つ:
   - tracker の依存関係か本文の明示参照が正。`${CLAUDE_SKILL_DIR}/scripts/read-issues.py deps --repo <issue 置き場> --targets <対象の N,...> <参照>=<file>...` が、手順 1 の取得結果から依存先を置き場付きの参照で抜き出す (抜き出す形は `--help` が正本)。出力の `unknown` に挙がった参照は、その置き場を `--repo` に、その置き場の tracker を `--tracker` に渡して手順 1 と同じく issue-fetch.py の `command` で引いて `read` に通し、file を `<置き場>#<N>=<file>` で足して呼び直す (`unknown` が空になるまで)。`unknown` が空になった出力を file に保存する (手順 3 で使う)。置き場付きの参照を issue 置き場で引くと、同じ番号の無関係な issue の状態で依存を判定する
   - 明示が無くても、後の CL が前の CL の成果 (書き換え後の file の内容・同じ振る舞い) を前提にすると読めたら依存にする。前の CL が他の CL に制約を課す (gate の追加・言語や依存の版上げ) なら、制約を受ける CL をその後続にする — 後続がその制約の上で作られ、CI でも検査される
   - どちらとも読めなければ独立とする

   依存先が複数ある CL は、1 つを分岐元と target にし (行の組の `base`)、残りは merge で取り込む (target 以外の前段の差分がその CL に混ざることを計画に書く)。依存先が対象の外にある CL・まとめた行の依存先・まとめた結果の循環は、手順 3 の `cl-plan.py build` が扱う。

3. **CL 計画を組む。** 単一の仕様を分割するときは、各 CL が単独でレビューでき、単独で CI を通る単位に切る。続けて、CL 同士が同じ file を触る見込みを洗い出す。依存の無い CL 同士が同じ箇所 (登録リスト・設定の列挙・共有の型定義など、各 CL が末尾に書き足す場所) を触るなら、その受け口を用意する作業を根の CL (分岐の起点になる CL) の担当範囲へ移し、他の CL をその後続にする — 各 CL が別々に書き足すと、merge が 1 本進むたびに残りの CL が conflict する。

   決めた行の組 (名前・branch・担当範囲・後続の前提・まとめた issue・親が決めた依存先 (手順 2 の暗黙の依存と、上の受け口のために足した依存)・`base`・触る見込みの file) を JSON の file に書く。1 issue を複数 CL に割るなら、その issue を割った全部の行に書く — 1 行だけに書くと `Closes` になり、残りの CL が merge される前に issue が閉じる。続けて `${CLAUDE_SKILL_DIR}/scripts/cl-plan.py build <行の組の file> --deps <手順 2 の deps の出力の file>` (issue の無い計画では `--deps` を省く) で CL 計画を導く (入力の形・導く欄・exit code は `--help` が正本)。出力に応じて次のとおり進める。
   - `cycles`: `split` の行を 1 issue = 1 CL に戻して呼び直す。`split` が空の組は、親が足した依存が循環を作ったならその依存を外して呼び直し、issue の明示の依存どうしの循環なら、組を挙げてユーザーに問う
   - `errors` と exit 2: 挙がった理由どおりに入力を直して呼び直す
   - `excluded` と `removed_rows`: 報告へ載せる。`excluded` の issue は、その issue が載っていた行の担当範囲と、手順 5 の prompt に入れる依頼の原文から外す
   - `unresolved`: 未解決の依存として計画に載せる

   出力を計画の記録として file に保存し、その path を応答テキストに書く (会話の圧縮や再開の後も読めるように残す)。以後の計画の修正と target の付け替えは、この記録の行を直す。行を足すときは、行の組に足して build を呼び直し、出力の足した行だけを記録へ加える。外した行も記録から消さない — CL を出した行は手順 7 でも引く。

4. **計画を提示する。** 次を応答テキストに書き、同じメッセージで手順 5 の起動を始める。承認は待たない — CL エージェントが CL を出すのは実装とレビューを終えた後なので、ユーザーがそれまでに割り込めば、誤りは CL エージェントの branch (後続の前提として push した commit を含む) に止まり、CL にも既定ブランチにも出ない。提示はその割り込みのための唯一の面で、起動の tool 呼び出しと同じメッセージに置くことで書き落とさずに済む。
   - CL 計画の全行 (表)
   - 1 CL にまとめた issue の組と、まとめた根拠 (見込んだ変更先)
   - 依存の判定根拠
   - 同じ file を触る見込みと、手順 3 で受け口を根の CL へ移した箇所
   - 本文が「前段の merge 後に着手」と求めているのに、stack に積んで先に着手する依存 (該当 issue 番号つき)
   - 起動する CL エージェントの数

5. **CL エージェントを起動して中継する。** 依存先の無い行の CL エージェントを 1 つの message にまとめて起動する。Agent tool で `subagent_type: swat-skills:cl-agent` を指名し、`name` に行の名前を入れて `isolation: "worktree"` を付ける。外れた呼び出しには Agent の PreToolUse hook が案内を返す。CL エージェントの最終報告は完了通知で届く。起動の戻りに出た worktree の絶対 path を、行の名前と並べて応答テキストに書く (手順 7 の片付けで使う。会話の圧縮や再開の後も読めるように残す)。

   prompt には次を入れる: CL 計画の全行、自分の行の名前、分岐元と target の branch 名 (依存先が複数なら、merge で取り込む残りの前段 branch も)、issue 参照の書き方、依頼の原文 (issue 本文 — 複数の issue をまとめた行ならその全部 — か、分割計画 file・仕様 file のうちその行に当たる部分を逐語で)、親の宛先 `main`、上の「最終報告の 1 行目」の表、後続の前提の知らせの書式 (1 行目を `後続の前提: <branch> <commit>` にする)、自律で下した判断の知らせの書式 (1 行目を `判断: <要約>` にする)。`swat-skills:cl-agent` が解決しないときは、起動せずにその旨と、swat-skills plugin の agent が読み込まれる状態にしてから呼び直す手順を報告して止まる — 汎用の agent では CL エージェントの手順が届かない。

   起動した後は、届いた通知ごとに次のとおり中継する。
   - **後続の前提の push**: 前段から後続の前提の知らせ (1 行目が `後続の前提:` で始まるもの) が届いたら、その前段に依存する行の CL エージェントを起動する。依存先が複数ある行は、全部の依存先から届いてから起動する。後続が起動済みなら、前段 branch の更新を取り込むよう後続へ SendMessage で伝える。前段のそれ以外の push は後続へ伝えない — 後続のレビューは前段 branch を base にして自分の差分だけを読むので、取り込まなくても範囲は変わらず、伝えるたびに後続が再開して費用が掛かる。取り込まなかった前段の更新との conflict は、前段の merge 後に手順 7 の「merged の CL を target にしている open の CL」と「open で conflict している CL」の項で解消させる
   - **届かなかった依頼**: 宛先の CL エージェントがまだ起動していなければ、その依頼を手元に持ち、起動するときの prompt に加える。終了していれば SendMessage で再開させて渡す
   - **どの担当範囲にも載らない変更**: 変更が既存の行 (CL を出した行を含む) のどれかと同じ理由で変わり、その行の変更と合わせて 1 CL に収まるなら、親がその行に含めると決め、依頼元と含める先の CL エージェントへ返し、含める判断と理由を含める先の CL の説明文に残させ、応答テキストの報告にも載せる — 1 CL に収まるかは CL の自己完結の判定で決まるので、ユーザーに問うと中継が人待ちで止まる。含める先から、CL が merged か closed の行は除く (含めても変更は既定ブランチに届かない)。新しい行が要るときは、親が行を足す (行の切り方も CL の自己完結の判定で決まる)。含め方が設計の方針の選択になるときは、下の「CL エージェント同士で決着しない事項と、CL エージェントからの質問」の項が Read させる leaf 2 本で AFK 境界の内側か外側かを分け、外側のときだけ推奨を付けてユーザーに問う。行を足したとき、または既存の行の担当範囲を広げたときは、次項の計画の修正として扱う
   - **計画の修正** (ユーザーの割り込みのほか、他の項が計画の行を足す・外す・直すとき): 直した計画を手順 4 の形で提示し直し、起動済みの全 CL エージェントへ SendMessage で送る。足した行と、依存先を直した未起動の行は、依存先の条件を満たしていればこの手順の形で起動する。外した行は見送りとし、その CL エージェントに作業を止めるよう伝える。その `見送り` の報告に未 commit の変更・push 済みの branch・出した CL のどれかがあれば、それぞれの有無を挙げて残すか消すかをユーザーに問う — 作業ツリーにだけある変更は、消すと戻らない

     行を外す計画では、提示の前に、計画に残す後続 (外す行を依存先にしている行のうち見送りでない行) を `${CLAUDE_SKILL_DIR}/references/remove-row.md` (後続の直し方) を Read して (会話の圧縮や再開の後は読み直す)そのとおり直す — 依存先が外す行のまま残ると、後続は起動条件を満たせず、手順 6 へ進めない。
   - **CL エージェント同士で決着しない事項と、CL エージェントからの質問**: `${CLAUDE_SKILL_DIR}/../../knowledge/principle-index/references/leaves/principle-ai-delegation-boundary.md` と、同じディレクトリの `principle-design-conformance.md` (設計 doc の記述を変える判断を外側に分けるため) を Read し (会話の圧縮や再開の後は読み直す)、AFK 境界の内側か外側かを分ける。内側なら親が裁定し、裁定と理由を関係する CL エージェントへ SendMessage で返して CL の説明文に残させ、応答テキストの報告にも載せる — 人は merge の前に CL を読み、そこで覆せるので、内側の判断をユーザーに問うと中継が人待ちで止まるだけになる。外側なら、双方の言い分 (質問なら選択肢と CL エージェントの推奨) を要約し、親の推奨を付けて AskUserQuestion で問い、答えを関係する CL エージェントへ SendMessage で返す
   - **依頼の完了の知らせと、自律で下した判断の知らせ** (1 行目が `判断:` で始まるもの): 応答テキストの報告に載せるだけでよい
   - **最終報告**: 1 行目の形で振り分ける。`CL` の行は、先にその CL にレビュー出力が投稿されているかを CL のコメントで確かめる (`gh pr view <番号> -R <CL の repo> --json comments` / `glab mr view <番号> -R <CL の repo> --comments`)。`${CLAUDE_SKILL_DIR}/../../procedure/review-stage/SKILL.md` の手順 5 にある「観点が走った巡の印」の段落 (`観点が走った巡の印` の grep で引く) と、段落が指す前後の template を、最初の `CL` の行を振り分ける前に Read し (会話の圧縮や再開の後は読み直す。段落が見つからなければ記憶の書式で判定せず、grep のコマンドと結果を添えて AskUserQuestion で問う)、投稿の印を持つコメントがあれば投稿済みとする (投稿の印は review-stage の出力書式と一緒に変わるので、記憶でなく正本で当てる)。target の付け替えで通し直しを指示した CL は、`${CLAUDE_SKILL_DIR}/../../procedure/review-stage/references/merge-check.md` の「target との分岐点がずれている」の項目 (同書の fetch と巡の呼び方を含む) に当たらないときだけ投稿済みとする — 付け替えの前の出力は前段の branch を base にしていて、増えた差分を通っていない — 最終報告の「レビュー出力を CL へ投稿したか」の欄は CL エージェント自身の申告なので、それだけでは CL エージェント定義の手順 2 (実装工程) の省略を見つけられない。投稿済みなら `CL` の行をそのまま応答テキストに書き写す。無ければ、CL を draft に戻し (`gh pr ready <番号> -R <CL の repo> --undo` / `glab mr update <番号> -R <CL の repo> --draft` — ready のまま置くと、レビューを通っていない CL が merge されうる)、その CL エージェントへ SendMessage で、CL エージェント定義の手順 2 (`swat-skills:playbook-implementation` の invoke と、その中の review-stage) を通し直すよう指示する。指示には、PR を出す箇所は既存の CL への push とし、レビュー出力をその CL へ投稿して ready に戻してから最終報告を返し直すことを添える。その行は CL 到達に数えず、CL の URL と「レビュー出力の投稿待ち」を応答テキストに書く。返し直された `CL` の最終報告も同じく確かめ、それでも投稿が無ければ通し直しを重ねず、確かめたコマンドと結果を添えて、見送るか待つかを AskUserQuestion で問う。`返事待ち` は、待っている相手へ用件を渡すか、ユーザーへ問うかして、返事を SendMessage で返す。`見送り` は、その行を見送りとし、理由と、報告の 2 行目以降をそのまま報告する。計画の修正で外した行の `見送り` は後続へ広げず (後続の扱いは直した計画が決めている)、報告を計画の修正の項の問いに使う。それ以外の `見送り` の branch と worktree は親が消さず、手順 6 で人へ渡す — 人が続きを引き継ぐ先なので。それ以外の `見送り` は、その行を依存先にしている後続へ次のとおり広げ、依存しない行は止めない。
     - 未起動の行: 見送りとし、その後続へも同じ扱いを広げる
     - 起動済みの行 (CL が merged か closed の行を除く): 止めるかをユーザーに問う。止めないと見送った行の commit が後続の CL に載りうることと、直した計画のどの行も前提を満たさないなら成果を後続の担当範囲へ移すことを、問いに添える。「止める」なら、計画の修正でその行を外す。「止めない」なら、その行は計画に残して CL 到達か見送りまで進め、元の `見送り` をこの行の後続へ広げない。どちらの答えでも、見送った行を外す行として、計画の修正の項の後続の直し方を当てる
   - **起票された follow-up issue** (CL エージェントが `<issue 置き場>#<N>` で知らせてくる): `${CLAUDE_SKILL_DIR}/references/follow-up-issue.md` を Read し (会話の圧縮や再開の後は読み直す)、その手順で triage にかけ、結果に応じて計画へ含める

6. **完了報告。** 全行が CL 到達か見送りになったら、CL の repo が GitHub なら merge で head branch を自動で消す設定の出力を file に保存し (取得のコマンドは `${CLAUDE_SKILL_DIR}/scripts/cl-state.py command --auto-delete --repo <CL の repo>` が出す)、`${CLAUDE_SKILL_DIR}/scripts/cl-state.py auto-delete <file>` で読む — `確かめられなかった` は有効として扱う (無効と扱うと、実際は有効な repo でユーザーに守れない指示を出す)。GitLab は設定を引かない (project の設定は MR ごとの既定を変えるだけで、MR ごとに外せる)。引いた結果 (有効 / 無効 / 確かめられなかった) を応答テキストに書く (会話の圧縮や再開の後も読めるように残す)。続けて次を報告する:
   - CL に到達した行を `CL <名前> → <参照> <URL>` の形で列挙し、stack 構造 (どの CL がどの branch を target にしているか) を添える
   - merge の推奨順: `${CLAUDE_SKILL_DIR}/scripts/cl-plan.py check-merge-order <計画の記録> <CL に到達した行の名前>...` が出す `recommended` (ここでは `violations` と exit 1 は見ない)
   - 見送った行とその理由。報告の 2 行目以降があれば、そのまま添える
   - ユーザーに残る作業: merge したら、親へ「<CL> を merge した」と一言伝える。親に merge を頼んでもよい (親が推奨順で代行する)。merge を伴わない branch 削除はしない。merge で source branch を消すかは、上で引いた自動削除の設定で分ける:
     - 自動削除が無効な GitHub の repo と GitLab: CL は source branch を消さずに merge する (`gh pr merge` に `--delete-branch` を付けない・GitLab は MR の「source branch を削除」を外す)。branch は手順 7 で親が後続の target を付け替えてから消す — 付け替えより先に消すと、後続の CL は close されうる
     - 自動削除が有効な GitHub の repo: merge の時点で GitHub が source branch を消すので、消さずに merge するよう求めない (後続の扱いは手順 7)

7. **merge に追従する。** ユーザーから merge の合図が届くたびと、親が代行した merge を 1 本終えるたびに、全 CL の状態を CL ごとに file へ引き (取得のコマンドは `${CLAUDE_SKILL_DIR}/scripts/cl-state.py command --tracker <gh|glab> --repo <CL の repo> <番号>...` が出す)、`${CLAUDE_SKILL_DIR}/scripts/cl-state.py classify <計画の記録> <file>...` で区分に分ける (区分の鍵と判定は `--help` が正本)。CL の tracker が gh でも glab でもないときは、cl-state.py を使わず、tracker の取得の出力を読み、`cl-state.py --help` の `classify` の項が挙げる鍵をすべて、同じ判定で CL ごとに決める。下の各項は、その項の鍵が true か値を持つ CL に当てる。`conflict` が null (未確定) の CL は、時間を置いて引き直し、確定するまで conflict の項にも merge の判断にも進めない。merge で source branch が消えたとき (GitHub の自動削除が有効な repo、GitLab で「source branch を削除」が付いたまま merge された MR) は、後続の付け替えを tracker の自動の付け替えに任せる — 自動の付け替えが届くのは、GitHub では同じ repo の open の PR、GitLab では merge と同時に消した source branch を target にしていた open の MR の 4 本までに限る。`auto_retargeted_to` が出た CL は、計画の記録のその行の `target` をその値へ直し、付け替えの後の確かめを当てる (直し方と確かめ方と理由は下の「merged の CL を target にしている open の CL」の項と同じ)。付け替わらなかった CL には `retarget_to` か `recover` が出るので、その項で扱う
   - **親が代行する merge** (ユーザーが merge を頼んだとき): merge の前に `${CLAUDE_SKILL_DIR}/references/delegated-merge.md` を Read し (会話の圧縮や再開の後は読み直す)、その手順で頼まれた CL を 1 本ずつ merge する
   - **open で conflict している CL** (`conflict`): その CL エージェントへ SendMessage で「既定ブランチへ merge が入ったので conflict を解消する」よう伝え、`${CLAUDE_SKILL_DIR}/../../procedure/playbook-conflict-resolution/SKILL.md` の絶対 path と、CL の番号・head (その CL の branch)・base (その CL の現在の target) を添える
   - **merged の CL を target にしていて closed になった CL** (`recover`。付け替えより先に branch が消えた: ユーザーが消したか、merge で消えて自動の付け替えが効かなかった): 下の片付けより先に当てる。`${CLAUDE_SKILL_DIR}/references/recover-closed-cl.md` を Read し (会話の圧縮や再開の後は読み直す)、その手順で merged の CL の source branch を push し直して closed の CL を reopen し、次の項の付け替えに回す。失敗してユーザーに問うたら、答えが届くまで、その CL と source branch に下の項を当てず、完了にも数えない
   - **merged の CL を target にしている open の CL** (`retarget_to`): 親が `retarget_to` の branch へ付け替える (`gh pr edit <番号> -R <CL の repo> --base <付け替え先>` / `glab mr update <番号> -R <CL の repo> --target-branch <付け替え先>`)。CL エージェントは起こさない — 付け替えは 1 コマンドで CL エージェントの文脈を使わず、起こすと待ちと費用だけが増える。付け替えたら、計画の記録のその行の `target` を付け替え先へ直し (`base_branch` は起動時の値のまま残し、付け替えた後の target は依存先から導き直さない)、状態を引き直して、conflict していれば「open で conflict している CL」の項に回す。続けて `${CLAUDE_SKILL_DIR}/../../procedure/review-stage/references/merge-check.md` の「target との分岐点がずれている」の項目を、同書の fetch と巡の呼び方を含めてその CL に当て、当たれば (前段が squash で merge されたときなど、付け替えで差分が増えた)、CL を draft に戻し (`gh pr ready <番号> -R <CL の repo> --undo` / `glab mr update <番号> -R <CL の repo> --draft`)、その CL エージェントへ CL エージェント定義の「手順 2 の通し直し」を、保存したレビュー出力を使わずに全差分で走らせ直すよう指示する。その CL は、「最終報告」の項で付け替えの後のレビュー出力を確かめるまで CL 到達に数えず、merge の判断へ進めない。計画の修正を送るときは直した target を送る (付け替えの知らせは送らない) — 古い target を送ると、CL エージェントが merged の前段 branch へ付け替え直す
   - **merged か closed の CL の後片付け** (remote の source branch・worktree・ローカルの branch): CL ごとに `${CLAUDE_SKILL_DIR}/scripts/cleanup-merged-cl.sh [--keep-remote-branch] <CL の branch> <手順 5 で書き残した worktree の絶対 path>` を、worktree の外の cwd (通常は main checkout) から呼ぶ。実行する規則 (消す順・`-d` が拒んだ branch の確かめ方・出力・exit code) は `--help` が正本で、ここには写さない。`--keep-remote-branch` なしで呼ぶのは `cleanable` が true の CL だけにする — script は既定で remote の source branch を消し、付け替えより先に消すと後続の CL は close されうる。merge の完了は script が remote で確かめ、既定ブランチに含まれない branch があれば何も消さずに exit 1 で返す。`open_dependents` か `recover_dependents` が残っていて付け替え前なら `--keep-remote-branch` を付けて呼び、付け替えてから状態を引き直し、`cleanable` が true になったら `--keep-remote-branch` なしでもう一度呼ぶ。merge されずに closed になった CL にも `--keep-remote-branch` を付ける。exit 1 で「残した」と報告された branch は、別のコマンドで消さずに残し、理由とともにユーザーに報告する。exit 3 (worktree に未 commit の変更がある) は何も消えていないので、変更の内容をユーザーに報告して扱いを問う
   - **既定ブランチへ merged になった CL を前提に、triage で停止した follow-up issue**: 手順 5 の follow-up issue の項と同じく triage にかけ直す

   SendMessage で CL エージェントが再開しないとき (このセッションが一度閉じられた後など) は、`${CLAUDE_SKILL_DIR}/scripts/cleanup-merged-cl.sh --worktree-only <その CL の worktree の絶対 path>` で worktree だけを片付ける (CL の branch は残る)。exit 3 は未 commit の変更があって何も消していないので、変更の内容をユーザーに報告して扱いを問う。exit 1 は worktree を消せなかったので、残った worktree と理由をユーザーに報告し、起動し直さない。そのうえで新しい CL エージェントを起動し、prompt には、手順 5 の prompt の項目に、CL の番号・既存の branch 名・頼む作業を加えて入れる。

   全 CL が merged か closed になり、triage の subagent が残っていなければ終える。
