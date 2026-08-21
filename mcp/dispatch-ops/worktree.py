"""WorktreePort — dispatch worktree の観測と安全回収の継ぎ目。

spec §4.4 の `observe_worktrees` / `worktree_tidy` を提供する。VCS は git のみを想定するので
port (`WorktreePort`) と adapter (`GitWorktrees`) を 1 module に置く (tracker が 3 module に
割れているのは adapter が gh / glab の 2 つあるため)。

継ぎ目は 3 段ある。呼び出し側 (`main.py` / テストの代役) が見るのは `WorktreePort` の 4 method
だけで、その下で git を起こすのが `proc.command_runner` で作った `run_command`。その間に、観測から
削除計画を起こす**純関数の内部 seam** (`tidy_snapshot` / `plan_tidy` / `derive_tidy_scope`) が在る。

内部 seam を面に出してあるのは、掃除の選別 (どのツリーを消し、どれを除外するか) が本 module で
最も不可逆な判断であり、git を起こさずに 1 本のテストで再現できる形に保つため (issue #610)。
port 面には出さない — 出すと呼び出し側が実装の途中経過に結合し、port を差し替えられなくなる。
安全回収 (`_remove_if_safe`) と parse / 照合の段 (`_parse_*` / `_slug_of` / `_lock_state` /
`_removal_args`) は引き続き `_` prefix の実装詳細で、自 module のテスト以外から import しない。

台帳を伴う掃除 (`tidy_dispatches` / `sweep_dispatches`) は port の面に載せず module 関数に置く。
台帳から保護 / 回収対象を導出するのも回収後に `cleaned` を記帳するのも VCS に触れない手続きなので、
port へ持ち上げると判断材料の導出まで VCS 実装の選択に引きずられる。port と ledger はどちらも
引数で受ける (本 module は生成しない)。

移植元と、移植にあたり変えた点:

- `_remove_if_safe` の安全規則は `worktree_vocabulary.py` (issue #386 で 3 経路の重複を潰した
  正本) から移す。**dirty なら消さない**・**`branch -d` を `-D` に昇格しない**・
  **live / 読めない lock は踏み越えない** の 3 規則が本 module の存在理由であり、違反すると
  成果が消える
- 掃除の選別 (merged branch / squash merge 後の駐機 worktree の 2 経路) は `repo_tidy.py` から移す
- **`checkout` / `pull --ff-only` は移さない**。旧 script は「repo root からのみ呼ぶ」前提を
  `resolve_root` の例外で守っていたが、本 server は pane (linked worktree の中) からも同じ tool を
  呼ばれる。merged 判定は `origin/<default>` を基準にすれば working tree を動かさずに済むので、
  main の working tree を書き換える手続きごと落とす (`fetch --prune` は remote-tracking ref しか
  触らないので残す)
- 保護対象は台帳の phase から導出する (`derive_tidy_scope`)。LLM が会話内記憶から `--active` を
  手組みする旧経路 (誤ると作業ツリー消失) を廃した spec §4.4 の主眼

`i<N>` 語彙の判定は `refs.parse_number_slug` が唯一の正本。本 module は branch 命名規約
(Claude Code の `--worktree i<N>` が branch を `worktree-i<N>` にする) だけを所有し、prefix を
剥がしてから refs へ渡す。語彙側に別定義を置くと、pane label (refs 由来) と worktree ディレクトリ名が
同じ綴りを別の番号に読む drift が起きる。

**掃除 (tidy / sweep) の照合だけは綴りを分類せず、台帳由来の slug 集合への membership で行う**
(`_slug_of`、#621)。番号を持たない tracker (jira の `swatcf-14`) の木もこれで保護・回収でき、
`observe` の `i<N>` 規約は動かさずに済む。綴りで分類しないのは、key slug の綴りが `feature-2`
のような**人間が自由に付ける branch 名と区別が付かない**ため — 綴りだけで dispatch 由来と
判定すると、台帳と無関係なツリーを掃除の対象に取り込む。
"""

import os
import re
import stat
import time

import ledger as ledger_mod
import proc
import refs
import vocabulary

SUBPROCESS_TIMEOUT_SEC = 120

# Claude Code の `--worktree i<N>` が branch 名に付ける prefix (worktree ディレクトリ名には付かない)。
BRANCH_PREFIX = "worktree-"

# dispatch worktree の置き場所 (main worktree root からの相対)。
WORKTREES_SUBDIR = (".claude", "worktrees")

# 名前だけで削除対象から外す branch。default branch は実行時に足す。
PROTECTED = frozenset(
    {
        "main",
        "master",
        "develop",
        "development",
        "release",
        "pre-release",
        "staging",
        "production",
    }
)

# 台帳から保護対象 / 回収対象を導出する phase は vocabulary の属性表から取る
# (`worktree_protected` / `worktree_reclaim` 軸)。直書きすると phase を足したときに黙って
# 古いまま通り、保護漏れが未 commit 作業の消失に化ける。

# sweep の閾値 (spec 2026-08-02-worktree-sweep-all-trees-design.md §判定)。dial であって
# 判断ではない — 呼び出し側が上書きできる。max_age を十分大きくすれば clean のみの回収に退行する。
SWEEP_DEFAULT_GRACE_HOURS = 2
SWEEP_DEFAULT_MAX_AGE_HOURS = 24

# Claude Code の `--worktree` が付ける lock 理由から pid を取り出す
# (`claude session i426 (pid 44689 start Sun Aug  2 13:30:32 2026)`、実測 2026-08-02)。
LOCK_PID = re.compile(r"\bpid (\d+)\b")


class WorktreeError(RuntimeError):
    """git の前提が成立しない (repo root を解決できない / default branch が無い)。"""


# --- `i<N>` 規約 ---------------------------------------------------------------


def _issue_ref(number):
    """issue 番号 → 正準の参照名。pane label と worktree ディレクトリ名の両方に使う。"""
    return f"i{number}"


def _strip_branch_prefix(value):
    """branch 命名規約の prefix を剥がして slug の綴りだけにする (本 module 唯一の綴り知識)。"""
    name = value or ""
    if name.startswith(BRANCH_PREFIX):
        name = name[len(BRANCH_PREFIX) :]
    return name


def _parse_branch(value):
    """`i<N>` / `worktree-i<N>` から issue 番号を返す (規約外は None)。

    prefix を剥がす knowledge だけが本 module の担当で、残りの綴りが `i<N>` 語彙かどうかの
    判定は refs に委ねる。`i007` のような非正準名は refs が撥ねるので追跡対象外になる。

    読むのは number slug (`i<N>`) だけ。`observe` の「dispatch した作業ツリーの一覧」を
    番号体系に閉じておくための判定であって、**掃除の保護 / 回収の照合はこれを使わない**
    (`_slug_of` の membership が担い、番号を持たない tracker の木もそちらで守られる)。
    """
    return refs.parse_number_slug(_strip_branch_prefix(value))


