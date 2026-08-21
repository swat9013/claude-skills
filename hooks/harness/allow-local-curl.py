#!/usr/bin/env python3
"""allow-local-curl.py — loopback 宛 curl のみを allow する許可専用 PreToolUse hook。

curl は settings の ask 対象 (外部ホストへのデータ持ち出し / 任意取得を確認するため)。
本 hook は以下を全て満たすときだけ permissionDecision:"allow" を返す:

  - コマンド中の全 curl サブコマンドの接続先 (URL operand / --url / proxy) が loopback
    (localhost / *.localhost / 127.0.0.0/8 / ::1 / 0.0.0.0 / ::)
  - curl 以外のサブコマンドは既知の安全 read-only コマンドのみ (compound 許容)
  - curl の出力ファイル (-o 等) は /dev/null / stdout / tmp ルート配下のみ
  - 接続先を再マップ・外部注入しうる flag (--resolve / --connect-to / -K) を含まない
  - shell リダイレクト / subshell / background を含まない

fail-safe: 静的に安全と確証できないケースは一切 allow せず exit 0 (出力なし) で素通しし、
settings の ask に委ねる。guard-* の deny は本 hook 追加後も従来どおり効く
(deny > allow のため compound 内の危険操作は別 hook がブロックする)。

allow-tmp-delete.sh と同じ「確証なき場合は素通し」方針。shell tokenize と URL 解析の
正確性のため sh ではなく python3 (shlex / urllib / ipaddress) で実装する。
"""
from __future__ import annotations

import ipaddress
import json
import os
import shlex
import sys
from urllib.parse import urlsplit

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
SHORT_BAIL = set("K")
LONG_BAIL = {"resolve", "connect-to", "config"}
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
                "trace-ascii", "etag-save", "stderr", "libcurl"}
# 値を消費するが接続先でない (無視する) short flag
SHORT_OTHER_VALUE = set("AbCdeEFHmPQrtTuUwXyYz")
# 値を消費するが接続先でない (無視する) long flag
LONG_OTHER_VALUE = {
    "request", "header", "user", "write-out", "form", "form-string",
    "referer", "user-agent", "cookie", "data", "data-raw", "data-binary",
    "data-ascii", "data-urlencode", "json", "max-time", "connect-timeout",
    "retry", "retry-delay", "retry-max-time", "range", "cert", "key",
    "cacert", "capath", "cert-type", "key-type", "pass", "upload-file",
    "oauth2-bearer", "aws-sigv4", "header-file", "limit-rate", "max-filesize",
    "proxy-user", "continue-at", "speed-limit", "speed-time", "time-cond",
    "url-query", "happy-eyeballs-timeout-ms", "max-redirs", "ftp-port",
    "quote", "telnet-option", "interface", "krb", "login-options", "tlsuser",
    "tlspassword", "unix-socket", "abstract-unix-socket", "hostpubmd5",
    "hostpubsha256", "proto", "proto-default", "tls-max", "tlsv1",
}

LOOPBACK_EXTRA = {"0.0.0.0", "::", "[::]"}


def passthrough() -> None:
    """判定を settings (ask) に委ねる。

    判断は出さないが観測痕跡は残す (#587 / ADR 0043)。無出力の exit は transcript に
    attachment を残さず、棚卸しで「壊れて死んだ hook」と「窓内に出番が無かった hook」が
    同じ見え方になる。`permissionDecision` を持たない envelope は通常の permission フローへ
    委ねるので、判断の意味論は無出力のときと変わらない。逐語で 1 行に保つ (テストが全 guard
    の一致を見る)。
    """
    sys.stdout.write(
        '{"hookSpecificOutput":{"hookEventName":"PreToolUse"},"suppressOutput":true}\n'
    )
    sys.exit(0)


def emit_allow() -> None:
    json.dump(
        {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "loopback 宛 curl を自動許可",
        }},
        sys.stdout,
    )
    sys.exit(0)


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
    real = os.path.realpath(p)
    for r in roots:
        if real == r or real.startswith(r + os.sep):
            return True
    return False


def curl_segment_ok(seg: list[str], out_roots: list[str]) -> bool:
    """curl サブコマンドが allow 可能なら True。
    1 つでも非 loopback 接続先 / 不正書き込み先 / bail flag があれば False。"""
    url_targets: list[str] = []
    host_targets: list[str] = []
    out_paths: list[str] = []
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
            if name in LONG_HOST or name in LONG_URL or name in LONG_PATHOUT:
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
                else:
                    out_paths.append(value)
            elif name in LONG_OTHER_VALUE:
                if not eq:
                    i += 1  # 値を消費して無視
            # それ以外の long flag は boolean 扱い
        elif tok.startswith("-") and tok != "-":
            cluster = tok[1:]
            j = 0
            while j < len(cluster):
                c = cluster[j]
                if c in SHORT_BAIL or c in SHORT_BAILOUT:
                    return False
                if c in SHORT_HOST or c in SHORT_PATHOUT or c in SHORT_OTHER_VALUE:
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
                    break  # value flag 以降の cluster 文字は値
                j += 1  # boolean short flag
        else:
            url_targets.append(tok)
        i += 1

    if not url_targets:
        return False  # 取得 URL がない curl は対象外
    for t in url_targets + host_targets:
        if not is_loopback(host_of(t)):
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
        except OSError:
            continue
    return roots


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (ValueError, TypeError):
        passthrough()
    command = (data.get("tool_input") or {}).get("command") or ""
    if not command or "curl" not in command:
        passthrough()

    lex = shlex.shlex(command, posix=True, punctuation_chars=PUNCT)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        tokens = list(lex)
    except ValueError:
        passthrough()  # クォート不整合等 → 確証なし

    out_roots = build_out_roots(data.get("cwd") or "")

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
        emit_allow()
    passthrough()


if __name__ == "__main__":
    main()
