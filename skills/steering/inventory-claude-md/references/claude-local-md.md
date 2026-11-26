# CLAUDE.local.md 由来の行の扱い

`sources.claude_md.local.status` が `present` のとき、SKILL.md 手順 2 の bucket 割当てと手順 3 の適用手順に足して当てる。

**`CLAUDE.local.md` 由来の行の追加制約** (`sources.claude_md.local` の行にのみ適用):

- **`.claude/rules/` / skill / project `CLAUDE.md` への移動提案は「移設」でなく「共有化」**。project-local は個人 override の器なので、移すと**他の作業者・他マシンにも配布される**。提案文に `共有化:` を明示し、「この内容は共有してよいか」を人間への確認事項として 1 行添える。無印の移設として書かない
- **共有化を提案しない側に倒す条件**: マシン固有 path / 個人ホスト名 / 個人の作業習慣・好み・実験中の設定など、**その行が local に置かれている理由が内容から読み取れる**場合。この場合は `keep-inline` (local 残置) を提案し、根拠に「project-local に置く理由が内容にある」と書く
- **提案先の器を書き分ける**: `keep-inline` の残置先は `CLAUDE.md` でなく `CLAUDE.local.md`。レポートの「対象」は必ず `CLAUDE.local.md:<line>` 形式で書き、root 由来と混ぜない
- **`merge` は器をまたいで提案してよいが方向を明示する** (local → project の重複なら「local 側を削除して project に一本化」か「project 側の個人 override として意図的」かを人間判断に残す。`推測:` で片寄せしない)

**source による適用経路の分岐** (bucket 分岐と直交。両方をレポートに書く):

- `CLAUDE.local.md` が `.gitignore` 済みなら**その file への編集は version control に乗らない** — レポートの適用手順に「反映しても version control には残らない」と書く。gitignore 済みかは observation JSON に無いので、レポートには条件形 (`.gitignore` 済みなら〜) で書くか、人間に確認を促す 1 行を添える
- 共有化 (local → project `CLAUDE.md` / `.claude/rules/` / skill) は**移動先が tracked**なので version control に載る。local 側の削除は載らない可能性があり、**2 経路の作業になる**ことを適用手順に明記する
