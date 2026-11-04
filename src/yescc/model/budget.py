"""单任务预算：调用次数、费用与墙钟时间。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from yescc.model.base import BudgetExceeded

DEFAULT_BUDGET_USD = 0.05
DEFAULT_MAX_ROUNDS = 2
DEFAULT_MAX_WALL_S = 180.0


@dataclass
class Budget:
    """受硬上限约束的调用预算。"""

    max_usd: float = DEFAULT_BUDGET_USD
    max_rounds: int = DEFAULT_MAX_ROUNDS
    max_wall_s: float = DEFAULT_MAX_WALL_S
    calls: int = 0
    spent_usd: float = 0.0
    started: float = field(default_factory=time.monotonic)

    def check(self) -> None:
        if self.calls >= self.max_rounds:
            raise BudgetExceeded(f"已达到最大调用轮数 {self.max_rounds}")
        if self.spent_usd >= self.max_usd:
            raise BudgetExceeded(f"已达到费用上限 ${self.max_usd:.2f}")
        if time.monotonic() - self.started > self.max_wall_s:
            raise BudgetExceeded(f"已达到任务墙钟上限 {self.max_wall_s:.0f}s")

    def record(self, cost_usd: float) -> None:
        self.calls += 1
        self.spent_usd += max(cost_usd, 0.0)

    @property
    def exhausted(self) -> bool:
        return (
            self.calls >= self.max_rounds
            or self.spent_usd >= self.max_usd
            or time.monotonic() - self.started > self.max_wall_s
        )
