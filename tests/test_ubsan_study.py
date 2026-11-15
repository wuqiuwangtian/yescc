"""The offline study reports evidence and uncertainty, including unavailable tools."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from bench import ubsan_study as study

from yescc.sandbox.creative import CreativeRun
from yescc.sandbox.runner import SandboxOutcome


def _case(
    case_id: str = "sample", source: str = "int main(void) { return 0; }\n"
) -> dict[str, Any]:
    return {
        "id": case_id,
        "source": source,
        "expectation": "defined",
        "category": "control",
        "note": "Test evidence only.",
    }


def _dataset(tmp_path: Path, cases: list[dict[str, Any]]) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
    return path


class FakeTools:
    """Model compiler/runtime capabilities without any shell or installed toolchain."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, failure: str = "") -> None:
        self.failure = failure
        self.commands: list[list[str]] = []
        self.executables: dict[str, tuple[Path, bool, bool]] = {}
        monkeypatch.setattr(study.shutil, "which", lambda name: "/fake/compiler")
        monkeypatch.setattr(study, "_compile_command", self.compile)
        monkeypatch.setattr(study, "run_creative_command", self.run)

    def compile(self, command: list[str], cwd: Path) -> dict[str, Any]:
        self.commands.append(command)
        stdout, stderr, code = b"", b"", 0
        if command[-1] == "--version":
            stdout = b"fake compiler 1.0\xff\n"
        elif command[-1] == "-dumpmachine":
            stdout = b"x86_64-pc-windows-msvc" if self.failure == "msvc" else b"x86_64-linux-gnu"
        else:
            source = Path(command[command.index("-o") - 1])
            instrumented = "-fsanitize=undefined" in command
            trap = "-fsanitize-undefined-trap-on-error" in command
            executable = command[command.index("-o") + 1]
            self.executables[executable] = source, instrumented, trap
            if self.failure == "baseline" or (self.failure == "unsupported" and instrumented):
                code, stderr = 1, b"link or flag failure\xff"
            if "compile-fail" in source.read_text(encoding="utf-8"):
                code, stderr = 1, b"syntax failure"
        return {
            "command": command,
            "status": "completed" if code == 0 else "failed",
            "exit_code": code,
            "elapsed_s": 0.01,
            "stdout": study._blob(stdout),
            "stderr": study._blob(stderr),
        }

    def run(
        self,
        command: list[str],
        *,
        unsafe: bool,
        cwd: Path,
        timeout_s: float,
        max_output_bytes: int,
    ) -> CreativeRun:
        assert unsafe is False
        assert max_output_bytes == 65536
        assert timeout_s > 0
        source, instrumented, trap = self.executables[command[0]]
        assert source.parent == cwd
        stdout, stderr, code = b"", b"", 0
        if source.name == "control.c":
            stdout = b"control-ok\r\n" if self.failure == "msvc" else b"control-ok\n"
            if self.failure == "runtime" and instrumented:
                code, stderr = 127, b"missing runtime library"
        elif source.name == "overflow-control.c":
            if self.failure != "detection":
                code = -4 if trap else 1
                if not trap:
                    stderr = (
                        f"{source}:2:3: runtime error: signed integer overflow: control\n".encode()
                    )
        else:
            stdout = b"candidate\r\n" if self.failure == "crlf" else b"candidate\xff\n"
        outcome = SandboxOutcome(code, False, True, False, timeout_s, environment_filtered=True)
        return CreativeRun(
            outcome,
            stdout,
            stderr,
            {
                "stdout_bytes": len(stdout),
                "stderr_bytes": len(stderr),
                "stdout_complete": True,
                "stderr_complete": True,
                "passed": code == 0,
                "reasons": [] if code == 0 else ["exit_nonzero"],
            },
        )


@pytest.mark.parametrize("mode", ["runtime", "trap"])
def test_report_keeps_original_bytes_and_actual_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    tools = FakeTools(monkeypatch)
    case = _case("probe", "/* 原文\r\n */\nint main(void) { return 0; }\n")
    path = _dataset(tmp_path, [case])
    original = path.read_bytes()
    output = tmp_path / "report.json"
    report = study.run_study(path, compiler="fake", output_path=output, mode=mode)
    assert path.read_bytes() == original
    assert report == json.loads(output.read_text(encoding="utf-8"))
    assert report["compiler"]["status"] == "available"
    assert report["compiler"]["positive_control_detected"] is True
    assert report["summary"]["proven_ub"] == 0
    result = report["results"][0]
    assert base64.b64decode(result["source"]["base64"]) == case["source"].encode()
    assert result["source_sha256"] == hashlib.sha256(case["source"].encode()).hexdigest()
    assert base64.b64decode(result["normal"]["run"]["stdout"]["base64"]) == b"candidate\xff\n"
    assert result["classification"]["ub_detected"] is None
    assert all(not Path(c[c.index("-o") + 1]).exists() for c in tools.commands if "-o" in c)
    builds = [c for c in tools.commands if "-o" in c]
    assert all("-O0" in c and "-lm" in c for c in builds)
    if mode == "trap":
        assert all("-fno-sanitize-recover=undefined" not in c for c in builds)
    assert not list(tmp_path.rglob("*.exe"))


