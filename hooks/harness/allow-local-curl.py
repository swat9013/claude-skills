#!/usr/bin/env python3
"""allow-local-curl.py — loopback 宛 curl のみを allow する許可専用 PreToolUse hook。

curl は settings の ask 対象 (外部ホストへのデータ持ち出し / 任意取得を確認するため)。
本 hook は以下を全て満たすときだけ permissionDecision:"allow" を返す:

  - コマンド中の全 curl サブコマンドの接続先 (URL operand / --url / proxy) が loopback
    (localhost / *.localhost / 127.0.0.0/8 / ::1 / 0.0.0.0 / ::)
  - curl 以外のサブコマンドは既知の安全 read-only コマンドのみ (compound 許容)
  - curl の出力ファイル (-o 等) は /dev/null / stdout / tmp ルート配下のみ
  - write-out (-w) の書式が file へ書かない (`%output{...}` を含まず、`@` で始まらない)
  - long flag はすべて既知 (値を取るか取らないかを確かめた flag。`LONG_BOOLEAN` 等) で、
    知らない long flag (`--expand-*` / 省略形 / 未分類の値つき flag) を含まない
  - URL operand の scheme は http / https か無し (file:// 等で local の file を読み書きしない)
  - short flag もすべて既知 (`SHORT_BOOLEAN` 等)
  - curlrc が存在するなら第 1 引数が `-q` / `--disable` (curlrc の設定は字面に現れない)
  - 接続先を再マップ・外部注入しうる flag (--resolve / --connect-to / -K / --location / -L /
    --proto-default / --next) を含まない
  - shell リダイレクト / subshell / background を含まない
  - 改行で続けた 2 行目を持たない (`_prelude.continues_on_another_line`)

invariant: 上の条件のどれか 1 つでも確証できない command は allow しない (各条件が 1 項目。
loopback に見せかけた host — userinfo・subdomain・suffix の似せ綴り — も loopback と数えない)。

fail-safe: 静的に安全と確証できないケースは一切 allow せず passthrough() で痕跡を出して
素通しし (ADR 0043)、settings の ask に委ねる。guard-* の deny は本 hook 追加後も従来どおり効く
(deny > allow のため compound 内の危険操作は別 hook がブロックする)。

allow-tmp-delete.py と同じ「確証なき場合は素通し」方針。shell tokenize と URL 解析の
正確性のため sh ではなく python3 (shlex / urllib / ipaddress) で実装する。
"""
from __future__ import annotations

import ipaddress
import os
import shlex
import sys
from typing import NoReturn
from urllib.parse import urlsplit

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

# --- サブコマンド境界 / その他 punctuation ---
SPLIT_OPS = {";", "&&", "||", "|"}
PUNCT = ";()<>&|"

# --- compound 内に混在してよい安全 head (curl 以外、read-only 相当) ---
SAFE_HEADS = {
    "curl", "sleep", "cat", "jq", "echo", "printf", "true", "false", ":",
    "head", "tail", "wc", "grep", "egrep", "fgrep", "sort", "uniq", "cut",
    "date", "basename", "dirname", "test", "[",
}

# --- curl flag 分類 ---
# 存在したら bail (接続先再マップ / 設定ファイル注入で検査を回避しうる)
SHORT_BAIL = set("KL:")
# --location は loopback の応答が返す redirect で外部 host へ接続し直し、body や header を送る。
# --proto-default は scheme の無い operand を file 等に読ませる。--next は option を初期化する
LONG_BAIL = {"resolve", "connect-to", "config", "location", "location-trusted",
             "proto-default", "next"}
# 存在したら bail (URL 由来名で CWD へ書き込む)
SHORT_BAILOUT = set("OJ")
LONG_BAILOUT = {"remote-name", "remote-name-all", "remote-header-name"}
# 値を接続先 host として loopback 検査する
SHORT_HOST = set("x")
LONG_HOST = {"proxy", "preproxy", "socks4", "socks4a", "socks5",
             "socks5-hostname", "proxy1.0"}
