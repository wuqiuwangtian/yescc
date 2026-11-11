"""Candidate extraction checks canonical destinations and still rejects escapes."""

from __future__ import annotations

import hashlib
import json
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from bench import binary_archive_smoke


def test_archive_install_normalizes_temporary_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "candidate.zip"
    content = b"fixture executable"
    manifest = {
        "version": "0.1.0",
        "files": {
            "yescc": {"sha256": hashlib.sha256(content).hexdigest()},
        },
    }
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("yescc-0.1.0/yescc", content)
        zipped.writestr("yescc-0.1.0/MANIFEST.json", json.dumps(manifest))
    monkeypatch.chdir(tmp_path)

    @contextmanager
    def directory(**_kwargs: object):
        Path("install").mkdir()
        yield "install"

    monkeypatch.setattr(binary_archive_smoke.tempfile, "TemporaryDirectory", directory)

    def verify(binary: Path, expected_version: str) -> dict:
        assert binary.resolve().is_relative_to(tmp_path)
        assert expected_version == "0.1.0"
        return {"passed": 1, "total": 1}

    monkeypatch.setattr(binary_archive_smoke, "verify", verify)
    report = binary_archive_smoke.verify_archive(archive, "0.1.0")
    assert report["manifest_verified"] and report["passed"] == 1


def test_archive_rejects_escape(tmp_path: Path) -> None:
    archive = tmp_path / "escape.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../escape", "untrusted")
    with pytest.raises(ValueError, match="escapes"):
        binary_archive_smoke.verify_archive(archive, "0.1.0")
