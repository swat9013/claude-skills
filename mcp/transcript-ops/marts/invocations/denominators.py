"""invocations mart の設定側分母 (skill / mcp_server / plugin の列挙)。

**store に入れない**: install 済み一覧は観測窓を持たない「現在の状態」であり、
cache に入れると staleness が混ざる (ADR 0031)。よって mart が実行のたびに読み直す。

汎用スキル制約により依存は Claude Code 標準ファイルのみ (installed_plugins.json /
plugin manifest / ~/.claude/skills/ / 現 repo .claude/skills/ / ~/.claude.json /
~/.mcp.json)。swat-skills 固有資産 (tool-signatures.jsonl 等) には触らない。
"""

from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path

# plugin root の目印。`~/.claude/skills/<name>/` がこの manifest を持つとき、その
# entry は個人 skill 1 本ではなく Skills ディレクトリプラグイン 1 本を指す。
PLUGIN_MANIFEST_REL = Path(".claude-plugin") / "plugin.json"

# Skills ディレクトリ配置の plugin は installed_plugins.json に現れないため
# marketplace 名を持たない。key の形 (`<name>@<marketplace>`) だけ揃えるための値。
SKILLS_DIR_MARKETPLACE = "skills-dir"


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _marketplace_plugins(config_dir: Path) -> dict[str, dict]:
    """installed_plugins.json から plugin 一覧を返す。

    plugin key は "<name>@<marketplace>" 形式なので `@` 前を name として扱う。
    複数 install がある場合は先頭 entry を採る (実運用では 1 件想定)。
    """
    data = _load_json(config_dir / "plugins" / "installed_plugins.json")
    plugins: dict[str, dict] = {}
    for pkey, entries in (data.get("plugins") or {}).items():
        if not isinstance(entries, list) or not entries:
            continue
        entry = entries[0] if isinstance(entries[0], dict) else {}
        name = pkey.split("@")[0] if isinstance(pkey, str) else str(pkey)
        plugins[name] = {
            "id": name,
            "key": pkey,
            "install_path": entry.get("installPath"),
            "version": entry.get("version"),
        }
    return plugins


def _skills_dir_plugins(config_dir: Path) -> dict[str, dict]:
    """`~/.claude/skills/<name>/` に直置き (多くは symlink) された plugin を返す。

    marketplace 経由でないため installed_plugins.json には 1 行も出ない。これを
    見ないと symlink 配置の plugin が丸ごと分母から落ちる (#508 a)。plugin 名は
    entry のディレクトリ名を採る — Claude Code が namespace に使うのがこの名前で、
    manifest の `name` ではない。install_path も entry path のまま (symlink を
    解決しない) にする: `${CLAUDE_PLUGIN_ROOT}` が解決する path と同じ表記になる。
    """
    skills_dir = config_dir / "skills"
    if not skills_dir.is_dir():
        return {}
    plugins: dict[str, dict] = {}
    for entry in sorted(skills_dir.iterdir()):
        if not (entry / PLUGIN_MANIFEST_REL).is_file():
            continue
        name = entry.name
        plugins[name] = {
            "id": name,
            "key": f"{name}@{SKILLS_DIR_MARKETPLACE}",
            "install_path": str(entry),
            "version": _load_json(entry / PLUGIN_MANIFEST_REL).get("version"),
        }
    return plugins


def enumerate_installed_plugins(config_dir: Path) -> dict[str, dict]:
    """marketplace install と Skills ディレクトリ配置を合わせた plugin 一覧。

    plugin 分母と skill 分母の両方がここを源にする — 源を分けると片方にしか出ない
    plugin が生まれ、配下 skill は列挙されるのに plugin 行が session-observed に
    落ちる (= `denominator_source_config` が永久に false) といった不整合になる。
    """
    plugins = _marketplace_plugins(config_dir)
    for name, meta in _skills_dir_plugins(config_dir).items():
        plugins.setdefault(name, meta)
    return plugins


def _manifest_skills(install_path: Path) -> set[str]:
    """plugin.json の `skills` が指す skill 名。SKILL.md が実在する entry だけ採る。

    `skills` を配列でなく文字列 1 本で書く plugin が実在する (`"skills": "./skills/"`)。
    str をそのまま反復すると一意文字ぶんの phantom skill が分母に入るため、str は
    1 要素の列として扱う。実在検査はそこから生まれる `<plugin>:skills` のような
    別の phantom も含め、**全 plugin の manifest entry** に効かせる (#508 c) —
    manifest から漏れた skill は下の walk が拾うので、検査で分母が痩せることはない。
    """
    data = _load_json(install_path / PLUGIN_MANIFEST_REL)
    declared = data.get("skills")
    entries = [declared] if isinstance(declared, str) else (declared or [])
    if not isinstance(entries, list):
        return set()
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, str):
            continue
        skill_dir = install_path / entry.removeprefix("./").rstrip("/")
        if (skill_dir / "SKILL.md").is_file():
            names.add(skill_dir.name)
    return names


