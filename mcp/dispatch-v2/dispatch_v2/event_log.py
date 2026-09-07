"""events.jsonl (append-only) の読み書き。**唯一の正本** (ADR 0057)。

- event の形は `{ts, actor, type, payload}`。意図の記帳も観測事実の変化も同じ log へ書く
- 書き込みは **完成した 1 行を `O_APPEND` の単一 `os.write()` + fsync**。これが本 module の
  atomic write で、途中で落ちても半端な行を「次の行として」読ませない。state file を持たない
  設計なので temp + rename は補助 file (socket path 等) 側の話になる
- 出力は `ensure_ascii=False` の挿入順。**正本は人が grep / cat で診断する**もので、
  日本語の note が `\\uXXXX` に潰れると読めない (ADR 0057 の「plain text である利点」)

並行書き込みの排除は本 module の責務ではない — **書き手は daemon 1 プロセスに集約されている**
(ADR 0057)。daemon が `<root>/daemon.lock` を保持し、HTTP を単一スレッドで回すことで直列性を
構造で保証する。ここに flock を重ねると「共有を分けずに直列化を足す」側の設計になる。
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# crash で千切れた末尾行を捨てた事実を、正本自身へ durable に残すための event type。
# 捨てた事実を stderr だけに書くと、後から台帳を読む人には欠けが見えない
TORN_TAIL_EVENT = "log_torn_tail_discarded"

# 千切れた行のうち記録へ残す長さ。診断に足りる程度に留める (行全体を持つと巨大 payload が
# そのまま正本へ二重に載る)
TORN_TAIL_EXCERPT_LENGTH = 200

ENVELOPE_FIELDS = ("ts", "actor", "type", "payload")


class EventLogError(RuntimeError):
    """event log の読み書きに失敗した / 行が envelope の形でない。"""


def now_iso():
    """UTC の ISO8601 (秒精度 + `Z`)。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_event(event_type, payload, *, actor, ts):
    """event envelope を組み立てる (key の順序が正本の読みやすさを決める)。"""
    return {"ts": ts, "actor": actor, "type": event_type, "payload": payload}


class EventLog:
    """1 project 分の events.jsonl。"""

    def __init__(self, path, clock=now_iso):
        self.path = Path(path)
        self._clock = clock

    def append(self, event):
        """完成した 1 行を追記して fsync する。返り値は書いた event そのもの。"""
        _require_envelope(event, source="append")
        line = json.dumps(event, ensure_ascii=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = line.encode("utf-8")
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            # **短書き (ENOSPC 等) を書き切るまで繰り返す**。返り値を捨てると、半端な行を
            # 書いたまま「記帳できた」と応答してしまう — 次の起動で末尾が捨てられれば記帳が
            # 消え、次の event が続けて書かれれば 2 行が 1 行に融合して log ごと読めなくなる。
            # O_APPEND なので続きも必ず末尾に付く
            written = 0
            while written < len(data):
                written += os.write(descriptor, data[written:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return event

    def read(self):
        """全 event を書かれた順で返す。壊れた行は黙って飛ばさず停止する。"""
        if not self.path.exists():
            return []
        text = self.path.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            raise EventLogError(
                f"{self.path}: 末尾の行が改行で終わっていない (crash で千切れた可能性)。"
                "daemon の起動時収束 (EventLog.recover) を先に通す"
            )
        events = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EventLogError(f"{self.path}:{number}: JSON として読めない行 ({exc})") from exc
            _require_envelope(event, source=f"{self.path}:{number}")
            events.append(event)
        return events

    def recover(self):
        """crash で千切れた末尾行を捨てて収束させる。捨てた byte 数を返す (無ければ 0)。

        **起動のたびに走らせてよい** — 千切れが無ければ何もしない。人手で truncate して
        もらう前提にしないための経路 (`principle-idempotent-operations`)。
        """
        if not self.path.exists():
            return 0
        data = self.path.read_bytes()
        if not data or data.endswith(b"\n"):
            return 0
        boundary = data.rfind(b"\n") + 1  # 見つからなければ 0 = 全体が千切れた 1 行
        discarded = data[boundary:]
        with self.path.open("r+b") as handle:
            handle.truncate(boundary)
            os.fsync(handle.fileno())
        excerpt = discarded[:TORN_TAIL_EXCERPT_LENGTH].decode("utf-8", errors="replace")
        print(
            f"[dispatch-v2] {self.path}: 千切れた末尾 {len(discarded)} byte を捨てた: {excerpt!r}",
            file=sys.stderr,
        )
        self.append(
            make_event(
                TORN_TAIL_EVENT,
                {"discarded_bytes": len(discarded), "discarded_excerpt": excerpt},
                actor="reconciler",
                ts=self._clock(),
            )
        )
        return len(discarded)


def _require_envelope(event, *, source):
    if not isinstance(event, dict):
        raise EventLogError(f"{source}: event が object でない ({type(event).__name__})")
    missing = [field for field in ENVELOPE_FIELDS if field not in event]
    if missing:
        raise EventLogError(f"{source}: event envelope の欄が足りない {missing}")
    if not isinstance(event["payload"], dict):
        raise EventLogError(f"{source}: payload が object でない")
