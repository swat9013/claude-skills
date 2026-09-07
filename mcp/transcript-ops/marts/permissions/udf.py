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
import urllib.parse

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

# `Tool(<param>:<value>)` 形の param 名。**コロン前後の空白は無視する**
# (`code.claude.com/docs/en/permissions` の「Match by input parameter」節が
# "Whitespace around the colon is ignored" と定める)。
PARAM_RULE_NAME_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:")

# param rule を書けない category。公式 doc が
# "Deny and ask rules can match a top-level input parameter" / "allow rules continue
# to use each tool's own specifier syntax" と定めるため、**allow entry の
# `Bash(timeout:*)` は常に command prefix**。この 1 条件が、param 名と同名の
# command (`timeout` / `test` 等) を持つ prefix rule の誤判別をほぼ全部落とす。
PARAM_RULE_CATEGORIES = ("deny", "ask")

# param rule に書けない key 名 (公式 doc の primary content field: "You can't match a
# tool's primary content field this way: `command` for Bash and PowerShell,
# `file_path` for Read, Edit, and Write, `path` for Grep and Glob, `notebook_path`
# for NotebookEdit, and `url` for WebFetch")。**本体は rule ごと無視して起動時に
# warning を出す**ので、この形の entry の match 0 は真の観測 = revoke 候補に出るのが
# 正しい。param rule 側へ倒すと、死んだ entry が候補から永久に消える。
#
# 集合は matcher が照合対象として読む key 名と一致する — 新しい一覧を持たないため、
# `test_primary_content_fields_are_the_keys_the_matcher_reads` が ingest 側の写像と
# 突き合わせて陳腐化を検知する。
PRIMARY_CONTENT_FIELDS = frozenset(
    {"command", "file_path", "path", "notebook_path", "url"})

# MCP tool 名の接頭辞と、server 名と tool 名を分ける区切り
# (`mcp__<server>__<tool>`)。**2 つの規則がこの接頭辞に乗る**:
#
# - 被覆 (`entry_tool_covers`): `mcp__foo` は foo server の全 tool を、
#   `mcp__foo__*` は tool 名位置の wildcard として同じ集合を指す
# - skip (`is_skipped_mcp_rule`): 括弧付きなら本体が settings 読み込み時に rule ごと
#   skip する (公式 doc: "Claude Code loads a settings file, it skips any `mcp__` rule
#   that has parentheses")。skip される entry は照合されようがないので、その 0 も真の観測
MCP_TOOL_PREFIX = "mcp__"
MCP_NAME_SEPARATOR = "__"

# tool 名の位置に書ける wildcard の文字 (`fnmatch` が解釈する 3 種すべて)。
#
# **`[` は入れない。** 公式 doc が挙げる形は `mcp__foo__*` / `mcp__foo__get_*` だけで
# bracket の実例が無く、当てない側が保守的だから。**ただし判定から外すだけでは
# 保守的にならない** — `*` と併用された entry は `fnmatchcase` に丸ごと渡るので、
# bracket が「wildcard ではない」と見なされたまま黙って効いてしまう
# (`mcp__foo__[ab]*` が `mcp__foo__alpha` に当たる)。`_tool_name_pattern` が literal へ
# escape して、判定と解釈の両方で bracket を素の文字として扱う。
TOOL_NAME_WILDCARD_CHARS = "*?"

# `fnmatch` の bracket 記法を literal の `[` として渡すための置換。
LITERAL_BRACKET = "[[]"

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


def url_host(url: str) -> str:
    """URL から照合対象の hostname を取り出す (小文字・末尾 `.` 除去)。

    `hostname` は port と userinfo を落とすので、`https://evil.com@github.com:443/x`
    は `github.com` になる — 本体 matcher と同じく**権威を持つ host だけ**を見る。

    取り出せなければ空文字を返す (不正な IPv6 表記 `http://[::1/x` 等で `urlsplit`
    が `ValueError` を投げる)。**この空文字は照合不能の入力そのもの**なので、
    `matcher_input_availability` は生の `target_url` ではなく本関数の結果を数える —
    生の非空だけを数えると、host を取り出せない URL しか無い entry が
    `unmatchable` にならず、照合できていないまま `exact` の 0 件として revoke 候補に
    出る (#873 の欠陥がそのまま再現する)。
    """
    if not url:
        return ""
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return ""
    return (host or "").rstrip(".")


