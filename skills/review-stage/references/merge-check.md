# merge の前の確かめ

人が明示に頼んだ merge を代わりに実行する前に、CL ごとに当てる確かめの手順。確かめは merge を止める側にだけ働く — 当たった CL は merge せず、理由を添えて人に問い直す。merge するかを決めるのは人のままにする。

依頼の後に増えた差分と、レビューを通っていない差分は、人の merge の決定に含まれない。target の取り込みと conflict の解消は CL 自身の変更に数えない — merge の決定を実行するのに要る作業で、CL が何を変えるかを変えない。

## 対象と控え

- 対象は、依頼が CL を名指せばその CL、全部を頼めば、merge を代行する側が追っている CL のうち依頼の時点で open なものの全部。条件で CL を選ばない。1 本だけ名指した依頼にも当てる — 名指しは差分を読んだ証拠にならない
- 依頼の時点で、CL ごとに head の SHA を控える (`gh pr view <番号> -R <CL の repo> --json headRefOid` / `glab mr view <番号> -R <CL の repo>` の SHA)
- 確かめる前に、CL の repo の remote を fetch する。下の項目は、CL の今の target の remote branch (`<remote>/<target>`) と CL の head を git で比べる

## 確かめる項目

CL のコメントのうち、レビュー出力の投稿の印を持つものを、投稿の古い順に並べてレビュー出力として読む。印の見分け方は [../SKILL.md](../SKILL.md) の「観点が走った巡の印」の段落 (`観点が走った巡の印` の grep で引く) とその前後の template で当てる — 印は出力の書式と一緒に変わるので、記憶でなく正本で当てる。段落が見つからなければ、記憶の書式で判定せず、grep のコマンドと結果を添えて人に問う。

最後に投稿されたレビュー出力を **最後の巡** と呼ぶ。最後の巡から投稿を遡り、`reviewed:` の左側が `<remote>/<target>` に含まれる (`git merge-base --is-ancestor <左側> <remote>/<target>` が真) 最も新しい出力を **全差分の巡** と呼ぶ。再レビューの巡と依頼分だけを読んだ巡は、左側が CL 自身の commit なので全差分の巡にならない。全差分の巡から最後の巡までの出力を **確かめる巡** と呼ぶ。

次のどれかに当たる CL は merge しない。

1. **ready でない**: CL が draft のまま
2. **レビュー出力が無いか読めない**: 投稿の印を持つコメントが無い。または最後の巡の `reviewed: <左側>..<右側>` 行から 2 つの SHA を読めない
3. **Consider がある**: 確かめる巡のどれかの、どれかの節の `### Consider` に「該当なし」以外の行がある。依頼分だけを読んだ巡は前の巡の Consider を引き継がないので、最後の巡だけでなく確かめる巡を全部読む
4. **レビューした範囲の外の commit**: 全差分の巡の `reviewed:` の右側から今の head までの CL 自身の commit (`git rev-list <全差分の巡の右側>..<今の head> ^<remote>/<target>`) のうち、次のどれでもないものがある
   - 確かめる巡のどれかの `reviewed:` の範囲 (`git rev-list <左側>..<右側>`) に入る commit
   - 「レビュー後に修正 (未レビュー)」の記録 (最後の巡の出力に書き足したものと、その後に足したコメント) が挙げた commit。記録はレビューの後に指摘を直した修正を書く場所なので、挙げた commit を指摘の修正として扱う。commit は SHA (短縮形を含む) で挙げたものか、範囲の中でその件名を持つ commit が 1 本だけの件名で挙げたもの
   - target を取り込む merge commit — 親を 2 つ以上持ち、片方の親が `<remote>/<target>` に含まれる (`git merge-base --is-ancestor <親> <remote>/<target>` が真)。親の形で判定し、conflict の解消の中身は読まない
5. **target との分岐点がずれている**: 全差分の巡が無いか、全差分の巡の `reviewed:` の左側が、CL の今の target との分岐点 (`git merge-base <remote>/<target> <全差分の巡の reviewed: の右側>`) と違う。target を付け替えると前段の commit が CL 自身の変更として差分に入り、その commit はどのレビューも通っていない
6. **依頼の後に増えた差分**: merge の直前に head の SHA を引き直し、控えた SHA から増えた CL 自身の commit (`git rev-list <控えた SHA>..<今の head> ^<remote>/<target>`) に、項目 4 の target を取り込む merge commit でないものがある。誰の依頼で増えた commit か (CL の作者・merge を代行する側の依頼による修正) は問わない。target を取り込む merge commit だけが増えたときは、問わずに控えを今の head へ更新する

項目 4 と 5 は、レビューした範囲の外に指摘の修正でない CL 自身の変更がある CL の 2 つの形にあたる。

## 問い直す

当たった CL は merge せず、CL ごとに、当たった項目と根拠を添えて人に返す。根拠は項目ごとに、CL の状態 (draft)・投稿の印のあるコメントが無いこと・Consider の行・範囲の外の commit の SHA と件名・全差分の巡の左側と分岐点の SHA・増えた commit の SHA と件名。当たった CL の branch を target にしている CL は、答えが届くまで merge しない。依存しない残りの CL は確かめを続ける。

人が merge すると答えたら、その CL の確かめは解ける。その時点の head の SHA を控え直し、同じ根拠で問い直さない。答えの後にさらに commit が増えたら、項目 6 を当て直す。

## merge する

確かめを通った CL と人が答えた CL を、控えた SHA を指定して merge する — GitHub は `gh pr merge <番号> -R <CL の repo> --match-head-commit <控えた SHA>`、GitLab は `glab mr merge <番号> -R <CL の repo> --sha <控えた SHA>`。head が控えから動いていれば tracker が merge を拒むので、確かめた後に増えた commit が merge に紛れない。拒まれたら項目 6 へ戻る。ほかの tracker では、その tracker の CLI が head を固定できればそれで固定し、固定できなければ merge の直前に引き直した head が控えと同じことを確かめてから merge する。

merge のコマンドが permission や classifier に止められたら、別の書き方や経路で merge せず、止めた理由を添えて人に返す。
