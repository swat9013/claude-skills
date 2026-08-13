"""transcript の形式知識を独占する adapter (pure module)。

`~/.claude/projects/**/*.jsonl` の on-disk 形式を知るのは本 module だけで、
`commands/` 配下の各 tool は正規化済みの値を受け取る (ADR 0029 の format
isolation)。形式知識が消費側にコピーされると、同じ record について tool ごとに
別の答えが出る — 実際に `is_error` 欠落時の outcome 解釈が 2 scanner で逆
(invocations は unknown、permissions は success) になり、2 mart が矛盾した
([#476](https://github.com/swat9013/swat-skills/issues/476))。

本 module が持つのは transcript-walk 系 5 定義 + **repo 識別子の解決** +
**tool 実行の outcome 判定** + **slash 呼出しの判定** + **user / assistant 以外の
record type の正規化** (attachment / system / usage / hook)。permissions の 7 分類は
base 語彙 (success / error / user-reject / unknown) の refinement として commands 側に
置き、成否そのものの分岐を再導出させない。

slash 判定 (`parse_slash_invocation` / `is_slash_expansion_record`) を丸ごとここに
置くのは、outcome と同じ事故が起きていたため。述語が adapter と commands に割れて
いた間、同じ record について 3 tool が 3 通りの答えを出していた (scan_prompts は
行頭一致だけ / scan_invocations は部分一致 + `<command-args>` / find_invocations は
その両方 + wrapper 除外)。**述語の一部だけを共有層に置くのは共有していないのと
同じ**で、欠けた条件の側に偏った mart が出る。

repo 解決 (`resolve_repo_at`) をここに置くのは、mart を書く `scan_prompts` と
mart を repo で絞る `select_candidates` が**同一の repo 表現**を出す必要があるため
(表現がずれると絞り込みが黙って 0 件になる)。一致をテストの assertion ではなく
呼び先の単一性で構造的に保証する。祖先遡り (消えた worktree の救済) は
`scan_prompts` 側の観測契約なので昇格させない。

**観測契約 (どの record をどう解釈するか) は tool 側に残す**: extract 層
(7 分類 vs 4 分類 vs 手入力 prompt 抽出) と観測窓判定 (`within_window` /
`_within_window`) は各 tool の mart schema に属するので本 module へ上げない
([ADR 0013](../../../docs/adr/0013-intra-subsystem-implementation-sharing.md))。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

# git 解決の上限 (秒)。解決不能でも観測は続けるため失敗は空文字に潰す。
GIT_TIMEOUT_SEC = 5

# user-reject の best-effort 判定文言。false positive は避けるので content 文字列を
# includes で軽く見る程度。#29499 の限界 (別種の user-reject を拾えない / 別種の
# error 文言を user-reject に誤検知する余地あり) は各 scanner の SKILL.md 側で注記する。
USER_REJECT_PATTERNS = (
    "the user doesn't want",
    "user rejected the",
    "request interrupted by user",
    "tool call was rejected",
)

# permission-rule deny の文言。base outcome では "error" 側に落ちるが、user-reject
# 文言との優先順を 1 箇所で固定するために adapter 側に置く (#476)。現状の呼び先は
# 判定 (search) のみで、named group (tool / cmd) を読む呼び先は無い。
PERMISSION_DENIAL_TOOL_RE = re.compile(
    r"Permission to use\s+(?P<tool>[A-Za-z_][A-Za-z0-9_-]*)"
    r"(?:\s+with\s+command\s+(?P<cmd>.+?))?\s+has been denied\.",
    re.DOTALL,
)

# toolDenialKind のうち「ユーザーの拒否ではない」deny 種別。base では error に落ち、
# permissions scanner 側で deny_* へ細分される。
NON_USER_DENIAL_KINDS = (
    "permission-rule",
    "automode-blocked",
    "automode-unavailable",
    "hook",
)

# base outcome の語彙。両 scanner の mart 語彙はこれの refinement (permissions は
# error / user-reject を deny_* へ細分する) であり、base 自体は mart へ出さない。
BASE_OUTCOMES = ("success", "error", "user-reject", "unknown")


def resolve_now(now_str: str | None) -> dt.datetime:
    if now_str:
        raw = now_str.replace("Z", "+00:00")
        return dt.datetime.fromisoformat(raw).astimezone(dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc)


def truncate(s: str | None, limit: int) -> str:
    if not s:
        return ""
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[:limit]


def git_output(cwd: Path, argv: list[str]) -> str:
    """`git -C <cwd> <argv>` の stdout。失敗・timeout・git 不在は空文字に潰す。"""
    return git_output_with_cause(cwd, argv)[0]


# 解決不能の理由。`git_output` は両方とも空文字に潰すため、旧来の呼び先
# (`resolve_repo_at`) では「git が動かせない」と「git は動いたが repo でない」を
# 区別できなかった (#496 からの繰り越しバグ)。区別が要る呼び先だけ
# `resolve_repo_at_with_cause` を使う。
GIT_UNAVAILABLE = "git_unavailable"
NOT_A_REPO = "not_a_repo"


def git_output_with_cause(cwd: Path, argv: list[str]) -> tuple[str, str | None]:
    """`git -C <cwd> <argv>` の stdout と、失敗した場合の理由。

    理由は `GIT_UNAVAILABLE` (git 自体を起動できない: 未インストール / timeout) と
    `NOT_A_REPO` (git は起動できたが非 0 終了。典型は非 repo だが、argv 依存の
    他の git 失敗も同じ枠に入る) の 2 種。成功時は理由 None。
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *argv],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "", GIT_UNAVAILABLE
    if proc.returncode != 0:
        return "", NOT_A_REPO
    return proc.stdout, None


