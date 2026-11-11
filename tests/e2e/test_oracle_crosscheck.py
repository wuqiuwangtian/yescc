"""G5 复核：固定规格由第二种实现求值，参考 C 程序接受更多事先给定输入。"""

from __future__ import annotations

from pathlib import Path

import pytest
from bench.dataset import TASKS, Task
from bench.oracle_checks import verify

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.name)
def test_oracle_against_independent_implementation(task: Task, tmp_path: Path) -> None:
    result = verify(task, tmp_path / task.name)
    assert result["fixed_oracle_correct"], result
    assert result["passed"], [row for row in result["checks"] if not row["passed"]]
