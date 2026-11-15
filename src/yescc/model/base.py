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
    served_model: str | None = None
    finish_reason: str | None = None
    reasoning_tokens: int | None = None
    response_error: str | None = None


class ModelError(Exception):
    """模型调用或响应解析失败。"""


class ModelUnavailable(ModelError):
    """没有可用配置或处于离线模式。"""


class ModelConfigurationError(ModelError):
    """显式模型控制无效；不得通过重试静默忽略。"""


class BudgetExceeded(ModelError):
    """调用次数、费用或时间超出预算。"""


class ModelResponseError(ModelError):
    """模型返回的内容不是约定的 JSON。"""