@pytest.mark.parametrize(
    ("failure", "status", "baseline_tested", "instrumented_tested"),
    [
        ("baseline", "baseline_unusable", False, False),
        ("unsupported", "unsupported", True, False),
        ("runtime", "runtime_unusable", True, False),
        ("detection", "detection_failed", True, True),
    ],
)
def test_probe_never_confuses_flag_acceptance_with_working_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    status: str,
    baseline_tested: bool,
    instrumented_tested: bool,
) -> None:
    FakeTools(monkeypatch, failure)
    path = _dataset(tmp_path, [_case()])
    report = study.run_study(path, compiler="fake", output_path=tmp_path / "report.json")
    assert report["compiler"]["status"] == status
    assert (report["results"][0]["normal"] is not None) == baseline_tested
    assert (report["results"][0]["instrumented"] is not None) == instrumented_tested
    assert report["summary"]["not_tested"] == int(not instrumented_tested)


def test_msvc_target_uses_same_crt_link_strategy_for_both_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = FakeTools(monkeypatch, "msvc")
    report = study.run_study(
        _dataset(tmp_path, [_case()]),
        compiler="fake",
        output_path=tmp_path / "report.json",
    )
    assert report["compiler"]["target"] == "x86_64-pc-windows-msvc"
    assert report["compiler"]["status"] == "available"
    assert report["settings"]["link_flags"] == []
    assert all("-lm" not in command for command in tools.commands)