def _walked_skills(skills_root: Path) -> set[str]:
    """skills/ 配下を歩いて skill 名を拾う (manifest 未記載の skill を落とさない)。

    **skill ディレクトリの内側に在る SKILL.md は数えない** — `<skill>/references/`
    配下の SKILL.md はその skill の資産であって別の skill ではない (実在する形)。
    浅い順に見て、既知の skill ディレクトリの子孫を落とす。
    """
    names: set[str] = set()
    skill_dirs: set[Path] = set()
    for skill_md in sorted(skills_root.rglob("SKILL.md"), key=lambda p: len(p.parts)):
        skill_dir = skill_md.parent
        if any(ancestor in skill_dirs for ancestor in skill_dir.parents):
            continue
        skill_dirs.add(skill_dir)
        names.add(skill_dir.name)
    return names


def _scan_plugin_skills(install_path: Path) -> set[str]:
    """plugin dir から skill 名の集合を返す。manifest の宣言と
    skills/ の走査を合わせる (manifest 未記載の skill も分母に載せる)。
    """
    names = _manifest_skills(install_path)
    skills_root = install_path / "skills"
    if skills_root.is_dir():
        names |= _walked_skills(skills_root)
    return names


def enumerate_skills(config_dir: Path, repo_root: Path) -> list[dict]:
    """全 source から skill id 一覧を返す。id の付け方:

    - plugin skill: "<plugin>:<skill>" (Claude Code の Skill invocation 表現に一致)
    - personal (~/.claude/skills/*) / current repo (.claude/skills/*): "<skill>"

    project skill に `project:` を冠さないのは、Claude Code が提示にも invocation にも
    その prefix を使わないため。冠すると分子と 1 件も join できず、実績のある skill が
    丸ごと偽の未使用候補になる (#508 b)。
    """
    skills: list[dict] = []
    for plugin_id, meta in enumerate_installed_plugins(config_dir).items():
        ipath = meta.get("install_path")
        if not ipath:
            continue
        pdir = Path(str(ipath))
        for skill_name in sorted(_scan_plugin_skills(pdir)):
            skills.append({
                "id": f"{plugin_id}:{skill_name}",
                "source": "config",
                "plugin": plugin_id,
                "install_path": str(pdir),
            })
    for root in (config_dir / "skills", repo_root / ".claude" / "skills"):
        if not root.is_dir():
            continue
        for skill_md in sorted(root.glob("*/SKILL.md")):
            skills.append({
                "id": skill_md.parent.name,
                "source": "config",
                "plugin": None,
                "install_path": str(skill_md.parent),
            })
    # dedupe (id 一致は先勝ち)
    seen = set()
    deduped = []
    for s in skills:
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        deduped.append(s)
    return deduped


def bundling_plugin_of(repo_root: Path, plugin_ids: Collection[str]) -> str | None:
    """repo 自身が登録済み plugin の root なら、その plugin 名を返す。

    このとき repo の `.mcp.json` は plugin 同梱定義と project scope 定義の二役で
    読まれる ([`docs/plugin-spec.md`](../../../../docs/plugin-spec.md) の
    「MCP server の同梱」)。分母を 2 経路に割ると invocation 実績は plugin 側の id に
    全部乗り、project 側が呼出 0 件の偽の未使用候補になる (#508 d)。
    """
    name = _load_json(repo_root / PLUGIN_MANIFEST_REL).get("name")
    return name if isinstance(name, str) and name in plugin_ids else None


def enumerate_mcp_servers(claude_json: Path, repo_root: Path,
                          plugin_ids: Collection[str]) -> list[dict]:
    """~/.claude.json の mcpServers + project entry + <repo>/.mcp.json から一覧。

    dedupe key は (id, scope)。claude.ai connectors はローカル config には出現
    しないため、LLM 段階で session-observed として補完される (mart 側で自動 tag)。

    `plugin_ids` は登録済み plugin 名の集合。repo が plugin root 自体かどうかは
    これと突き合わせないと判定できない (単に manifest があるだけの未登録 repo は
    plugin として動いていない)。
    """
    servers: list[dict] = []
    data = _load_json(claude_json)
    for name in (data.get("mcpServers") or {}).keys():
        servers.append({"id": name, "source": "config", "scope": "global"})
    projects = data.get("projects") or {}
    # project key は絶対 path (~/.claude.json の格納形式)
    for key in (str(repo_root), str(repo_root.resolve())):
        entry = projects.get(key)
        if not isinstance(entry, dict):
            continue
        for name in (entry.get("mcpServers") or {}).keys():
            servers.append({"id": name, "source": "config", "scope": "project-entry"})
        for name in entry.get("enabledMcpjsonServers") or []:
            servers.append({"id": name, "source": "config", "scope": "project-enabled"})
    bundling_plugin = bundling_plugin_of(repo_root, plugin_ids)
    for name in (_load_json(repo_root / ".mcp.json").get("mcpServers") or {}).keys():
        if bundling_plugin:
            # `/mcp` 上の表示名と同形。呼出側の `mcp__plugin_<p>_<s>__<tool>` とは
            # present.normalize_mcp_server を通して join する
            servers.append({"id": f"plugin:{bundling_plugin}:{name}",
                            "source": "config", "scope": "plugin-bundled"})
        else:
            servers.append({"id": name, "source": "config",
                            "scope": "project-mcp-json"})
    seen = set()
    deduped = []
    for s in servers:
        key = (s["id"], s["scope"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    return deduped


__all__: list[str] = [
    "bundling_plugin_of",
    "enumerate_installed_plugins",
    "enumerate_skills",
    "enumerate_mcp_servers",
]
