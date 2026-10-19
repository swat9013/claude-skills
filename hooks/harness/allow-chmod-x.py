#!/usr/bin/env python3
"""allow-chmod-x.py — PreToolUse / Bash: repo の script 置き場への `chmod +x` を allow する許可専用 hook。

Why: `chmod +x` は permission の承認を求める。repo の `scripts/` / `.githooks` / `hooks` と
    本 plugin 配下への +x は script を足すたびに起きるので、承認の往復を消す。

    path prefix で 2 分岐する:
      - allow prefix (本 plugin 配下 / repo scripts / .githooks / hooks) → auto allow
        (改行で続けた 2 行目と、`PLAIN_COMMAND_CHARS` の外の文字 — shell の構文になりうる文字 — を
        持つ command は除く — #1698)
      - それ以外 → passthrough (既存 ask フローに委ねる)

    自動発火 path (`.git/hooks` / LaunchAgents / `~/.claude/hooks` / `/etc/cron.*` 等) は、
    allow prefix 配下にあっても allow しない (本 plugin の checkout 内の `.git/hooks` 等)。
    deny はしない — 数値 mode (`chmod 755`)・`install -m 755`・`/bin/chmod` で同じ +x が通るので、
    deny しても止まらない (ADR 0077 / ADR 0108)。

invariant:
    1. allow prefix の外 (hook の設置先 root の外 / repo の scripts・.githooks・hooks 以外 /
       cwd が git repo でないときの相対 path) は allow しない
    2. 自動発火 path は allow prefix 配下でも allow しない (allow prefix の path と混ぜても)
    3. 改行で続けた 2 行目・`PLAIN_COMMAND_CHARS` の外の文字・chmod 以外の command・一部だけ
       allow prefix に入る複数 path は allow しない
    4. recursive (`-R` / `--recursive`) は allow しない
    5. payload を読めない / tokenize できないときは allow しない
    6. 1 は symlink を辿った実体の path で、2 は字面・実体と、辿る途中で通る symlink の各 hop の
       path で判定する (symlink 越しに外へ +x を付けない。辿りきれない path は allow しない)。
       実体が自動発火の場所の別名 (場所そのものの symlink / entry の symlink・hardlink) を通じて
       自動発火の対象になるときも allow しない (repo の hooks dir を引けないときも allow しない)。
       自動発火 path の segment は大文字小文字を区別せずに照らす

fail-posture: allow-hook なので fail-safe 固定 (ADR 0046)。payload を読めない / コマンドを
    tokenize できないときは allow せずに passthrough する。

Registration: hooks/hooks.json PreToolUse / Bash matcher / if: Bash(chmod:*)
"""
from __future__ import annotations

import fnmatch
import glob
import os
import shlex
import subprocess
import sys
from typing import NoReturn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _prelude import (  # noqa: E402
    allow,
    continues_on_another_line,
    decide,
    passthrough,
    read_payload_or_pass,
    read_stdin_text,
    tool_input_text,
)

# allow してよい command に現れる文字。区切り・置換・リダイレクト・subshell・glob・brace 展開
# (`{a,../../b}` は shell が allow prefix の外の path へ展開する) を表す文字はすべて外にある。
# 拒否する文字を列挙すると漏れがそのまま承認なしの経路になるので、許す文字を列挙する
PLAIN_COMMAND_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./+=,:@%~- \t'\""
)


# --- chmod command parse ----------------------------------------------------


def parse_chmod(tokens: list[str]) -> tuple[str, list[str], bool] | None:
    """(mode, paths, recursive) を返す。chmod 呼び出しでなければ None。"""
    i = 0
    # optional `sudo` (with its flags; `-u USER` / `-g GROUP` は次トークンを消費)
    if i < len(tokens) and tokens[i] == "sudo":
        i += 1
        while i < len(tokens) and tokens[i].startswith("-"):
            if tokens[i] in ("-u", "-g", "-p", "-C", "-D", "-h", "-r", "-t") and i + 1 < len(tokens):
                i += 2
            else:
                i += 1
    if i >= len(tokens):
        return None
    if os.path.basename(tokens[i]) != "chmod":
        return None
    i += 1
    recursive = False
    # flags (chmod は - or + で始まる引数を持つが、+ / = で始まるものは symbolic mode)
    while i < len(tokens) and tokens[i].startswith("-") and tokens[i] != "--":
        tok = tokens[i]
        if tok == "--recursive" or (len(tok) >= 2 and tok[1] != "-" and "R" in tok[1:]):
            recursive = True
        i += 1
    if i < len(tokens) and tokens[i] == "--":
        i += 1
    if i >= len(tokens):
        return None
    mode = tokens[i]
    i += 1
    # mode と path の間にも `--` が入りうる (POSIX 一般的には flag セクションの
    # 直後だが、実装により mode 後の `--` も許容)
    if i < len(tokens) and tokens[i] == "--":
        i += 1
    paths = tokens[i:]
    if not paths:
        return None
    return mode, paths, recursive


