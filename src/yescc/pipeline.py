"""执行流水线：语言识别、原生构建、报告与退出码。

M1 只做原生直通：不修改源码、不调用模型。
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from yescc import __version__
from yescc.adapters import BuildResult, detect_language, get_adapter
from yescc.errors import ExitCode, UnsupportedInput, UsageError
from yescc.report import SCHEMA_VERSION, load_report, report_path, summarize, write_report
from yescc.util import (
    display_path,
    read_text_utf8,
    sha256_file,
    truncate,
)

Mode = Literal["build", "run"]


@dataclass
class Invocation:
    """一次命令调用的解析结果。"""

    mode: Mode
    source: Path | None = None
    lang: str | None = None
    passthrough: list[str] = field(default_factory=list)
    run_args: list[str] = field(default_factory=list)
    strict: bool = False
    json_output: bool = False
    no_cache: bool = False
    explain: bool = False


def _validate_source(source: Path | None) -> Path:
    if source is None:
        raise UsageError("缺少输入文件。用法见 `yescc --help`。")
    if not source.exists():
        raise UsageError(f"输入文件不存在：{source}")
    if not source.is_file():
        raise UsageError(f"输入不是普通文件：{source}")
    return source


def _read_and_check_encoding(source: Path) -> tuple[int, str]:
    try:
        _text, encoding = read_text_utf8(source)
    except UnicodeDecodeError as exc:
        raise UnsupportedInput(
            f"无法读取 {source}：v0.1 仅支持 UTF-8 编码（可带 BOM），请先转换编码。"
        ) from exc
    return source.stat().st_size, encoding


def _attempt_entry(build: BuildResult) -> dict[str, object]:
    return {
        "kind": "native",
        "round": 1,
        "command": build.command,
        "exit_code": build.returncode,
        "duration_ms": build.duration_ms,
        "stdout": truncate(build.stdout),
        "stderr": truncate(build.stderr),
    }


def _payload(
    *,
    argv: list[str],
    invocation: Invocation,
    language: str,
    source: Path,
    size: int,
    encoding: str,
    toolchain: dict[str, str],
    build: BuildResult,
    status: str,
    artifact: Path | None,
    total_ms: int,
    program_exit: int | None,
    cwd: Path,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "yescc", "version": __version__},
        "invocation": {
            "argv": argv,
            "mode": invocation.mode,
            "lang": language,
        },
        "input": {
            "path": display_path(source, cwd),
            "sha256": sha256_file(source),
            "bytes": size,
            "encoding": encoding,
        },
        "toolchain": toolchain,
        "diagnostics": [],
        "attempts": [_attempt_entry(build)],
        "result": {
            "status": status,
            "artifact": display_path(artifact, cwd) if artifact is not None else None,
            "program_exit": program_exit,
        },
        "timings": {"total_ms": total_ms, "toolchain_ms": build.duration_ms},
        "privacy": {"cloud_used": False, "uploaded_bytes": 0},
        "limits": {"sandboxed": False, "network_isolated": False},
    }


def _status_line(build: BuildResult, cwd: Path) -> str:
    """TTY 下的一行成功状态；管道下不输出，保持 gcc 式安静。"""
    parts = ["backend=native", "ai_calls=0"]
    if build.artifact is not None:
        parts.append(f"artifact={display_path(build.artifact, cwd)}")
    return "yescc: ok (" + ", ".join(parts) + ")"


def _print_failure(build: BuildResult, report_file: Path, strict: bool) -> None:
    if strict:
        if build.stdout:
            sys.stderr.write(build.stdout)
        if build.stderr:
            sys.stderr.write(build.stderr)
        return
    print(
        f"构建未完成（工具链退出码 {build.returncode}）。"
        f"诊断已保存：{report_file}；使用 --strict 查看原始输出。",
        file=sys.stderr,
    )


def execute(argv: list[str], invocation: Invocation) -> int:
    """执行一次构建或运行。"""
    cwd = Path.cwd()
    source = _validate_source(invocation.source)
    size, encoding = _read_and_check_encoding(source)
    language = detect_language(source, invocation.lang)
    adapter = get_adapter(language)
    toolchain_info = adapter.toolchain()

    started = time.perf_counter()
    build = adapter.build(source, invocation.passthrough, cwd)
    report_file = report_path(cwd, source)

    if invocation.mode == "run":
        if build.returncode != 0:
            total_ms = int((time.perf_counter() - started) * 1000)
            payload = _payload(
                argv=argv,
                invocation=invocation,
                language=language,
                source=source,
                size=size,
                encoding=encoding,
                toolchain={
                    "adapter": toolchain_info.adapter,
                    "command": toolchain_info.command,
                    "version": toolchain_info.version,
                },
                build=build,
                status="native_failed",
                artifact=None,
                total_ms=total_ms,
                program_exit=None,
                cwd=cwd,
            )
            write_report(report_file, payload)
            _print_failure(build, report_file, invocation.strict)
            return int(ExitCode.BUILD_FAILED)

        run = adapter.run(source, build, invocation.run_args, cwd)
        total_ms = int((time.perf_counter() - started) * 1000)
        payload = _payload(
            argv=argv,
            invocation=invocation,
            language=language,
            source=source,
            size=size,
            encoding=encoding,
            toolchain={
                "adapter": toolchain_info.adapter,
                "command": toolchain_info.command,
                "version": toolchain_info.version,
            },
            build=build,
            status="ran",
            artifact=build.artifact,
            total_ms=total_ms,
            program_exit=run.returncode,
            cwd=cwd,
        )
        write_report(report_file, payload)
        if sys.stderr.isatty() and not invocation.json_output:
            print(_status_line(build, cwd), file=sys.stderr)
        if invocation.strict and build.stderr:
            sys.stderr.write(build.stderr)
        return run.returncode

    status = "native_ok" if build.returncode == 0 else "native_failed"
    total_ms = int((time.perf_counter() - started) * 1000)
    payload = _payload(
        argv=argv,
        invocation=invocation,
        language=language,
        source=source,
        size=size,
        encoding=encoding,
        toolchain={
            "adapter": toolchain_info.adapter,
            "command": toolchain_info.command,
            "version": toolchain_info.version,
        },
        build=build,
        status=status,
        artifact=build.artifact if build.returncode == 0 else None,
        total_ms=total_ms,
        program_exit=None,
        cwd=cwd,
    )
    write_report(report_file, payload)

    if invocation.json_output:
        import json

        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif invocation.strict:
        if build.stdout:
            sys.stdout.write(build.stdout)
        if build.stderr:
            sys.stderr.write(build.stderr)
    elif build.returncode != 0:
        _print_failure(build, report_file, strict=False)
    elif sys.stderr.isatty():
        print(_status_line(build, cwd), file=sys.stderr)

    return int(ExitCode.OK) if build.returncode == 0 else int(ExitCode.BUILD_FAILED)


def explain(source: Path | None, as_json: bool) -> int:
    """读取最近一次报告，不调用模型。"""
    if source is None:
        raise UsageError("--explain 需要一个输入文件。")
    report_file = report_path(Path.cwd(), source)
    payload = load_report(report_file)
    if payload is None:
        print(f"没有找到报告：{report_file}。先运行一次构建再查看。", file=sys.stderr)
        return int(ExitCode.USAGE)
    if as_json:
        import json

        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(summarize(payload))
    return int(ExitCode.OK)
