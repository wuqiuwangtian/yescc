"""可选 UBSan 后端；只用当前 C 工具链，以固定正负对照验证实际能力。"""

from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from yescc.adapters.base import BuildResult, LanguageAdapter
from yescc.adapters.c import _default_artifact
from yescc.errors import UsageError
from yescc.sandbox.creative import DEFAULT_MAX_OUTPUT_BYTES, run_creative_command
from yescc.sandbox.runner import _network_isolation_supported, _run_once

_RUNTIME_FLAGS = ("-fsanitize=undefined", "-fno-sanitize-recover=undefined")
_TRAP_FLAGS = ("-fsanitize=undefined", "-fsanitize-undefined-trap-on-error")
_MARKER = b"yescc-undefined-control-ok\n"
_NORMAL_CONTROL = (
    "#include <stdio.h>\nint main(void) { volatile int a = 1; volatile int b = 2; "
    "volatile int c = a + b; if (c != 3) return 1; "
    'puts("yescc-undefined-control-ok"); return 0; }\n'
)
_OVERFLOW_CONTROL = (
    "#include <limits.h>\nint main(void) { volatile int a = INT_MAX; volatile int b = 1; "
    "volatile int c = a + b; (void)c; return 0; }\n"
)
# GCC still needs its platform SDK environment; model credentials and loader overrides do not pass.
_ENV_KEYS = {
    "PATH",
    "HOME",
    "USERPROFILE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "INCLUDE",
    "LIB",
    "LIBPATH",
    "VCINSTALLDIR",
    "VCTOOLSINSTALLDIR",
    "WINDOWSSDKDIR",
    "WINDOWSSDKVERSION",
    "UCRTVERSION",
    "UNIVERSALCRTSDKDIR",
    "VSINSTALLDIR",
    "SYSTEMDRIVE",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES",
}


@dataclass
class UndefinedProfile:
    """本次探针实际取得的能力与证据；identity 不包含随机探针路径或耗时。"""

    available: bool
    mode: str | None
    flags: tuple[str, ...]
    reason: str
    probes: list[dict[str, object]]
    _version: str = field(default="unknown", repr=False)
    _arguments: tuple[str, ...] = field(default=(), repr=False)
    _timeout_s: float = field(default=20.0, repr=False)
    _elapsed_ms: int = field(default=0, repr=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "mode": self.mode,
            "flags": list(self.flags),
            "reason": self.reason,
            "compiler_version": self._version,
            "arguments": list(self._arguments),
            "probes": self.probes,
            "timeout_s": self._timeout_s,
            "elapsed_ms": self._elapsed_ms,
        }

    def identity(self) -> dict[str, object]:
        return {
            "backend_version": 1,
            "compiler_version": self._version,
            "available": self.available,
            "mode": self.mode,
            "flags": list(self.flags),
            "arguments": list(self._arguments),
        }


def validate_undefined_options(passthrough: Sequence[str]) -> None:
    """拒绝会绕开插桩、改编译模式或在探针目录外写额外产物的参数。"""
    # Operands of these options are data, even when a macro value looks like an option.
    operands = {
        "-o",
        "-I",
        "-L",
        "-D",
        "-U",
        "-include",
        "-imacros",
        "-isystem",
        "-iquote",
        "-idirafter",
        "-iprefix",
        "-iwithprefix",
        "-iwithprefixbefore",
        "-isysroot",
        "--sysroot",
        "-l",
        "-u",
        "-A",
        "-std",
        "-aux-info",
    }
    exact = {
        "-c",
        "-S",
        "-E",
        "-fsyntax-only",
        "-nostdlib",
        "-nodefaultlibs",
        "-nostartfiles",
        "-r",
        "-shared",
        "-###",
        "--help",
        "--version",
        "-combine",
        "-fcompare-debug",
        "-aux-info",
        "-wrapper",
        "-Xassembler",
        "-Xpreprocessor",
        "-Xlinker",
    }
    prefixes = (
        "-fsanitize",
        "-fno-sanitize",
        "-static-libubsan",
        "-shared-libubsan",
        "-M",
        "-x",
        "-save-temps",
        "--save-temps",
        "-fdump",
        "-MJ",
        "-fprofile",
        "-fauto-profile",
        "-ftest-coverage",
        "--coverage",
        "-fdiagnostics-file",
        "-fdiagnostics-output",
        "-fdiagnostics-add-output",
        "-fdiagnostics-set-output",
        "-fopt-info",
        "-fstack-usage",
        "-fcallgraph-info",
        "-gsplit-dwarf",
        "-dump",
        "-auxbase",
        "-fplugin",
        "-specs",
        "--specs",
        "-B",
        "-fcompare-debug=",
        "-Wa,",
        "-Wp,",
        "-Wl,",
    )
    pending = False
    for arg in passthrough:
        if arg.startswith("@"):
            raise UsageError(f"--check-undefined 不支持此编译参数：{arg}")
        if pending:
            pending = False
            continue
        file_diagnostics = arg.startswith("-fdiagnostics-format=") and arg.endswith("-file")
        if arg in exact or arg.startswith(prefixes) or file_diagnostics:
            raise UsageError(f"--check-undefined 不支持此编译参数：{arg}")
        pending = arg in operands


