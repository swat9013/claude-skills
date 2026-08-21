"""project の宣言 (issue 置き場 / PR 置き場) を解決する — server 内の**単一箇所**。

宣言の正本は構造化 config (`<台帳ディレクトリ>/dispatch-project.toml`)。tracker 種別だけでなく
**置き場の repo 識別子まで**を機械可読な形で持ち、issue 置き場と PR 置き場を別々に宣言できる
(cross-tracker: issue = Jira / PR = GitLab)。

**config を台帳の隣に置くのは、宣言と台帳の identity 軸を一致させるため** (ADR 0036 の追補 /
#589)。台帳は `pane_spawn` が env (`ISSUE_DISPATCH_LEDGER_DIR`) で注入するので project に 1 つへ
着地するのに、宣言だけを cwd の clone から解くと、関連 repo で走る worker が**自分の clone の
宣言**を読む (= 別の置き場を観測する)。両者が `ledger.resolve_ledger_dir` 1 つを共有すれば、
この経路は構造的に消える。

解決順は 2 段で、**上が下を完全に上書きする** (merge しない):

| 順 | 源 | 読めるもの |
|---|---|---|
| 1 | `<台帳ディレクトリ>/dispatch-project.toml` | tracker 種別 + repo 識別子 (issue / PR 別) |
| 2 | `git remote -v` の host | tracker 種別のみ (**宣言ではなく推測**) |

**宣言の源は config 1 つだけ** (#614)。かつては `docs/agents/issue-tracker.md` の H1 から tracker
種別だけを読む層を間に挟んでいたが、人間向けの見出しが宣言として振る舞う (見出しを書き換えると
宣言が黙って変わる) 状態を残さないため削除した。2 段目は宣言の代替ではなく最後の推測で、
`source: "remote"` として呼び出し側が推測だと読み取れる。

**config は version 管理の外にある** (台帳と同じくマシンローカル)。置き忘れた環境は 2 段目の推測へ
倒れるので、issue 置き場がメイン repo 自身なら従来どおり動くが、**置き場が関連 repo の project では
cwd 推論へ倒れて別の置き場を黙って観測する**。この検知は setup / doctor (#592) の担当で、本 module は
「宣言が無い」を error にしない (config を置いていない環境を壊さない側を採る)。

**Jira 置き場は config でしか表現できない。** 2 段目が返せるのは adapter を持つ tracker
(`tracker._ADAPTERS` = gh / glab) だけで、`jira` へ解決する経路は config にしか無い。legacy 層は
散文の H1 から Jira を拾って `get_adapter` の「未実装」で落とす退路も兼ねていたが、その退路ごと
config へ寄せた — 残る露出は「Jira 置き場 + config 不在」で remote host へ倒れ、gh / glab と誤判定
して静かに成立する経路 1 つで、これは doctor の `project_config` (`missing` / silent) が名指しする
担当。**散文の見出しを宣言として読み続けるより、宣言の源を 1 つにして検知を doctor へ集約する側を
採った** (#614)。

config の書式違反は握り潰さず `ProjectError` で落とす。remote host の fallback へ静かに倒れると、
**綴りを間違えた宣言が「宣言が無い環境」と同じ挙動になる** — 誤った置き場を観測し続ける状態が
無言で成立する経路を作らない。

本 module が持つのは**書式の知識** (解決順 + 生成) だけで、tracker 固有の綴り — remote host の
host 名と、識別子を CLI へどう渡すか — は adapter (`tracker`) の責務、前提が揃っているかの検査は
`doctor` の責務。**adapter を 1 つ足すとき本 module は触らない** (宣言できる語彙は `refs.TRACKERS`、
remote host の綴りは adapter 側の宣言) が、adapter との分担の判定基準になる。生成 (`write_config`) を
doctor 側へ出さないのは、table 名 / key 名の変更が 2 module に分かれないようにするため。
"""

import tomllib
from pathlib import Path

import ledger as ledger_mod
import proc
import refs
import repo_key as repo_key_mod
import tracker as tracker_mod

SUBPROCESS_TIMEOUT_SEC = 60

# 宣言の正本 (構造化 config) の file 名。**探索しない** — project に 1 つ、台帳ディレクトリ直下
PROJECT_CONFIG_FILENAME = "dispatch-project.toml"

_CONFIG_TABLES = ("issue", "pr")
_CONFIG_KEYS = ("tracker", "repo")


class ProjectError(RuntimeError):
    """宣言を解決できない (config の書式違反 / tracker 種別の判定不能)。"""


run_command = proc.command_runner(error=ProjectError, timeout_sec=SUBPROCESS_TIMEOUT_SEC)


# --- config の読み取り -----------------------------------------------------------


def config_path(root):
    """宣言 config の path (台帳ディレクトリ直下)。台帳ディレクトリを解けなければ None。

    repo-key を導出できない場所 (git repo でない) では config 層ごと skip して remote host の
    推測へ倒す — 台帳も開けない場所なので、ここで別の error を上げても情報が増えない。
    """
    try:
        resolved = ledger_mod.resolve_ledger_dir(root)
    except repo_key_mod.RepoKeyError:
        return None
    return resolved["path"] / PROJECT_CONFIG_FILENAME


