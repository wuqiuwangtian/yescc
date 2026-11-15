"""执行流水线：原生直通、规则修复、模型修复/生成与创意兜底。"""

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
from yescc.adapters.base import LanguageAdapter
from yescc.errors import (
    ExitCode,
    ToolchainUnavailable,
    UnsupportedInput,
    UsageError,
)
from yescc.fallback import fallback_output, generate_c_program
from yescc.fallback.guidance import degradation_guidance
from yescc.fallback.quality import creative_source_issues
from yescc.fix import parse_c
from yescc.fix.engine import _output_argument, _same_file, attempt_repair, build_in_temp
from yescc.model import (
    BudgetExceeded,
    ModelError,
    ModelSession,
    ModelStats,
)
from yescc.model.base import ModelConfigurationError
from yescc.report import SCHEMA_VERSION, load_report, report_path, summarize, write_report
from yescc.sandbox import SandboxOutcome, run_ai_command, run_creative_command
from yescc.sandbox.runner import default_timeout
from yescc.sandbox.undefined import (
    UndefinedProfile,
    select_undefined_profile,
    validate_undefined_options,
)
from yescc.util import (
    display_path,
    read_text_utf8,
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
    r"cannot find -l|is a directory",
    re.IGNORECASE,
)

_NON_EXECUTABLE_SUFFIXES = {".o", ".obj", ".s", ".asm", ".a", ".lib"}

_SPEC_HINTS = (
    "写",
    "实现",
    "计算",
    "输出",
    "输入",
    "程序",
    "函数",
    "排序",
    "最大",
    "最小",
    "统计",
    "读取",
    "生成",
    "game",
    "program",
    "write",
    "calculate",
    "print",
    "input",
    "read",
    "sort",
)


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
    offline: bool = False
    budget_usd: float = 0.05
    max_rounds: int = 2
    provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    thinking: str | None = None
    max_output_tokens: int | None = None
    emit_source: bool = False
    trace: bool = False
    unsafe_run: bool = False
    check_undefined: bool = False


@dataclass
class Context:
    """一次任务的共享状态。"""

    argv: list[str]
    invocation: Invocation
    source: Path
    size: int
    encoding: str
    text: str
    cwd: Path
    started: float
    attempts: list[dict[str, object]] = field(default_factory=list)
    diagnostics: list[dict[str, object]] = field(default_factory=list)
    syntax_errors: list[dict[str, object]] = field(default_factory=list)
    toolchain: dict[str, str] = field(
        default_factory=lambda: {"adapter": "unknown", "command": "", "version": ""}
    )
    language: str | None = None
    session: ModelSession | None = None
    undefined_profile: UndefinedProfile | None = None
    undefined_check: dict[str, object] | None = None


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


def _changed_chars(original: str, patched: str) -> int:
    common = min(len(original), len(patched))
    diff = sum(1 for index in range(common) if original[index] != patched[index])
    return diff + abs(len(original) - len(patched))


def _diff(original: str, patched: str, filename: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=filename,
            tofile=f"{filename}.fixed",
        )
    )


def _compiler_feedback(build: BuildResult) -> str:
    """模型重试不依赖随机构建目录，保证同一诊断可复用缓存。"""
    if len(build.command) < 2:
        return build.stderr
    directory = Path(build.command[1]).parent
    return build.stderr.replace(str(directory), "<yescc-build>").replace(
        directory.as_posix(), "<yescc-build>"
    )


def _looks_like_spec(text: str) -> bool:
    stripped = text.strip()
    return len(stripped) >= 4 and any(hint in stripped.lower() for hint in _SPEC_HINTS)


def _trace(ctx: Context, message: str) -> None:
    if ctx.invocation.trace:
        print(f"yescc: trace {message}", file=sys.stderr)


