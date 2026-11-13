"""Offline, bounded C execution study; never a production acceptance gate.

Candidate stderr is untrusted. A UBSan-shaped diagnostic is recorded as suspected,
not proof of undefined behavior. Only the fixed positive control establishes that
this compiler/runtime detects its known overflow in the measured environment.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from yescc.sandbox.creative import DEFAULT_MAX_OUTPUT_BYTES, run_creative_command
from yescc.sandbox.runner import _stop_process_tree

COMPILE_TIMEOUT_S = 20.0
BASE_FLAGS = ["-std=c11", "-O0"]
UBSAN_FLAGS = ["-fsanitize=undefined", "-fno-sanitize-recover=undefined"]
TRAP_FLAGS = ["-fsanitize=undefined", "-fsanitize-undefined-trap-on-error"]
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
    # VS developer environments provide the Windows SDK/CRT search paths.
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
_NORMAL_CONTROL = (
    "#include <stdio.h>\nint main(void) { volatile int a = 1; volatile int b = 2; "
    'volatile int c = a + b; if (c != 3) return 1; puts("control-ok"); return 0; }\n'
)
_OVERFLOW_CONTROL = (
    "#include <limits.h>\nint main(void) { volatile int a = INT_MAX; "
    "volatile int b = 1; volatile int c = a + b; (void)c; return 0; }\n"
)


def _blob(data: bytes, *, total_bytes: int | None = None) -> dict[str, Any]:
    total = len(data) if total_bytes is None else total_bytes
    return {
        "base64": base64.b64encode(data).decode("ascii"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "captured_bytes": len(data),
        "total_bytes": total,
        "complete": len(data) == total,
        "text": data.decode("utf-8", errors="replace"),
    }


def _compile_command(command: list[str], cwd: Path) -> dict[str, Any]:
    """Bound compiler wall time; preserve complete captured bytes on temporary disk."""
    started = time.monotonic()
    status, exit_code, error = "completed", None, None
    env = {
        k: v for k, v in os.environ.items() if k.upper() in _ENV_KEYS or k.upper().startswith("LC_")
    }
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=os.name == "posix",
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
                ),
            )
            try:
                exit_code = process.wait(timeout=COMPILE_TIMEOUT_S)
            finally:
                _stop_process_tree(process)
            if exit_code != 0:
                status = "failed"
        except subprocess.TimeoutExpired:
            status = "timeout"
        except OSError as exc:
            status, error = "launch_failed", str(exc)
        out.seek(0)
        err.seek(0)
        stdout, stderr = out.read(), err.read()
    return {
        "command": command,
        "status": status,
        "exit_code": exit_code,
        "elapsed_s": time.monotonic() - started,
        "timeout_s": COMPILE_TIMEOUT_S,
        "environment_filtered": True,
        "error": error,
        "stdout": _blob(stdout),
        "stderr": _blob(stderr),
    }


def _byte_count(value: object) -> int:
    assert isinstance(value, int)
    return value


def _execute(command: list[str], cwd: Path, timeout_s: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        run = run_creative_command(
            command,
            unsafe=False,
            cwd=cwd,
            timeout_s=timeout_s,
            max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES,
        )
    except OSError as exc:
        return {
            "command": command,
            "status": "launch_failed",
            "exit_code": None,
            "elapsed_s": time.monotonic() - started,
            "error": str(exc),
            "stdout": _blob(b""),
            "stderr": _blob(b""),
            "validation": None,
            "sandbox": None,
        }
    outcome = run.sandbox
    status = (
        "timeout"
        if outcome.timed_out
        else "output_limit"
        if outcome.output_limited
        else "exited_nonzero"
        if outcome.exit_code != 0
        else "completed"
    )
    return {
        "command": command,
        "status": status,
        "exit_code": outcome.exit_code,
        "elapsed_s": time.monotonic() - started,
        "error": None,
        "stdout": _blob(run.stdout, total_bytes=_byte_count(run.validation["stdout_bytes"])),
        "stderr": _blob(run.stderr, total_bytes=_byte_count(run.validation["stderr_bytes"])),
        "validation": run.validation,
        "sandbox": asdict(outcome),
    }


def _build_run(
    compiler: str,
    source: Path,
    *,
    instrumented: bool,
    timeout_s: float,
    mode: str = "runtime",
    link_flags: list[str] | None = None,
) -> dict[str, Any]:
    executable = source.with_name("ubsan.exe" if instrumented else "normal.exe")
    command = [compiler, *BASE_FLAGS]
    if instrumented:
        command.extend(UBSAN_FLAGS if mode == "runtime" else TRAP_FLAGS)
    command.extend([str(source), "-o", str(executable), *(link_flags or [])])
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    compilation = _compile_command(command, source.parent)
    execution = None
    if compilation["status"] == "completed":
        execution = _execute([str(executable)], source.parent, timeout_s)
    return {"source_sha256": source_hash, "compile": compilation, "run": execution}


def _diagnostics(variant: dict[str, Any], source: Path) -> list[str]:
    run = variant["run"]
    if run is None:
        return []
    # Variants compile independent original-byte copies; their paths can differ.
    command = variant["compile"]["command"]
    if "-o" in command:
        source = Path(command[command.index("-o") - 1])
    # Require this source's full location and a runtime-error prefix. Still untrusted.
    pattern = re.compile(re.escape(str(source)) + r":\d+(?::\d+)?: runtime error: .+")
    return pattern.findall(run["stderr"]["text"])


def _probe(
    compiler: str | None,
    requested: str,
    cwd: Path,
    timeout: float,
    mode: str,
) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "requested": requested,
        "resolved": compiler,
        "status": "unavailable",
        "version": None,
        "normal_control": None,
        "sanitized_control": None,
        "overflow_control": None,
        "instrumented_execution_usable": False,
        "target_probe": None,
        "target": None,
        "link_flags": ["-lm"],
    }
    if compiler is None:
        return probe
    probe["version"] = _compile_command([compiler, "--version"], cwd)
    target_probe = _compile_command([compiler, "-dumpmachine"], cwd)
    probe["target_probe"] = target_probe
    if target_probe["status"] == "completed":
        probe["target"] = target_probe["stdout"]["text"].strip()
    link_flags = [] if "msvc" in str(probe["target"]).lower() else ["-lm"]
    probe["link_flags"] = link_flags
    source = cwd / "control.c"
    source.write_bytes(_NORMAL_CONTROL.encode("utf-8"))
    probe["normal_control_source"] = _blob(source.read_bytes())
    normal = _build_run(
        compiler, source, instrumented=False, timeout_s=timeout, mode=mode, link_flags=link_flags
    )
    probe["normal_control"] = normal
    if not _control_ok(normal):
        probe["status"] = "baseline_unusable"
        return probe
    sanitized = _build_run(
        compiler, source, instrumented=True, timeout_s=timeout, mode=mode, link_flags=link_flags
    )
    probe["sanitized_control"] = sanitized
    if sanitized["compile"]["status"] != "completed":
        probe["status"] = "unsupported"
        return probe
    if not _control_ok(sanitized):
        probe["status"] = "runtime_unusable"
        return probe
    probe["instrumented_execution_usable"] = True
    source = cwd / "overflow-control.c"
    source.write_bytes(_OVERFLOW_CONTROL.encode("utf-8"))
    probe["overflow_control_source"] = _blob(source.read_bytes())
    overflow = _build_run(
        compiler, source, instrumented=True, timeout_s=timeout, mode=mode, link_flags=link_flags
    )
    probe["overflow_control"] = overflow
    run = overflow["run"]
    detected = (
        run is not None
        and run["status"] == "exited_nonzero"
        and (
            mode == "trap"
            or any("signed integer overflow" in line for line in _diagnostics(overflow, source))
        )
    )
    probe["positive_control_detected"] = detected
    probe["status"] = "available" if detected else "detection_failed"
    return probe


def _control_ok(variant: dict[str, Any]) -> bool:
    run = variant["run"]
    return (
        variant["compile"]["status"] == "completed"
        and run is not None
        and run["status"] == "completed"
        and run["stdout"]["base64"]
        in {
            base64.b64encode(value).decode("ascii")
            for value in (b"control-ok\n", b"control-ok\r\n")
        }
    )


def _load_cases(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a nonempty array")
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each case must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", case_id):
            raise ValueError("case.id must be a safe ASCII identifier")
        if case_id in ids:
            raise ValueError(f"duplicate case id: {case_id}")
        ids.add(case_id)
        for field in ("source", "category", "note"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise ValueError(f"case.{field} must be a nonempty string")
        if not isinstance(case.get("expectation"), str) or case["expectation"] not in {
            "defined",
            "undefined",
            "boundary",
        }:
            raise ValueError("case.expectation must be defined, undefined, or boundary")
        if "expected_stdout" in case and not isinstance(case["expected_stdout"], str):
            raise ValueError("case.expected_stdout must be a string")
        # Reject surrogates before executing anything or reserving the report path.
        json.dumps(case, ensure_ascii=False).encode("utf-8")
    return raw, cases


def _classification(
    normal: dict[str, Any] | None,
    sanitized: dict[str, Any] | None,
    source: Path,
    mode: str,
) -> dict[str, Any]:
    if sanitized is None:
        return {
            "status": "not_tested",
            "ub_detected": None,
            "diagnostics": [],
            "ubsan_diagnostic_observed": False,
            "instrumentation_only_failure": False,
        }
    if sanitized["compile"]["status"] != "completed":
        return {
            "status": (
                "compile_failed"
                if sanitized["compile"]["status"] == "failed"
                else "compile_" + str(sanitized["compile"]["status"])
            ),
            "ub_detected": None,
            "diagnostics": [],
            "ubsan_diagnostic_observed": False,
            "instrumentation_only_failure": False,
        }
    run = sanitized["run"]
    assert run is not None
    diagnostics = _diagnostics(sanitized, source)
    if run["status"] in {"timeout", "output_limit", "launch_failed"}:
        status = run["status"]
    elif (
        mode == "trap"
        and run["status"] == "exited_nonzero"
        and normal is not None
        and normal["run"] is not None
        and normal["run"]["status"] == "completed"
    ):
        status = "instrumentation_failure"
    elif run["status"] == "exited_nonzero" and diagnostics:
        status = "suspected"
    elif run["status"] == "exited_nonzero":
        status = "nonzero_without_diagnostic"
    elif diagnostics:
        status = "diagnostic_without_failure"
    else:
        status = "no_diagnostic"
    return {
        "status": status,
        "ub_detected": None,
        "diagnostics": diagnostics,
        "ubsan_diagnostic_observed": bool(diagnostics) if mode == "runtime" else False,
        "instrumentation_only_failure": bool(
            normal
            and normal["run"]
            and normal["run"]["status"] == "completed"
            and run["status"] == "exited_nonzero"
        ),
        "baseline_has_diagnostic": bool(normal and _diagnostics(normal, source)),
        "reason": "candidate-controlled stderr is not trusted runtime provenance",
    }


def _variant_status(variant: dict[str, Any] | None) -> str:
    if variant is None:
        return "not_tested"
    if variant["compile"]["status"] != "completed":
        return "compile_" + str(variant["compile"]["status"])
    return str(variant["run"]["status"])


def _stdout_matches(
    variant: dict[str, Any] | None,
    expected: str,
    *,
    text: bool = False,
) -> bool | None:
    if not variant or not variant["run"] or not variant["run"]["stdout"]["complete"]:
        return None
    raw = base64.b64decode(variant["run"]["stdout"]["base64"])
    if not text:
        return raw == expected.encode("utf-8")
    try:
        actual = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    # Only platform CRLF/LF line endings are equivalent. Preserve all whitespace,
    # lone carriage returns, Unicode code points, and final-newline differences.
    return actual.replace("\r\n", "\n") == expected.replace("\r\n", "\n")


def run_study(
    cases_path: Path,
    *,
    compiler: str,
    output_path: Path,
    run_timeout_s: float = 3.0,
    mode: str = "runtime",
) -> dict[str, Any]:
    """Record actual comparisons; exit/completion never asserts UB absence or intent."""
    if mode not in {"runtime", "trap"}:
        raise ValueError("mode must be runtime or trap")
    if not isinstance(run_timeout_s, (int, float)) or isinstance(run_timeout_s, bool):
        raise ValueError("run timeout must be a positive finite number")
    if not math.isfinite(run_timeout_s) or run_timeout_s <= 0:
        raise ValueError("run timeout must be a positive finite number")
    if not isinstance(compiler, str) or not compiler or compiler.startswith("-"):
        raise ValueError("compiler must name one executable without flags")
    if output_path.resolve() == cases_path.resolve():
        raise ValueError("output must not overwrite the cases input")
    if output_path.exists() or output_path.is_symlink():
        raise ValueError("output already exists; choose a new report path")
    raw, cases = _load_cases(cases_path)
    resolved = shutil.which(compiler)
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="yescc-ubsan-study-") as temporary:
        root = Path(temporary).resolve()
        probe_dir = root / "probe"
        probe_dir.mkdir()
        probe = _probe(resolved, compiler, probe_dir, run_timeout_s, mode)
        baseline_usable = probe["normal_control"] is not None and _control_ok(
            probe["normal_control"]
        )
        case_root = root / "cases"
        case_root.mkdir()
        for index, case in enumerate(cases):
            case_dir = case_root / f"case-{index:04d}"
            case_dir.mkdir()
            source_bytes = case["source"].encode("utf-8")
            source = case_dir / "candidate.c"
            normal = sanitized = None
            if baseline_usable:
                assert resolved is not None
                normal_dir = Path(tempfile.mkdtemp(prefix="normal-", dir=case_dir))
                source = normal_dir / "candidate.c"
                source.write_bytes(source_bytes)
                normal = _build_run(
                    resolved,
                    source,
                    instrumented=False,
                    timeout_s=run_timeout_s,
                    mode=mode,
                    link_flags=probe["link_flags"],
                )
                if probe["instrumented_execution_usable"]:
                    # Do not let a baseline program modifying __FILE__ change the
                    # bytes subsequently presented to the instrumented compiler.
                    instrumented_dir = Path(tempfile.mkdtemp(prefix="instrumented-", dir=case_dir))
                    source = instrumented_dir / "candidate.c"
                    source.write_bytes(source_bytes)
                    sanitized = _build_run(
                        resolved,
                        source,
                        instrumented=True,
                        timeout_s=run_timeout_s,
                        mode=mode,
                        link_flags=probe["link_flags"],
                    )
            result = {
                "id": case["id"],
                "expectation": case["expectation"],
                "category": case["category"],
                "note": case["note"],
                "source": _blob(source_bytes),
                "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "normal": normal,
                "instrumented": sanitized,
                "classification": _classification(normal, sanitized, source, mode),
            }
            if "expected_stdout" in case:
                result["expected_stdout"] = case["expected_stdout"]
                variants = (("normal", normal), ("instrumented", sanitized))
                exact = {
                    name: _stdout_matches(variant, case["expected_stdout"])
                    for name, variant in variants
                }
                result["expected_stdout_matches"] = exact
                result["expected_stdout_exact_matches"] = exact
                result["expected_stdout_comparison"] = "exact_bytes"
                result["expected_stdout_text_matches"] = {
                    name: _stdout_matches(variant, case["expected_stdout"], text=True)
                    for name, variant in variants
                }
                result["expected_stdout_text_comparison"] = "strict_utf8_lf_crlf"
            results.append(result)
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "offline_c_ubsan_study",
        "completed": True,
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "cases_path": str(cases_path.resolve()),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "compiler": probe,
        "settings": {
            "mode": mode,
            "base_flags": BASE_FLAGS,
            "ubsan_flags": UBSAN_FLAGS if mode == "runtime" else TRAP_FLAGS,
            "link_flags": probe["link_flags"],
            "actual_flags": [
                *BASE_FLAGS,
                *(UBSAN_FLAGS if mode == "runtime" else TRAP_FLAGS),
                *probe["link_flags"],
            ],
            "compile_timeout_s": COMPILE_TIMEOUT_S,
            "run_timeout_s": run_timeout_s,
            "max_output_bytes": DEFAULT_MAX_OUTPUT_BYTES,
            "unsafe": False,
        },
        "results": results,
        "summary": {
            "total": len(results),
            "normal_statuses": dict(Counter(_variant_status(r["normal"]) for r in results)),
            "instrumented_statuses": dict(
                Counter(_variant_status(r["instrumented"]) for r in results)
            ),
            "classification_statuses": dict(
                Counter(r["classification"]["status"] for r in results)
            ),
            "not_tested": sum(r["instrumented"] is None for r in results),
            "proven_ub": 0,
        },
        "limitations": [
            "No diagnostic does not prove defined behavior or intended semantics.",
            "Candidate stderr can imitate UBSan; matches are suspected, never proven UB.",
            "Runtime output contains captured prefixes only when its quota is exceeded.",
            "Sandbox/resource limits and cleanup are best effort, not filesystem isolation.",
            "Compiler support is specific to the recorded host and runtime.",
        ],
    }
    # Exclusive creation protects existing inputs/reports, including hardlink aliases.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["runtime", "trap"], default="runtime")
    args = parser.parse_args(argv)
    try:
        run_study(args.cases, compiler=args.compiler, output_path=args.output, mode=args.mode)
    except (OSError, ValueError) as exc:
        print(f"ubsan study: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