def load_config(path):
    """config を読んで検証済み dict にする。無ければ None。

    public なのは doctor (#592) が**解決を経由せず**この層だけを読むため。`resolve_declaration`
    は tool 層で cache される (`Ports.get_declaration`) ので、`project_setup` で置いた直後の
    config を cache 越しに見ると「まだ無い」と報告してしまう。
    """
    if path is None or not path.is_file():
        return None
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ProjectError(f"{path} を読めない: {exc}") from exc
    return _validate_config(path, raw)


def _validate_config(path, raw):
    """未知の table / key / tracker 名を名指しで落とす。

    黙って無視すると、綴り違いの宣言が「宣言していない」と同じ挙動になる (置き場を
    取り違えたまま観測・claim・label が進む)。
    """
    unknown_tables = sorted(set(raw) - set(_CONFIG_TABLES))
    if unknown_tables:
        raise ProjectError(
            f"{path} に未知の table: {', '.join(unknown_tables)} "
            f"(書けるのは {', '.join(_CONFIG_TABLES)})"
        )
    if "issue" not in raw:
        raise ProjectError(f"{path} に [issue] table が無い (issue 置き場は必須)")
    return {
        name: _validate_table(path, name, raw[name]) for name in _CONFIG_TABLES if name in raw
    }


def _validate_table(path, name, table):
    if not isinstance(table, dict):
        raise ProjectError(f"{path} の [{name}] が table でない")
    unknown_keys = sorted(set(table) - set(_CONFIG_KEYS))
    if unknown_keys:
        raise ProjectError(
            f"{path} の [{name}] に未知の key: {', '.join(unknown_keys)} "
            f"(書けるのは {', '.join(_CONFIG_KEYS)})"
        )
    tracker = table.get("tracker")
    if tracker is not None and tracker not in refs.TRACKERS:
        raise ProjectError(
            f"{path} の [{name}] tracker が未知: {tracker!r} (候補: {', '.join(refs.TRACKERS)})"
        )
    repo = table.get("repo")
    if repo is not None and not isinstance(repo, str):
        raise ProjectError(f"{path} の [{name}] repo が文字列でない: {repo!r}")
    if name == "issue" and tracker is None:
        raise ProjectError(f"{path} の [issue] に tracker が無い")
    if name == "pr" and not repo:
        # `[pr]` を書く = 置き場が issue 側と違う、の意思表示。識別子を落とすと CLI の cwd 推論へ
        # 倒れ、**別 repo の PR を黙って観測する**。継ぐなら table ごと省くのが正しい形
        raise ProjectError(
            f"{path} の [pr] に repo が無い (PR 置き場が issue 置き場と同じなら [pr] table ごと省く)"
        )
    return {"tracker": tracker, "repo": repo or None}


# --- 生成 (setup が置く宣言) --------------------------------------------------------


def render_config(issue, pr=None):
    """宣言 config の本文 (TOML) を組み立てる。

    **書式を知る module を 1 つに保つため、生成も解析と同じここに置く。** 生成側だけを
    doctor / skill 側へ出すと、key 名や table 名の変更が 2 箇所に分かれる。

    Args:
        issue: `{"tracker": "gh", "repo": "owner/name"}` (tracker は必須)
        pr: PR 置き場が issue 置き場と違うときだけ渡す。`repo` 必須 / `tracker` は省略可

    値は TOML の basic string として escape する。識別子に `"` や `\\` が現れる余地は
    実務上ほぼ無いが、escape しないと**壊れた config が「宣言が無い環境」と同じ挙動**
    (置き場を黙って取り違える) に化けるので、生成側で塞ぐ。
    """
    lines = ["[issue]", f"tracker = {_toml_string(issue['tracker'])}"]
    if issue.get("repo"):
        lines.append(f"repo = {_toml_string(issue['repo'])}")
    if pr:
        lines += ["", "[pr]"]
        for key in ("tracker", "repo"):
            # 欠けた値を空文字や "None" で埋めない — 埋めると検証を素通りし、実在しない
            # 置き場を宣言した config が出来上がる (検出は CLI が失敗するときまで遅れる)
            if pr.get(key):
                lines.append(f"{key} = {_toml_string(pr[key])}")
    return "\n".join(lines) + "\n"


