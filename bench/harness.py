"""固定 M4 基准执行器：离线可重复，真实模型须显式 --models。

uv run python -m bench.harness --output /tmp/m4-offline.json
uv run python -m bench.harness --models --warm --output /tmp/m4-models.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from bench.dataset import VERSION, Case, Check, cases, fingerprint

from yescc.model.config import load_settings

_WRAPPER = (
    "import sys; from pathlib import Path; from yescc.sandbox import run_ai_command; "
    "r=run_ai_command(sys.argv[1:],unsafe=False,cwd=Path.cwd()); sys.exit(r.exit_code)"
)


def capture(command: list[str], cwd: Path, stdin: str = "") -> tuple[int, str, str, bool]:
    """程序输出先落临时文件，限制读取量，避免大输出耗尽评测器内存。"""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                input=stdin.encode(),
                stdout=out,
                stderr=err,
                timeout=15,
                check=False,
            )
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
        out.seek(0, 2)
        err.seek(0, 2)
        oversized = out.tell() > 1_048_576 or err.tell() > 1_048_576
        out.seek(0)
        err.seek(0)
        return (
            code,
            out.read(1_048_576).decode("utf-8", "replace"),
            err.read(1_048_576).decode("utf-8", "replace"),
            oversized,
        )


def materialize(case: Case, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in case.files.items():
        (directory / name).write_text(text, encoding="utf-8")
    suffix = (
        ".txt"
        if case.category in {"nl", "fun"}
        else {"c": ".c", "python": ".py", "javascript": ".js"}[case.lang]
    )
    source = directory / (case.id + suffix)
    source.write_text(case.source, encoding="utf-8")
    return source


def native_command(case: Case, source: Path, directory: Path) -> list[str]:
    if case.lang == "python":
        return [sys.executable, str(source)]
    if case.lang == "javascript":
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("Node.js is required")
        return [node, str(source)]
    compiler = shutil.which("gcc")
    if compiler is None:
        raise RuntimeError("GCC is required")
    binary = directory / ("reference.exe" if os.name == "nt" else "reference")
    built = subprocess.run(
        [compiler, str(source), "-o", str(binary)],
        cwd=directory,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if built.returncode:
        raise RuntimeError(built.stderr.decode("utf-8", "replace")[:1000])
    return [str(binary)]


def matches(check: Check, result: tuple[int, str, str, bool]) -> bool:
    code, stdout, _, oversized = result
    if code != check.exit_code or oversized:
        return False
    if check.stdout is None:
        return bool(stdout.strip())
    if check.comparison == "exact":
        return stdout == check.stdout
    return stdout.split() == check.stdout.split()


def run_case(case: Case, root: Path, models: bool, budget: float) -> dict[str, object]:
    directory = root / case.id / "actual"
    source = materialize(case, directory)
    initial_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    flags = ["--json", "--budget", str(budget)]
    if not models or case.category in {"native", "low"}:
        flags.append("--offline")
    if case.category == "fun":
        flags.append("--play")
    if case.category == "complex":
        flags.append("--no-fallback")
    started = time.perf_counter()
    build = subprocess.run(
        [sys.executable, "-m", "yescc", str(source), *flags],
        cwd=directory,
        capture_output=True,
        timeout=190,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    try:
        report = json.loads(build.stdout)
    except ValueError:
        return {
            "id": case.id,
            "category": case.category,
            "passed": False,
            "error": build.stderr[:500],
            "build_exit": build.returncode,
        }
    result = report["result"]
    expected = {
        "native": "native_ok",
        "low": "repaired",
        "complex": "repaired",
        "nl": "generated",
        "fun": "creative_fallback",
    }[case.category]
    correct = build.returncode == 0 and result["status"] == expected
    checks: list[dict[str, object]] = []
    if correct:
        if case.lang == "c":
            artifact = directory / (result["artifact"] or "__missing__")
            correct = artifact.is_file()
            command = [str(artifact)]
        elif case.lang == "python":
            command = [sys.executable, str(source)]
        else:
            command = [shutil.which("node") or "node", str(source)]
        if case.category != "native":
            command = [sys.executable, "-c", _WRAPPER, *command]
        reference_command: list[str] = []
        if case.category == "native":
            reference_dir = root / case.id / "reference"
            reference_source = materialize(case, reference_dir)
            reference_command = native_command(case, reference_source, reference_dir)
        for check in case.checks if correct else ():
            actual = capture([*command, *check.args], directory, check.stdin)
            if reference_command:
                expected_run = capture(
                    [*reference_command, *check.args], reference_dir, check.stdin
                )
                passed = actual == expected_run
            else:
                passed = matches(check, actual)
            checks.append(
                {
                    "passed": passed,
                    "exit_code": actual[0],
                    "stdout": actual[1][:250],
                    "oversized": actual[3],
                }
            )
            correct = correct and passed
    unchanged = hashlib.sha256(source.read_bytes()).hexdigest() == initial_hash
    model = report.get("model") or {}
    attempts = report.get("budget", {}).get("attempts", 0)
    successful_model_build = any(
        attempt["kind"] == "model" and attempt["exit_code"] == 0
        for attempt in report["attempts"]
    )
    fallback_kind = (result.get("fallback") or {}).get("kind")
    model_origin = (
        fallback_kind == "model_creative"
        if case.category == "fun"
        else (
            result["status"] == "generated"
            if case.category == "nl"
            else "model" in (result.get("repair") or {}).get("rules", [])
        )
    )
    model_success = bool(correct and unchanged and model_origin and successful_model_build)
    if case.category == "native":
        correct = correct and model.get("calls", 0) == 0
    return {
        "id": case.id,
        "category": case.category,
        "passed": correct and unchanged,
        "status": result["status"],
        "source_unchanged": unchanged,
        "checks": checks,
        "calls": model.get("calls", 0),
        "cache_hits": model.get("cache_hits", 0),
        "prompt_tokens": model.get("prompt_tokens", 0),
        "completion_tokens": model.get("completion_tokens", 0),
        "cost_usd": model.get("cost_usd", 0),
        "cost_known": model.get("cost_known", True),
        "committed_usd": report.get("budget", {}).get("committed_usd", 0),
        "fallback_kind": fallback_kind,
        "model_success": model_success,
        "first_candidate_correct": bool(model_success and attempts == 1),
        "model_builds": sum(a["kind"] == "model" for a in report["attempts"]),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "budget_blocked": report.get("budget", {}).get("blocked_reason"),
        "model_errors": model.get("errors", []),
        "limits": report["limits"],
    }


def summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for category in ("native", "low", "complex", "nl", "fun"):
        selected = [r for r in rows if r["category"] == category]
        evaluated = [r for r in selected if "passed" in r]
        times = sorted(int(r.get("duration_ms", 0)) for r in evaluated)
        summary[category] = {
            "total": len(selected),
            "evaluated": len(evaluated),
            "correct": sum(r["passed"] for r in evaluated),
            "first_candidate_correct": sum(
                r.get("first_candidate_correct", False) for r in evaluated
            ),
            "model_success": sum(r.get("model_success", False) for r in evaluated),
            "p50_ms": times[(len(times) - 1) // 2] if times else None,
            "p95_ms": times[min(len(times) - 1, int(len(times) * 0.95))] if times else None,
        }
    summary["known_cost_usd"] = round(sum(float(r.get("cost_usd", 0)) for r in rows), 8)
    summary["unknown_cost_cases"] = sum(not r.get("cost_known", True) for r in rows)
    summary["calls"] = sum(int(r.get("calls", 0)) for r in rows)
    return summary


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent.parent
    files = [*root.glob("src/**/*.py"), *root.glob("bench/*.py"), *root.glob("tests/fixtures/*.py")]
    for file in sorted(files):
        digest.update(str(file.relative_to(root)).replace("\\", "/").encode())
        digest.update(file.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", action="store_true")
    parser.add_argument("--warm", action="store_true")
    parser.add_argument("--budget", type=float, default=0.005)
    parser.add_argument("--total-budget", type=float, default=0.50)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (0 < args.budget <= args.total_budget < float("inf")):
        parser.error("budgets must be finite and 0 < task budget <= suite budget")
    settings = load_settings("l3")
    if args.models and settings is None:
        parser.error("--models requires configured L3 model; no API key is printed")
    corpus = cases()
    assert Counter(c.category for c in corpus) == {
        "native": 50,
        "low": 50,
        "complex": 30,
        "nl": 40,
        "fun": 30,
    }
    env_backup = dict(os.environ)
    rows: dict[str, list[dict[str, object]]] = {}
    with tempfile.TemporaryDirectory(prefix="yescc-m4-") as raw:
        root = Path(raw)
        os.environ["YESCC_CACHE_DIR"] = str(root / "cache")
        os.environ["YESCC_CONFIG_DIR"] = str(root / "config")
        os.environ["YESCC_RUN_TIMEOUT_S"] = "2"
        os.environ["PYTHONIOENCODING"] = "utf-8"
        committed = 0.0
        for phase in ["cold", "warm"] if args.warm else ["cold"]:
            rows[phase] = []
            for case in corpus:
                needs_model = case.category in {"complex", "nl"}
                if needs_model and not args.models:
                    row = {
                        "id": case.id,
                        "category": case.category,
                        "not_evaluated": "real model disabled",
                    }
                elif args.models and committed + args.budget > args.total_budget:
                    row = {
                        "id": case.id,
                        "category": case.category,
                        "not_evaluated": "suite budget exhausted",
                    }
                else:
                    try:
                        row = run_case(case, root, args.models, args.budget)
                    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                        row = {
                            "id": case.id,
                            "category": case.category,
                            "passed": False,
                            "error": str(exc)[:500],
                        }
                    committed += float(row.get("committed_usd", 0))
                rows[phase].append(row)
                print(f"{phase} {case.id}: {row.get('passed', 'not_evaluated')}", flush=True)
        os.environ.clear()
        os.environ.update(env_backup)
    payload = {
        "dataset_version": VERSION,
        "dataset_sha256": fingerprint(),
        "source_sha256": source_fingerprint(),
        "platform": platform.system(),
        "python": platform.python_version(),
        "models_enabled": args.models,
        "model": {"provider": settings.provider, "name": settings.model} if args.models else None,
        "task_budget_usd": args.budget,
        "suite_budget_usd": args.total_budget,
        "summary": {phase: summarize(values) for phase, values in rows.items()},
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return int(any(row.get("passed") is False for values in rows.values() for row in values))


if __name__ == "__main__":
    raise SystemExit(main())