def _issue_of(branch, path):
    """branch / worktree path が属する issue 番号 (規約外なら None)。

    branch 名と path の basename の両方で照合する。`--worktree i<N>` は path が `i<N>`、
    branch 名が `worktree-i<N>` になるため、片方だけの照合では取りこぼす。
    """
    number = _parse_branch(branch)
    if number is not None:
        return number
    if path:
        return _parse_branch(os.path.basename(path.rstrip("/")))
    return None


def _slug_of(branch, path, slugs):
    """branch / worktree path が指す issue slug のうち `slugs` に属するもの (無ければ None)。

    **綴りを「slug か否か」で分類せず、台帳が持っている slug 集合への membership で決める。**
    key slug (`swatcf-14`) の綴りは `feature-2` / `wip-3` のような人間が自由に付ける branch 名
    と区別が付かないので、綴りだけで dispatch 由来と判定すると台帳と無関係なツリーを保護・
    回収の対象に取り込む。集合が台帳由来である限り、当たった綴りは必ずその dispatch を指す。

    番号体系 (gh / glab) の挙動は変わらない — `i217` の membership は、台帳が `gh#217` を
    持っているときだけ当たる旧来の番号照合と 1:1 で対応する。`i0217` のような非正準名は
    `refs.format_issue_slug` が正準形しか作らないので集合に入らず、従来どおり当たらない。

    branch 名と path の basename を独立に見るのは、`--worktree i<N>` が path を `i<N>`、
    branch を `worktree-i<N>` にするため (片方だけの照合では取りこぼす)。
    """
    if not slugs:
        return None
    candidates = [_strip_branch_prefix(branch)]
    if path:
        candidates.append(_strip_branch_prefix(os.path.basename(path.rstrip("/"))))
    for candidate in candidates:
        if candidate and candidate in slugs:
            return candidate
    return None


# --- git 出力の parse -----------------------------------------------------------


def _parse_worktrees(text):
    """`git worktree list --porcelain` → [{path, branch, locked}] (登録順。先頭が main)。

    登録順を保つのは、先頭 entry = main worktree という git の保証に依る呼び出し側があるため。
    locked は理由付きと理由なしを区別せず保持する (未 lock は None、理由なし lock は "")。
    record 間の空行で状態をリセットするので、branch を持たない entry (detached) が次の entry へ
    漏れない。
    """
    records = []
    current = None
    for line in text.splitlines():
        if line.startswith("worktree "):
            current = {"path": line[len("worktree ") :], "branch": None, "locked": None}
            records.append(current)
        elif not line.strip():
            current = None
        elif current is None:
            continue
        elif line.startswith("branch refs/heads/"):
            current["branch"] = line[len("branch refs/heads/") :]
        elif line == "locked" or line.startswith("locked "):
            current["locked"] = line[len("locked ") :] if line.startswith("locked ") else ""
    return records


def _worktree_map(records):
    """porcelain の record 列 → {branch: path}。掃除対象は branch 単位で決まる。"""
    return {r["branch"]: r["path"] for r in records if r["branch"]}


def _lock_map(records):
    """porcelain の record 列 → {branch: locked}。未 lock は None のまま持つ。

    `_worktree_map` と分けてあるのは、選別 (branch → path) と回収可否 (lock 状態) が別の
    問いだから。値の None を「entry が無い」と混同させないため、`get` の既定値 (未 lock) と
    一致させてある。
    """
    return {r["branch"]: r["locked"] for r in records if r["branch"]}


def _parse_merged_branches(text):
    """`git branch --merged <ref>` の出力 → branch 名。現在 HEAD (`*`) は除き `+` 印は剥がす。

    `+` (他 worktree で checkout 中) は残す — その worktree を remove してから branch -d するのが
    本 module の主目的。
    """
    names = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("*"):
            continue
        if stripped.startswith("+ "):
            stripped = stripped[2:].strip()
        if stripped.startswith("(") or " -> " in stripped:
            # detached HEAD 表示 ("(HEAD detached at ...)") と symref は branch ではない
            continue
        names.append(stripped)
    return names


# --- lock の読み (tidy / sweep 共通) ------------------------------------------------


def _parse_lock_pid(reason):
    """lock 理由から pid を取り出す (読めない形式なら None)。

    pid を書かない lock は Claude Code 以外が付けたものなので、呼び出し側は保護側に倒す。
    """
    match = LOCK_PID.search(reason or "")
    return int(match.group(1)) if match else None


def _pid_alive_using(kill):
    """`kill(pid, 0)` で pid 生存を判定する述語を作る。**ESRCH のときだけ False** を返す。

    `EPERM` (存在するが signal を送れない) を死亡と読むと稼働中セッションの worktree を
    消す。sandbox 下では稼働中プロセスへの `kill(pid, 0)` が実際に EPERM で落ちる
    (実測 2026-08-02) ため、ESRCH 以外はすべて生存側に倒す。

    signal 送出を引数に取るのは、この errno 規則だけを実 process 抜きで固定できるように
    するため。判定そのものの差し替えは adapter の `pid_alive` 注入が担う (2 段に分けて
    あるのは、lock の live / stale 分岐を試すのに毎回 errno を作らせないため)。
    """

    def _alive(pid):
        try:
            kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True

    return _alive


# 生存判定の実体。`GitWorktrees(pid_alive=...)` の既定であり、production で使う唯一の束縛
_pid_alive = _pid_alive_using(os.kill)


def _lock_state(locked, pid_alive):
    """`git worktree list --porcelain` の locked 値 → `unlocked` / `live` / `stale` / `unparsed`。

    `locked` が None のときだけ未 lock。理由なし lock ("") は「読めない lock」であって
    未 lock ではない — 消せる側に倒すと他者の lock を踏む。

    `pid_alive` (pid -> bool) は lock に書かれた pid の生存判定。既定を持たせない — 既定で
    実 process を叩くと、呼び出し側が注入し忘れても気づけない。
    """
    if locked is None:
        return "unlocked"
    pid = _parse_lock_pid(locked)
    if pid is None:
        return "unparsed"
    return "live" if pid_alive(pid) else "stale"


def _removal_args(path, *, locked, force):
    """worktree remove の argv。locked なら `-f -f` (実測 2026-08-02: それ以外は rc=128)。

    `--force` 1 個では lock を越えられず、`-f -f` は lock と同時に dirty 保護も踏み越える。
    そのため clean 判定は remove の試行ではなく明示の `status --porcelain` で行う
    (前身 GC v2 の TOCTOU 排除は lock 下では成立しない)。
    """
    flags = ["--force", "--force"] if locked else (["--force"] if force else [])
    return ["worktree", "remove", *flags, path]