def _status_line(
    status: str,
    artifact: Path | None,
    repair: dict[str, object] | None,
    fallback: dict[str, object] | None,
    cwd: Path,
    ai_calls: int,
) -> str:
    parts = ["backend=native", f"ai_calls={ai_calls}"]
    if repair is not None:
        parts.append(f"rules={_as_list_len(repair.get('rules'))}")
        parts.append(f"changed={repair.get('changed', 0)}")
    if fallback is not None:
        parts.append(f"fallback={fallback.get('kind', 'unknown')}")
    if artifact is not None:
        parts.append(f"artifact={display_path(artifact, cwd)}")
    return f"yescc: {status} (" + ", ".join(parts) + ")"


def _exit_for_status(status: str) -> int:
    if status in {"native_ok", "repaired", "generated", "creative_fallback"}:
        return int(ExitCode.OK)
    if status == "budget_exceeded":
        return int(ExitCode.BUDGET)
    return int(ExitCode.BUILD_FAILED)


def _run_program(ctx: Context, artifact: Path | None) -> tuple[int, SandboxOutcome]:
    if artifact is None or artifact.suffix.lower() in _NON_EXECUTABLE_SUFFIXES:
        raise UsageError("本次构建没有产生可执行文件，无法运行。")
    outcome = run_ai_command(
        [str(artifact), *ctx.invocation.run_args],
        unsafe=ctx.invocation.unsafe_run,
        cwd=ctx.cwd,
    )
    return outcome.exit_code, outcome


def _finalize(
    ctx: Context,
    *,
    status: str,
    artifact: Path | None = None,
    program_exit: int | None = None,
    repair: dict[str, object] | None = None,
    generated: dict[str, object] | None = None,
    fallback: dict[str, object] | None = None,
    sandbox: SandboxOutcome | None = None,
    emitted_source: str | None = None,
    failure_message: str | None = None,
    strict: bool = False,
    failure_code: ExitCode | None = None,
) -> int:
    total_ms = int((time.perf_counter() - ctx.started) * 1000)
    model_stats = ctx.session.stats if ctx.session is not None else ModelStats()
    build_exit = int(failure_code) if failure_code is not None else (
        int(ExitCode.BUDGET)
        if status == "failed" and ctx.session is not None and ctx.session.budget_reason
        else _exit_for_status(status)
    )
    model_payload = (
        model_stats.as_dict()
        if (model_stats.calls or model_stats.cache_hits or model_stats.errors)
        else None
    )
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "yescc", "version": __version__},
        "invocation": {
            "argv": ctx.argv,
            "mode": ctx.invocation.mode,
            "lang": ctx.language or "unknown",
            "strict": ctx.invocation.strict,
            "play": ctx.invocation.play,
            "offline": ctx.invocation.offline,
        },
        "input": {
            "path": display_path(ctx.source, ctx.cwd),
            "sha256": sha256_file(ctx.source),
            "bytes": ctx.size,
            "encoding": ctx.encoding,
        },
        "toolchain": ctx.toolchain,
        "diagnostics": ctx.diagnostics,
        "syntax_errors": ctx.syntax_errors,
        "attempts": ctx.attempts,
        "model": model_payload,
        "result": {
            "status": status,
            "artifact": display_path(artifact, ctx.cwd) if artifact is not None else None,
            "program_exit": program_exit,
            "repair": repair,
            "generated": generated,
            "fallback": fallback,
            "timed_out": sandbox.timed_out if sandbox is not None else False,
            "emitted_source": emitted_source if ctx.invocation.emit_source else None,
        },
        "timings": {
            "total_ms": total_ms,
            "toolchain_ms": sum(_as_int(item.get("duration_ms", 0)) for item in ctx.attempts),
            "model_ms": model_stats.duration_ms,
        },
        "privacy": {
            "cloud_used": model_stats.cloud_used,
            "uploaded_bytes": model_stats.uploaded_bytes,
        },
        "budget": (
            {
                "limit_usd": ctx.session.budget.max_usd,
                "committed_usd": ctx.session.budget.spent_usd,
                "attempts": ctx.session.attempts,
                "blocked_reason": ctx.session.budget_reason,
            }
            if ctx.session is not None
            else None
        ),
        "limits": (
            {
                "sandboxed": sandbox.sandboxed,
                "network_isolated": sandbox.network_isolated,
                "timeout_s": sandbox.timeout_s,
                "environment_filtered": sandbox.environment_filtered,
                "process_tree_cleanup": sandbox.process_tree_cleanup,
                "resource_limits": sandbox.resource_limits,
                "filesystem_isolated": False,
                "output_limited": sandbox.output_limited,
            }
            if sandbox is not None
            else {
                "sandboxed": False,
                "network_isolated": False,
                "timeout_s": None,
                "environment_filtered": False,
                "process_tree_cleanup": "none",
                "resource_limits": "none",
                "filesystem_isolated": False,
                "output_limited": False,
            }
        ),
    }
    if ctx.invocation.check_undefined:
        payload["undefined_behavior_check"] = ctx.undefined_check or {
            "requested": True, "scope": "model_creative", "performed": False,
            "available": None, "mode": None, "flags": [], "probes": [],
            "reason": "not_model_creative", "ub_detected": None,
        }
    report_file = report_path(ctx.cwd, ctx.source)
    write_report(report_file, payload)

    if ctx.invocation.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return program_exit if program_exit is not None else build_exit

    if ctx.invocation.emit_source and emitted_source is not None:
        stream = sys.stdout if ctx.invocation.mode == "build" else sys.stderr
        stream.write(emitted_source)
        if not emitted_source.endswith("\n"):
            stream.write("\n")

    if failure_message is not None:
        # strict 的完整原生输出已在 execute 中转发，不回放报告中的截断文本。
        if not strict:
            print(failure_message, file=sys.stderr)
            print(f"诊断已保存：{report_file}；使用 --strict 查看原始输出。", file=sys.stderr)
        return build_exit

    if fallback is not None:
        print(
            "yescc: 原始输入未能按语义完成，已生成创意兜底程序"
            f"（kind={fallback.get('kind', 'unknown')}，不是原程序的修复）。",
            file=sys.stderr,
        )
        guidance = fallback.get("guidance")
        if isinstance(guidance, list):
            for hint in guidance:
                print(f"yescc: {hint}", file=sys.stderr)
    if artifact is not None and sys.stderr.isatty():
        line = _status_line(status, artifact, repair, fallback, ctx.cwd, model_stats.calls)
        print(line, file=sys.stderr)

    return program_exit if program_exit is not None else build_exit


