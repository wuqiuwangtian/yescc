"""冻结题组的独立合格/违规示例，防止改检查器时放宽验收。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from bench.creative_consistency import evaluate_output

DATASET = Path(__file__).resolve().parents[1] / "bench/creative_consistency_cases.json"
CASES = json.loads(DATASET.read_text("utf-8"))


def test_dataset_is_independently_frozen() -> None:
    assert hashlib.sha256(DATASET.read_bytes()).hexdigest() == (
        "a16f8b397ad39f367ddc6ad312ae73a7f6d9c954c680626af8a9b9ab8cfbe2a7"
    )
    assert len(CASES) == 12
    assert sum(bool(case["checks"]) for case in CASES) == 8


@pytest.mark.parametrize("case", [case for case in CASES if case["checks"]],
                         ids=lambda case: case["id"])
@pytest.mark.parametrize("example,expected", [("example_pass", True), ("example_fail", False)])
def test_frozen_examples_retain_their_expected_judgment(
    case: dict, example: str, expected: bool,
) -> None:
    result = evaluate_output(case, case[example].encode("utf-8"))
    assert result["passed"] is expected
