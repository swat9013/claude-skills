#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.0,<3"]
# ///
"""transcript-ops MCP server の entry point (stdio transport)。

決定は [ADR 0029](../../docs/adr/0029-transcript-ops-consolidation.md)。steering の
inventory 系が使う transcript 観測を一元化し、**on-disk 形式を知るのは `adapter/` だけ**
にする (format isolation)。tool 1 本 = `commands/` の module 1 本で、mart schema
(観測契約) は tool 単位に保つ。

**server はポリシーを持たない** — 抽出・集計だけを行い、bucket 判定 (revoke /
delete-candidate / 器の分類) も候補の採否も知らない。判断は人間、文章の具体化は
各 SKILL.md の LLM 段階 (ADR 0011 の 3 層分離)。

**mart を context に返さない**: 各 tool は `/tmp` 配下に mart / slice を書き、返すのは
出力 path と件数 meta だけ。「大きく出して絞って読む + PR 証拠として残す」消費パターンを
維持するためで、返り値に本体を載せると窓が即死する。

**本 module が持つのは配線だけ** — tool 関数は commands の `run()` へ 1 式で委譲する。
手続きがここに溜まると、LLM 向け interface (docstring) の module に振る舞いが乗り、
commands 単体では検証できない合成が生まれる。

配布・登録は plugin root の `.mcp.json`。server 名 `transcript-ops`、tool 完全名は
`mcp__plugin_swat-skills_transcript-ops__<tool>`。

**cwd の扱い**: `repo_root` 既定は **server プロセスの cwd** (セッション起動時に固定)。
別 project を観測したいときは引数で明示する。
"""

import sys
from pathlib import Path
from typing import Any

# uv run が PEP 723 script をどう起動しても sibling package を解決できるようにする
# (script ディレクトリの sys.path 追加は起動側の実装に依存させない)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server import MCPServer  # noqa: E402

from commands import find_invocations as find_invocations_mod  # noqa: E402
from commands import scan_invocations as scan_invocations_mod  # noqa: E402
from commands import scan_overhead as scan_overhead_mod  # noqa: E402
from commands import scan_permissions as scan_permissions_mod  # noqa: E402
from commands import scan_prompts as scan_prompts_mod  # noqa: E402
from commands import select_candidates as select_candidates_mod  # noqa: E402

SERVER_NAME = "transcript-ops"
SERVER_VERSION = "0.1.0"

# 手順は各 SKILL.md が持つ。ここは全利用セッションの context に常駐するので、
# 「返り値の読み方」だけに絞る (ADR 0029: context 常駐コストの圧縮)。
INSTRUCTIONS = """\
標準 transcript (~/.claude/projects) の観測を一元化する policy-free な server。

- どの tool も mart / slice を /tmp 配下に書き、返すのは path と件数 meta だけ。
  中身が要るなら返ってきた path を Read する
- bucket 判定 (revoke / delete-candidate / 器の分類) は tool の責務外。手順は
  呼び出し元の SKILL.md が持つ
"""

server = MCPServer(name=SERVER_NAME, version=SERVER_VERSION, instructions=INSTRUCTIONS)


@server.tool()
def scan_permissions(
    section: str = "project",
    days: int = scan_permissions_mod.DEFAULT_DAYS,
    repo_root: str | None = None,
    output_dir: str = str(scan_permissions_mod.DEFAULT_OUTPUT_DIR),
    transcripts_dir: str = str(scan_permissions_mod.DEFAULT_TRANSCRIPTS_DIR),
    global_settings: str = str(scan_permissions_mod.DEFAULT_GLOBAL_SETTINGS),
    config_dir: str = str(scan_permissions_mod.DEFAULT_CONFIG_DIR),
    now: str | None = None,
) -> dict[str, Any]:
    """permission entry / hook 設定 × 実績の両軸 mart を書き、読む順の path を返す。

    Args:
        section: project (cwd × 当該 repo 実績) / global (~/.claude/settings.json ×
            全 repo 実績) / all
        days: 観測窓 (日)。既定 30
        repo_root: project section の起点。省略時は server プロセスの cwd
        output_dir: 出力先。既定 /tmp/inventory-permissions
        transcripts_dir: transcript lake。既定 ~/.claude/projects
        global_settings: global settings の path。既定 ~/.claude/settings.json
        config_dir: Claude Code config dir (plugin hooks の分母源)。既定 ~/.claude
        now: ISO timestamp。観測窓の起点を固定する (再現用)

    `paths` は読む順に並ぶ (`00-meta` → `10-derived-views` → `20-axis-a` →
    `30-bypass-samples` → `40-hooks` → `90-mart`)。**`90-mart.json` は標準フローでは
    読まない** — 数 MB 級の全量で、想定外の追加検査にだけ使う。各ファイルの用途は
    `00-meta.json` の `contract` が正本。

    `meta.sufficient_for_relative_judgment` が false なら相対判定 (未使用の
    entry を「使われていない」と読む) は成立しない — 観測不足であって不使用の
    証拠ではない。

    `40-hooks.json` の `hook_activity` は hook 設定 (settings + plugin hooks.json) と
    fire 実績の突合で、section (cwd scope) では絞らない。**`nonzero_exit_count` を
    「失敗していない」と読まない** — 観測限界は同ファイルの `observability` が正本。
    """
    return scan_permissions_mod.run(
        section=section,
        days=days,
        repo_root=repo_root,
        output_dir=output_dir,
        transcripts_dir=transcripts_dir,
        global_settings=global_settings,
        config_dir=config_dir,
        now=now,
    )


