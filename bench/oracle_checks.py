"""M5 第二实现交叉验证；不改固定 200 例，不调用模型，也不冒充人类复核。"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import math
import platform
import random
import shutil
import statistics
import subprocess
import tempfile
from functools import cache
from pathlib import Path

from bench.dataset import TASKS, Task, fingerprint

EXACT = {"uppercase", "lowercase", "reverse_text"}
PAIR = {"sum", "difference", "product", "maximum", "minimum", "gcd", "lcm", "power"}
ARRAY = {
    "array_sum",
    "array_max",
    "array_min",
    "positive_count",
    "even_count",
    "linear_search",
    "reverse_array",
    "sort_array",
    "unique_count",
    "second_largest",
}
TEXT = {"text_length", "uppercase", "lowercase", "vowel_count", "text_palindrome", "reverse_text"}


@cache
def fibonacci(n: int) -> int:
    return n if n < 2 else fibonacci(n - 1) + fibonacci(n - 2)


def evaluate(name: str, stdin: str) -> str:
    """按规格使用 Python 内建/标准库计算，不解析或执行 C 参考实现。"""
    if name in TEXT:
        text = stdin.rstrip("\r\n")
        if name == "text_length":
            return str(len(text))
        if name == "uppercase":
            return text.upper()
        if name == "lowercase":
            return text.lower()
        if name == "vowel_count":
            return str(sum(char.lower() in "aeiou" for char in text))
        if name == "text_palindrome":
            return str(int(text == text[::-1]))
        return text[::-1]
    values = list(map(int, stdin.split()))
    if name in ARRAY:
        n = values[0]
        items = values[1 : 1 + n]
        if name == "array_sum":
            return str(sum(items))
        if name == "array_max":
            return str(max(items))
        if name == "array_min":
            return str(min(items))
        if name == "positive_count":
            return str(sum(value > 0 for value in items))
        if name == "even_count":
            return str(sum(value % 2 == 0 for value in items))
        if name == "linear_search":
            target = values[1 + n]
            return str(items.index(target) if target in items else -1)
        if name == "reverse_array":
            return " ".join(map(str, reversed(items)))
        if name == "sort_array":
            return " ".join(map(str, sorted(items)))
        if name == "unique_count":
            return str(len(set(items)))
        distinct = sorted(set(items))
        return str(distinct[-2]) if len(distinct) > 1 else "NONE"
    if name in PAIR:
        a, b = values
        operations = {
            "sum": lambda: a + b,
            "difference": lambda: a - b,
            "product": lambda: a * b,
            "maximum": lambda: max(a, b),
            "minimum": lambda: min(a, b),
            "gcd": lambda: math.gcd(a, b),
            "lcm": lambda: math.lcm(a, b),
            "power": lambda: a**b,
        }
        return str(operations[name]())
    if name == "triangle":
        a, b, c = sorted(values)
        return "YES" if a > 0 and a + b > c else "NO"
    if name == "median_three":
        return str(int(statistics.median(values)))
    if name == "sort_three":
        return " ".join(map(str, sorted(values)))
    n = values[0]
    operations = {
        "absolute": lambda: abs(n),
        "sign": lambda: (n > 0) - (n < 0),
        "parity": lambda: "ODD" if n % 2 else "EVEN",
        "sum_to_n": lambda: sum(range(n + 1)),
        "factorial": lambda: math.factorial(n),
        "fibonacci": lambda: fibonacci(n),
        "digit_sum": lambda: sum(map(int, str(abs(n)))),
        "digit_count": lambda: len(str(abs(n))),
        "reverse_number": lambda: int(str(n)[::-1]),
        "palindrome_number": lambda: int(str(n) == str(n)[::-1]),
        "prime": lambda: int(n >= 2 and all(n % d for d in range(2, math.isqrt(n) + 1))),
        "divisor_count": lambda: (
            2 * sum(n % d == 0 for d in range(1, math.isqrt(n) + 1)) - (math.isqrt(n) ** 2 == n)
        ),
        "leap_year": lambda: int(calendar.isleap(n)),
    }
    return str(operations[name]())


def probes(task: Task) -> tuple[str, ...]:
    """先给定固定样本，再用固定 seed 扩展，不读取被测模型输出。"""
    result = [stdin for stdin, _ in task.examples]
    rng = random.Random(int.from_bytes(hashlib.sha256(task.name.encode()).digest()[:8]))
    for _ in range(10):
        name = task.name
        if name in TEXT:
            text = "".join(rng.choice("aAzZeEiIoOuU 0129!?-_") for _ in range(rng.randrange(0, 40)))
            result.append(text + "\n")
        elif name in ARRAY:
            n = rng.randrange(1 if name in {"array_max", "array_min"} else 0, 15)
            items = [rng.randrange(-8, 9) for _ in range(n)]
            tokens = [n, *items]
            if name == "linear_search":
                tokens.append(rng.choice([*items, 99]))
            result.append(" ".join(map(str, tokens)) + "\n")
        elif name in PAIR:
            a, b = rng.randrange(-1000, 1001), rng.randrange(-1000, 1001)
            if name in {"gcd", "lcm"}:
                a, b = abs(a), abs(b)
            if name == "power":
                a, b = rng.randrange(-5, 6), rng.randrange(0, 9)
            result.append(f"{a} {b}\n")
        elif name in {"median_three", "sort_three", "triangle"}:
            result.append(" ".join(str(rng.randrange(-5, 20)) for _ in range(3)) + "\n")
        else:
            bounds = {
                "factorial": (0, 21),
                "fibonacci": (0, 41),
                "sum_to_n": (0, 10001),
                "prime": (-10, 1000001),
                "divisor_count": (1, 10001),
                "leap_year": (1, 10000),
                "reverse_number": (0, 1000000001),
                "palindrome_number": (0, 1000000001),
            }
            lo, hi = bounds.get(name, (-1000000000, 1000000001))
            result.append(f"{rng.randrange(lo, hi)}\n")
    return tuple(dict.fromkeys(result))


def compare(name: str, actual: str, expected: str) -> bool:
    return actual == expected + "\n" if name in EXACT else actual.split() == expected.split()


def verify(task: Task, directory: Path) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which("gcc")
    if compiler is None:
        raise RuntimeError("GCC is required")
    source = directory / "reference.c"
    source.write_text(task.reference, encoding="utf-8")
    binary = directory / "reference.exe"
    built = subprocess.run(
        [compiler, str(source), "-o", str(binary)], capture_output=True, timeout=30
    )
    if built.returncode:
        raise RuntimeError(built.stderr.decode("utf-8", "replace"))
    checks = []
    for stdin in probes(task):
        expected = evaluate(task.name, stdin)
        actual = subprocess.run(
            [str(binary)],
            input=stdin,
            capture_output=True,
            encoding="utf-8",
            timeout=3,
            check=False,
        )
        checks.append(
            {
                "stdin": stdin,
                "expected": expected,
                "actual": actual.stdout,
                "passed": actual.returncode == 0 and compare(task.name, actual.stdout, expected),
            }
        )
    fixed_correct = all(
        evaluate(task.name, stdin).split() == expected.split() for stdin, expected in task.examples
    )
    return {
        "id": task.name,
        "fixed_oracle_correct": fixed_correct,
        "checks": checks,
        "passed": fixed_correct and all(check["passed"] for check in checks),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="yescc-oracle-") as raw:
        rows = [verify(task, Path(raw) / task.name) for task in TASKS]
    output = {
        "mode": "independent-python-oracle",
        "human_reviewed": False,
        "dataset_sha256": fingerprint(),
        "platform": platform.system(),
        "python": platform.python_version(),
        "tasks": len(rows),
        "checks": sum(len(row["checks"]) for row in rows),
        "passed": sum(row["passed"] for row in rows),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in output.items() if k != "rows"}, ensure_ascii=False))
    return int(not all(row["passed"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