def resolve_repo_at(dir_path: Path) -> str | None:
    """実在するディレクトリ 1 つに対して git の repo 識別子を返す。

    解決手順は origin remote URL → git-common-dir の親の順。worktree からでも
    git-common-dir が親 repo に寄るため、同一 repo の worktree は同じ識別子になる。

    解決できない理由 (git 不在 / 非 repo) を要る呼び先は
    `resolve_repo_at_with_cause` を使う — 本関数は理由を捨てる後方互換の薄い形。
    """
    repo, _cause = resolve_repo_at_with_cause(dir_path)
    return repo


def resolve_repo_at_with_cause(dir_path: Path) -> tuple[str | None, str | None]:
    """`resolve_repo_at` と同じ解決だが、解決できないときの理由も返す。

    理由は 2 手順目 (git-common-dir) の失敗種別を採る。1 手順目 (remote) が
    `GIT_UNAVAILABLE` で失敗すれば 2 手順目も同じ理由で失敗するため、実質的には
    「git 自体が動くか」で決まる。
    """
    url, remote_cause = git_output_with_cause(dir_path, ["remote", "get-url", "origin"])
    url = url.strip()
    if url:
        return url.splitlines()[0].strip(), None
    common_dir, common_cause = git_output_with_cause(
        dir_path, ["rev-parse", "--git-common-dir"])
    common_dir = common_dir.strip()
    if not common_dir:
        return None, common_cause or remote_cause
    common_path = Path(common_dir)
    if not common_path.is_absolute():
        common_path = dir_path / common_path
    try:
        return str(common_path.resolve().parent), None
    except OSError:
        return None, NOT_A_REPO


# --- outcome 判定 ------------------------------------------------------------
# tool 実行の成否は 2 scanner が別々に判定していたため、`is_error` 欠落時の解釈が
# 逆 (invocations: unknown / permissions: success) になっていた (#476)。判定は
# 本節に一本化し、commands 側はここを呼ぶ。


