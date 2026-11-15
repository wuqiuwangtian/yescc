"""Replay three-arm fact-review experiments without network or program execution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from bench.creative_consistency import _validate_report_destination
from bench.creative_fact_review import evaluate_fact_review
from bench.creative_review import evaluate_review
from bench.creative_review_eval import _load_items, _response, comparison_metrics

ARMS = ("basic", "facts", "facts_low")


def evaluate_dataset(items_path: Path, responses: Path) -> dict[str, Any]:
    items, fingerprint = _load_items(items_path)
    rows = []
    for item in items:
        row: dict[str, Any] = {
            "id": item["id"], "group": item.get("group", "unspecified"),
            "reference": item["reference"],
        }
        for arm in ARMS:
            text, metadata = _response(responses / arm / f"{item['id']}.txt")
            evaluate = evaluate_review if arm == "basic" else evaluate_fact_review
            row[arm] = {
                **(evaluate(text, item["input"], item["source"], item["stdout"])
                   if text is not None else {}), **metadata,
            }
        rows.append(row)
    return {
        "schema_version": 1, "scope": "offline_three_arm_fact_review",
        "items_sha256": fingerprint, "model_calls": 0, "program_executions": 0,
        "summary": {arm: comparison_metrics(rows, arm) for arm in ARMS},
        "groups": {
            group: {arm: comparison_metrics([r for r in rows if r["group"] == group], arm)
                    for arm in ARMS}
            for group in sorted({r["group"] for r in rows})
        },
        "results": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=Path, required=True)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        items, _ = _load_items(args.items)
        protected = [args.items, *(args.responses / arm / f"{item['id']}.txt"
                                  for item in items for arm in ARMS)]
        _validate_report_destination(args.output, protected)
        report = evaluate_dataset(args.items, args.responses)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc:
        print(f"creative fact review: {exc}", file=sys.stderr)
        return 2
    return 1 if any(report["summary"][arm]["invalid"] for arm in ARMS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