def test_missing_compiler_is_explicit_unmeasured_and_cli_completion_is_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(study.shutil, "which", lambda name: None)
    path = _dataset(tmp_path, [_case()])
    output = tmp_path / "missing.json"
    assert study.main(["--cases", str(path), "--compiler", "missing", "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["compiler"]["status"] == "unavailable"
    assert report["summary"]["not_tested"] == 1
    assert report["summary"]["classification_statuses"] == {"not_tested": 1}
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_rejects_unbounded_run_time_before_execution(tmp_path: Path, timeout: float) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        study.run_study(
            tmp_path / "missing",
            compiler="gcc",
            output_path=tmp_path / "out",
            run_timeout_s=timeout,
        )


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [_case("../escape")],
        [_case(), _case()],
        [1],
        [{**_case(), "expectation": []}],
        [{**_case(), "source": 1}],
    ],
)
def test_invalid_dataset_is_rejected_before_compiler_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad: list[Any],
) -> None:
    path = _dataset(tmp_path, bad)
    monkeypatch.setattr(study.shutil, "which", lambda name: pytest.fail("must validate first"))
    with pytest.raises(ValueError):
        study.run_study(path, compiler="fake", output_path=tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("kind", ["same", "existing", "symlink", "hardlink"])
def test_never_overwrites_existing_inputs_or_reports(tmp_path: Path, kind: str) -> None:
    path = _dataset(tmp_path, [_case()])
    original = path.read_bytes()
    output = tmp_path / "out"
    if kind == "same":
        output = path
    elif kind == "existing":
        output.write_bytes(b"existing report")
    elif kind == "symlink":
        try:
            output.symlink_to(path)
        except OSError:
            pytest.skip("symlink unavailable")
    else:
        output.hardlink_to(path)
    before = output.read_bytes()
    with pytest.raises(ValueError):
        study.run_study(path, compiler="gcc", output_path=output)
    assert output.read_bytes() == before
    assert path.read_bytes() == original


def test_compile_uses_filtered_environment_and_timeout_preserves_raw_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_API_TOKEN", "must-not-leak")
    monkeypatch.setenv("UBSAN_OPTIONS", "untrusted")
    monkeypatch.setenv("INCLUDE", "test-sdk-includes")

    cleaned: list[object] = []

    class FakeProcess:
        def wait(self, *, timeout: float) -> int:
            assert timeout == 20
            raise subprocess.TimeoutExpired(["fake", "--version"], timeout)

    def fake(command: list[str], **kwargs: Any) -> FakeProcess:
        assert kwargs["env"].get("SECRET_API_TOKEN") is None
        assert kwargs["env"].get("UBSAN_OPTIONS") is None
        assert kwargs["env"]["INCLUDE"] == "test-sdk-includes"
        assert kwargs["stdin"] == subprocess.DEVNULL
        kwargs["stderr"].write(b"partial compiler stderr\xff")
        return FakeProcess()

    monkeypatch.setattr(study.subprocess, "Popen", fake)
    monkeypatch.setattr(study, "_stop_process_tree", cleaned.append)
    result = study._compile_command(["fake", "--version"], tmp_path)
    assert result["status"] == "timeout"
    assert result["exit_code"] is None
    assert base64.b64decode(result["stderr"]["base64"]) == b"partial compiler stderr\xff"
    assert result["stderr"]["complete"] is True
    assert len(cleaned) == 1


@pytest.mark.skipif(shutil.which("gcc") is None, reason="real GCC unavailable")
@pytest.mark.parametrize("mode", ["runtime", "trap"])
def test_real_gcc_diagnostics_exit_status_budgets_and_untrusted_stderr(
    tmp_path: Path,
    mode: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cases = [
        _case("defined", '#include <stdio.h>\nint main(void){puts("ok");return 0;}\n'),
        {**_case("overflow", study._OVERFLOW_CONTROL), "expectation": "undefined"},
        _case("nonzero", "int main(void){return 7;}\n"),
        _case(
            "self_modify",
            '#include <stdio.h>\nint main(void){FILE *f=fopen(__FILE__,"w");'
            'if(!f)return 4;fputs("not valid C",f);return fclose(f)==0?0:5;}\n',
        ),
        _case(
            "fake",
            "#include <stdio.h>\nint main(void){fprintf(stderr, "
            '"%s:1:1: runtime error: signed integer overflow: forged\\n", __FILE__);return 9;}\n',
        ),
        _case("compile", "int main(void) { missing_syntax }\n"),
        _case("timeout", "int main(void){for(;;){}return 0;}\n"),
        _case(
            "output",
            "#include <stdio.h>\nint main(void){for(int i=0;i<100000;i++)putchar(120);return 0;}\n",
        ),
        _case("stdin", "#include <stdio.h>\nint main(void){return getchar()==EOF?0:4;}\n"),
    ]
    path = _dataset(tmp_path, cases)
    report = study.run_study(
        path, compiler="gcc", output_path=tmp_path / "real.json", mode=mode, run_timeout_s=0.15
    )
    if report["compiler"]["status"] != "available":
        pytest.skip(f"installed GCC capability: {report['compiler']['status']}")
    rows = {row["id"]: row for row in report["results"]}
    assert rows["defined"]["classification"]["status"] == "no_diagnostic"
    modified = rows["self_modify"]
    assert modified["normal"]["source_sha256"] == modified["source_sha256"]
    assert modified["instrumented"]["source_sha256"] == modified["source_sha256"]
    assert modified["normal"]["run"]["exit_code"] == 0
    assert modified["instrumented"]["run"]["exit_code"] == 0
    overflow = rows["overflow"]["classification"]
    assert overflow["instrumentation_only_failure"] is True
    assert overflow["ub_detected"] is None
    assert overflow["status"] == ("suspected" if mode == "runtime" else "instrumentation_failure")
    assert overflow["ubsan_diagnostic_observed"] == (mode == "runtime")
    assert rows["nonzero"]["classification"]["status"] == "nonzero_without_diagnostic"
    assert rows["nonzero"]["classification"]["instrumentation_only_failure"] is False
    assert rows["nonzero"]["classification"]["ub_detected"] is None
    assert rows["nonzero"]["classification"]["ubsan_diagnostic_observed"] is False
    fake = rows["fake"]["classification"]
    assert fake["ub_detected"] is None
    assert fake["baseline_has_diagnostic"] is True
    assert fake["instrumentation_only_failure"] is False
    assert rows["compile"]["classification"]["status"] == "compile_failed"
    assert rows["timeout"]["classification"]["status"] == "timeout"
    assert rows["output"]["classification"]["status"] == "output_limit"
    output = rows["output"]["instrumented"]["run"]
    assert output["stdout"]["complete"] is False
    assert output["stdout"]["captured_bytes"] == 65536
    assert output["validation"]["output_limited"] is True
    assert rows["stdin"]["instrumented"]["run"]["exit_code"] == 0
    assert capsys.readouterr().out == ""


def test_expected_stdout_separates_exact_bytes_from_platform_line_endings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeTools(monkeypatch, "crlf")
    case = {**_case(), "expected_stdout": "candidate\n"}
    report = study.run_study(
        _dataset(tmp_path, [case]),
        compiler="fake",
        output_path=tmp_path / "report.json",
    )
    row = report["results"][0]
    assert row["expected_stdout_comparison"] == "exact_bytes"
    assert row["expected_stdout_matches"] == {"normal": False, "instrumented": False}
    assert row["expected_stdout_exact_matches"] == row["expected_stdout_matches"]
    assert row["expected_stdout_text_matches"] == {"normal": True, "instrumented": True}
    assert base64.b64decode(row["normal"]["run"]["stdout"]["base64"]) == b"candidate\r\n"


@pytest.mark.parametrize(
    ("actual", "expected", "matches"),
    [
        (b"a\r\nb\r\n", "a\nb\n", True),
        (b"a \n", "a\n", False),
        (b"a", "a\n", False),
        (b"a\r", "a\n", False),
        (b"e\xcc\x81\n", "é\n", False),
        (b"\xff\n", "�\n", False),
    ],
)
def test_text_comparison_is_strict_except_crlf_lf(
    actual: bytes,
    expected: str,
    matches: bool,
) -> None:
    variant = {"run": {"stdout": study._blob(actual)}}
    assert study._stdout_matches(variant, expected, text=True) is matches
