"""dispatch の前提を**非 fatal に**機械検査する (#592 の doctor)。

検査対象は導入前提の正本 (`skills/util/orchestrator/README.md` §6 のチェックリスト) と
1:1 に対応させてある。各検査は独立に走り、1 つ落ちても後続を止めない — doctor の成果物は
「最初の失敗」ではなく**不足項目の一覧**だから。

**なぜ server 側に置くか (script 化に戻さないこと)**: Bash tool から走らせると sandbox の中に
なり、`herdr status` は `sandbox.excludedCommands` に `herdr:*` が無い環境で必ず落ちる。
doctor が検出したい状態 (settings 不足) が「herdr が壊れている」に化けて、原因の切り分けが
できなくなる。server プロセスの subprocess は Bash tool を通らないので sandbox の影響を受けず、
settings 不足と herdr 不在を区別して報告できる。

**fail-closed な preflight (`pane_herdr.HerdrAdapter.ensure_ready`) とは役割が違う。** あちらは
最初の失敗で止めて誤 dispatch を防ぐ側で、副作用 (自 pane の残骸 label の付け直し) も持つ。
doctor から呼ぶと 1 項目目で止まり、状態まで書き換える。共有するのは**定数だけ**にして、
判定は非 fatal に書き直してある。

status は 3 値:

| status | 意味 | 読み方 |
|---|---|---|
| `ok` | 検査して成立 | — |
| `missing` | 検査して不成立 | `items` に不足を逐語で持つ (件数ではなく entry そのもの) |
| `unknown` | 検査できなかった | 材料が読めない層がある。**missing に倒さない** |

`unknown` を `missing` に倒さないのは、settings の偽 red が「既に足りている設定を人間に
編集させる」方向の害を持つため。逆に**偽 green は doctor の存在意義を消す**ので、permission
entry の照合は完全一致だけで green にする — 広い entry が覆っている可能性は `related` に情報
として載せるが、根拠にはしない (subsumption の規則は本 repo でも未検証:
`docs/research/2026-08-01-permission-rule-wildcard-matching.md`)。

`visibility` は**不成立時の見え方** (README の同名の列)。`silent` の項目 (宣言 config /
plugin 名) が、この機構で最も高くつく前提 — 誤った置き場を黙って観測し続ける。
"""

import json
import os
from pathlib import Path

import pane as pane_mod
import pane_herdr
import proc
import project as project_mod
import refs
import repo_key as repo_key_mod

SUBPROCESS_TIMEOUT_SEC = 60

# ADR 0038 の配布契約。**publisher 側の宣言**であって harness が登録した名前ではない
PLUGIN_NAME = "swat-skills"

# 配布物 (plugin root) — `mcp/dispatch-ops/doctor.py` から 2 つ上がる。symlink 配布でも
# marketplace の cache コピーでも、server file と同じ配布物の中に settings 正本が居る
BUNDLE_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_MANIFEST = BUNDLE_ROOT / ".claude-plugin" / "plugin.json"
SETTINGS_TEMPLATE = BUNDLE_ROOT / "settings" / "settings.local.json"

# 効いている settings を読む層。managed settings (/Library/Application Support/ClaudeCode) は
# 読まない — 読めない環境があり、読めたつもりの false negative を作るより開示するほうが安い
USER_SETTINGS = Path("~/.claude/settings.json")
PROJECT_SETTINGS_NAMES = ("settings.json", "settings.local.json")

# 台帳ディレクトリを指す allowWrite entry の目印 (path 表記は `~` 展開の有無で揺れる)
LEDGER_WRITE_MARK = "issue-dispatch"

# tracker ごとの認証検査コマンド。adapter を持たない tracker (jira) は CLI を持たない
TRACKER_AUTH_COMMANDS = {"gh": ["gh", "auth", "status"], "glab": ["glab", "auth", "status"]}


class DoctorError(RuntimeError):
    """検査そのものを実行できない (root を解けない等)。個々の検査の不成立はこれではない。"""