# 値を URL operand として loopback 検査する
LONG_URL = {"url"}
# 値を書き込み先 path として検査する
SHORT_PATHOUT = set("oDc")
LONG_PATHOUT = {"output", "output-dir", "dump-header", "cookie-jar", "trace",
                "trace-ascii", "etag-save", "stderr", "libcurl", "hsts", "alt-svc"}
# 値を消費するが接続先でない (無視する) short flag
SHORT_OTHER_VALUE = set("AbCdeEFHmPQrtTuUXyYz")
# 値を write-out の書式として検査する (`%output{file}` で file へ書ける。curl 8.3.0+)
SHORT_WRITEOUT = set("w")
# 値を取らない (boolean) と確かめた short flag。ここに無い文字は allow しない (LONG_BOOLEAN と同じ理由)
SHORT_BOOLEAN = set("0123461aBfgGIijkMNpqRsSvVZ#")
LONG_WRITEOUT = {"write-out"}
# 値を消費するが接続先でない (無視する) long flag
LONG_OTHER_VALUE = {
    "request", "header", "user", "form", "form-string",
    "referer", "user-agent", "cookie", "data", "data-raw", "data-binary",
    "data-ascii", "data-urlencode", "json", "max-time", "connect-timeout",
    "retry", "retry-delay", "retry-max-time", "range", "cert", "key",
    "cacert", "capath", "cert-type", "key-type", "pass", "upload-file",
    "oauth2-bearer", "aws-sigv4", "header-file", "limit-rate", "max-filesize",
    "proxy-user", "continue-at", "speed-limit", "speed-time", "time-cond",
    "url-query", "happy-eyeballs-timeout-ms", "max-redirs", "ftp-port",
    "quote", "telnet-option", "interface", "krb", "login-options", "tlsuser",
    "tlspassword", "unix-socket", "abstract-unix-socket", "hostpubmd5",
    "hostpubsha256", "proto", "tls-max", "noproxy",
}

LOOPBACK_EXTRA = {"0.0.0.0", "::", "[::]"}


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    h = host.strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h == "localhost" or h.endswith(".localhost"):
        return True
    if h in LOOPBACK_EXTRA:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def host_of(operand: str) -> str | None:
    """URL operand / proxy 値から host を取り出す (userinfo / port を除去)。"""
    s = operand
    try:
        parsed = urlsplit(s if "://" in s else "//" + s)
        return parsed.hostname
    except ValueError:
        return None


def scheme_of(operand: str) -> str:
    return operand.split("://", 1)[0].lower() if "://" in operand else ""


def outpath_ok(path: str, roots: list[str]) -> bool:
    if path in ("/dev/null", "-"):
        return True
    p = path
    home = os.environ.get("HOME", "")
    if p.startswith("~/"):
        if not home:
            return False
        p = home + p[1:]
    elif p.startswith("~"):
        return False  # ~user 非対応
    if "$" in p or "*" in p or "?" in p:
        return False  # 未展開変数 / glob → 確証なし
    try:
        real = os.path.realpath(p)
    except (OSError, ValueError):
        return False  # NUL・対の無い surrogate 等で解決できない path → 確証なし
    for r in roots:
        if real == r or real.startswith(r + os.sep):
            return True
    return False


def writeout_ok(fmt: str) -> bool:
    """write-out の書式が file へ書かないと確証できれば True。

    `%output{file}` / `%output{>>file}` は書式の残りを任意の file へ書く (curl 8.3.0+)。
    `@file` は書式を file から読むので中身を確証できない。どちらも allow しない (#1983)。
    """
    return not fmt.startswith("@") and "%output" not in fmt