def domain_matches(pattern: str, host: str) -> int:
    """`WebFetch(domain:<pattern>)` が hostname にマッチするか。

    **`host` は `url_host` が正規化済みの前提** (小文字・末尾 `.` 除去)。正規化の
    持ち主を 1 つにするため、本関数は pattern 側だけを同じ形へ揃える。

    Claude Code 公式 doc (`code.claude.com/docs/en/permissions` の WebFetch 節) の
    規則をそのまま写す: 大文字小文字を無視し、rule / hostname 双方の末尾 `.` を
    落とす。裸の `*` は全 domain、先頭 `*.` は任意の深さの subdomain (裸の domain
    自身は含まない)、**それ以外の位置の wildcard はドットを跨がない** (label 内
    だけ)。跨がせると `example.*` が攻撃者の登録しうる `example.evil.com` を
    拾う。
    """
    pattern = pattern.strip().lower().rstrip(".")
    if not host:
        return 0
    if pattern == "*":
        return 1
    host_labels = host.split(".")
    if pattern.startswith("*."):
        # 先頭 `*.` だけが任意の深さを表す。残りの label はここでも label 単位で
        # 照合する — literal な suffix 比較にすると `*.example.*` のように先頭以外
        # にも wildcard を持つ pattern が黙って 1 件もマッチしなくなる
        pattern_labels = pattern[2:].split(".")
        if len(host_labels) <= len(pattern_labels):
            return 0
        host_labels = host_labels[-len(pattern_labels):]
    else:
        pattern_labels = pattern.split(".")
        if len(pattern_labels) != len(host_labels):
            return 0
    return int(all(fnmatch.fnmatchcase(host_label, pattern_label)
                   for pattern_label, host_label
                   in zip(pattern_labels, host_labels)))


def matcher_input_column(match_kind: str, tool: str) -> str | None:
    """entry の照合が読む `scoped_event` の列名。**照合対象を要さないなら `None`**。

    **`entry_matches` の分岐はこの戻り値で決まる** — 照合が何を読むかと「読む列が
    空の event しか無いなら照合不能」の判定を 1 箇所に固定するため。2 箇所に分けて
    書くと、片方だけ tool 種別が増えたときに両者が食い違う。

    `None` は `exact_tool` (tool 名だけで成立するので常に照合可能)。列名を返す場合は
    `matcher_input_availability` が返す件数列と同じ語で、present 側はこの値をそのまま
    key に使う (派生名を組み立てない)。**列名以外の語を返さない**ので、写像が壊れた
    ときは KeyError で落ちる。

    **本関数が拾えるのは「列が空」の照合不能だけ**。`Tool(param:value)` 形の
    param rule (`Bash(run_in_background:true)` 等) は照合対象が列ではなく
    **その param を渡した呼び出しの有無**なので、`param_rule_name` と観測された
    input param 名の突合が別に担う (#888)。
    """
    if match_kind == "exact_tool":
        return None
    if match_kind == "domain":
        return "target_url"
    if tool == "Bash":
        return "command"
    return "target_path"


def is_skipped_mcp_rule(match_kind: str, tool: str) -> int:
    """本体が settings 読み込み時に rule ごと skip する形か (括弧付きの `mcp__…`)。

    公式 doc (`code.claude.com/docs/en/permissions`) が
    "When Claude Code loads a settings file, it skips any `mcp__` rule that has
    parentheses" と定める。**skip された rule は何にも当たらない**ので、照合は常に
    0 で、その 0 は「未使用」ではなく「rule として存在していない」を意味する
    (`unmatchable` の札は付けず、revoke 候補に出るのが正しい)。

    括弧の有無は `match_kind` が持つ — 括弧なしだけが `exact_tool` になる。

    **判定を 1 箇所に固定する** (#916): 以前は `entry_matches` が「MCP 実行は
    `target_path` を持たない」偶然に頼って 0 を返していた。MCP tool が path を入力に
    取れば黙って当たり出すうえ、被覆を server 単位へ広げると当たる母集団も広がる。
    """
    return int(match_kind != "exact_tool" and tool.startswith(MCP_TOOL_PREFIX))


def _has_tool_name_wildcard(name: str) -> bool:
    """tool 名 (またはその一部) に wildcard が含まれるか。

    **判定を 2 箇所へ写さない**ための 1 本 — `entry_tool_covers` (解釈へ入るか) と
    `_mcp_wildcard_is_anchored` (server 部が glob-free か) が同じ集合を読む。
    """
    return any(char in name for char in TOOL_NAME_WILDCARD_CHARS)


def _tool_name_pattern(entry_tool: str) -> str:
    """`fnmatchcase` へ渡す pattern。**bracket を literal へ escape する。**

    `TOOL_NAME_WILDCARD_CHARS` が `[` を wildcard と見なさない以上、fnmatch にも
    見なさせない — escape しないと `mcp__foo__[ab]*` のように `*` を併せ持つ entry で
    bracket だけが黙って効き、「当てない側が保守的」という宣言と挙動が逆になる。
    """
    return entry_tool.replace("[", LITERAL_BRACKET)


