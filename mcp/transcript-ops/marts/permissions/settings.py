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
import os
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


def _parse_json(path: Path) -> dict | None:
    """読めた JSON object を返す。読めない / object でないときだけ None。

    `{}` (空だが健全) と「読めなかった」を呼び分けるために None を分ける —
    分母の観測 (`describe_settings_paths`) が両者を別の reason に振る。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_json(path: Path) -> dict:
    return _parse_json(path) or {}


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


def _main_clone_root(repo_root: Path) -> Path | None:
    """repo_root が git worktree なら親 clone の root、そうでなければ None。

    worktree の `.git` は `gitdir: <main clone>/.git/worktrees/<name>` の 1 行 file
    (通常の clone では directory)。**subprocess を呼ばずに file 読みだけで解く** —
    mart は決定的に組み、実行環境の git に依存させない。
    """
    marker = repo_root / ".git"
    if not marker.is_file():
        return None
    try:
        content = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not content.startswith("gitdir:"):
        return None
    gitdir = Path(content.split(":", 1)[1].strip())
    if not gitdir.is_absolute():
        # `git worktree add --relative-paths` は worktree からの相対 path を書く。
        # `..` は字面で畳む (resolve() だと symlink まで解決してしまい、path と
        # resolved_path を別列で出す意味が消える)
        gitdir = Path(os.path.normpath(marker.parent / gitdir))
    if gitdir.parent.name != "worktrees":
        return None
    return gitdir.parents[2]


def _settings_candidates(repo_root: Path, global_settings: Path) -> list[dict]:
    """settings 層の候補表 (`sections` はその層を**分母として**読む section)。

    `is_layer: False` の行は分母に入れない。user scope の file 名は常に
    `settings.json` で、`.local.json` を作るのは project の local scope だけ
    (v2.1.234)。`<config dir>/settings.local.json` を置いても probe すらされない
    ため、足すと**効いていない entry が分母に混ざり**、promote 候補が黙って消える。
    列挙だけして読まない理由を残す (#583)。

    **例外**: cwd が config dir の親 (通常 `$HOME`) のとき、同 path は local scope
    の解決先そのものになり実際に効く。この場合は project 側の候補と path が一致し、
    先勝ちの重複除去で層として残る。

    global 層は section `project` でも**突合のためだけに**読む (#513) ので、
    sections に project を含めず `_describe_candidate` が別 reason を付ける。
    """
    candidates = [
        {"path": repo_root / ".claude" / "settings.json",
         "scope": "project", "sections": ("project", "all"), "is_layer": True},
        {"path": repo_root / ".claude" / "settings.local.json",
         "scope": "project_local", "sections": ("project", "all"), "is_layer": True},
    ]
    main_clone = _main_clone_root(repo_root)
    if main_clone is not None:
        # worktree セッションでは親 clone 側の settings.local.json も読まれる
        # (実測: probe されるのは親の local だけで settings.json は読まれない)。
        # gitignore される file なので worktree には無く、親側にしか実体が無い
        candidates.append(
            {"path": main_clone / ".claude" / "settings.local.json",
             "scope": "project_local_main_clone",
             "sections": ("project", "all"), "is_layer": True})
    candidates += [
        {"path": global_settings,
         "scope": "global", "sections": ("global", "all"), "is_layer": True},
        {"path": global_settings.parent / "settings.local.json",
         "scope": "global_local", "sections": (), "is_layer": False},
    ]
    # **同一 path は先勝ち**で畳む。cwd が config dir の親 (通常 `$HOME`) のとき
    # `<config dir>/settings.local.json` は local scope の解決先そのものになり、
    # 実際に効く — 層として先に並ぶ project 側が残り、`not_a_settings_layer` の
    # 行は消える。順序がこの正しさを担っているので入れ替えないこと
    deduped: list[dict] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate["path"])
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped


def enumerate_settings_sources(section: str, repo_root: Path,
                               global_settings: Path) -> list[dict]:
    """section 別に読む settings.json の一覧。"""
    return [{"path": candidate["path"], "scope": candidate["scope"]}
            for candidate in _settings_candidates(repo_root, global_settings)
            if candidate["is_layer"] and section in candidate["sections"]]


def _resolved_path(path: Path) -> str:
    """symlink 解決後の表記 (解決できなければ解決前をそのまま返す)。"""
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _describe_candidate(candidate: dict, section: str) -> dict:
    """候補 1 件を「読んだか / 読まなかったならなぜか」の行に落とす。"""
    path: Path = candidate["path"]
    exists = path.is_file()
    in_section = section in candidate["sections"]
    # global 層だけは section project でも突合用に読む (#513)
    read_for_cross_layer_match = candidate["scope"] == "global"
    if not candidate["is_layer"]:
        reason = "not_a_settings_layer"
    elif not (in_section or read_for_cross_layer_match):
        reason = "out_of_section"
    elif not exists:
        reason = "absent"
    elif _parse_json(path) is None:
        reason = "unparsed"
    else:
        reason = "read" if in_section else "cross_layer_match"
    return {
        "path": str(path),
        "resolved_path": _resolved_path(path),
        "scope": candidate["scope"],
        "exists": exists,
        "read": reason in ("read", "cross_layer_match"),
        "reason": reason,
    }


def describe_settings_paths(section: str, repo_root: Path,
                            global_settings: Path) -> list[dict]:
    """分母の観測可能性: 読んだ path と、存在するのに読まなかった path を列挙する。

    **読めた entry から逆算すると、読まなかった層は痕跡すら残らない** (#583)。
    reason は `read` (section の分母として読んだ) / `cross_layer_match` (global 突合
    のためだけに読んだ) / `absent` / `unparsed` (在るが JSON として読めない) /
    `out_of_section` / `not_a_settings_layer` に分ける。symlink は `path` (解決前) と
    `resolved_path` (解決後) の両方を出す。
    """
    return [_describe_candidate(candidate, section)
            for candidate in _settings_candidates(repo_root, global_settings)]


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