def _toml_string(value):
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_config(directory, issue, pr=None, overwrite=False):
    """宣言 config を**渡された台帳ディレクトリ**の直下へ書く (#592 の setup)。

    書く前に**生成した本文を解析し直して検証する** — 書式違反の config は握り潰されず
    `ProjectError` になる (= server が起動しなくなる) ので、壊れたものをディスクへ残さない。

    既存 config は `overwrite=True` を明示しない限り上書きしない。宣言は version 管理の外に
    あり、上書きすると**元の置き場を復元する手段が無い**。

    置き先を再解決せず引数で受けるのは、**書き先と台帳を必ず同じディレクトリに落とす**ため。
    呼び出し側は `Ledger.ensure_directory` の返り値をそのまま渡す — 実体化と書き込みが別々に
    解決すると、台帳を作った場所と config を置いた場所がずれうる。
    """
    path = Path(directory) / PROJECT_CONFIG_FILENAME
    if path.exists() and not overwrite:
        raise ProjectError(
            f"{path} は既に在る (上書きするなら overwrite を明示する。宣言は version 管理の"
            "外にあり、上書きすると元の置き場は復元できない)"
        )
    body = render_config(issue, pr)
    declaration = _validate_config(path, tomllib.loads(body))
    path.write_text(body, encoding="utf-8")
    return {"path": str(path), "body": body, "config": declaration}


# --- fallback (宣言が無い環境の推測) -----------------------------------------------


def tracker_from_remote(root):
    """git remote の host で tracker を推測する (宣言が無い / 宣言が PR を持たないときの経路)。

    host の綴りは adapter が宣言する (`tracker.tracker_for_remote`)。本 module が持つのは
    「いつこの経路へ倒れるか」だけで、tracker を足しても本 module は動かない。

    **返せるのは adapter を持つ tracker (gh / glab) だけ**なので、`jira` はこの経路から出ない
    (= Jira 置き場は config で宣言しない限り成立しない)。宣言と推測の別は `source` で返る。
    """
    rc, out, _err = run_command(["git", "-C", str(root), "remote", "-v"])
    if rc != 0:
        return None
    return tracker_mod.tracker_for_remote(out)


# --- 解決 (server 内で宣言を読む唯一の入口) ----------------------------------------


def resolve_declaration(root):
    """project の宣言を解決して置き場 2 つ (issue / PR) を返す。

    返り値::

        {"config_path": "<台帳ディレクトリ>/dispatch-project.toml" | None,
         "issue": {"tracker": "gh" | None, "repo": str | None, "source": "config"|"remote"},
         "pr":    {"tracker": "gh" | None, "repo": str | None, "source": "config"|"issue"|"remote"}}

    `config_path` は**効いた config の絶対パス** (読めなければ None)。置き場が version 管理の
    外にあるぶん、どの file が効いたかを呼び出し側から見えるようにしておく。

    `source` が `config` 以外のときは**宣言が無い** (`remote` = git remote host からの推測、
    `issue` = PR 側が issue 置き場を継いだ)。`repo` が埋まるのは config 経由のときだけで、
    推測の経路は tracker 種別しか決めないので `repo` は None (= 呼び出し側が指定しなければ
    CLI の cwd 推論のまま)。
    """
    path = config_path(root)
    config = load_config(path)
    issue = _resolve_issue(root, config)
    return {
        "config_path": str(path) if config is not None else None,
        "issue": issue,
        "pr": _resolve_pr(root, config, issue),
    }


def _resolve_issue(root, config):
    if config is not None:
        return {**config["issue"], "source": "config"}
    return {"tracker": tracker_from_remote(root), "repo": None, "source": "remote"}


def _resolve_pr(root, config, issue):
    """**PR 置き場**を issue 置き場とは別軸で解く (#576)。

    config の `[pr]` が第一正。無ければ issue 置き場が PR を持つ tracker (gh / glab) の
    ときだけそれを継ぐ — issue 置き場と PR 置き場が同じ構成 (GitHub 単独 / GitLab 単独) の
    挙動を動かさないため。issue 置き場が PR を持たない tracker (Jira) のときだけ git remote の
    host へ落ちる。

    分けないと、issue 置き場が Jira の project では **GitLab 側で普通に観測できる MR まで
    観測できない** — プロセスが持つ adapter が 1 つで、それが未実装 adapter になるため。
    """
    if config is not None and "pr" in config:
        declared = config["pr"]
        tracker = declared["tracker"] or _inherited_pr_tracker(root, issue)
        return {"tracker": tracker, "repo": declared["repo"], "source": "config"}
    if issue["tracker"] in refs.PR_PATTERNS:
        return {"tracker": issue["tracker"], "repo": issue["repo"], "source": "issue"}
    return {"tracker": tracker_from_remote(root), "repo": None, "source": "remote"}


def _inherited_pr_tracker(root, issue):
    """`[pr]` が repo だけを宣言したときの tracker (issue 置き場と同じ tracker の別 repo)。"""
    if issue["tracker"] in refs.PR_PATTERNS:
        return issue["tracker"]
    return tracker_from_remote(root)


# --- 後方互換の薄い入口 (tracker 種別だけが要る呼び出し) ---------------------------


def detect_tracker(root):
    """issue 置き場の tracker 種別。"""
    return resolve_declaration(root)["issue"]["tracker"]


def detect_pr_tracker(root):
    """PR 置き場の tracker 種別。"""
    return resolve_declaration(root)["pr"]["tracker"]
