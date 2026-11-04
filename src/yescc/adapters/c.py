"""C 语言适配器：直接调用 gcc，参数原样透传。"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from yescc.adapters.base import (
    AdapterCapabilities,
    BuildResult,
    RunResult,
    ToolchainInfo,
)
from yescc.errors import ToolchainUnavailable, UsageError
from yescc.util import run_capture

_OBJECT_SUFFIXES = {".o", ".obj", ".s", ".asm"}


def _find_gcc() -> str:
    for candidate in ("gcc", "gcc.exe", "cc"):
        found = shutil.which(candidate)
        if found:
            return found
    raise ToolchainUnavailable(
        "未找到 gcc。请安装 GCC（Linux: 发行版包管理器；Windows: MSYS2/MinGW-w64）后重试。"
    )


def _output_argument(passthrough: Sequence[str]) -> str | None:
    for index, arg in enumerate(passthrough):
        if arg == "-o" and index + 1 < len(passthrough):
            return passthrough[index + 1]
        if arg.startswith("-o") and len(arg) > 2 and not arg.startswith("-O"):
            return arg[2:]
    return None


def _default_artifact(source: Path, passthrough: Sequence[str], cwd: Path) -> Path | None:
    if "-fsyntax-only" in passthrough:
        return None
    output = _output_argument(passthrough)
    if output is not None:
        path = Path(output)
        return path if path.is_absolute() else cwd / path
    if any(flag in passthrough for flag in ("-E", "-M", "-MM")):
        return None
    if "-c" in passthrough:
        return cwd / f"{source.stem}.o"
    if "-S" in passthrough:
        return cwd / f"{source.stem}.s"
    return cwd / ("a.exe" if os.name == "nt" else "a.out")


class CAdapter:
    """通过 gcc 构建 C 单文件程序。"""

    name = "c"
    extensions: tuple[str, ...] = (".c",)

    def __init__(self) -> None:
        self._cached: ToolchainInfo | None = None

    def toolchain(self) -> ToolchainInfo:
        if self._cached is not None:
            return self._cached
        executable = _find_gcc()
        version = "unknown"
        try:
            probe = run_capture([executable, "-dumpfullversion"], cwd=Path.cwd())
            if probe.returncode == 0 and probe.stdout.strip():
                version = probe.stdout.strip()
        except OSError:
            version = "unknown"
        self._cached = ToolchainInfo(adapter=self.name, command=executable, version=version)
        return self._cached

    def build(self, source: Path, passthrough: Sequence[str], cwd: Path) -> BuildResult:
        import time

        started = time.perf_counter()
        command = [self.toolchain().command, str(source), *passthrough]
        completed = run_capture(command, cwd=cwd)
        predicted = _default_artifact(source, passthrough, cwd)
        artifact = (
            predicted
            if completed.returncode == 0 and predicted is not None and predicted.exists()
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
        artifact = build.artifact
        if artifact is None or artifact.suffix.lower() in _OBJECT_SUFFIXES:
            raise UsageError("本次构建没有产生可执行文件，无法运行。")
        import time

        started = time.perf_counter()
        from yescc.util import run_inherit

        returncode = run_inherit([str(artifact), *args], cwd=cwd)
        return RunResult(
            command=[str(artifact), *args],
            returncode=returncode,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(native_build=True, run=True)