# 回収を諦める lock 状態 (稼働中セッション / 読めない lock)。踏み越えると他者の作業を消す。
BLOCKING_LOCK_STATES = ("live", "unparsed")


# --- 安全回収 (worktree_vocabulary.remove_if_safe の移植) ---------------------------


def _git_error(step, args, rc, stderr):
    """git 失敗の構造化。stderr は生のまま報告用に残す。"""
    detail = stderr.strip()
    return {
        "step": step,
        "stderr": detail,
        "message": f"git {args[0]} failed (exit {rc}): {detail}",
    }


def _remove_if_safe(git, root, worktree, branch, *, locked=None, pid_alive):
    """dirty 検査 → lock 判定 → worktree remove → 非昇格の `branch -d` (安全規則の唯一の実装)。

    守る規則は 3 つ: **dirty な worktree は消さない** (未回収の変更を失う)、**live / 読めない
    lock は踏み越えない** (稼働中セッションの作業ツリーを消す)、**`branch -d` が拒んだら `-D`
    へ昇格しない** (未 merge commit を失う)。

    段の順序が規則そのもの。dirty 判定は `-f -f` を撃つ前に済ませる — `-f -f` は lock と同時に
    dirty 保護も踏み越えるため、remove の rc を dirty のシグナルとして使えない。

    結果の分類はしない — 各段の生の結果を返し、削除拒否を成功と読むか失敗と読むかは呼び出し側に
    残す (merged branch 経路では失敗、駐機 worktree 経路では squash merge 後の正常結果)。

    引数:
    - git: (cwd, args) -> (rc, stdout, stderr)。本関数は raise せず、失敗は
      error {step, stderr, message} で返す
    - worktree: falsy なら worktree 操作を飛ばし branch 削除だけ試みる (merged branch 経路)
    - branch: falsy / "HEAD" なら branch 削除自体を試みない (detached worktree の回収)
    - locked: `git worktree list --porcelain` の locked 値をそのまま渡す (None = 未 lock)。
      `live` / `unparsed` なら remove も branch 削除も撃たず `_lock_state` を載せて返す
    - pid_alive: lock の pid 生存判定 (pid -> bool)。git と同じ外部状態の観測なので注入で受ける
    """
    result = {
        "dirty": None,
        "lock_state": None,
        "removed": False,
        "branch": branch,
        "branch_deleted": None,
        "branch_error": None,
        "error": None,
    }
    if worktree:
        args = ["status", "--porcelain"]
        rc, out, err = git(worktree, args)
        if rc != 0:
            # dirty 不明を clean と読ませない (未回収の変更ごと消す判断につながる)
            result["error"] = _git_error("status", args, rc, err)
            return result
        result["dirty"] = bool(out.strip())
        if result["dirty"]:
            return result
        result["lock_state"] = _lock_state(locked, pid_alive)
        if result["lock_state"] in BLOCKING_LOCK_STATES:
            # 稼働中セッション / 他者の lock。branch はその worktree に checkout 済みなので
            # branch 削除も試みない
            return result
        args = _removal_args(worktree, locked=result["lock_state"] == "stale", force=False)
        rc, _out, err = git(root, args)
        if rc != 0:
            result["error"] = _git_error("worktree-remove", args, rc, err)
            return result
        result["removed"] = True
    target = result["branch"]
    if target and target != "HEAD":
        # `-D` への昇格はしない (未 merge branch の強制削除は成果喪失に直結する)
        rc, _out, err = git(root, ["branch", "-d", target])
        result["branch_deleted"] = rc == 0
        if rc != 0:
            result["branch_error"] = err.strip()
    return result


# --- 掃除の選別 (repo_tidy の移植) --------------------------------------------------


def _is_protected(branch, path, protected_slugs):
    """台帳が保護している issue に属する branch / worktree か。

    `protected_slugs` は `derive_tidy_scope` が台帳から導出した issue slug の集合。番号を
    持たない tracker (jira の `swatcf-14`) の entry も同じ集合に入るので、tidy / sweep の
    両方でその worktree が守られる (#621)。

    branch 名と worktree path を**独立に**照合する — 片方だけが保護対象を指す食い違い
    (branch `worktree-i218` が path `i217` に checkout されている等) でも、どちらか一方が
    当たれば保護する側に倒す (`_slug_of`)。
    """
    return _slug_of(branch, path, protected_slugs) is not None


def _select_branches(merged, worktrees, default_branch, protected_slugs):
    """merged branch → 削除対象と除外理由。除外は報告用に理由付きで残す。"""
    targets, excluded = [], []
    for branch in merged:
        if branch == default_branch or branch in PROTECTED:
            excluded.append({"branch": branch, "reason": "protected-branch"})
            continue
        path = worktrees.get(branch)
        if _is_protected(branch, path, protected_slugs):
            excluded.append({"branch": branch, "reason": "ledger-protected"})
            continue
        targets.append({"branch": branch, "worktree": path})
    return targets, excluded


def same_worktree(recorded, observed, root):
    """記録パスと観測パスが同じツリーを指すか。判定できないなら None。

    issue slug は project に 1 つでも clone をまたぐと一意でない (ADR 0036 — 実装 repo は
    複数ありうる)。slug だけで突き合わせると**別 clone の同 slug ツリーを回収**し、その回収を
    根拠に entry が `cleaned` へ進んで本物のツリーが永久に取り残される。

    相対パスは root 基準で解く (台帳導入初期の entry は相対で記録されている)。realpath を
    通すのは、ghq 配下を symlink にした構成 (ADR 0027) で記録と git の答えが別表記になる
    ため — 表記差を不一致と読むと、回収すべきツリーが回収されない側に倒れる。
    """
    if not recorded or not observed:
        return None
    if not os.path.isabs(recorded):
        if not root:
            return None
        recorded = os.path.join(str(root), recorded)
    return os.path.realpath(recorded) == os.path.realpath(observed)


def _select_reclaim(worktrees, reclaim_worktrees, protected_slugs, handled, root):
    """台帳が `done` と記録した issue の worktree → 削除対象と除外理由。

    `reclaim_worktrees` は issue slug → 台帳が記録した worktree パス (無ければ None)。記録が
    あるのに観測したパスと違うツリーは**別 clone の同 slug** なので回収しない。記録が無い entry
    は slug だけで回収する (記録以前の entry を回収不能にしない)。

    merged branch 経路が既に扱った branch (`handled`) は飛ばす — 同じ worktree を 2 回
    remove しにいかず、報告も重複させない。
    """
    targets, excluded = [], []
    for branch, path in sorted(worktrees.items()):
        slug = _slug_of(branch, path, reclaim_worktrees)
        if slug is None or branch in handled:
            continue
        if branch in PROTECTED:
            # path だけが規約に合う worktree (protected branch を checkout 中) を消さない
            excluded.append({"branch": branch, "reason": "protected-branch"})
            continue
        if _is_protected(branch, path, protected_slugs):
            excluded.append({"branch": branch, "reason": "ledger-protected"})
            continue
        recorded = reclaim_worktrees[slug]
        if same_worktree(recorded, path, root) is False:
            excluded.append(
                {
                    "branch": branch,
                    "reason": "recorded-path-mismatch",
                    "worktree": path,
                    "recorded": recorded,
                }
            )
            continue
        targets.append({"branch": branch, "worktree": path, "issue_slug": slug})
    return targets, excluded


