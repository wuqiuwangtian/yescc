"""端到端：CLI 契约、报告与失败状态。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

PYTHON = sys.executable


def _run(
    args: list[str], cwd: Path, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "yescc", *args],
        cwd=str(cwd),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def test_help_and_version(tmp_path: Path) -> None:
    help_result = _run(["--help"], tmp_path)
    assert help_result.returncode == 0
    assert "用法" in help_result.stdout
    version_result = _run(["--version"], tmp_path)
    assert version_result.returncode == 0
    assert "yescc" in version_result.stdout


def test_json_report(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    result = _run(["hello.py", "--json"], tmp_path)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1"
    assert payload["result"]["status"] == "native_ok"
    assert payload["privacy"]["cloud_used"] is False
    assert payload["attempts"][0]["kind"] == "native"


def test_explain_reads_report_without_building_again(tmp_path: Path) -> None:
    source = tmp_path / "hello.py"
    source.write_text("print('hi')\n", encoding="utf-8")
    assert _run(["hello.py"], tmp_path).returncode == 0
    explained = _run(["hello.py", "--explain"], tmp_path)
    assert explained.returncode == 0
    assert "状态：native_ok" in explained.stdout


def test_explain_without_report(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    result = _run(["hello.py", "--explain"], tmp_path)
    assert result.returncode == 100
    assert "没有找到报告" in result.stderr


def test_broken_c_falls_back_by_default(tmp_path: Path) -> None:
    (tmp_path / "broken.c").write_text("int main(void) { return 0 }\n", encoding="utf-8")
    result = _run(["broken.c"], tmp_path)
    assert result.returncode == 0
    assert "创意兜底" in result.stderr
    report = json.loads((tmp_path / ".yescc" / "broken" / "report.json").read_text("utf-8"))
    assert report["result"]["status"] == "creative_fallback"
    assert report["result"]["fallback"]["kind"] == "print_input"


def test_broken_c_strict_shows_toolchain_output(tmp_path: Path) -> None:
    (tmp_path / "broken.c").write_text("int main(void) { return 0 }\n", encoding="utf-8")
    result = _run(["broken.c", "--strict"], tmp_path)
    assert result.returncode == 102
    assert "error" in result.stderr.lower()


def test_no_fallback_records_failure(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    result = _run(["broken.py", "--no-fallback"], tmp_path)
    assert result.returncode == 102
    report = json.loads((tmp_path / ".yescc" / "broken" / "report.json").read_text("utf-8"))
    assert report["result"]["status"] == "native_failed"
    assert report["attempts"][0]["exit_code"] != 0


def test_unknown_text_falls_back(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    result = _run(["run", "notes.txt"], tmp_path)
    assert result.returncode == 0
    assert result.stdout == "hello\n"
    report = json.loads((tmp_path / ".yescc" / "notes" / "report.json").read_text("utf-8"))
    assert report["result"]["status"] == "creative_fallback"


def test_unsupported_extension_with_strict(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    result = _run(["notes.txt", "--strict"], tmp_path)
    assert result.returncode == 101
    assert "无法从扩展名识别" in result.stderr


def test_language_override(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("print('hi')\n", encoding="utf-8")
    result = _run(["notes.txt", "--lang", "python"], tmp_path)
    assert result.returncode == 0


def test_missing_file(tmp_path: Path) -> None:
    result = _run(["nope.c"], tmp_path)
    assert result.returncode == 100
    assert "不存在" in result.stderr


def test_non_utf8_source(tmp_path: Path) -> None:
    (tmp_path / "latin.py").write_bytes(b"print('\xff\xfe')\n")
    result = _run(["latin.py"], tmp_path)
    assert result.returncode == 101
    assert "UTF-8" in result.stderr


def test_run_mode_json_rejected(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    result = _run(["run", "hello.py", "--json"], tmp_path)
    assert result.returncode == 100


def test_broken_python_falls_back(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    result = _run(["run", "broken.py"], tmp_path)
    assert result.returncode == 0
    assert "def broken(:" in result.stdout
    report = json.loads((tmp_path / ".yescc" / "broken" / "report.json").read_text("utf-8"))
    assert report["result"]["status"] == "creative_fallback"


def test_run_mode_passes_program_exit_code(tmp_path: Path) -> None:
    (tmp_path / "exit.py").write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    result = _run(["run", "exit.py"], tmp_path)
    assert result.returncode == 3


def test_run_mode_stdin(tmp_path: Path) -> None:
    (tmp_path / "echo.py").write_text("print(input().upper())\n", encoding="utf-8")
    result = _run(["run", "echo.py"], tmp_path, stdin="abc\n")
    assert result.returncode == 0
    assert result.stdout == "ABC\n"