@server.tool()
def scan_invocations(
    days: int = scan_invocations_mod.DEFAULT_DAYS,
    repo_root: str | None = None,
    output_dir: str = str(scan_invocations_mod.DEFAULT_OUTPUT_DIR),
    transcripts_dir: str = str(scan_invocations_mod.DEFAULT_TRANSCRIPTS_DIR),
    config_dir: str = str(scan_invocations_mod.DEFAULT_CONFIG_DIR),
    claude_json: str = str(scan_invocations_mod.DEFAULT_CLAUDE_JSON),
    now: str | None = None,
) -> dict[str, Any]:
    """skill / agent / MCP tool の invocation 実績 mart を書き、path を返す。

    Args:
        days: 観測窓 (日)。既定 30
        repo_root: 分母源 project (`.claude/skills` / `.mcp.json`)。省略時は
            server プロセスの cwd
        output_dir: 出力先。既定 /tmp/inventory-skill-mcp
        transcripts_dir: transcript lake。既定 ~/.claude/projects
        config_dir: Claude Code config dir。既定 ~/.claude
        claude_json: MCP server 分母源。既定 ~/.claude.json
        now: ISO timestamp。観測窓の起点を固定する (再現用)

    skill unit の count は 3 channel (Skill tool_use / slash command / SKILL.md への
    Read) の合算で、内訳は mart の `channels` に出る。分母に無い id は
    `source: session-observed` で補完されるが、これは**観測分の下限保証**であって
    install 済み一覧ではない (claude.ai connectors 等はローカル config に出ない)。
    """
    return scan_invocations_mod.run(
        days=days,
        repo_root=repo_root,
        output_dir=output_dir,
        transcripts_dir=transcripts_dir,
        config_dir=config_dir,
        claude_json=claude_json,
        now=now,
    )


@server.tool()
def scan_prompts(
    days: int = scan_prompts_mod.DEFAULT_DAYS,
    output_dir: str = str(scan_prompts_mod.DEFAULT_OUTPUT_DIR),
    transcripts_dir: str = str(scan_prompts_mod.DEFAULT_TRANSCRIPTS_DIR),
    text_limit: int = scan_prompts_mod.DEFAULT_TEXT_LIMIT,
    now: str | None = None,
) -> dict[str, Any]:
    """人間が手入力した prompt だけの mart を書き、path を返す。

    Args:
        days: 観測窓 (日)。既定 30
        output_dir: 出力先。既定 /tmp/inventory-values。分母の違う棚卸し
            (engineering-values) は別 dir に分ける
        transcripts_dir: transcript lake。既定 ~/.claude/projects
        text_limit: 1 prompt あたりの保存文字数上限。既定 4000
        now: ISO timestamp。観測窓の起点を固定する (再現用)

    mart は**全 project 横断**で作る。repo での絞り込みは `select_candidates` の
    領分。`meta.excluded.no_prompt_source` が急増していたら CLI の schema 変更で
    観測が劣化した疑い (silent zero にはならない設計)。
    """
    return scan_prompts_mod.run(
        days=days,
        output_dir=output_dir,
        transcripts_dir=transcripts_dir,
        text_limit=text_limit,
        now=now,
    )