def _contains_cwd(cwd, path):
    """`path` の worktree が `cwd` を含むか (server プロセスの足元を消さないための判定)。"""
    real = os.path.realpath(path)
    return cwd == real or cwd.startswith(real + os.sep)


def _drop_self(targets, excluded, cwd):
    """server プロセスが立っている worktree を対象から外す (自分の足元を消さない)。"""
    kept = []
    for target in targets:
        path = target["worktree"]
        if path and _contains_cwd(cwd, path):
            excluded.append({"branch": target["branch"], "reason": "server-cwd"})
            continue
        kept.append(target)
    return kept


# --- 掃除の計画 (観測 snapshot → 削除計画) ------------------------------------------


def tidy_snapshot(*, root, cwd, default_branch, merged_text, worktree_text):
    """git の生出力 → 計画に要る観測だけを持つ snapshot (parse をここへ閉じる)。

    - `merged`: `branch --merged <ref>` が挙げた branch 名
    - `worktrees`: branch → worktree パス。掃除の単位が branch なので、branch を持たない
      worktree (detached) はここで落ちる
    - `locks`: branch → lock 理由 (未 lock は None、理由なし lock は "")

    `root` / `cwd` も載せる — 計画が記録パスの照合 (`same_worktree`) と自分の足元の除外に
    使うため。**git には触れない**ので、テストは porcelain の text から snapshot を組んで
    計画だけを検査できる (issue #610 の内部 seam)。
    """
    records = _parse_worktrees(worktree_text)
    return {
        "root": str(root),
        "cwd": os.path.realpath(str(cwd)),
        "default_branch": default_branch,
        "merged": _parse_merged_branches(merged_text),
        "worktrees": _worktree_map(records),
        "locks": _lock_map(records),
    }


def plan_tidy(snapshot, protected_slugs, reclaim_worktrees):
    """観測 snapshot + 台帳由来の scope → 削除計画。git に触れない純関数。

    → `{"targets": [...], "excluded": [...]}`。target は
    `{branch, worktree, locked, route, issue_slug}` で、`route` が `branch -d` の拒否を
    どう読むかを決める (`merged` = merged と読んだのに消せない = 前提が崩れている /
    `reclaim` = squash merge 後の定常状態で正常結果)。

    2 経路の順序は計画の一部。merged branch 経路が扱った branch (対象・除外の両方) は
    `handled` に入り、駐機 worktree 経路は同じツリーを 2 度 remove しにいかない。除外の並び
    (merged の除外 → merged の server-cwd → 駐機の除外 → 駐機の server-cwd) も報告の外形
    なので保つ。

    merged 経路の `issue_slug` は**綴りから起こさず**、台帳由来の回収対象集合への membership
    でだけ付ける (#621)。集合の外の branch は帰属先無し (None) のまま報告し、台帳に居ない
    branch を「その issue のツリー」と名乗らせない。
    """
    excluded = []
    merged_targets, merged_excluded = _select_branches(
        snapshot["merged"],
        snapshot["worktrees"],
        snapshot["default_branch"],
        protected_slugs,
    )
    excluded.extend(merged_excluded)
    merged_targets = _drop_self(merged_targets, excluded, snapshot["cwd"])

    handled = {t["branch"] for t in merged_targets} | {e["branch"] for e in excluded}
    reclaim_targets, reclaim_excluded = _select_reclaim(
        snapshot["worktrees"],
        reclaim_worktrees,
        protected_slugs,
        handled,
        snapshot["root"],
    )
    excluded.extend(reclaim_excluded)
    reclaim_targets = _drop_self(reclaim_targets, excluded, snapshot["cwd"])

    locks = snapshot["locks"]
    targets = [
        {
            **target,
            "route": "merged",
            "issue_slug": _slug_of(target["branch"], target["worktree"], reclaim_worktrees),
            "locked": locks.get(target["branch"]),
        }
        for target in merged_targets
    ]
    targets += [
        {**target, "route": "reclaim", "locked": locks.get(target["branch"])}
        for target in reclaim_targets
    ]
    return {"targets": targets, "excluded": excluded}


def derive_tidy_scope(entries):
    """台帳 entry 列 → 保護対象 / 回収対象の issue slug (spec §4.4 の「台帳から自動導出」)。

    entry は `ledger.Ledger` の view 形式 (`phase` / `issue_ref` / `agent`) を読む。鍵は
    **番号ではなく issue slug** (`refs.format_issue_slug`) — 番号を持たない tracker
    (jira の `jira:PROJ-9` → `proj-9`) も同じ集合に入り、tidy / sweep の両方で守られる (#621)。

    slug を作れない entry (`issue_ref` が無い / 中立 ref の書式でない) は `unmappable` に
    落として報告する — 黙って無視すると「保護したつもりの worktree が消える」に化ける。
    正常な台帳では空だが、手で編集された state や将来の ref 語彙の取りこぼしをここで鳴らす。

    回収対象には記録された worktree パスを添える (slug だけでは clone をまたいで一意にならない
    — `same_worktree`)。保護対象は slug だけで持つ: 別 clone の同 slug まで保護に数えるのは
    過剰保護で、安全側の外し方だから。
    """
    protected, reclaim, unmappable = set(), {}, []
    for entry in entries:
        phase = entry.get("phase")
        if (
            phase not in vocabulary.PROTECTED_PHASES
            and phase not in vocabulary.RECLAIM_PHASES
        ):
            continue
        try:
            slug = refs.format_issue_slug(entry.get("issue_ref"))
        except refs.RefError:
            unmappable.append({"issue_ref": entry.get("issue_ref"), "phase": phase})
            continue
        if phase in vocabulary.PROTECTED_PHASES:
            protected.add(slug)
        else:
            reclaim[slug] = {
                "issue_ref": entry.get("issue_ref"),
                "worktree": (entry.get("agent") or {}).get("worktree"),
            }
    # 同じ slug が両方に立つことは phase が 1 つである以上ありえないが、保護を優先して念のため落とす
    for slug in protected:
        reclaim.pop(slug, None)
    return {"protected": protected, "reclaim": reclaim, "unmappable": unmappable}


# --- 台帳を伴う掃除 (tool `worktree_tidy` / `worktree_sweep` の実体) ------------------


