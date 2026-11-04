"""执行流水线：原生直通、规则修复、确定性兜底与报告。

M2 只做确定性路径；模型修复与创意生成在 M3。
"""

from __future__ import annotations

import difflib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from yescc import __version__
from yescc.adapters import BuildResult, detect_language, get_adapter
from yescc.errors import (
    ExitCode,
    ToolchainUnavailable,
    UnsupportedInput,
    UsageError,
)
from yescc.fallback import fallback_output, generate_c_program
from yescc.fix import parse_c
from yescc.fix.engine import attempt_repair, build_in_temp
from yescc.report import SCHEMA_VERSION, load_report, report_path, summarize, write_report
from yescc.util import (
    display_path,
    read_text_utf8,
    run_inherit,
    sha256_file,
    truncate,
)

Mode = Literal["build", "run"]

_GCC_DIAGNOSTIC = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<column>\d+):\s*"
    r"(?P<severity>fatal error|error|warning|note):\s*(?P<message>.*)$"
)

_ENVIRONMENT_FAILURE = re.compile(
    r"no space left on device|permission denied|cannot open output file|"
    r"internal compiler error|fatal error:.*no such file or directory|"
    r"cannot find -l|undefined reference|is a directory",
    re.IGNORECASE,
)

_NON_EXECUTABLE_SUFFIXES = {".o", ".obj", ".s", ".asm", ".a", ".lib"}


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
    no_fallback: bool = False
    play: bool = False


def _validate_source(source: Path | None) -> Path:
    if source is None:
        raise UsageError("缺少输入文件。用法见 `yescc --help`。")
    if not source.exists():
        raise UsageError(f"输入文件不存在：{source}")
    if not source.is_file():
        raise UsageError(f"输入不是普通文件：{source}")
    return source


def _read_source(source: Path) -> tuple[int, str, str]:
    try:
        text, encoding = read_text_utf8(source)
    except UnicodeDecodeError as exc:
        raise UnsupportedInput(
            f"无法读取 {source}：v0.1 仅支持 UTF-8 编码（可带 BOM），请先转换编码。"
        ) from exc
    return source.stat().st_size, encoding, text


def _attempt(kind: str, round_number: int, build: BuildResult) -> dict[str, object]:
    return {
        "kind": kind,
        "round": round_number,
        "command": build.command,
        "exit_code": build.returncode,
        "duration_ms": build.duration_ms,
        "stdout": truncate(build.stdout),
        "stderr": truncate(build.stderr),
    }


def _gcc_diagnostics(stderr: str) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    for line in stderr.splitlines():
        match = _GCC_DIAGNOSTIC.match(line.strip())
        if match is None:
            continue
        diagnostics.append(
            {
                "path": match.group("path"),
                "line": int(match.group("line")),
                "column": int(match.group("column")),
                "severity": match.group("severity"),
                "message": match.group("message"),
            }
        )
    return diagnostics


def _is_environment_failure(stderr: str) -> bool:
    return _ENVIRONMENT_FAILURE.search(stderr) is not None


def _as_int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _as_list_len(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def _diff(original: str, patched: str, filename: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=filename,
            tofile=f"{filename}.fixed",
        )
    )


def _payload(
    *,
    argv: list[str],
    invocation: Invocation,
    language: str | None,
    source: Path,
    size: int,
    encoding: str,
    toolchain: dict[str, str],
    diagnostics: list[dict[str, object]],
    syntax_errors: list[dict[str, object]],
    attempts: list[dict[str, object]],
    status: str,
    artifact: Path | None,
    program_exit: int | None,
    repair: dict[str, object] | None,
    fallback: dict[str, object] | None,
    total_ms: int,
    cwd: Path,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "yescc", "version": __version__},
        "invocation": {
            "argv": argv,
            "mode": invocation.mode,
            "lang": language or "unknown",
            "strict": invocation.strict,
            "play": invocation.play,
        },
        "input": {
            "path": display_path(source, cwd),
            "sha256": sha256_file(source),
            "bytes": size,
            "encoding": encoding,
        },
        "toolchain": toolchain,
        "diagnostics": diagnostics,
        "syntax_errors": syntax_errors,
        "attempts": attempts,
        "result": {
            "status": status,
            "artifact": display_path(artifact, cwd) if artifact is not None else None,
            "program_exit": program_exit,
            "repair": repair,
            "fallback": fallback,
        },
        "timings": {
            "total_ms": total_ms,
            "toolchain_ms": sum(_as_int(item.get("duration_ms", 0)) for item in attempts),
        },
        "privacy": {"cloud_used": False, "uploaded_bytes": 0},
        "limits": {
            "sandboxed": status in {"repaired", "creative_fallback"},
            "network_isolated": False,
            "timeout_s": None,
        },
    }


