"""Explicit controls must affect real wire requests, accounting and cache identity."""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

import httpx
import pytest

from yescc.model.base import ModelConfigurationError, ModelResponseError, ModelUnavailable
from yescc.model.config import ModelSettings, load_settings, resolve_controls
from yescc.model.openai_compat import OpenAICompatibleProvider
from yescc.model.session import ModelSession

SOURCE = '#include <stdio.h>\nint main(void){puts("hello");return 0;}\n'


def settings(provider: str = "deepseek", **kwargs) -> ModelSettings:
    # Tests never use a real endpoint or credential.
    return replace(
        ModelSettings(provider, "http://127.0.0.1:9/v1", "test-model", "dummy-key", 1, 1, 1),
        **kwargs,
    )


def session(configured: ModelSettings, **kwargs) -> ModelSession:
    with patch("yescc.model.session.load_settings", return_value=configured):
        return ModelSession(
            offline=kwargs.pop("offline", False),
            no_cache=kwargs.pop("no_cache", True),
            budget_usd=kwargs.pop("budget_usd", 0.05),
            max_rounds=2,
            **kwargs,
        )


def response(**kwargs) -> httpx.Response:
    body = {
        "model": "served-model",
        "choices": [
            {"message": {"content": json.dumps({"source": SOURCE})}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        **kwargs,
    }
    return httpx.Response(200, json=body)


def test_deepseek_default_explicitly_disables_thinking() -> None:
    with patch("httpx.Client.post", return_value=response()) as post:
        outcome = session(settings()).generate("月亮摸鱼", creative=True)
    assert outcome is not None
    payload = post.call_args.kwargs["json"]
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["temperature"] == 0.4
    assert "reasoning_effort" not in payload
    assert payload["max_tokens"] == 1024


@pytest.mark.parametrize(
    ("requested", "wire"),
    [
        ("minimal", "low"),
        ("low", "low"),
        ("medium", "high"),
        ("high", "high"),
        ("xhigh", "high"),
        ("max", "max"),
    ],
)
def test_deepseek_effort_enables_thinking_and_normalizes(requested, wire) -> None:
    with patch("httpx.Client.post", return_value=response()) as post:
        assert session(settings(reasoning_effort=requested)).generate("月亮", creative=True)
    payload = post.call_args.kwargs["json"]
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == wire
    assert "temperature" not in payload


def test_deepseek_none_is_nonthinking_not_unsupported_effort() -> None:
    with patch("httpx.Client.post", return_value=response()) as post:
        assert session(settings(reasoning_effort="none")).generate("月亮", creative=True)
    assert post.call_args.kwargs["json"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in post.call_args.kwargs["json"]


@pytest.mark.parametrize("effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max"])
def test_openai_explicit_effort_uses_completion_budget(effort) -> None:
    with patch("httpx.Client.post", return_value=response()) as post:
        assert session(settings("openai", reasoning_effort=effort)).generate("月亮", creative=True)
    payload = post.call_args.kwargs["json"]
    assert payload["reasoning_effort"] == effort
    assert payload["max_completion_tokens"] == 1024
    assert "max_tokens" not in payload and "thinking" not in payload
    assert ("temperature" in payload) == (effort == "none")


@pytest.mark.parametrize(
    "configured",
    [
        settings(reasoning_effort="high", thinking="disabled"),
        settings(reasoning_effort="none", thinking="enabled"),
        settings("openai", thinking="disabled"),
        settings("openai-compatible", reasoning_effort="none"),
        settings("ollama", thinking="enabled"),
        settings(reasoning_effort="bad-secret-value"),
        settings(thinking="bad-secret-value"),
        settings(max_output_tokens=0),
        settings(max_output_tokens=16385),
    ],
)
def test_invalid_controls_do_not_upload_or_reserve_and_repair_does_not_spin(configured) -> None:
    current = session(configured)
    with patch("httpx.Client.post") as post, pytest.raises(ModelConfigurationError) as caught:
        current.repair("int main( {", "broken.c", "error")
    assert not post.called
    assert current.stats.calls == current.budget.calls == current.attempts == 0
    assert current.budget.spent_usd == 0
    assert "bad-secret-value" not in str(caught.value)


def test_bad_environment_is_lazy_and_offline_ignores_it(monkeypatch, fake_model) -> None:
    monkeypatch.setenv("YESCC_MODEL_L3_MAX_OUTPUT_TOKENS", "private-invalid-value")
    monkeypatch.setenv("YESCC_MODEL_L3_REASONING_EFFORT", "private-invalid-value")
    configured = load_settings("l3")
    assert configured is not None
    current = session(configured, offline=True)
    with pytest.raises(ModelUnavailable):
        current.generate("月亮", creative=True)
    assert not fake_model.requests
    assert current.stats.calls == 0


def test_explicit_auto_clears_environment_control_and_cap_override_is_validated(fake_model) -> None:
    configured = settings(
        "openai-compatible", reasoning_effort="invalid", thinking="invalid", max_output_tokens=0
    )
    current = session(
        configured,
        reasoning_effort_override="auto",
        thinking_override="auto",
        max_output_tokens_override=2048,
    )
    with patch("httpx.Client.post", return_value=response()) as post:
        assert current.generate("月亮", creative=True)
    payload = post.call_args.kwargs["json"]
    assert payload["max_tokens"] == 2048
    assert "thinking" not in payload and "reasoning_effort" not in payload


def test_tier_environment_precedence(monkeypatch, fake_model) -> None:
    monkeypatch.setenv("YESCC_MODEL_REASONING_EFFORT", "medium")
    monkeypatch.setenv("YESCC_MODEL_THINKING", "enabled")
    monkeypatch.setenv("YESCC_MODEL_MAX_OUTPUT_TOKENS", "4096")
    monkeypatch.setenv("YESCC_MODEL_L2_REASONING_EFFORT", "low")
    monkeypatch.setenv("YESCC_MODEL_L2_MAX_OUTPUT_TOKENS", "512")
    l2, l3 = load_settings("l2"), load_settings("l3")
    assert l2 and l3
    assert (l2.reasoning_effort, l2.max_output_tokens) == ("low", 512)
    assert (l3.reasoning_effort, l3.thinking, l3.max_output_tokens) == ("medium", "enabled", 4096)


def test_increased_output_cap_still_obeys_price_budget() -> None:
    current = session(settings(input_price_per_mtok=0.1, max_output_tokens=8192), budget_usd=0.002)
    with patch("httpx.Client.post", return_value=response()) as post:
        assert current.generate("月亮", creative=True)
    assert 0 < post.call_args.kwargs["json"]["max_tokens"] < 2000
    assert current.budget.spent_usd <= 0.002


@pytest.mark.parametrize("control", ["max_output_tokens", "thinking", "reasoning_effort"])
def test_cache_identity_tracks_effective_controls_and_cache_never_fakes_response(control) -> None:
    original = settings()
    changed = {
        "max_output_tokens": replace(original, max_output_tokens=2048),
        "thinking": replace(original, thinking="enabled"),
        "reasoning_effort": replace(original, reasoning_effort="high"),
    }[control]
    with patch("httpx.Client.post", return_value=response()) as post:
        first = session(original, no_cache=False)
        assert first.generate("月亮", creative=True)
        second = session(changed, no_cache=False)
        assert second.generate("月亮", creative=True)
        cached = session(changed, no_cache=False)
        assert cached.generate("月亮", creative=True)
    assert post.call_count == 2
    assert first.stats.served_model == second.stats.served_model == "served-model"
    assert cached.stats.cache_hits == cached.attempts == 1
    assert cached.stats.calls == 0 and cached.stats.response_source == "cache"
    assert cached.stats.served_model is cached.stats.finish_reason is None
    assert cached.stats.request_settings is None and cached.stats.responses == []


@pytest.mark.parametrize("content", ["", '{"source":"otherwise valid"}'])
def test_length_response_records_usage_before_rejecting_even_valid_json(content) -> None:
    body = response(
        model="deepseek-flash",
        choices=[
            {
                "message": {"content": content, "reasoning_content": "PRIVATE_REASONING"},
                "finish_reason": "length",
            }
        ],
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 512,
            "completion_tokens_details": {"reasoning_tokens": 512},
        },
    )
    current = session(settings())
    with (
        patch("httpx.Client.post", return_value=body),
        pytest.raises(ModelResponseError, match="截断"),
    ):
        current.generate("月亮", creative=True)
    assert current.stats.cost_known is True
    assert current.stats.prompt_tokens == 10 and current.stats.completion_tokens == 512
    assert current.stats.cost_usd == pytest.approx(0.000522)
    assert current.budget.spent_usd == pytest.approx(0.000522)
    assert current.stats.served_model == "deepseek-flash"
    assert current.stats.finish_reason == "length" and current.stats.reasoning_tokens == 512
    assert "PRIVATE_REASONING" not in json.dumps(current.stats.as_dict())


@pytest.mark.parametrize(
    "choices", [[], [{"message": {"content": None}}], [{"message": {"content": []}}]]
)
def test_empty_or_malformed_message_preserves_known_usage(choices) -> None:
    current = session(settings())
    with (
        patch("httpx.Client.post", return_value=response(choices=choices)),
        pytest.raises(ModelResponseError),
    ):
        current.generate("月亮", creative=True)
    assert current.stats.cost_known is True
    assert current.budget.spent_usd == pytest.approx(0.000015)


def test_metadata_redacts_a_provider_that_echoes_the_key() -> None:
    with patch("httpx.Client.post", return_value=response(model="dummy-key")):
        result = OpenAICompatibleProvider(settings()).complete([], max_tokens=10)
    assert result.served_model == "[REDACTED]"


def test_default_generic_provider_keeps_compatible_request_format() -> None:
    with patch("httpx.Client.post", return_value=response()) as post:
        assert session(settings("openai-compatible")).generate("月亮", creative=True)
    assert set(post.call_args.kwargs["json"]) == {
        "model",
        "messages",
        "stream",
        "temperature",
        "max_tokens",
    }


def test_resolving_normalized_deepseek_controls_is_idempotent() -> None:
    configured = resolve_controls(settings(reasoning_effort="medium"))
    assert resolve_controls(configured) == configured