def tidy_dispatches(port, ledger):
    """台帳から保護 / 回収対象を導出して掃除し、回収できた worktree の entry を `cleaned` へ送る。

    port と ledger をどちらも引数で受ける — 生成は tool 層の責務。`derive_tidy_scope` が既に
    台帳 view の形 (`phase` / `issue_ref` / `agent`) を読んでいた依存が、これで signature に
    現れる。
    """
    scope = derive_tidy_scope(ledger.list_entries())
    reclaim = scope["reclaim"]
    result = port.tidy(
        scope["protected"], {slug: rec["worktree"] for slug, rec in reclaim.items()}
    )
    cleaned, transition_errors, unattributed = _clean_reclaimed(
        ledger, reclaim, result["removed_worktrees"], port.root
    )
    result["ledger"] = {
        "repo_key": ledger.repo_key,
        "protected_slugs": sorted(scope["protected"]),
        "reclaimable_slugs": sorted(reclaim),
        "unmappable": scope["unmappable"],
        "cleaned": cleaned,
        "unattributed": unattributed,
        "transition_errors": transition_errors,
    }
    return result


def _clean_reclaimed(ledger, reclaim, removed_worktrees, root):
    """回収できた worktree を持つ entry を `cleaned` へ送る。

    → (遷移した ref, 遷移に失敗した ref, 記録パスと一致せず遷移させなかったもの)。

    遷移させるのは**実際に消えた** worktree の entry だけ。server 自身が今作った事実の記帳で
    あって判断ではないので、dirty で見送った worktree や最初から無かった worktree は動かさない
    (後者は台帳と現実の食い違いなので `resolve` の領分)。

    記録パスと違うツリーの回収では遷移させない — 別 clone の同 slug ツリーや merged branch 経路の
    巻き添えで `cleaned` にすると、その entry は二度と回収対象にならず本物のツリーが残り続ける
    (`same_worktree`)。黙って落とさず `unattributed` として返す。

    遷移の失敗は集めて返し、掃除の結果ごと落とさない — worktree は既に消えているので、
    例外で応答を捨てると「何が消えたか」の記録が呼び出し側に残らない。
    """
    cleaned, errors, unattributed = [], [], []
    for removed in removed_worktrees:
        record = reclaim.get(removed["issue_slug"])
        if record is None:
            continue
        issue_ref = record["issue_ref"]
        if same_worktree(record["worktree"], removed["worktree"], root) is False:
            unattributed.append(
                {
                    "issue_ref": issue_ref,
                    "worktree": removed["worktree"],
                    "recorded": record["worktree"],
                }
            )
            continue
        try:
            ledger.transition(
                issue_ref,
                "cleaned",
                note=f"worktree_tidy が {removed['worktree']} を回収した",
            )
            cleaned.append(issue_ref)
        except (ledger_mod.LedgerError, vocabulary.TransitionError) as exc:
            errors.append({"issue_ref": issue_ref, "error": str(exc)})
    return cleaned, errors, unattributed


def sweep_dispatches(port, ledger, *, grace_hours, max_age_hours, dry_run):
    """台帳が保護する issue だけを避けて worktree 領域を回収する。

    回収対象は台帳から導出しない (lock の pid 生存と最終活動時刻から port が決める) —
    台帳が見られない木を掃除するのが sweep の存在理由なので、`tidy` の選別と混ぜない。
    phase も動かさない: 回収した木が台帳 entry を持つとは限らず、持っていても phase を
    動かす根拠にならない。
    """
    scope = derive_tidy_scope(ledger.list_entries())
    result = port.sweep(
        scope["protected"],
        grace_hours=grace_hours,
        max_age_hours=max_age_hours,
        dry_run=dry_run,
    )
    result["ledger"] = {
        "repo_key": ledger.repo_key,
        "protected_slugs": sorted(scope["protected"]),
        "unmappable": scope["unmappable"],
    }
    return result


# --- sweep の判定 (`.claude/worktrees` 全域の有界化) ---------------------------------


def _admin_dir_last_activity(root, name):
    """worktree の最終活動時刻 (epoch 秒) を git admin dir の mtime から推定する。

    `.git/worktrees/<name>/` 直下ファイル (HEAD / index 等) は git 操作のたびに更新される
    ため、worktree root の mtime より生存 proxy として頑健。読めない場合 (dir 不在 / 権限)
    は None — 呼び出し側は安全側 (keep) に倒す。

    `GitWorktrees(last_activity=...)` の既定であり、production で使う唯一の束縛
    (`_pid_alive` と同じ扱い)。実 FS を叩く観測なので、テストは注入で差し替える。
    """
    admin = os.path.join(str(root), ".git", "worktrees", name)
    try:
        mtimes = [
            os.stat(os.path.join(admin, entry)).st_mtime
            for entry in os.listdir(admin)
            if os.path.isfile(os.path.join(admin, entry))
        ]
    except OSError:
        return None
    return max(mtimes, default=None)


# --- git 実行 (subprocess 境界) ----------------------------------------------------


# git の起動境界。テストと adapter の `run` 引数がこれを差し替える
run_command = proc.command_runner(error=WorktreeError, timeout_sec=SUBPROCESS_TIMEOUT_SEC)


# --- port と adapter --------------------------------------------------------------


class WorktreePort:
    """dispatch worktree への中立 API。VCS 固有の呼び出しは下の継ぎ目 method が担う。

    継ぎ目は**git を触る 4 method だけ**。安全回収と parse の段 (`_remove_if_safe` /
    `_parse_worktrees` 等) は adapter の内側にあり、port の面には出さない — 出すと呼び出し側が
    実装の途中経過に結合し、port を差し替えられなくなる。

    VCS に触れない純関数 (`derive_tidy_scope` の台帳導出、`tidy_snapshot` / `plan_tidy` の
    削除計画) は port を通さない (module 関数のまま)。adapter 越しにすると、判断材料の導出まで
    VCS 実装の選択に引きずられる。
    """

    # この port が面倒を見る clone の root。台帳が相対で記録したパスを解く基準になるので、
    # 面に出しておく (実装が持つ値であって、呼び出し側が設定するものではない)
    root = None

    def resolve_default_branch(self):
        """default branch 名と merged 判定に使う ref を `(name, ref)` で返す。"""
        raise NotImplementedError

    def observe(self):
        """`i<N>` 規約の worktree 一覧 + dirty 状態 → `{root, count, worktrees}`。"""
        raise NotImplementedError

    def probe(self, path, issue_ref):
        """記録された worktree パス 1 本を観測する (別 clone のツリーを照合する継ぎ目)。

        `observe` が返すのは**この port の root にぶら下がる worktree だけ**なので、別の
        clone に切られたツリーはそこに現れない。台帳 entry が持つパスを直接見る経路を
        分けてあるのは、「一覧に無い」を「消えた」と読ませないため (ADR 0036)。

        受け取るのは番号ではなく中立 ref。**番号を持たない ref (jira) でも記録パスを
        観測できる**ようにするためで、record の `label` / `issue_number` はその ref から
        導く (key で識別する ref の `issue_number` は null)。

        返り値は `observe` の record と同じ形 + `issue_ref` + `source`。**存在しないなら
        None、判定できなければ `WorktreeError`** — この 3 値を潰すと、観測できなかった
        ツリーが「回収済み」に化ける。
        """
        raise NotImplementedError

    def tidy(self, protected_slugs, reclaim_worktrees):
        """merged branch / 回収対象 worktree を安全規則に従って掃除する。

        `protected_slugs` は保護する issue slug の集合、`reclaim_worktrees` は回収する issue
        slug → 台帳が記録した worktree パス (無ければ None)。どちらも `derive_tidy_scope` が
        台帳から導出したもので、実装は「どれを保護すべきか」を判断せず渡されたまま適用する。
        """
        raise NotImplementedError

    def sweep(self, protected_slugs, *, grace_hours, max_age_hours, dry_run, now):
        """worktree 領域を生成主体を問わず回収し、総数を有界化する。

        閾値は判断ではなく dial なので既定値は実装側が持つ。`now` (epoch 秒) は時刻の注入口 —
        テストが経過時間を固定するために port の面に置いてある。
        """
        raise NotImplementedError