def _write_patched(
    ctx: Context,
    patched_source: str,
    diff_text: str,
    *,
    prefix: str = "",
) -> tuple[Path, Path]:
    directory = ctx.cwd / ".yescc" / ctx.source.stem
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}{ctx.source.name}" if prefix else ctx.source.name
    patched = directory / name
    patched.write_text(patched_source, encoding="utf-8", newline="")
    patch_file = directory / "patch.diff"
    patch_file.write_text(diff_text, encoding="utf-8", newline="")
    return patched, patch_file


def _model_repair(
    ctx: Context,
    adapter: LanguageAdapter,
    native: BuildResult,
) -> int | None:
    session = ctx.session
    if session is None or not session.available or ctx.language != "c":
        return None
    session.toolchain = ctx.toolchain
    try:
        outcome = session.repair(ctx.text, ctx.source.name, native.stderr)
    except ModelConfigurationError as exc:
        raise UsageError(str(exc)) from exc
    except (ModelError, BudgetExceeded) as exc:
        if isinstance(exc, BudgetExceeded):
            session.budget_reason = str(exc)
        session.stats.errors.append(str(exc))
        _trace(ctx, f"model repair skipped: {exc}")
        return None
    if outcome is None:
        _trace(ctx, "model repair: no usable diff")
        return None

    round_number = 1
    while round_number <= ctx.invocation.max_rounds:
        result = build_in_temp(
            adapter,
            outcome.patched_source,
            ctx.source.name,
            ctx.invocation.passthrough,
            ctx.cwd,
            ctx.source.parent,
        )
        ctx.attempts.append(_attempt("model", round_number, result.build))
        _trace(
            ctx,
            f"model repair build round={round_number} exit={result.build.returncode}",
        )
        if result.build.returncode == 0:
            patched, patch_file = _write_patched(
                ctx,
                outcome.patched_source,
                _diff(ctx.text, outcome.patched_source, ctx.source.name),
            )
            repair = {
                "rules": ["model"],
                "changed": _changed_chars(ctx.text, outcome.patched_source),
                "candidate_count": round_number,
                "patched_source": display_path(patched, ctx.cwd),
                "patch": display_path(patch_file, ctx.cwd),
                "assumptions": outcome.assumptions,
                "patch_normalizations": outcome.normalizations,
                "raw_diff": outcome.diff,
            }
            sandbox: SandboxOutcome | None = None
            program_exit: int | None = None
            if ctx.invocation.mode == "run":
                program_exit, sandbox = _run_program(ctx, result.artifact)
            return _finalize(
                ctx,
                status="repaired",
                sandbox=sandbox,
                artifact=result.artifact,
                program_exit=program_exit,
                repair=repair,
                emitted_source=outcome.patched_source,
            )
        try:
            session.discard_response()
            outcome = session.repair(
                ctx.text,
                ctx.source.name,
                native.stderr,
                feedback=_compiler_feedback(result.build),
            )
        except ModelConfigurationError as exc:
            raise UsageError(str(exc)) from exc
        except (ModelError, BudgetExceeded) as exc:
            if isinstance(exc, BudgetExceeded):
                session.budget_reason = str(exc)
            session.stats.errors.append(str(exc))
            _trace(ctx, f"model repair retry skipped: {exc}")
            return None
        if outcome is None:
            return None
        round_number += 1
    return None


