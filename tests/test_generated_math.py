"""Generated C may opt into libm without changing native or repair commands."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from yescc.adapters.c import CAdapter
from yescc.fix.engine import build_in_temp

MATH_SOURCE = """#include <math.h>
#include <stdio.h>
int main(void) {
    volatile double x = 0.6;
    printf("%.3f\\n", sin(x) + cos(x) + floor(x));
    return 0;
}
"""

pytestmark = pytest.mark.skipif(shutil.which("gcc") is None, reason="requires real GCC")


def test_generated_math_links_and_runs_without_rewriting_source(tmp_path: Path) -> None:
    adapter = CAdapter()
    original = tmp_path / "original.c"
    original.write_text(MATH_SOURCE, encoding="utf-8")
    before = original.read_bytes()
    # Disable builtin folding so this exercises the real link symbols on POSIX.
    native = adapter.build(original, ["-fno-builtin"], tmp_path)
    default = build_in_temp(
        adapter, MATH_SOURCE, "generated.c", ["-fno-builtin"], tmp_path, tmp_path
    )
    assert "-lm" not in native.command and "-lm" not in default.build.command
    if os.name != "nt":
        # MinGW's default runtime may already export these math symbols.
        assert native.returncode != 0 and default.build.returncode != 0

    generated = build_in_temp(
        adapter,
        MATH_SOURCE,
        "generated.c",
        ["-fno-builtin"],
        tmp_path,
        tmp_path,
        link_math=True,
    )
    assert generated.build.returncode == 0, generated.build.stderr
    assert generated.build.command[-1] == "-lm"
    assert generated.artifact is not None
    result = subprocess.run(
        [str(generated.artifact)], capture_output=True, check=False, timeout=5
    )
    assert result.returncode == 0 and result.stdout == f"1.390{os.linesep}".encode()
    assert original.read_bytes() == before


@pytest.mark.parametrize(
    "flag", ["-c", "-S", "-E", "-M", "-MM", "-fsyntax-only", "-nostdlib", "-nodefaultlibs"]
)
def test_generated_nonlink_or_explicit_no_runtime_keeps_flags(
    flag: str, tmp_path: Path
) -> None:
    result = build_in_temp(
        CAdapter(), MATH_SOURCE, "generated.c", [flag], tmp_path, tmp_path, link_math=True
    )
    assert flag in result.build.command
    assert "-lm" not in result.build.command


@pytest.mark.parametrize("flags", [["-lm"], ["-l", "m"]])
def test_explicit_math_library_is_not_duplicated(flags: list[str], tmp_path: Path) -> None:
    result = build_in_temp(
        CAdapter(), MATH_SOURCE, "generated.c", flags, tmp_path, tmp_path, link_math=True
    )
    assert result.build.returncode == 0, result.build.stderr
    assert result.build.command.count("-lm") + result.build.command.count("-l") == 1
