"""参考实现和独立规格交叉验证，防止把错误 oracle 或可原生编译输入算模型修复。"""

from __future__ import annotations

import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from bench.dataset import TASKS, Task, cases, fingerprint
from bench.harness import matches

from yescc.adapters import get_adapter
from yescc.fix.engine import attempt_repair

pytestmark = pytest.mark.e2e


def test_fixed_dataset_contract() -> None:
    corpus = cases()
    assert len({c.id for c in corpus}) == 200
    assert Counter(c.category for c in corpus) == {
        "native": 50,
        "low": 50,
        "complex": 30,
        "nl": 40,
        "fun": 30,
    }
    assert len(TASKS) == 40
    assert all(len(task.examples) >= 3 for task in TASKS)
    assert fingerprint() == "d24f6ce70b73f746e932c6b932d6aeb4c2c88bff4ec1724bf7465b1c648a2ab2"


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.name)
def test_reference_matches_predefined_io(task: Task, tmp_path: Path) -> None:
    compiler = shutil.which("gcc")
    if compiler is None:
        pytest.skip("GCC is required")
    source = tmp_path / "reference.c"
    source.write_text(task.reference, encoding="utf-8")
    binary = tmp_path / "reference.exe"
    built = subprocess.run([compiler, str(source), "-o", str(binary)], capture_output=True)
    assert built.returncode == 0, built.stderr.decode("utf-8", "replace")
    for stdin, stdout in task.examples:
        result = subprocess.run(
            [str(binary)],
            input=stdin,
            capture_output=True,
            encoding="utf-8",
            timeout=3,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.split() == stdout.split()


@pytest.mark.parametrize(
    "case", [c for c in cases() if c.category == "complex"], ids=lambda c: c.id
)
def test_complex_cases_really_require_model(case, tmp_path: Path) -> None:
    adapter = get_adapter("c")
    source = tmp_path / "broken.c"
    source.write_text(case.source, encoding="utf-8")
    assert adapter.build(source, [], tmp_path).returncode != 0
    rules = attempt_repair(adapter, case.source, source.name, [], tmp_path, tmp_path)
    assert rules.candidate is None, "规则可修复样本不得混入模型修复基准"


def test_behavior_verdict_rejects_compiled_but_wrong_program() -> None:
    check = next(c for c in cases() if c.id == "nl_sum").checks[0]
    assert not matches(check, (0, "999\n", "", False))
    assert not matches(check, (1, "5\n", "", False))
    assert not matches(check, (0, "5\n", "", True))
