---
name: repo-agent-maturity
user-invocable: true
description: repo (省略時は cwd) をコーディングエージェント (Claude Code / Cursor / Windsurf) 受け入れ準備度で Lv.1〜5 に採点する。Use when「agent 活用度を採点」「agent maturity」「repo が agent-ready か」.
---

# repo-agent-maturity

汎用 repo を **level** (Lv.1〜5) に振り分ける 20 **checkpoint** の reference。判定は repo 内のファイル存在・内容 pattern・行数のみで完結し、SKILL.md 単独で配布・実行できる (依存 script なし)。

runtime 挙動 (hook 発火・rule 遵守・MCP コスト) は判定不能なので、下記 checkpoint には含めない。参考観点は「範囲外」節に列挙する。

## 入力

- 引数なし → cwd を対象
- `<path>` → 指定 path (相対 / 絶対 / `~/` 可) を対象

対象 repo の絶対 path を `$ROOT` として以降のコマンドに埋める。判定は Read only で完結し、対象 repo に書き込まない。

## 手順

### Step 1: 全 20 checkpoint を判定する

「判定コマンド」節の bash block を 1 発実行する。標準出力に `<id> pass` / `<id> fail` が 20 行出る (順序: L1〜L5, id 昇順)。行数が 20 でなければ実行失敗として同 block を再実行し、20 行揃ってから Step 2 に進む。

fail の扱いは機械判定の結果がすべて: 原因の深掘り (`git check-ignore` 等の追加コマンド) はせず、そのまま Step 2 に渡す。

### Step 2: 到達 level を確定する

Lv.1 から順に、その level の全 checkpoint が pass なら次 level に進む。最初に fail を含む level の 1 つ手前が **reached_level** (初期値 0、飛び級なし)。

### Step 3: 改善リストを作る

未達 checkpoint 全件に hint 表の hint を添え、level 昇順 → id 昇順に並べる。このうち **level が最小のもの** を「手始めの改善」とする (その level を pass するのが最短の次段だから)。残り全件は「続く改善」として同じ順で続け、省略しない。

### Step 4: レポートを出す

以下 template を stdout に書く。読み手は agent 活用の初学者と想定し、checkpoint は id だけでなく意味・hint の日本語を必ず添える。

規則:

- レポートは template の節のみで構成する (前置き・独自の節を足さない)
- 到達 level は Step 2 の reached_level をそのまま書く (「実質 Lv.X 相当」のような再解釈を加えない)
- hint は hint 表の文言を使う。repo 固有の補足は各 hint 末尾に 1 文まで。補足に書けるのは機械判定の既知の検出限界の注記のみで、「実質達成している」等の判定を覆す断定は書かない
- 「範囲外」節の手動レビュー観点は、ユーザーが明示的に求めた場合のみ末尾に追加する

```markdown
## Repo Agent Maturity: Lv.<N> 到達

### レベル別サマリ
- Lv.1 (基本文書がある): <pass>/3
- Lv.2 (agent 向け設定・ルールをチーム共有): <pass>/4
- Lv.3 (lint・test・CI で品質チェックを自動化): <pass>/5
- Lv.4 (hooks・skills・subagent で agent を拡張): <pass>/4
- Lv.5 (決定と運用ルールを文書で記録): <pass>/4

### できていること (<pass 合計>/20)
- [Lv.1] <id> — <意味>
- ... (pass 全件を列挙)

### 手始めの改善 (まず Lv.<未達最低> を埋めるのが最短)
1. [Lv.M] <id> — <意味>。hint: <hint>
2. ...

### 続く改善 (残り全件、この順で)
3. [Lv.M] <id> — <意味>。hint: <hint>
4. ...
```

未達が 0 件なら改善 2 節をまとめて `### 改善なし — 全 20 checkpoint 達成` の 1 行に置き換える。

## 判定コマンド

以下 block を対象 repo の絶対 path で `ROOT` を埋めて実行する。macOS / Linux 両対応 (POSIX shell + BSD/GNU の find / grep / awk / cat / tr / ls / git のみに依存。python 等の言語 runtime は不要)。

