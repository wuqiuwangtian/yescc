"""M5 失败审计：真实工具链、本地假供应商，验证失败状态和产物，不花模型费用。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from tests.conftest import FakeModelServer
from tests.fixtures.failure_cases import CASES, FailureCase


def audit_case(case: FailureCase, directory: Path, endpoint: str) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / ("audit_" + case.name + case.suffix)
    payload = case.source.encode("utf-8") if isinstance(case.source, str) else case.source
    source.write_bytes(payload)
    before = hashlib.sha256(payload).hexdigest()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("YESCC_MODEL_", "FCOMP_MODEL_"))
    }
    env.update(
        {
            "YESCC_MODEL_L3_BASE_URL": endpoint,
            "YESCC_MODEL_L3_NAME": "audit-fake",
            "YESCC_MODEL_L3_API_KEY": "test-key",
            "YESCC_MODEL_L3_INPUT_PRICE_PER_MTOK": "1",
            "YESCC_MODEL_L3_OUTPUT_PRICE_PER_MTOK": "1",
            "YESCC_SUPPRESS_NOTICE": "1",
            "YESCC_CONFIG_DIR": str(directory / "config"),
            "YESCC_CACHE_DIR": str(directory / "cache"),
            "LC_ALL": "C",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    if case.condition == "missing_compiler":
        env["PATH"] = ""
    completed = subprocess.run(
        [sys.executable, "-m", "yescc", str(source), "--json", *case.flags],
        cwd=directory,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    report_path = directory / ".yescc" / source.stem / "report.json"
    report = json.loads(report_path.read_text("utf-8")) if report_path.exists() else None
    status = report["result"]["status"] if report else None
    artifact = report["result"]["artifact"] if report else None
    unchanged = hashlib.sha256(source.read_bytes()).hexdigest() == before
    stdout_matches = (
        json.loads(completed.stdout) == report if report else not completed.stdout.strip()
    )
    passed = (
        completed.returncode == case.exit_code
        and status == case.status
        and artifact is None
        and unchanged
        and stdout_matches
    )
    return {
        "id": case.name,
        "exit_code": completed.returncode,
        "status": status,
        "artifact": artifact,
        "source_unchanged": unchanged,
        "report_exists": report is not None,
        "stdout_matches_report": stdout_matches,
        "passed": passed,
        "model_calls": (report.get("model") or {}).get("calls", 0) if report else 0,
        "stderr": completed.stderr[:500],
    }


def fingerprint() -> str:
    serializable = []
    for case in CASES:
        row = asdict(case)
        if isinstance(row["source"], bytes):
            row["source"] = {"bytes_hex": row["source"].hex()}
        serializable.append(row)
    return hashlib.sha256(json.dumps(serializable, sort_keys=True).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    server = FakeModelServer()
    server.start()
    try:
        with tempfile.TemporaryDirectory(prefix="yescc-failure-audit-") as raw:
            for case in CASES:
                server.responses.clear()
                if case.response is not None:
                    server.responses.append(case.response)
                row = audit_case(case, Path(raw) / case.name, server.base_url)
                rows.append(row)
                print(case.name, row["passed"], row["exit_code"], row["status"], flush=True)
    finally:
        server.stop()
    output = {
        "mode": "local-fake-provider",
        "platform": platform.system(),
        "python": platform.python_version(),
        "dataset_sha256": fingerprint(),
        "total": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return int(not all(row["passed"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
