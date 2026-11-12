"""创意运行检查必须依据真实退出/原始字节，并保持普通执行的继承流。"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from yescc.sandbox import run_ai_command, run_creative_command, runner

PYTHON = sys.executable


def _command(code: str) -> list[str]:
    return [PYTHON, "-c", code]


def test_capture_keeps_utf8_and_stderr_bytes_without_printing(
    tmp_path: Path, capfdbinary: pytest.CaptureFixture[bytes]
) -> None:
    outcome = run_creative_command(
        _command(
            "import sys;sys.stdout.buffer.write('月亮🌙\\n'.encode('utf8'));"
            "sys.stderr.buffer.write(b'notice\\n')"
        ),
        unsafe=False,
        cwd=tmp_path,
    )
    assert outcome.stdout == "月亮🌙\n".encode()
    assert outcome.stderr == b"notice\n"
    assert outcome.validation["passed"] is True
    assert outcome.validation["performed"] is True
    assert outcome.validation["utf8_valid"] is True
    assert outcome.sandbox.environment_filtered is True
    assert outcome.sandbox.output_limited is False
    assert capfdbinary.readouterr() == (b"", b"")


def test_capture_rejects_invalid_stdout_but_keeps_diagnostic_bytes(tmp_path: Path) -> None:
    outcome = run_creative_command(
        _command("import sys;sys.stdout.buffer.write(b'\\xb2');sys.stderr.buffer.write(b'\\xfe')"),
        unsafe=False,
        cwd=tmp_path,
    )
    assert outcome.sandbox.exit_code == 0
    assert outcome.stdout == b"\xb2" and outcome.stderr == b"\xfe"
    assert outcome.validation["passed"] is False
    assert outcome.validation["utf8_valid"] is False
    assert outcome.validation["reasons"] == ["stdout_invalid_utf8"]


@pytest.mark.parametrize("text", ["", " \t\n\u3000"])
def test_capture_rejects_empty_unicode_whitespace(tmp_path: Path, text: str) -> None:
    outcome = run_creative_command(
        _command(f"import sys;sys.stdout.buffer.write({text!r}.encode('utf8'))"),
        unsafe=False,
        cwd=tmp_path,
    )
    assert outcome.validation["passed"] is False
    assert outcome.validation["stdout_nonempty"] is False
    assert outcome.validation["reasons"] == ["stdout_empty"]


def test_capture_has_empty_stdin_instead_of_waiting_for_user(tmp_path: Path) -> None:
    outcome = run_creative_command(
        _command("import sys;print('empty' if sys.stdin.buffer.read()==b'' else 'unexpected')"),
        unsafe=False,
        cwd=tmp_path,
        timeout_s=3,
    )
    assert outcome.stdout.strip() == b"empty"
    assert outcome.validation["passed"] is True


def test_capture_rejects_nonzero_exit_even_with_valid_output(tmp_path: Path) -> None:
    outcome = run_creative_command(
        _command("import sys;print('almost');sys.exit(7)"), unsafe=False, cwd=tmp_path
    )
    assert outcome.sandbox.exit_code == 7
    assert outcome.validation["passed"] is False
    assert outcome.validation["reasons"] == ["exit_nonzero"]


def test_capture_combined_streams_exact_quota_is_allowed(tmp_path: Path) -> None:
    outcome = run_creative_command(
        _command("import sys;sys.stdout.buffer.write(b'abcd');sys.stderr.buffer.write(b'efgh')"),
        unsafe=False,
        cwd=tmp_path,
        max_output_bytes=8,
    )
    assert outcome.stdout == b"abcd" and outcome.stderr == b"efgh"
    assert outcome.validation["passed"] is True
    assert outcome.validation["stdout_bytes"] == outcome.validation["stderr_bytes"] == 4
    assert outcome.sandbox.output_limited is False


def test_capture_combined_streams_over_quota_records_actual_counts(tmp_path: Path) -> None:
    outcome = run_creative_command(
        _command("import sys;sys.stdout.buffer.write(b'abcd');sys.stderr.buffer.write(b'efghi')"),
        unsafe=False,
        cwd=tmp_path,
        max_output_bytes=8,
    )
    assert outcome.stdout == b"abcd" and outcome.stderr == b"efgh"
    assert outcome.validation["stdout_bytes"] == 4
    assert outcome.validation["stderr_bytes"] == 5
    assert outcome.validation["stderr_complete"] is False
    assert outcome.validation["passed"] is False
    assert outcome.sandbox.output_limited is True
    assert "output_limit" in outcome.validation["reasons"]


def test_capture_fast_finished_writer_still_fails_quota(tmp_path: Path) -> None:
    outcome = run_creative_command(
        _command("import sys;sys.stdout.buffer.write(b'x'*100000)"),
        unsafe=False,
        cwd=tmp_path,
        max_output_bytes=64,
    )
    assert outcome.sandbox.output_limited is True
    assert len(outcome.stdout) == 64
    assert outcome.validation["stdout_bytes"] == 100000
    assert outcome.validation["stdout_complete"] is False
    assert outcome.validation["utf8_valid"] is None
    assert outcome.validation["passed"] is False


@pytest.mark.parametrize("unsafe", [False, True])
def test_capture_stops_a_live_writer_on_output_quota(tmp_path: Path, unsafe: bool) -> None:
    code = (
        "import sys,time\n"
        "while True:\n"
        " sys.stdout.buffer.write(b'x'*1024);sys.stdout.flush();time.sleep(0.003)\n"
    )
    started = time.monotonic()
    outcome = run_creative_command(
        _command(code),
        unsafe=unsafe,
        cwd=tmp_path,
        timeout_s=5,
        max_output_bytes=4096,
    )
    assert time.monotonic() - started < 4
    assert outcome.sandbox.timed_out is False
    assert outcome.sandbox.output_limited is True
    assert outcome.validation["stdout_bytes"] > 4096
    assert len(outcome.stdout) == 4096


def test_capture_timeout_is_real_and_reported(tmp_path: Path) -> None:
    outcome = run_creative_command(
        _command("import time;time.sleep(60)"),
        unsafe=False,
        cwd=tmp_path,
        timeout_s=0.3,
    )
    assert outcome.sandbox.timed_out is True
    assert outcome.sandbox.exit_code == 124
    assert outcome.validation["passed"] is False
    assert "timeout" in outcome.validation["reasons"]


@pytest.mark.parametrize("unsafe", [False, True])
def test_capture_retains_existing_cwd_and_environment_semantics(
    tmp_path: Path, unsafe: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESCC_TEST_CREATIVE_PRIVATE", "private")
    (tmp_path / "private.txt").write_text("private")
    outcome = run_creative_command(
        _command(
            "import os;from pathlib import Path;"
            "print(Path('private.txt').exists(),os.environ.get('YESCC_TEST_CREATIVE_PRIVATE'))"
        ),
        unsafe=unsafe,
        cwd=tmp_path,
    )
    assert outcome.stdout.strip() == (b"True private" if unsafe else b"False None")
    assert outcome.sandbox.sandboxed is not unsafe
    assert outcome.sandbox.environment_filtered is not unsafe


@pytest.mark.skipif(os.name != "posix", reason="POSIX 进程组后代清理")
def test_capture_output_limit_cleans_child_process_group(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    leaked = tmp_path / "leaked"
    child = f"import time;time.sleep(0.7);open({str(leaked)!r},'w').write('leak')"
    parent = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
        f"open({str(ready)!r},'w').write('ready');"
        "sys.stdout.buffer.write(b'x'*10000);sys.stdout.flush();time.sleep(60)"
    )
    outcome = run_creative_command(
        _command(parent), unsafe=False, cwd=tmp_path, timeout_s=3, max_output_bytes=100
    )
    assert outcome.sandbox.output_limited is True
    assert ready.exists(), "必须先实际创建后代再验证清理"
    time.sleep(1)
    assert not leaked.exists()


def test_existing_execution_keeps_unlimited_inherited_output(
    tmp_path: Path, capfdbinary: pytest.CaptureFixture[bytes]
) -> None:
    outcome = run_ai_command(
        _command(
            "import sys;sys.stdout.buffer.write(b'x'*70000);sys.stderr.buffer.write(b'\\xfe')"
        ),
        unsafe=False,
        cwd=tmp_path,
    )
    captured = capfdbinary.readouterr()
    assert captured.out == b"x" * 70000 and captured.err == b"\xfe"
    assert outcome.output_limited is False


def test_frozen_capture_keeps_external_library_environment_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/original")
    monkeypatch.setattr(runner, "_network_isolation_supported", lambda: False)
    outcome = run_creative_command(
        _command("import os;print(os.environ.get('LD_LIBRARY_PATH','missing'))"),
        unsafe=False,
        cwd=tmp_path,
    )
    assert outcome.stdout.strip() == b"missing"
    assert outcome.validation["passed"] is True


def test_capture_default_timeout_stays_configurable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESCC_RUN_TIMEOUT_S", "3")
    outcome = run_creative_command(_command("print('done')"), unsafe=False, cwd=tmp_path)
    assert outcome.sandbox.timeout_s == 3


@pytest.mark.parametrize("limit", [0, -1])
def test_capture_rejects_invalid_quota_before_execution(tmp_path: Path, limit: int) -> None:
    with pytest.raises(ValueError, match="必须大于0"):
        run_creative_command(
            _command("raise AssertionError('must not execute')"),
            unsafe=False,
            cwd=tmp_path,
            max_output_bytes=limit,
        )
