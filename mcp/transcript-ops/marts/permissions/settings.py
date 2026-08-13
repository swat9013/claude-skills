"""設定側の分母 (permission entry / hook 設定) の列挙。

**store に入れない**: 設定は観測窓を持たない「現在の状態」であり、cache に入れると
staleness が混ざる (ADR 0031)。よって mart が実行のたびに読み直す。

汎用スキル制約により依存は Claude Code 標準ファイルのみ
(`~/.claude/settings.json` / `<repo>/.claude/settings(.local).json` /
install 済み plugin の `hooks/hooks.json`)。swat-skills 固有の hook 資産には触らない。
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from . import udf

# `Tool(pattern)` 形式を解体する。
PERMISSION_ENTRY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*(?:\((.*)\))?\s*$")

# 設定に書かれた文字列の保存上限 (mart は抜粋を出す。原型は settings.json が正本)。
SETTINGS_EXCERPT_LIMIT = 200

PERMISSION_CATEGORIES = ("allow", "deny", "ask")
SANDBOX_CATEGORY = "sandbox_excluded_commands"


@dataclasses.dataclass(frozen=True)
class PermissionEntry:
    raw: str
    category: str
    source_path: str
    scope: str
    tool: str
    pattern: str
    confidence: str
    match_kind: str


def _clip(text: str, limit: int) -> str:
    """設定文字列の抜粋。adapter (transcript の形式層) には依存させない。"""
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit]


def parse_permission_entry(raw: str, category: str, source_path: str,
                           scope: str) -> PermissionEntry:
    """`Bash(git diff:*)` 等を分解し matcher の確度ラベルを付ける。

    match_kind: `exact_tool` (括弧なし) / `exact_command` / `prefix` (`xxx:*`) /
    `glob` (`*` や `?` を含む)。

    confidence が `glob` だけ `approx` なのは、fnmatch が Claude Code 本体の
    matcher と揺れうるため — **確度は数値の隣に置く**。
    """
    match = PERMISSION_ENTRY_RE.match(raw)
    tool = match.group(1) if match else raw.strip()
    pattern = match.group(2) if match else None
    if pattern is None:
        return PermissionEntry(raw=raw, category=category, source_path=source_path,
                               scope=scope, tool=tool, pattern="",
                               confidence="exact", match_kind="exact_tool")
    if pattern.endswith(":*"):
        match_kind, confidence = "prefix", "exact"
    elif "*" in pattern or "?" in pattern:
        match_kind, confidence = "glob", "approx"
    else:
        match_kind, confidence = "exact_command", "exact"
    return PermissionEntry(raw=raw, category=category, source_path=source_path,
                           scope=scope, tool=tool, pattern=pattern,
                           confidence=confidence, match_kind=match_kind)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _sandbox_excluded_commands(data: dict) -> list[str]:
    """`sandbox.excludedCommands` を top-level と `permissions.sandbox` の両方から集める。

    片方しか読まないと excludedCommands が黙って 0 件になり、「allow entry と対に
    なる excludedCommands の連動削除確認」が無検出で素通りする (実測で 10 entry が
    0 件と報告された)。重複は初出順で除く。
    """
    permissions = data.get("permissions")
    containers = [data.get("sandbox"),
                  permissions.get("sandbox") if isinstance(permissions, dict) else None]
    seen: set[str] = set()
    out: list[str] = []
    for container in containers:
        if not isinstance(container, dict):
            continue
        for raw in (container.get("excludedCommands") or []):
            if isinstance(raw, str) and raw not in seen:
                seen.add(raw)
                out.append(raw)
    return out


def read_permission_entries(settings_path: Path, scope: str) -> list[PermissionEntry]:
    """settings.json から permission entry を列挙する。file が無ければ空 list。"""
    if not settings_path.is_file():
        return []
    data = _read_json(settings_path)
    entries: list[PermissionEntry] = []
    permissions = data.get("permissions")
    if isinstance(permissions, dict):
        for category in PERMISSION_CATEGORIES:
            for raw in (permissions.get(category) or []):
                if isinstance(raw, str):
                    entries.append(parse_permission_entry(
                        raw, category, str(settings_path), scope))
    for raw in _sandbox_excluded_commands(data):
        # excludedCommands は `Tool(...)` 形ではなく素の Bash command pattern
        # (`gh:*` / `git push:*`)。`Bash(<raw>)` として解釈しないと tool 名一致で
        # 弾かれ、常に match_count 0 になって zero-match view を汚す
        entry = parse_permission_entry(
            f"Bash({raw})", SANDBOX_CATEGORY, str(settings_path), scope)
        entries.append(dataclasses.replace(entry, raw=raw))
    return entries


def enumerate_settings_sources(section: str, repo_root: Path,
                               global_settings: Path) -> list[dict]:
    """section 別に読む settings.json の一覧。"""
    sources: list[dict] = []
    if section in ("project", "all"):
        for name, scope in (("settings.json", "project"),
                            ("settings.local.json", "project_local")):
            sources.append({"path": repo_root / ".claude" / name, "scope": scope})
    if section in ("global", "all"):
        sources.append({"path": global_settings, "scope": "global"})
    return sources


def collect_permission_entries(section: str, repo_root: Path,
                               global_settings: Path) -> list[PermissionEntry]:
    entries: list[PermissionEntry] = []
    for source in enumerate_settings_sources(section, repo_root, global_settings):
        entries.extend(read_permission_entries(source["path"], source["scope"]))
    return entries


# --- hook 設定の分母 ---------------------------------------------------------
# transcript には fire した hook しか現れないので、observed だけでは「0 回」を
# 主張できない (#478 P4)。分母は settings の `hooks` と plugin の `hooks.json` の
# 2 系統から列挙する。


def hook_name_for(event: str, matcher: str) -> str:
    """設定の (event, matcher) から観測側の `hookName` 表記を組む。

    matcher が空 / `*` の hook は観測側でも event 名だけで現れる (`Stop` 等)。
    """
    if not matcher or matcher == "*":
        return event
    return f"{event}:{matcher}"


def read_hook_entries(settings_path: Path, scope: str,
                      source_label: str) -> list[dict]:
    """settings / hooks.json の `hooks` から hook 設定を列挙する。

    1 単位は **(event, command) の組**で、同一 command に複数 matcher が紐づく場合は
    matchers に畳む — raw entry のままだと、同じ script を複数 matcher で登録した
    hook に同じ fire を重複計上してしまう。
    """
    data = _read_json(settings_path)
    grouped: dict[tuple[str, str], dict] = {}
    for event, matcher_groups in (data.get("hooks") or {}).items():
        if not isinstance(matcher_groups, list):
            continue
        for group in matcher_groups:
            if not isinstance(group, dict):
                continue
            matcher = str(group.get("matcher") or "")
            for hook in group.get("hooks") or []:
                if not isinstance(hook, dict):
                    continue
                command = str(hook.get("command") or "")
                if not command:
                    continue
                entry = grouped.setdefault((str(event), command), {
                    "hook_event": str(event),
                    "matchers": [],
                    "hook_names": [],
                    "command": _clip(command, SETTINGS_EXCERPT_LIMIT),
                    "command_key": udf.hook_command_key(command),
                    "source_path": str(settings_path),
                    "source": source_label,
                    "scope": scope,
                })
                if matcher not in entry["matchers"]:
                    entry["matchers"].append(matcher)
                    entry["hook_names"].append(hook_name_for(str(event), matcher))
    return list(grouped.values())


def enumerate_hook_sources(repo_root: Path, global_settings: Path,
                           config_dir: Path) -> list[dict]:
    """hook 設定の source 一覧 (settings 3 種 + install 済み plugin の hooks.json)。

    plugin まで見るのは、統治対象の guard hook の実体が plugin 同梱だから。
    settings だけを分母にすると、統治対象の本体が丸ごと「未設定」に見える。
    """
    sources: list[dict] = [
        {"path": global_settings, "scope": "global", "source": "settings"},
        {"path": repo_root / ".claude" / "settings.json",
         "scope": "project", "source": "settings"},
        {"path": repo_root / ".claude" / "settings.local.json",
         "scope": "project-local", "source": "settings"},
    ]
    # skills ディレクトリプラグイン (symlink 配置) は installed_plugins.json に
    # 載らない。載らない側が本 repo の配布形なので、両方の install 形を列挙する
    skills_root = config_dir / "skills"
    if skills_root.is_dir():
        for entry in sorted(skills_root.iterdir()):
            sources.append({"path": entry / "hooks" / "hooks.json",
                            "scope": f"plugin:{entry.name}", "source": "plugin"})
    for plugin_key, entries in (
            _read_json(config_dir / "plugins" / "installed_plugins.json")
            .get("plugins") or {}).items():
        if not isinstance(entries, list) or not entries:
            continue
        first = entries[0] if isinstance(entries[0], dict) else {}
        install_path = first.get("installPath")
        if not install_path:
            continue
        sources.append({"path": Path(str(install_path)) / "hooks" / "hooks.json",
                        "scope": f"plugin:{str(plugin_key).split('@')[0]}",
                        "source": "plugin"})
    # 同一実体を 2 経路で拾うと (symlink 配置 + installed_plugins) 同じ hook が
    # 2 unit に割れ、unobserved_units が水増しされる。実 path で重複を落とす
    deduped: list[dict] = []
    seen: set[str] = set()
    for source in sources:
        if not source["path"].is_file():
            continue
        try:
            key = str(source["path"].resolve())
        except OSError:
            key = str(source["path"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(source)
    return deduped


def collect_hook_entries(repo_root: Path, global_settings: Path,
                         config_dir: Path) -> list[dict]:
    entries: list[dict] = []
    for source in enumerate_hook_sources(repo_root, global_settings, config_dir):
        entries.extend(read_hook_entries(
            source["path"], source["scope"], source["source"]))
    return entries