def _mcp_wildcard_is_anchored(entry_tool: str) -> bool:
    """allow の tool 名 wildcard が `mcp__<server>__` の後ろに限られているか。

    公式 doc: "Allow rules accept tool-name globs only after a literal
    `mcp__<server>__` prefix. The server segment must be glob-free" / 錨の無い allow
    glob (`*` / `B*` / `mcp__*`) は "skipped with a warning and doesn't auto-approve
    anything"。**skip される形を当ててしまうと、死んだ entry が revoke 候補から
    永久に消える**ので、deny / ask と同じ扱いにはできない。
    """
    server, separator, _ = entry_tool[len(MCP_TOOL_PREFIX):].partition(
        MCP_NAME_SEPARATOR)
    return bool(separator) and not _has_tool_name_wildcard(server)


def entry_tool_covers(category: str, entry_tool: str, event_tool: str) -> int:
    """設定 entry の tool 名が 1 実行の tool 名を被覆するか (**完全一致より広い**)。

    公式 doc (`code.claude.com/docs/en/permissions` の MCP 節) が MCP rule に 3 形を
    定める: `mcp__foo` = foo server の全 tool / `mcp__foo__*` = 同じ集合を tool 名位置の
    wildcard で書いた形 / `mcp__foo__bar` = その tool。**完全一致だけで突合すると前 2 形が
    `mcp__foo__bar` の実行に当たらず、収載済みの entry が「未収載」として出る** (#916)。

    server 単位 entry は `__` 境界を要求する — `mcp__foo` は `mcp__foobar__baz` を
    被覆しない。境界を捨てると本体の matcher より広く当たり、`prefix_matches_command`
    が token 境界で防いでいるのと同じ過剰マッチが tool 名の側で起きる。

    **wildcard は `mcp__` 接頭辞の entry だけで解釈する。** 公式 doc の
    "Tool name wildcards" 節は非 MCP の tool 名 glob (deny の `*` / `B*`) も定めるが、
    実 settings に 1 件も無く category ごとに可否が分かれるため、本 issue の範囲外
    として完全一致のまま残す (当てない側が保守的)。**この限界は A 軸と B 軸へ同じ形で
    効く** — 片側にだけ札を付けて補償すると、A 軸「照合不能・revoke 不可」/ B 軸
    「その実行は全件未収載」という矛盾した像が同時に出る (#916 の 2 巡目で実測)。
    補償するなら両軸そろえて別 issue で行う。

    **tool 名の wildcard は `confidence` を `approx` へ落とさない。** pattern 側の
    wildcard (`Read(**/*.env)`) が `approx` なのは fnmatch が `~` 展開と `**` の
    意味論を本体 matcher どおりに再現しないため。tool 名は path ではなく
    `[A-Za-z0-9_-]` の平坦な識別子なので、`~` も `**` も path 区切りも現れず、
    bracket も `_tool_name_pattern` が literal へ落とすので、残る `*` / `?` の
    fnmatch は本体の照合と揺れる余地が無い。**この違いは
    `revoke_candidate` の `matcher_exact` 条件を通すかどうかを分ける**ので、
    approx へ落とすと使われていない `mcp__foo__*` が二度と revoke 候補に出なくなる。
    """
    if entry_tool == event_tool:
        return 1
    if not entry_tool.startswith(MCP_TOOL_PREFIX):
        return 0
    if _has_tool_name_wildcard(entry_tool):
        if category == "allow" and not _mcp_wildcard_is_anchored(entry_tool):
            return 0
        return int(fnmatch.fnmatchcase(event_tool, _tool_name_pattern(entry_tool)))
    if MCP_NAME_SEPARATOR in entry_tool[len(MCP_TOOL_PREFIX):]:
        # `mcp__foo__bar` は tool を名指した形なので完全一致でしか当たらない
        return 0
    return int(event_tool.startswith(entry_tool + MCP_NAME_SEPARATOR))


