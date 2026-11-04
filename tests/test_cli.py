"""命令行解析与入口测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yescc.cli import main, parse
from yescc.errors import ExitCode, UsageError


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert "yescc" in capsys.readouterr().out


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0
    assert "用法" in capsys.readouterr().out


def test_no_args_returns_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == int(ExitCode.USAGE)
    assert "缺少输入文件" in capsys.readouterr().err


def test_build_mode_passthrough() -> None:
    parsed = parse(["hello.c", "-o", "hello", "-O2"])
    assert parsed.action == "execute"
    assert parsed.invocation.mode == "build"
    assert parsed.invocation.source == Path("hello.c")
    assert parsed.invocation.passthrough == ["-o", "hello", "-O2"]


def test_run_mode_program_args() -> None:
    parsed = parse(["run", "script.py", "--", "-x", "y"])
    assert parsed.invocation.mode == "run"
    assert parsed.invocation.run_args == ["-x", "y"]


def test_run_mode_rejects_toolchain_args() -> None:
    with pytest.raises(UsageError):
        parse(["run", "a.c", "-o", "out"])


def test_run_mode_rejects_json() -> None:
    with pytest.raises(UsageError):
        parse(["run", "a.py", "--json"])


def test_lang_flag_forms() -> None:
    assert parse(["a.txt", "--lang=python"]).invocation.lang == "python"
    assert parse(["--lang", "c", "a.c"]).invocation.lang == "c"


def test_multiple_sources_rejected() -> None:
    with pytest.raises(UsageError):
        parse(["a.c", "b.c"])


def test_double_dash_in_build_mode() -> None:
    parsed = parse(["a.c", "--", "-O2"])
    assert parsed.invocation.passthrough == ["-O2"]


def test_explain_requires_build_mode() -> None:
    with pytest.raises(UsageError):
        parse(["run", "a.py", "--explain"])
