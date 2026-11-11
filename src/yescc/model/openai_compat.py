"""OpenAI 兼容 Chat Completions 客户端。"""

from __future__ import annotations

import time
from collections.abc import Sequence

import httpx

from yescc.model.base import Completion, Message, ModelError
from yescc.model.config import ModelSettings


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
        url = self.settings.base_url + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        payload = {
            "model": self.settings.model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
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
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError("模型响应格式不符合 OpenAI 兼容约定") from exc

        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str):
            raise ModelError("模型响应内容不是文本")

        usage = data.get("usage")
        usage_known = (
            isinstance(usage, dict)
            and type(usage.get("prompt_tokens")) is int
            and type(usage.get("completion_tokens")) is int
            and usage["prompt_tokens"] >= 0
            and usage["completion_tokens"] >= 0
        )
        prompt_tokens = usage["prompt_tokens"] if usage_known else 0
        completion_tokens = usage["completion_tokens"] if usage_known else 0
        cost = (
            prompt_tokens / 1_000_000 * (self.settings.input_price_per_mtok or 0.0)
            + completion_tokens / 1_000_000 * (self.settings.output_price_per_mtok or 0.0)
        )
        return Completion(
            text=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            duration_ms=duration_ms,
            usage_known=usage_known,
        )
