from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from bench.ubsan_corpus import ARCHIVE, EXAMPLES, prepare_corpus


def test_corpus_preserves_stored_sources_and_cohorts(tmp_path: Path) -> None:
    output = tmp_path / "prepared"
    manifest = prepare_corpus(output)
    assert manifest["counts"] == {"model": 54, "example": 2, "fixture": 16}
    raw = (output / "cases.json").read_bytes()
    assert manifest["cases_sha256"] == hashlib.sha256(raw).hexdigest()
    cases = json.loads(raw)
    assert len(cases) == 72
    files = json.loads(gzip.decompress(ARCHIVE.read_bytes()))["files"]
    for case in cases:
        origin = manifest["origins"][case["id"]]
        if origin["cohort"] == "model":
            task = origin["task"]["task_id"]
            original = base64.b64decode(files[f"runs/{task}/generated.c"]["base64"])
            assert case["source"].encode("utf-8") == original
            assert case["expectation"] == "boundary"
    assert manifest["model_calls"] == 0


def test_corpus_does_not_overwrite_existing_directory(tmp_path: Path) -> None:
    marker = tmp_path / "user.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        prepare_corpus(tmp_path)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_corpus_rejects_changed_archived_source(tmp_path: Path) -> None:
    data = json.loads(gzip.decompress(ARCHIVE.read_bytes()))
    name = next(name for name in data["files"] if name.endswith("/generated.c"))
    data["files"][name]["base64"] = base64.b64encode(b"changed").decode()
    archive = tmp_path / "changed.gz"
    archive.write_bytes(gzip.compress(json.dumps(data).encode()))
    with pytest.raises(ValueError, match="manifest"):
        prepare_corpus(tmp_path / "prepared", archive=archive)
    assert not (tmp_path / "prepared").exists()


def test_corpus_rejects_modified_examples(tmp_path: Path) -> None:
    examples = tmp_path / "examples.json"
    examples.write_bytes(EXAMPLES.read_bytes() + b" ")
    with pytest.raises(ValueError, match="examples differ"):
        prepare_corpus(tmp_path / "prepared", examples=examples)
