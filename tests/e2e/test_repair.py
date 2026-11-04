"""端到端：规则修复与确定性兜底。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.broken_c_cases import CASES, BrokenCase
from yescc.util import sha256_file

pytestmark = pytest.mark.e2e

PYTHON = sys.executable
GCC = shutil.which("gcc") or shutil.which("gcc.exe") or shutil.which("cc")


def _run(command: list[str], cwd: Path, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def _materialize(case: BrokenCase, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "main.c"
    source.write_text(case.source, encoding="utf-8")
    return source


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_rule_repair_case(case: BrokenCase, tmp_path: Path) -> None:
    if GCC is None:
        pytest.skip("gcc 不可用")

    directory = tmp_path / "case dir"
    source = _materialize(case, directory)
    before = sha256_file(source)

    result = _run(
        [PYTHON, "-m", "yescc", "run", "main.c", "--", *case.args],
        directory,
        case.stdin,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == case.expected_stdout
    assert sha256_file(source) == before, "修复不应覆盖源文件"
    assert (directory / ".yescc" / "main" / "main.c").exists()

    report = json.loads((directory / ".yescc" / "main" / "report.json").read_text("utf-8"))
    assert report["result"]["status"] == "repaired"
    assert report["result"]["repair"]["rules"]
    assert report["result"]["fallback"] is None
    assert report["privacy"]["cloud_used"] is False


def test_strict_disables_repair_and_fallback(tmp_path: Path) -> None:
    if GCC is None:
        pytest.skip("gcc 不可用")
    broken = tmp_path / "broken.c"
    broken.write_text("#include <stdio.h>\nint main(void) { printf(\"a\\n\"; return 0; }\n")

    ok = _run([PYTHON, "-m", "yescc", "broken.c", "--strict"], tmp_path)
    assert ok.returncode == 102
    assert "error" in ok.stderr.lower()


def test_unfixable_source_falls_back(tmp_path: Path) -> None:
    if GCC is None:
        pytest.skip("gcc 不可用")
    source = tmp_path / "hopeless.c"
    source.write_text("int main( {\n", encoding="utf-8")

    fallback = _run([PYTHON, "-m", "yescc", "run", "hopeless.c"], tmp_path)
    assert fallback.returncode == 0
    assert "创意兜底" in fallback.stderr
    report = json.loads((tmp_path / ".yescc" / "hopeless" / "report.json").read_text("utf-8"))
    assert report["result"]["status"] == "creative_fallback"
    assert report["result"]["fallback"]["kind"] == "print_input"

    strict = _run([PYTHON, "-m", "yescc", "hopeless.c", "--no-fallback"], tmp_path)
    assert strict.returncode == 102


def test_play_forces_fallback_on_valid_source(tmp_path: Path) -> None:
    if GCC is None:
        pytest.skip("gcc 不可用")
    source = tmp_path / "hello.c"
    source.write_text(
        '#include <stdio.h>\nint main(void) { printf("hi\\n"); return 0; }\n',
        encoding="utf-8",
    )
    result = _run([PYTHON, "-m", "yescc", "run", "hello.c", "--play"], tmp_path)
    assert result.returncode == 0
    assert "int main(void)" in result.stdout
    report = json.loads((tmp_path / ".yescc" / "hello" / "report.json").read_text("utf-8"))
    assert report["result"]["status"] == "creative_fallback"
    assert report["result"]["fallback"]["reason"] == "用户请求 --play"


def test_missing_dependency_does_not_fall_back(tmp_path: Path) -> None:
    if GCC is None:
        pytest.skip("gcc 不可用")
    source = tmp_path / "badinclude.c"
    source.write_text(
        "#include <definitely_missing_header.h>\nint main(void) { return 0; }\n",
        encoding="utf-8",
    )
    result = _run([PYTHON, "-m", "yescc", "badinclude.c"], tmp_path)
    assert result.returncode == 102
    assert "环境或依赖" in result.stderr
    report = json.loads(
        (tmp_path / ".yescc" / "badinclude" / "report.json").read_text("utf-8")
    )
    assert report["result"]["status"] == "failed"
