"""候选修复与兜底程序的真实编译验证。"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from yescc.adapters.base import BuildResult, LanguageAdapter
from yescc.fix.c_rules import Candidate, generate_candidates


@dataclass
class RepairOutcome:
    """一次规则修复尝试的结果。"""

    attempts: list[tuple[str, BuildResult]]
    candidate: Candidate | None = None
    build: BuildResult | None = None
    artifact: Path | None = None


@dataclass
class TempBuild:
    """在隔离目录中构建一段源码的结果。"""

    build: BuildResult
    artifact: Path | None


def _output_argument(passthrough: Sequence[str]) -> str | None:
    for index, arg in enumerate(passthrough):
        if arg == "-o" and index + 1 < len(passthrough):
            return passthrough[index + 1]
        if arg.startswith("-o") and len(arg) > 2 and not arg.startswith("-O"):
            return arg[2:]
    return None


def _without_output(passthrough: Sequence[str]) -> list[str]:
    result: list[str] = []
    skip_next = False
    for arg in passthrough:
        if skip_next:
            skip_next = False
            continue
        if arg == "-o":
            skip_next = True
            continue
        if arg.startswith("-o") and len(arg) > 2 and not arg.startswith("-O"):
            continue
        result.append(arg)
    return result


def _destination(cwd: Path, passthrough: Sequence[str], artifact: Path) -> Path:
    output = _output_argument(passthrough)
    if output is not None:
        path = Path(output)
        return path if path.is_absolute() else cwd / path
    return cwd / artifact.name


def _resolve_paths(passthrough: Sequence[str], cwd: Path) -> list[str]:
    """内部临时构建仍按用户 cwd 解释相对搜索路径，原生参数不受影响。"""
    options = {"-I", "-L", "-iquote", "-isystem", "-idirafter", "-include", "-imacros"}
    result: list[str] = []
    pending = False
    for arg in passthrough:
        if pending:
            result.append(str((cwd / arg).resolve()))
            pending = False
        elif arg in options:
            result.append(arg)
            pending = True
        elif arg.startswith(("-I", "-L")) and len(arg) > 2:
            result.append(arg[:2] + str((cwd / arg[2:]).resolve()))
        else:
            result.append(arg)
    return result


def build_in_temp(
    adapter: LanguageAdapter,
    text: str,
    filename: str,
    passthrough: Sequence[str],
    cwd: Path,
    source_dir: Path,
) -> TempBuild:
    """把源码写到隔离目录、用真实工具链构建，成功后把产物复制回 cwd。"""
    with tempfile.TemporaryDirectory(prefix="yescc-build-") as raw_directory:
        directory = Path(raw_directory)
        for header in source_dir.glob("*.h"):
            shutil.copy2(header, directory / header.name)
        target = directory / filename
        target.write_text(text, encoding="utf-8")
        arguments = _resolve_paths(_without_output(passthrough), cwd)
        arguments.extend(["-iquote", str(source_dir.resolve())])
        build = adapter.build(target, arguments, directory)
        artifact: Path | None = None
        if build.returncode == 0 and build.artifact is not None:
            destination = _destination(cwd, passthrough, build.artifact)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(build.artifact, destination)
            artifact = destination
        return TempBuild(build=build, artifact=artifact)


def attempt_repair(
    adapter: LanguageAdapter,
    source_text: str,
    filename: str,
    passthrough: Sequence[str],
    cwd: Path,
    source_dir: Path,
) -> RepairOutcome:
    """按改动量从小到大验证规则候选；第一个通过真实编译的候选胜出。"""
    outcome = RepairOutcome(attempts=[])
    for candidate in generate_candidates(source_text):
        result = build_in_temp(adapter, candidate.text, filename, passthrough, cwd, source_dir)
        outcome.attempts.append(("rule", result.build))
        if result.build.returncode == 0:
            outcome.candidate = candidate
            outcome.build = result.build
            outcome.artifact = result.artifact
            break
    return outcome
