"""`invocations` の分母 (install 済みの skill / MCP server / plugin) の列挙。

install 済みの一覧は観測窓を持たない今の状態で、lake の事実ではない (設計規則 (9))。呼ばれるたびに
Claude Code の標準の file だけを読み直す。

invariant:
1. plugin と個人 skill の見分けは manifest の有無で決める
2. skill の分母は Claude Code が読み込む skill だけを持つ (既定の scan は `skills/<名前>/SKILL.md` の 1 段で、
   manifest の `skills` は skill の dir か skill の dir を並べた dir を足す。manifest の文字列形で phantom を
   生やさず、SKILL.md の無い entry と、どちらにも当たらない深さの SKILL.md を数えない)
3. 同梱の MCP server は登録済みの plugin の名前で 1 回だけ数える (未登録の repo は project scope のまま)
4. MCP の分母は `~/.claude.json` の global / project-entry / project-enabled を scope 付きで列挙し、同じ名前は
   Claude Code が載せる scope の 1 行にする
5. 在るのに読めない利用者の設定 file は空の分母に倒さずに止まる。読めない manifest と `.mcp.json` は、Claude Code も
   読み込まないので無いものとする
"""

from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path

from named_query import Stopped

PLUGIN_MANIFEST_REL = Path(".claude-plugin") / "plugin.json"


def _read_json_object(path: Path) -> dict | None:
    """無い file は空。在るのに読めないか object でない file は None。"""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _load_user_config(path: Path) -> dict:
    """利用者の設定 (`~/.claude.json`・`installed_plugins.json`)。読めないのを空にすると、install 済みの unit が黙って
    分母から消えるので止まる。"""
    data = _read_json_object(path)
    if data is None:
        raise Stopped("config_unreadable", f"{path} を読めないか JSON の object ではありません。file を直して呼び直してください")
    return data


def _load_component(path: Path) -> dict:
    """plugin の manifest と `.mcp.json`。読めない file は Claude Code も読み込まないので、その component は無いものとする。"""
    return _read_json_object(path) or {}


def _marketplace_plugins(config_dir: Path) -> dict[str, str | None]:
    """plugin の名前 → install 先。key は `<名前>@<marketplace>` で、複数の install は先頭を採る。"""
    data = _load_user_config(config_dir / "plugins" / "installed_plugins.json")
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


def _child_skills(scan_dir: Path) -> dict[str, Path]:
    """Claude Code の scan は 1 段だけ降りる: `<scan_dir>/<名前>/SKILL.md` の名前 → dir。"""
    return {skill_md.parent.name: skill_md.parent for skill_md in sorted(scan_dir.glob("*/SKILL.md"))}


def _scanned_skills(scan_dir: Path) -> dict[str, Path]:
    """manifest の 1 つの entry の skill。SKILL.md を直下に持てばその dir が skill、無ければ子の dir が skill。"""
    return {scan_dir.name: scan_dir} if (scan_dir / "SKILL.md").is_file() else _child_skills(scan_dir)


def _manifest_skills(install_path: Path, manifest: dict) -> dict[str, Path]:
    """manifest の `skills` が足す skill の名前 → dir。文字列 1 本の形は 1 要素の列として読む。"""
    declared = manifest.get("skills")
    entries = [declared] if isinstance(declared, str) else (declared or [])
    if not isinstance(entries, list):
        return {}
    skills: dict[str, Path] = {}
    for entry in entries:
        if isinstance(entry, str):
            skills.update(_scanned_skills(install_path / entry.removeprefix("./").rstrip("/")))
    return skills


def manifest_unreadable(install_path: Path) -> bool:
    """manifest が在るのに読めない plugin。Claude Code はその plugin を読み込まないので、skill も同梱の server も持たない。"""
    return _read_json_object(install_path / PLUGIN_MANIFEST_REL) is None


def unreadable_components(plugins: dict[str, str | None], repo_root: Path) -> list[str]:
    """在るのに読めない manifest と `.mcp.json` の path。分母から外したことを読み手に見せる。"""
    roots = {Path(path) for path in plugins.values() if path} | {repo_root}
    paths = {root / PLUGIN_MANIFEST_REL for root in roots if manifest_unreadable(root)}
    paths |= {root / ".mcp.json" for root in roots if _read_json_object(root / ".mcp.json") is None}
    return sorted(str(path) for path in paths)


def _server_names(doc: dict) -> list[str]:
    """`.mcp.json` の `mcpServers` の名前。object でない形は名前を持たない。"""
    servers = doc.get("mcpServers")
    return list(servers) if isinstance(servers, dict) else []


