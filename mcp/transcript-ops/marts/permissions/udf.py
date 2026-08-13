"""permissions mart の純関数 (ADR 0031 の UDF 規律)。SQL からも Python からも呼ぶ。

**I/O・clock・store アクセスを持たない。** 入力は値だけ、出力は値だけ。層で
テスト容易性を保証し、format 変更の影響を ingest 側へ隔離するため
(cclens の core 層 "No I/O, no clock, no SQL" と同型)。

「SQL は関係代数だけ、意味は UDF」の配置規則により、文字列の解釈 (command の
先頭 token / matcher / deny 文言) はすべてここに来る。
"""

from __future__ import annotations

import fnmatch
import re
import sqlite3

# Bash command の集約キーに使う先頭 token 数。
COMMAND_HEAD_MAX_TOKENS = 2

# prefix マッチで「token が続いていない」と見なす境界文字。空白のほか shell の
# 区切り (`;` `&` `|` `<` `>` `)`) を含める。素の `str.startswith` だと
# `git push --force:*` が `git push --force-with-lease ...` に、`comm:*` が
# `command rm ...` にマッチする (2026-07-28 の棚卸しで --force-with-lease 4 件が
# deny entry の match に混入した実測がある)。
PREFIX_BOUNDARY_CHARS = frozenset(";&|<>)")

# `Permission to use <tool> [with command <cmd>] has been denied.`
# **user-reject 文言より先に見る** — 優先順が割れると同じ record に 2 つの答えが出る。
PERMISSION_DENIAL_RE = re.compile(
    r"Permission to use\s+[A-Za-z_][A-Za-z0-9_-]*"
    r"(?:\s+with\s+command\s+.+?)?\s+has been denied\.",
    re.DOTALL,
)

# 自動モード分類器 deny の本文と、Reason 先頭ラベル `[Xxx Yyy]`。
AUTOMODE_DENIAL_TEXT = "denied by the claude code auto mode"
AUTOMODE_REASON_LABEL_RE = re.compile(r"Reason:\s*\[([^\]]+)\]")

# hook command 中の script file token (照合キーの抽出源)。
HOOK_SCRIPT_TOKEN_RE = re.compile(
    r"[\w./~${}@-]*[\w-]+\.(?:py|sh|bash|zsh|js|cjs|mjs|ts|rb|pl)\b"
)

_ASSIGNMENT_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# 1 行に複数コマンドを並べる shell 連結演算子。`command_head` が切る位置と同じ集合を
# 使う (同じ「1 行 = 複数コマンド」の定義を 2 通り持たない)。
COMMAND_SEPARATORS = ("&&", "||", ";", "|")


def command_head(command: str, max_tokens: int = COMMAND_HEAD_MAX_TOKENS) -> str:
    """Bash command の集約キー (先頭 token 列)。

    `git diff origin/main` → `git diff` / `ls` → `ls`。連結演算子で切り、代入
    prefix (`VAR=x cmd`) は落とす。redirection や変数展開が混じる先頭 2 token は
    1 token へ degrade する (key が実行ごとに割れるのを避ける)。
    """
    if not command:
        return ""
    for separator in COMMAND_SEPARATORS:
        if separator in command:
            command = command.split(separator, 1)[0]
    tokens = command.strip().split()
    while tokens and _ASSIGNMENT_PREFIX_RE.match(tokens[0]):
        tokens.pop(0)
    if not tokens:
        return ""
    head = " ".join(tokens[:max_tokens])
    if any(char in head for char in ("<", ">", "$", "`")):
        return tokens[0]
    return head


def prefix_matches_command(prefix: str, command: str) -> bool:
    """`Bash(xxx:*)` の xxx が command の先頭 **token 列**として現れるか。

    Claude Code 本体の matcher は token 境界を見ており、prefix が単語の途中で
    切れるケースはマッチしない。prefix 直後が行末・空白・shell 区切りであることを
    追加条件にして過剰マッチを防ぐ。
    """
    if not prefix:
        return True
    if not command.startswith(prefix):
        return False
    rest = command[len(prefix):]
    if not rest:
        return True
    return rest[0].isspace() or rest[0] in PREFIX_BOUNDARY_CHARS


def entry_matches(match_kind: str, pattern: str, tool: str, command: str,
                  target_path: str) -> int:
    """設定 entry が 1 実行にマッチするか (**conservative**: 誤検知より取りこぼし)。

    tool 名の一致は SQL 側の join 条件が担う。本関数は pattern の解釈だけを持つ。

    - `exact_tool` (括弧なし): 常に真 (tool 全体を許可 / 禁止する形)
    - Bash: `exact_command` は完全一致、`prefix` は token 境界つき前方一致、
      `glob` は fnmatch
    - path 系 tool: 候補 path を glob で照合。**候補が無ければ `prefix` のみ真**
      (tool 名一致で拾う保守側)
    """
    if match_kind == "exact_tool":
        return 1
    if tool == "Bash":
        if match_kind == "exact_command":
            return int(command.strip() == pattern.strip())
        if match_kind == "prefix":
            return int(prefix_matches_command(pattern[:-2], command))
        if match_kind == "glob":
            return int(fnmatch.fnmatch(command, pattern))
        return 0
    if not target_path:
        return int(match_kind == "prefix")
    if match_kind in ("glob", "prefix", "exact_command"):
        return int(fnmatch.fnmatch(target_path, pattern))
    return 0