def _compile_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _ENV_KEYS or key.upper().startswith("LC_")
    }


def bounded_c_build(
    compiler: str,
    source: Path,
    arguments: Sequence[str],
    cwd: Path,
    *,
    timeout_s: float = 20,
) -> BuildResult:
    """仅编译；有界捕获、环境白名单与进程树清理，不执行产物。"""
    started = time.monotonic()
    command = [compiler, str(source), *arguments]
    stdout, stderr = "", ""
    returncode = 124
    timeout = min(timeout_s, 20.0)
    if not math.isfinite(timeout) or timeout <= 0:
        stderr = "YesCC undefined-check compilation timeout (no remaining time).\n"
    else:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                outcome = _run_once(
                    command,
                    cwd=cwd,
                    env=_compile_environment(),
                    timeout_s=timeout,
                    preexec=None,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES,
                )
            except OSError as exc:
                returncode = 127
                stderr = f"YesCC undefined-check compiler launch failed: {exc}\n"
            else:
                returncode = outcome.exit_code
                if outcome.timed_out:
                    stderr = "YesCC undefined-check compilation timeout.\n"
                elif outcome.output_limited:
                    # A compiler can emit unlimited output and exit zero before polling observes it.
                    returncode = returncode if returncode != 0 else 1
                    stderr = "YesCC undefined-check compiler output limit exceeded.\n"
            out.seek(0)
            captured_stdout = out.read(DEFAULT_MAX_OUTPUT_BYTES)
            err.seek(0)
            captured_stderr = err.read(DEFAULT_MAX_OUTPUT_BYTES - len(captured_stdout))
            stdout = captured_stdout.decode("utf-8", errors="replace")
            stderr = captured_stderr.decode("utf-8", errors="replace") + stderr
    predicted = _default_artifact(source, arguments, cwd)
    artifact = (
        predicted if returncode == 0 and predicted is not None and predicted.exists() else None
    )
    return BuildResult(
        command=command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        artifact=artifact,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _control_run(executable: Path, cwd: Path, timeout_s: float) -> dict[str, object]:
    started = time.monotonic()
    try:
        result = run_creative_command(
            [str(executable)],
            unsafe=False,
            cwd=cwd,
            timeout_s=min(timeout_s, 3.0),
            max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES,
        )
    except OSError as exc:
        return {
            "passed_execution": False,
            "error": str(exc),
            "command": [str(executable)],
            "timeout_s": min(timeout_s, 3.0),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    return {
        "passed_execution": not result.sandbox.timed_out and not result.sandbox.output_limited,
        "command": [str(executable)],
        "stdout": result.stdout.decode("utf-8", errors="replace"),
        "stderr": result.stderr.decode("utf-8", errors="replace"),
        "sandbox": asdict(result.sandbox),
        "validation": result.validation,
        "exit_code": result.sandbox.exit_code,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "timeout_s": min(timeout_s, 3.0),
    }


def select_undefined_profile(
    adapter: LanguageAdapter,
    passthrough: Sequence[str],
    cwd: Path,
    *,
    timeout_s: float = 20,
) -> UndefinedProfile:
    """每次请求重新探针；runtime 可用才选用，否则尝试同一编译器的 trap。"""
    from yescc.fix.engine import _resolve_paths, _without_output

    validate_undefined_options(passthrough)
    started = time.monotonic()
    toolchain = adapter.toolchain()
    arguments = _resolve_paths(_without_output(passthrough), cwd)
    profile = UndefinedProfile(
        False, None, (), "unavailable", [], toolchain.version, tuple(arguments)
    )
    profile._timeout_s = min(timeout_s, 20.0) if math.isfinite(timeout_s) and timeout_s > 0 else 0.0
    try:
        if profile._timeout_s <= 0:
            profile.reason = "probe_timeout"
            return profile
        deadline = started + profile._timeout_s
        with tempfile.TemporaryDirectory(prefix="yescc-undefined-") as raw:
            directory = Path(raw)
            for mode, flags in (("runtime", _RUNTIME_FLAGS), ("trap", _TRAP_FLAGS)):
                probe: dict[str, object] = {"mode": mode, "flags": list(flags), "passed": False}
                profile.probes.append(probe)
                for name, text in (("normal", _NORMAL_CONTROL), ("overflow", _OVERFLOW_CONTROL)):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        probe["reason"] = profile.reason = "probe_timeout"
                        return profile
                    source = directory / f"{mode}_{name}.c"
                    executable = directory / f"{mode}_{name}.exe"
                    source.write_text(text, encoding="utf-8", newline="")
                    build = bounded_c_build(
                        toolchain.command,
                        source,
                        [*arguments, *flags, "-o", str(executable)],
                        directory,
                        timeout_s=min(20.0, remaining),
                    )
                    evidence = asdict(build)
                    evidence["artifact"] = (
                        str(build.artifact) if build.artifact is not None else None
                    )
                    evidence["environment_filtered"] = True
                    evidence["timeout_s"] = min(20.0, remaining)
                    evidence["max_output_bytes"] = DEFAULT_MAX_OUTPUT_BYTES
                    evidence["process_tree_cleanup"] = (
                        "posix_group" if os.name == "posix" else "windows_taskkill"
                    )
                    control: dict[str, object] = {"compile": evidence}
                    probe[name] = control
                    if build.returncode == 130:
                        raise KeyboardInterrupt
                    if build.returncode != 0 or build.artifact is None:
                        probe["reason"] = "control_compile_failed"
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        probe["reason"] = profile.reason = "probe_timeout"
                        return profile
                    # The shared runner lazily probes unshare with a separate 3s timeout.
                    # Reserve that worst case before its first call, then charge actual time.
                    if _network_isolation_supported.cache_info().currsize == 0:
                        if remaining <= 3.0:
                            probe["reason"] = profile.reason = "probe_timeout"
                            return profile
                        _network_isolation_supported()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            probe["reason"] = profile.reason = "probe_timeout"
                            return profile
                    run = _control_run(executable, directory, remaining)
                    control["run"] = run
                    if run.get("exit_code") == 130:
                        raise KeyboardInterrupt
                    if not run["passed_execution"]:
                        probe["reason"] = "control_execution_failed"
                        break
                    if name == "normal":
                        if run["exit_code"] != 0 or run["stdout"] not in {
                            _MARKER.decode("ascii"),
                            _MARKER.decode("ascii").replace("\n", "\r\n"),
                        }:
                            probe["reason"] = "normal_control_failed"
                            break
                    else:
                        diagnostic = re.search(
                            re.escape(str(source)) + r":\d+(?::\d+)?: runtime error: "
                            r"signed integer overflow",
                            str(run["stderr"]),
                        )
                        exit_code = run["exit_code"]
                        assert isinstance(exit_code, int)
                        abnormal = exit_code < 0 or exit_code >= 0x80000000
                        if (
                            exit_code == 0
                            or (mode == "runtime" and diagnostic is None)
                            or (mode == "trap" and not abnormal)
                        ):
                            probe["reason"] = "overflow_control_failed"
                            break
                else:
                    probe["passed"] = True
                    probe["reason"] = "controls_passed"
                    profile.available, profile.mode, profile.flags = True, mode, flags
                    profile.reason = "controls_passed"
                    return profile
        profile.reason = "controls_failed"
        return profile
    finally:
        profile._elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