run_command = proc.command_runner(error=DoctorError, timeout_sec=SUBPROCESS_TIMEOUT_SEC)


def _check(check_id, title, status, visibility, detail, remedy=None, items=None, related=None):
    """検査 1 件の結果。`items` は不足の逐語 (件数に丸めない)。"""
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "visibility": visibility,
        "detail": detail,
        "remedy": remedy,
        "items": list(items or []),
        "related": list(related or []),
    }


def _probe(argv):
    """外部コマンドを起動して `(rc, stdout, stderr)` を返す。起動できなければ rc=None。

    `proc` の runner は起動失敗と timeout を例外にするが、doctor では**それも観測結果**
    (= その binary が無い) なので、ここで戻り値へ潰す。
    """
    try:
        return run_command(argv)
    except DoctorError as exc:
        return None, "", str(exc)


# --- 検査 1 件ずつ -------------------------------------------------------------------


def check_messaging(env):
    """cross-session messaging を受信できるセッションか (README §6 #1)。"""
    socket = env.get(pane_mod.MESSAGING_SOCKET_ENV)
    if socket:
        return _check(
            "messaging", "cross-session messaging の受信", "ok", "loud",
            f"{pane_mod.MESSAGING_SOCKET_ENV}={socket}",
        )
    return _check(
        "messaging", "cross-session messaging の受信", "missing", "loud",
        f"{pane_mod.MESSAGING_SOCKET_ENV} が未設定",
        remedy="現行 binary で**新規起動**した Claude Code セッションから実行し直す "
        "(旧 binary の resume セッションは送信できても受信できず、worker の質問も "
        "observer の escalation も誰にも届かない)",
        items=[pane_mod.MESSAGING_SOCKET_ENV],
    )


def check_herdr_session(env):
    """herdr session 内で server が起動しているか (README §6 #3)。"""
    missing = []
    if env.get("HERDR_ENV") != "1":
        missing.append("HERDR_ENV=1")
    for name in ("HERDR_PANE_ID", "HERDR_WORKSPACE_ID"):
        if not env.get(name):
            missing.append(name)
    if missing:
        return _check(
            "herdr_session", "herdr session 内での起動", "missing", "loud",
            f"env が揃っていない: {', '.join(missing)}",
            remedy="herdr session 内で Claude Code を起動し直す (herdr 以外の terminal "
            "multiplexer は非対応)",
            items=missing,
        )
    return _check(
        "herdr_session", "herdr session 内での起動", "ok", "loud",
        f"HERDR_PANE_ID={env['HERDR_PANE_ID']} / HERDR_WORKSPACE_ID={env['HERDR_WORKSPACE_ID']}",
    )


def check_herdr_daemon():
    """herdr が PATH に在り daemon へ疎通できるか (README §6 #2)。"""
    rc, _out, err = _probe(["herdr", "status"])
    if rc == 0:
        return _check("herdr_daemon", "herdr daemon への疎通", "ok", "loud", "herdr status が exit 0")
    if rc is None:
        return _check(
            "herdr_daemon", "herdr daemon への疎通", "missing", "loud",
            f"herdr を起動できない: {err.strip()}",
            remedy="herdr を install して PATH へ通す (https://github.com/HerdrHQ/herdr)",
            items=["herdr"],
        )
    return _check(
        "herdr_daemon", "herdr daemon への疎通", "missing", "loud",
        f"herdr status が失敗 (exit {rc}): {err.strip()}",
        remedy="herdr daemon を起動する",
    )


