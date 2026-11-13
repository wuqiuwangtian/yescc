"""Validate the offline UBSan study fixtures without running unsafe native C.

These are AI-designed controls, not a human-reviewed semantic gold standard.
Only explicitly defined cases are executed here; sanitizer detection rates belong
to the study results and must not become a self-fulfilling test oracle.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

DATASET = Path(__file__).resolve().parents[1] / "bench/ubsan_cases.json"
CASES: list[dict[str, Any]] = json.loads(DATASET.read_text(encoding="utf-8"))
GCC = shutil.which("gcc")


def test_ubsan_case_inventory_is_explicit() -> None:
    raw = DATASET.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert len(CASES) == 16
    assert len({case["id"] for case in CASES}) == 16
    assert Counter(case["expectation"] for case in CASES) == {
        "defined": 6,
        "undefined": 6,
        "boundary": 4,
    }
    for case in CASES:
        for field in ("id", "source", "category", "note", "standard_basis"):
            assert isinstance(case[field], str) and case[field].strip(), (case["id"], field)
        if case["expectation"] == "defined":
            assert isinstance(case["expected_stdout"], str)
        else:
            # Undefined C has no legitimate expected output; boundaries must not
            # silently turn sanitizer coverage into a semantic pass/fail oracle.
            assert "expected_stdout" not in case


def test_ubsan_cases_keep_contrasting_coverage() -> None:
    by_expectation = {
        expectation: {case["category"] for case in CASES if case["expectation"] == expectation}
        for expectation in ("defined", "undefined", "boundary")
    }
    assert by_expectation["defined"] >= {
        "unsigned_wrap",
        "utf8_literary_output",
        "libm_valid_domain",
        "legal_one_past_pointer",
        "signed_in_range_boundary",
        "legal_unsigned_shift",
    }
    assert by_expectation["undefined"] >= {
        "array_out_of_bounds",
        "signed_integer_overflow",
        "integer_division_by_zero",
        "shift_count_out_of_range",
        "negative_signed_left_shift",
        "null_pointer_dereference",
    }
    boundaries = [case for case in CASES if case["expectation"] == "boundary"]
    assert {case["boundary_semantics"] for case in boundaries} == {
        "defined_but_incorrect_content",
        "defined_executed_path_with_latent_ub",
        "undefined_if_allocation_succeeds",
    }
    assert {case["category"] for case in boundaries} >= {
        "semantic_content_error",
        "unexecuted_undefined_branch",
        "heap_out_of_bounds_coverage",
        "use_after_free_coverage",
    }


@pytest.mark.skipif(GCC is None, reason="requires real GCC")
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_ubsan_case_is_self_contained_and_defined_controls_run(
    case: dict[str, Any], tmp_path: Path
) -> None:
    source = tmp_path / "case.c"
    executable = tmp_path / "case.exe"
    source.write_text(case["source"], encoding="utf-8", newline="\n")
    assert GCC is not None
    compiled = subprocess.run(
        [
            GCC,
            "-std=c11",
            "-O1",
            "-fno-builtin",
            "-finput-charset=UTF-8",
            "-fexec-charset=UTF-8",
            str(source),
            "-o",
            str(executable),
            "-lm",
        ],
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert compiled.returncode == 0, compiled.stderr.decode("utf-8", errors="replace")
    if case["expectation"] != "defined":
        return
    result = subprocess.run(
        [str(executable)], capture_output=True, check=False, timeout=2
    )
    assert result.returncode == 0
    # Normalize platform line endings after decoding UTF-8 strictly.
    actual = result.stdout.decode("utf-8").replace("\r\n", "\n")
    assert actual == case["expected_stdout"]
    assert result.stderr == b""
