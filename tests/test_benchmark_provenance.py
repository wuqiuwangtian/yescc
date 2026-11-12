"""The executable fallback must not inflate model-first-candidate measurements."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from bench.dataset import Case, Check
from bench.harness import run_case, summarize

from tests.conftest import FakeModelServer

pytestmark = pytest.mark.skipif(shutil.which("gcc") is None, reason="requires real GCC")


def _creative_case(name: str = "provenance") -> Case:
    return Case(name, "fun", "c", "一颗会唱歌的石头", (Check(),))


@pytest.mark.parametrize("response", [{"source": ""}, "this is not JSON"])
def test_model_response_failure_then_print_input_is_not_model_success(
    response: object, tmp_path: Path, fake_model: FakeModelServer
) -> None:
    fake_model.responses.extend([response, response])
    result = run_case(_creative_case(), tmp_path, models=True, budget=0.005)
    assert result["passed"] is True
    assert result["fallback_kind"] == "print_input"
    assert result["calls"] == 2 and result["model_builds"] == 0
    assert result["model_success"] is False
    assert result["first_candidate_correct"] is False
    summary = summarize([result])["fun"]
    assert summary["correct"] == 1
    assert summary["model_success"] == summary["first_candidate_correct"] == 0


def test_compiled_and_executed_model_creative_counts_once(
    tmp_path: Path, fake_model: FakeModelServer
) -> None:
    fake_model.responses.append(
        {"source": '#include <stdio.h>\nint main(void) { puts("stone sings"); return 0; }\n'}
    )
    result = run_case(_creative_case(), tmp_path, models=True, budget=0.005)
    assert result["passed"] is True and result["source_unchanged"] is True
    assert result["fallback_kind"] == "model_creative"
    assert result["model_builds"] == result["calls"] == 1
    assert result["model_success"] is True and result["first_candidate_correct"] is True


def test_known_easter_egg_remains_passed_without_model_credit(
    tmp_path: Path, fake_model: FakeModelServer
) -> None:
    case = Case("known_egg", "fun", "c", "天王盖地虎", (Check(),))
    result = run_case(case, tmp_path, models=True, budget=0.005)
    assert result["passed"] is True and result["fallback_kind"] == "easter_egg"
    assert result["calls"] == result["model_builds"] == 0
    assert result["model_success"] is result["first_candidate_correct"] is False
    assert not fake_model.requests