```bash
ROOT="/absolute/path/to/repo"   # ← 対象 repo に置換

# ---- Lv.1 ----
if [ -f "$ROOT/CLAUDE.md" ] || [ -f "$ROOT/AGENTS.md" ]; then echo "L1.CLAUDE_MD pass"; else echo "L1.CLAUDE_MD fail"; fi
if ls "$ROOT" 2>/dev/null | grep -qiE '^readme'; then echo "L1.README pass"; else echo "L1.README fail"; fi
if [ -f "$ROOT/.gitignore" ]; then echo "L1.GITIGNORE pass"; else echo "L1.GITIGNORE fail"; fi

# ---- Lv.2 ----
if [ -f "$ROOT/.claude/settings.json" ] || [ -f "$ROOT/.claude/settings.local.json" ] || [ -e "$ROOT/.cursor/rules" ] || [ -f "$ROOT/.cursorrules" ] || [ -f "$ROOT/.windsurfrules" ]; then echo "L2.CLAUDE_SETTINGS pass"; else echo "L2.CLAUDE_SETTINGS fail"; fi
if find "$ROOT/.claude/rules" "$ROOT/rules" "$ROOT/docs/rules" -maxdepth 1 -type f -name '*.md' 2>/dev/null | grep -q . || [ "$(find "$ROOT/docs" -maxdepth 1 -type f -name '*.md' 2>/dev/null | wc -l)" -ge 2 ]; then echo "L2.RULES_OR_INDEX pass"; else echo "L2.RULES_OR_INDEX fail"; fi
if cat "$ROOT/CLAUDE.md" "$ROOT/AGENTS.md" 2>/dev/null | grep -qE '\b(test|lint|build|format|check|typecheck|fmt)\b'; then echo "L2.CLAUDE_MD_COMMANDS pass"; else echo "L2.CLAUDE_MD_COMMANDS fail"; fi
if git -C "$ROOT" ls-files 2>/dev/null | grep -qE '(^|/)(CLAUDE|AGENTS)\.md$'; then echo "L2.CLAUDE_MD_TRACKED pass"; else echo "L2.CLAUDE_MD_TRACKED fail"; fi

# ---- Lv.3 ----
if find "$ROOT" -maxdepth 2 -type f \( -name '.eslintrc*' -o -name 'eslint.config.*' -o -name 'ruff.toml' -o -name '.ruff.toml' -o -name '.pylintrc' -o -name '.rubocop.yml' -o -name '.golangci.y*ml' -o -name 'clippy.toml' -o -name '.stylelintrc*' -o -name 'biome.json*' \) 2>/dev/null | grep -q . || cat "$ROOT/pyproject.toml" 2>/dev/null | grep -qE '\[tool\.(ruff|pylint|flake8|mypy|pyright)'; then echo "L3.LINT_CONFIG pass"; else echo "L3.LINT_CONFIG fail"; fi
if find "$ROOT" -maxdepth 2 -type f \( -name '.prettierrc*' -o -name 'prettier.config.*' -o -name '.editorconfig' -o -name 'rustfmt.toml' -o -name '.rustfmt.toml' -o -name 'dprint.json*' -o -name '.standard.yml' -o -name '.streerc' \) 2>/dev/null | grep -q . || cat "$ROOT/pyproject.toml" 2>/dev/null | grep -qE '\[tool\.(black|autopep8|yapf)|\[tool\.ruff\.format' || grep -q '^Layout' "$ROOT/.rubocop.yml" 2>/dev/null; then echo "L3.FORMAT_CONFIG pass"; else echo "L3.FORMAT_CONFIG fail"; fi
if find "$ROOT" -maxdepth 2 -type f \( -name 'pytest.ini' -o -name 'tox.ini' -o -name 'jest.config.*' -o -name 'vitest.config.*' -o -name 'karma.conf.*' -o -name 'phpunit.xml*' -o -name '.rspec' \) 2>/dev/null | grep -q . || cat "$ROOT/pyproject.toml" "$ROOT/package.json" 2>/dev/null | grep -qE '\[tool\.pytest\.ini_options|"test"[[:space:]]*:' || find "$ROOT" -maxdepth 4 -type f -name '*_test.go' 2>/dev/null | grep -q . || [ -f "$ROOT/test/test_helper.rb" ] || [ -f "$ROOT/spec/spec_helper.rb" ] || [ -f "$ROOT/spec/rails_helper.rb" ]; then echo "L3.TEST_CONFIG pass"; else echo "L3.TEST_CONFIG fail"; fi
if find "$ROOT/.github/workflows" -maxdepth 1 -type f -name '*.y*ml' 2>/dev/null | grep -q . || find "$ROOT" -maxdepth 2 -type f \( -name '.gitlab-ci.yml' -o -name 'Jenkinsfile' -o -name '.travis.yml' -o -name 'buildkite.yml' -o -name 'azure-pipelines.y*ml' -o -name 'bitbucket-pipelines.yml' \) 2>/dev/null | grep -q . || find "$ROOT/.circleci" -maxdepth 1 -type f -name 'config.yml' 2>/dev/null | grep -q .; then echo "L3.CI_CONFIG pass"; else echo "L3.CI_CONFIG fail"; fi
HOOKSPATH=$(git -C "$ROOT" config core.hooksPath 2>/dev/null)
if find "$ROOT" -maxdepth 2 -type f \( -name '.pre-commit-config.y*ml' -o -name 'lefthook.y*ml' -o -name '.lefthook.y*ml' \) 2>/dev/null | grep -q . || [ -f "$ROOT/.githooks/pre-commit" ] || [ -f "$ROOT/.husky/pre-commit" ] || { [ -n "$HOOKSPATH" ] && { [ -f "$ROOT/$HOOKSPATH/pre-commit" ] || [ -f "$HOOKSPATH/pre-commit" ]; }; }; then echo "L3.PRECOMMIT pass"; else echo "L3.PRECOMMIT fail"; fi

# ---- Lv.4 ----
if [ -f "$ROOT/.claude/hooks/hooks.json" ] || [ -f "$ROOT/hooks/hooks.json" ] || cat "$ROOT/.claude/settings.json" "$ROOT/.claude/settings.local.json" 2>/dev/null | grep -q '"hooks"'; then echo "L4.CLAUDE_HOOKS pass"; else echo "L4.CLAUDE_HOOKS fail"; fi
if { awk '/"deny"[[:space:]]*:[[:space:]]*\[/,/\]/' "$ROOT/.claude/settings.json" 2>/dev/null | grep -q '"[^"][^"]*"'; } || { awk '/"deny"[[:space:]]*:[[:space:]]*\[/,/\]/' "$ROOT/.claude/settings.local.json" 2>/dev/null | grep -q '"[^"][^"]*"'; }; then echo "L4.DENY_LIST pass"; else echo "L4.DENY_LIST fail"; fi
if [ -f "$ROOT/.claude-plugin/plugin.json" ] || find "$ROOT/.claude/skills" "$ROOT/skills" -type f -name 'SKILL.md' 2>/dev/null | grep -q .; then echo "L4.SKILLS_DIR pass"; else echo "L4.SKILLS_DIR fail"; fi
if find "$ROOT/.claude/agents" "$ROOT/agents" -maxdepth 1 -type f -name '*.md' 2>/dev/null | grep -q .; then echo "L4.AGENTS_DIR pass"; else echo "L4.AGENTS_DIR fail"; fi

# ---- Lv.5 ----
if find "$ROOT/docs/adr" "$ROOT/adr" "$ROOT/docs/decisions" "$ROOT/docs/architecture/decisions" -maxdepth 1 -type f -name '*.md' 2>/dev/null | grep -qE '/[0-9]{3,}[-_]'; then echo "L5.ADR_DIR pass"; else echo "L5.ADR_DIR fail"; fi
if find "$ROOT" \( -name node_modules -o -name vendor -o -name .git \) -prune -o -type f -name 'SKILL.md' -exec grep -lE '^(user-invocable|disable-model-invocation)[[:space:]]*:' {} + 2>/dev/null | grep -q .; then echo "L5.SKILLS_FRONTMATTER pass"; else echo "L5.SKILLS_FRONTMATTER fail"; fi
if [ -f "$ROOT/CONTRIBUTING.md" ] || [ -f "$ROOT/CONTRIBUTING.rst" ] || [ -f "$ROOT/docs/CONTRIBUTING.md" ]; then echo "L5.CONTRIBUTING pass"; else echo "L5.CONTRIBUTING fail"; fi
if grep -qEr '(pytest|npm test|yarn test|pnpm test|cargo test|go test|jest|vitest|mvn test|gradle test|make test|rspec|tox|minitest|rails test|rake test)' "$ROOT/.github/workflows" "$ROOT/.circleci" 2>/dev/null || cat "$ROOT/.gitlab-ci.yml" "$ROOT/Jenkinsfile" "$ROOT/.travis.yml" "$ROOT/buildkite.yml" 2>/dev/null | grep -qE '(pytest|npm test|yarn test|pnpm test|cargo test|go test|jest|vitest|mvn test|gradle test|make test|rspec|tox|minitest|rails test|rake test)'; then echo "L5.CI_HAS_TEST pass"; else echo "L5.CI_HAS_TEST fail"; fi
```

