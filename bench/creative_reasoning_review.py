"""Anonymous materials and frozen gates; no network, paid judges or execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DIMENSIONS = {"relevance", "creativity", "programmatic", "integrity"}


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_new(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_mapping(workspace: Path, phase: str) -> dict[str, Any]:
    freeze = read(workspace / "execution-freeze.json")
    manifest = freeze["files"] if phase == "discovery" else read(
        workspace / "confirmation-freeze.json")["files"]
    for relative in (f"phases/{phase}.json", f"review-mapping-{phase}.json"):
        if sha(workspace / relative) != manifest[relative]:
            raise ValueError("frozen task plan or mapping changed")
    plan = read(workspace / f"phases/{phase}.json")
    mapping = read(workspace / f"review-mapping-{phase}.json")
    if phase == "discovery":
        protocol_path = workspace / "snapshot/bench/creative_reasoning_protocol.json"
        if sha(protocol_path) != freeze["protocol_sha256"]:
            raise ValueError("frozen protocol changed")
        expected = read(protocol_path)["tasks"]
    else:
        expected = plan["tasks"]
    def canonical(tasks: Iterable[dict[str, Any]]) -> list[str]:
        return sorted(json.dumps(t, sort_keys=True) for t in tasks)
    count = 42 if phase == "discovery" else 16
    if (len(mapping) != count or len(expected) != count
            or len({t["task_id"] for t in mapping.values()}) != count
            or canonical(mapping.values()) != canonical(expected)
            or canonical(plan["tasks"]) != canonical(expected)):
        raise ValueError("mapping does not cover all frozen tasks exactly once")
    return dict(mapping)


def materialize(workspace: Path, phase: str = "discovery") -> dict[str, str]:
    mapping = frozen_mapping(workspace, phase)
    materials = workspace / f"review-materials-{phase}"
    if materials.exists():
        raise FileExistsError(materials)
    rows = []
    for ident, task in mapping.items():
        directory = workspace / "runs" / task["task_id"]
        outcome = read(directory / "outcome.json")
        report = outcome["report"] or {}
        result = report.get("result") or {}
        model = (result.get("fallback") or {}).get("kind") == "model_creative"
        raw = (directory / "program.stdout").read_bytes()
        try:
            stdout = raw.decode("utf-8")
            utf8 = True
        except UnicodeDecodeError:
            stdout = raw.decode("utf-8", errors="replace")
            utf8 = False
        source = directory / "generated.c"
        validation = ((result.get("generated") or {}).get("validation") or {})
        rows.append((ident, {
            "id": ident, "input": outcome["case"]["input"],
            "source": source.read_text("utf-8") if source.exists() else "",
            "stdout": stdout,
            "evidence": {
                "cli_exit": outcome["exit_code"], "model_generated": model,
                "actual_run_validation": validation.get("passed") if model else None,
                "stdout_utf8_valid": utf8, "source_unchanged": outcome["source_unchanged"],
                "stdout_sha256": hashlib.sha256(raw).hexdigest(),
            },
        }))
    materials.mkdir()
    for ident, item in rows:
        write_new(materials / "items" / f"{ident}.json", item)
    ids = list(mapping)
    random.Random(2026111411 if phase == "discovery" else 2026111412).shuffle(ids)
    write_new(materials / "index.json", {"count": len(ids), "items": [
        f"items/{ident}.json" for ident in ids
    ]})
    rubric = workspace / "snapshot/bench/creative_reasoning_rubric.json"
    (materials / "rubric.json").write_bytes(rubric.read_bytes())
    manifest = {str(p.relative_to(materials)): sha(p) for p in sorted(materials.rglob("*.json"))}
    write_new(workspace / f"{phase}-material-manifest.json", manifest)
    return manifest


def valid_work(item: dict[str, Any]) -> bool:
    e = item["evidence"]
    return bool(e["model_generated"] and e["actual_run_validation"] is True
                and e["cli_exit"] == 0 and e["stdout_utf8_valid"] and e["source_unchanged"]
                and item["source"])


def strong(row: dict[str, Any]) -> bool:
    s = row["scores"]
    return (all(type(v) is int for v in s.values()) and sum(s.values()) >= 9
            and s["relevance"] >= 2 and s["integrity"] >= 2
            and s["creativity"] >= 1 and s["programmatic"] >= 1)


def analyze(workspace: Path, phase: str = "discovery") -> dict[str, Any]:
    materials = workspace / f"review-materials-{phase}"
    manifest = read(workspace / f"{phase}-material-manifest.json")
    if any(sha(materials / p) != digest for p, digest in manifest.items()):
        raise ValueError("review material changed")
    mapping = frozen_mapping(workspace, phase)
    reviews = []
    for suffix in ("a", "b"):
        rows = read(workspace / "reviews" / f"{phase}-{suffix}.json")
        indexed = {r["id"]: r for r in rows}
        if len(rows) != len(indexed) or set(indexed) != set(mapping):
            raise ValueError("missing, duplicate or unexpected review IDs")
        for ident, row in indexed.items():
            if (set(row["scores"]) != DIMENSIONS
                    or any(v is not None and (type(v) is not int or not 0 <= v <= 3)
                           for v in row["scores"].values())
                    or row["consistency"] not in {"pass", "fail", "uncertain"}
                    or row["label"] not in {"strong", "mixed", "fallback", "unscorable"}
                    or not row["reason"] or not isinstance(row["consistency_evidence"], list)
                    or (row["consistency"] == "fail" and not row["consistency_evidence"])):
                raise ValueError(f"invalid review: {ident}")
            item = read(materials / "items" / f"{ident}.json")
            if row["label"] != "unscorable":
                expected = valid_work(item) and strong(row)
                if (row["label"] == "strong") != expected:
                    raise ValueError(f"strong label disagrees with frozen rubric: {ident}")
        reviews.append(indexed)
    groups: dict[str, Any] = {}
    differences = []
    complete = True
    for ident, task in mapping.items():
        key = task["profile"] + ("_repeat" if task["repeat"] > 1 else "")
        g = groups.setdefault(key, {
            "count": 0, "joint_creative_ready": 0, "joint_strong": 0, "model_run": 0,
            "contradictions_any_rater": 0, "uncertain_any_rater": 0,
            "creativity_sum": 0, "scorable_creativity_ratings": 0,
            "duration_ms": [], "known_cost_subtotal_usd": 0.0, "unknown_cost_tasks": 0,
        })
        item = read(materials / "items" / f"{ident}.json")
        outcome = read(workspace / "runs" / task["task_id"] / "outcome.json")
        model = (outcome["report"] or {}).get("model") or {}
        a, b = (r[ident] for r in reviews)
        valid = valid_work(item)
        complete = complete and outcome["source_unchanged"] and bool(outcome["report"])
        if (item["evidence"]["model_generated"]
                and item["evidence"]["actual_run_validation"] is True):
            complete = complete and valid and bool(item["stdout"].strip())
        g["count"] += 1
        g["model_run"] += valid
        g["joint_strong"] += valid and a["label"] == b["label"] == "strong"
        g["joint_creative_ready"] += valid and all(
            r["label"] == "strong" and r["scores"]["creativity"] >= 2
            and r["consistency"] == "pass" for r in (a, b)
        )
        g["contradictions_any_rater"] += any(r["consistency"] == "fail" for r in (a, b))
        g["uncertain_any_rater"] += any(r["consistency"] == "uncertain" for r in (a, b))
        for row in (a, b):
            if valid and row["label"] != "unscorable" and row["scores"]["creativity"] is not None:
                g["creativity_sum"] += row["scores"]["creativity"]
                g["scorable_creativity_ratings"] += 1
        g["duration_ms"].append(outcome["duration_ms"])
        g["known_cost_subtotal_usd"] += model.get("cost_usd", 0.0)
        g["unknown_cost_tasks"] += model.get("cost_known") is not True
        if a["consistency"] != b["consistency"] or a["label"] != b["label"]:
            differences.append({"id": ident, **task, "a": a, "b": b})
    for g in groups.values():
        g["creativity_utility"] = g["creativity_sum"] / (2 * g["count"])
        n = g["scorable_creativity_ratings"]
        g["conditional_mean_creativity"] = g["creativity_sum"] / n if n else None
        g["mean_wall_ms"] = sum(g["duration_ms"]) / g["count"]
    base = groups["default"]
    gates: dict[str, dict[str, bool]] = {}
    for profile in ("tokens", "low"):
        if profile not in groups:
            continue
        g = groups[profile]
        checks = {
            "joint_ready_gain": g["joint_creative_ready"] >= base["joint_creative_ready"]
            + (2 if phase == "discovery" else 1),
            "creativity_gain": g["creativity_utility"] >= base["creativity_utility"]
            + (0.2 if phase == "discovery" else 0) - 1e-12,
            "model_run_nonregression": g["model_run"] >= base["model_run"],
            "contradictions_nonregression": g["contradictions_any_rater"]
            <= base["contradictions_any_rater"],
            "wall_time_limit": g["mean_wall_ms"] <= 2 * base["mean_wall_ms"],
            "all_costs_known": all(x["unknown_cost_tasks"] == 0 for x in groups.values()),
            "core_evidence_complete": complete,
        }
        if phase == "discovery":
            checks["repeat_nonregression"] = groups[profile + "_repeat"]["joint_creative_ready"] \
                >= groups["default_repeat"]["joint_creative_ready"]
        gates[profile] = {**checks, "eligible": all(checks.values())}
    eligible = [p for p, values in gates.items() if values["eligible"]]
    def rank(profile: str) -> tuple[Any, ...]:
        g = groups[profile]
        return (-g["joint_creative_ready"], -g["creativity_utility"], g["mean_wall_ms"],
                g["known_cost_subtotal_usd"], profile)

    selected = min(eligible, key=rank) if eligible else None
    return {"phase": phase, "groups": groups, "gates": gates, "selected_profile": selected,
            "disagreements": differences, "material_manifest_sha256": sha(
                workspace / f"{phase}-material-manifest.json"),
            "reviews_sha256": {s: sha(workspace / "reviews" / f"{phase}-{s}.json")
                               for s in ("a", "b")}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["materials", "analyze"])
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--phase", choices=["discovery", "confirmation"], default="discovery")
    args = parser.parse_args()
    if args.action == "materials":
        materialize(args.workspace, args.phase)
    else:
        write_new(
            args.workspace / f"{args.phase}-selection.json", analyze(args.workspace, args.phase),
        )


if __name__ == "__main__":
    main()
