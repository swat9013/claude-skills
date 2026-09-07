"""利用者環境の設定 (`~/.config/swat-skills/config.json`) を読む — server 内の**単一箇所**。

`dispatch-project.toml` (`project`) との違いは**識別子の軸**。あちらは project 1 つの宣言
(どの tracker のどの repo を置き場にするか) なので台帳の隣に置くが、こちらは**利用者 1 人**の
設定で、project をまたいで 1 つしかない。dashboard は台帳 root 配下の全 project を 1 枚に
並べる画面なので、その起動設定は project 側に載せると置き場が project 数だけ増える。

| | 置き場 | 軸 |
|---|---|---|
| `dispatch-project.toml` | `<台帳ディレクトリ>/` | project 1 つ |
| `config.json` (本 module) | `~/.config/swat-skills/` | 利用者 1 人 |

読みは **file が無ければ既定値で生成してから読む**。人手の設置を前提にすると、設定できる
ことに気づかない環境が既定値のまま残り、`autostart` を切る導線も見つからない。

失敗の扱いは**宣言が在るかどうか**で割る。

| 起きたこと | 扱い |
|---|---|
| 宣言が在って、書式が壊れている | `UserConfigError` (呼び出し側が server ごと止める) |
| 宣言が在って、読めない (`OSError`) | `UserConfigError` (同上) |
| 宣言が無く、置けもしない (`OSError`) | stderr へ理由を出し、既定値で続行する |

**在る宣言を握り潰さないのは** (`project` と同じ規則)、綴り違い・読めない宣言が「宣言して
いない」と同じ挙動になる経路を作らないため — `dashbord` を黙って無視するのも、権限で読めない
file を既定値へ倒すのも、**切ったはずの autostart が効かないまま dashboard が上がり続ける**
同じ結末になる。

**置けない環境で落とさないのは**、この file が dispatch の前提ではないから。read-only な home や
書き込みを塞いだ sandbox で server ごと停めると、dashboard 1 枚のために台帳・tracker・pane の
全 tool を失う。**宣言が存在しない**なら既定値が利用者の意図の唯一の解釈で、置けなかったことは
stderr に残る。

**宣言されていない key は既定へ倒す** (これは握り潰しではなく仕様化された既定値で、テストで
固定してある)。`{}` は「何も宣言していない」であって壊れた config ではない。落とすのは
**書式そのものが壊れているとき** — JSON として読めない・未知の綴り・型違い・範囲外。
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import dashboard as dashboard_mod

# 利用者 1 人分の設定の正本。**探索しない** — 環境に 1 つ
DEFAULT_PATH = Path.home() / ".config" / "swat-skills" / "config.json"

# section ごとの既定値。**生成する本文もここから作る** ので、既定値の綴りは 1 箇所にしかない。
# port の既定は dashboard 側の正本 (`dashboard.DEFAULT_PORT`) を参照する — 手元に写すと、
# dashboard が待ち受ける port と config が生成する port が別々に動きうる
DEFAULTS = {"dashboard": {"autostart": True, "port": dashboard_mod.DEFAULT_PORT}}

MIN_PORT = 1
MAX_PORT = 65535


class UserConfigError(RuntimeError):
    """利用者設定を読めない (JSON として壊れている / 未知の綴り / 型違い)。"""


def load_or_create(path=None):
    """設定を読んで検証済み dict にする。file が無ければ既定値で置いてから読む。

    Args:
        path: 読む file。**テストと検証のためだけの引数** で、実運用は既定 (`DEFAULT_PATH`)

    返るのは全 key が埋まった dict (`{"dashboard": {"autostart": bool, "port": int}}`)。
    呼び出し側が `.get` で既定を補う必要は無い — 補う処理が呼び出し側に散ると、既定値の
    綴りが本 module の外に増える。

    **生成する副作用を名前に出してある。** `load` だと、読むだけのつもりの呼び出しが
    `~/.config` へ file を置く。
    """
    target = DEFAULT_PATH if path is None else Path(path)
    body = _read_or_create(target)
    if body is None:
        # 読めず置けもしない環境。既定値も本 module の書式を通す — 返り値の形が経路で変わらない
        return _validate(target, render(DEFAULTS))
    return _validate(target, body)


def _read_or_create(path):
    """本文を返す。宣言が無く置けもしない環境では None (呼び出し側が既定値で続行する)。

    **在る file を読めないのは落とす側**。権限や種別で読めない宣言を既定値へ倒すと、切った
    はずの `autostart` が効かないまま dashboard が上がり続ける (書式違反と同じ結末になる)。
    """
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        # どちらも「宣言が存在しない」。`NotADirectoryError` は途中の path 要素が file
        # (`~/.config/swat-skills` が file 等) で、その下に宣言が在りようがない状態
        pass
    except OSError as exc:
        raise UserConfigError(f"{path} は在るが読めない: {exc}") from exc
    body = render(DEFAULTS)
    try:
        _place(path, body)
    except OSError as exc:
        _warn(f"{path} を置けないので既定値で続行する: {exc}")
        return None
    return body


def _place(path, body):
    """同じディレクトリの一時 file へ書き切ってから rename する。

    **中身の無い file を他プロセスに観測させない** のが要点。`open(path, "x")` の排他生成では
    生成と書き込みの間に隙間があり、そこで読んだ側は空 file を「壊れた config」として扱う
    (= server ごと止まる)。dispatch-ops server は session ごとに起動するので、初回に複数の
    worker pane が同時に立ち上がればこの隙間は現実に踏まれる。rename は atomic なので、path
    には「無い」か「完全な本文」しか現れない。

    後勝ちで上書きする (`os.replace`) — 競合する書き手は初回生成どうしで中身が同じなので、
    どちらが勝っても終状態は変わらない。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".config-", suffix=".json", delete=False
    ) as sink:
        sink.write(body)
        temporary = Path(sink.name)
    try:
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _warn(message):
    """stderr へ出す (server の起動 log に残る)。"""
    print(f"dispatch-ops user config: {message}", file=sys.stderr)


