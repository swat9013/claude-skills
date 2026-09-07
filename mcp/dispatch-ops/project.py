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
| 1 | `<台帳ディレクトリ>/dispatch-project.toml` | tracker 種別 + repo 識別子 (issue / PR 別) + merge 後に issue を閉じるかの宣言 + label の綴り (claim / AFK-ready) + worker の standing orders |
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

# 書ける key は table ごとに違う。`close_on_merge` / `done_status` は issue 側の宣言なので、
# `[pr]` に書いたら「未知の key」で落ちる (issue 側の宣言が PR 置き場に書けると、閉じる対象を
# 取り違えた宣言が黙って成立する)
_CONFIG_KEYS = {
    "issue": ("tracker", "repo", "close_on_merge", "done_status", "claim_label", "ready_label"),
    "pr": ("tracker", "repo"),
    "worker": ("standing",),
}
_CONFIG_TABLES = tuple(_CONFIG_KEYS)


# AI が claim 中の印として issue へ付ける label の既定綴り。宣言が無くても**必ず値が出る**
# (None を返すと claim が label を付けず、候補除外も効かないまま二重 dispatch が黙って成立する)。
# GitLab だけ綴りが違うのは scoped label (`key::value`) の書式に合わせるため — tracker ごとの
# 宣言規則を本 module が持つのは `done_status` の jira 限定と同じ形。
_CLAIM_LABEL_DEFAULTS = {"glab": "dispatch::claimed"}
DEFAULT_CLAIM_LABEL = "dispatch:claimed"

# 候補プール (AFK-ready) を表す triage label の**生成時だけ**の既定。`claim_label` と規則が
# 逆で、解決 (`resolve_declaration`) はこの既定を持たない — 未宣言は `None` のまま返し、読み手
# (orchestrator / observer / dashboard) が推測せず止まる。綴りは環境の triage 語彙なので、
# server が既定を配ると**外した綴りが `count: 0` を返して「候補が無い」と読まれる**沈黙の失敗が
# 復活する (#802)。生成側だけが既定を持つのは、宣言を file の上に見える形で置くため
DEFAULT_READY_LABEL = "ready-for-agent"

# AFK-ready を label で表す tracker。**jira はここに入らない** — Jira 置き場の AFK-ready は
# workflow の status で表され、候補の観測 (JQL) も status で絞る。既定を書き込むと、誰も
# 読まない綴りが宣言として残り、doctor が「揃っている」と報告してしまう
READY_LABEL_TRACKERS = ("gh", "glab")


def declares_ready_label(tracker):
    """その tracker が AFK-ready を label で表すか (= `ready_label` の宣言を要るか)。"""
    return tracker in READY_LABEL_TRACKERS


def default_claim_label(tracker):
    """宣言が無いときの claim label。**tracker 不明でも既定を返す** (None にしない)。"""
    return _CLAIM_LABEL_DEFAULTS.get(tracker, DEFAULT_CLAIM_LABEL)


def config_format_summary():
    """書ける table と key を人間向けの 1 行にする (doctor の remedy が使う)。

    doctor が同じ列挙を文字列で持たないようにする — 写しを持つと、key を足したときに
    **doctor の案内だけが古い許容集合を教える**状態が黙って成立する (検証本体の error 文は
    `_CONFIG_KEYS` から組み立てるので食い違いに気づけない)。
    """
    tables = " / ".join(f"[{name}]" for name in _CONFIG_TABLES)
    keys = "、".join(f"[{name}] が {' / '.join(_CONFIG_KEYS[name])}" for name in _CONFIG_TABLES)
    return f"書ける table は {tables}、key は {keys}"


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
    は scope 層で cache される (`Scope.declaration`) ので、`project_setup` で置いた直後の
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
    allowed = _CONFIG_KEYS[name]
    unknown_keys = sorted(set(table) - set(allowed))
    if unknown_keys:
        raise ProjectError(
            f"{path} の [{name}] に未知の key: {', '.join(unknown_keys)} "
            f"(書けるのは {', '.join(allowed)})"
        )
    if name == "worker":
        # 置き場の table ではないので tracker / repo の検査を通さない
        return {"standing": _validate_standing(path, table)}
    tracker = table.get("tracker")
    if tracker is not None and tracker not in refs.TRACKERS:
        raise ProjectError(
            f"{path} の [{name}] tracker が未知: {tracker!r} (候補: {', '.join(refs.TRACKERS)})"
        )
    repo = table.get("repo")
    if repo is not None and not isinstance(repo, str):
        raise ProjectError(f"{path} の [{name}] repo が文字列でない: {repo!r}")
    if name == "pr":
        if not repo:
            # `[pr]` を書く = 置き場が issue 側と違う、の意思表示。識別子を落とすと CLI の cwd
            # 推論へ倒れ、**別 repo の PR を黙って観測する**。継ぐなら table ごと省くのが正しい形
            raise ProjectError(
                f"{path} の [pr] に repo が無い "
                "(PR 置き場が issue 置き場と同じなら [pr] table ごと省く)"
            )
        return {"tracker": tracker, "repo": repo}
    if tracker is None:
        raise ProjectError(f"{path} の [issue] に tracker が無い")
    return {
        "tracker": tracker,
        "repo": repo or None,
        **_validate_closure(path, tracker, table),
        "claim_label": _validate_claim_label(path, tracker, table),
        "ready_label": _validate_ready_label(path, table),
    }


