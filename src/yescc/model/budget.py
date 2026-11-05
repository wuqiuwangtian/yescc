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
        self.check_deadline()

    def check_deadline(self) -> None:
        if self.remaining_s <= 0:
            raise BudgetExceeded(f"已达到任务墙钟上限 {self.max_wall_s:.0f}s")

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.max_wall_s - (time.monotonic() - self.started))

    def reserve(self, cost_usd: float) -> None:
        """请求前预留最大估算费用；失败请求也占用一次调用和预留。"""
        self.check()
        if self.spent_usd + cost_usd > self.max_usd:
            raise BudgetExceeded("剩余费用不足以支付本次请求的最大估算费用")
        self.calls += 1
        self.spent_usd += cost_usd

    def settle(self, reserved_usd: float, actual_usd: float) -> None:
        self.spent_usd += actual_usd - reserved_usd
        if self.spent_usd > self.max_usd:
            raise BudgetExceeded("供应商报告费用超过任务上限，停止使用本次结果")

    @property
    def exhausted(self) -> bool:
        return (
            self.calls >= self.max_rounds
            or self.spent_usd >= self.max_usd
            or time.monotonic() - self.started > self.max_wall_s
        )
