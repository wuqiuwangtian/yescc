"""JavaScript 适配器：node --check 做语法构建，node 运行。"""

from __future__ import annotations

import shutil
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
from yescc.util import run_capture, run_inherit


def _find_node() -> str:
    for candidate in ("node", "node.exe"):
        found = shutil.which(candidate)
        if found:
            return found
    raise ToolchainUnavailable("未找到 Node.js。请安装 Node.js 后重试。")


class JavaScriptAdapter:
    """通过 node --check 检查语法；不产生构建产物。"""

    name = "javascript"
    extensions: tuple[str, ...] = (".js", ".mjs", ".cjs")

    def toolchain(self) -> ToolchainInfo:
        executable = _find_node()
        version = "unknown"
        try:
            probe = run_capture([executable, "--version"], cwd=Path.cwd())
            if probe.returncode == 0 and probe.stdout.strip():
                version = probe.stdout.strip()
        except OSError:
            version = "unknown"
        return ToolchainInfo(adapter=self.name, command=executable, version=version)

    def build(self, source: Path, passthrough: Sequence[str], cwd: Path) -> BuildResult:
        if passthrough:
            raise UsageError(
                "JavaScript 构建模式不接受工具链参数；运行参数请使用 `yescc run <file> -- <args>`。"
            )
        started = time.perf_counter()
        command = [self.toolchain().command, "--check", str(source)]
        completed = run_capture(command, cwd=cwd)
        return BuildResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            artifact=None,
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