def _validate_claim_label(path, tracker, table):
    """AI の claim 信号として付け外しする label の綴りを検証する。未宣言なら tracker 既定。

    空文字を既定へ倒さず落とすのは、`claim_label = ""` が「宣言していない」と同じ挙動に
    なるのを防ぐため (未知 key を名指しで落とすのと同じ規則)。
    """
    declared = table.get("claim_label")
    if declared is None:
        return default_claim_label(tracker)
    if not isinstance(declared, str) or not declared.strip():
        raise ProjectError(
            f"{path} の [issue] claim_label が空でない文字列でない: {declared!r}"
        )
    return declared.strip()


def _validate_ready_label(path, table):
    """候補プール (AFK-ready) を表す triage label の綴りを検証する。**未宣言は None**。

    `claim_label` と違って既定へ倒さないのは、綴りが**機構の外の語彙**だから。claim label は
    機構自身が付け外しするので既定を配れるが、AFK-ready はその環境の triage 体系が決める
    (`needs-triage` / `ready-for-agent` … の列は project ごとに違う)。既定を配ると、外した綴りで
    `observe_issues` が `count: 0` を返し、読み手が「候補が空」と読む沈黙の失敗になる — 宣言が
    無いことを None で告げれば、読み手は推測せず止まれる (#802)。
    """
    declared = table.get("ready_label")
    if declared is None:
        return None
    if not isinstance(declared, str) or not declared.strip():
        raise ProjectError(
            f"{path} の [issue] ready_label が空でない文字列でない: {declared!r}"
        )
    return declared.strip()


def _validate_standing(path, table):
    """worker へ毎 spawn 貼る project 固有の制約 (自由文の配列) を検証する。

    **書式だけを見て意味を解釈しない** (ADR 0012 の硬直リスク)。貼るのは orchestrator で、
    server は「配列で、各要素が空でない文字列」までしか知らない — 中身を語彙として縛ると、
    制約を 1 行足すたびに server の改修が要る形へ戻る。改行を含む要素も**書式としては正当**で、
    TOML の escape は生成側 (`_toml_string`) が持つ。
    """
    declared = table.get("standing")
    if declared is None:
        return []
    if not isinstance(declared, list):
        raise ProjectError(f"{path} の [worker] standing が配列でない: {declared!r}")
    entries = []
    for item in declared:
        if not isinstance(item, str) or not item.strip():
            # 空要素を落として通すと、**貼られない制約が宣言されている**状態が黙って成立する
            raise ProjectError(
                f"{path} の [worker] standing に空でない文字列でない要素: {item!r}"
            )
        entries.append(item.strip())
    return entries


def _validate_closure(path, tracker, table):
    """merge 後に issue を閉じる宣言 (`close_on_merge` / `done_status`) を検証する。

    `done_status` を `tracker = "jira"` 限定にするのは、gh / glab の close が遷移先の status 名を
    取らないため。書けてしまうと**書いたのに効かない宣言**が黙って成立する。逆に Jira は
    「閉じる」が遷移先の指定を要るので、`close_on_merge` だけの宣言は閉じ方が決まらない。

    どちらも server は使わない (閉じるのは orchestrator)。ここで持つのは書式の知識だけで、
    宣言が指す status が実在するかは遷移させる側が確かめる。
    """
    close_on_merge = table.get("close_on_merge", False)
    if not isinstance(close_on_merge, bool):
        raise ProjectError(
            f"{path} の [issue] close_on_merge が真偽値でない: {close_on_merge!r}"
        )
    done_status = table.get("done_status")
    if done_status is not None and not isinstance(done_status, str):
        raise ProjectError(f"{path} の [issue] done_status が文字列でない: {done_status!r}")
    done_status = done_status or None
    if done_status is not None and tracker != "jira":
        raise ProjectError(
            f'{path} の [issue] done_status は tracker = "jira" のときだけ書ける '
            f"(宣言された tracker: {tracker})。gh / glab の close は遷移先 status を取らない"
        )
    if tracker == "jira" and close_on_merge and done_status is None:
        raise ProjectError(
            f'{path} の [issue] tracker = "jira" で close_on_merge = true なら done_status が要る '
            "(Jira は「閉じる」が遷移先 status の指定を要る)"
        )
    return {"close_on_merge": close_on_merge, "done_status": done_status}