def _model_generate(
    ctx: Context,
    adapter: LanguageAdapter,
    *,
    creative: bool,
    reason: str,
) -> int | None:
    session = ctx.session
    if session is None or not session.available:
        return None
    session.toolchain = ctx.toolchain
    check_requested = (
        creative and ctx.invocation.mode == "run" and ctx.invocation.check_undefined
    )
    profile = ctx.undefined_profile if check_requested else None
    if check_requested and session.attempts < ctx.invocation.max_rounds:
        validate_undefined_options(ctx.invocation.passthrough)
        output = _output_argument(ctx.invocation.passthrough)
        if output is not None and _same_file(ctx.cwd / output, ctx.source):
            raise UsageError("检查产物的 -o 路径不能覆盖原始输入。")
        if session.budget.remaining_s <= 0:
            session.budget_reason = "已达到任务墙钟上限，未执行检查能力探针"
            return None
        if profile is None:
            profile = select_undefined_profile(
                adapter, ctx.invocation.passthrough, ctx.cwd,
                timeout_s=min(20.0, session.budget.remaining_s),
            )
            ctx.undefined_profile = profile
            ctx.undefined_check = {
                **profile.as_dict(), "requested": True, "scope": "model_creative",
                "performed": False, "ub_detected": None,
                "capability_reason": profile.reason,
                "reason": "candidate_not_run" if profile.available else "unavailable",
            }
        if not profile.available:
            return _finalize(
                ctx, status="failed",
                failure_code=ExitCode.BUILD_FAILED,
                failure_message=(
                    "请求的未定义行为检查不可用（runtime/trap 均未通过能力探针）；"
                    "未请求新的创意候选。可移除 --check-undefined 使用普通运行。"
                ),
            )
        session.creative_validation_profile = profile.identity()
    feedback = ""
    round_number = 0
    while session.attempts < ctx.invocation.max_rounds:
        before = session.attempts
        try:
            program = session.generate(ctx.text, creative=creative, feedback=feedback)
        except ModelConfigurationError as exc:
            raise UsageError(str(exc)) from exc
        except BudgetExceeded as exc:
            session.budget_reason = str(exc)
            session.stats.errors.append(str(exc))
            return None
        except ModelError as exc:
            session.stats.errors.append(str(exc))
            _trace(ctx, f"model generate rejected: {exc}")
            # Invalid configuration makes no request/attempt; never spin on it.
            if session.attempts == before:
                return None
            session.discard_response()
            feedback = str(exc)
            continue
        if program is None:
            if session.attempts == before:
                return None
            session.discard_response()
            feedback = "The response did not contain a nonempty complete C source."
            continue
        round_number += 1
        if profile is not None and session.budget.remaining_s <= 0:
            session.discard_response()
            session.budget_reason = "已达到任务墙钟上限，未编译检查候选"
            return None
        compile_timeout = (
            min(20.0, session.budget.remaining_s) if profile is not None else None
        )
        result = build_in_temp(
            adapter,
            program.source,
            "generated.c",
            ctx.invocation.passthrough,
            ctx.cwd,
            ctx.source.parent,
            link_math=True,
            extra_flags=profile.flags if profile is not None else (),
            compile_timeout_s=compile_timeout,
            protected_source=ctx.source,
        )
        attempt = _attempt("model", round_number, result.build)
        candidate_check: dict[str, object] | None = None
        if profile is not None:
            candidate_check = {
                "requested": True, "scope": "model_creative", "available": True,
                "mode": profile.mode, "flags": list(profile.flags), "performed": False,
                "reason": "candidate_not_run" if result.build.returncode == 0 else "build_failed",
                "compile_timeout_s": compile_timeout,
                "diagnostic_observed": False, "stderr": "", "ub_detected": None,
            }
            attempt["undefined_behavior_check"] = candidate_check
        ctx.attempts.append(attempt)
        if profile is not None and result.build.returncode == 130:
            session.discard_response()
            return _finalize(ctx, status="failed", program_exit=130)
        _trace(
            ctx,
            f"model generate build round={round_number} exit={result.build.returncode}",
        )
        if result.build.returncode == 0:
            source_checks: dict[str, object] | None = None
            if creative:
                issues = creative_source_issues(program.source)
                source_checks = {"performed": True, "passed": not issues, "reasons": issues}
                attempt["source_checks"] = source_checks
                if issues:
                    guidance = {
                        "mixed_stdout_orientation": (
                            "Creative output mixes wide and narrow stdout functions. "
                            "Use UTF-8 byte strings with printf/puts only; mixing wprintf "
                            "and printf/puts can silently lose output."
                        ),
                        "non_ascii_character_literal": (
                            "An ordinary C character constant contains a non-ASCII symbol. "
                            "Use a double-quoted UTF-8 string and print it with printf/puts; "
                            "use an array of complete strings when repeating symbols."
                        ),
                    }
                    feedback = " ".join(guidance[issue] for issue in issues)
                    session.stats.errors.append("creative source rejected: " + ", ".join(issues))
                    session.discard_response()
                    continue
            sandbox: SandboxOutcome | None = None
            program_exit: int | None = None
            validation: dict[str, object] = {
                "performed": False, "passed": None, "reason": "build_only",
            }
            if ctx.invocation.mode == "run":
                if creative:
                    if (
                        result.artifact is None
                        or result.artifact.suffix.lower() in _NON_EXECUTABLE_SUFFIXES
                    ):
                        raise UsageError("本次构建没有产生可执行文件，无法运行。")
                    run_timeout = None
                    if profile is not None:
                        remaining = session.budget.remaining_s
                        if remaining <= 0:
                            session.discard_response()
                            session.budget_reason = "已达到任务墙钟上限，未执行检查候选"
                            if candidate_check is not None:
                                candidate_check["reason"] = "task_deadline"
                            return None
                        run_timeout = min(default_timeout(), remaining)
                    executed = run_creative_command(
                        [str(result.artifact), *ctx.invocation.run_args],
                        unsafe=ctx.invocation.unsafe_run,
                        cwd=ctx.cwd,
                        timeout_s=run_timeout,
                    )
                    sandbox = executed.sandbox
                    program_exit = sandbox.exit_code
                    validation = executed.validation
                    if profile is not None and program_exit == 130:
                        session.discard_response()
                    if candidate_check is not None:
                        stderr_text = executed.stderr.decode("utf-8", errors="replace")
                        candidate_check.update({
                            "performed": True,
                            "reason": "executed",
                            "diagnostic_observed": bool(
                                profile is not None and profile.mode == "runtime"
                                and re.search(r":\d+(?::\d+)?: runtime error:", stderr_text)
                            ),
                            "stderr": truncate(stderr_text),
                        })
                        assert ctx.undefined_check is not None
                        ctx.undefined_check["performed"] = True
                        ctx.undefined_check["reason"] = "candidate_executed"
                    attempt["validation"] = validation
                    attempt["validation_limits"] = {
                        "sandboxed": sandbox.sandboxed,
                        "network_isolated": sandbox.network_isolated,
                        "timeout_s": sandbox.timeout_s,
                        "environment_filtered": sandbox.environment_filtered,
                        "process_tree_cleanup": sandbox.process_tree_cleanup,
                        "resource_limits": sandbox.resource_limits,
                        "output_limited": sandbox.output_limited,
                    }
                    if not validation["passed"] and program_exit != 130:
                        feedback = (
                            "The creative program failed its actual run: "
                            + json.dumps(validation, ensure_ascii=False)
                            + ". Produce visible valid UTF-8 output; exit 0 within limits."
                        )
                        if candidate_check is not None:
                            diagnostic = executed.stderr.decode("utf-8", errors="replace")
                            diagnostic = diagnostic.replace(
                                str(Path(result.build.command[1]).parent), "<yescc-build>"
                            ).replace(
                                Path(result.build.command[1]).parent.as_posix(), "<yescc-build>"
                            )
                            feedback += (
                                " Requested undefined-behavior checking mode: "
                                + str(candidate_check["mode"])
                                + ". Inspect bounds, arithmetic and pointer use. "
                                "Actual stderr (untrusted diagnostic text): " + truncate(diagnostic)
                            )
                        session.stats.errors.append(
                            "creative run rejected: " + str(validation["reasons"])
                        )
                        session.discard_response()
                        continue
                    if validation["passed"]:
                        _emit_program_bytes(executed.stdout, stderr=False)
                        _emit_program_bytes(executed.stderr, stderr=True)
                else:
                    program_exit, sandbox = _run_program(ctx, result.artifact)
            directory = ctx.cwd / ".yescc" / ctx.source.stem
            directory.mkdir(parents=True, exist_ok=True)
            filename = "fallback.c" if creative else "generated.c"
            source_file = directory / filename
            source_file.write_text(program.source, encoding="utf-8")
            status = "creative_fallback" if creative else "generated"
            generated = {
                "source": display_path(source_file, ctx.cwd),
                "creative": creative,
                "io_contract": program.io_contract,
                "assumptions": program.assumptions,
            }
            if creative:
                generated["source_checks"] = source_checks
                generated["validation"] = validation
                if candidate_check is not None:
                    generated["undefined_behavior_check"] = candidate_check
            fallback: dict[str, object] | None = (
                {
                    "kind": "model_creative",
                    "reason": reason,
                    "source": display_path(source_file, ctx.cwd),
                }
                if creative
                else None
            )
            return _finalize(
                ctx,
                status=status,
                artifact=result.artifact,
                program_exit=program_exit,
                sandbox=sandbox,
                generated=generated,
                fallback=fallback,
                emitted_source=program.source,
            )
        session.discard_response()
        feedback = _compiler_feedback(result.build)
    session.budget_reason = f"已达到最大候选尝试次数 {ctx.invocation.max_rounds}"
    return None


