"""退出码约定。

100 及以上由 fcomp 保留，避免与用户程序自身退出码冲突。
契约见 docs/adr/0005-exit-codes-and-report-contract.md 与路线图 §6.5。
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """fcomp 自身退出码。"""

    OK = 0
    USAGE = 100
    UNSUPPORTED = 101
    BUILD_FAILED = 102
    BUDGET = 103
    INTERNAL = 104
