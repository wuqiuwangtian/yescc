"""Historical evidence stays immutable while current production can evolve."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from bench.creative_reasoning_archive import ARCHIVE, ROOT, restore_snapshot


def test_restore_only_original_snapshot_bytes_without_execution(tmp_path: Path) -> None:
    target = tmp_path / "source"
    with (
        patch("httpx.Client.send", side_effect=AssertionError("network forbidden")),
        patch("subprocess.Popen", side_effect=AssertionError("execution forbidden")),
    ):
        assert restore_snapshot(target) == target
    files = [p for p in target.rglob("*") if p.is_file()]
    assert len(files) == 47
    assert not any("confirmation" in str(p.relative_to(target)) for p in files)
    protocol = json.loads((target / "bench/creative_reasoning_protocol.json").read_text("utf-8"))
    for relative, expected in protocol["frozen_files"].items():
        assert hashlib.sha256((target / relative).read_bytes()).hexdigest() == expected
    assert (target / "bench/creative_reasoning.py").read_bytes() != (
        ROOT / "bench/creative_reasoning.py").read_bytes()
    with pytest.raises(FileExistsError):
        restore_snapshot(target)


def test_corrupt_archive_is_rejected_before_destination_creation(tmp_path: Path) -> None:
    corrupt = tmp_path / "bad.json.gz"
    corrupt.write_bytes(ARCHIVE.read_bytes() + b"tampered")
    target = tmp_path / "source"
    with pytest.raises(ValueError, match="archive changed"):
        restore_snapshot(target, archive=corrupt)
    assert not target.exists()


def test_restore_cannot_overlay_repository_sources() -> None:
    with pytest.raises(ValueError, match="outside repository"):
        restore_snapshot(ROOT / "dist/forbidden-historical-source")
