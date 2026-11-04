"""报告读写与摘要测试。"""

from __future__ import annotations

from pathlib import Path

from yescc.report import load_report, report_path, summarize, write_report


def test_report_path_uses_cwd_and_stem(tmp_path: Path) -> None:
    assert report_path(tmp_path, Path("src/main.c")) == tmp_path / ".yescc" / "main" / "report.json"


def test_write_and_load_roundtrip(tmp_path: Path) -> None:
    path = report_path(tmp_path, Path("main.py"))
    payload = {
        "schema_version": "1",
        "result": {"status": "native_ok", "artifact": None, "program_exit": None},
        "invocation": {"lang": "python"},
        "toolchain": {"command": "python", "version": "3.11"},
        "timings": {"total_ms": 5, "toolchain_ms": 4},
    }
    write_report(path, payload)
    loaded = load_report(path)
    assert loaded is not None
    assert loaded["schema_version"] == "1"
    assert "状态：native_ok" in summarize(loaded)


def test_load_missing_report(tmp_path: Path) -> None:
    assert load_report(tmp_path / "missing.json") is None


def test_load_corrupt_report(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_report(path) is None