def check_herdr_integration():
    """herdr の Claude 連携 hook が現行版か (README §6 #4)。"""
    rc, out, err = _probe(["herdr", "integration", "status"])
    if rc == 0 and pane_herdr.HOOK_CURRENT.search(out):
        return _check(
            "herdr_integration", "herdr の Claude 連携 hook", "ok", "loud",
            "herdr integration status に `claude: current` が在る",
        )
    detail = (
        f"herdr integration status を起動できない: {err.strip()}"
        if rc is None
        else "herdr integration status の出力に `claude: current` が無い (hook が古い / 未導入)"
    )
    return _check(
        "herdr_integration", "herdr の Claude 連携 hook", "missing", "loud", detail,
        remedy="`herdr integration install claude` を実行する (hook が無いと agent field が "
        "埋まらず、起動確認と駐機判定が壊れる)",
    )


def check_uv():
    """MCP server の起動に使う uv が在るか (README §6 #5)。"""
    rc, out, err = _probe(["uv", "--version"])
    if rc == 0:
        return _check("uv", "uv", "ok", "loud", out.strip())
    return _check(
        "uv", "uv", "missing", "loud",
        f"uv を起動できない: {err.strip()}",
        remedy="uv を install する (MCP server は PEP 723 script を `uv run --script` で起動する)",
        items=["uv"],
    )


def check_tracker_cli(declaration):
    """宣言された置き場の tracker CLI が認証済みか (README §6 #6)。

    issue 置き場と PR 置き場が別 tracker のことがある (cross-tracker) ので、**宣言に現れる
    tracker をすべて**検査する。adapter を持たない tracker (jira) は CLI 経路が無いので
    `unknown` — 観測は orchestrator が Rovo MCP で行う (ADR 0040)。
    """
    trackers = [t for t in (declaration["issue"]["tracker"], declaration["pr"]["tracker"]) if t]
    if not trackers:
        return _check(
            "tracker_cli", "tracker CLI の認証", "unknown", "loud",
            "tracker 種別を判定できない (宣言も git remote も読めない)",
            remedy="宣言 config を置く (project_setup)",
        )
    checked, failed, skipped = [], [], []
    for name in dict.fromkeys(trackers):
        argv = TRACKER_AUTH_COMMANDS.get(name)
        if argv is None:
            skipped.append(name)
            continue
        rc, _out, err = _probe(argv)
        checked.append(f"{name}: {'ok' if rc == 0 else 'failed'}")
        if rc != 0:
            failed.append(f"{' '.join(argv)} ({err.strip() or f'exit {rc}'})")
    if failed:
        return _check(
            "tracker_cli", "tracker CLI の認証", "missing", "loud",
            " / ".join(checked),
            remedy="CLI を install して認証する (`gh auth login` / `glab auth login`)",
            items=failed,
        )
    if not checked:
        return _check(
            "tracker_cli", "tracker CLI の認証", "unknown", "loud",
            f"CLI 経路を持たない tracker のみ: {', '.join(skipped)} "
            "(issue 側の観測・claim は orchestrator が Rovo MCP で行う)",
        )
    return _check("tracker_cli", "tracker CLI の認証", "ok", "loud", " / ".join(checked))


# --- settings (README §6 #7-#9) ------------------------------------------------------


