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


def test_summary_does_not_present_unknown_cost_as_free() -> None:
    summary = summarize({
        "model": {"calls": 1, "cost_usd": 0, "cost_known": False},
        "result": {"status": "generated"},
    })
    assert "费用未知" in summary
    assert "$0" not in summary


def test_optional_check_summary_distinguishes_scope_and_execution() -> None:
    from yescc.report import summarize

    checked = summarize({"undefined_behavior_check": {"performed": True, "mode": "trap"}})
    assert "创意候选检查：trap" in checked and "不证明无UB" in checked
    unavailable = summarize({"undefined_behavior_check": {
        "performed": False, "available": False,
    }})
    assert "不可用，未检查作品" in unavailable
    skipped = summarize({"undefined_behavior_check": {
        "performed": False, "available": None, "reason": "not_model_creative",
    }})
    assert "未执行（not_model_creative）" in skipped
    assert "创意候选检查" not in summarize({})
