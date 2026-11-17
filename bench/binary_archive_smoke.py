"""Extract the candidate archive outside the repository and verify its CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tarfile
import tempfile
import zipfile
from pathlib import Path

from bench.binary_smoke import verify


def verify_release_hashes(
    archive: Path, expected_version: str, checksums: Path, proposal: Path
) -> dict:
    """Bind downloaded bytes to both the release checksums and reviewed proposal."""
    approved = json.loads(proposal.read_text("utf-8"))
    if approved["version"] != expected_version:
        raise ValueError("Candidate proposal version does not match requested version")
    records = {}
    for line in checksums.read_text("utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest) or not name:
            raise ValueError("Malformed release checksum record")
        if name in records:
            raise ValueError("Duplicate release checksum record")
        records[name] = digest
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if records.get(archive.name) != actual:
        raise ValueError("Archive does not match release SHA256SUMS")
    if approved["assets"].get(archive.name) != actual:
        raise ValueError("Archive does not match reviewed candidate proposal")
    return {"sha256": actual, "source_commit": approved["source_commit"]}


def verify_archive(
    archive: Path,
    expected_version: str,
    *,
    checksums: Path | None = None,
    proposal: Path | None = None,
) -> dict:
    if (checksums is None) != (proposal is None):
        raise ValueError("Release checksums and candidate proposal must be supplied together")
    approval = (
        verify_release_hashes(archive, expected_version, checksums, proposal)
        if checksums is not None and proposal is not None
        else None
    )
    with tempfile.TemporaryDirectory(prefix="yescc-native-install-") as raw:
        # Windows TEMP may use an 8.3 alias; compare canonical paths on both sides.
        root = Path(raw).resolve()
        if archive.name.endswith(".tar.gz"):
            with tarfile.open(archive, "r:gz") as packed:
                packed.extractall(root, filter="data")
        else:
            with zipfile.ZipFile(archive) as packed_zip:
                for name in packed_zip.namelist():
                    if not (root / name).resolve().is_relative_to(root):
                        raise ValueError("Archive entry escapes the installation directory")
                packed_zip.extractall(root)
        folders = [path for path in root.iterdir() if path.is_dir()]
        assert len(folders) == 1
        bundle = folders[0]
        manifest = json.loads((bundle / "MANIFEST.json").read_text("utf-8"))
        assert manifest["version"] == expected_version
        if approval is not None and manifest.get("code_commit") != approval["source_commit"]:
            raise ValueError("Archive source commit does not match reviewed candidate proposal")
        for name, recorded in manifest["files"].items():
            path = bundle / name
            assert path.resolve().is_relative_to(bundle)
            if "symlink" in recorded:
                assert path.is_symlink() and os.readlink(path) == recorded["symlink"]
            else:
                assert hashlib.sha256(path.read_bytes()).hexdigest() == recorded["sha256"], name
        binary = bundle / ("yescc.exe" if os.name == "nt" else "yescc")
        result = verify(binary, expected_version)
        result["archive"] = archive.name
        result["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        result["manifest_verified"] = True
        if approval is not None:
            result["release_checksum_verified"] = True
            result["reviewed_candidate_verified"] = True
            result["source_commit"] = approval["source_commit"]
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--release-checksums", type=Path)
    parser.add_argument("--candidate-proposal", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (args.release_checksums is None) != (args.candidate_proposal is None):
        parser.error("--release-checksums and --candidate-proposal must be supplied together")
    result = verify_archive(
        args.archive.resolve(),
        args.expected_version,
        checksums=args.release_checksums,
        proposal=args.candidate_proposal,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}))
    raise SystemExit(0 if result["passed"] == result["total"] else 1)


if __name__ == "__main__":
    main()