def _status_line(
    status: str,
    artifact: Path | None,
    repair: dict[str, object] | None,
    fallback: dict[str, object] | None,
    cwd: Path,
) -> str:
    parts = ["backend=native", "ai_calls=0"]
    if repair is not None:
        parts.append(f"rules={_as_list_len(repair.get('rules'))}")
        parts.append(f"changed={repair.get('changed', 0)}")
    if fallback is not None:
        parts.append(f"fallback={fallback.get('kind', 'unknown')}")
    if artifact is not None:
        parts.append(f"artifact={display_path(artifact, cwd)}")
    return f"yescc: {status} (" + ", ".join(parts) + ")"


def _write_and_report(
    *,
    argv: list[str],
    invocation: Invocation,
    language: str | None,
    source: Path,
    size: int,
    encoding: str,
    toolchain: dict[str, str],
    diagnostics: list[dict[str, object]],
    syntax_errors: list[dict[str, object]],
    attempts: list[dict[str, object]],
    status: str,
    artifact: Path | None,
    program_exit: int | None,
    repair: dict[str, object] | None,
    fallback: dict[str, object] | None,
    total_ms: int,
    cwd: Path,
    failure_message: str | None = None,
    strict: bool = False,
) -> int:
    report_file = report_path(cwd, source)
    payload = _payload(
        argv=argv,
        invocation=invocation,
        language=language,
        source=source,
        size=size,
        encoding=encoding,
        toolchain=toolchain,
        diagnostics=diagnostics,
        syntax_errors=syntax_errors,
        attempts=attempts,
        status=status,
        artifact=artifact,
        program_exit=program_exit,
        repair=repair,
        fallback=fallback,
        total_ms=total_ms,
        cwd=cwd,
    )
    write_report(report_file, payload)

    if invocation.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return _exit_for_status(status)

    if failure_message is not None:
        if strict and attempts:
            last = attempts[-1]
            sys.stdout.write(str(last.get("stdout", "")))
            sys.stderr.write(str(last.get("stderr", "")))
        else:
            print(failure_message, file=sys.stderr)
            print(f"诊断已保存：{report_file}；使用 --strict 查看原始输出。", file=sys.stderr)
        return _exit_for_status(status)

    if fallback is not None:
        print(
            "yescc: 原始输入未能按语义完成，已生成创意兜底程序"
            f"（kind={fallback.get('kind', 'unknown')}，不是原程序的修复）。",
            file=sys.stderr,
        )
    if invocation.strict and attempts:
        last = attempts[-1]
        sys.stdout.write(str(last.get("stdout", "")))
        sys.stderr.write(str(last.get("stderr", "")))
    if artifact is not None and sys.stderr.isatty():
        print(_status_line(status, artifact, repair, fallback, cwd), file=sys.stderr)

    if program_exit is not None:
        return program_exit
    return _exit_for_status(status)


def _exit_for_status(status: str) -> int:
    if status in {"native_ok", "repaired", "creative_fallback"}:
        return int(ExitCode.OK)
    if status == "budget_exceeded":
        return int(ExitCode.BUDGET)
    return int(ExitCode.BUILD_FAILED)


def _run_artifact(artifact: Path | None, args: list[str], cwd: Path) -> int:
    if artifact is None or artifact.suffix.lower() in _NON_EXECUTABLE_SUFFIXES:
        raise UsageError("本次构建没有产生可执行文件，无法运行。")
    return run_inherit([str(artifact), *args], cwd=cwd)


