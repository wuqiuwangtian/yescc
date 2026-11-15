"""Preflight the historical protocol with its explicitly bound product snapshot.

The archived protocol is not retargeted to evolving production code. Restore its
snapshot with bench.creative_reasoning_archive, then pass --source-root. This
only starts an isolated Python calculation; no compiler, C or model is executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from bench.creative_reasoning_archive import PROTOCOL_SHA

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "bench/creative_reasoning_protocol.json"


def preflight(root: Path = ROOT) -> dict[str, Any]:
    """Validate original bytes before calculating with the bound historical Python."""
    root = root.resolve()
    protocol_raw = (root / PROTOCOL).read_bytes()
    if hashlib.sha256(protocol_raw).hexdigest() != PROTOCOL_SHA:
        raise ValueError("historical protocol changed")
    protocol = json.loads(protocol_raw)
    for relative, expected in protocol["frozen_files"].items():
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("invalid frozen file path")
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(
                f"frozen file changed: {relative}; use an explicitly restored --source-root"
            )
    # -I ignores ambient PYTHONPATH and the working directory. The isolated child
    # imports the original yescc before running the hash-verified original helper.
    code = """
import pathlib, runpy, sys
root = pathlib.Path(sys.argv[1])
def offline_only(event, args):
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.posix_spawn"}:
        raise RuntimeError("historical preflight forbids network and process execution")
sys.addaudithook(offline_only)
sys.path.insert(0, str(root / "src"))
import yescc
assert pathlib.Path(yescc.__file__).resolve().is_relative_to(root / "src")
helper = root / "bench/creative_reasoning.py"
sys.argv = [str(helper)]
runpy.run_path(str(helper), run_name="__main__")
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("YESCC_", "FCOMP_"))
           and not k.upper().endswith("_PROXY")}
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(root)], cwd=root,
        env=env, capture_output=True, timeout=30, check=False,
    )
    if result.returncode:
        raise ValueError("historical preflight rejected: " + result.stderr.decode("utf-8"))
    report: dict[str, Any] = json.loads(result.stdout)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="new file; existing files are never replaced")
    args = parser.parse_args(argv)
    report = preflight(args.source_root)
    raw = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(raw, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
