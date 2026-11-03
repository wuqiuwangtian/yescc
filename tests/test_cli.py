"""fcomp 命令行接口测试（M0 范围）。"""

from __future__ import annotations

import pytest

from fcomp import __version__
from fcomp.cli import main
from fcomp.errors import ExitCode


def test_version_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_args_returns_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == int(ExitCode.USAGE)
    assert "usage" in capsys.readouterr().err.lower()


def test_unimplemented_input_returns_unsupported(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["hello.c"]) == int(ExitCode.UNSUPPORTED)
    assert "M0" in capsys.readouterr().err


def test_exit_codes_stay_above_reserved_range() -> None:
    assert int(ExitCode.USAGE) == 100
    assert int(ExitCode.INTERNAL) == 104
