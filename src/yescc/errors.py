"""错误类型与退出码。

契约见 docs/adr/0005-exit-codes-and-report-contract.md。
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """yescc 自身退出码；100 及以上为保留区间。"""

    OK = 0
    USAGE = 100
    UNSUPPORTED = 101
    BUILD_FAILED = 102
    BUDGET = 103
    INTERNAL = 104


class FcompError(Exception):
    """带退出码的可预期错误。"""

    exit_code: ExitCode = ExitCode.INTERNAL

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UsageError(FcompError):
    """命令行用法错误。"""

    exit_code = ExitCode.USAGE


class UnsupportedInput(FcompError):
    """无法识别的语言或输入。"""

    exit_code = ExitCode.UNSUPPORTED


class ToolchainUnavailable(FcompError):
    """对应语言的工具链不可用：属于环境失败，不进入兜底。"""

    exit_code = ExitCode.BUILD_FAILED
