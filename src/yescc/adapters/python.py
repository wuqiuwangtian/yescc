"""Python 适配器：用 py_compile 做语法构建，用当前解释器运行。"""

from __future__ import annotations

import importlib.util
import platform
import time
from collections.abc import Sequence
from pathlib import Path

from yescc.adapters.base import (
    AdapterCapabilities,
    BuildResult,
    RunResult,
    ToolchainInfo,
)
from yescc.errors import UsageError
from yescc.util import python_executable, run_capture, run_inherit


class PythonAdapter:
    """调用标准库 py_compile 检查语法；不改变源码。"""

    name = "python"
    extensions: tuple[str, ...] = (".py",)

    def toolchain(self) -> ToolchainInfo:
        return ToolchainInfo(
            adapter=self.name,
            command=python_executable(),
            version=platform.python_version(),
        )

    def build(self, source: Path, passthrough: Sequence[str], cwd: Path) -> BuildResult:
        if passthrough:
            raise UsageError(
                "Python 构建模式不接受工具链参数；运行参数请使用 `yescc run <file> -- <args>`。"
            )
        started = time.perf_counter()
        command = [python_executable(), "-m", "py_compile", str(source)]
        completed = run_capture(command, cwd=cwd)
        cache_path = Path(importlib.util.cache_from_source(str(source)))
        artifact = cache_path if cache_path.exists() and completed.returncode == 0 else None
        return BuildResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            artifact=artifact,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def run(self, source: Path, build: BuildResult, args: Sequence[str], cwd: Path) -> RunResult:
        started = time.perf_counter()
        command = [python_executable(), str(source), *args]
        returncode = run_inherit(command, cwd=cwd)
        return RunResult(
            command=command,
            returncode=returncode,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(native_build=True, run=True)
