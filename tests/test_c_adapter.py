"""C 适配器的输出参数与产物推断测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from yescc.adapters.c import _default_artifact, _output_argument


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


@pytest.mark.parametrize("old_extensionless_exists", [False, True])
@pytest.mark.parametrize("joined", [False, True])
def test_windows_linked_output_finds_actual_exe_over_old_extensionless_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old_extensionless_exists: bool,
    joined: bool,
) -> None:
    from types import SimpleNamespace

    from yescc.adapters import c

    target = tmp_path / "program"
    executable = tmp_path / "program.exe"
    if old_extensionless_exists:
        target.write_bytes(b"stale extensionless file")
    executable.write_bytes(b"actual linked executable")
    monkeypatch.setattr(c, "os", SimpleNamespace(name="nt"))
    arguments = ["-oprogram"] if joined else ["-o", "program"]
    assert _default_artifact(Path("main.c"), arguments, tmp_path) == executable
    if old_extensionless_exists:
        assert target.read_bytes() == b"stale extensionless file"


@pytest.mark.parametrize("mode", ["-c", "-S", "-E", "-M", "-MM", "-r", "-shared"])
def test_windows_non_executable_mode_keeps_requested_output_even_with_old_exe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    from types import SimpleNamespace

    from yescc.adapters import c

    target = tmp_path / "program"
    target.write_bytes(b"current compiler output")
    (tmp_path / "program.exe").write_bytes(b"stale executable")
    monkeypatch.setattr(c, "os", SimpleNamespace(name="nt"))
    assert _default_artifact(Path("main.c"), [mode, "-o", "program"], tmp_path) == target


def test_windows_does_not_invent_missing_exe_or_suffix_existing_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from yescc.adapters import c

    monkeypatch.setattr(c, "os", SimpleNamespace(name="nt"))
    assert _default_artifact(Path("main.c"), ["-o", "program"], tmp_path) == tmp_path / "program"
    target = tmp_path / "program.bin"
    target.write_bytes(b"requested output")
    (tmp_path / "program.bin.exe").write_bytes(b"unrelated file")
    assert _default_artifact(Path("main.c"), ["-o", str(target)], tmp_path) == target


def test_posix_exact_linked_output_ignores_an_existing_exe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from yescc.adapters import c

    target = tmp_path / "program"
    target.write_bytes(b"current executable")
    (tmp_path / "program.exe").write_bytes(b"unrelated file")
    monkeypatch.setattr(c, "os", SimpleNamespace(name="posix"))
    assert _default_artifact(Path("main.c"), ["-o", str(target)], tmp_path) == target


def test_real_gcc_explicit_extensionless_output_keeps_command_and_is_not_run(
    tmp_path: Path,
) -> None:
    import shutil

    from yescc.adapters.c import CAdapter

    if shutil.which("gcc") is None:
        pytest.skip("需要当前平台 GCC")
    source = tmp_path / "main.c"
    source.write_text("int main(void) { return 19; }\n", encoding="utf-8")
    arguments = ["-O2", "-std=c11", "-o", "program"]
    result = CAdapter().build(source, arguments, tmp_path)
    assert result.returncode == 0
    assert result.command[1:] == [str(source), *arguments]
    expected = "program.exe" if os.name == "nt" else "program"
    assert result.artifact == tmp_path / expected
    assert result.artifact.is_file()
