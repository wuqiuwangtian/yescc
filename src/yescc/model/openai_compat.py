"""OpenAI 兼容 Chat Completions 客户端。"""

from __future__ import annotations

import time
from collections.abc import Sequence

import httpx

from yescc.model.base import Completion, Message, ModelConfigurationError, ModelError
from yescc.model.config import ModelSettings, resolve_controls


def request_parameters(
    settings: ModelSettings, *, max_tokens: int, temperature: float
) -> dict[str, object]:
    """Return the effective non-secret wire controls, also used for cache identity."""
    settings = resolve_controls(settings)
    if type(max_tokens) is not int or not 1 <= max_tokens <= settings.max_output_tokens:
        raise ModelConfigurationError("实际输出 token 上限超出配置范围。")
    parameters: dict[str, object] = {"max_tokens": max_tokens, "temperature": temperature}
    if settings.provider == "deepseek":
        parameters["thinking"] = {"type": settings.thinking}
        if settings.reasoning_effort is not None:
            parameters["reasoning_effort"] = settings.reasoning_effort
        if settings.thinking == "enabled":
            parameters.pop("temperature")
    elif settings.provider == "openai" and settings.reasoning_effort is not None:
        parameters["max_completion_tokens"] = parameters.pop("max_tokens")
        parameters["reasoning_effort"] = settings.reasoning_effort
        if settings.reasoning_effort != "none":
            parameters.pop("temperature")
    return parameters


class OpenAICompatibleProvider:
    """覆盖 OpenAI、DeepSeek、Qwen、vLLM、Ollama、LM Studio 等兼容端点。"""

    def __init__(self, settings: ModelSettings) -> None:
        self.settings = settings

    def complete(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Completion:
        parameters = request_parameters(
            self.settings, max_tokens=max_tokens, temperature=temperature
        )
        url = self.settings.base_url + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        payload = {
            "model": self.settings.model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "stream": False,
            **parameters,
        }

        def redact(text: str) -> str:
            key = self.settings.api_key
            return text.replace(key, "[REDACTED]") if key else text

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.settings.timeout_s) as client:
                response = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise ModelError(f"模型请求失败：{redact(str(exc))}") from None

        duration_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            body = redact(response.text)[:300]
            raise ModelError(f"模型返回 HTTP {response.status_code}：{body}")

        try:
            data = response.json()
        except ValueError as exc:
            raise ModelError("模型响应格式不符合 OpenAI 兼容约定") from exc
        if not isinstance(data, dict):
            raise ModelError("模型响应格式不符合 OpenAI 兼容约定")

        content: object = None
        finish_reason: str | None = None
        response_error: str | None = None
        try:
            choice = data["choices"][0]
            content = choice["message"].get("content")
            raw_finish = choice.get("finish_reason")
            if isinstance(raw_finish, str):
                finish_reason = redact(raw_finish)[:80]
        except (KeyError, IndexError, TypeError, AttributeError):
            response_error = "模型响应格式不符合 OpenAI 兼容约定"
        if isinstance(content, list):
            content = "".join(
                part["text"]
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        if finish_reason == "length":
            response_error = (
                "模型输出达到 token 上限而截断（finish_reason=length）；"
                "推理可能占用了输出额度，请降低推理等级、禁用支持的thinking或调整输出上限。"
            )
        elif finish_reason == "content_filter":
            response_error = "模型输出被供应商过滤（finish_reason=content_filter）。"
        elif not isinstance(content, str) or not content.strip():
            response_error = response_error or "模型响应正文为空或不是文本，未产生可用程序。"

        usage = data.get("usage")
        usage_known = (
            isinstance(usage, dict)
            and type(usage.get("prompt_tokens")) is int
            and type(usage.get("completion_tokens")) is int
            and usage["prompt_tokens"] >= 0
            and usage["completion_tokens"] >= 0
        )
        prompt_tokens = (
            int(usage["prompt_tokens"]) if isinstance(usage, dict) and usage_known else 0
        )
        completion_tokens = (
            int(usage["completion_tokens"]) if isinstance(usage, dict) and usage_known else 0
        )
        reasoning_tokens: int | None = None
        if isinstance(usage, dict):
            details = usage.get("completion_tokens_details")
            raw_reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
            if type(raw_reasoning) is int and raw_reasoning >= 0:
                reasoning_tokens = raw_reasoning
        served_model = data.get("model")
        cost = prompt_tokens / 1_000_000 * (
            self.settings.input_price_per_mtok or 0.0
        ) + completion_tokens / 1_000_000 * (self.settings.output_price_per_mtok or 0.0)
        return Completion(
            text=content if isinstance(content, str) else "",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            duration_ms=duration_ms,
            usage_known=usage_known,
            served_model=redact(served_model)[:200] if isinstance(served_model, str) else None,
            finish_reason=finish_reason,
            reasoning_tokens=reasoning_tokens,
            response_error=response_error,
        )