class GitWorktrees(WorktreePort):
    """WorktreePort の git 実装。

    `root` は main worktree root (`repo_key.main_worktree_root` が解決した値)。pane が linked
    worktree の中から呼んでも同じ root に収束するので、dispatcher と pane で対象がずれない。

    `cwd` は server プロセスが立っている場所。ここを含む worktree は回収対象から外す —
    自分の足元を消すと呼び出し元のセッションが宙に浮く。

    外部状態の観測は 3 つとも注入で受ける (`run` = git、`pid_alive` = lock の pid 生存、
    `last_activity` = git admin dir の mtime)。既定は production の実体だが、テストが差し
    替える経路を signature に出しておく — module 属性を書き換える経路しか無いと、注入し
    忘れが黙って実 process / 実 FS を叩く。
    """

    def __init__(
        self,
        root,
        cwd=None,
        run=run_command,
        pid_alive=_pid_alive,
        last_activity=_admin_dir_last_activity,
    ):
        self.root = str(root)
        self.cwd = os.path.realpath(str(cwd)) if cwd is not None else os.path.realpath(os.getcwd())
        self._run = run
        self._pid_alive = pid_alive
        self._last_activity = last_activity

    # --- git ------------------------------------------------------------------

    def _git(self, cwd, args):
        """git 実行。(rc, stdout, stderr) をそのまま返す — 失敗の扱いは呼び出し側が決める。"""
        return self._run(["git", "-C", str(cwd), *args])

    def _git_out(self, args):
        """成功前提の git 実行。非 0 exit は WorktreeError で即表面化する (fail-closed)。"""
        rc, out, err = self._git(self.root, args)
        if rc != 0:
            raise WorktreeError(f"git {args[0]} failed (exit {rc}): {err.strip()}")
        return out

    def _list_worktrees(self):
        return _parse_worktrees(self._git_out(["worktree", "list", "--porcelain"]))

    def resolve_default_branch(self):
        """default branch 名と merged 判定に使う ref を返す。

        merged 判定を `origin/<default>` で行うのが要点 — local の default branch を最新化する
        には checkout + pull が要り、それは main の working tree を動かす。remote-tracking ref
        なら `fetch --prune` だけで最新になり、どの worktree から呼ばれても副作用がない。

        origin/HEAD は `git clone` が設定するが `git remote add` では設定されないので、無い
        場合も remote-tracking ref があればそちらを基準にする。local branch へ落ちるのは
        remote 自体が無い repo だけ (この経路では基準が古くなりうるが、取りこぼす側に倒れる
        ので次サイクルで回収される)。
        """
        rc, out, _err = self._git(
            self.root, ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"]
        )
        if rc == 0 and out.strip():
            # prefix-strip で '/' 含む branch 名 (release/1.0 等) も正しく抽出する
            name = out.strip()[len("refs/remotes/origin/") :]
            return name, f"origin/{name}"
        for candidate in ("main", "master"):
            rc, _out, _err = self._git(
                self.root, ["show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"]
            )
            if rc != 0:
                continue
            rc_remote, _out, _err = self._git(
                self.root,
                ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{candidate}"],
            )
            return candidate, f"origin/{candidate}" if rc_remote == 0 else candidate
        raise WorktreeError(
            "default branch を解決できない (origin/HEAD も local main/master も無い)"
        )

    # --- observe --------------------------------------------------------------

    def observe(self):
        """`i<N>` 規約の worktree 一覧 + dirty 状態 (spec §4.4 の `observe_worktrees`)。

        規約外の worktree (main / 手動作成の feature ツリー) は返さない — 本 tool の用途は
        dispatch した作業ツリーと台帳の突合であって、repo の worktree 全量報告ではない。

        dirty が判定できなかった worktree は `dirty: null` + `dirty_error` で返す。
        `dirty: false` (検査して clean) と混同すると、未回収の変更ごと消す判断につながる。
        """
        observed = []
        for record in self._list_worktrees():
            number = _issue_of(record["branch"], record["path"])
            if number is None:
                continue
            rc, out, err = self._git(record["path"], ["status", "--porcelain"])
            observed.append(
                {
                    "label": _issue_ref(number),
                    "issue_number": number,
                    "path": record["path"],
                    "branch": record["branch"],
                    "locked": record["locked"],
                    "dirty": bool(out.strip()) if rc == 0 else None,
                    "dirty_error": None if rc == 0 else err.strip(),
                }
            )
        return {"root": self.root, "count": len(observed), "worktrees": observed}

    def probe(self, path, issue_ref):
        """記録された worktree パス 1 本の存在と dirty を見る (別 clone のツリー用)。

        相対パスは本 port の root から解く — 台帳導入初期の entry は相対で記録されており、
        絶対パス規約 (ADR 0036) 以前の記録を「判定できない」に落とすと保護が消える。

        `label` / `issue_number` は渡された ref から導く。**パスを見るのに番号は要らない**
        ので、番号を持たない ref (jira) でも記録パスの観測はこの経路で成立する
        (`issue_number` は null で返り、突合は `issue_ref` で行う)。

        `branch` / `locked` は **null 固定**。1 本のパスを見るだけで clone の worktree 一覧を
        引かないためで、「branch が無い」ではない (`source` がその区別の印)。

        存在しなければ None を返し、**stat が「無い」以外の理由で落ちたら raise する** —
        権限や I/O の失敗を「無かった」と読むと、生きている作業ツリーを回収対象に見せる。
        """
        parsed = refs.parse_issue_ref(issue_ref)
        target = path if os.path.isabs(path) else os.path.join(self.root, path)
        try:
            # `os.path.isdir` を使わないのは、存在しない (ENOENT) と判定できない (権限 / I/O)
            # を同じ False に潰すため。前者だけが「無かった」という観測になる
            found = stat.S_ISDIR(os.stat(target).st_mode)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise WorktreeError(f"worktree パスを観測できない ({target}): {exc}") from exc
        if not found:
            return None
        rc, out, err = self._git(target, ["status", "--porcelain"])
        return {
            "label": refs.format_issue_slug(parsed["ref"]),
            "issue_number": parsed["number"],
            "issue_ref": parsed["ref"],
            "path": target,
            "branch": None,
            "locked": None,
            "dirty": bool(out.strip()) if rc == 0 else None,
            "dirty_error": None if rc == 0 else err.strip(),
            "source": "recorded-path",
        }

    # --- tidy -----------------------------------------------------------------

    def tidy(self, protected_slugs, reclaim_worktrees):
        """掃除の 2 経路 (merged branch / 台帳が `done` と記録した駐機 worktree) を通す。

        観測 (`tidy_snapshot`) → 計画 (`plan_tidy`) → 適用の 3 段。**どれを消すかの判断は
        すべて計画側の純関数に在り**、本 method に残るのは git を撃つ段だけ (issue #610)。

        `fetch --prune` で remote-tracking ref だけを最新化してから merged 判定を撃つので、
        どの worktree から呼ばれても working tree に触らない。
        """
        default_branch, merged_ref = self.resolve_default_branch()
        result = {
            "ok": False,  # 掃除の完走後に確定させる
            "root": self.root,
            "default_branch": default_branch,
            "merged_ref": merged_ref,
            "fetch": self._fetch(),
            "removed_worktrees": [],
            "deleted_branches": [],
            "skipped": [],
            "excluded": [],
            "failed": [],
        }

        snapshot = tidy_snapshot(
            root=self.root,
            cwd=self.cwd,
            default_branch=default_branch,
            merged_text=self._git_out(["branch", "--merged", merged_ref]),
            worktree_text=self._git_out(["worktree", "list", "--porcelain"]),
        )
        plan = plan_tidy(snapshot, protected_slugs, reclaim_worktrees)
        result["excluded"] = plan["excluded"]
        for target in plan["targets"]:
            if target["route"] == "merged":
                self._remove_merged(target, result)
            else:
                self._remove_reclaimed(target, result)

        self._git_out(["worktree", "prune"])
        result["ok"] = result["fetch"]["ok"] and not result["failed"]
        return result

    def _fetch(self):
        """remote-tracking ref を最新化する。working tree には触れないので副作用がない。

        失敗しても掃除は続ける — merged 判定が古い origin/<default> 基準になるだけで、
        「merged でないものを消す」側には倒れない (取りこぼしは次サイクルで回収される)。
        """
        rc, _out, err = self._git(self.root, ["fetch", "--prune"])
        if rc != 0:
            return {"ok": False, "error": f"fetch --prune failed: {err.strip()}"}
        return {"ok": True, "error": None}

    def _reclaim(self, target, result):
        """dirty 検査 → lock 判定 → worktree remove → 非昇格 `branch -d`。共通部分を result へ。

        受け取る target は `plan_tidy` が組んだ 1 件 (`branch` / `worktree` / `locked` /
        `issue_slug`)。**どれを消すかは既に決まっている** — ここに残るのは撃つ段だけ。

        続行不能 (dirty / live な lock / worktree remove 失敗) なら None、そうでなければ結果を
        そのまま返す — `branch -d` の拒否をどう読むかは経路で違うので呼び出し側に残す。dirty
        判定自体ができない (status 失敗) のは前提不成立なので fail-closed に倒す。

        live / 読めない lock は `failed` ではなく `skipped` に積む。稼働中セッションを踏まな
        かったのは正常結果であって人手を要する異常ではない (`failed` に積むと `ok` が落ちて
        「人手が要る」信号が埋まる)。
        """
        branch, path = target["branch"], target["worktree"]
        outcome = _remove_if_safe(
            self._git,
            self.root,
            path,
            branch,
            locked=target["locked"],
            pid_alive=self._pid_alive,
        )
        error = outcome["error"]
        if error and error["step"] == "status":
            raise WorktreeError(error["message"])
        if error:
            result["failed"].append(
                {"branch": branch, "step": error["step"], "error": error["stderr"]}
            )
            return None
        if outcome["dirty"]:
            result["skipped"].append({"branch": branch, "worktree": path, "reason": "dirty"})
            return None
        if outcome["lock_state"] in BLOCKING_LOCK_STATES:
            result["skipped"].append(
                {
                    "branch": branch,
                    "worktree": path,
                    "reason": f"locked-{outcome['lock_state']}",
                }
            )
            return None
        if outcome["removed"]:
            result["removed_worktrees"].append(
                {"branch": branch, "worktree": path, "issue_slug": target["issue_slug"]}
            )
        return outcome

    def _remove_merged(self, target, result):
        """merged branch を worktree ごと回収する。`branch -d` の拒否は failed。"""
        outcome = self._reclaim(target, result)
        if outcome is None:
            return
        if outcome["branch_deleted"]:
            result["deleted_branches"].append(target["branch"])
        else:
            result["failed"].append(
                {
                    "branch": target["branch"],
                    "step": "branch-delete",
                    "error": outcome["branch_error"],
                }
            )

    def _remove_reclaimed(self, target, result):
        """台帳 `done` の worktree を回収する。`branch -d` の拒否は skipped (正常結果)。

        squash merge 後は拒否が定常状態であり、failed に積むと ok が落ち続けて「人手が要る」
        信号が埋まる。回収の目的物は worktree で、それは成功している。
        """
        outcome = self._reclaim(target, result)
        if outcome is None:
            return
        if outcome["branch_deleted"]:
            result["deleted_branches"].append(target["branch"])
        else:
            result["skipped"].append(
                {
                    "branch": target["branch"],
                    "worktree": target["worktree"],
                    "reason": "unmerged-branch",
                }
            )


    # --- sweep ----------------------------------------------------------------

    def sweep(
        self,
        protected_slugs,
        *,
        grace_hours=SWEEP_DEFAULT_GRACE_HOURS,
        max_age_hours=SWEEP_DEFAULT_MAX_AGE_HOURS,
        dry_run=False,
        now=None,
    ):
        """`.claude/worktrees` 配下の worktree を生成主体を問わず回収し、総数を有界化する。

        spec: `docs/superpowers/specs/2026-08-02-worktree-sweep-all-trees-design.md`
        (前身は orchestrator の GC v2。実装先が ADR 0012 で消えたため本 port へ移した)。
        `tidy` が届かない木 — harness の `agent-*` / 人間命名 / 台帳に entry の無い `i<N>` /
        dirty で見送られ続ける木 — を回収するのが存在理由なので、選別を tidy と混ぜない。

        優先順位付き規則 (上から順に評価し、最初に該当したところで確定する):

        1. 台帳が保護する issue (phase active / parked) → skip (`ledger-protected`)
        2. server プロセスの cwd を含む木 → skip (`server-cwd`。自分の足元を消さない)
        3. lock の pid が生存 / pid を読めない lock → skip (`locked-live` / `locked-unparsed`)
        4. 最終活動が不明 or 猶予内 → keep (`age-unknown` / `young`)
        5. clean → reap
        6. dirty かつ最終活動が max_age 超過 → force reap (preview 付き)
        7. それ以外 → keep (`dirty-young`。次サイクルで規則 6 が回収する)

        **契約 (時間軸)**: 未 commit 作業は `max_age_hours` (既定 24h) までのみ保護される。
        それを超えて最終活動の無い worktree は force reap され、未 commit 内容は返り値の
        preview (diffstat + untracked 一覧) 以外に痕跡を残さず失われる。退避 (patch 保存) は
        しない — 有界性優先の決定事項であり、復元が必要な作業は commit されているべき、が契約。

        branch は削除しない (E2BIG は worktree 数由来。commit 済み作業は branch ref に残る)。
        `dry_run` は判定だけを返す — git の破壊操作を一切撃たない。
        """
        moment = time.time() if now is None else now
        grace_seconds = float(grace_hours) * 3600
        max_age_seconds = float(max_age_hours) * 3600
        result = {
            "ok": False,  # 完走後に確定させる
            "root": self.root,
            "grace_hours": grace_hours,
            "max_age_hours": max_age_hours,
            "dry_run": bool(dry_run),
            "removed_worktrees": [],
            "planned": [],
            "kept": [],
            "excluded": [],
            "failed": [],
        }

        for record in self._list_worktrees():
            path, branch = record["path"], record["branch"]
            if not self._under_worktrees_dir(path):
                continue  # main working tree / worktree 領域外は管轄外
            entry = {"path": path, "branch": branch}
            reason = self._screen(record, protected_slugs, moment, grace_seconds)
            if reason is not None:
                bucket = "kept" if reason in ("young", "age-unknown") else "excluded"
                result[bucket].append({**entry, "reason": reason})
                continue
            self._sweep_one(record, entry, moment, max_age_seconds, dry_run, result)

        if not dry_run:
            # prune の失敗で raise しない。ここへ来た時点で force reap は済んでおり、
            # 返り値の preview が消えた未 commit 内容の唯一の手がかりだから — 例外にすると
            # 掃除の副作用だけが残って報告が消える (tidy が fail-closed なのは、あちらが
            # dirty を force しない = 失う内容を持たないため)
            rc, _out, err = self._git(self.root, ["worktree", "prune"])
            if rc != 0:
                result["failed"].append(
                    {"path": None, "branch": None, "step": "worktree-prune", "error": err.strip()}
                )
        result["ok"] = not result["failed"]
        return result

    def _under_worktrees_dir(self, path):
        """`<root>/.claude/worktrees/<name>` の直下か (main working tree を自然に外す)。"""
        parent, name = os.path.split(path.rstrip(os.sep))
        return bool(name) and os.path.split(parent)[1] == WORKTREES_SUBDIR[1] and os.path.split(
            os.path.split(parent)[0]
        )[1] == WORKTREES_SUBDIR[0]

    def _screen(self, record, protected_slugs, moment, grace_seconds):
        """git を撃たずに決まる除外理由 (回収候補なら None)。

        規則 1–4。lock と台帳という確度の高い生存シグナルを先に見て、どちらも無い木にだけ
        時間 (最終活動) を代替シグナルとして当てる。
        """
        path, branch = record["path"], record["branch"]
        if _is_protected(branch, path, protected_slugs):
            return "ledger-protected"
        if _contains_cwd(self.cwd, path):
            return "server-cwd"
        state = _lock_state(record["locked"], self._pid_alive)
        if state == "live":
            return "locked-live"
        if state == "unparsed":
            return "locked-unparsed"
        last = self._last_activity(self.root, os.path.basename(path.rstrip(os.sep)))
        if last is None:
            return "age-unknown"
        if moment - last < grace_seconds:
            return "young"
        return None

    def _sweep_one(self, record, entry, moment, max_age_seconds, dry_run, result):
        """候補 1 件を規則 5–7 で処理する (dirty 判定 → reap / force reap / keep)。"""
        path = record["path"]
        locked = record["locked"] is not None
        args = ["status", "--porcelain"]
        rc, out, err = self._git(path, args)
        if rc != 0:
            # dirty 不明を clean と読ませない (未回収の変更ごと消す判断につながる)
            result["failed"].append({**entry, "step": "status", "error": err.strip()})
            return
        dirty = bool(out.strip())
        if not dirty:
            self._reap(entry, path, locked, "clean", None, dry_run, result)
            return
        last = self._last_activity(self.root, os.path.basename(path.rstrip(os.sep)))
        if last is None or moment - last <= max_age_seconds:
            result["kept"].append({**entry, "reason": "dirty-young"})
            return
        self._reap(entry, path, locked, "max-age", self._preview(path), dry_run, result)

    def _reap(self, entry, path, locked, reason, preview, dry_run, result):
        """remove を撃つ (dry_run なら planned に積むだけ)。失敗は failed に落として続行する。"""
        planned = {**entry, "reason": reason, "locked": locked}
        if preview is not None:
            planned["preview"] = preview
        if dry_run:
            result["planned"].append(planned)
            return
        args = _removal_args(path, locked=locked, force=reason != "clean")
        rc, _out, err = self._git(self.root, args)
        if rc != 0:
            result["failed"].append({**entry, "step": "worktree-remove", "error": err.strip()})
            return
        result["removed_worktrees"].append(planned)

    def _preview(self, path):
        """force reap 直前の diffstat + untracked 一覧 (消える内容の唯一の手がかり)。"""
        rc, out, _err = self._git(path, ["diff", "HEAD", "--stat"])
        diffstat = out.strip() if rc == 0 else "<unavailable>"
        rc, out, _err = self._git(path, ["status", "--porcelain"])
        untracked = (
            [line[3:] for line in out.splitlines() if line.startswith("??")] if rc == 0 else []
        )
        return {"diffstat": diffstat, "untracked": untracked}


# import 時の phase 整合検査は vocabulary 側に集約した (属性表の網羅性と
# `worktree_protected` / `worktree_reclaim` の排他性を `vocabulary._require_import_time_consistency`
# が見る)。本 module に写しを置かない — 検査が 2 箇所に散ると片方だけ古くなる。
