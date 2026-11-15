"""Actionable failure messages use real tool discovery and local model responses."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import FakeModelServer
from yescc.cli import main

pytestmark = pytest.mark.e2e

TEXT = "一只在月球上报告天气的纸鹤"
GOOD = '#include <stdio.h>\nint main(void) { puts("GOOD"); return 0; }\n'
BAD_EXIT = (
    '#include <stdio.h>\nint main(void) { puts("PRIVATE_REJECTED_OUTPUT"); '
    'fputs("PRIVATE_REJECTED_ERROR\\n", stderr); return 7; }\n'
)
OVERFLOW = (
    '#include <stdio.h>\n#include <limits.h>\nint main(void) { '
    'puts("PRIVATE_REJECTED_OUTPUT"); fflush(stdout); '
    'volatile int upper = INT_MAX; volatile int one = 1; '
    'volatile int sum = upper + one; (void)sum; return 0; }\n'
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    source = tmp_path / name
    source.write_text(text, encoding="utf-8")
    return source


def _run(
    tmp_path: Path, *args: str, empty_path: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if empty_path:
        directory = tmp_path / "empty-tools"
        directory.mkdir(exist_ok=True)
        env["PATH"] = str(directory)
    return subprocess.run(
        [sys.executable, "-m", "yescc", *args], cwd=tmp_path,
        env=env, capture_output=True, timeout=60, check=False,
    )


def _report_file(tmp_path: Path, source: Path) -> Path:
    return tmp_path / ".yescc" / source.stem / "report.json"


def _assert_unavailable_report(saved: dict, tool: str) -> str:
    assert saved["schema_version"] == "1" and "report_kind" not in saved
    assert saved["result"]["status"] == "failed"
    message = saved["result"]["failure_message"]
    assert isinstance(message, str) and tool.lower() in message.lower()
    assert saved["attempts"] == [] and saved["diagnostics"] == []
    assert saved["toolchain"]["command"] == saved["toolchain"]["version"] == ""
    assert saved["result"]["artifact"] is None
    assert saved["model"] is None
    assert saved["privacy"] == {"cloud_used": False, "uploaded_bytes": 0}
    assert saved["budget"]["attempts"] == 0
    return message


@pytest.mark.parametrize("name,text,tool", [
    ("missing.c", "int main(void) { return 0; }\n", "gcc"),
    ("missing.js", "console.log('never run');\n", "Node.js"),
])
def test_missing_tools_json_and_explain_preserve_actual_cause(
    tmp_path: Path, fake_model: FakeModelServer, name: str, text: str, tool: str,
) -> None:
    source = _write(tmp_path, name, text)
    original = source.read_bytes()
    built = _run(tmp_path, str(source), "--strict", "--json", empty_path=True)
    assert built.returncode == 102 and built.stderr == b""
    saved = json.loads(built.stdout)
    message = _assert_unavailable_report(saved, tool)
    report_file = _report_file(tmp_path, source)
    before = report_file.read_bytes()
    assert json.loads(before) == saved
    explain_json = _run(tmp_path, str(source), "--explain", "--json", empty_path=True)
    assert explain_json.returncode == 0 and explain_json.stderr == b""
    assert json.loads(explain_json.stdout) == saved
    explained = _run(tmp_path, str(source), "--explain", empty_path=True)
    assert explained.returncode == 0 and explained.stderr == b""
    assert "失败原因：" in explained.stdout.decode("utf-8")
    assert message in explained.stdout.decode("utf-8")
    assert report_file.read_bytes() == before and source.read_bytes() == original
    assert not fake_model.requests


@pytest.mark.parametrize("name,text,tool", [
    ("missing.c", "int main(void) { return 0; }\n", "gcc"),
    ("missing.js", "console.log('never run');\n", "Node.js"),
])
def test_missing_tools_text_still_explains_environment_failure(
    tmp_path: Path, fake_model: FakeModelServer, name: str, text: str, tool: str,
) -> None:
    source = _write(tmp_path, name, text)
    original = source.read_bytes()
    built = _run(tmp_path, str(source), "--strict", empty_path=True)
    assert built.returncode == 102 and built.stdout == b""
    saved = json.loads(_report_file(tmp_path, source).read_text("utf-8"))
    message = _assert_unavailable_report(saved, tool)
    assert message in built.stderr.decode("utf-8")
    assert source.read_bytes() == original and not fake_model.requests


@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
def test_missing_frozen_python_preserves_cause_without_fake_build_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    fake_model: FakeModelServer, as_json: bool,
) -> None:
    """Simulate frozen discovery with a real empty PATH, not a binary install test."""
    source = _write(tmp_path, "missing.py", "print('never run')\n")
    original = source.read_bytes()
    directory = tmp_path / "empty-tools"
    directory.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(directory))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    args = [str(source), "--strict", *(["--json"] if as_json else [])]
    assert main(args) == 102
    output = capsys.readouterr()
    saved = json.loads(_report_file(tmp_path, source).read_text("utf-8"))
    message = _assert_unavailable_report(saved, "Python 3")
    if as_json:
        assert json.loads(output.out) == saved and output.err == ""
    else:
        assert output.out == "" and message in output.err
    assert source.read_bytes() == original and not fake_model.requests


def test_missing_fallback_compiler_json_is_an_environment_failure(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    source = _write(tmp_path, "idea.txt", TEXT)
    original = source.read_bytes()
    built = _run(tmp_path, str(source), "--offline", "--json", empty_path=True)
    assert built.returncode == 102 and built.stderr == b""
    saved = json.loads(built.stdout)
    _assert_unavailable_report(saved, "gcc")
    assert saved["result"]["fallback"] is None
    assert source.read_bytes() == original and not fake_model.requests


@pytest.mark.parametrize("checked", [False, True], ids=["ordinary", "checked"])
def test_rejected_run_is_explained_alongside_attempt_limit(
    tmp_path: Path, fake_model: FakeModelServer, checked: bool,
) -> None:
    source = _write(tmp_path, "idea.txt", TEXT)
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    fake_model.responses.append({"source": OVERFLOW if checked else BAD_EXIT})
    flags = ["--check-undefined"] if checked else []
    built = _run(tmp_path, "run", str(source), "--play", "--max-rounds", "1", *flags)
    assert built.returncode == 0
    assert built.stdout == (TEXT + os.linesep).encode()
    assert b"PRIVATE_REJECTED" not in built.stdout + built.stderr
    saved = json.loads(_report_file(tmp_path, source).read_text("utf-8"))
    assert saved["result"]["fallback"]["kind"] == "print_input"
    assert saved["model"]["calls"] == saved["budget"]["attempts"] == 1
    assert len(fake_model.requests) == 1
    attempt = next(a for a in saved["attempts"] if a["kind"] == "model")
    assert attempt["validation"]["performed"] and attempt["validation"]["passed"] is False
    assert "exit_nonzero" in attempt["validation"]["reasons"]
    hints = saved["result"]["fallback"]["guidance"]
    joined = "\n".join(hints)
    assert "非零" in joined and str(attempt["validation"]["exit_code"]) in joined
    assert "拒绝" in joined and "最大候选尝试次数" in joined
    stderr = built.stderr.decode("utf-8")
    assert all(hint in stderr for hint in hints)
    if checked:
        check = attempt["undefined_behavior_check"]
        assert check["performed"] and check["mode"] in {"runtime", "trap"}
        assert check["mode"] in joined and check["ub_detected"] is None
    report_before = _report_file(tmp_path, source).read_bytes()
    explained = _run(tmp_path, str(source), "--explain")
    assert explained.returncode == 0 and all(hint in explained.stdout.decode("utf-8")
                                             for hint in hints)
    assert _report_file(tmp_path, source).read_bytes() == report_before
    assert len(fake_model.requests) == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


def test_successful_retry_only_forwards_successful_program_streams(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    source = _write(tmp_path, "idea.txt", TEXT)
    fake_model.responses.extend([{"source": BAD_EXIT}, {"source": GOOD}])
    built = _run(tmp_path, "run", str(source), "--play")
    assert built.returncode == 0 and built.stdout == ("GOOD" + os.linesep).encode()
    assert b"PRIVATE_REJECTED" not in built.stdout + built.stderr
    assert "非零" not in built.stderr.decode("utf-8")
    saved = json.loads(_report_file(tmp_path, source).read_text("utf-8"))
    assert saved["result"]["fallback"]["kind"] == "model_creative"
    assert "failure_message" not in saved["result"]
    assert saved["result"]["generated"]["validation"]["passed"] is True
    assert saved["model"]["calls"] == saved["budget"]["attempts"] == 2
    assert len(fake_model.requests) == 2


def test_json_creative_build_does_not_execute_or_claim_run_failure(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    source = _write(tmp_path, "idea.txt", TEXT)
    marker = tmp_path / "must-not-run"
    candidate = (
        '#include <stdio.h>\nint main(void) { FILE *f = fopen('
        + json.dumps(marker.as_posix())
        + ', "w"); if (f) fclose(f); puts("PRIVATE_BUILD_OUTPUT"); return 7; }\n'
    )
    fake_model.responses.append({"source": candidate})
    built = _run(tmp_path, str(source), "--play", "--json")
    assert built.returncode == 0 and built.stderr == b"" and not marker.exists()
    saved = json.loads(built.stdout)
    assert saved == json.loads(_report_file(tmp_path, source).read_text("utf-8"))
    assert saved["result"]["generated"]["validation"] == {
        "performed": False, "passed": None, "reason": "build_only",
    }
    assert "failure_message" not in saved["result"]
    assert len(fake_model.requests) == 1


def test_native_nonzero_program_exit_preserves_streams_without_failure_hint(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    source = _write(tmp_path, "native.c", '#include <stdio.h>\nint main(void) {'
                    'puts("NATIVE_OUTPUT"); fputs("NATIVE_ERROR\\n", stderr); return 7; }\n')
    before = source.read_bytes()
    built = _run(tmp_path, "run", str(source))
    assert built.returncode == 7
    assert built.stdout == ("NATIVE_OUTPUT" + os.linesep).encode()
    assert built.stderr == ("NATIVE_ERROR" + os.linesep).encode()
    saved = json.loads(_report_file(tmp_path, source).read_text("utf-8"))
    assert saved["result"]["status"] == "native_ok"
    assert saved["result"]["program_exit"] == 7
    assert "failure_message" not in saved["result"]
    assert source.read_bytes() == before and not fake_model.requests


def test_strict_compiler_failure_keeps_original_native_output(
    tmp_path: Path, fake_model: FakeModelServer,
) -> None:
    source = _write(tmp_path, "broken.c", "int main(void) { return unknown_native_symbol; }\n")
    compiler = shutil.which("gcc") or shutil.which("gcc.exe") or shutil.which("cc")
    assert compiler is not None
    native = subprocess.run([compiler, str(source)], cwd=tmp_path,
                            capture_output=True, timeout=30, check=False)
    assert native.returncode != 0
    built = _run(tmp_path, str(source), "--strict")
    assert built.returncode == 102
    assert built.stdout == native.stdout and built.stderr == native.stderr
    assert not fake_model.requests
