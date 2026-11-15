"""G5：独立失败输入的退出码、状态、产物与报告必须一致。"""

from __future__ import annotations

from pathlib import Path

import pytest
from bench.failure_audit import audit_case

from tests.conftest import FakeModelServer
from tests.fixtures.failure_cases import CASES, FailureCase

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_failure_does_not_report_success(
    case: FailureCase, tmp_path: Path, fake_model: FakeModelServer
) -> None:
    if case.response is not None:
        fake_model.responses.append(case.response)
    row = audit_case(case, tmp_path / case.name, fake_model.base_url)
    assert row["passed"], row
    if case.response is None:
        assert not fake_model.requests


def test_failure_inputs_are_independent() -> None:
    signatures = {(c.suffix, c.source, c.flags, c.condition) for c in CASES}
    assert len(CASES) == len(signatures) >= 20
