"""语言适配器的公共类型与协议。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ToolchainInfo:
    """一次构建所用的工具链信息。"""

    adapter: str
    command: str
    version: str


@dataclass
class BuildResult:
    """一次构建尝试的结果。"""

    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    artifact: Path | None = None
    duration_ms: int = 0


@dataclass
class RunResult:
    """用户程序的一次运行结果。"""

    command: list[str]
    returncode: int
    duration_ms: int = 0


@dataclass
class AdapterCapabilities:
    """语言能力声明；未列出的能力视为不支持。"""

    native_build: bool = True
    rule_fix: bool = False
    model_fix: bool = False
    natural_language: bool = False
    run: bool = True
    extra: dict[str, bool] = field(default_factory=dict)


class LanguageAdapter(Protocol):
    """语言适配器：只负责调用真实工具链，不做任何改写。"""

    name: str
    extensions: tuple[str, ...]

    def toolchain(self) -> ToolchainInfo:
        """返回工具链信息；不可用时抛 ToolchainUnavailable。"""
        ...

    def build(self, source: Path, passthrough: Sequence[str], cwd: Path) -> BuildResult:
        """以原生方式构建，参数原样透传。"""
        ...

    def run(self, source: Path, build: BuildResult, args: Sequence[str], cwd: Path) -> RunResult:
        """运行构建产物或解释器。"""
        ...

    def capabilities(self) -> AdapterCapabilities:
        """能力声明。"""
        ...
