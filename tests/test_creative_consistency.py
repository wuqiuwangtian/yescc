"""Offline checks expose mismatched counts and state without inventing a semantic score."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from bench.creative_consistency import evaluate_dataset, evaluate_output, main


def _case(*checks: dict[str, Any], case_id: str = "C01") -> dict[str, Any]:
    return {
        "id": case_id,
        "input": "按行倒数，报告最后状态",
        "category": "state",
        "checks": list(checks),
    }


def test_state_sequence_and_last_line_detect_plausible_but_inconsistent_story() -> None:
    case = _case(
        {"type": "line_sequence", "pattern": r"剩余：(\d+)", "expected": ["2", "1", "0"]},
        {"type": "last_nonempty_line", "text": "任务完成，剩余0颗"},
        {"type": "not_contains", "text": "剩余3颗"},
    )
    correct = "拿走一颗，剩余：2\n拿走一颗，剩余：1\n拿走一颗，剩余：0\n任务完成，剩余0颗\n"
    assert evaluate_output(case, correct.encode())["passed"] is True
    stale = correct.replace("任务完成，剩余0颗", "任务完成，剩余3颗")
    result = evaluate_output(case, stale.encode())
    assert result["passed"] is False
    assert [check["passed"] for check in result["checks"]] == [True, False, False]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_count_keeps_internal_blank_lines_but_not_final_newline(newline: str) -> None:
    case = _case(
        {"type": "line_count", "equals": 4},
        {"type": "line_count", "min": 2, "max": 2, "include_blank": False},
        {"type": "last_nonempty_line", "text": "收工"},
    )
    output = newline.join(["开工", "", " \t", "收工", ""])
    result = evaluate_output(case, output.encode())
    assert result["passed"] is True
    assert all(check["passed"] for check in result["checks"])


def test_unicode_symbols_use_exact_sequences_and_do_not_measure_terminal_width() -> None:
    case = _case(
        {"type": "symbol_count", "symbol": "🌧️", "equals": 2},
        {"type": "contains", "text": "雨停"},
        {"type": "line_count", "equals": 1},
    )
    assert evaluate_output(case, "🌧️🌧️ 雨停\n".encode())["passed"] is True
    result = evaluate_output(case, "🌧️🌧 雨停\n".encode())
    assert result["passed"] is False
    assert result["checks"][0]["detail"] == "nonoverlapping symbol count: 1"
    # Combining text is never silently normalized to a composed code point.
    assert (
        evaluate_output(_case({"type": "contains", "text": "é"}), b"e\xcc\x81")["passed"] is False
    )


def test_sequence_search_collects_only_first_match_per_line() -> None:
    check = {"type": "line_sequence", "pattern": r"step=(\d+)", "expected": ["2", "1"]}
    output = b"log: step=2 step=999\nignored line\nstep=1\n"
    assert evaluate_output(_case(check), output)["passed"] is True
    assert evaluate_output(_case({**check, "fullmatch": True}), output)["passed"] is False
    assert (
        evaluate_output(_case({**check, "fullmatch": True}), b"step=2\nstep=1\n")["passed"] is True
    )


def test_empty_output_is_zero_lines_but_does_not_satisfy_a_final_state() -> None:
    assert evaluate_output(_case({"type": "line_count", "equals": 0}), b"")["passed"] is True
    result = evaluate_output(_case({"type": "last_nonempty_line", "text": "完成"}), b"")
    assert result["utf8_valid"] is True
    assert result["passed"] is False


@pytest.mark.parametrize("stdout", [b"", "正常文本\n".encode()])
def test_without_checks_is_not_assessed_even_with_valid_utf8(stdout: bytes) -> None:
    result = evaluate_output(_case(), stdout)
    assert result == {"utf8_valid": True, "checks": [], "passed": None, "status": "not_assessed"}


@pytest.mark.parametrize("checks", [[], [{"type": "not_contains", "text": "bad"}]])
def test_invalid_utf8_fails_even_without_semantic_checks(checks: list[dict[str, Any]]) -> None:
    result = evaluate_output(_case(*checks), b"good\n\xff")
    assert result["utf8_valid"] is False
    assert result["passed"] is False
    assert result["status"] == "failed"
    assert all(check["passed"] is False for check in result["checks"])


@pytest.mark.parametrize(
    "check",
    [
        {"type": "guessed_semantics"},
        {"type": "line_count"},
        {"type": "line_count", "equals": True},
        {"type": "line_count", "equals": -1},
        {"type": "line_count", "equals": 1.0},
        {"type": "line_count", "equals": 1, "max": 2},
        {"type": "line_count", "min": 3, "max": 2},
        {"type": "line_count", "equals": 1, "include_blank": 1},
        {"type": "line_count", "equals": 1, "include_blanks": True},
        {"type": "contains", "text": ""},
        {"type": "not_contains", "text": 1},
        {"type": "symbol_count", "symbol": "", "equals": 0},
        {"type": "symbol_count", "symbol": "*", "equals": False},
        {"type": "line_sequence", "pattern": "[", "expected": []},
        {"type": "line_sequence", "pattern": r"\d+", "expected": []},
        {"type": "line_sequence", "pattern": r"(\d+)(x)", "expected": []},
        {"type": "line_sequence", "pattern": r"(\d+)", "expected": [1]},
        {"type": "line_sequence", "pattern": r"(\d+)", "expected": [], "fullmatch": 0},
        {"type": "last_nonempty_line", "text": "done", "name": ""},
    ],
)
def test_invalid_checks_raise_before_decoding_stdout(check: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        evaluate_output(_case(check), b"\xff")


@pytest.mark.parametrize("changes", [{"id": "../escape"}, {"checks": None}, {"input": 2}])
def test_invalid_case_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        evaluate_output({**_case(), **changes}, b"")


@pytest.mark.parametrize("wrapped", [False, True])
def test_dataset_hash_summary_and_missing_outputs(tmp_path: Path, wrapped: bool) -> None:
    cases = [
        _case({"type": "contains", "text": "完成"}),
        _case(case_id="C02"),
        _case({"type": "line_count", "equals": 1}, case_id="C03"),
    ]
    cases_path = tmp_path / "cases.json"
    raw = json.dumps({"cases": cases} if wrapped else cases, ensure_ascii=False).encode()
    cases_path.write_bytes(raw)
    (tmp_path / "C01.stdout").write_bytes("完成\r\n".encode())
    (tmp_path / "C02.stdout").write_bytes(b"")
    report = evaluate_dataset(cases_path, tmp_path)
    assert report["dataset_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["summary"] == {
        "total": 3,
        "passed": 1,
        "failed": 1,
        "not_assessed": 1,
        "without_semantic_checks": 1,
        "missing_output": 1,
        "invalid_utf8": 0,
    }
    assert report["results"][1]["passed"] is None
    assert report["results"][2]["status"] == "missing_output"
    output = tmp_path / "report.json"
    args = ["--cases", str(cases_path), "--outputs", str(tmp_path), "--output", str(output)]
    assert main(args) == 1
    assert json.loads(output.read_text(encoding="utf-8"))["summary"] == report["summary"]


def test_cli_module_success_and_configuration_errors(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps([_case()]), encoding="utf-8")
    (tmp_path / "C01.stdout").write_bytes(b"mechanically valid")
    output = tmp_path / "report.json"
    args = ["--cases", str(cases_path), "--outputs", str(tmp_path), "--output", str(output)]
    result = subprocess.run(
        [sys.executable, "-m", "bench.creative_consistency", *args],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["summary"]["passed"] == 0
    cases_path.write_text(json.dumps([_case(), _case()]), encoding="utf-8")
    assert main(args) == 2
    cases_path.write_bytes(b"not JSON")
    assert main(args) == 2
    cases_path.unlink()
    assert main(args) == 2


@pytest.mark.parametrize("destination", ["cases", "stdout", "missing_stdout"])
def test_cli_never_overwrites_frozen_inputs_or_creates_expected_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    destination: str,
) -> None:
    cases_path = tmp_path / "cases.json"
    cases_bytes = json.dumps([_case(), _case(case_id="C02")]).encode()
    cases_path.write_bytes(cases_bytes)
    captured = tmp_path / "C01.stdout"
    captured_bytes = "原始输出\r\n".encode()
    captured.write_bytes(captured_bytes)
    missing = tmp_path / "C02.stdout"
    output = {"cases": cases_path, "stdout": captured, "missing_stdout": missing}[destination]

    assert (
        main(
            [
                "--cases",
                str(cases_path),
                "--outputs",
                str(tmp_path),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert "conflicts with protected input" in capsys.readouterr().err
    assert cases_path.read_bytes() == cases_bytes
    assert captured.read_bytes() == captured_bytes
    assert not missing.exists()


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
@pytest.mark.parametrize("target_kind", ["cases", "stdout"])
def test_cli_rejects_report_aliases_of_protected_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    alias_kind: str,
    target_kind: str,
) -> None:
    cases_path = tmp_path / "cases.json"
    cases_bytes = json.dumps([_case()]).encode()
    cases_path.write_bytes(cases_bytes)
    captured = tmp_path / "C01.stdout"
    captured_bytes = b"unchanged capture\r\n"
    captured.write_bytes(captured_bytes)
    target = cases_path if target_kind == "cases" else captured
    output = tmp_path / "report.json"
    try:
        if alias_kind == "symlink":
            output.symlink_to(target)
        else:
            output.hardlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"{alias_kind} unavailable on this platform: {exc}")

    assert (
        main(
            [
                "--cases",
                str(cases_path),
                "--outputs",
                str(tmp_path),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert "conflicts with protected input" in capsys.readouterr().err
    assert cases_path.read_bytes() == cases_bytes
    assert captured.read_bytes() == captured_bytes
    assert output.samefile(target)


def test_cli_rejects_dangling_report_symlink_to_missing_capture(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_bytes = json.dumps([_case()]).encode()
    cases_path.write_bytes(cases_bytes)
    missing = tmp_path / "C01.stdout"
    output = tmp_path / "report.json"
    try:
        output.symlink_to(missing)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable on this platform: {exc}")

    assert (
        main(
            [
                "--cases",
                str(cases_path),
                "--outputs",
                str(tmp_path),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert cases_path.read_bytes() == cases_bytes
    assert not missing.exists()
    assert output.is_symlink()
