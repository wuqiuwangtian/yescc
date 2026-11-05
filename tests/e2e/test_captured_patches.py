"""离线重放真实失败响应，真实工具链与固定行为检查均不能跳过。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bench.dataset import cases, fingerprint
from bench.harness import run_case

from tests.conftest import FakeModelServer

pytestmark = pytest.mark.e2e
CAPTURE = json.loads(
    (Path(__file__).parents[2] / "docs/评测/data/m5-diff-capture.json").read_text("utf-8")
)


@pytest.mark.parametrize("record", CAPTURE["records"], ids=lambda row: row["id"])
def test_replay_captured_patch(record: dict, tmp_path: Path, fake_model: FakeModelServer) -> None:
    assert CAPTURE["dataset_sha256"] == fingerprint()
    case = next(case for case in cases() if case.id == record["id"])
    assert case.source == record["source"]
    fake_model.responses.append(record["raw_response"])
    result = run_case(case, tmp_path, models=True, budget=0.005)
    assert result["passed"], result
    assert len(fake_model.requests) == 1
    report = json.loads(
        (tmp_path / case.id / "actual" / ".yescc" / case.id / "report.json").read_text("utf-8")
    )
    repair = report["result"]["repair"]
    assert repair["raw_diff"] == json.loads(record["raw_response"])["diff"]
    assert bool(repair["patch_normalizations"]) is (case.id != "complex_product")