def required_settings(template_path=None):
    """配布物の settings 正本から**dispatch が要求する entry だけ**を導出する。

    件数を散文から引かない — README の見出しに書いた件数は tool を 1 つ足すたびにずれる。
    正本 (`settings/settings.local.json`) を filter するので、entry が増減しても追従する。

    filter 規則 (規則そのものが契約なので、変えるときはここを直す):

    | key | 採る entry |
    |---|---|
    | `permissions.allow` | `herdr` / `dispatch-ops` を含むもの (pane 操作 + MCP tool) |
    | `sandbox.excludedCommands` | 全部 (dispatch が使う CLI と git の network 系で構成されている) |
    | `sandbox.filesystem.allowWrite` | 台帳ディレクトリを指すもの |

    正本を読めない配布形では None を返す (呼び出し側は `unknown` に倒す)。

    path 引数の既定は**呼び出し時に** module 変数から取る (def 時に束ねると、正本の置き場を
    差し替えたテストが既定値経由の呼び出しで実物を読む)。
    """
    try:
        raw = json.loads(Path(template_path or SETTINGS_TEMPLATE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    allow = raw.get("permissions", {}).get("allow", [])
    sandbox = raw.get("sandbox", {})
    return {
        "permissions.allow": [e for e in allow if "herdr" in e or "dispatch-ops" in e],
        "sandbox.excludedCommands": list(sandbox.get("excludedCommands", [])),
        "sandbox.filesystem.allowWrite": [
            e for e in sandbox.get("filesystem", {}).get("allowWrite", []) if LEDGER_WRITE_MARK in e
        ],
    }


def settings_sources(cwd, root):
    """効いている settings の候補 path (user → project → local の順)。

    cwd が linked worktree のときにどちらが project 扱いになるかは harness の実装依存なので、
    **cwd 側と main worktree root 側の両方を読んで開示する**。推測して片方だけ読むと、
    書いてある entry を「無い」と報告する。
    """
    paths = [USER_SETTINGS.expanduser()]
    for base in dict.fromkeys(p for p in (cwd, root) if p is not None):
        paths += [Path(base) / ".claude" / name for name in PROJECT_SETTINGS_NAMES]
    return paths


def _merge_settings(paths):
    """読めた settings を union する。`(集合 dict, 読めた path, 読めなかった path)` を返す。"""
    merged = {"permissions.allow": [], "sandbox.excludedCommands": [], "sandbox.filesystem.allowWrite": []}
    read, unreadable = [], []
    for path in paths:
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            unreadable.append(f"{path}: {exc}")
            continue
        read.append(str(path))
        sandbox = raw.get("sandbox", {})
        merged["permissions.allow"] += raw.get("permissions", {}).get("allow", [])
        merged["sandbox.excludedCommands"] += sandbox.get("excludedCommands", [])
        merged["sandbox.filesystem.allowWrite"] += sandbox.get("filesystem", {}).get("allowWrite", [])
    return merged, read, unreadable


def _prefix_form(entry):
    """entry の prefix 部 (`Bash(herdr:*)` → `Bash(herdr` / `gh:*` → `gh`)。無ければ None。"""
    for suffix in (":*)", ":*"):
        if entry.endswith(suffix):
            return entry[: -len(suffix)]
    return None


def _related_entries(required, effective):
    """欠けている entry を**覆っているかもしれない**広い entry (情報としてだけ載せる)。

    `Bash(herdr:*)` が `Bash(herdr status:*)` を覆うかは未検証なので、green の根拠にしない。
    ここに出るのは「required が effective の prefix 形に token 境界で前方一致する」ものだけ
    (境界を見ないと `gh:*` が `ghost:*` を覆うことになる)。
    """
    related = []
    for entry in effective:
        prefix = _prefix_form(entry)
        if not prefix or not required.startswith(prefix):
            continue
        rest = required[len(prefix) :]
        if rest and rest[0] in " :":
            related.append(entry)
    return related


def check_settings(cwd, root, template_path=None):
    """dispatch が要求する settings entry が効いているか (README §6 #7-#9)。

    照合は**完全一致**。不足は逐語で返す (そのまま `.claude/settings.local.json` へ写せる形)。
    書き込みは doctor の責務ではない — settings の writer は `/apply-swat-settings` 1 つに保つ。
    """
    template_path = template_path or SETTINGS_TEMPLATE
    required = required_settings(template_path)
    if required is None:
        return _check(
            "settings", "settings の entry", "unknown", "loud",
            f"settings 正本を読めない ({template_path})。この配布形では要求 entry を導出できない",
            remedy="`/apply-swat-settings` で適用先 settings を確認する",
        )
    merged, read, unreadable = _merge_settings(settings_sources(cwd, root))
    missing, related = [], []
    for key, entries in required.items():
        effective = merged[key]
        for entry in entries:
            if entry in effective:
                continue
            missing.append(f"{key}: {entry}")
            related += [f"{key}: {e} (覆っている可能性。green の根拠にしない)"
                        for e in _related_entries(entry, effective)]
    detail = f"読んだ settings: {', '.join(read) or '無し'}"
    if unreadable:
        detail += f" / 読めなかった: {', '.join(unreadable)}"
    detail += " (managed settings は読んでいない)"
    if not read:
        return _check(
            "settings", "settings の entry", "unknown", "loud", detail,
            remedy="`/apply-swat-settings` を適用先 project で起動する",
        )
    if missing:
        return _check(
            "settings", "settings の entry", "missing", "loud", detail,
            remedy="`/apply-swat-settings` を適用先 project で起動して不足 entry を適用する "
            "(doctor は settings を書かない)",
            items=missing, related=related,
        )
    return _check("settings", "settings の entry", "ok", "loud", detail)


# --- 宣言 config / plugin 名 (README §6 #10-#12) --------------------------------------


def check_project_config(root):
    """宣言 config が在り、置き場の repo 識別子まで宣言しているか (README §6 #10)。

    `Ports.get_declaration` の cache を経由せず config を直読みする — `project_setup` で
    置いた直後に doctor を走らせるのが自然な流れで、cache 越しでは「まだ無い」と誤報する。
    """
    path = project_mod.config_path(root)
    if path is None:
        return _check(
            "project_config", "宣言 config (置き場)", "unknown", "silent",
            f"台帳ディレクトリを解決できない ({root} が git repo でない)",
            remedy="git repo の中から実行する",
        )
    try:
        config = project_mod.load_config(path)
    except project_mod.ProjectError as exc:
        return _check(
            "project_config", "宣言 config (置き場)", "missing", "loud", str(exc),
            remedy="config の書式を直す (書ける table は [issue] / [pr]、key は tracker / repo)",
            items=[str(path)],
        )
    if config is None:
        return _check(
            "project_config", "宣言 config (置き場)", "missing", "silent",
            f"{path} が無い。tracker 種別は git remote の host からの**推測**で決まり "
            "(gh / glab のみ)、repo 識別子は CLI の cwd 推論へ倒れる",
            remedy="`project_setup` で置く (置き場がメイン repo 自身なら結果は同じだが、"
            "置き場が関連 repo の project では別の置き場を**黙って**観測し続ける。"
            "Jira 置き場は config でしか宣言できないので、無いと別 tracker と誤判定する)",
            items=[str(path)],
        )
    if not config["issue"].get("repo"):
        return _check(
            "project_config", "宣言 config (置き場)", "missing", "silent",
            f"{path} の [issue] に repo が無い (tracker 種別だけの宣言)",
            remedy="[issue] へ repo 識別子を足す (`project_setup` の overwrite でも置き直せる)",
            items=[f"{path}: [issue] repo"],
        )
    detail = f"{path}: issue = {config['issue']['tracker']} {config['issue']['repo']}"
    if "pr" in config:
        detail += f" / pr = {config['pr']['tracker'] or '(issue を継ぐ)'} {config['pr']['repo']}"
    return _check(
        "project_config", "宣言 config (置き場)", "ok", "silent", detail,
        remedy="綴りが正しい置き場を指しているかは server では検出できない — "
        "`observe_issues` の `issues[].url` を目視で 1 度確かめる",
    )


def check_plugin_name(manifest_path=None):
    """配布物の plugin 名が契約値 `swat-skills` か (README §6 #12)。

    **これは publisher 側の宣言の検査で、harness が実際に登録した名前ではない** — server から
    登録名は見えない。plugin 名は observer の自己再読込文面と MCP tool の完全名に直書きされて
    いるので、ずれると observer が手順を失ったまま観測を続ける (silent)。
    """
    manifest_path = manifest_path or PLUGIN_MANIFEST
    try:
        name = json.loads(Path(manifest_path).read_text(encoding="utf-8")).get("name")
    except (OSError, json.JSONDecodeError) as exc:
        return _check(
            "plugin_name", "plugin 名の契約", "unknown", "silent",
            f"{manifest_path} を読めない: {exc} (marketplace 配布では marketplace entry の "
            "`name` が正本)",
        )
    if name == PLUGIN_NAME:
        return _check(
            "plugin_name", "plugin 名の契約", "ok", "silent",
            f"{manifest_path} の name = {name} (**publisher の宣言**であって、harness が"
            "登録した名前ではない。実際の登録名は `/plugin list` で見る)",
        )
    return _check(
        "plugin_name", "plugin 名の契約", "missing", "silent",
        f"{manifest_path} の name = {name!r} (契約値は {PLUGIN_NAME!r})",
        remedy=f"plugin 名を {PLUGIN_NAME} に戻す (install 側では変えられない — publisher 側の宣言)",
        items=[str(name)],
    )


# --- 集約 ---------------------------------------------------------------------------


def _resolve_or_unknown(root):
    """tracker 種別の判定に使う宣言。**解決できなくても例外を外へ出さない**。

    宣言が壊れている環境は doctor が最も要る場面なのに、`resolve_declaration` の
    `ProjectError` をそのまま通すと**報告ごと落ちて 1 項目も返らない**。tracker が判らない
    ことは `tracker_cli` の `unknown` として、書式違反そのものは `project_config` が名指しで
    `missing` として報告する — 各検査の非 fatal を、集約の層で無効化しない。
    """
    unknown = {"issue": {"tracker": None}, "pr": {"tracker": None}}
    if root is None:
        return unknown
    try:
        return project_mod.resolve_declaration(root)
    except project_mod.ProjectError:
        return unknown


def run_checks(cwd=None, env=None, declaration=None):
    """全検査を回して報告を返す (**1 つ落ちても後続を止めない**)。

    Args:
        cwd: 検査の基準ディレクトリ (既定 = プロセスの cwd)
        env: 環境変数の写像 (既定 = `os.environ`)
        declaration: 解決済み宣言。未指定なら**その場で解決する** (cache を経由しない)

    返り値の `ok` は「missing が 1 件も無い」。`unknown` は ok を落とさない — 検査できて
    いない項目を落として報告すると、直しようのない red が常駐して doctor が読まれなくなる。
    """
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else Path(cwd)
    try:
        root = repo_key_mod.main_worktree_root(cwd)
    except repo_key_mod.RepoKeyError:
        root = None
    if declaration is None:
        declaration = _resolve_or_unknown(root)
    checks = [
        check_messaging(env),
        check_herdr_session(env),
        check_herdr_daemon(),
        check_herdr_integration(),
        check_uv(),
        check_tracker_cli(declaration),
        check_settings(cwd, root),
        check_project_config(root),
        check_plugin_name(),
    ]
    summary = {
        status: sum(1 for c in checks if c["status"] == status)
        for status in ("ok", "missing", "unknown")
    }
    return {
        "ok": summary["missing"] == 0,
        "summary": summary,
        "root": str(root) if root is not None else None,
        "cwd": str(cwd),
        "checks": checks,
        "missing": [c["id"] for c in checks if c["status"] == "missing"],
    }


__all__ = [
    "DoctorError",
    "check_herdr_daemon",
    "check_herdr_integration",
    "check_herdr_session",
    "check_messaging",
    "check_plugin_name",
    "check_project_config",
    "check_settings",
    "check_tracker_cli",
    "check_uv",
    "required_settings",
    "run_checks",
    "settings_sources",
]

# refs は tracker 語彙の正本。CLI 表を語彙からずらさない (jira を足したのに検査表が
# 知らない、の逆向きは黙って skip になる)
_UNKNOWN_TRACKERS = set(TRACKER_AUTH_COMMANDS) - set(refs.TRACKERS)
if _UNKNOWN_TRACKERS:  # pragma: no cover - import 時の整合検査
    raise RuntimeError(f"doctor.TRACKER_AUTH_COMMANDS に未知の tracker: {_UNKNOWN_TRACKERS}")
