"""全局标识符工具。

Python 3.12 尚未内置 ``uuid.uuid7``，因此这里提供一个零第三方依赖实现。
后续若最低 Python 版本原生支持 UUIDv7，可以在不改变领域模型的情况下替换实现。
"""

from __future__ import annotations

import secrets
import time
import uuid


def uuid7() -> uuid.UUID:
    """生成符合 RFC 9562 布局的 UUIDv7。

    48 bit 时间戳 + version 7 + 随机段 + RFC variant。这里不自行实现严格单调扩展；
    对数据库主键已经足够，如未来需要进程内严格单调排序再通过 ADR 引入。
    """
    unix_ms = time.time_ns() // 1_000_000
    if unix_ms >= 1 << 48:
        raise OverflowError("Unix 时间戳超出 UUIDv7 48 位毫秒字段可表示范围")
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (unix_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)
