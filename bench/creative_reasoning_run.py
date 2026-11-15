"""Frozen, bounded research execution; only the explicit run command reads a key."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import runpy
import shlex
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

from bench.creative_reasoning import PROTOCOL, ROOT, preflight

PROTOCOL_SHA = "ddf9733c7b271dd8dbcf97a9b430e5c7cc71e362f794bb895fd7091a4e16fdb9"
RUNNER_FILES = ("bench/creative_reasoning_run.py", "bench/creative_reasoning_guard.py")
OPTIONAL_FILES = (
    "tests/test_creative_reasoning_run.py", "tests/test_creative_reasoning_guard.py",
    "bench/creative_reasoning_review.py", "tests/test_creative_reasoning_review.py",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _phase(tasks: list[dict[str, Any]], cases: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    mapping = {f"{rng.getrandbits(128):032x}": task for task in tasks}
    return {"tasks": tasks, "cases": [{"id": c["id"], "input": c["input"]} for c in cases],
            "review_mapping": mapping}


def prepare(workspace: Path, *, fake_endpoint: str | None = None,
            root: Path = ROOT) -> dict[str, Any]:
    """Create an outside-repository byte snapshot, without configuration or keys."""
    workspace = workspace.resolve()
    if workspace.is_relative_to(root.resolve()):
        raise ValueError("workspace must be outside repository")
    if _sha(root / PROTOCOL) != PROTOCOL_SHA:
        raise ValueError("protocol changed")
    checked = preflight(root)
    protocol = _json(root / PROTOCOL)
    endpoint = protocol["settings"]["base_url"]
    mode = "live"
    if fake_endpoint is not None:
        parsed = urlsplit(fake_endpoint)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path.rstrip("/") != "/v1" or parsed.port is None):
            raise ValueError("fake endpoint must be explicit loopback http://host:port/v1")
        endpoint, mode = fake_endpoint.rstrip("/"), "local_fake"
    workspace.mkdir(parents=True, exist_ok=False)
    snapshot = workspace / "snapshot"
    relative_files = [*protocol["frozen_files"], PROTOCOL, *RUNNER_FILES]
    relative_files.extend(f for f in OPTIONAL_FILES if (root / f).exists())
    manifest = {}
    for relative in dict.fromkeys(relative_files):
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / relative).read_bytes())
        manifest[target.relative_to(workspace).as_posix()] = _sha(target)
    cases = _json(root / protocol["cases_file"])["cases"]
    phase = _phase(protocol["tasks"], cases, 2026111407)
    _write(workspace / "phases/discovery.json", phase)
    _write(workspace / "review-mapping-discovery.json", phase["review_mapping"])
    for relative in ("phases/discovery.json", "review-mapping-discovery.json"):
        manifest[relative] = _sha(workspace / relative)
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                         text=True, check=True)
    freeze = {"schema": "yescc-reasoning-execution-freeze-v1", "files": manifest,
              "protocol_sha256": PROTOCOL_SHA, "product_commit": protocol["product_commit"],
              "tool_base_commit": "3608d20559944d1053d6ea9181937317e684ee59",
              "checkout_commit": git.stdout.strip(), "mode": mode, "endpoint": endpoint,
              "python": sys.version, "planned_tasks": 42,
              "maximum_phase_slots": 58, "maximum_reservation_usd": 0.29,
              "preflight": checked}
    _write(workspace / "execution-freeze.json", freeze)
    return freeze


def verify_workspace(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = workspace.resolve()
    freeze = _json(workspace / "execution-freeze.json")
    if freeze["protocol_sha256"] != PROTOCOL_SHA:
        raise ValueError("protocol identity changed")
    for relative, expected in freeze["files"].items():
        path = workspace / relative
        if not path.resolve().is_relative_to(workspace) or _sha(path) != expected:
            raise ValueError(f"frozen bytes changed: {relative}")
    protocol = _json(workspace / "snapshot" / PROTOCOL)
    if _sha(workspace / "snapshot" / PROTOCOL) != PROTOCOL_SHA:
        raise ValueError("snapshot protocol changed")
    # An orchestrator edited after freezing may not run an older snapshot silently.
    current = Path(__file__).resolve()
    expected_runner = freeze["files"]["snapshot/bench/creative_reasoning_run.py"]
    if _sha(current) != expected_runner:
        raise ValueError("loaded runner differs from frozen runner")
    return protocol, freeze


def _secret(credentials_file: Path) -> str:
    loaded = {}
    for line in credentials_file.read_text(encoding="utf-8").splitlines():
        parts = shlex.split(line, comments=True)
        if parts and parts[0] == "export":
            parts = parts[1:]
        if len(parts) == 1 and "=" in parts[0]:
            key, value = parts[0].split("=", 1)
            loaded[key] = value
    secret = loaded.get("YESCC_MODEL_L3_API_KEY") or loaded.get("YESCC_MODEL_API_KEY")
    if not secret:
        raise ValueError("configured credential missing")
    return str(secret)


def _environment(workspace: Path, directory: Path, settings: dict[str, Any],
                 endpoint: str, secret: str, expected_model: str | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("YESCC_", "FCOMP_"))
           and not k.upper().endswith("_PROXY") and k not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update({
        "PYTHONPATH": os.pathsep.join([str(workspace / "snapshot/src"),
                                     str(workspace / "snapshot")]),
        "YESCC_MODEL_L3_PROVIDER": "deepseek", "YESCC_MODEL_L3_NAME": settings["model"],
        "YESCC_MODEL_L3_BASE_URL": endpoint, "YESCC_MODEL_L3_API_KEY": secret,
        "YESCC_MODEL_L3_INPUT_PRICE_PER_MTOK": str(settings["input_price_per_mtok"]),
        "YESCC_MODEL_L3_OUTPUT_PRICE_PER_MTOK": str(settings["output_price_per_mtok"]),
        "YESCC_MODEL_L3_TIMEOUT_S": str(settings["request_timeout_s"]),
        "YESCC_RUN_TIMEOUT_S": str(settings["run_timeout_s"]),
        "YESCC_CONFIG_DIR": str(directory / "config"),
        "YESCC_CACHE_DIR": str(directory / "cache"), "YESCC_SUPPRESS_NOTICE": "1",
        "YESCC_EVAL_EXPECTED_MODEL": expected_model or "",
    })
    return env


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    else:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True, check=False)
        process.kill()


def _execute(command: list[str], directory: Path, env: dict[str, str],
             timeout: float = 195) -> tuple[int, bytes, bytes, str | None]:
    process = subprocess.Popen(command, cwd=directory, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=os.name == "posix")
    reason = None
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        reason = "host_timeout" if isinstance(exc, subprocess.TimeoutExpired) else "cancelled"
        _kill_group(process)
        stdout, stderr = process.communicate()
    return process.returncode, stdout, stderr, reason


def _phase_plan(workspace: Path, phase: str) -> dict[str, Any]:
    if phase == "confirmation":
        freeze = _json(workspace / "confirmation-freeze.json")
        if freeze["execution_freeze_sha256"] != _sha(workspace / "execution-freeze.json"):
            raise ValueError("original execution freeze changed")
        for relative, expected in freeze["files"].items():
            if _sha(workspace / relative) != expected:
                raise ValueError("confirmation frozen bytes changed")
    return dict(_json(workspace / f"phases/{phase}.json"))


def run_workspace(workspace: Path, credentials_file: Path | None = None, *,
                  phase: str = "discovery") -> dict[str, Any]:
    """Run each frozen task at most once; uncertainty stops all later tasks."""
    workspace = workspace.resolve()
    protocol, freeze = verify_workspace(workspace)
    plan = _phase_plan(workspace, phase)
    # A batch marker precedes credential access and is never reused for resumption.
    _write(workspace / f"{phase}-started.json", {"phase": phase, "tasks": len(plan["tasks"])})
    secret = "test-key" if freeze["mode"] == "local_fake" else _secret(
        credentials_file or Path.home() / ".config/yescc/model.env",
    )
    settings = protocol["settings"]
    cases = {c["id"]: c for c in plan["cases"]}
    expected_model = None
    if phase == "confirmation":
        expected_model = _json(workspace / "discovery-summary.json")["served_model"]
    outcomes = []
    stopped = None
    ledger = workspace / "reservations.jsonl"
    for task in plan["tasks"]:
        verify_workspace(workspace)
        _phase_plan(workspace, phase)
        slots = len(ledger.read_text().splitlines()) if ledger.exists() else 0
        if slots >= 58 or (slots + 1) * settings["task_budget_usd"] > 0.29 + 1e-12:
            raise ValueError("stage reservation exhausted")
        with ledger.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({"phase": phase, "task_id": task["task_id"],
                                     "reserved_usd": settings["task_budget_usd"]}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        directory = workspace / "runs" / task["task_id"]
        directory.mkdir(parents=True, exist_ok=False)
        case = cases[task["case_id"]]
        source = directory / "input.txt"
        original = case["input"].encode("utf-8")
        source.write_bytes(original)
        env = _environment(workspace, directory, settings, freeze["endpoint"], secret,
                           expected_model)
        _write(directory / "environment.json", {k: v for k, v in env.items()
               if k.startswith(("YESCC_", "PYTHONPATH")) and "API_KEY" not in k})
        command = [sys.executable, "-m", "bench.creative_reasoning_run", "driver",
                   "--workspace", str(workspace), "--phase", phase,
                   "--task-id", task["task_id"]]
        started = time.monotonic()
        try:
            code, stdout, stderr, stopped = _execute(command, directory, env)
        except OSError as exc:
            code, stdout, stderr, stopped = 127, b"", str(exc).encode(), "child_start_failed"
        duration_ms = int((time.monotonic() - started) * 1000)
        privacy_redaction = secret.encode() in stdout + stderr
        stdout = stdout.replace(secret.encode(), b"[REDACTED]")
        stderr = stderr.replace(secret.encode(), b"[REDACTED]")
        (directory / "program.stdout").write_bytes(stdout)
        (directory / "program.stderr").write_bytes(stderr)
        report_path = directory / ".yescc/input/report.json"
        report = _json(report_path) if report_path.exists() else None
        guard_path = directory / "guard-state.json"
        guard = _json(guard_path) if guard_path.exists() else {}
        after = source.read_bytes() if source.exists() else None
        changed = after != original
        if report is not None:
            generated = (report.get("result") or {}).get("generated") or {}
            if generated.get("source"):
                generated_path = directory / generated["source"]
                if generated_path.resolve().is_relative_to(directory) and generated_path.exists():
                    (directory / "generated.c").write_bytes(generated_path.read_bytes())
        stopped = stopped or guard.get("halted_reason")
        if privacy_redaction:
            stopped = stopped or "credential_in_output"
        if changed:
            stopped = stopped or "input_changed"
        if not guard_path.exists():
            stopped = stopped or "missing_guard_state"
        model = (report or {}).get("model") or {}
        if not model.get("cost_known", False):
            stopped = stopped or "unknown_fees"
        observed = guard.get("served_model")
        if observed:
            if expected_model is not None and expected_model != observed:
                stopped = stopped or "served_model_changed"
            expected_model = expected_model or observed
        outcome = {"task": task, "case": case, "command": command,
                   "exit_code": code, "report": report, "guard": guard,
                   "source_unchanged": not changed, "duration_ms": duration_ms,
                   "input_sha256": hashlib.sha256(original).hexdigest(),
                   "input_after_sha256": hashlib.sha256(after).hexdigest() if after else None,
                   "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                   "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                   "privacy_redaction": privacy_redaction, "stopped_reason": stopped}
        _write(directory / "outcome.json", outcome)
        outcomes.append(outcome)
        print(f"{task['task_id']} http={guard.get('request_count')} "
              f"exit={code} stop={stopped}", flush=True)
        if stopped:
            break
    summary = {"phase": phase, "planned_tasks": len(plan["tasks"]),
               "completed_tasks": len(outcomes), "served_model": expected_model,
               "stopped_reason": stopped, "reservation_slots": len(ledger.read_text().splitlines()),
               "task_budget_usd": settings["task_budget_usd"],
               "outcomes": [o["task"]["task_id"] for o in outcomes]}
    _write(workspace / f"{phase}-summary.json", summary)
    return summary


def driver(workspace: Path, phase: str, task_id: str) -> int:
    """Internal snapshot child: observe actual HTTP without modifying payloads."""
    import httpx
    from bench.creative_reasoning_guard import HTTPGuard

    import yescc
    from yescc.cli import main as cli
    from yescc.model.config import DEFAULT_BASE_URLS

    workspace = workspace.resolve()
    protocol, freeze = verify_workspace(workspace)
    if not Path(yescc.__file__).resolve().is_relative_to(workspace / "snapshot/src"):
        raise ValueError("product import escaped snapshot")
    if any(k.upper().endswith("_PROXY") for k in os.environ):
        raise ValueError("proxy inheritance forbidden in research driver")
    plan = _phase_plan(workspace, phase)
    task = next(t for t in plan["tasks"] if t["task_id"] == task_id)
    profile = next(p for p in protocol["profiles"] if p["id"] == task["profile"])
    directory = workspace / "runs" / task_id
    secret = os.environ["YESCC_MODEL_L3_API_KEY"]
    endpoint = freeze["endpoint"]
    if freeze["mode"] == "local_fake":
        parsed = urlsplit(endpoint)
        if parsed.hostname not in {"127.0.0.1", "::1"}:
            raise ValueError("fake endpoint escaped loopback")
        DEFAULT_BASE_URLS["deepseek"] = endpoint
    if DEFAULT_BASE_URLS["deepseek"].rstrip("/") != endpoint:
        raise ValueError("provider registry does not match frozen endpoint")

    def record(event: dict[str, Any]) -> None:
        raw = json.dumps(event, ensure_ascii=False)
        if secret in raw:
            raise ValueError("credential in evidence")
        with (directory / "provider-events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(raw + "\n")

    guard = HTTPGuard(
        endpoint=endpoint + "/chat/completions", requested_model=protocol["settings"]["model"],
        profile=profile, record=record,
        expected_model=os.environ.get("YESCC_EVAL_EXPECTED_MODEL") or None,
        max_requests=protocol["settings"]["max_rounds"], secrets=(secret,),
    )
    original_send = httpx.Client.send
    settings = protocol["settings"]
    arguments = ["run", "input.txt", "--play", "--provider", "deepseek", "--model",
                 settings["model"], "--no-cache", "--budget", str(settings["task_budget_usd"]),
                 "--max-rounds", str(settings["max_rounds"]), *profile["cli_arguments"]]
    _write(directory / "cli-arguments.json", arguments)
    with patch("httpx.Client.send", new=guard.wrap_send(original_send)):
        try:
            return cli(arguments)
        finally:
            _write(directory / "guard-state.json", {
                "halted_reason": guard.halted_reason, "served_model": guard.served_model,
                "request_count": guard.request_count,
            })


def prepare_confirmation(workspace: Path, selection_file: Path) -> dict[str, Any]:
    """Open the sealed inputs only after completed discovery and a qualified winner."""
    protocol, _ = verify_workspace(workspace)
    decision = _json(selection_file)
    winner = decision.get("selected_profile")
    gates = decision.get("gates", {}).get(winner, {})
    if winner not in {"tokens", "low"} or not gates or not all(v is True for v in gates.values()):
        raise ValueError("no uniquely qualified selected profile")
    discovery = _json(workspace / "discovery-summary.json")
    if discovery["completed_tasks"] != 42 or discovery["stopped_reason"] is not None:
        raise ValueError("discovery incomplete")
    for task in protocol["tasks"]:
        if not (workspace / "runs" / task["task_id"] / "outcome.json").is_file():
            raise ValueError("missing discovery outcome")
    for suffix in ("a", "b"):
        if not (workspace / f"reviews/discovery-{suffix}.json").is_file():
            raise ValueError("missing independent discovery review")
    review_tool = workspace / "snapshot/bench/creative_reasoning_review.py"
    if not review_tool.is_file():
        raise ValueError("frozen review analyzer missing")
    computed = runpy.run_path(str(review_tool))["analyze"](workspace, "discovery")
    if decision != computed:
        raise ValueError("selection differs from frozen analysis")
    # The checks above deliberately precede reading or hashing the sealed body.
    sealed = Path(protocol["confirmation"]["sealed_path_outside_repository"])
    if _sha(sealed) != protocol["confirmation"]["sealed_sha256"]:
        raise ValueError("sealed input bytes changed")
    raw = _json(sealed)
    cases = raw["cases"] if isinstance(raw, dict) else raw
    if len(cases) != 8 or len({c["id"] for c in cases}) != 8:
        raise ValueError("expected eight sealed inputs")
    tasks = [{"task_id": f"{c['id']}__{p}__confirm", "case_id": c["id"],
              "profile": p, "repeat": 1} for c in cases for p in ("default", winner)]
    random.Random(2026111408).shuffle(tasks)
    phase = _phase(tasks, cases, 2026111409)
    _write(workspace / "phases/confirmation.json", phase)
    _write(workspace / "review-mapping-confirmation.json", phase["review_mapping"])
    _write(workspace / "selection-frozen.json", decision)
    manifest = {relative: _sha(workspace / relative) for relative in (
        "phases/confirmation.json", "review-mapping-confirmation.json", "selection-frozen.json",
        "reviews/discovery-a.json", "reviews/discovery-b.json",
        "discovery-material-manifest.json",
    )}
    freeze = {"execution_freeze_sha256": _sha(workspace / "execution-freeze.json"),
              "files": manifest, "maximum_tasks": 16, "selected_profile": winner}
    _write(workspace / "confirmation-freeze.json", freeze)
    return freeze


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--workspace", type=Path, required=True)
    prep.add_argument("--fake-endpoint")
    run = sub.add_parser("run")
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument("--credentials-file", type=Path)
    run.add_argument("--phase", choices=("discovery", "confirmation"), default="discovery")
    confirm = sub.add_parser("confirmation")
    confirm.add_argument("--workspace", type=Path, required=True)
    confirm.add_argument("--selection-file", type=Path, required=True)
    child = sub.add_parser("driver")
    child.add_argument("--workspace", type=Path, required=True)
    child.add_argument("--phase", choices=("discovery", "confirmation"), required=True)
    child.add_argument("--task-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare(args.workspace, fake_endpoint=args.fake_endpoint)
        print(f"Frozen {len(result['files'])} files; no credential read or model call.")
    elif args.command == "run":
        result = run_workspace(args.workspace, args.credentials_file, phase=args.phase)
        return 1 if result["stopped_reason"] else 0
    elif args.command == "confirmation":
        prepare_confirmation(args.workspace, args.selection_file)
    else:
        return driver(args.workspace, args.phase, args.task_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