# 値を取らない (boolean) と確かめた long flag。ここに無い long flag は値を取りうる
# (`--netrc-file` / `--variable` 等) か省略形 (`--write` → `--write-out`) なので allow しない —
# boolean と読むと次の語 (値) を URL operand と取り違える (#2000)。拒否する flag を列挙すると
# 漏れがそのまま承認なしの経路になるので、許す flag を列挙する
LONG_BOOLEAN = {
    "silent", "show-error", "fail", "fail-with-body", "fail-early",
    "include", "head", "verbose", "insecure", "compressed", "get", "globoff",
    "tlsv1", "tlsv1.0", "tlsv1.1", "tlsv1.2", "tlsv1.3", "ssl-reqd", "trace-time", "parallel",
    "http1.0", "http1.1", "http2", "http2-prior-knowledge", "http3", "http3-only",
    "ipv4", "ipv6", "no-buffer", "no-progress-meter", "progress-bar", "no-keepalive",
    "path-as-is", "raw", "tcp-nodelay", "show-headers", "retry-connrefused",
    "retry-all-errors", "post301", "post302", "post303", "basic", "digest", "ntlm",
    "negotiate", "anyauth", "no-silent", "no-show-error", "no-fail",
    "no-include", "no-verbose", "no-insecure", "no-compressed", "styled-output",
    "no-styled-output", "tr-encoding", "remote-time", "create-dirs", "list-only",
    "suppress-connect-headers", "ignore-content-length", "disable", "no-sessionid",
}
KNOWN_LONG_FLAGS = frozenset(LONG_BAIL | LONG_BAILOUT | LONG_HOST | LONG_URL | LONG_PATHOUT
                             | LONG_WRITEOUT | LONG_OTHER_VALUE | LONG_BOOLEAN)


def curlrc_paths() -> list[str]:
    """curl が既定で読む設定 file のうち、存在するもの (curl の `--config` の既定の探索先)。"""
    dirs = [os.environ.get("CURL_HOME", ""), os.environ.get("XDG_CONFIG_HOME", ""),
            os.environ.get("HOME", "")]
    home = os.environ.get("HOME", "")
    candidates = [os.path.join(d, ".curlrc") for d in dirs if d]
    if home:
        candidates.append(os.path.join(home, ".config", "curlrc"))
    return [c for c in candidates if os.path.lexists(c)]


def curl_segment_ok(seg: list[str], out_roots: list[str]) -> bool:
    """curl サブコマンドが allow 可能なら True。
    1 つでも非 loopback 接続先 / 不正書き込み先 / bail flag があれば False。"""
    url_targets: list[str] = []
    host_targets: list[str] = []
    out_paths: list[str] = []
    # backtick / `$` はコマンド置換・変数展開になり、URL の字面と実際の接続先・実行内容がずれる
    if any("`" in tok or "$" in tok for tok in seg):
        return False
    # curl は第 1 引数が `-q` / `--disable` でない限り curlrc を暗黙に読む。curlrc の `location` /
    # `proxy` 等は字面に現れないまま接続先を変えるので、curlrc があれば `-q` 無しは allow しない
    if seg[1:2] not in (["-q"], ["--disable"]) and curlrc_paths():
        return False
    i = 1  # seg[0] == "curl"
    n = len(seg)
    while i < n:
        tok = seg[i]
        if tok == "--":
            url_targets.extend(seg[i + 1:])
            break
        if tok.startswith("--"):
            name, eq, val = tok[2:].partition("=")
            if name in LONG_BAIL or name in LONG_BAILOUT:
                return False
            if name not in KNOWN_LONG_FLAGS:
                return False  # 値を取るか確かめていない flag (`--expand-*` もここで落ちる)
            if (name in LONG_HOST or name in LONG_URL or name in LONG_PATHOUT
                    or name in LONG_WRITEOUT):
                if eq:
                    value = val
                elif i + 1 < n:
                    value = seg[i + 1]
                    i += 1
                else:
                    return False
                if name in LONG_HOST:
                    host_targets.append(value)
                elif name in LONG_URL:
                    url_targets.append(value)
                elif name in LONG_WRITEOUT:
                    if not writeout_ok(value):
                        return False
                else:
                    out_paths.append(value)
            elif name in LONG_OTHER_VALUE:
                if not eq:
                    i += 1  # 値を消費して無視
            # 残りは LONG_BOOLEAN (値を取らない)
        elif tok.startswith("-") and tok != "-":
            cluster = tok[1:]
            j = 0
            while j < len(cluster):
                c = cluster[j]
                if c in SHORT_BAIL or c in SHORT_BAILOUT:
                    return False
                if (c in SHORT_HOST or c in SHORT_PATHOUT or c in SHORT_WRITEOUT
                        or c in SHORT_OTHER_VALUE):
                    attached = cluster[j + 1:]
                    if attached:
                        value = attached
                    elif i + 1 < n:
                        value = seg[i + 1]
                        i += 1
                    else:
                        return False
                    if c in SHORT_HOST:
                        host_targets.append(value)
                    elif c in SHORT_PATHOUT:
                        out_paths.append(value)
                    elif c in SHORT_WRITEOUT and not writeout_ok(value):
                        return False
                    break  # value flag 以降の cluster 文字は値
                if c not in SHORT_BOOLEAN:
                    return False  # 値を取るか確かめていない short flag
                j += 1
        else:
            url_targets.append(tok)
        i += 1

    if not url_targets:
        return False  # 取得 URL がない curl は対象外
    for t in url_targets + host_targets:
        if not is_loopback(host_of(t)):
            return False
    # file:// 等は host が localhost でも local の file を読み書きする (`-T x file://localhost/...`)
    if not all(scheme_of(t) in ("", "http", "https") for t in url_targets):
        return False
    for p in out_paths:
        if not outpath_ok(p, out_roots):
            return False
    return True