def _plugin_server_names(doc: dict) -> list[str] | None:
    """plugin の `.mcp.json` の server の名前。`mcpServers` で包まない形は top-level の key を読み、`mcpServers` が object でない
    形は名前が分からない (None)。flat の形の object でない値 (`$schema` など) は server ではない。"""
    if "mcpServers" not in doc:
        return [name for name, server in doc.items() if isinstance(server, dict)]
    return list(doc["mcpServers"]) if isinstance(doc["mcpServers"], dict) else None


def _plugin_skills(install_path: Path) -> dict[str, Path]:
    if manifest_unreadable(install_path):
        return {}
    manifest = _load_component(install_path / PLUGIN_MANIFEST_REL)
    return {**_child_skills(install_path / "skills"), **_manifest_skills(install_path, manifest)}


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
        for skill_name, skill_dir in _child_skills(root).items():
            skills.append({"id": skill_name, "source": "config", "plugin": None,
                           "install_path": str(skill_dir), "skill_md": str(skill_dir / "SKILL.md")})
    unique: dict[str, dict] = {}
    for skill in skills:
        unique.setdefault(skill["id"], skill)
    return list(unique.values())


def _bundling_plugin_of(repo_root: Path, plugin_ids: Collection[str]) -> str | None:
    """repo 自身が登録済みの plugin の root なら、その名前。

    そのとき repo の `.mcp.json` は同梱の定義で、project の分母に割ると呼出しのない偽の候補になる。
    """
    name = _load_component(repo_root / PLUGIN_MANIFEST_REL).get("name")
    return name if isinstance(name, str) and name in plugin_ids else None


def enumerate_mcp_servers(claude_json: Path, repo_root: Path, plugin_ids: Collection[str]) -> list[dict]:
    """ローカルの設定に出る MCP server。claude.ai の connector は出ないので、呼出しの補完か読み手の補完に回る。

    同じ名前の server は Claude Code が local (project-entry) > project (`.mcp.json`) > user (global) の順で 1 つだけ
    載せるので、その順に並べて最初の scope の 1 行にする。plugin の root の repo の `.mcp.json` は同梱の定義だが、
    承認した名前は bare の名前の project の server としても別に載る。
    """
    data = _load_user_config(claude_json)
    bundling_plugin = _bundling_plugin_of(repo_root, plugin_ids)
    servers: list[tuple[str, str]] = []
    mcp_doc = _load_component(repo_root / ".mcp.json")
    mcp_json = _server_names(mcp_doc)
    for key in (str(repo_root), str(repo_root.resolve())):
        entry = (data.get("projects") or {}).get(key)
        if isinstance(entry, dict):
            servers += [(name, "project-entry") for name in entry.get("mcpServers") or {}]
            # 承認が `.mcp.json` から消えた名前を指すなら、その server は載らない
            servers += [(name, "project-enabled") for name in entry.get("enabledMcpjsonServers") or []
                        if name in mcp_json]
    if bundling_plugin:
        servers += [(f"plugin:{bundling_plugin}:{name}", "plugin-bundled") for name in _plugin_server_names(mcp_doc) or []]
    else:
        servers += [(name, "project-mcp-json") for name in mcp_json]
    servers += [(name, "global") for name in data.get("mcpServers") or {}]
    scopes: dict[str, str] = {}
    for name, scope in servers:
        scopes.setdefault(name, scope)
    return [{"id": name, "source": "config", "scope": scope} for name, scope in scopes.items()]


def enumerate_bundled_servers(plugins: dict[str, str | None]) -> dict[str, set[str]]:
    """同梱の MCP server の名前が分かる plugin の名前 → その名前。`.mcp.json` と manifest の inline の `mcpServers` から読む。

    manifest の `mcpServers` が path や配列の形の plugin と、`.mcp.json` の `mcpServers` が object でない plugin は名前が
    分からないので載せない。`mcpServers` で包まない `.mcp.json` は top-level の key を名前として読む。manifest が読めない
    plugin は Claude Code が読み込まないので載せない。
    """
    bundled: dict[str, set[str]] = {}
    for plugin_id, install_path in plugins.items():
        if not install_path or manifest_unreadable(Path(install_path)):
            continue
        inline = _load_component(Path(install_path) / PLUGIN_MANIFEST_REL).get("mcpServers")
        if inline is not None and not isinstance(inline, dict):
            continue
        names = _plugin_server_names(_load_component(Path(install_path) / ".mcp.json"))
        if names is None:
            continue
        bundled[plugin_id] = set(names) | set(inline or {})
    return bundled
