"""Check captured creative stdout against explicit, offline case constraints.

This module never executes a candidate or infers general semantic correctness.
Text comparisons are exact Unicode comparisons, without normalization or display
width assumptions. ``line_sequence`` searches each line once (or fullmatches it)
and compares the single capture from each matching line in order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

_FIELDS = {
    "line_count": {"min", "max", "equals", "include_blank"},
    "contains": {"text"},
    "not_contains": {"text"},
    "line_sequence": {"pattern", "expected", "fullmatch"},
    "symbol_count": {"symbol", "equals"},
    "last_nonempty_line": {"text"},
}


def _string(value: object, field: str, *, nonempty: bool = False) -> None:
    if not isinstance(value, str) or (nonempty and not value):
        raise ValueError(f"{field} must be {'a nonempty' if nonempty else 'a'} string")


def _integer(value: object, field: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")


def _validate_check(check: object) -> None:
    if not isinstance(check, dict):
        raise ValueError("each check must be an object")
    kind = check.get("type")
    if not isinstance(kind, str) or kind not in _FIELDS:
        raise ValueError(f"unknown check type: {kind!r}")
    unknown = check.keys() - (_FIELDS[kind] | {"type", "name"})
    if unknown:
        raise ValueError(f"{kind}: unknown fields: {sorted(unknown)}")
    if "name" in check:
        _string(check["name"], "check.name", nonempty=True)
    if kind == "line_count":
        bounds = check.keys() & {"min", "max", "equals"}
        if not bounds or ("equals" in bounds and len(bounds) > 1):
            raise ValueError("line_count needs min/max or equals, not both")
        for field in bounds:
            _integer(check[field], f"line_count.{field}")
        if "min" in bounds and "max" in bounds and check["min"] > check["max"]:
            raise ValueError("line_count.min exceeds max")
        if "include_blank" in check and type(check["include_blank"]) is not bool:
            raise ValueError("line_count.include_blank must be a boolean")
    elif kind == "line_sequence":
        _string(check.get("pattern"), "line_sequence.pattern", nonempty=True)
        try:
            pattern = re.compile(check["pattern"])
        except re.error as exc:
            raise ValueError(f"invalid line_sequence.pattern: {exc}") from exc
        if pattern.groups != 1:
            raise ValueError("line_sequence.pattern must have exactly one capture group")
        expected = check.get("expected")
        if not isinstance(expected, list) or any(not isinstance(x, str) for x in expected):
            raise ValueError("line_sequence.expected must be a list of strings")
        if "fullmatch" in check and type(check["fullmatch"]) is not bool:
            raise ValueError("line_sequence.fullmatch must be a boolean")
    elif kind == "symbol_count":
        _string(check.get("symbol"), "symbol_count.symbol", nonempty=True)
        _integer(check.get("equals"), "symbol_count.equals")
    else:
        _string(check.get("text"), f"{kind}.text", nonempty=True)


def _validate_case(case: object) -> None:
    if not isinstance(case, dict):
        raise ValueError("each case must be an object")
    for field in ("id", "input", "category"):
        _string(case.get(field), f"case.{field}", nonempty=field != "input")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", case["id"]) is None:
        raise ValueError("case.id must be a safe ASCII filename identifier")
    if not isinstance(case.get("checks"), list):
        raise ValueError("case.checks must be a list (use [] for not_assessed)")
    for check in case["checks"]:
        _validate_check(check)


def _check_output(check: dict[str, Any], text: str, lines: list[str]) -> tuple[bool, str]:
    kind = check["type"]
    if kind == "line_count":
        count = (
            len(lines)
            if check.get("include_blank", True)
            else sum(bool(line.strip()) for line in lines)
        )
        if "equals" in check:
            passed = count == check["equals"]
        else:
            passed = count >= check.get("min", 0) and count <= check.get("max", count)
        return passed, f"line count: {count}"
    if kind in {"contains", "not_contains"}:
        present = check["text"] in text
        passed = present if kind == "contains" else not present
        return passed, f"text present: {present}"
    if kind == "symbol_count":
        count = text.count(check["symbol"])
        return count == check["equals"], f"nonoverlapping symbol count: {count}"
    if kind == "last_nonempty_line":
        last = next((line for line in reversed(lines) if line.strip()), None)
        return last == check["text"], f"last nonempty line: {last!r}"
    pattern = re.compile(check["pattern"])
    match_line = pattern.fullmatch if check.get("fullmatch", False) else pattern.search
    actual = [match.group(1) for line in lines if (match := match_line(line)) is not None]
    return actual == check["expected"], f"captured sequence: {actual!r}"


def evaluate_output(case: dict[str, Any], stdout: bytes) -> dict[str, Any]:
    """Return explicit checks; no checks means ``passed=None``, never semantic pass.

    ``splitlines`` preserves internal blank lines and accepts LF/CRLF; the final
    newline does not create an extra line. Blank means empty or whitespace-only.
    Symbol counts use nonoverlapping exact substrings, including variation selectors.
    """
    _validate_case(case)
    if not isinstance(stdout, bytes):
        raise ValueError("stdout must be raw bytes")
    try:
        text = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        return {
            "utf8_valid": False,
            "checks": [
                {
                    "name": check.get("name", check["type"]),
                    "passed": False,
                    "detail": f"stdout is not valid UTF-8 at byte {exc.start}",
                }
                for check in case["checks"]
            ],
            "passed": False,
            "status": "failed",
        }
    checks = []
    lines = text.splitlines()
    for check in case["checks"]:
        passed, detail = _check_output(check, text, lines)
        checks.append(
            {"name": check.get("name", check["type"]), "passed": passed, "detail": detail}
        )
    overall_passed = all(check["passed"] for check in checks) if checks else None
    return {
        "utf8_valid": True,
        "checks": checks,
        "passed": overall_passed,
        "status": (
            "not_assessed" if overall_passed is None else "passed" if overall_passed else "failed"
        ),
    }


def evaluate_dataset(cases_path: Path, outputs: Path) -> dict[str, Any]:
    """Read a frozen dataset and pre-captured ``{id}.stdout`` files, without execution."""
    raw = cases_path.read_bytes()
    try:
        dataset = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid cases JSON: {exc}") from exc
    cases = dataset.get("cases") if isinstance(dataset, dict) else dataset
    if not isinstance(cases, list):
        raise ValueError("dataset must be a case list or an object containing cases")
    ids: set[str] = set()
    for case in cases:
        _validate_case(case)
        if case["id"] in ids:
            raise ValueError(f"duplicate case id: {case['id']}")
        ids.add(case["id"])
    results = []
    for case in cases:
        path = outputs / f"{case['id']}.stdout"
        try:
            stdout = path.read_bytes()
        except OSError as exc:
            result: dict[str, Any] = {
                "utf8_valid": None,
                "checks": [],
                "passed": False,
                "status": "missing_output",
                "error": str(exc),
            }
        else:
            result = evaluate_output(case, stdout)
            result["stdout_sha256"] = hashlib.sha256(stdout).hexdigest()
            result["stdout_bytes"] = len(stdout)
        results.append(
            {
                "id": case["id"],
                "input": case["input"],
                "category": case["category"],
                "configured_checks": len(case["checks"]),
                **result,
            }
        )
    summary = {
        "total": len(results),
        "passed": sum(result["passed"] is True for result in results),
        "failed": sum(result["passed"] is False for result in results),
        "not_assessed": sum(result["status"] == "not_assessed" for result in results),
        "without_semantic_checks": sum(not case["checks"] for case in cases),
        "missing_output": sum(result["status"] == "missing_output" for result in results),
        "invalid_utf8": sum(result["utf8_valid"] is False for result in results),
    }
    return {
        "schema_version": 1,
        "scope": "explicit_offline_output_constraints",
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "input_valid": True,
        "results": results,
        "summary": summary,
    }


def _validate_report_destination(output: Path, protected: list[Path]) -> None:
    """Keep the report separate from the frozen dataset and captured stdout files."""
    destination = output.resolve()
    for path in protected:
        conflict = destination == path.resolve()
        if not conflict:
            # Resolve also protects expected stdout paths that do not yet exist.
            with suppress(FileNotFoundError):
                conflict = output.samefile(path)
        if conflict:
            raise ValueError(f"report output conflicts with protected input: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = evaluate_dataset(args.cases, args.outputs)
        protected = [
            args.cases,
            *(args.outputs / f"{case['id']}.stdout" for case in report["results"]),
        ]
        _validate_report_destination(args.output, protected)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError) as exc:
        print(f"creative consistency: {exc}", file=sys.stderr)
        return 2
    return 1 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