def flatten_result_text(content: Any) -> str:
    """tool_result の content (str または block list) を 1 本の text に潰す。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if isinstance(c, dict):
                t = c.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return " ".join(parts)
    return ""


def outcome_from_tool_use_result(tool_use_result: Any) -> str | None:
    """user record の `toolUseResult` (構造化真値) だけから base outcome を出す。

    判定材料が無い (field ごと欠落) 場合のみ None を返し、呼び元が `is_error` へ
    fallback する。dict でも str でも「結果が返っている」事実自体が success の証跡
    なので、否定シグナル (interrupted / is_error / success:false) が無ければ
    success とする。`userModified` は「ユーザーが編集して受け入れた」であり成功。
    """
    if tool_use_result is None:
        return None
    if isinstance(tool_use_result, dict):
        if tool_use_result.get("interrupted") is True:
            return "user-reject"
        if tool_use_result.get("is_error") is True:
            return "error"
        if tool_use_result.get("success") is False:
            return "error"
        return "success"
    return "success"


def classify_base_outcome(
    is_error: Any,
    content: Any,
    tool_use_result: Any = None,
    tool_denial_kind: str | None = None,
) -> str:
    """tool_result 1 件の base outcome (success / error / user-reject / unknown)。

    優先順:

    1. `is_error is True` → 失敗確定。種別だけ toolDenialKind + content 文言で分ける。
       この分岐が `toolUseResult` より先なのは実測にもとづく — `is_error: True` の
       record 186/186 で `toolUseResult` は生の error 文字列 (構造化 verdict を
       持たない) であり、先に見ると全件 success に化ける
    2. `toolUseResult` (構造化真値) → success / error / user-reject
    3. `is_error is False` → success
    4. どちらも無い → unknown (判定不能を success に丸めない)

    実測 (直近 200 transcript / tool_result 3,125 件) では `toolUseResult` は全件
    存在し、`is_error` は 47% で欠落する。2 が効くことで Skill / Agent / `mcp__*` の
    outcome が unknown 一色になる不具合が解消する。
    """
    if is_error is True:
        text = flatten_result_text(content)
        if tool_denial_kind == "user-rejected":
            return "user-reject"
        if tool_denial_kind in NON_USER_DENIAL_KINDS:
            return "error"
        # permission-rule deny を user-reject 文言より先に見る (permissions scanner の
        # 既存優先順)。両者を別順で見ると 2 scanner の判定が割れるため lib で固定する
        if PERMISSION_DENIAL_TOOL_RE.search(text):
            return "error"
        low = text.lower()
        for pat in USER_REJECT_PATTERNS:
            if pat in low:
                return "user-reject"
        return "error"

    truth = outcome_from_tool_use_result(tool_use_result)
    if truth is not None:
        return truth
    if is_error is False:
        return "success"
    return "unknown"


def tool_use_result_of(rec: dict, tool_result_blocks: list) -> Any:
    """user record の `toolUseResult` を、対応 block が一意なときだけ返す。

    `toolUseResult` は record 単位・tool_result block は複数あり得るので、2 つ以上
    ある record では真値をどの block に帰属させるか決められない。実測 (3,125 件) では
    全件 1 block だが、割れた場合に別 tool の真値を誤帰属させないため None に倒す。
    """
    if len(tool_result_blocks) != 1:
        return None
    return rec.get("toolUseResult")


# --- record 共通の anchor ----------------------------------------------------
# session id は record によって `sessionId` / `session_id` の両方で現れる (同一
# record に両方載ることもある)。どちらを見るかを消費側に選ばせない。


def session_id_of(rec: dict) -> str:
    """record の session id。両表記を吸収し、無ければ空文字。"""
    return str(rec.get("sessionId") or rec.get("session_id") or "")


# --- attachment / system record の正規化 -------------------------------------
# scanner が読んでいた record type は user / assistant の 2 種だけで、「session で
# 実際に提示された分母」「hook の実行実績」「token 経済」はいずれも別 type に載って
# いる (#478)。type ごとの読み方を本節に集約し、tool 側には正規化済みの値を渡す。

# hook の実行実績が載る attachment。`hook_success` だけが exitCode / durationMs を
# 持ち、他 3 種は hookName / hookEvent までしか持たない (実測)。
HOOK_ATTACHMENT_TYPES = (
    "hook_success",
    "hook_cancelled",
    "hook_additional_context",
    "hook_system_message",
)

# attachment type → **提示された unit 名が載る field**。type ごとに field 名が違う
# のは形式の事実なので adapter が持ち、その名前を消費側の unit 型 (skill /
# mcp_server / …) に写す表は tool 側の観測契約に残す。skill_listing は全量
# (`names` + `skillCount`)、delta 系は差分。除去 (`removedNames`) は追わない —
# 分母の定義が「一度でも提示された」だから。
PRESENTED_NAME_FIELDS: dict[str, tuple[str, ...]] = {
    "skill_listing": ("names",),
    "mcp_instructions_delta": ("addedNames",),
    "agent_listing_delta": ("addedTypes",),
    "deferred_tools_delta": ("addedNames", "readdedNames"),
}

# 「session で実際に提示された分母」を持つ attachment。**上の表から導出する** —
# 2 つを別々に書くと、片方にだけ type を足したときに「gate は通るが field を読めない」
# 型が生まれる。
PRESENTED_ATTACHMENT_TYPES = tuple(PRESENTED_NAME_FIELDS)

# attachment type → **本文 (token を数える対象) が載る field**。上の名前 field とは
# 別物で、同じ attachment でも「何が提示されたか」と「何字だったか」は別 field。
STATIC_PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    "skill_listing": ("content",),
    "mcp_instructions_delta": ("addedBlocks",),
    "deferred_tools_delta": ("addedLines",),
    "agent_listing_delta": ("addedLines",),
}

# memory file (CLAUDE.md / .claude/rules/*) の注入が載る attachment type。
MEMORY_ATTACHMENT_TYPE = "nested_memory"


@dataclasses.dataclass(frozen=True)
class MemoryInjection:
    """memory file が 1 session に注入された事実。

    `path` は観測された絶対 path そのもの (worktree 断片の畳み込みは消費側の
    集計単位の話なので持ち込まない)。
    """

    path: str
    display_path: str
    memory_type: str
    globs: list[str]
    text: str
    differs_from_disk: bool


def attachment_text_of(body: dict, fields: Iterable[str]) -> str:
    """attachment の本文 field (str または list[str]) を 1 本の text に潰す。"""
    parts: list[str] = []
    for field in fields:
        value = body.get(field)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.append("\n".join(v for v in value if isinstance(v, str)))
    return "\n".join(parts)


def memory_injection_of(rec: dict) -> MemoryInjection | None:
    """`attachment.nested_memory` を 1 注入に正規化する。該当しなければ None。

    本文・type・globs は `attachment.content` (dict) の内側に入れ子で載り、path は
    外側と内側の双方に現れうる。この入れ子の形が消費側へ漏れると、同じ record に
    ついて tool ごとに別の path / 別の字数が出る。
    """
    body = attachment_of(rec)
    if body is None or body.get("type") != MEMORY_ATTACHMENT_TYPE:
        return None
    inner = body.get("content")
    inner = inner if isinstance(inner, dict) else {}
    globs = inner.get("globs")
    return MemoryInjection(
        path=str(body.get("path") or inner.get("path") or ""),
        display_path=str(body.get("displayPath") or ""),
        memory_type=str(inner.get("type") or ""),
        globs=globs if isinstance(globs, list) else [],
        text=attachment_text_of(inner, ("content",)),
        differs_from_disk=inner.get("contentDiffersFromDisk") is True,
    )

# token 概算の係数。CJK は 1 字 ≈ 1 token、その他は 4 字 ≈ 1 token として数える。
# **正確な tokenizer ではない** — 消費側が単独根拠にしないよう、mart には
# `TOKEN_ESTIMATOR` を添えて approx であることを明示する (#478 の cclens issue #1
# 同型の過大評価リスク)。
#
# 同じ係数を `skills/steering/inventory-claude-md/scripts/scan-claude-md.py` が写しで
# 持つ (PEP 723 単一 script は本 module を import できない)。**片方だけ変えると同じ
# file の静的コストと注入実績が別スケールになる**ので、変更は両方同時に行う
# (parity は `tests/test_scan_claude_md.py` が検査する)。
CJK_RE = re.compile("[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef]")
ASCII_CHARS_PER_TOKEN = 4.0
CJK_CHARS_PER_TOKEN = 1.0
TOKEN_ESTIMATOR = "approx: cjk 1 字/token + その他 4 字/token (tokenizer 非使用)"


@dataclasses.dataclass(frozen=True)
class HookFiring:
    """hook が 1 回 fire した実績。`fired` した事実のみを表し、設定側の分母は持たない。

    `exit_code` / `duration_ms` は `hook_success` にしか載らないので、他の
    attachment 由来では None になる。**None を 0 と読まない** (未観測と成功の混同)。
    """

    hook_name: str
    hook_event: str
    attachment_type: str
    command: str
    exit_code: int | None
    duration_ms: int | None
    timed_out: bool
    session_id: str
    timestamp: str
    cwd: str


def attachment_of(rec: dict) -> dict | None:
    """attachment record から本体 dict を返す。attachment でなければ None。"""
    if rec.get("type") != "attachment":
        return None
    body = rec.get("attachment")
    return body if isinstance(body, dict) else None


def hook_firing_of(rec: dict) -> HookFiring | None:
    """hook 系 attachment を 1 firing に正規化する。該当しなければ None。

    `hook_cancelled` は timeout での打ち切りで、`timedOut` が真値。成否そのものは
    `exit_code` にしか出ないため、cancel と失敗を同じ枠に潰さない。
    """
    body = attachment_of(rec)
    if body is None:
        return None
    atype = body.get("type")
    if atype not in HOOK_ATTACHMENT_TYPES:
        return None
    exit_code = body.get("exitCode")
    duration = body.get("durationMs")
    return HookFiring(
        hook_name=str(body.get("hookName") or ""),
        hook_event=str(body.get("hookEvent") or ""),
        attachment_type=str(atype),
        # command は hook_success / hook_cancelled にしか載らない。設定側の分母と
        # 紐づける唯一の手掛かりなので、空文字と「持たない」を区別せず空に潰す。
        command=str(body.get("command") or ""),
        exit_code=exit_code if isinstance(exit_code, int) else None,
        duration_ms=duration if isinstance(duration, int) else None,
        timed_out=body.get("timedOut") is True,
        session_id=session_id_of(rec),
        timestamp=str(rec.get("timestamp") or ""),
        cwd=str(rec.get("cwd") or ""),
    )


def stop_hook_durations_of(rec: dict) -> list[int]:
    """`system.stop_hook_summary` の hookInfos から durationMs を返す。

    実測で hookInfos が持つのは `{command, durationMs}` の 2 key だけで、
    **hookName も exitCode も無い** — Stop hook の所要時間は観測できるが、どの
    hook 設定に紐づくかは attachment 側 (`hook_success`) からしか分からない。
    """
    if rec.get("type") != "system" or rec.get("subtype") != "stop_hook_summary":
        return []
    out: list[int] = []
    for info in rec.get("hookInfos") or []:
        if isinstance(info, dict) and isinstance(info.get("durationMs"), int):
            out.append(info["durationMs"])
    return out


def extract_user_text(content: Any) -> str:
    """user turn の `message.content` から表示可能な text を取り出す。

    tool_result / system tag (`<...>` 始まり、slash 展開 record を含む) 混入 turn は
    空文字を返す (人間可読の prompt として扱わない)。ingest (`store.ingest`) と
    scan_invocations の excerpt が同じ答えを要るため adapter に一本化する。
    """
    if isinstance(content, str):
        if content.startswith("<"):
            return ""
        return content
    if isinstance(content, list):
        parts = []
        has_tool_result = False
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_result":
                has_tool_result = True
                continue
            if btype == "text":
                txt = block.get("text", "")
                if isinstance(txt, str) and not txt.startswith("<"):
                    parts.append(txt)
        if has_tool_result and not parts:
            return ""
        return " ".join(parts)
    return ""


def usage_of(rec: dict) -> dict[str, int] | None:
    """assistant record の `message.usage` を 4 項目へ正規化する。

    `usage.iterations[]` は同じ turn の内訳を再掲した list なので**足さない** —
    top-level と両方を数えると全項目が二重計上になる。cache 系を分けて残すのは、
    「重い」の正体が入力の再送 (cache_creation) か読み出し (cache_read) かで
    示唆が変わるため。
    """
    if rec.get("type") != "assistant":
        return None
    usage = (rec.get("message") or {}).get("usage")
    if not isinstance(usage, dict):
        return None
    out: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens",
                "cache_creation_input_tokens", "cache_read_input_tokens"):
        value = usage.get(key)
        out[key] = value if isinstance(value, int) else 0
    return out


def compact_boundary_of(rec: dict) -> dict | None:
    """`system.compact_boundary` の compactMetadata を正規化する。

    `cumulative_dropped_tokens` は session 内で**累積**する値なので、消費側は
    session ごとに最後の 1 件を採る (boundary をまたいで足すと多重計上になる)。
    """
    if rec.get("type") != "system" or rec.get("subtype") != "compact_boundary":
        return None
    meta = rec.get("compactMetadata")
    if not isinstance(meta, dict):
        meta = {}

    def _int(key: str) -> int:
        value = meta.get(key)
        return value if isinstance(value, int) else 0

    return {
        "trigger": str(meta.get("trigger") or ""),
        "pre_tokens": _int("preTokens"),
        "post_tokens": _int("postTokens"),
        "cumulative_dropped_tokens": _int("cumulativeDroppedTokens"),
        "duration_ms": _int("durationMs"),
        "session_id": session_id_of(rec),
        "timestamp": str(rec.get("timestamp") or ""),
        "cwd": str(rec.get("cwd") or ""),
    }


def count_chars(text: str | None) -> tuple[int, int]:
    """text の (cjk_chars, other_chars)。ingest が store へ永続化する粒度で、
    query 層の UDF (`estimate_tokens_from_counts`) が token 数へ変換する。
    """
    if not text:
        return 0, 0
    cjk = len(CJK_RE.findall(text))
    return cjk, len(text) - cjk


def estimate_tokens_from_counts(cjk_chars: int, other_chars: int) -> int:
    """(cjk_chars, other_chars) から token 数を概算する (`TOKEN_ESTIMATOR` の係数)。

    `estimate_tokens` と計算式を共有する唯一の理由は、係数の単一ソースを
    `adapter/transcript.py` に保つため (`scan-claude-md.py` との parity 制約)。
    """
    return math.ceil(cjk_chars / CJK_CHARS_PER_TOKEN + other_chars / ASCII_CHARS_PER_TOKEN)


def estimate_tokens(text: str | None) -> int:
    """文字列の token 数を概算する (`TOKEN_ESTIMATOR` の係数)。

    tokenizer を持ち込まないのは、観測の決定性 (同じ入力に同じ数) と依存ゼロを
    優先するため。**桁の比較にだけ使える精度**で、bucket 判定の単独根拠にはしない。
    """
    cjk, other = count_chars(text)
    return estimate_tokens_from_counts(cjk, other)


# --- slash command マーカー --------------------------------------------------
# 「slash 呼出しがどう transcript に現れるか」は形式知識なので adapter が持つ。
# 分母 (enumerate 済み skill) との突合は各 tool の観測契約なので tool 側に残す。

# slash 展開 record の先頭 tag。harness は呼出しをこの形の user record に展開する。
SLASH_COMMAND_TAGS = ("<command-name>", "<command-message>")

# 別 session の生ログを丸ごと内包した wrapper record の目印 (claude-mem の observer
# session 等)。内包された呼出しは「この transcript で起きた呼出し」ではない。
WRAPPER_TAG = "<transcript-data>"

# `<command-name>/xxx</command-name>` の `xxx`。**leading slash は必須** — 直近 200
# transcript の実測で、slash 無しの形は全件が SKILL.md 本文や regex 例の引用
# (`<command-name>{name}</command-name>` 等) で、実呼出しは 1 件も無かった。
COMMAND_NAME_RE = re.compile(r"<command-name>/([^<]+)</command-name>")


def is_slash_expansion_record(content: Any) -> bool:
    """user record の content が slash command の展開 record か (**行頭一致**)。

    部分一致にしないのは、これらの tag を本文中に引用しただけの手入力 prompt を
    巻き込まないため。手入力 prompt から展開 record を除外する側 (`scan_prompts`) と、
    呼出しとして計上する側 (`scan_invocations` / `find_invocations`) が同じ問いに
    同じ答えを出すよう、判定はここ 1 箇所に置く。
    """
    return isinstance(content, str) and content.lstrip().startswith(SLASH_COMMAND_TAGS)


def parse_slash_invocation(content: Any) -> str | None:
    """user turn の content が**実呼出**なら slash command 名 (leading slash 抜き)。

    実呼出しの条件は 3 つとも構造で判定する。判定材料を欠くと片方向に外れる:

    1. 展開 record であること (`is_slash_expansion_record`) — 行頭一致。本文中に
       引用されただけのマーカー (SKILL.md の提示 / regex 例 / Bash 出力の再掲) は
       ここで落ちる。**これを欠くと実測 30 日窓で 86 件 (32%) の偽呼出しが載り**、
       doc 中の regex 断片 (`?([a-z0-9-]+:)?xxx`) が skill id として mart に入る
    2. wrapper (`WRAPPER_TAG`) を含まないこと — 別 session の生ログを内包した
       record 内の呼出しは、この transcript の呼出しではない
    3. `<command-name>/…</command-name>` が在ること

    **`<command-args>` の有無は条件にしない。** 引数なし呼出し (`/loop` 等) には
    付かないため、必須にすると実測 149 件を取りこぼす。かつて必須だったのは
    literal 引用を弾くためだが、その役割は条件 1 が構造的に果たす。
    """
    if not is_slash_expansion_record(content):
        return None
    if WRAPPER_TAG in content:
        return None
    m = COMMAND_NAME_RE.search(content)
    if not m:
        return None
    return m.group(1).strip()


def _iter_jsonl(fp) -> Iterable[dict]:
    for line in fp:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            yield rec


def walk_transcripts(
    transcripts_dir: Path,
    cutoff: dt.datetime,
) -> Iterable[tuple[str, Path]]:
    """transcript file を (project_dir_name, path) で yield。

    lake の dir 名エンコードが lossy なため directory 単位のスコープ絞りは
    行わない。scope は event.cwd で filter する (_filter_events_for_project)。
    mtime による cutoff filter のみ適用。
    """
    if not transcripts_dir.is_dir():
        return
    cutoff_ts = cutoff.timestamp()
    for project_dir in sorted(transcripts_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        for jsonl in sorted(project_dir.glob("*.jsonl")):
            try:
                mtime = jsonl.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff_ts:
                continue
            yield project_dir.name, jsonl
