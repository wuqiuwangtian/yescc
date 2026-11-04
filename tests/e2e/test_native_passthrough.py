"""端到端：合法程序与原生工具链行为一致，且零干扰。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.legal_cases import CASES, LegalCase
from yescc.util import sha256_file

pytestmark = pytest.mark.e2e

EXE = ".exe" if os.name == "nt" else ""

# CI 的管道不是控制台：Windows 上 Python 默认会用 cp1252 输出，
# 中文样例会因此失败。统一按 UTF-8 环境对照，避免把平台默认编码
# 差异误判成 yescc 的行为差异。
_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}
PYTHON = sys.executable
NODE = shutil.which("node") or shutil.which("node.exe")
GCC = shutil.which("gcc") or shutil.which("gcc.exe") or shutil.which("cc")

_EXTENSION = {"python": ".py", "c": ".c", "javascript": ".js"}


def _available(language: str) -> bool:
    if language == "python":
        return True
    if language == "javascript":
        return NODE is not None
    return GCC is not None


def _materialize(case: LegalCase, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in case.files.items():
        (directory / name).write_text(content, encoding="utf-8")
    source = directory / f"main{_EXTENSION[case.lang]}"
    source.write_text(case.source, encoding="utf-8")
    return source


def _run(
    command: list[str], cwd: Path, stdin: str = "", timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=_ENV,
    )


def _native(case: LegalCase, source: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    if case.lang == "python":
        return _run([PYTHON, str(source), *case.args], cwd, case.stdin)
    if case.lang == "javascript":
        assert NODE is not None
        return _run([NODE, str(source), *case.args], cwd, case.stdin)
    assert GCC is not None
    binary = cwd / f"native{EXE}"
    built = _run([GCC, str(source), "-o", str(binary)], cwd)
    assert built.returncode == 0, built.stderr
    return _run([str(binary), *case.args], cwd, case.stdin)


def _yescc(case: LegalCase, source: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return _run([PYTHON, "-m", "yescc", "run", str(source), "--", *case.args], cwd, case.stdin)


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_legal_case_matches_native(case: LegalCase, tmp_path: Path) -> None:
    if not _available(case.lang):
        pytest.skip(f"{case.lang} 工具链不可用")

    native_dir = tmp_path / "native dir"
    yescc_dir = tmp_path / "yescc dir"
    native_source = _materialize(case, native_dir)
    yescc_source = _materialize(case, yescc_dir)
    before = sha256_file(yescc_source)

    expected = _native(case, native_source, native_dir)
    actual = _yescc(case, yescc_source, yescc_dir)

    assert actual.stdout == expected.stdout
    assert actual.stderr == expected.stderr
    assert actual.returncode == expected.returncode

    assert sha256_file(yescc_source) == before, "合法输入被修改了源码"
    report_file = yescc_dir / ".yescc" / "main" / "report.json"
    report = json.loads(report_file.read_text(encoding="utf-8"))
    assert report["privacy"]["cloud_used"] is False
    assert all(attempt["kind"] == "native" for attempt in report["attempts"])
    assert report["result"]["status"] == "native_ok"


_C_CASES = [case for case in CASES if case.lang == "c"]


@pytest.mark.parametrize("case", _C_CASES, ids=[case.name for case in _C_CASES])
def test_c_build_mode_matches_gcc(case: LegalCase, tmp_path: Path) -> None:
    if GCC is None:
        pytest.skip("gcc 不可用")

    native_dir = tmp_path / "native dir"
    yescc_dir = tmp_path / "yescc dir"
    native_source = _materialize(case, native_dir)
    yescc_source = _materialize(case, yescc_dir)
    native_binary = native_dir / f"native{EXE}"
    output_name = f"out{EXE}"

    native = _run([GCC, str(native_source), "-o", str(native_binary)], native_dir)
    built = _run([PYTHON, "-m", "yescc", str(yescc_source), "-o", output_name], yescc_dir)
    assert native.returncode == built.returncode == 0, built.stderr
    assert (yescc_dir / output_name).exists()

    expected = _run([str(native_binary)], native_dir)
    actual = _run([str(yescc_dir / output_name)], yescc_dir)
    assert actual.stdout == expected.stdout
    assert actual.returncode == expected.returncode
