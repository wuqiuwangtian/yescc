"""模型配置：环境变量、供应商别名与两级路由。"""

from __future__ import annotations

import os
from dataclasses import dataclass

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
    input_price_per_mtok: float
    output_price_per_mtok: float
    timeout_s: float

    @property
    def is_local(self) -> bool:
        return self.base_url.startswith(("http://localhost", "http://127.0.0.1", "http://[::1]"))


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
        return float(raw)
    except ValueError:
        return default


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
        _env(prefix + "BASE_URL", legacy + "BASE_URL")
        or (tier == "l3" and _env("YESCC_MODEL_BASE_URL", "FCOMP_MODEL_BASE_URL"))
        or DEFAULT_BASE_URLS.get(provider)
    )
    model = (
        model_override
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
    if not api_key and not base_url.startswith(
        ("http://localhost", "http://127.0.0.1", "http://[::1]")
    ):
        return None

    return ModelSettings(
        provider=provider,
        base_url=base_url.rstrip("/"),
        model=model,
        api_key=api_key,
        input_price_per_mtok=_float_env(
            prefix + "INPUT_PRICE_PER_MTOK",
            _float_env("YESCC_MODEL_INPUT_PRICE_PER_MTOK", 0.0),
        ),
        output_price_per_mtok=_float_env(
            prefix + "OUTPUT_PRICE_PER_MTOK",
            _float_env("YESCC_MODEL_OUTPUT_PRICE_PER_MTOK", 0.0),
        ),
        timeout_s=_float_env(prefix + "TIMEOUT_S", 60.0),
    )
