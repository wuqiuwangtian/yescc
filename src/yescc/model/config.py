"""模型配置：环境变量、供应商别名与两级路由。"""

from __future__ import annotations

import ipaddress
import math
import os
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from yescc.model.base import ModelConfigurationError

MAX_OUTPUT_TOKENS = 16384
REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}

DEFAULT_BASE_URLS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
    "vllm": "http://localhost:8000/v1",
}

DEFAULT_MODELS: dict[str, str] = {
    "deepseek": "deepseek-chat",
}


@dataclass(frozen=True)
class ModelSettings:
    """一个模型端点的配置。"""

    provider: str
    base_url: str
    model: str
    api_key: str | None
    input_price_per_mtok: float | None
    output_price_per_mtok: float | None
    timeout_s: float
    reasoning_effort: str | None = None
    thinking: str | None = None
    max_output_tokens: int = 1024

    @property
    def is_local(self) -> bool:
        host = urlsplit(self.base_url).hostname
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host or "").is_loopback
        except ValueError:
            return False


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
        return value if math.isfinite(value) and value > 0 else default
    except ValueError:
        return default


def _price_env(tier_name: str, common_name: str) -> float | None:
    raw = _env(tier_name, common_name)
    try:
        value = float(raw) if raw is not None else None
    except ValueError:
        return None
    return value if value is not None and math.isfinite(value) and value >= 0 else None


def _output_limit_env(tier_name: str) -> int:
    raw = _env(tier_name, "YESCC_MODEL_MAX_OUTPUT_TOKENS")
    if raw is None:
        return 1024
    try:
        return int(raw)
    except ValueError:
        # Validate only on a model path; native success must ignore model configuration.
        return 0


def resolve_controls(settings: ModelSettings) -> ModelSettings:
    """Validate and normalize explicit controls before budget reservation or HTTP."""
    effort = settings.reasoning_effort
    thinking = settings.thinking
    effort = None if effort in (None, "auto") else effort
    thinking = None if thinking in (None, "auto") else thinking
    if effort is not None and effort not in REASONING_EFFORTS:
        raise ModelConfigurationError("reasoning_effort 配置无效，请使用受支持的推理等级。")
    if thinking not in (None, "enabled", "disabled"):
        raise ModelConfigurationError("thinking 配置无效，请使用 auto、enabled 或 disabled。")
    if type(settings.max_output_tokens) is not int or not (
        1 <= settings.max_output_tokens <= MAX_OUTPUT_TOKENS
    ):
        raise ModelConfigurationError(f"max_output_tokens 必须是 1–{MAX_OUTPUT_TOKENS} 的整数。")
    if settings.provider == "deepseek":
        if effort == "none":
            if thinking == "enabled":
                raise ModelConfigurationError(
                    "DeepSeek 的 reasoning_effort=none 与 thinking=enabled 冲突。"
                )
            thinking, effort = "disabled", None
        elif effort is not None:
            if thinking == "disabled":
                raise ModelConfigurationError("DeepSeek 的推理等级与 thinking=disabled 冲突。")
            thinking = "enabled"
            effort = {"minimal": "low", "medium": "high", "xhigh": "high"}.get(effort, effort)
        if thinking is None:
            thinking = "enabled" if settings.model == "deepseek-reasoner" else "disabled"
    elif settings.provider == "openai":
        if thinking is not None:
            raise ModelConfigurationError("OpenAI 不支持 thinking 开关，请使用 reasoning_effort。")
    elif thinking is not None or effort is not None:
        raise ModelConfigurationError("此兼容供应商尚未声明推理控制支持，不能忽略显式设置。")
    return replace(settings, reasoning_effort=effort, thinking=thinking)


def load_settings(
    tier: str,
    *,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> ModelSettings | None:
    """加载 L2/L3 配置；缺少必要字段时返回 None。"""
    prefix = "YESCC_MODEL_L3_" if tier == "l3" else "YESCC_MODEL_L2_"
    legacy = "FCOMP_MODEL_L3_" if tier == "l3" else "FCOMP_MODEL_L2_"

    provider = (
        provider_override
        or _env(prefix + "PROVIDER", legacy + "PROVIDER")
        or _env("YESCC_MODEL_PROVIDER", "FCOMP_MODEL_PROVIDER")
        or "openai-compatible"
    ).lower()

    base_url = (
        (DEFAULT_BASE_URLS.get(provider) if provider_override else None)
        or _env(prefix + "BASE_URL", legacy + "BASE_URL")
        or (tier == "l3" and _env("YESCC_MODEL_BASE_URL", "FCOMP_MODEL_BASE_URL"))
        or DEFAULT_BASE_URLS.get(provider)
    )
    model = (
        model_override
        or (DEFAULT_MODELS.get(provider) if provider_override else None)
        or _env(prefix + "NAME", prefix + "MODEL", legacy + "NAME", legacy + "MODEL")
        or (tier == "l3" and _env("YESCC_MODEL_NAME", "FCOMP_MODEL_NAME"))
        or DEFAULT_MODELS.get(provider)
    )
    api_key = _env(
        prefix + "API_KEY",
        legacy + "API_KEY",
        "YESCC_MODEL_API_KEY",
        "FCOMP_MODEL_API_KEY",
    )

    if not base_url or not model:
        return None
    settings = ModelSettings(
        provider=provider,
        base_url=base_url.rstrip("/"),
        model=model,
        api_key=api_key,
        input_price_per_mtok=_price_env(
            prefix + "INPUT_PRICE_PER_MTOK",
            "YESCC_MODEL_INPUT_PRICE_PER_MTOK",
        ),
        output_price_per_mtok=_price_env(
            prefix + "OUTPUT_PRICE_PER_MTOK",
            "YESCC_MODEL_OUTPUT_PRICE_PER_MTOK",
        ),
        timeout_s=_float_env(prefix + "TIMEOUT_S", 60.0),
        reasoning_effort=_env(prefix + "REASONING_EFFORT", "YESCC_MODEL_REASONING_EFFORT"),
        thinking=_env(prefix + "THINKING", "YESCC_MODEL_THINKING"),
        max_output_tokens=_output_limit_env(prefix + "MAX_OUTPUT_TOKENS"),
    )
    return settings if api_key or settings.is_local else None