def mode_adds_execute(mode: str) -> bool:
    """symbolic mode に `+x` 系の execute bit 追加が含まれるか。

    対応: `+x` / `a+x` / `u+x` / `ug+x` / `+rwx` / `+wx` / `u+xw` 等の任意順、
    複数 clause (`,` 区切り) のいずれかに含まれば True。数値モード (755 等) は
    範囲外 (issue #279 spec に合わせる)。
    """
    for clause in mode.split(","):
        # 各 clause 内の `+` から次の非 perm 文字 (or 末尾) までに `x` が含まれるか
        idx = 0
        while True:
            plus = clause.find("+", idx)
            if plus < 0:
                break
            j = plus + 1
            while j < len(clause) and clause[j] in "rwxXstugo":
                j += 1
            if "x" in clause[plus + 1: j]:
                return True
            idx = j
    return False


# --- path classification ----------------------------------------------------


def resolve_path(raw: str, cwd: str) -> str:
    """~ 展開 + cwd 基準の絶対化 + realpath。存在は問わない (chmod は新規 file 相手にも打てる)。

    symlink を辿る: chmod は symlink の先に +x を付けるので、`scripts/l` → `../.git/hooks/x` や
    `scripts/l/../x` の字面で判定すると allow prefix の外・自動発火 path へ +x が通る (#1983)。
    """
    p = _absolute(raw, cwd)
    # normpath を先に掛けない: `l/..` を字面で畳むと symlink `l` の先の親を辿れない
    return os.path.realpath(p) if os.path.isabs(p) else p


def literal_path(raw: str, cwd: str) -> str:
    """~ 展開 + cwd 基準の絶対化 + normpath (symlink は辿らない)。"""
    return os.path.normpath(_absolute(raw, cwd))


def _absolute(raw: str, cwd: str) -> str:
    """~ 展開 + cwd 基準の絶対化。cwd が無い相対 path は相対のまま返し、どの allow prefix にも
    当てない (hook process の cwd で解決しない)。"""
    # zsh は `~NAME` (named directory / `~-` / `~1`) と `=cmd` (PATH 上の実体) を展開するので、
    # 字面から実体を決められない。相対のまま返して neutral に落とす
    if raw.startswith("=") or (raw.startswith("~") and not (raw == "~" or raw.startswith("~/"))):
        return raw
    p = os.path.expanduser(raw)
    if not os.path.isabs(p) and cwd:
        p = os.path.join(cwd, p)
    return p


def repo_root_of(cwd: str) -> str | None:
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def contains_segment(path: str, segments: tuple[str, ...]) -> bool:
    """path に `.git/hooks/` のような連続 segment が含まれるか。

    `.git/hooks/**` 判定に使う (worktree 検出 + submodule / 別 repo でも安全側)。
    """
    # macOS の既定 APFS は大文字小文字を区別しないので、`.git/Hooks` も同じ dir を指す
    parts = path.casefold().split(os.sep)
    n = len(segments)
    for i in range(len(parts) - n + 1):
        if tuple(parts[i:i + n]) == segments:
            return True
    return False


