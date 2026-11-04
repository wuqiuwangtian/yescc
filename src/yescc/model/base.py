"""模型层公共类型与错误。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    duration_ms: int
    usage_known: bool = True


class ModelError(Exception):
    """模型调用或响应解析失败。"""


class ModelUnavailable(ModelError):
    """没有可用配置或处于离线模式。"""


class BudgetExceeded(ModelError):
    """调用次数、费用或时间超出预算。"""


class ModelResponseError(ModelError):
    """模型返回的内容不是约定的 JSON。"""