## checkpoint と hint

| id | 意味 | 手始めの改善に使う hint |
|---|---|---|
| L1.CLAUDE_MD | CLAUDE.md か AGENTS.md が root にある | root に `CLAUDE.md` を置き、1 行説明・主要ディレクトリ・build/test コマンドだけ書く |
| L1.README | README が root にある | `README.md` を追加し、セットアップ手順と主要コマンドを書く |
| L1.GITIGNORE | .gitignore が存在 | `github/gitignore` の言語別テンプレをコピー |
| L2.CLAUDE_SETTINGS | agent 用 settings が存在 (.claude / .cursor / .windsurf) | `.claude/settings.json` を作り `permissions.allow` を最小限で列挙 |
| L2.RULES_OR_INDEX | rules 系 dir (`.claude/rules` / `rules` / `docs/rules`) に md、または docs/ 直下に 2 件以上の md | `.claude/rules/coding.md` と `security.md` を作り NEVER ルールとコミット規約を書く |
| L2.CLAUDE_MD_COMMANDS | CLAUDE.md/AGENTS.md に build/test/lint 系キーワードあり | fenced code block で `make test` `npm run lint` などの主要コマンドを書く |
| L2.CLAUDE_MD_TRACKED | CLAUDE.md/AGENTS.md が git 管理下 (repo 内のどこでも可) | `git add CLAUDE.md && git commit` でチーム共有化 |
| L3.LINT_CONFIG | lint 設定ファイル or pyproject の lint section | 使用言語に応じ ruff / eslint / rubocop / golangci-lint 等を導入 |
| L3.FORMAT_CONFIG | format 設定ファイル or pyproject の format section or rubocop の Layout 設定 | prettier / black / rustfmt / .editorconfig いずれかで format を決め打ち (Ruby は rubocop Layout / standard) |
| L3.TEST_CONFIG | test runner 設定 or `package.json` の scripts.test / `*_test.go` / `test_helper.rb`・`spec_helper.rb` 等の慣習 marker | pytest.ini / jest.config / scripts.test 等の CI から呼べる entry を作る |
| L3.CI_CONFIG | 主要 CI サービスの config | `.github/workflows/ci.yml` を追加し lint と test を PR 毎に走らせる |
| L3.PRECOMMIT | precommit hook 設定 (core.hooksPath の独自 dir も可) | `.pre-commit-config.yaml` か lefthook で format+lint を commit 時に走らせる |
| L4.CLAUDE_HOOKS | Claude Code の hooks 定義 | `.claude/settings.json` の `hooks` で PostToolUse に lint/format を仕込む |
| L4.DENY_LIST | settings の `permissions.deny` に entry が 1 件以上 | `git push --force` `rm -rf /` `curl \| sh` 等を deny に入れる |
| L4.SKILLS_DIR | SKILL.md か plugin.json が存在 | `.claude/skills/<name>/SKILL.md` でチーム標準の手順を skill 化 |
| L4.AGENTS_DIR | subagent 定義がある | `.claude/agents/<name>.md` で code-reviewer 等を定義し tools を最小化 |
| L5.ADR_DIR | 番号付き ADR が 1 件以上 | `docs/adr/0001-initial.md` を作り MADR テンプレで最初の decision を記録 |
| L5.SKILLS_FRONTMATTER | SKILL.md に invocability が明示 (1 件以上で pass) | 少なくとも 1 つの SKILL.md の frontmatter に `user-invocable: true` を書く (全件明示が理想) |
| L5.CONTRIBUTING | CONTRIBUTING.md 相当が存在 | ブランチ運用・PR テンプレ・review 手順を書く |
| L5.CI_HAS_TEST | CI 内で test コマンド実行 | CI yml に `pytest` `npm test` `rails test` 等の実行行を入れる |

## 範囲外 (機械判定不能な参考観点)

以下は repo 内のファイルだけでは判定できない。出力条件は Step 4 の規則に従う (ユーザーが明示的に求めた場合のみ「手動レビュー観点」として文で列挙する):

- CLAUDE.md が索引 → リンク → 詳細に分離されているか (30-60 行目安)
- rule が肯定形かつ検証可能な述語で書かれているか
- hook が同期 100ms 制約を守っているか / matcher が広すぎないか
- Skill description が trigger として機能しているか (呼ばれない / 呼ばれすぎがないか)
- MCP tool の context コストが 10 以下の目安に収まっているか
- OWASP Agentic Top 10 各項 (認証情報継承・supply chain 検証・memory poisoning 等)
- CI が test コマンドを独自 shell script に包んでいる場合の実行有無 (L5.CI_HAS_TEST の keyword 判定は script 名や CI yml に test 系語彙が現れる場合のみ検出できる)

## 参照

- 原典スライド: nwiizo「新年度からコーディングエージェントを使いこなす — 構造と規約で引き出す Claude Code の実践知」
