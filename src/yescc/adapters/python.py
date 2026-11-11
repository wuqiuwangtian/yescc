"""Python 适配器：用 py_compile 做语法构建，用当前解释器运行。"""

from __future__ import annotations

import importlib.util
import json
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
from yescc.errors import ToolchainUnavailable, UsageError
from yescc.util import is_frozen, python_executable, run_capture, run_inherit


class PythonAdapter:
    """调用标准库 py_compile 检查语法；不改变源码。"""

    name = "python"
    extensions: tuple[str, ...] = (".py",)

    def __init__(self) -> None:
        self._frozen_toolchain: ToolchainInfo | None = None

    def toolchain(self) -> ToolchainInfo:
        if is_frozen():
            if self._frozen_toolchain is not None:
                return self._frozen_toolchain
            executable = python_executable()
            result = run_capture(
                [executable, "-I", "-c", "import platform; print(platform.python_version())"],
                cwd=Path.cwd(),
            )
            if result.returncode != 0 or not result.stdout.strip():
                raise ToolchainUnavailable("无法取得外部 Python 3 的版本。")
            self._frozen_toolchain = ToolchainInfo(
                adapter=self.name, command=executable, version=result.stdout.strip()
            )
            return self._frozen_toolchain
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
        executable = self.toolchain().command
        command = [executable, "-m", "py_compile", str(source)]
        completed = run_capture(command, cwd=cwd)
        cache_path: Path | None = None
        if is_frozen() and completed.returncode == 0:
            # 必须由同一外部解释器计算，包含它的 cache tag / optimize / pycache_prefix。
            probe = run_capture(
                [
                    executable,
                    "-c",
                    "import importlib.util,json,sys; "
                    "print(json.dumps(importlib.util.cache_from_source(sys.argv[1])))",
                    str(source),
                ],
                cwd=cwd,
            )
            if probe.returncode == 0:
                try:
                    reported = json.loads(probe.stdout.strip().splitlines()[-1])
                    if isinstance(reported, str):
                        cache_path = Path(reported)
                except (ValueError, IndexError):
                    pass
        elif not is_frozen():
            cache_path = Path(importlib.util.cache_from_source(str(source)))
        artifact = (
            cache_path
            if cache_path is not None and cache_path.exists() and completed.returncode == 0
            else None
        )
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
        command = [self.toolchain().command, str(source), *args]
        returncode = run_inherit(command, cwd=cwd)
        return RunResult(
            command=command,
            returncode=returncode,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(native_build=True, run=True)
