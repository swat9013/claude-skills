#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.0,<3"]
# ///
"""transcript-ops MCP server の entry point (stdio transport)。

決定は [ADR 0029](../../docs/adr/0029-transcript-ops-consolidation.md) と、それを
更新した [ADR 0031](../../docs/adr/0031-transcript-store-elt.md) (store 中心 ELT)。
steering の inventory 系が使う transcript 観測を一元化し、**on-disk 形式を知るのは
`store/` + `adapter/` だけ**にする (format isolation)。mart schema (観測契約) は
tool 単位に保つ。

構成: 観測契約を持つ 5 tool (`scan_permissions` / `scan_prompts` /
`select_candidates` / `scan_invocations` / `scan_overhead`) は store への query
(`marts/`)、`find_invocations` (直読み残置) と `query` (read-only ad-hoc) は
`commands/`。

**server は bucket を確定しない** — 決定的ルール (`marts/*/rules.py`) は評価するが、
出すのは候補 (`bucket_candidate`) と導出過程 (`rule_fired` / `rule_inputs`) と
未判定条件 (`open_predicates`) までで、bucket の確定は LLM 段階、最終採否は人間
([ADR 0032](../../docs/adr/0032-policy-free-refinement-deterministic-rules.md) の
出力契約 / ADR 0011 の 3 層分離)。

**mart を context に返さない**: 各 tool は `/tmp` 配下に mart / slice を書き、返すのは
出力 path と件数 meta だけ。「大きく出して絞って読む + PR 証拠として残す」消費パターンを
維持するためで、返り値に本体を載せると窓が即死する。

**docstring は Args と 1 行の役割だけに絞る** — docstring は全利用セッションの
context に常駐するコストを払う一方、mart の読み方が要るのは mart を読む段階だけ
なので、注記の置き場は mart 側の contract (`00-meta.json` の `contract.notes` /
mart の `contract.notes`) にする (ADR 0031: context 常駐コストの削減)。

**本 module が持つのは配線だけ** — tool 関数は commands / marts の `run()` へ 1 式で
委譲する。手続きがここに溜まると、LLM 向け interface (docstring) の module に
振る舞いが乗り、実装単体では検証できない合成が生まれる。

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
from commands import query as query_mod  # noqa: E402
from marts.invocations import present as invocations_mart  # noqa: E402
from marts.overhead import present as overhead_mart  # noqa: E402
from marts.permissions import present as permissions_mart  # noqa: E402
from marts.prompts import present as prompts_mart  # noqa: E402

SERVER_NAME = "transcript-ops"
SERVER_VERSION = "0.1.0"

# 手順は各 SKILL.md が持つ。ここは全利用セッションの context に常駐するので、
# 「返り値の読み方」だけに絞る (ADR 0029: context 常駐コストの圧縮)。
INSTRUCTIONS = """\
標準 transcript (~/.claude/projects) の観測を一元化する server。

- どの tool も mart / slice を /tmp 配下に書き、返すのは path と件数 meta だけ。
  中身が要るなら返ってきた path を Read する
- 読み方の注記・rule カタログは mart 側の `contract` が正本 (docstring には無い)
- **bucket は tool が確定しない**。`rule_candidates` が出すのは候補と導出過程と
  未判定条件 (`open_predicates`) までで、確定と採否は呼び出し元の SKILL.md 手順
