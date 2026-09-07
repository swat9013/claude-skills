"""ULID (woId / escalationId) の生成。

**既製品 (`python-ulid` 等) ではなく自前**にした要件: reconciler daemon は MCP client から
lazy 起動されるので、**stdlib だけで起動できる**必要がある (uv の cache が冷えている /
offline の環境で起動が落ちない)。48bit 時刻 + 80bit 乱数の Crockford base32 という仕様は
固定されており、差し替えたくなればこの module 1 本の交換で済む。

**同一ミリ秒内の単調性は保証しない** — 一意性は 80bit の乱数が担い、同一ミリ秒に生成した
2 つの id の順序は未定義。台帳の順序は event log の並び順が正本なので、id の順序に依存しない。
"""

import os
import time

# Crockford base32 (I / L / O / U を除く 32 文字)。読み上げ・書き写しの誤りを避ける ULID 仕様
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

TIMESTAMP_BITS = 48
RANDOMNESS_BYTES = 10  # 80 bit
ULID_LENGTH = 26  # (48 + 80) bit を 5 bit ずつ


def encode_ulid(*, timestamp_ms, randomness):
    """48bit 時刻 + 80bit 乱数を 26 文字の ULID に符号化する (純関数)。"""
    if not 0 <= timestamp_ms < 2**TIMESTAMP_BITS:
        raise ValueError(f"ULID の時刻が 48bit に収まらない: {timestamp_ms}")
    if len(randomness) != RANDOMNESS_BYTES:
        raise ValueError(f"ULID の乱数は {RANDOMNESS_BYTES} byte 固定: {len(randomness)} byte")
    value = (timestamp_ms << (RANDOMNESS_BYTES * 8)) | int.from_bytes(randomness, "big")
    characters = []
    for _ in range(ULID_LENGTH):
        value, remainder = divmod(value, len(CROCKFORD_ALPHABET))
        characters.append(CROCKFORD_ALPHABET[remainder])
    return "".join(reversed(characters))


def _system_clock_ms():
    return int(time.time() * 1000)


def new_ulid(clock_ms=_system_clock_ms, entropy=os.urandom):
    """ULID を 1 つ発行する。時刻と乱数は差し替えられる (テストを実時刻に依存させない)。"""
    return encode_ulid(timestamp_ms=clock_ms(), randomness=entropy(RANDOMNESS_BYTES))