def _emit_program_bytes(data: bytes, *, stderr: bool) -> None:
    """只转发通过检查的创意输出；普通原生输出仍沿用继承流。"""
    stream = sys.stderr if stderr else sys.stdout
    stream.flush()
    binary = getattr(stream, "buffer", None)
    if binary is not None:
        binary.write(data)
        binary.flush()
    else:
        stream.write(data.decode("utf-8", errors="replace"))
        stream.flush()


def _deterministic_fallback(ctx: Context, reason: str) -> int:
    try:
        adapter = get_adapter("c")
        info = adapter.toolchain()
    except ToolchainUnavailable as exc:
        return _finalize(
            ctx,
            status="failed",
            failure_message=f"构建失败（环境问题）：{exc.message}",
        )
    ctx.toolchain = {
        "adapter": info.adapter,
        "command": info.command,
        "version": info.version,
    }
    output = fallback_output(ctx.text)
    program = generate_c_program(output.message)
    result = build_in_temp(
        adapter, program, "fallback.c", ctx.invocation.passthrough, ctx.cwd, ctx.source.parent,
        protected_source=ctx.source,
    )
    ctx.attempts.append(_attempt("fallback", 1, result.build))
    _trace(ctx, f"deterministic fallback exit={result.build.returncode}")
    if result.build.returncode != 0:
        ctx.diagnostics = _gcc_diagnostics(result.build.stderr)
        return _finalize(
            ctx,
            status="failed",
            failure_message="兜底程序也未能构建成功；请检查 GCC 环境。",
        )
    directory = ctx.cwd / ".yescc" / ctx.source.stem
    directory.mkdir(parents=True, exist_ok=True)
    source_file = directory / "fallback.c"
    source_file.write_text(program, encoding="utf-8")
    sandbox: SandboxOutcome | None = None
    program_exit: int | None = None
    if ctx.invocation.mode == "run":
        program_exit, sandbox = _run_program(ctx, result.artifact)
    return _finalize(
        ctx,
        status="creative_fallback",
        artifact=result.artifact,
        program_exit=program_exit,
        sandbox=sandbox,
        fallback={
            "kind": output.kind,
            "guidance": degradation_guidance(
                ctx.text,
                language=ctx.language,
                kind=output.kind,
                offline=ctx.invocation.offline,
                model_available=ctx.session is not None and ctx.session.available,
                model_attempted=ctx.session is not None
                and bool(ctx.session.stats.calls or ctx.session.stats.cache_hits),
                budget_blocked=ctx.session is not None and bool(ctx.session.budget_reason),
                rule_builds=sum(item["kind"] == "rule" for item in ctx.attempts),
            ),
            "reason": reason,
            "source": display_path(source_file, ctx.cwd),
        },
        emitted_source=program,
    )


