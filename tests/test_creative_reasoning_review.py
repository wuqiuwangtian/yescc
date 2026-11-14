"""Review gates retain failures, uncertainty, fixed repeats and original scores."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bench.creative_reasoning_review import analyze, materialize, sha, write_new


@pytest.fixture
def evidence(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    protocol = json.loads((root / "bench/creative_reasoning_protocol.json").read_text("utf-8"))
    mapping = {f"opaque-{i:02}": task for i, task in enumerate(protocol["tasks"])}
    write_new(tmp_path / "review-mapping-discovery.json", mapping)
    target = tmp_path / "snapshot/bench/creative_reasoning_rubric.json"
    target.parent.mkdir(parents=True)
    target.write_bytes((root / "bench/creative_reasoning_rubric.json").read_bytes())
    protocol_path = tmp_path / "snapshot/bench/creative_reasoning_protocol.json"
    protocol_path.write_bytes((root / "bench/creative_reasoning_protocol.json").read_bytes())
    write_new(tmp_path / "phases/discovery.json", {"tasks": protocol["tasks"]})
    write_new(tmp_path / "execution-freeze.json", {
        "protocol_sha256": sha(protocol_path), "files": {relative: sha(tmp_path / relative)
        for relative in ("phases/discovery.json", "review-mapping-discovery.json")},
    })
    for task in mapping.values():
        directory = tmp_path / "runs" / task["task_id"]
        write_new(directory / "outcome.json", {
            "task": task, "case": {"input": "只写测试材料"}, "exit_code": 0,
            "source_unchanged": True, "duration_ms": 100,
            "report": {"model": {"cost_usd": 0.001, "cost_known": True}, "result": {
                "fallback": {"kind": "model_creative"},
                "generated": {"validation": {"passed": True}},
            }},
        })
        (directory / "program.stdout").write_bytes(b"work\n")
        (directory / "generated.c").write_text("int main(void){return 0;}\n")
    materialize(tmp_path)
    rows = [{"id": ident, "scores": {"relevance": 3, "creativity": 3,
                                      "programmatic": 2, "integrity": 3},
             "label": "strong", "consistency": "pass", "reason": "test evidence",
             "consistency_evidence": []} for ident in mapping]
    for suffix in ("a", "b"):
        write_new(tmp_path / "reviews" / f"discovery-{suffix}.json", rows)
    return tmp_path


def update_review(workspace: Path, suffix: str, ident: str, **changes) -> None:
    path = workspace / "reviews" / f"discovery-{suffix}.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    next(r for r in rows if r["id"] == ident).update(changes)
    path.write_text(json.dumps(rows))


def primary_id(workspace: Path, profile: str) -> str:
    mapping = json.loads((workspace / "review-mapping-discovery.json").read_text(encoding="utf-8"))
    return next(i for i, t in mapping.items() if t["profile"] == profile and t["repeat"] == 1)


def test_primary_and_repeats_stay_separate(evidence: Path) -> None:
    result = analyze(evidence)
    assert result["groups"]["default"]["count"] == 12
    assert result["groups"]["default_repeat"]["count"] == 2
    assert result["groups"]["default"]["joint_creative_ready"] == 12
    assert result["selected_profile"] is None  # No gain over the equal baseline.


def test_null_is_preserved_and_failure_stays_in_utility_denominator(evidence: Path) -> None:
    ident = primary_id(evidence, "low")
    for suffix in ("a", "b"):
        update_review(evidence, suffix, ident, label="unscorable", consistency="uncertain",
                      scores={k: None for k in ("relevance", "creativity",
                                               "programmatic", "integrity")})
    result = analyze(evidence)["groups"]["low"]
    assert result["count"] == 12
    assert result["joint_creative_ready"] == 11
    assert result["creativity_utility"] == pytest.approx(66 / 24)
    assert result["conditional_mean_creativity"] == 3
    raw = json.loads((evidence / "reviews/discovery-a.json").read_text(encoding="utf-8"))
    assert next(r for r in raw if r["id"] == ident)["scores"]["creativity"] is None


def test_one_rater_fail_keeps_strong_but_vetoes_ready(evidence: Path) -> None:
    ident = primary_id(evidence, "tokens")
    update_review(
        evidence, "a", ident, consistency="fail", consistency_evidence=["explicit conflict"],
    )
    g = analyze(evidence)["groups"]["tokens"]
    assert g["joint_strong"] == 12
    assert g["joint_creative_ready"] == 11
    assert g["contradictions_any_rater"] == 1


def test_material_changes_are_not_silently_rereviewed(evidence: Path) -> None:
    ident = primary_id(evidence, "default")
    with (evidence / "review-materials-discovery/items" / f"{ident}.json").open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="material changed"):
        analyze(evidence)


def test_missing_review_cannot_be_dropped_from_denominator(evidence: Path) -> None:
    path = evidence / "reviews/discovery-a.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(rows[:-1]))
    with pytest.raises(ValueError, match="missing"):
        analyze(evidence)


def test_unknown_cost_blocks_selection_across_groups(evidence: Path) -> None:
    mapping = json.loads((evidence / "review-mapping-discovery.json").read_text(encoding="utf-8"))
    task = mapping[primary_id(evidence, "tokens")]
    path = evidence / "runs" / task["task_id"] / "outcome.json"
    outcome = json.loads(path.read_text(encoding="utf-8"))
    outcome["report"]["model"]["cost_known"] = False
    path.write_text(json.dumps(outcome))
    result = analyze(evidence)
    assert result["groups"]["tokens"]["unknown_cost_tasks"] == 1
    assert all(not v["all_costs_known"] for v in result["gates"].values())


def test_materials_are_anonymous_and_do_not_publish_design_notes(evidence: Path) -> None:
    ident = primary_id(evidence, "default")
    path = evidence / "review-materials-discovery/items" / f"{ident}.json"
    item = json.loads(path.read_text(encoding="utf-8"))
    assert set(item) == {"id", "input", "source", "stdout", "evidence"}
    assert not {"profile", "model", "duration_ms", "cost", "literal_requirements"} & set(item)
    with pytest.raises(FileExistsError):
        materialize(evidence)


def test_mapping_cannot_shrink_the_planned_denominator(evidence: Path) -> None:
    path = evidence / "review-mapping-discovery.json"
    mapping = json.loads(path.read_text(encoding="utf-8"))
    mapping.pop(next(iter(mapping)))
    path.write_text(json.dumps(mapping))
    with pytest.raises(ValueError, match="frozen task plan"):
        analyze(evidence)


def test_duplicate_task_mapping_is_rejected_even_with_rehashed_manifest(evidence: Path) -> None:
    path = evidence / "review-mapping-discovery.json"
    mapping = json.loads(path.read_text(encoding="utf-8"))
    a, b = list(mapping)[:2]
    mapping[a] = mapping[b]
    path.write_text(json.dumps(mapping))
    freeze_path = evidence / "execution-freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    freeze["files"]["review-mapping-discovery.json"] = sha(path)
    freeze_path.write_text(json.dumps(freeze))
    with pytest.raises(ValueError, match="cover all frozen tasks"):
        analyze(evidence)


def test_claimed_success_without_source_blocks_selection(evidence: Path) -> None:
    ident = primary_id(evidence, "low")
    path = evidence / "review-materials-discovery/items" / f"{ident}.json"
    item = json.loads(path.read_text(encoding="utf-8"))
    item["source"] = ""
    path.write_text(json.dumps(item))
    manifest_path = evidence / "discovery-material-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[f"items/{ident}.json"] = sha(path)
    manifest_path.write_text(json.dumps(manifest))
    for suffix in ("a", "b"):
        update_review(evidence, suffix, ident, label="unscorable", consistency="uncertain",
                      scores={k: None for k in ("relevance", "creativity",
                                               "programmatic", "integrity")})
    result = analyze(evidence)
    assert all(not g["core_evidence_complete"] for g in result["gates"].values())