# --- 生成 (setup が置く宣言) --------------------------------------------------------


def render_config(issue, pr=None, worker=None):
    """宣言 config の本文 (TOML) を組み立てる。

    **書式を知る module を 1 つに保つため、生成も解析と同じここに置く。** 生成側だけを
    doctor / skill 側へ出すと、key 名や table 名の変更が 2 箇所に分かれる。

    Args:
        issue: `{"tracker": "gh", "repo": "owner/name"}` (tracker は必須)。merge 後に issue を
            閉じるなら `close_on_merge` / `done_status` (jira 限定) を足す。AI の claim 信号に
            使う label の綴りを既定から変えるなら `claim_label` を足す。候補プールを表す
            triage label の綴りは `ready_label` (**解決側に既定が無いので、生成側が必ず書く**)
        pr: PR 置き場が issue 置き場と違うときだけ渡す。`repo` 必須 / `tracker` は省略可
        worker: `{"standing": ["...", ...]}`。project 固有の worker 制約を宣言するときだけ渡す

    値は TOML の basic string として escape する。識別子に `"` や `\\` が現れる余地は
    実務上ほぼ無いが、escape しないと**壊れた config が「宣言が無い環境」と同じ挙動**
    (置き場を黙って取り違える) に化けるので、生成側で塞ぐ。
    """
    lines = ["[issue]", f"tracker = {_toml_string(issue['tracker'])}"]
    if issue.get("repo"):
        lines.append(f"repo = {_toml_string(issue['repo'])}")
    if issue.get("close_on_merge"):
        # 既定 (false) は行ごと省く — 既存 config と同形に保ち、宣言していない項目を
        # 「明示的に切った」と読ませない。TOML の真偽値は小文字 (Python の repr は不正)
        lines.append("close_on_merge = true")
    if issue.get("done_status"):
        lines.append(f"done_status = {_toml_string(issue['done_status'])}")
    if issue.get("claim_label"):
        # 既定と同じ綴りでも渡されたら書く (`done_status` と同じ「渡されたときだけ」規則)。
        # 既定を無条件に書き出すと、既定が変わったとき古い綴りが宣言として固定される
        lines.append(f"claim_label = {_toml_string(issue['claim_label'])}")
    if issue.get("ready_label"):
        # **こちらは既定でも書く** (`claim_label` と逆)。解決側に既定が無いので、書かないと
        # 候補プールを観測する 3 者 (orchestrator / observer / dashboard) が全員止まる
        lines.append(f"ready_label = {_toml_string(issue['ready_label'])}")
    if pr:
        lines += ["", "[pr]"]
        for key in ("tracker", "repo"):
            # 欠けた値を空文字や "None" で埋めない — 埋めると検証を素通りし、実在しない
            # 置き場を宣言した config が出来上がる (検出は CLI が失敗するときまで遅れる)
            if pr.get(key):
                lines.append(f"{key} = {_toml_string(pr[key])}")
    if worker and worker.get("standing"):
        # 1 要素 1 行の配列にする — 自由文なので、1 行へ詰めると人が読めない形で伸びる
        lines += ["", "[worker]", "standing = ["]
        lines += [f"  {_toml_string(entry)}," for entry in worker["standing"]]
        lines.append("]")
    return "\n".join(lines) + "\n"


def _toml_string(value):
    """TOML の basic string 1 個。**制御文字まで escape する。**

    生の制御文字を通すと basic string として不正な本文ができ、`write_config` の書き戻し検証が
    `tomllib` の decode error で落ちる — それは `ProjectError` ではないので tool 層の翻訳に
    掛からず、**呼び出し側には理由の無い失敗**として届く。自由文を取る `[worker] standing` は
    改行が普通に現れるので、識別子だけを見ていた頃の escape では足りない。
    """
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    for char, replacement in (("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t")):
        escaped = escaped.replace(char, replacement)
    # 上で名指しした 3 つ以外の制御文字は `\uXXXX` へ倒す (TOML は生のまま置けない)
    return '"{}"'.format(
        "".join(char if char.isprintable() or char in '\\"' else f"\\u{ord(char):04X}" for char in escaped)
    )


