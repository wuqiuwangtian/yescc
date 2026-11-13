"""Replay captured contract/reviewer responses; never call models or execute C."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from bench.creative_consistency import _validate_report_destination
from bench.creative_review import evaluate_contract, evaluate_review


def comparison_metrics(rows: list[dict[str, Any]], method: str) -> dict[str, Any]:
    """Reference labels are frozen checks/AI judgments, not human ground truth."""
    counts = dict.fromkeys(
        ("reference_pass", "reference_fail", "reference_contested", "accepted_good",
         "rejected_good", "caught_bad", "accepted_bad", "abstain", "invalid",
         "decisive_known", "total"), 0,
    )
    for row in rows:
        reference, status = row["reference"], row[method]["status"]
        counts["total"] += 1
        counts[f"reference_{reference}"] += 1
        if status in {"invalid", "abstain"}:
            counts[status] += 1
        if reference == "contested" or status not in {"pass", "fail"}:
            continue
        counts["decisive_known"] += 1
        key = {
            ("pass", "pass"): "accepted_good", ("pass", "fail"): "rejected_good",
            ("fail", "fail"): "caught_bad", ("fail", "pass"): "accepted_bad",
        }[(reference, status)]
        counts[key] += 1

    def ratio(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    known = counts["reference_pass"] + counts["reference_fail"]
    return {
        **counts,
        "known_reference_coverage": ratio(counts["decisive_known"], known),
        "bad_detection_rate": ratio(counts["caught_bad"], counts["reference_fail"]),
        "good_rejection_rate": ratio(counts["rejected_good"], counts["reference_pass"]),
        "decisive_accuracy": ratio(counts["accepted_good"] + counts["caught_bad"],
                                   counts["decisive_known"]),
    }


def _load_items(path: Path) -> tuple[list[dict[str, Any]], str]:
    raw = path.read_bytes()
    items = json.loads(raw)
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a nonempty list")
    seen: set[str] = set()
    case_inputs: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each item must be an object")
        for field in ("id", "case_id", "input", "source", "stdout"):
            if not isinstance(item.get(field), str):
                raise ValueError(f"item.{field} must be a string")
        for field in ("id", "case_id"):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", item[field]) is None:
                raise ValueError(f"item.{field} must be a safe filename identifier")
        if item["id"] in seen:
            raise ValueError("duplicate item id")
        seen.add(item["id"])
        if not isinstance(item.get("reference"), str) or item["reference"] not in {
            "pass", "fail", "contested",
        }:
            raise ValueError("reference must be pass/fail/contested")
        if item["case_id"] in case_inputs and case_inputs[item["case_id"]] != item["input"]:
            raise ValueError("one contract case_id cannot have different inputs")
        case_inputs[item["case_id"]] = item["input"]
        if "group" in item and (not isinstance(item["group"], str) or not item["group"]):
            raise ValueError("group must be a nonempty string")
    return items, hashlib.sha256(raw).hexdigest()


def _response(path: Path) -> tuple[str | None, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as exc:
        return None, {"status": "invalid", "passed": None, "error": str(exc)}
    return text, {"response_sha256": hashlib.sha256(raw).hexdigest()}


def evaluate_dataset(items_path: Path, responses: Path) -> dict[str, Any]:
    items, fingerprint = _load_items(items_path)
    results = []
    for item in items:
        contract, contract_meta = _response(responses / "contracts" / f"{item['case_id']}.txt")
        review, review_meta = _response(responses / "reviews" / f"{item['id']}.txt")
        results.append({
            "id": item["id"], "case_id": item["case_id"],
            "group": item.get("group", "unspecified"), "reference": item["reference"],
            "has_fixed_checks": bool(item.get("checks", [])),
            "contract": {
                **(evaluate_contract(contract, item["input"], item["stdout"].encode("utf-8"))
                   if contract is not None else {}), **contract_meta,
            },
            "review": {
                **(evaluate_review(review, item["input"], item["source"], item["stdout"])
                   if review is not None else {}), **review_meta,
            },
        })
    methods = ("contract", "review")
    return {
        "schema_version": 1, "scope": "offline_captured_review_responses",
        "items_sha256": fingerprint, "model_calls": 0, "program_executions": 0,
        "summary": {method: comparison_metrics(results, method) for method in methods},
        "groups": {
            group: {method: comparison_metrics([r for r in results if r["group"] == group], method)
                    for method in methods}
            for group in sorted({r["group"] for r in results})
        },
        "slices": {
            f"{group}:{scope}": {
                method: comparison_metrics([
                    r for r in results if r["group"] == group and r["has_fixed_checks"] == explicit
                ], method) for method in methods
            }
            for group in sorted({r["group"] for r in results})
            for scope, explicit in (("explicit", True), ("open", False))
        },
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=Path, required=True)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        items, _ = _load_items(args.items)
        protected = [args.items]
        for item in items:
            protected.extend([
                args.responses / "contracts" / f"{item['case_id']}.txt",
                args.responses / "reviews" / f"{item['id']}.txt",
            ])
        _validate_report_destination(args.output, protected)
        report = evaluate_dataset(args.items, args.responses)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc:
        print(f"creative review: {exc}", file=sys.stderr)
        return 2
    invalid = any(report["summary"][method]["invalid"] for method in ("contract", "review"))
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
