---
name: stacked-implement
disable-model-invocation: true
argument-hint: "<issue 番号…> 例: 14-16 / 14 15 16"
description: 名指しした実装 issue 集合を依存順に直列実装し、issue ごとに CL (PR / MR) を 1 本ずつ作ってスタックする。
---

# stacked-implement

名指しされた実装 issue 集合を、このセッション自身が依存順に 1 件ずつ実装する。issue 1 件につき CL (PR / MR) 1 本。依存 chain の後続は前段 branch から分岐し、CL の target を前段 branch にしたスタックとして積む。全 issue の CL 到達が完了条件で、merge と issue close は user の領分。

並列化はしない — 実装工程の review step は Skill tool の invoke を要し、subagent からは invoke できないため、全工程をこのセッションで直列に通す。worker セッションの起動・配車 (dispatcher) は行わない。

## args

`/swat-skills:stacked-implement <issue…>` — 範囲 (`14-16`) か列挙 (`14 15 16`)。範囲は両端を含む連番へ展開する。

## 手順

1. **/goal を提示して、待たずに作業を始める。** 実装から CL 作成までは以降の手順でこのセッション自身が実行する。`/goal` は built-in コマンドで model からは設定できないので、次の条件文を args の issue で埋めて最初の報告に載せるだけにし、user の実行を待たずに手順 2 へ進む:

   ```
   /goal issue <N…> のそれぞれに closing reference 付きの CL が存在する (作れない issue はその理由の報告をもって代える)。全件の CL 番号を列挙した完了報告が出ている
   ```

   goal は「途中で turn が切れても user の一声なしで自動再開する」ための保険で、user はいつ打ってもよい。goal の評価器はこのセッションの出力しか読まないので、以降の手順では CL 到達のたびに CL 番号と URL を本文に明示する。

2. **issue を読む。** 各 issue の title と本文を引く (`gh issue view <N> --json title,body` / `glab issue view <N>`。issue 置き場が cwd の repo と別なら `-R <置き場>` を付ける)。closed の issue・読めない issue は対象から外し、理由を付けて報告へ載せる。

3. **依存グラフを組む。** 判定は次の順で、上が勝つ:
   - 本文の明示参照 (`Blocked by #N` / `Depends on #N` / 「#N に依存」) が正
   - 明示が無くても、後の issue が前の issue の成果 (同じ file・同じ振る舞い) の上に積むと本文から読めたら chain にする
   - どちらとも読めなければ独立とする — 実行は直列なので順序は issue 番号順でよく、独立と chain の差は「分岐元と CL の target をどこにするか」にだけ効く

   依存先が手順 2 で除外された後続は: 依存先が closed なら成果は既定ブランチに居る前提で独立として扱い、依存先が「読めない」で除外されたなら後続ごと対象から外して報告へ載せる。

   組んだ graph と判定根拠を報告してから先へ進む — 依存判定の誤りは実装が積み上がる前でしか安く覆せないので、ここで user の目に載せる。

4. **issue ごとの実装ループ。** 依存順 (chain は前段 → 後続) に 1 件ずつ:
   1. **branch を切る。** 独立な issue は既定ブランチから、chain の後続は前段 issue の branch から (`git checkout -b <branch> <前段 branch>`) 分岐する (既定ブランチの最新化は playbook の step が行う)
   2. **実装工程を通す。** `swat-skills:playbook-implementation` を Skill tool で invoke し、届いた step を逐語で todolist へ写して通す (飛ばす step には `skip: <理由>` を残す)。**issue ごとに invoke し直して todolist を作り直す** — 前 issue の list を使い回すと skip 判定が前の issue の文脈のまま残る。読み替えは 3 つで、それ以外の step は逐語のまま通す:
      - (全 issue) playbook 末尾の PR 作成は手順 4-3 が行う — playbook 側は PR 説明文の組み立てまでで止める (base 未指定の PR が先に立つと chain の stack が壊れる)
      - (chain の後続) 「土台を最新にする」(既定ブランチを取り込む) step は「前段 branch から分岐済み」で満たしたことにし、既定ブランチは取り込まない (前段の成果が土台)
      - (chain の後続) 欠陥探しと 2 軸レビューの差分対象は「既定ブランチとの merge-base」を「前段 branch との merge-base」に読み替える (前段の差分は前段の CL でレビュー済みで、混ぜると指摘の文脈がずれる)
   3. **CL を作る。** commit → push → 独立な issue は `gh pr create` / `glab mr create` (target は既定ブランチ)、chain の後続は `gh pr create --base <前段 branch>` / `glab mr create --target-branch <前段 branch>`。本文に closing reference (`Closes #<N>`) と、playbook が要求する Act on / Dismissed の記録を載せる。target が既定ブランチでない CL は merge しても issue が自動 close されないことがあるが、紐づけの表明として書く
   4. **CL 番号と URL を報告してから次の issue へ進む** (goal の評価器がこの報告で条件を判定する)
   5. 実装が人手 (user しか出せない入力・作業ツリー外の実体) で詰まった issue は、理由を報告して**その issue だけ見送り、chain の後続も道連れで見送って**次の独立 issue へ進む — 全体を止めない

5. **完了報告。** 全 issue を消化したら:
   - issue → CL の対応と stack 構造 (どの CL がどの branch を target にしているか) を全件列挙する
   - user に残る作業: stack は前段から順に merge する。前段 CL を merge して source branch を削除すると、後続の target は自動で既定ブランチへ付け替わる (gh / glab とも。ただし環境依存があるので、付け替わったことを確認してから次を merge する)。merge を伴わない branch 削除は後続の CL を close しうるので行わない
   - 見送った issue とその理由