def plugin_root() -> str:
    """本 hook を含む plugin の root (= `${CLAUDE_PLUGIN_ROOT}` の実体)。

    hooks.json は `"${CLAUDE_PLUGIN_ROOT}"/hooks/harness/<file>` で起動するため、
    自ファイルの 3 階層上が plugin root になる。install 形態 (skills ディレクトリ
    プラグインの symlink / marketplace install / repo 直) を問わず一意に解決される。

    chmod 対象 path 側を realpath で解決する (resolve_path) ので、こちらも realpath で揃える。
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))


def _is_under_casefold(path: str, root: str) -> bool:
    return is_under(path.casefold(), root.casefold())


def is_auto_trigger(path: str, home: str) -> bool:
    """session の外で自動実行される path (+x で次の commit / システム起動 / hook 発火で走る) か。

    macOS の既定 APFS は大文字小文字を区別しないので、全 path を casefold して照らす。
    """
    folded = path.casefold()
    return (
        contains_segment(path, (".git", "hooks"))
        or _is_under_casefold(path, os.path.join(home, "Library", "LaunchAgents"))
        or _is_under_casefold(path, os.path.join(home, "Library", "LaunchDaemons"))
        or _is_under_casefold(path, os.path.join(home, ".claude", "hooks"))
        or fnmatch.fnmatchcase(folded, "/etc/cron.*")
        or fnmatch.fnmatchcase(folded, "/etc/cron.*/*")
        or folded == os.path.join(home, ".crontab").casefold()
    )


MAX_SYMLINK_HOPS = 40


def symlink_hops(path: str) -> list[str] | None:
    """絶対 path を先頭から 1 component ずつ辿り、通った symlink の path を順に返す。

    chmod は symlink の連鎖を最後まで辿るので、途中の hop (`scripts/l` → `.git/hooks/pre-commit`
    → `scripts/pre-commit` の `.git/hooks/pre-commit`) も +x の付く inode を指す (#2000)。
    hop の数が上限を超えたら (循環を含む) None。相対 path は辿らずに空で返す。
    """
    if not os.path.isabs(path):
        return []
    hops: list[str] = []
    resolved = os.sep
    pending = [c for c in path.split(os.sep) if c]
    while pending:
        comp = pending.pop(0)
        if comp == ".":
            continue
        if comp == "..":
            resolved = os.path.dirname(resolved)
            continue
        candidate = os.path.join(resolved, comp)
        if not os.path.islink(candidate):
            resolved = candidate
            continue
        hops.append(candidate)
        if len(hops) > MAX_SYMLINK_HOPS:
            return None
        try:
            target = os.readlink(candidate)
        except OSError:
            return None
        if os.path.isabs(target):
            resolved = os.sep
        pending = [c for c in target.split(os.sep) if c] + pending
    return hops


def git_hooks_dir(cwd: str) -> str | None:
    """cwd の repo が実際に使う hooks dir (worktree / core.hooksPath を反映した絶対 path)。"""
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    # git < 2.31 は `--path-format` を知らずに字面のまま出力する。絶対 path 1 行でなければ None
    out = proc.stdout.strip()
    return out if out and "\n" not in out and os.path.isabs(out) else None


def auto_trigger_locations(home: str) -> list[str]:
    """session の外で自動実行される dir / file のうち、git に依らないもの (別名の照合に使う)。"""
    return [
        os.path.join(home, "Library", "LaunchAgents"),
        os.path.join(home, "Library", "LaunchDaemons"),
        os.path.join(home, ".claude", "hooks"),
        os.path.join(home, ".crontab"),
        *sorted(glob.glob("/etc/cron.*")),
    ]


def aliases_auto_trigger(resolved: str, locations: list[str]) -> bool:
    """実体 `resolved` が、自動発火の場所 (dir / file) の別名を通じて自動発火の対象になるか。

    自動発火 dir 自体が allow prefix への symlink (`~/.claude/hooks` → `<repo>/hooks`) だったり、
    `.git/hooks/pre-commit` → `../../scripts/pre-commit` のように allow prefix の file を指す entry
    (symlink / hardlink / dir への symlink) があったりすると、target 側の字面・hop には自動発火
    path が現れない (#2000)。entry 自身が target のとき (`chmod +x .githooks/x` で hooksPath が
    `.githooks`) は別名でないので照らさない — 直の path は字面の判定と allow prefix が決める。
    entry は配下の dir の中まで辿る。
    """
    folded = resolved.casefold()
    for loc in locations:
        if not os.path.lexists(loc):
            continue
        real_loc = os.path.realpath(loc)
        if real_loc != os.path.normpath(loc) and is_under(folded, real_loc.casefold()):
            return True  # 場所そのものが別の場所への symlink
        if not os.path.isdir(loc):
            if real_loc.casefold() == folded or _same_file(loc, resolved):
                return True
            continue
        entries: list[str] = []
        unreadable: list[OSError] = []
        # 配下の dir も辿る (`~/.claude/hooks/real/x.sh` → `<repo>/hooks/x.sh`)。symlink の dir の中へは
        # 入らず、その dir 自体を entry として実体の配下を照らす
        for root, dirs, files in os.walk(loc, onerror=unreadable.append, followlinks=False):
            entries.extend(os.path.join(root, name) for name in dirs + files)
        if unreadable:
            return True  # 読めない自動発火 dir は照合できないので allow しない
        for entry in entries:
            if os.path.normpath(entry).casefold() == folded:
                continue  # entry 自身が target
            # symlink の entry は指す先 (dir なら配下) を、それ以外は同じ inode (hardlink) を照らす
            if os.path.islink(entry) and is_under(folded, os.path.realpath(entry).casefold()):
                return True
            if _same_file(entry, resolved):
                return True
    return False


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def classify(path: str, repo_root: str | None, home: str, plugin: str) -> str:
    """path を "allow" / "neutral" に分類。自動発火 path は allow prefix 配下でも neutral にする。"""
    if is_auto_trigger(path, home):
        return "neutral"

    # --- allow prefixes ---
    if is_under(path, plugin):
        return "allow"
    if repo_root:
        if is_under(path, os.path.join(repo_root, "scripts")):
            return "allow"
        if is_under(path, os.path.join(repo_root, ".githooks")):
            return "allow"
        if is_under(path, os.path.join(repo_root, "hooks")):
            return "allow"

    return "neutral"


# --- main -------------------------------------------------------------------


def judge(stdin_text: str) -> NoReturn:
    payload = read_payload_or_pass(stdin_text)

    command = tool_input_text(payload, "command")
    if not command:
        passthrough()

    try:
        tokens = shlex.split(command)
    except ValueError:
        # unbalanced quotes 等でコマンドを tokenize できない。allow-hook は確証が無ければ
        # allow しない (ADR 0046 の fail-safe)
        passthrough()
    if not tokens:
        passthrough()

    parsed = parse_chmod(tokens)
    if parsed is None:
        passthrough()
    mode, paths, recursive = parsed

    if not mode_adds_execute(mode):
        passthrough()

    # 安い構文検査を先に済ませる (以降の path 解決は file system を辿り、git を起動する)
    # `-R` は allow prefix でも auto-allow しない (再帰配下の symlink や submodule
    # 越しに想定外の file まで +x される可能性を排除)
    if recursive:
        passthrough()

    # 改行の後の行は別のコマンドとして走るので auto allow しない (#1698)。shlex は改行を語の
    # 区切りとしか見ず、2 行目の語 (+x した script の起動など) が chmod の path に混ざる
    if continues_on_another_line(command):
        passthrough()

    # `;` `&&` `|` の後ろ・置換・リダイレクトも、shlex は path の語として読む。cwd が allow prefix
    # 配下だと後続のコマンドの語まで allow prefix に解決され、コマンド全体が承認なしで通るので
    # (`chmod +x a.sh; <任意のコマンド>`)、shell の構文になりうる文字を含む command は allow しない
    if any(char not in PLAIN_COMMAND_CHARS for char in command.rstrip("\n")):
        passthrough()

    cwd = str(payload.get("cwd") or "")
    repo_root = repo_root_of(cwd)
    if repo_root:
        repo_root = os.path.realpath(repo_root)
    home = os.path.realpath(os.path.expanduser("~"))

    plugin = plugin_root()
    home_literal = os.path.expanduser("~")
    trigger_locations = auto_trigger_locations(home_literal)
    # allow prefix を持つ repo (cwd の repo と本 plugin の repo) の hooks dir も自動発火の場所。
    # 引けなければ照合できないので allow しない (fail-safe。ADR 0046)
    for repo in {r for r in (repo_root, repo_root_of(plugin)) if r}:
        hooks_dir = git_hooks_dir(repo)
        if hooks_dir is None:
            passthrough()
        trigger_locations.append(hooks_dir)

    # 自動発火の判定は字面 (literal_path)・辿る途中の各 symlink hop・実体の 3 つに当てる。実体は、
    # 自動発火の場所の別名 (場所そのものの symlink / entry の symlink・hardlink) を通じて自動発火の
    # 対象になれば allow しない (`.git/hooks/pre-commit` → `../../scripts/pre-commit` での
    # `chmod +x scripts/pre-commit`。#2000)
    def verdict(p: str) -> str:
        hops = symlink_hops(_absolute(p, cwd))
        if hops is None:
            return "neutral"
        if is_auto_trigger(literal_path(p, cwd), home_literal) or any(
            is_auto_trigger(hop, home_literal) or is_auto_trigger(hop, home) for hop in hops
        ):
            return "neutral"
        resolved = resolve_path(p, cwd)
        if os.path.isabs(resolved) and aliases_auto_trigger(resolved, trigger_locations):
            return "neutral"
        return classify(resolved, repo_root, home, plugin)

    # 全 path が allow prefix にマッチしたときのみ auto allow
    if all(verdict(p) == "allow" for p in paths):
        allow("chmod +x の対象が全て allow prefix 配下")

    passthrough()


def main(stdin_text: str) -> tuple[int, str]:
    """hook の純粋な入口。(exit code, stdout 本文) を返す。"""
    return decide(judge, stdin_text)


if __name__ == "__main__":
    exit_code, stdout_text = main(read_stdin_text())
    sys.stdout.write(stdout_text)
    sys.exit(exit_code)