def param_rule_name(category: str, tool: str, match_kind: str, pattern: str) -> str:
    """entry が `Tool(<param>:<value>)` 形なら param 名、そうでなければ空文字。

    **構文だけでは command prefix rule と判別が付かない** — `Bash(cat:*)` と
    `Bash(run_in_background:*)` は同形で、実設定の 60 件超が前者。本関数が返すのは
    「param 名でありうる文字列」までで、実際に param rule かどうかは呼び出し側が
    **観測された input param 名**と突き合わせて決める (#888)。**tool ごとの param 名
    一覧を持たない** — 陳腐化する一覧は無いより悪い (`rules.py` の禁止事項)。

    category で先に絞るのは公式 doc
    (`code.claude.com/docs/en/permissions` の「Match by input parameter」節) の
    "Deny and ask rules can match a top-level input parameter" による。allow rule は
    param rule になりえないので、`Bash(timeout:*)` のような**param 名と同名の
    command を持つ prefix rule**が誤判別されるのは deny / ask に書いた場合だけになる。

    `domain` (`WebFetch(domain:…)`) は `domain` が WebFetch の入力 param ではなく
    専用の specifier 文法なので除く。`exact_tool` は括弧を持たない。

    **本体が rule ごと無視する形も除く** — primary content field を名指した entry
    (`Bash(command:*)` / `Read(file_path:*)`) と、括弧付きの `mcp__…` entry。どちらも
    照合されようがないので match 0 は真の観測で、revoke 候補に出るのが正しい。
    """
    if category not in PARAM_RULE_CATEGORIES:
        return ""
    if match_kind in ("exact_tool", "domain"):
        return ""
    if is_skipped_mcp_rule(match_kind, tool):
        return ""
    match = PARAM_RULE_NAME_RE.match(pattern)
    if match is None or match.group(1) in PRIMARY_CONTENT_FIELDS:
        return ""
    return match.group(1)


def entry_matches(match_kind: str, pattern: str, tool: str, command: str,
                  target_path: str, target_url: str) -> int:
    """設定 entry が 1 実行にマッチするか (**conservative**: 誤検知より取りこぼし)。

    tool 名の被覆は SQL 側の join 条件 (`covered_tool`) が担う。本関数は pattern の
    解釈と、**本体が rule ごと skip する形の除外**だけを持つ。

    - 括弧付きの `mcp__…`: 常に 0 (`is_skipped_mcp_rule`)
    - `exact_tool` (括弧なし): 常に真 (tool 全体を許可 / 禁止する形)
    - `domain` (`WebFetch(domain:…)`): URL の hostname で照合する
    - Bash: `exact_command` は完全一致、`prefix` は token 境界つき前方一致、
      `glob` は fnmatch
    - path 系 tool: 候補 path を glob で照合

    **照合対象を持たない実行は一律 0 を返し、その 0 は「未使用」を意味しない。**
    実行単位で区別を付けようとすると、同じ状態を実行単位では「マッチ」・entry 単位
    では「照合不能」と逆に解釈することになる (`match_count > 0` かつ `unmatchable`
    という読めない行が出る)。区別は entry 単位の `matcher_confidence` が持つ。
    """
    if is_skipped_mcp_rule(match_kind, tool):
        return 0
    column = matcher_input_column(match_kind, tool)
    if column is None:
        return 1
    if column == "target_url":
        return domain_matches(pattern, url_host(target_url))
    if column == "command":
        if match_kind == "exact_command":
            return int(command.strip() == pattern.strip())
        if match_kind == "prefix":
            return int(prefix_matches_command(pattern[:-2], command))
        if match_kind == "glob":
            return int(fnmatch.fnmatch(command, pattern))
        return 0
    if not target_path:
        return 0
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
#
# `matcher_input_column` も載せない — SQL が名前で呼ぶことは無く、`entry_matches`
# の中と present 側から Python として直接呼ぶため。**行ごとの実行回数は減らない**
# (`entry_matches` の冒頭で呼ぶので join 述語と同じ回数走る)。
#
# `param_rule_name` も同じ (present 側から entry ごとに 1 回)。行ごとに呼ぶ形にする
# 必要が無いので SQL へは出さない。
#
# `entry_tool_covers` / `is_skipped_mcp_rule` も載せない。**特に前者を join 述語に
# 置いてはいけない** — tool 名が等値でなくなると
# `scoped_event_tool_idx` が効かず総当たりになる。present 側が (entry, tool) の対応表
# `covered_tool` を先に作り、join は等値のまま残す (#916)。実測の桁は
# `present.covered_tool_rows` の docstring が正本 (数字を写すと片方だけ古くなる)。
REGISTERED = (
    ("command_head", 1, command_head),
    ("is_compound_command", 1, is_compound_command),
    ("entry_matches", 6, entry_matches),
    ("url_host", 1, url_host),
    ("looks_like_permission_denial", 1, looks_like_permission_denial),
    ("looks_like_automode_denial", 1, looks_like_automode_denial),
    ("automode_reason_label", 1, automode_reason_label),
    ("cwd_in_scope", 2, cwd_in_scope),
)


def register(conn: sqlite3.Connection) -> None:
    """接続に UDF を登録する。すべて deterministic (同じ入力に同じ答え)。"""
    for name, argument_count, function in REGISTERED:
        conn.create_function(name, argument_count, function, deterministic=True)
