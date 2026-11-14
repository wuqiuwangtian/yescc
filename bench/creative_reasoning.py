"""Offline preflight for the frozen creative-generation settings comparison.

This command only checks bytes and computes first-request reservations using the
bound product implementation. It never reads credentials, calls a model, runs C,
opens the sealed confirmation set, or starts the future paid experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from yescc.model.config import ModelSettings, resolve_controls
from yescc.model.openai_compat import request_parameters
from yescc.model.prompts import PROMPT_VERSION, generate_messages
from yescc.model.session import ModelSession

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "bench/creative_reasoning_protocol.json"


def preflight(root: Path = ROOT) -> dict[str, Any]:
    """Verify frozen inputs/source and emit plans, not paid requests."""
    protocol = json.loads((root / PROTOCOL).read_text(encoding="utf-8"))
    for relative, expected in protocol["frozen_files"].items():
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"frozen file changed: {relative}")
    if protocol["product_prompt_version"] != PROMPT_VERSION:
        raise ValueError("product prompt version changed")
    cases = json.loads((root / protocol["cases_file"]).read_text(encoding="utf-8"))["cases"]
    by_id = {item["id"]: item for item in cases}
    if len(cases) != 12 or len(by_id) != 12:
        raise ValueError("expected twelve unique discovery inputs")
    settings = protocol["settings"]
    profiles = {item["id"]: item for item in protocol["profiles"]}
    task_budget = settings["task_budget_usd"]
    tasks = protocol["tasks"]
    if len(tasks) != 42 or len({item["task_id"] for item in tasks}) != 42:
        raise ValueError("expected 42 unique discovery tasks")
    expected_pairs = {
        (case_id, profile_id, repeat)
        for case_id in by_id
        for profile_id in profiles
        for repeat in ([1, 2] if case_id in protocol["repeat_inputs"] else [1])
    }
    actual_pairs = {(t["case_id"], t["profile"], t["repeat"]) for t in tasks}
    if actual_pairs != expected_pairs:
        raise ValueError("unbalanced tasks or changed repeats")
    total = (len(tasks) + protocol["confirmation"]["maximum_tasks"]) * task_budget
    if total > settings["maximum_total_reservation_usd"]:
        raise ValueError("planned tasks exceed stage reservation ceiling")
    plans = []
    for task in tasks:
        profile = profiles[task["profile"]]
        configured = ModelSettings(
            provider="deepseek", base_url="http://127.0.0.1:9/v1",
            model=settings["model"], api_key="unused-offline-placeholder",
            timeout_s=settings["request_timeout_s"],
            input_price_per_mtok=settings["input_price_per_mtok"],
            output_price_per_mtok=settings["output_price_per_mtok"],
            thinking=profile["thinking"], reasoning_effort=profile["reasoning_effort"],
            max_output_tokens=profile["max_output_tokens"],
        )
        # Avoid all environment/config/credential loading, even with user settings.
        with patch("yescc.model.session.load_settings", return_value=configured):
            session = ModelSession(
                offline=True, no_cache=True, budget_usd=task_budget,
                max_rounds=settings["max_rounds"],
            )
        messages = generate_messages(by_id[task["case_id"]]["input"], creative=True)
        max_tokens, reserved, known = session._request_limit(configured, messages)
        if not known or reserved > task_budget:
            raise ValueError("invalid first-request reservation")
        parameters = request_parameters(
            resolve_controls(configured), max_tokens=max_tokens, temperature=0.4,
        )
        encoded = json.dumps(
            [{"role": m.role, "content": m.content} for m in messages], ensure_ascii=False,
        ).encode("utf-8")
        plans.append({
            **task, "first_messages_sha256": hashlib.sha256(encoded).hexdigest(),
            "first_request_parameters": parameters,
            "first_request_reservation_usd": reserved,
            "cli_arguments": [
                "run", "input.txt", "--play", "--provider", "deepseek",
                "--model", settings["model"], "--no-cache", "--budget", str(task_budget),
                "--max-rounds", str(settings["max_rounds"]), *profile["cli_arguments"],
            ],
        })
    return {
        "schema": "yescc-creative-reasoning-preflight-v1",
        "protocol_sha256": hashlib.sha256((root / PROTOCOL).read_bytes()).hexdigest(),
        "source_commit": protocol["product_commit"], "checked_files": len(protocol["frozen_files"]),
        "discovery_tasks": len(tasks), "discovery_reservation_usd": len(tasks) * task_budget,
        "confirmation_maximum_tasks": protocol["confirmation"]["maximum_tasks"],
        "maximum_planned_reservation_usd": round(total, 6),
        "real_model_calls": 0, "candidate_executions": 0, "sealed_inputs_opened": False,
        "note": "First request only; retry allowance uses remaining cost and feedback.",
        "plans": plans,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="new JSON file; existing files are never replaced",
    )
    args = parser.parse_args(argv)
    report = preflight()
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