def _fallback_flow(
    *,
    argv: list[str],
    invocation: Invocation,
    source: Path,
    text: str,
    size: int,
    encoding: str,
    cwd: Path,
    started: float,
    attempts: list[dict[str, object]],
    language: str | None,
    reason: str,
) -> int:
    try:
        adapter = get_adapter("c")
        info = adapter.toolchain()
    except ToolchainUnavailable as exc:
        return _write_and_report(
            argv=argv,
            invocation=invocation,
            language=language,
            source=source,
            size=size,
            encoding=encoding,
            toolchain={"adapter": language or "unknown", "command": "", "version": ""},
            diagnostics=[],
            syntax_errors=[],
            attempts=attempts,
            status="failed",
            artifact=None,
            program_exit=None,
            repair=None,
            fallback=None,
            total_ms=int((time.perf_counter() - started) * 1000),
            cwd=cwd,
            failure_message=f"构建失败（环境问题）：{exc.message}",
        )

    output = fallback_output(text)
    program = generate_c_program(output.message)
    result = build_in_temp(
        adapter, program, "fallback.c", invocation.passthrough, cwd, source.parent
    )
    attempts.append(_attempt("fallback", 1, result.build))

    if result.build.returncode != 0:
        return _write_and_report(
            argv=argv,
            invocation=invocation,
            language=language,
            source=source,
            size=size,
            encoding=encoding,
            toolchain={
                "adapter": info.adapter,
                "command": info.command,
                "version": info.version,
            },
            diagnostics=_gcc_diagnostics(result.build.stderr),
            syntax_errors=[],
            attempts=attempts,
            status="failed",
            artifact=None,
            program_exit=None,
            repair=None,
            fallback=None,
            total_ms=int((time.perf_counter() - started) * 1000),
            cwd=cwd,
            failure_message="兜底程序也未能构建成功；请检查 GCC 环境。",
        )

    directory = cwd / ".yescc" / source.stem
    directory.mkdir(parents=True, exist_ok=True)
    fallback_source = directory / "fallback.c"
    fallback_source.write_text(program, encoding="utf-8")

    program_exit: int | None = None
    if invocation.mode == "run":
        program_exit = _run_artifact(result.artifact, invocation.run_args, cwd)

    return _write_and_report(
        argv=argv,
        invocation=invocation,
        language=language,
        source=source,
        size=size,
        encoding=encoding,
        toolchain={
            "adapter": info.adapter,
            "command": info.command,
            "version": info.version,
        },
        diagnostics=[],
        syntax_errors=[],
        attempts=attempts,
        status="creative_fallback",
        artifact=result.artifact,
        program_exit=program_exit,
        repair=None,
        fallback={
            "kind": output.kind,
            "reason": reason,
            "source": display_path(fallback_source, cwd),
        },
        total_ms=int((time.perf_counter() - started) * 1000),
        cwd=cwd,
    )


