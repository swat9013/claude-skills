# 規約の置き場の節

対象 repo の `docs/agents/domain.md` に置く「規約の置き場」節の正本。`review-stage` の原則の観点は、**既定の対象** (project memory・`.claude/rules/`・project memory が `@` で取り込む file・それらが判断の正本として指す文書) を規約として読む。repo がコードの書き方の規約を既定の対象の外の file に書いているとき、この節でその file を宣言すると、原則の観点はその file も project memory と同じ格の規約として読む。project memory は SKILL.md のスコープ表の定義に従う。

## 候補の探し方

repo root から次の file を探し、既定の対象から届いていないものを候補にする。

- `CODING_STANDARDS.md`・`STYLEGUIDE.md`・`REVIEW.md` (`docs/` 配下を含む)
- `.github/copilot-instructions.md`・`.github/instructions/*.instructions.md`
- project memory が `CLAUDE.md` / `.claude/CLAUDE.md` のときの `AGENTS.md`
- `CONTRIBUTING.md` (`.github/`・`docs/` 配下を含む) のうち、コードの書き方かレビューの観点を扱う見出しを持つもの。開発フローだけの `CONTRIBUTING.md` (setup の手順 5 が作る file もこれに当たる) は候補にしない

候補が規約を持つかは file 名だけでは決まらない。開発フローだけの file を規約として読ませると、開発フローの記述まで規約違反の根拠になる。

## 雛形

`docs/agents/domain.md` の末尾に、次の節を置く。表の行は、利用者が宣言すると決めた候補だけにする。

```markdown
## 規約の置き場

コードの書き方の規約を、`CLAUDE.md`・`.claude/rules/` に加えて次の file に置く。レビューは、変更する file に当たる行の file を `CLAUDE.md` と同じ格の規約として読む。`当たる path` は repo root 相対の glob で、`.claude/rules/` の frontmatter の `paths` と同じ照合をする。空欄はすべての file に当たる。

| file | 当たる path |
|---|---|
| `CODING_STANDARDS.md` | |
```

- `file` は repo root 相対の path
- `当たる path` の照合規則は節の本文に書き、`review-stage` はその本文に従って照合する (照合規則の正本は宣言の節)。一部の file にだけ当たる規約を全差分に当てて誤検知させないための列。候補の中身が特定のディレクトリだけを扱うと読めるときは、その glob を示して利用者に確かめる。それ以外は空欄にする

## 突き合わせ (節が既にあるとき)

repo が書いた行は、承認された変更提案の分だけ書き換える。変更提案にするのは次の 2 つ。

- 節の見出し・表の列 (`file` / `当たる path`)・本文の照合規則の文が雛形と違う: 雛形の形へ揃える提案を、現状 / 提案 / なぜ違うか の 3 点で示す。形が違うと `review-stage` が宣言を読めず、照合規則の文が無いと当たる行を決められない
- 行の `file` が repo に無い: その行を外すか、移った先の path へ直すかを提案する。無い file の行は、レビューのたびに注記として残り続ける

表に無い候補が「候補の探し方」で見つかったら、行を足すかを尋ねる。これは質問であって変更提案ではない。