def is_compound_command(command: str) -> int:
    """1 行に複数コマンドが連結されているか (**構文の検査であって危険度の判定ではない**)。

    1 行に allow 対象と deny 対象が混在すると call 全体が deny され、その deny は
    行内でマッチする**全 entry へ計上される**。allow entry の高 deny 比率はまず
    これを疑う必要があり、その疑いを機械側で立てるのが本関数
    ([ADR 0032](../../../../docs/adr/0032-policy-free-refinement-deterministic-rules.md))。

    引用符の中の演算子も連結と見なす近似 (`echo "a && b"` は誤検知する)。厳密な
    shell parse を持たないのは、**過検知が「入力コマンドを読め」という
    open predicate に落ちるだけで、判定を機械が確定させないから**。
    """
    return int(any(separator in (command or "")
                   for separator in COMMAND_SEPARATORS))


def looks_like_permission_denial(text: str) -> int:
    """permission-rule deny の文言か (`toolDenialKind` 欠落時の fallback)。"""
    return int(bool(PERMISSION_DENIAL_RE.search(text or "")))


def looks_like_automode_denial(text: str) -> int:
    """自動モード分類器 deny の文言か (同上)。"""
    return int(AUTOMODE_DENIAL_TEXT in (text or "").lower())


def automode_reason_label(text: str) -> str | None:
    """自動モード deny の Reason 先頭ラベル。無ければ None。"""
    match = AUTOMODE_REASON_LABEL_RE.search(text or "")
    return match.group(1).strip() if match else None


def hook_command_key(command: str) -> str:
    """hook command の照合キー (**最後に**現れる script file の basename)。

    設定側は `"${CLAUDE_PLUGIN_ROOT}"/hooks/harness/guard-git.sh` のように変数を
    含み、観測側は展開済み絶対 path で現れるため、文字列一致では紐づかない。

    先頭ではなく末尾を採るのは、`export PATH=...; <runner>.js <entry>.js` のような
    長い shell 一行 hook が実在し、先頭側は共通 runner なので**別 hook が同じ key に
    潰れて fire を二重計上する**ため。script 拡張子を持つ token だけを候補にするのは、
    素の token 分割では `:true}` のような shell 断片を掴むため。
    """
    tokens = HOOK_SCRIPT_TOKEN_RE.findall(command or "")
    if tokens:
        return tokens[-1].rsplit("/", 1)[-1]
    for token in (command or "").replace('"', " ").replace("'", " ").split():
        if "=" in token or token.startswith("-"):
            continue
        return token.rsplit("/", 1)[-1]
    return ""


def cwd_in_scope(cwd: str, roots: str) -> int:
    """実行の cwd が観測対象 root (改行区切り) の配下にあるか。

    root を複数受けるのは、**symlink の解決前と解決後の両表記で比較する**ため。
    片側だけで比較すると、symlink 経由で開いた repo の project section が黙って
    0 件になる (移行前の欠陥)。
    """
    if not cwd:
        return 0
    for root in roots.split("\n"):
        root = root.rstrip("/")
        if not root:
            continue
        if cwd == root or cwd.startswith(root + "/"):
            return 1
    return 0


# SQL 名 → (関数, 引数) の登録表。**query.sql が呼ぶ名前の単一ソース**。
#
# `hook_command_key` はここに載せない — 呼び先が settings 側 (設定の分母) と
# present 側 (fire の集約) で、どちらも「command ごとに 1 回」で足りる。行ごとに
# 呼ぶ形にすると実測 4 万 fire に対し 5.9 秒かかる。
REGISTERED = (
    ("command_head", 1, command_head),
    ("is_compound_command", 1, is_compound_command),
    ("entry_matches", 5, entry_matches),
    ("looks_like_permission_denial", 1, looks_like_permission_denial),
    ("looks_like_automode_denial", 1, looks_like_automode_denial),
    ("automode_reason_label", 1, automode_reason_label),
    ("cwd_in_scope", 2, cwd_in_scope),
)


def register(conn: sqlite3.Connection) -> None:
    """接続に UDF を登録する。すべて deterministic (同じ入力に同じ答え)。"""
    for name, argument_count, function in REGISTERED:
        conn.create_function(name, argument_count, function, deterministic=True)
