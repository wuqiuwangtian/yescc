"""Offline replay keeps invalid/abstained judgments out of successful decisions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bench.creative_review_eval import comparison_metrics, evaluate_dataset, main


def _item(ident: str = "one", **changes: object) -> dict:
    return {
        "id": ident, "case_id": "stars", "input": "只输出三颗星。", "source": 'puts("***");',
        "stdout": "***\n", "reference": "pass", "group": "observed", **changes,
    }


def _write(tmp_path: Path, items: list[dict]) -> tuple[Path, Path]:
    cases = tmp_path / "items.json"
    cases.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    responses = tmp_path / "responses"
    (responses / "contracts").mkdir(parents=True)
    (responses / "reviews").mkdir()
    return cases, responses


def test_frozen_replay_separates_real_violation_from_good_output(tmp_path: Path) -> None:
    items, responses = _write(tmp_path, [_item(), _item("two", stdout="****\n", reference="fail")])
    (responses / "contracts/stars.txt").write_text(json.dumps({"checks": [{
        "check": {"type": "symbol_count", "symbol": "*", "equals": 3},
        "evidence": "三颗星",
    }], "uncheckable": []}, ensure_ascii=False), encoding="utf-8")
    (responses / "reviews/one.txt").write_text('{"verdict":"pass","findings":[]}',
                                              encoding="utf-8")
    (responses / "reviews/two.txt").write_text(json.dumps({"verdict": "fail", "findings": [{
        "reason": "四颗多于三颗", "evidence": [
            {"origin": "input", "quote": "三颗星"}, {"origin": "stdout", "quote": "****"},
        ],
    }]}, ensure_ascii=False), encoding="utf-8")
    saved = evaluate_dataset(items, responses)
    assert saved["model_calls"] == saved["program_executions"] == 0
    for method in ("contract", "review"):
        assert saved["summary"][method]["caught_bad"] == 1
        assert saved["summary"][method]["accepted_good"] == 1
        assert saved["summary"][method]["rejected_good"] == 0


def test_missing_or_invented_evidence_is_invalid_not_acceptance(tmp_path: Path) -> None:
    items, responses = _write(tmp_path, [_item()])
    (responses / "reviews/one.txt").write_text(json.dumps({"verdict": "fail", "findings": [{
        "reason": "unfounded", "evidence": [
            {"origin": "input", "quote": "三颗星"}, {"origin": "stdout", "quote": "FAKE"},
        ],
    }]}), encoding="utf-8")
    report = evaluate_dataset(items, responses)
    for method in ("contract", "review"):
        assert report["summary"][method]["invalid"] == 1
        assert report["summary"][method]["accepted_good"] == 0
        assert report["summary"][method]["rejected_good"] == 0
    assert main(["--items", str(items), "--responses", str(responses),
                 "--output", str(tmp_path / "report.json")]) == 1


def test_abstention_and_contested_reference_do_not_inflate_accuracy() -> None:
    rows = [
        {"reference": "pass", "review": {"status": "abstain"}},
        {"reference": "fail", "review": {"status": "invalid"}},
        {"reference": "contested", "review": {"status": "pass"}},
    ]
    report = comparison_metrics(rows, "review")
    assert report["known_reference_coverage"] == 0
    assert report["decisive_accuracy"] is None
    assert report["bad_detection_rate"] == 0
    assert report["reference_contested"] == 1


def test_cli_does_not_overwrite_material_or_response(tmp_path: Path) -> None:
    items, responses = _write(tmp_path, [_item()])
    original = items.read_bytes()
    for destination in [items, responses / "contracts/stars.txt", responses / "reviews/one.txt"]:
        assert main(["--items", str(items), "--responses", str(responses),
                     "--output", str(destination)]) == 2
        assert items.read_bytes() == original
        if destination != items:
            assert not destination.exists()


@pytest.mark.parametrize("items", [[], [_item("../outside")], [_item(), _item()], [
    _item(), _item("two", input="同一个case_id的另一个输入"),
]])
def test_bad_materials_are_rejected(tmp_path: Path, items: list[dict]) -> None:
    path, responses = _write(tmp_path, items)
    with pytest.raises(ValueError):
        evaluate_dataset(path, responses)
