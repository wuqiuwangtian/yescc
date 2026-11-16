"""Candidate extraction checks canonical destinations and still rejects escapes."""

from __future__ import annotations

import hashlib
import json
import os
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


def release_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    archive = tmp_path / "yescc-0.1.0-windows-x86_64.zip"
    binary_name = "yescc.exe" if os.name == "nt" else "yescc"
    content = b"fixture executable"
    manifest = {
        "version": "0.1.0",
        "code_commit": "a" * 40,
        "files": {binary_name: {"sha256": hashlib.sha256(content).hexdigest()}},
    }
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(f"yescc-0.1.0/{binary_name}", content)
        zipped.writestr("yescc-0.1.0/MANIFEST.json", json.dumps(manifest))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksums = tmp_path / "SHA256SUMS"
    checksums.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    proposal = tmp_path / "proposal.json"
    proposal.write_text(
        json.dumps(
            {"version": "0.1.0", "source_commit": "a" * 40, "assets": {archive.name: digest}}
        ),
        encoding="utf-8",
    )
    return archive, checksums, proposal


def test_release_archive_runs_only_reviewed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, checksums, proposal = release_fixture(tmp_path)
    calls = []

    def verify(binary: Path, expected_version: str) -> dict:
        calls.append(binary)
        assert binary.read_bytes() == b"fixture executable" and expected_version == "0.1.0"
        return {"passed": 1, "total": 1}

    monkeypatch.setattr(binary_archive_smoke, "verify", verify)
    report = binary_archive_smoke.verify_archive(
        archive, "0.1.0", checksums=checksums, proposal=proposal
    )
    assert len(calls) == 1
    assert report["release_checksum_verified"] and report["reviewed_candidate_verified"]
    assert report["source_commit"] == "a" * 40


@pytest.mark.parametrize(
    "damage,reason",
    [
        ("archive", "release SHA256SUMS"),
        ("forged_release", "reviewed candidate proposal"),
        ("duplicate", "Duplicate"),
        ("missing", "release SHA256SUMS"),
        ("malformed", "Malformed"),
        ("version", "version"),
        ("source", "source commit"),
        ("checksums_only", "supplied together"),
        ("proposal_only", "supplied together"),
    ],
)
def test_release_archive_rejects_unreviewed_input_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str, reason: str
) -> None:
    archive, checksums, proposal = release_fixture(tmp_path)
    if damage in {"archive", "forged_release"}:
        archive.write_bytes(archive.read_bytes() + b"unreviewed change")
        if damage == "forged_release":
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            checksums.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    elif damage == "duplicate":
        checksums.write_text(checksums.read_text("utf-8") * 2, encoding="utf-8")
    elif damage == "missing":
        checksums.write_text("", encoding="utf-8")
    elif damage == "malformed":
        checksums.write_text("not-a-checksum\n", encoding="utf-8")
    elif damage in {"version", "source"}:
        approved = json.loads(proposal.read_text("utf-8"))
        approved["version" if damage == "version" else "source_commit"] = "different"
        proposal.write_text(json.dumps(approved), encoding="utf-8")

    def never_run(_binary: Path, _expected_version: str) -> dict:
        pytest.fail("Unreviewed release must not execute a binary")

    monkeypatch.setattr(binary_archive_smoke, "verify", never_run)
    with pytest.raises(ValueError, match=reason):
        binary_archive_smoke.verify_archive(
            archive,
            "0.1.0",
            checksums=None if damage == "proposal_only" else checksums,
            proposal=None if damage == "checksums_only" else proposal,
        )