def _inherited_standing(path):
    """置き直しで渡されなかった `[worker] standing` を既存 config から継ぐ。無ければ None。

    **落ちたことを誰も検知できない唯一の宣言だから継ぐ。** 置き場の宣言や `ready_label` は
    落ちれば `project_doctor` が名指しし (空が不正なので)、`claim_label` は落ちても機構内の
    既定へ戻るだけ。`standing` は**空が正当な状態**なので検査を置けず、落ちると「制約なしで
    走った spawn」が正常な文面のまま残る — この機構が防ごうとしている失敗そのもの。

    既存 config が壊れていても継ぎ足しの失敗で置き直しごと止めない (置き直しの動機が「壊れた
    config を直す」ことでもあるため)。継げなければ渡されなかった扱いに倒す。
    """
    try:
        existing = load_config(path)
    except ProjectError:
        return None
    return (existing or {}).get("worker")


def write_config(directory, issue, pr=None, overwrite=False, worker=None):
    """宣言 config を**渡された台帳ディレクトリ**の直下へ書く (#592 の setup)。

    書く前に**生成した本文を解析し直して検証する** — 書式違反の config は握り潰されず
    `ProjectError` になる (= server が起動しなくなる) ので、壊れたものをディスクへ残さない。

    既存 config は `overwrite=True` を明示しない限り上書きしない。宣言は version 管理の外に
    あり、上書きすると**元の置き場を復元する手段が無い**。上書きは渡された引数だけで本文を
    組み直す (merge しない) が、**`[worker] standing` だけは渡されなければ既存を継ぐ** — 下記。

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
    if worker is None:
        worker = _inherited_standing(path)
    body = render_config(issue, pr, worker)
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
    """project の宣言を解決して置き場 2 つ (issue / PR) と worker 契約を返す。

    返り値::

        {"config_path": "<台帳ディレクトリ>/dispatch-project.toml" | None,
         "issue": {"tracker": "gh" | None, "repo": str | None,
                   "close_on_merge": bool, "done_status": str | None,
                   "claim_label": str, "ready_label": str | None,
                   "source": "config"|"remote"},
         "pr":    {"tracker": "gh" | None, "repo": str | None, "source": "config"|"issue"|"remote"},
         "worker": {"standing": [str, ...]}}

    `config_path` は**効いた config の絶対パス** (読めなければ None)。置き場が version 管理の
    外にあるぶん、どの file が効いたかを呼び出し側から見えるようにしておく。

    `close_on_merge` / `done_status` は「closes PR が merged になったとき issue を閉じてよいか」の
    宣言で、**server はこれを使って何もしない** — 閉じるのは orchestrator。server が持つのは
    書式の検証と公開までで、遷移そのものは LLM の領分に残す (ADR 0040)。`worker.standing` も
    同じ立場で、**貼るのは orchestrator** (server は prompt を解釈しない)。

    `ready_label` は候補プール (AFK-ready) を表す triage label の綴りで、**未宣言を既定へ倒さない
    唯一の宣言**。読み手が推測で埋めると外した綴りが `count: 0` を返して「候補が空」に化けるので、
    None は「読み手が止まる合図」として配る (`claim_label` が必ず値を返すのと逆)。

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
        # `[worker]` を書いていない config でも key を欠かさない (置き場側の `close_on_merge` と
        # 同じ規則) — 有無で分岐させると、読み手ごとに「無い」の読みがぶれる
        "worker": (config or {}).get("worker") or {"standing": []},
    }


def _resolve_issue(root, config):
    if config is not None:
        return {**config["issue"], "source": "config"}
    # 宣言が無い環境でも issue 側の key を欠かさない — 呼び出し側 (orchestrator) が
    # `close_on_merge` の**有無**で分岐せず値だけを読めるようにする (欠けた key を
    # 「宣言していない」と読むか「false」と読むかは、読み手ごとにぶれる)。
    # `claim_label` は tracker を判定できなくても既定が入る — None を配ると claim が
    # label を付けず、候補除外も効かないまま二重 dispatch が黙って成立する。
    # `ready_label` は逆に None のまま — 推測できる綴りが無い (環境の triage 語彙)
    tracker = tracker_from_remote(root)
    return {
        "tracker": tracker,
        "repo": None,
        "close_on_merge": False,
        "done_status": None,
        "claim_label": default_claim_label(tracker),
        "ready_label": None,
        "source": "remote",
    }


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
