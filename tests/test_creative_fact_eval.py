"""Three arms keep missing judgments and disputed references distinct."""

from __future__ import annotations

import json
from pathlib import Path

from bench.creative_fact_eval import ARMS, evaluate_dataset, main


def _material(tmp_path: Path) -> Path:
    path = tmp_path / "items.json"
    path.write_text(json.dumps([{
        "id": "one", "case_id": "one", "input": "只输出一颗星", "source": 'puts("*");',
        "stdout": "*\n", "reference": "pass", "group": "fresh",
    }, {
        "id": "ambiguous", "case_id": "ambiguous", "input": "一个星星的故事",
        "source": 'puts("*");', "stdout": "*\n", "reference": "contested", "group": "diagnostic",
    }], ensure_ascii=False), encoding="utf-8")
    return path


def test_replay_all_arms_and_exclude_contested_accuracy(tmp_path: Path) -> None:
    items = _material(tmp_path)
    for arm in ARMS:
        directory = tmp_path / arm
        directory.mkdir()
        if arm == "basic":
            data = {"verdict": "pass", "findings": []}
        else:
            check = {"topic": "count", "status": "consistent", "reason": "一颗星",
                     "evidence": [{"origin": "stdout", "quote": "*"},
                                  {"origin": "source", "quote": 'puts("*");'}]}
            data = {"verdict": "pass", "checks": [check, {**check, "topic": "state"}]}
        for ident in ("one", "ambiguous"):
            (directory / f"{ident}.txt").write_text(json.dumps(data, ensure_ascii=False),
                                                  encoding="utf-8")
    report = evaluate_dataset(items, tmp_path)
    assert report["model_calls"] == report["program_executions"] == 0
    for arm in ARMS:
        assert report["summary"][arm]["accepted_good"] == 1
        assert report["summary"][arm]["reference_contested"] == 1
        assert report["summary"][arm]["decisive_known"] == 1
        assert report["groups"]["diagnostic"][arm]["decisive_accuracy"] is None


def test_missing_response_is_invalid_in_every_arm(tmp_path: Path) -> None:
    items = _material(tmp_path)
    report = evaluate_dataset(items, tmp_path)
    assert all(report["summary"][arm]["invalid"] == 2 for arm in ARMS)
    output = tmp_path / "report.json"
    assert main(["--items", str(items), "--responses", str(tmp_path),
                 "--output", str(output)]) == 1
    assert json.loads(output.read_text("utf-8"))["model_calls"] == 0


def test_report_cannot_replace_missing_response_or_input(tmp_path: Path) -> None:
    items = _material(tmp_path)
    original = items.read_bytes()
    for output in (items, tmp_path / "facts_low/one.txt"):
        assert main(["--items", str(items), "--responses", str(tmp_path),
                     "--output", str(output)]) == 2
    assert items.read_bytes() == original
    assert not (tmp_path / "facts_low/one.txt").exists()
