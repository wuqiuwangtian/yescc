"""Restore only the historical research snapshot; never execute it or read keys."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "docs/评测/data/creative-reasoning-generation-2026-11-14.json.gz"
ARCHIVE_SHA = "71cf4861396c1f6dc89cd7b59a681385d269e8f344c2a6074141691fbf317caf"
PROTOCOL_SHA = "ddf9733c7b271dd8dbcf97a9b430e5c7cc71e362f794bb895fd7091a4e16fdb9"


def restore_snapshot(destination: Path, *, archive: Path = ARCHIVE) -> Path:
    """Verify the immutable archive, then create a new outside-repo snapshot root."""
    destination = destination.resolve()
    if destination.is_relative_to(ROOT):
        raise ValueError("historical snapshot must be outside repository")
    raw = archive.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA:
        raise ValueError("historical evidence archive changed")
    data = json.loads(gzip.decompress(raw))
    files: dict[str, bytes] = {}
    for entry in data["files"]:
        if not entry["path"].startswith("snapshot/"):
            continue
        member = PurePosixPath(entry["path"]).relative_to("snapshot")
        if member.is_absolute() or ".." in member.parts:
            raise ValueError("invalid snapshot archive path")
        contents = base64.b64decode(entry["base64"], validate=True)
        if (len(contents) != entry["bytes"]
                or hashlib.sha256(contents).hexdigest() != entry["sha256"]):
            raise ValueError("snapshot archive bytes changed")
        if str(member) in files:
            raise ValueError("duplicate snapshot archive path")
        files[str(member)] = contents
    if len(files) != 47:
        raise ValueError("expected exactly 47 original snapshot files")
    protocol_raw = files["bench/creative_reasoning_protocol.json"]
    if hashlib.sha256(protocol_raw).hexdigest() != PROTOCOL_SHA:
        raise ValueError("historical protocol changed")
    protocol = json.loads(protocol_raw)
    for frozen_name, digest in protocol["frozen_files"].items():
        if hashlib.sha256(files[frozen_name]).hexdigest() != digest:
            raise ValueError(f"historical frozen bytes changed: {frozen_name}")
    destination.mkdir(parents=True, exist_ok=False)
    for relative, contents in files.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    restore_snapshot(args.destination)
    print("Restored 47 original snapshot files; no model, compiler or credential access.")


if __name__ == "__main__":
    main()