def build_out_roots(cwd: str) -> list[str]:
    roots = []
    home = os.environ.get("HOME", "")
    candidates = ["/tmp", "/private/tmp"]
    if home:
        candidates.append(os.path.join(home, ".claude", "tmp"))
    if cwd:
        candidates.append(os.path.join(cwd, "tmp"))
    for c in candidates:
        try:
            roots.append(os.path.realpath(c))
        except (OSError, ValueError):  # 解決できない root (cwd に NUL 等) は候補から外す
            continue
    return roots


def judge(stdin_text: str) -> NoReturn:
    payload = read_payload_or_pass(stdin_text)
    command = tool_input_text(payload, "command")
    if not command or "curl" not in command:
        passthrough()
    # 2 行目は別のコマンドとして走るので allow しない (#1698)。shlex は改行を語の区切りとしか
    # 見ず、2 行目の語が 1 行目の curl の operand に混ざるので、下の分割では捉えられない
    if continues_on_another_line(command):
        passthrough()

    lex = shlex.shlex(command, posix=True, punctuation_chars=PUNCT)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        tokens = list(lex)
    except ValueError:
        passthrough()  # クォート不整合等 → 確証なし

    out_roots = build_out_roots(str(payload.get("cwd") or ""))

    # punctuation でサブコマンドへ分割。SPLIT_OPS 以外の punctuation は bail。
    segments: list[list[str]] = []
    seg: list[str] = []
    for t in tokens:
        if t and all(ch in PUNCT for ch in t):
            if t in SPLIT_OPS:
                segments.append(seg)
                seg = []
                continue
            passthrough()  # リダイレクト / subshell / background → bail
        else:
            seg.append(t)
    segments.append(seg)

    curl_seen = False
    for s in segments:
        if not s:
            continue
        head = s[0]
        if head not in SAFE_HEADS:
            passthrough()
        if head == "curl":
            curl_seen = True
            if not curl_segment_ok(s, out_roots):
                passthrough()

    if curl_seen:
        allow("loopback 宛 curl を自動許可")
    passthrough()


def main(stdin_text: str) -> tuple[int, str]:
    """hook の純粋な入口。(exit code, stdout 本文) を返す。"""
    return decide(judge, stdin_text)


if __name__ == "__main__":
    exit_code, stdout_text = main(read_stdin_text())
    sys.stdout.write(stdout_text)
    sys.exit(exit_code)