def execute(argv: list[str], invocation: Invocation) -> int:
    """执行一次构建或运行。"""
    cwd = Path.cwd()
    source = _validate_source(invocation.source)
    size, encoding, text = _read_source(source)
    started = time.perf_counter()
    attempts: list[dict[str, object]] = []

    if invocation.play:
        return _fallback_flow(
            argv=argv,
            invocation=invocation,
            source=source,
            text=text,
            size=size,
            encoding=encoding,
            cwd=cwd,
            started=started,
            attempts=attempts,
            language=None,
            reason="用户请求 --play",
        )

    try:
        language = detect_language(source, invocation.lang)
    except UnsupportedInput:
        if invocation.strict or invocation.no_fallback:
            raise
        return _fallback_flow(
            argv=argv,
            invocation=invocation,
            source=source,
            text=text,
            size=size,
            encoding=encoding,
            cwd=cwd,
            started=started,
            attempts=attempts,
            language=None,
            reason="未识别的输入类型",
        )

    adapter = get_adapter(language)
    try:
        info = adapter.toolchain()
    except ToolchainUnavailable as exc:
        return _write_and_report(
            argv=argv,
            invocation=invocation,
            language=language,
            source=source,
            size=size,
            encoding=encoding,
            toolchain={"adapter": language, "command": "", "version": ""},
            diagnostics=[],
            syntax_errors=[],
            attempts=attempts,
            status="failed",
            artifact=None,
            program_exit=None,
            repair=None,
            fallback=None,
            total_ms=int((time.perf_counter() - started) * 1000),
            cwd=cwd,
            failure_message=f"构建失败（环境问题）：{exc.message}",
        )

    toolchain = {
        "adapter": info.adapter,
        "command": info.command,
        "version": info.version,
    }
    native = adapter.build(source, invocation.passthrough, cwd)
    attempts.append(_attempt("native", 1, native))
    diagnostics = _gcc_diagnostics(native.stderr) if language == "c" else []
    syntax_errors: list[dict[str, object]] = (
        [{"start": item.start, "end": item.end} for item in parse_c(text).errors]
        if language == "c"
        else []
    )

    if native.returncode == 0:
        program_exit: int | None = None
        if invocation.mode == "run":
            program_exit = adapter.run(source, native, invocation.run_args, cwd).returncode
        return _write_and_report(
            argv=argv,
            invocation=invocation,
            language=language,
            source=source,
            size=size,
            encoding=encoding,
            toolchain=toolchain,
            diagnostics=diagnostics,
            syntax_errors=syntax_errors,
            attempts=attempts,
            status="native_ok",
            artifact=native.artifact,
            program_exit=program_exit,
            repair=None,
            fallback=None,
            total_ms=int((time.perf_counter() - started) * 1000),
            cwd=cwd,
        )

    if invocation.strict:
        return _write_and_report(
            argv=argv,
            invocation=invocation,
            language=language,
            source=source,
            size=size,
            encoding=encoding,
            toolchain=toolchain,
            diagnostics=diagnostics,
            syntax_errors=syntax_errors,
            attempts=attempts,
            status="native_failed",
            artifact=None,
            program_exit=None,
            repair=None,
            fallback=None,
            total_ms=int((time.perf_counter() - started) * 1000),
            cwd=cwd,
            failure_message="构建未完成。",
            strict=True,
        )

    if _is_environment_failure(native.stderr):
        return _write_and_report(
            argv=argv,
            invocation=invocation,
            language=language,
            source=source,
            size=size,
            encoding=encoding,
            toolchain=toolchain,
            diagnostics=diagnostics,
            syntax_errors=syntax_errors,
            attempts=attempts,
            status="failed",
            artifact=None,
            program_exit=None,
            repair=None,
            fallback=None,
            total_ms=int((time.perf_counter() - started) * 1000),
            cwd=cwd,
            failure_message="构建失败（环境或依赖问题）；没有进入创意兜底。",
        )

    repair_info: dict[str, object] | None = None
    if language == "c":
        outcome = attempt_repair(
            adapter, text, source.name, invocation.passthrough, cwd, source.parent
        )
        for index, (_, build) in enumerate(outcome.attempts, start=2):
            attempts.append(_attempt("rule", index, build))
        if outcome.candidate is not None and outcome.build is not None:
            directory = cwd / ".yescc" / source.stem
            directory.mkdir(parents=True, exist_ok=True)
            patched_source = directory / source.name
            patched_source.write_text(outcome.candidate.text, encoding="utf-8")
            diff_file = directory / "patch.diff"
            diff_file.write_text(
                _diff(text, outcome.candidate.text, source.name), encoding="utf-8"
            )
            repair_info = {
                "rules": list(outcome.candidate.rules),
                "changed": outcome.candidate.changed,
                "candidate_count": len(outcome.attempts),
                "patched_source": display_path(patched_source, cwd),
                "patch": display_path(diff_file, cwd),
            }
            program_exit = None
            if invocation.mode == "run":
                program_exit = _run_artifact(outcome.artifact, invocation.run_args, cwd)
            return _write_and_report(
                argv=argv,
                invocation=invocation,
                language=language,
                source=source,
                size=size,
                encoding=encoding,
                toolchain=toolchain,
                diagnostics=diagnostics,
                syntax_errors=syntax_errors,
                attempts=attempts,
                status="repaired",
                artifact=outcome.artifact,
                program_exit=program_exit,
                repair=repair_info,
                fallback=None,
                total_ms=int((time.perf_counter() - started) * 1000),
                cwd=cwd,
            )

    if invocation.no_fallback:
        return _write_and_report(
            argv=argv,
            invocation=invocation,
            language=language,
            source=source,
            size=size,
            encoding=encoding,
            toolchain=toolchain,
            diagnostics=diagnostics,
            syntax_errors=syntax_errors,
            attempts=attempts,
            status="native_failed",
            artifact=None,
            program_exit=None,
            repair=None,
            fallback=None,
            total_ms=int((time.perf_counter() - started) * 1000),
            cwd=cwd,
            failure_message=f"构建未完成（工具链退出码 {native.returncode}）。",
        )

    return _fallback_flow(
        argv=argv,
        invocation=invocation,
        source=source,
        text=text,
        size=size,
        encoding=encoding,
        cwd=cwd,
        started=started,
        attempts=attempts,
        language=language,
        reason="原始构建与规则修复均未成功",
    )


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
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(summarize(payload))
    return int(ExitCode.OK)