@server.tool()
def select_candidates(
    mart: str,
    min_chars: int = select_candidates_mod.DEFAULT_MIN_CHARS,
    repo: str | None = None,
    all_repos: bool = False,
    limit: int | None = None,
    output_dir: str = str(select_candidates_mod.DEFAULT_OUTPUT_DIR),
    repo_root: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """mart を長さ / repo / 正規形で絞り、読み順を確定した slice を書いて path を返す。

    Args:
        mart: `scan_prompts` が返した mart JSON の path
        min_chars: 候補に含める text_chars の下限。既定 60
        repo: mart の repo field と完全一致で絞る
        all_repos: repo 絞り込みを外して全 project 横断で見る
        limit: 出力件数の上限。打ち切りは `meta.truncated_by_limit` に出る
        output_dir: 出力先。既定 /tmp/inventory-values
        repo_root: repo 既定解決の起点。省略時は server プロセスの cwd
        now: ISO timestamp。出力ファイル名の stamp を固定する (再現用)

    `repo` も `all_repos` も渡さないと `repo_root` (既定 cwd) の git remote に
    解決する。**解決できないときは全 repo へ倒さず失敗する** — 他 project の
    prompt を黙って slice に載せないことが既定解決の目的そのものだから。

    読み順は `text_chars` 降順の全順序 (`rank` 昇順)。長さは絞り込みの入口であって
    判定材料ではない。除外は `meta.excluded` / `meta.band_histogram` に出し、
    定型と判定した正規形は slice の `boilerplate_forms` に残す (**逐語反復された
    規範がここに落ちる**ので、拾い戻しの候補源として読む)。
    """
    return select_candidates_mod.run(
        mart=mart,
        min_chars=min_chars,
        repo=repo,
        all_repos=all_repos,
        limit=limit,
        output_dir=output_dir,
        repo_root=repo_root,
        now=now,
    )


@server.tool()
def scan_overhead(
    days: int = scan_overhead_mod.DEFAULT_DAYS,
    repo_root: str | None = None,
    all_repos: bool = False,
    output_dir: str = str(scan_overhead_mod.DEFAULT_OUTPUT_DIR),
    transcripts_dir: str = str(scan_overhead_mod.DEFAULT_TRANSCRIPTS_DIR),
    now: str | None = None,
) -> dict[str, Any]:
    """静的コンテキストの実測コスト × 実績 (注入 session 数 / compaction) を書く。

    Args:
        days: 観測窓 (日)。既定 30
        repo_root: repo 絞り込みの起点。省略時は server プロセスの cwd
        all_repos: repo 絞り込みを外して全 project 横断で見る
        output_dir: 出力先。既定 /tmp/inventory-claude-md
        transcripts_dir: transcript lake。既定 ~/.claude/projects
        now: ISO timestamp。観測窓の起点を固定する (再現用)

    `memory_files[]` は `.claude/rules/` / CLAUDE.md が**実際に注入された session 数**
    で、`paths:` で絞った規範が届いているかの実測になる。path は worktree 断片を
    畳んである。`compaction` は捨てられた token (`cumulative_dropped_tokens` は
    session ごとの最大値。boundary をまたいで足さない)。

    **token 数はすべて概算** (`meta.token_estimator`)。桁の比較にだけ使う。実績が
    決定的なのは **file 粒度まで**で、行単位の静的コストは repo static 側
    (`scan-claude-md.py` の `token_cost`) が出す。
    """
    return scan_overhead_mod.run(
        days=days,
        repo_root=repo_root,
        all_repos=all_repos,
        output_dir=output_dir,
        transcripts_dir=transcripts_dir,
        now=now,
    )


@server.tool()
def find_invocations(
    skill: str,
    limit: int = find_invocations_mod.DEFAULT_LIMIT,
    transcripts_dir: str = str(find_invocations_mod.DEFAULT_TRANSCRIPTS_DIR),
    output_dir: str = str(find_invocations_mod.DEFAULT_OUTPUT_DIR),
    now: str | None = None,
) -> dict[str, Any]:
    """指定 skill の**実呼出** transcript を特定し、slice を書いて path を返す。

    Args:
        skill: 対象 skill 名。plugin prefix (`swat-skills:`) は付けても外しても可
        limit: slice に載せる transcript 数の上限。既定 3
        transcripts_dir: transcript lake。既定 ~/.claude/projects
        output_dir: 出力先。既定 /tmp/skill-usage-audit
        now: ISO timestamp。出力ファイル名の stamp を固定する (再現用)

    判定は **record 構造** (assistant の `Skill` tool_use / user の slash 展開) で
    行い、行 grep はしない。そのため queue-operation の二重記録・wrapper
    transcript・literal 引用は呼出しに計上されず、件数だけが `meta.excluded` に
    理由別で残る。**`meta.total_invocations` が呼出回数**で、`matched_files` は
    file 数 (両者を混同しない)。

    観測窓は持たない — 監査対象は「最後に呼ばれた n 件」であって期間ではない。
    `meta.matched_files` が 0 なら実呼出なしで、監査は成立しない。
    """
    return find_invocations_mod.run(
        skill=skill,
        limit=limit,
        transcripts_dir=transcripts_dir,
        output_dir=output_dir,
        now=now,
    )


def main():
    server.run("stdio")


if __name__ == "__main__":
    main()
