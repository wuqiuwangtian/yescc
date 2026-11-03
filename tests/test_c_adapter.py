"""C 适配器的输出参数与产物推断测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fcomp.adapters.c import _default_artifact, _output_argument


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], None),
        (["-O2"], None),
        (["-o", "out"], "out"),
        (["-oout"], "out"),
        (["-O3", "-o", "app", "-Wall"], "app"),
    ],
)
def test_output_argument(args: list[str], expected: str | None) -> None:
    assert _output_argument(args) == expected


def test_default_artifact_is_output_argument(tmp_path: Path) -> None:
    artifact = _default_artifact(Path("main.c"), ["-o", "app"], tmp_path)
    assert artifact == tmp_path / "app"


def test_default_artifact_is_object_for_c(tmp_path: Path) -> None:
    assert _default_artifact(Path("main.c"), ["-c"], tmp_path) == tmp_path / "main.o"


def test_default_artifact_is_assembly_for_s(tmp_path: Path) -> None:
    assert _default_artifact(Path("main.c"), ["-S"], tmp_path) == tmp_path / "main.s"


def test_default_artifact_is_none_for_preprocess(tmp_path: Path) -> None:
    assert _default_artifact(Path("main.c"), ["-E"], tmp_path) is None


def test_default_artifact_is_platform_binary(tmp_path: Path) -> None:
    expected = tmp_path / ("a.exe" if os.name == "nt" else "a.out")
    assert _default_artifact(Path("main.c"), [], tmp_path) == expected
