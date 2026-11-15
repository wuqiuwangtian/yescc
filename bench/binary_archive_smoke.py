"""Extract the candidate archive outside the repository and verify its CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
import tempfile
import zipfile
from pathlib import Path

from bench.binary_smoke import verify


def verify_archive(archive: Path, expected_version: str) -> dict:
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
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify_archive(args.archive.resolve(), args.expected_version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}))
    raise SystemExit(0 if result["passed"] == result["total"] else 1)


if __name__ == "__main__":
    main()