"""

server = MCPServer(name=SERVER_NAME, version=SERVER_VERSION, instructions=INSTRUCTIONS)


@server.tool()
def scan_permissions(
    section: str = "project",
    days: int = permissions_mart.DEFAULT_DAYS,
    repo_root: str | None = None,
    output_dir: str = str(permissions_mart.DEFAULT_OUTPUT_DIR),
    transcripts_dir: str = str(permissions_mart.DEFAULT_TRANSCRIPTS_DIR),
    global_settings: str = str(permissions_mart.DEFAULT_GLOBAL_SETTINGS),
    config_dir: str = str(permissions_mart.DEFAULT_CONFIG_DIR),
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
        global_settings: global 層として読む settings の path (**この 1 本だけ**。
            sibling の settings.local.json は Claude Code が読まない層なので足さ
            ない)。既定 ~/.claude/settings.json
        config_dir: Claude Code config dir (plugin hooks の分母源)。既定 ~/.claude
        now: ISO timestamp。観測窓の起点を固定する (再現用)

    `paths` は読む順に並ぶ。各ファイルの用途・derived view の意味論・rule カタログ・
    読み方の注記は **`00-meta.json` の `contract` が正本**。
    """
    return permissions_mart.run(
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
    days: int = invocations_mart.DEFAULT_DAYS,
    repo_root: str | None = None,
    output_dir: str = str(invocations_mart.DEFAULT_OUTPUT_DIR),
    transcripts_dir: str = str(invocations_mart.DEFAULT_TRANSCRIPTS_DIR),
    config_dir: str = str(invocations_mart.DEFAULT_CONFIG_DIR),
    claude_json: str = str(invocations_mart.DEFAULT_CLAUDE_JSON),
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

    読み方の注記と rule カタログは **mart の `contract` が正本**。
    """
    return invocations_mart.run(
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
    days: int = prompts_mart.DEFAULT_DAYS,
    output_dir: str = str(prompts_mart.DEFAULT_OUTPUT_DIR),
    transcripts_dir: str = str(prompts_mart.DEFAULT_TRANSCRIPTS_DIR),
    text_limit: int = prompts_mart.DEFAULT_TEXT_LIMIT,
    now: str | None = None,
) -> dict[str, Any]:
    """人間が手入力した prompt だけの mart を書き、path を返す。

    Args:
        days: 観測窓 (日)。既定 30
        output_dir: 出力先。既定 /tmp/inventory-values。分母の違う棚卸し
            (engineering-values) は別 dir に分ける
        transcripts_dir: transcript lake。既定 ~/.claude/projects
        text_limit: 1 prompt あたりの mart 保存文字数上限。既定 4000
            (store は全文を持つ。`select_candidates` の候補は切り詰めない)
        now: ISO timestamp。観測窓の起点を固定する (再現用)

    読み方の注記は **mart の `contract` が正本**。
    """
    return prompts_mart.run_scan_prompts(
        days=days,
        output_dir=output_dir,
        transcripts_dir=transcripts_dir,
        text_limit=text_limit,
        now=now,
    )


@server.tool()
def select_candidates(
    mart: str | None = None,
    min_chars: int = prompts_mart.DEFAULT_MIN_CHARS,
    repo: str | None = None,
    all_repos: bool = False,
    limit: int | None = None,
    output_dir: str = str(prompts_mart.DEFAULT_OUTPUT_DIR),
    repo_root: str | None = None,
    days: int = prompts_mart.DEFAULT_DAYS,
    transcripts_dir: str = str(prompts_mart.DEFAULT_TRANSCRIPTS_DIR),
    now: str | None = None,
) -> dict[str, Any]:
    """store を長さ / repo / 正規形で絞り、読み順を確定した slice を書いて path を返す。

    Args:
        mart: provenance。`scan_prompts` が返した mart JSON の path を渡すと、
            その観測窓 (`days` 相当) を再利用して候補を絞る。省略時は `days` から
            窓を作る (既定 30) — mart の事前生成は不要
        min_chars: 候補に含める text_chars の下限。既定 60
        repo: 完全一致で絞る repo 識別子
        all_repos: repo 絞り込みを外して全 project 横断で見る
        limit: 出力件数の上限。打ち切りは `meta.truncated_by_limit` に出る
        output_dir: 出力先。既定 /tmp/inventory-values
        repo_root: repo 既定解決の起点。省略時は server プロセスの cwd
        days: `mart` 省略時の観測窓 (日)。既定 30 (`mart` を渡した場合は無視され、
            その meta の窓を使う)
        transcripts_dir: transcript lake。既定 ~/.claude/projects
        now: ISO timestamp。出力ファイル名の stamp を固定する (再現用)

    読み方の注記と rule カタログは **slice の `contract` が正本**。
    """
    return prompts_mart.run_select_candidates(
        mart=mart,
        min_chars=min_chars,
        repo=repo,
        all_repos=all_repos,
        limit=limit,
        output_dir=output_dir,
        repo_root=repo_root,
        days=days,
        transcripts_dir=transcripts_dir,
        now=now,
    )


@server.tool()
def scan_overhead(
    days: int = overhead_mart.DEFAULT_DAYS,
    repo_root: str | None = None,
    all_repos: bool = False,
    output_dir: str = str(overhead_mart.DEFAULT_OUTPUT_DIR),
    transcripts_dir: str = str(overhead_mart.DEFAULT_TRANSCRIPTS_DIR),
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

    読み方の注記は **mart の `meta.notes` / `meta.token_estimator` が正本**。
    """
    return overhead_mart.run(
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

    読み方の注記は **slice の `meta.notes` が正本**。
    """
    return find_invocations_mod.run(
        skill=skill,
        limit=limit,
        transcripts_dir=transcripts_dir,
        output_dir=output_dir,
        now=now,
    )


@server.tool()
def query(
    sql: str,
    limit: int = query_mod.DEFAULT_ROW_LIMIT,
    transcripts_dir: str = str(query_mod.DEFAULT_TRANSCRIPTS_DIR),
    output_dir: str = str(query_mod.DEFAULT_OUTPUT_DIR),
    now: str | None = None,
) -> dict[str, Any]:
    """store へ read-only の ad-hoc query を投げ、結果を書いて path を返す。

    Args:
        sql: 単一の SELECT / WITH 文。複数文・`PRAGMA` / `ATTACH` は拒否する
        limit: 行数 cap。既定 500 / 上限 10,000。打ち切りは
            `meta.truncated_by_limit` に出る
        transcripts_dir: transcript lake。既定 ~/.claude/projects
        output_dir: 出力先。既定 /tmp/transcript-ops-query
        now: ISO timestamp。出力ファイル名の stamp を固定する (再現用)

    **想定外の追加検査だけに使う。** 恒常的に必要になった集計は mart の分割出力 /
    derived view の拡張として提案する — ad-hoc query を手順に埋めると、観測契約を
    持たない集計が棚卸しの既定経路になる。schema は
    `SELECT sql FROM sqlite_master` で引ける。
    """
    return query_mod.run(
        sql=sql,
        limit=limit,
        transcripts_dir=transcripts_dir,
        output_dir=output_dir,
        now=now,
    )


def main():
    server.run("stdio")


if __name__ == "__main__":
    main()
