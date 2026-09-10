---
name: principle-mechanism-not-policy
description: 他の project へ配布されるもの (skill / CLI / library / template) の本文・手順・description を書くときに適用する。配るのは仕組みだけで、運用方針は実行 project に委ねる。
---

# 仕組みを配り、運用方針は実行 project に委ねる

- **配布物が決めるのは仕組み (mechanism) で、運用方針 (policy) は使う側が決める** (Wulf et al. "HYDRA: The Kernel of a Multiprocessor Operating System", CACM 1974 / X Window System の "mechanism, not policy")
- **変更の届け方 (作業ツリーを分ける / branch を切る / commit する / PR を作る) は実行 project の運用に従う**。開発フローは自分の repo の規約 (CLAUDE.md / rules) 側で効かせ、配布物には委ねる旨だけを書く ([ADR 0052](https://github.com/swat9013/swat-skills/blob/main/docs/adr/0052-distributable-skill-delivery-deferral.md) が却下案まで持つ)
- **配布物が自分の言葉で持つのは自分の保証** (その配布物が守ると約束したこと) と、自分がやらないことの宣言
- **判定は 2 問を順に当てる**。(1)「その記述が実行 project の規約と衝突しうるか」— 衝突しうるなら policy なので委ねる。運用そのものを主題にする配布物 (作業ツリーや PR を扱う skill 等) と、動作環境の事実の記述はこの線の外。(2)「名指しした対象が配布先でも成立するか」— 開発元 repo にしか無い path・その repo だけが持つディレクトリや構成要素を対象に据えると、配布先では当たらないまま指示だけが残る。特定の環境でしか成り立たない扱いが要るなら、判定を実行 project 側の宣言へ寄せる
- **frontmatter の `description` も本文と同じ線で見る** (上の 2 問を両方当てる)。手順から運用指示を外しても description に残れば、運用方針は配られたまま
- **前提 (想定環境) は 1 箇所に宣言し、個々の配布物へ再記述しない**。再記述した数だけ drift 面が増え、前提が変わったときに黙って食い違う
- **運用方針が宣言されていないときは既定を 1 つ決めて進む**。宣言の有無を判定して止める仕組みは置かない — 誤検知と見逃しを持つ判定が増えるだけで、既定で進めても自分の保証と version control で回復できる。動作環境の前提 (外部依存の導入有無・疎通) はこの線の外で、不成立なら loud に止める