def render(config):
    """設定の本文 (JSON) を組み立てる。**生成も解析と同じここに置く** (`project` と同じ規則)。

    生成側だけを別 module へ出すと、key 名の変更が 2 箇所に分かれる。行末の改行を付けるのは
    人が開いて編集する file だから。
    """
    return json.dumps(config, ensure_ascii=False, indent=2) + "\n"


def _validate(path, body):
    """未知の section / key / 型違いを名指しで落とし、宣言されていない key を既定で埋める。"""
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UserConfigError(
            f"{path} を JSON として読めない: {exc} (直せないなら削除すれば既定値で置き直す)"
        ) from exc
    if not isinstance(raw, dict):
        raise UserConfigError(f"{path} の最上位が object でない: {type(raw).__name__}")
    unknown = sorted(set(raw) - set(DEFAULTS))
    if unknown:
        raise UserConfigError(
            f"{path} に未知の section: {', '.join(unknown)} (書けるのは {', '.join(DEFAULTS)})"
        )
    return {"dashboard": _validate_dashboard(path, raw.get("dashboard", {}))}


def _validate_dashboard(path, table):
    if not isinstance(table, dict):
        raise UserConfigError(f"{path} の dashboard が object でない: {table!r}")
    defaults = DEFAULTS["dashboard"]
    unknown = sorted(set(table) - set(defaults))
    if unknown:
        raise UserConfigError(
            f"{path} の dashboard に未知の key: {', '.join(unknown)} "
            f"(書けるのは {', '.join(defaults)})"
        )
    return {
        "autostart": _validate_autostart(path, table.get("autostart", defaults["autostart"])),
        "port": _validate_port(path, table.get("port", defaults["port"])),
    }


def _validate_autostart(path, value):
    """真偽値以外を落とす。`"false"` (文字列) が真として通ると、切った宣言が効かない。"""
    if not isinstance(value, bool):
        raise UserConfigError(f"{path} の dashboard.autostart が真偽値でない: {value!r}")
    return value


def _validate_port(path, value):
    """整数かつ bind できる範囲であることを確かめる。

    `bool` を先に弾くのは Python では `bool` が `int` の派生型だから — `true` が port 1 として
    通ると、失敗が dashboard の bind まで遅れて理由が config から読めなくなる。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise UserConfigError(f"{path} の dashboard.port が整数でない: {value!r}")
    if not MIN_PORT <= value <= MAX_PORT:
        raise UserConfigError(
            f"{path} の dashboard.port が範囲外: {value} ({MIN_PORT}〜{MAX_PORT})"
        )
    return value