def _fallback(ctx: Context, reason: str) -> int:
    if ctx.invocation.no_fallback:
        return _finalize(
            ctx,
            status="native_failed",
            failure_message=reason,
        )
    if fallback_output(ctx.text).kind == "easter_egg":
        return _deterministic_fallback(ctx, reason)
    if ctx.session is not None and ctx.session.available:
        if ctx.language is None:
            creative = ctx.invocation.play or not _looks_like_spec(ctx.text)
        else:
            creative = True
        try:
            adapter = get_adapter("c")
            info = adapter.toolchain()
            ctx.toolchain = {
                "adapter": info.adapter,
                "command": info.command,
                "version": info.version,
            }
        except ToolchainUnavailable:
            return _deterministic_fallback(ctx, reason)
        generated = _model_generate(ctx, adapter, creative=creative, reason=reason)
        if generated is not None:
            return generated
    return _deterministic_fallback(ctx, reason)


def execute(argv: list[str], invocation: Invocation) -> int:
    """执行一次构建或运行。"""
    if invocation.check_undefined and invocation.mode != "run":
        raise UsageError("--check-undefined 只用于模型创意 run，build 不执行检查。")
    cwd = Path.cwd()
    source = _validate_source(invocation.source)
    output = _output_argument(invocation.passthrough)
    if output is not None and _same_file(cwd / output, source):
        raise UsageError("输出路径 -o 指向输入源文件，拒绝覆盖；未请求模型。")
    size, encoding, text = _read_source(source)
    # 显式语言错误不能进入未知输入的创意兜底（包括 --play），也不应创建模型会话。
    if invocation.lang is not None:
        detect_language(source, invocation.lang)
    session = ModelSession(
        offline=invocation.offline,
        no_cache=invocation.no_cache,
        budget_usd=invocation.budget_usd,
        max_rounds=invocation.max_rounds,
        provider_override=invocation.provider,
        model_override=invocation.model,
        reasoning_effort_override=invocation.reasoning_effort,
        thinking_override=invocation.thinking,
        max_output_tokens_override=invocation.max_output_tokens,
    )
    ctx = Context(
        argv=argv,
        invocation=invocation,
        source=source,
        size=size,
        encoding=encoding,
        text=text,
        cwd=cwd,
        started=time.perf_counter(),
        session=session,
    )

    if invocation.play:
        return _fallback(ctx, "用户请求 --play")

    try:
        language = detect_language(source, invocation.lang)
    except UnsupportedInput:
        if invocation.strict or invocation.no_fallback:
            raise
        return _fallback(ctx, "未识别的输入类型")
    ctx.language = language

    adapter = get_adapter(language)
    try:
        info = adapter.toolchain()
    except ToolchainUnavailable as exc:
        return _finalize(
            ctx,
            status="failed",
            failure_message=f"构建失败（环境问题）：{exc.message}",
        )
    ctx.toolchain = {
        "adapter": info.adapter,
        "command": info.command,
        "version": info.version,
    }

    native = adapter.build(source, invocation.passthrough, cwd)
    ctx.attempts.append(_attempt("native", 1, native))
    _trace(ctx, f"native exit={native.returncode}")
    if language == "c":
        ctx.diagnostics = _gcc_diagnostics(native.stderr)
        ctx.syntax_errors = [
            {"start": item.start, "end": item.end} for item in parse_c(text).errors
        ]

    if native.returncode == 0:
        if not invocation.json_output:
            sys.stdout.write(native.stdout)
            sys.stderr.write(native.stderr)
            sys.stdout.flush()
            sys.stderr.flush()
        program_exit = (
            adapter.run(source, native, invocation.run_args, cwd).returncode
            if invocation.mode == "run"
            else None
        )
        return _finalize(
            ctx,
            status="native_ok",
            artifact=native.artifact,
            program_exit=program_exit,
            emitted_source=text,
        )

    if invocation.strict:
        if not invocation.json_output:
            sys.stdout.write(native.stdout)
            sys.stderr.write(native.stderr)
        return _finalize(
            ctx,
            status="native_failed",
            failure_message="构建未完成。",
            strict=True,
        )

    if _is_environment_failure(native.stderr):
        return _finalize(
            ctx,
            status="failed",
            failure_message="构建失败（环境或依赖问题）；没有进入创意兜底。",
        )

    if language == "c":
        outcome = attempt_repair(
            adapter, text, source.name, invocation.passthrough, cwd, source.parent, native.stderr
        )
        for index, (_, build) in enumerate(outcome.attempts, start=2):
            ctx.attempts.append(_attempt("rule", index, build))
        _trace(ctx, f"rule candidates={len(outcome.attempts)}")
        if outcome.candidate is not None and outcome.build is not None:
            patched, patch_file = _write_patched(
                ctx,
                outcome.candidate.text,
                _diff(text, outcome.candidate.text, source.name),
            )
            repair = {
                "rules": list(outcome.candidate.rules),
                "changed": outcome.candidate.changed,
                "candidate_count": len(outcome.attempts),
                "patched_source": display_path(patched, cwd),
                "patch": display_path(patch_file, cwd),
                "assumptions": [],
            }
            sandbox = None
            program_exit = None
            if invocation.mode == "run":
                program_exit, sandbox = _run_program(ctx, outcome.artifact)
            return _finalize(
                ctx,
                status="repaired",
                artifact=outcome.artifact,
                program_exit=program_exit,
                sandbox=sandbox,
                repair=repair,
                emitted_source=outcome.candidate.text,
            )

        repaired = _model_repair(ctx, adapter, native)
        if repaired is not None:
            return repaired

    if invocation.no_fallback:
        return _finalize(
            ctx,
            status="failed" if session.budget_reason else "native_failed",
            failure_message=f"构建未完成（工具链退出码 {native.returncode}）。",
        )

    return _fallback(ctx, "原始构建与确定性修复均未成功")


def explain(source: Path | None, as_json: bool) -> int:
    """读取最近一次报告，不调用模型。"""
    if source is None:
        raise UsageError("--explain 需要一个输入文件。")
    report_file = report_path(Path.cwd(), source)
    payload = load_report(report_file)
    if payload is None:
        raise UsageError(f"没有找到报告：{report_file}。先运行一次构建再查看。")
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(summarize(payload))
    return int(ExitCode.OK)
