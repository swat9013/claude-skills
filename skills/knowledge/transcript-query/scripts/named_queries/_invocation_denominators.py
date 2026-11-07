"""`invocations` の分母 (install 済みの skill / MCP server / plugin) の列挙。

install 済みの一覧は観測窓を持たない今の状態で、lake の事実ではない (設計規則 (9))。呼ばれるたびに
Claude Code の標準の file だけを読み直す。

invariant:
1. plugin と個人 skill の見分けは manifest の有無で決める
2. skill の分母は実体のある skill だけを持つ (manifest の文字列形で phantom を生やさず、SKILL.md の無い
   entry と skill の中の入れ子の SKILL.md を数えない)
3. 同梱の MCP server は登録済みの plugin の名前で 1 回だけ数える (未登録の repo は project scope のまま)
4. MCP の分母は `~/.claude.json` の global / project-entry / project-enabled を scope 付きで列挙する
"""

from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path

PLUGIN_MANIFEST_REL = Path(".claude-plugin") / "plugin.json"


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _marketplace_plugins(config_dir: Path) -> dict[str, str | None]:
    """plugin の名前 → install 先。key は `<名前>@<marketplace>` で、複数の install は先頭を採る。"""
    data = _load_json(config_dir / "plugins" / "installed_plugins.json")
    plugins: dict[str, str | None] = {}
    for key, entries in (data.get("plugins") or {}).items():
        if not isinstance(entries, list) or not entries:
            continue
        entry = entries[0] if isinstance(entries[0], dict) else {}
        plugins[key.split("@")[0]] = entry.get("installPath")
    return plugins


def _skills_dir_plugins(config_dir: Path) -> dict[str, str | None]:
    """`~/.claude/skills/<名前>/` に直置き (多くは symlink) された plugin。

    marketplace を経ないので installed_plugins.json に出ない。名前は dir の名前で、manifest の `name` ではない —
    Claude Code が namespace に使うのが dir の名前だから。install 先も symlink を解決しない。
    """
    skills_dir = config_dir / "skills"
    if not skills_dir.is_dir():
        return {}
    return {entry.name: str(entry) for entry in sorted(skills_dir.iterdir())
            if (entry / PLUGIN_MANIFEST_REL).is_file()}


def enumerate_installed_plugins(config_dir: Path) -> dict[str, str | None]:
    """plugin の分母と skill の分母の源。源を分けると片方にしか出ない plugin が生まれ、plugin の行が補完に落ちる。"""
    plugins = _marketplace_plugins(config_dir)
    for name, install_path in _skills_dir_plugins(config_dir).items():
        plugins.setdefault(name, install_path)
    return plugins


def _manifest_skills(install_path: Path) -> dict[str, Path]:
    """manifest の `skills` が指す skill の名前 → dir。文字列 1 本の形は 1 要素の列として読み、SKILL.md の実在を見る。"""
    declared = _load_json(install_path / PLUGIN_MANIFEST_REL).get("skills")
    entries = [declared] if isinstance(declared, str) else (declared or [])
    if not isinstance(entries, list):
        return {}
    skill_dirs = [install_path / entry.removeprefix("./").rstrip("/") for entry in entries if isinstance(entry, str)]
    return {skill_dir.name: skill_dir for skill_dir in skill_dirs if (skill_dir / "SKILL.md").is_file()}


def _walked_skills(skills_root: Path) -> dict[str, Path]:
    """skills/ の下を歩いて skill の名前 → dir を拾う。skill の dir の内側の SKILL.md はその skill の資産で、別の skill ではない。"""
    skill_dirs: set[Path] = set()
    for skill_md in sorted(skills_root.rglob("SKILL.md"), key=lambda path: len(path.parts)):
        if not any(ancestor in skill_dirs for ancestor in skill_md.parent.parents):
            skill_dirs.add(skill_md.parent)
    return {skill_dir.name: skill_dir for skill_dir in sorted(skill_dirs)}


def _plugin_skills(install_path: Path) -> dict[str, Path]:
    skills_root = install_path / "skills"
    return {**(_walked_skills(skills_root) if skills_root.is_dir() else {}), **_manifest_skills(install_path)}


def enumerate_skills(config_dir: Path, repo_root: Path) -> list[dict]:
    """全 source の skill。id は plugin なら `<plugin>:<skill>`、個人と project は `<skill>` で、呼び出しの表記に揃える。"""
    skills: list[dict] = []
    for plugin_id, install_path in enumerate_installed_plugins(config_dir).items():
        if not install_path:
            continue
        for skill_name, skill_dir in sorted(_plugin_skills(Path(install_path)).items()):
            skills.append({"id": f"{plugin_id}:{skill_name}", "source": "config", "plugin": plugin_id,
                           "install_path": install_path, "skill_md": str(skill_dir / "SKILL.md")})
    for root in (config_dir / "skills", repo_root / ".claude" / "skills"):
        if root.is_dir():
            for skill_md in sorted(root.glob("*/SKILL.md")):
                skills.append({"id": skill_md.parent.name, "source": "config", "plugin": None,
                               "install_path": str(skill_md.parent), "skill_md": str(skill_md)})
    unique: dict[str, dict] = {}
    for skill in skills:
        unique.setdefault(skill["id"], skill)
    return list(unique.values())


def _bundling_plugin_of(repo_root: Path, plugin_ids: Collection[str]) -> str | None:
    """repo 自身が登録済みの plugin の root なら、その名前。

    そのとき repo の `.mcp.json` は同梱の定義で、project の分母に割ると呼出しのない偽の候補になる。
    """
    name = _load_json(repo_root / PLUGIN_MANIFEST_REL).get("name")
    return name if isinstance(name, str) and name in plugin_ids else None


def enumerate_mcp_servers(claude_json: Path, repo_root: Path, plugin_ids: Collection[str]) -> list[dict]:
    """ローカルの設定に出る MCP server。claude.ai の connector は出ないので、呼出しの補完か読み手の補完に回る。"""
    servers: list[tuple[str, str]] = []
    data = _load_json(claude_json)
    servers += [(name, "global") for name in data.get("mcpServers") or {}]
    for key in (str(repo_root), str(repo_root.resolve())):
        entry = (data.get("projects") or {}).get(key)
        if isinstance(entry, dict):
            servers += [(name, "project-entry") for name in entry.get("mcpServers") or {}]
            servers += [(name, "project-enabled") for name in entry.get("enabledMcpjsonServers") or []]
    bundling_plugin = _bundling_plugin_of(repo_root, plugin_ids)
    for name in _load_json(repo_root / ".mcp.json").get("mcpServers") or {}:
        servers.append((f"plugin:{bundling_plugin}:{name}", "plugin-bundled") if bundling_plugin
                       else (name, "project-mcp-json"))
    return [{"id": name, "source": "config", "scope": scope} for name, scope in dict.fromkeys(servers)]
