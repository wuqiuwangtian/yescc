"""UBSan 后端能力必须来自正负对照，不用编译退出零冒充支持。"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from yescc.adapters.base import BuildResult, ToolchainInfo
from yescc.adapters.c import CAdapter
from yescc.errors import UsageError
from yescc.sandbox import undefined


def _adapter(compiler: str = "gcc") -> CAdapter:
    adapter = CAdapter()
    adapter._cached = ToolchainInfo("c", compiler, "test-version")
    return adapter


@pytest.fixture
def gcc() -> str:
    command = shutil.which("gcc")
    if command is None:
        pytest.skip("需要当前平台 GCC")
    return command


def test_actual_gcc_controls_and_stable_identity(gcc: str, tmp_path: Path) -> None:
    adapter = _adapter(gcc)
    first = undefined.select_undefined_profile(adapter, ["-O2", "-o", "untouched"], tmp_path)
    if not first.available:
        pytest.skip(f"当前 GCC 的 runtime/trap 控制未通过：{first.as_dict()}")
    assert first.mode in {"runtime", "trap"}
    assert first.probes[-1]["passed"] is True
    assert not (tmp_path / "untouched").exists()
    second = undefined.select_undefined_profile(adapter, ["-O2", "-oother"], tmp_path)
    assert first.identity() == second.identity()
    assert first.as_dict()["probes"] != second.as_dict()["probes"]
    assert "yescc-undefined-" not in str(first.identity())
    assert first.identity()["arguments"] == ["-O2"]


def test_missing_runtime_falls_back_to_actual_trap(
    gcc: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = undefined.bounded_c_build

    def missing_runtime(
        compiler: str,
        source: Path,
        arguments: list[str],
        cwd: Path,
        **kwargs: Any,
    ) -> BuildResult:
        if "-fno-sanitize-recover=undefined" in arguments:
            return BuildResult([compiler, str(source), *arguments], 1, "", "missing libubsan")
        return original(compiler, source, arguments, cwd, **kwargs)

    monkeypatch.setattr(undefined, "bounded_c_build", missing_runtime)
    profile = undefined.select_undefined_profile(_adapter(gcc), [], tmp_path)
    if not profile.available:
        pytest.skip(f"当前 GCC trap 正负对照不可用：{profile.as_dict()}")
    assert profile.mode == "trap"
    assert profile.probes[0]["reason"] == "control_compile_failed"
    assert "missing libubsan" in str(profile.probes[0])


def test_never_selects_another_compiler_and_reports_both_failures(tmp_path: Path) -> None:
    missing = str(tmp_path / "nonexistent-gcc")
    profile = undefined.select_undefined_profile(_adapter(missing), [], tmp_path)
    assert not profile.available
    assert profile.flags == ()
    assert profile.mode is None
    assert len(profile.probes) == 2
    for probe in profile.probes:
        assert probe["reason"] == "control_compile_failed"
        normal = probe["normal"]
        assert isinstance(normal, dict)
        compilation = normal["compile"]
        assert isinstance(compilation, dict)
        assert compilation["command"][0] == missing


@pytest.mark.parametrize(
    "flag",
    [
        "-fsanitize=address",
        "-fno-sanitize=undefined",
        "-fsanitize-recover=all",
        "-fsanitize-undefined-trap-on-error",
        "@flags",
        "-c",
        "-S",
        "-E",
        "-MMD",
        "-fsyntax-only",
        "-nostdlib",
        "-nodefaultlibs",
        "-r",
        "-shared",
        "-x",
        "-xc++",
        "-save-temps=obj",
        "-fdump-tree-all",
        "-MJout.json",
        "-fprofile-generate",
        "-fdiagnostics-format=sarif-file",
        "-fdiagnostics-format=json-file",
        "-Wl,-Map=output.map",
        "-fplugin=plugin.so",
        "--specs=custom.specs",
    ],
)
def test_rejects_options_which_bypass_checks_or_write_extra_files(flag: str) -> None:
    with pytest.raises(UsageError, match="--check-undefined"):
        undefined.validate_undefined_options([flag])


def test_keeps_semantic_arguments_and_rejects_nested_response_file() -> None:
    undefined.validate_undefined_options(
        [
            "-I",
            "include",
            "-Llib",
            "-D",
            "-fsanitize=macro-text",
            "-UOLD",
            "-std=c99",
            "-O3",
            "-include",
            "header.h",
            "-o",
            "my-program",
            "-lm",
            "-pthread",
            "-flto",
        ]
    )
    with pytest.raises(UsageError):
        undefined.validate_undefined_options(["-include", "@response"])


def test_bounded_build_preserves_command_and_never_runs_output(gcc: str, tmp_path: Path) -> None:
    source = tmp_path / "hello.c"
    marker = tmp_path / "must-not-run"
    source.write_text(
        '#include <stdio.h>\nint main(void) { FILE *f = fopen("must-not-run", "w"); '
        "if (f) fclose(f); return VALUE == 17 ? 0 : 1; }\n",
        encoding="utf-8",
    )
    arguments = ["-std=c99", "-O2", "-DVALUE=17", "-o", "program"]
    result = undefined.bounded_c_build(gcc, source, arguments, tmp_path)
    assert result.command == [gcc, str(source), *arguments]
    assert result.returncode == 0
    expected = "program.exe" if os.name == "nt" else "program"
    assert result.artifact == tmp_path / expected
    assert not marker.exists()


def test_compilation_environment_excludes_secrets_and_loader_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UBSAN_OPTIONS", "halt_on_error=0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    monkeypatch.setenv("LD_PRELOAD", "/missing/preload")
    monkeypatch.setenv("CPATH", "/unrequested/include")
    monkeypatch.setenv("LIB", "platform-libraries")
    source = tmp_path / "environment.py"
    source.write_text(
        "import os\nprint('|'.join(os.environ.get(k, 'absent') for k in "
        "['UBSAN_OPTIONS', 'OPENAI_API_KEY', 'LD_PRELOAD', 'CPATH', 'LIB']))\n",
        encoding="utf-8",
    )
    result = undefined.bounded_c_build(sys.executable, source, [], tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "absent|absent|absent|absent|platform-libraries"


def test_compiler_output_is_bounded_even_when_process_exits_zero(tmp_path: Path) -> None:
    source = tmp_path / "writer.py"
    source.write_text("import os\nos.write(1, b'x' * 2000000)\n", encoding="utf-8")
    result = undefined.bounded_c_build(sys.executable, source, [], tmp_path)
    assert result.returncode != 0
    assert len(result.stdout.encode("utf-8")) <= undefined.DEFAULT_MAX_OUTPUT_BYTES
    assert "output limit" in result.stderr
    assert result.artifact is None


def test_compilation_timeout_and_missing_command_are_explicit(tmp_path: Path) -> None:
    source = tmp_path / "slow.py"
    source.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    started = time.monotonic()
    timeout = undefined.bounded_c_build(sys.executable, source, [], tmp_path, timeout_s=0.1)
    assert time.monotonic() - started < 3
    assert timeout.returncode == 124
    assert "timeout" in timeout.stderr
    assert timeout.artifact is None
    missing = undefined.bounded_c_build(str(tmp_path / "missing"), source, [], tmp_path)
    assert missing.returncode != 0
    assert "launch failed" in missing.stderr


def test_total_probe_deadline_caps_each_compile_and_does_not_restart_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [10.0]
    calls: list[float] = []
    monkeypatch.setattr(undefined.time, "monotonic", lambda: clock[0])

    def consume_budget(
        compiler: str,
        source: Path,
        arguments: list[str],
        cwd: Path,
        *,
        timeout_s: float,
    ) -> BuildResult:
        calls.append(timeout_s)
        clock[0] += timeout_s
        return BuildResult([compiler, str(source), *arguments], 124, "", "timeout")

    monkeypatch.setattr(undefined, "bounded_c_build", consume_budget)
    profile = undefined.select_undefined_profile(_adapter(), [], tmp_path, timeout_s=0.5)
    assert not profile.available
    assert profile.reason == "probe_timeout"
    assert calls == [0.5]


def test_positive_control_exit_zero_without_marker_is_not_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def compile_control(
        compiler: str,
        source: Path,
        arguments: list[str],
        cwd: Path,
        **kwargs: Any,
    ) -> BuildResult:
        executable = Path(arguments[-1])
        executable.touch()
        return BuildResult([compiler, str(source), *arguments], 0, "", "", executable)

    monkeypatch.setattr(undefined, "bounded_c_build", compile_control)
    monkeypatch.setattr(
        undefined,
        "_control_run",
        lambda *args: {
            "passed_execution": True,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
        },
    )
    profile = undefined.select_undefined_profile(_adapter(), [], tmp_path)
    assert not profile.available
    assert all(probe["reason"] == "normal_control_failed" for probe in profile.probes)


@pytest.mark.skipif(os.name != "posix", reason="POSIX GCC trap 使用负信号退出码")
def test_trap_control_normal_nonzero_exit_is_not_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def compile_control(
        compiler: str,
        source: Path,
        arguments: list[str],
        cwd: Path,
        **kwargs: Any,
    ) -> BuildResult:
        executable = Path(arguments[-1])
        executable.touch()
        return BuildResult([compiler, str(source), *arguments], 0, "", "", executable)

    def run_control(executable: Path, *args: object) -> dict[str, object]:
        normal = "normal" in executable.name
        return {
            "passed_execution": True,
            "exit_code": 0 if normal else 1,
            "stdout": undefined._MARKER.decode("ascii") if normal else "",
            "stderr": "",
        }

    monkeypatch.setattr(undefined, "bounded_c_build", compile_control)
    monkeypatch.setattr(undefined, "_control_run", run_control)
    profile = undefined.select_undefined_profile(_adapter(), [], tmp_path)
    assert not profile.available
    assert all(probe["reason"] == "overflow_control_failed" for probe in profile.probes)


@pytest.mark.parametrize("failure", ["timeout", "output_limit", "launch_failed"])
def test_control_execution_failure_never_establishes_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    def compile_control(
        compiler: str,
        source: Path,
        arguments: list[str],
        cwd: Path,
        **kwargs: Any,
    ) -> BuildResult:
        executable = Path(arguments[-1])
        executable.touch()
        return BuildResult([compiler, str(source), *arguments], 0, "", "", executable)

    monkeypatch.setattr(undefined, "bounded_c_build", compile_control)
    monkeypatch.setattr(
        undefined,
        "_control_run",
        lambda *args: {
            "passed_execution": False,
            "error": failure,
        },
    )
    profile = undefined.select_undefined_profile(_adapter(), [], tmp_path)
    assert not profile.available
    assert all(probe["reason"] == "control_execution_failed" for probe in profile.probes)


def test_probe_keeps_semantic_flags_resolves_search_paths_and_overrides_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def refuse_compile(
        compiler: str,
        source: Path,
        arguments: list[str],
        cwd: Path,
        **kwargs: Any,
    ) -> BuildResult:
        commands.append([compiler, str(source), *arguments])
        return BuildResult(commands[-1], 1, "", "deliberate unsupported compiler")

    monkeypatch.setattr(undefined, "bounded_c_build", refuse_compile)
    options = ["-O3", "-std=c99", "-I", "headers", "-Llibs", "-DVALUE=4", "-o", "user.exe"]
    profile = undefined.select_undefined_profile(_adapter("same-gcc"), options, tmp_path)
    assert not profile.available
    assert len(commands) == 2
    for command in commands:
        assert command[0] == "same-gcc"
        assert command[1].endswith(".c")
        assert "-O3" in command
        assert "-std=c99" in command
        assert "-DVALUE=4" in command
        assert str(tmp_path / "headers") in command
        assert "-L" + str(tmp_path / "libs") in command
        assert "user.exe" not in command
        assert command.count("-o") == 1
        assert Path(command[-1]).parent != tmp_path
    assert not (tmp_path / "user.exe").exists()


def test_control_runner_is_safe_bounded_and_does_not_print(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from yescc.sandbox.creative import CreativeRun
    from yescc.sandbox.runner import SandboxOutcome

    calls: list[dict[str, object]] = []

    def run(command: list[str], **kwargs: Any) -> CreativeRun:
        calls.append(kwargs)
        return CreativeRun(
            sandbox=SandboxOutcome(0, False, True, False, 3.0),
            stdout=undefined._MARKER,
            stderr=b"",
            validation={"performed": True},
        )

    monkeypatch.setattr(undefined, "run_creative_command", run)
    result = undefined._control_run(tmp_path / "control.exe", tmp_path, 9.0)
    assert result["passed_execution"] is True
    assert calls[0]["unsafe"] is False
    assert calls[0]["timeout_s"] == 3.0
    assert calls[0]["max_output_bytes"] == undefined.DEFAULT_MAX_OUTPUT_BYTES
    assert capsys.readouterr() == ("", "")


def test_entire_probe_has_twenty_second_ceiling_and_reports_actual_elapsed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [10.0]
    calls: list[float] = []
    monkeypatch.setattr(undefined.time, "monotonic", lambda: clock[0])

    def consume_budget(
        compiler: str,
        source: Path,
        arguments: list[str],
        cwd: Path,
        *,
        timeout_s: float,
    ) -> BuildResult:
        calls.append(timeout_s)
        clock[0] += timeout_s
        return BuildResult([compiler, str(source), *arguments], 124, "", "timeout")

    monkeypatch.setattr(undefined, "bounded_c_build", consume_budget)
    profile = undefined.select_undefined_profile(_adapter(), [], tmp_path, timeout_s=50)
    assert not profile.available
    assert calls == [20.0]
    assert profile.as_dict()["timeout_s"] == 20.0
    assert profile.as_dict()["elapsed_ms"] == 20000
    assert "timeout_s" not in profile.identity()
    assert "elapsed_ms" not in profile.identity()


@pytest.mark.parametrize("stage", ["compile", "control"])
def test_probe_cancellation_never_falls_back_or_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    compiles: list[list[str]] = []
    runs: list[Path] = []

    def compile_control(
        compiler: str,
        source: Path,
        arguments: list[str],
        cwd: Path,
        **kwargs: Any,
    ) -> BuildResult:
        command = [compiler, str(source), *arguments]
        compiles.append(command)
        if stage == "compile":
            return BuildResult(command, 130, "", "interrupted")
        executable = Path(arguments[-1])
        executable.touch()
        return BuildResult(command, 0, "", "", executable)

    def interrupted_control(executable: Path, *args: object) -> dict[str, object]:
        runs.append(executable)
        return {"passed_execution": False, "exit_code": 130, "error": "interrupted"}

    monkeypatch.setattr(undefined, "bounded_c_build", compile_control)
    monkeypatch.setattr(undefined, "_control_run", interrupted_control)
    with pytest.raises(KeyboardInterrupt):
        undefined.select_undefined_profile(_adapter(), [], tmp_path)
    assert len(compiles) == 1
    assert len(runs) == (1 if stage == "control" else 0)
    assert "-fno-sanitize-recover=undefined" in compiles[0]


def test_bounded_build_preserves_cancellation_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yescc.sandbox.runner import SandboxOutcome

    monkeypatch.setattr(
        undefined,
        "_run_once",
        lambda *args, **kwargs: SandboxOutcome(
            130,
            False,
            True,
            False,
            20.0,
        ),
    )
    source = tmp_path / "control.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    result = undefined.bounded_c_build("gcc", source, [], tmp_path)
    assert result.returncode == 130
    assert result.artifact is None


def test_network_initialization_is_charged_to_entire_probe_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from functools import lru_cache

    clock = [10.0]
    run_budgets: list[float] = []
    monkeypatch.setattr(undefined.time, "monotonic", lambda: clock[0])

    @lru_cache(maxsize=1)
    def initialize_network() -> bool:
        clock[0] += 2.0
        return False

    def compile_control(
        compiler: str,
        source: Path,
        arguments: list[str],
        cwd: Path,
        **kwargs: Any,
    ) -> BuildResult:
        executable = Path(arguments[-1])
        executable.touch()
        return BuildResult([compiler, str(source), *arguments], 0, "", "", executable)

    def consume_remaining(
        executable: Path,
        cwd: Path,
        timeout_s: float,
    ) -> dict[str, object]:
        run_budgets.append(timeout_s)
        clock[0] += timeout_s
        return {"passed_execution": False, "error": "timeout"}

    monkeypatch.setattr(undefined, "_network_isolation_supported", initialize_network)
    monkeypatch.setattr(undefined, "bounded_c_build", compile_control)
    monkeypatch.setattr(undefined, "_control_run", consume_remaining)
    profile = undefined.select_undefined_profile(_adapter(), [], tmp_path, timeout_s=4)
    assert not profile.available
    assert profile.reason == "probe_timeout"
    assert run_budgets == [2.0]
    assert profile.as_dict()["timeout_s"] == 4.0
    assert profile.as_dict()["elapsed_ms"] == 4000
