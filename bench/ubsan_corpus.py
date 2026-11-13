"""Prepare exact stored C sources for offline instrumentation; no model calls."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "docs/评测/data/creative-generation-support-2026-11-13.json.gz"
EXAMPLES = ROOT / "bench/creative_generation_examples.json"
FIXTURES = ROOT / "bench/ubsan_cases.json"


def _restore(files: dict[str, Any], name: str) -> bytes:
    item = files[name]
    raw = base64.b64decode(item["base64"], validate=True)
    if len(raw) != item["size"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError(f"archived bytes do not match manifest: {name}")
    return raw


def prepare_corpus(
    output_dir: Path,
    *,
    archive: Path = ARCHIVE,
    examples: Path = EXAMPLES,
    fixtures: Path = FIXTURES,
) -> dict[str, Any]:
    """Make a new corpus, binding every model source to its saved run report."""
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError("output directory already exists; choose a new path")
    packed = archive.read_bytes()
    evidence = json.loads(gzip.decompress(packed))
    files = evidence["files"]
    tasks = json.loads(_restore(files, "protocol.json"))["tasks"]
    if len(tasks) != 54 or len({task["task_id"] for task in tasks}) != 54:
        raise ValueError("expected the frozen 54-task generation study")
    cases: list[dict[str, Any]] = []
    origins: dict[str, Any] = {}
    for task in tasks:
        prefix = f"runs/{task['task_id']}"
        source = _restore(files, prefix + "/generated.c")
        outcome = json.loads(_restore(files, prefix + "/outcome.json"))
        generated = outcome["report"]["result"]["generated"]
        if (
            outcome["task"] != task
            or outcome["exit_code"] != 0
            or not generated["validation"]["passed"]
            or outcome["report"]["result"]["fallback"]["kind"] != "model_creative"
        ):
            raise ValueError(f"archive lacks a successful model run: {task['task_id']}")
        ident = "model-" + task["task_id"]
        cases.append({
            "id": ident,
            "source": source.decode("utf-8"),
            "expectation": "boundary",
            "category": "archived_model_candidate",
            "note": "Previously passed ordinary run; UB status unknown, not a semantic oracle.",
        })
        origins[ident] = {"cohort": "model", "task": task,
                          "source_sha256": hashlib.sha256(source).hexdigest()}
    example_raw = examples.read_bytes()
    frozen_example_raw = _restore(files, "tool-snapshot/bench/creative_generation_examples.json")
    if example_raw != frozen_example_raw:
        raise ValueError("examples differ from the original frozen generation study")
    for item in json.loads(example_raw):
        ident = "example-" + item["id"]
        cases.append({"id": ident, "source": item["source"], "expectation": "defined",
                      "category": "frozen_generation_example", "note": item["note"],
                      "expected_stdout": item["stdout"]})
        origins[ident] = {"cohort": "example", "original_id": item["id"]}
    fixture_raw = fixtures.read_bytes()
    for item in json.loads(fixture_raw):
        ident = "fixture-" + item["id"]
        cases.append({**item, "id": ident})
        origins[ident] = {"cohort": "fixture", "original_id": item["id"]}
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("duplicate corpus case identifier")
    raw = (json.dumps(cases, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest = {
        "schema": "yescc-ubsan-corpus-v1", "model_calls": 0,
        "archive_sha256": hashlib.sha256(packed).hexdigest(),
        "examples_sha256": hashlib.sha256(example_raw).hexdigest(),
        "fixtures_sha256": hashlib.sha256(fixture_raw).hexdigest(),
        "cases_sha256": hashlib.sha256(raw).hexdigest(),
        "counts": {"model": len(tasks), "example": 2, "fixture": len(json.loads(fixture_raw))},
        "origins": origins,
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "cases.json").write_bytes(raw)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = prepare_corpus(args.output_dir)
    print(json.dumps({key: value for key, value in result.items() if key != "origins"}))


if __name__ == "__main__":
    main()
