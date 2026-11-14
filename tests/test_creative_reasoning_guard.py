"""HTTP-boundary stopping: all transports are in-memory and never execute C."""

from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import patch

import httpx
import pytest
from bench.creative_reasoning_guard import HTTPGuard

from yescc.model.base import ModelError, ModelResponseError
from yescc.model.config import ModelSettings
from yescc.model.session import ModelSession

ENDPOINT = "http://127.0.0.1:9/v1/chat/completions"
PROFILE = {"thinking": "disabled", "reasoning_effort": None, "max_output_tokens": 1024}
SOURCE = '#include <stdio.h>\nint main(void) { puts("MOCK"); return 0; }\n'


def payload() -> dict:
    return {
        "model": "deepseek-flash", "stream": False, "max_tokens": 1024,
        "temperature": 0.4, "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": "tea"}],
    }


def body() -> dict:
    return {
        "model": "served-flash", "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "choices": [{"message": {"content": json.dumps({"source": SOURCE})},
                     "finish_reason": "stop"}],
    }


def fixture_guard(**kwargs):
    events = []
    guard = HTTPGuard(
        endpoint=ENDPOINT, requested_model="deepseek-flash", profile=PROFILE,
        record=events.append, **kwargs,
    )
    return guard, events


def install(monkeypatch, guard, replies):
    forwarded = []
    returned = []

    def original_send(client, request, **kwargs):
        forwarded.append(request)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        response = reply if isinstance(reply, httpx.Response) else httpx.Response(
            200, json=reply, request=request,
        )
        returned.append(response)
        return response

    monkeypatch.setattr(httpx.Client, "send", guard.wrap_send(original_send))
    return forwarded, returned


def session() -> ModelSession:
    configured = ModelSettings(
        provider="deepseek", base_url=ENDPOINT.removesuffix("/chat/completions"),
        model="deepseek-flash",
        api_key="public-test-key", input_price_per_mtok=.3, output_price_per_mtok=1.2,
        timeout_s=1, thinking="disabled", max_output_tokens=1024,
    )
    with patch("yescc.model.session.load_settings", return_value=configured):
        return ModelSession(offline=False, no_cache=True, budget_usd=.005, max_rounds=2)


def test_unknown_usage_blocks_second_send_inside_same_model_session(monkeypatch) -> None:
    first = body()
    first["usage"] = None
    first["choices"][0]["message"]["content"] = "not-json"
    guard, events = fixture_guard()
    forwarded, _ = install(monkeypatch, guard, [first, body()])
    current = session()
    with pytest.raises(ModelResponseError):
        current.generate("a quiet teapot", creative=True)
    assert current.stats.cost_known is False
    with pytest.raises(ModelError, match="unknown_usage"):
        current.generate("a quiet teapot", creative=True, feedback="retry")
    assert current.stats.calls == 2  # Product attempts include the blocked send.
    assert guard.request_count == len(forwarded) == 1
    assert [e["event"] for e in events] == ["request", "response", "blocked_before_send"]


@pytest.mark.parametrize("change,reason", [
    (lambda d: d.pop("model"), "missing_served_model"),
    (lambda d: d.update(model="different-model"), "served_model_mismatch"),
    (lambda d: d.pop("usage"), "unknown_usage"),
    (lambda d: d["usage"].update(prompt_tokens=True), "unknown_usage"),
    (lambda d: d["usage"].update(completion_tokens=-1), "unknown_usage"),
])
def test_response_uncertainty_returns_original_then_stops_before_next_send(
    change, reason, monkeypatch,
) -> None:
    first = body()
    change(first)
    guard, events = fixture_guard(expected_model="served-flash")
    forwarded, returned = install(monkeypatch, guard, [first, body()])
    with httpx.Client(trust_env=False) as client:
        response = client.post(ENDPOINT, json=payload())
        assert response is returned[0] and response.json() == first
        with pytest.raises(httpx.RequestError, match=reason):
            client.post(ENDPOINT, json=payload())
    assert len(forwarded) == 1 and guard.halted_reason == reason
    assert events[-1]["event"] == "blocked_before_send"


@pytest.mark.parametrize("response,reason", [
    (httpx.Response(400, json={"error": "unsupported reasoning_effort"}), "http_error"),
    (httpx.Response(429, json={"error": "rate limit"}), "http_error"),
    (httpx.Response(302, json={"redirect": True}), "http_error"),
    (httpx.Response(200, text="not json"), "response_not_json"),
    (httpx.Response(200, json=[]), "response_not_object"),
])
def test_http_and_format_errors_halt_next_request(response, reason, monkeypatch) -> None:
    guard, _ = fixture_guard()
    forwarded, _ = install(monkeypatch, guard, [response, body()])
    with httpx.Client(trust_env=False) as client:
        assert client.post(ENDPOINT, json=payload()) is response
        with pytest.raises(httpx.RequestError, match=reason):
            client.post(ENDPOINT, json=payload())
    assert len(forwarded) == 1


def test_transport_error_halts_second_model_request_and_redacts_key(monkeypatch) -> None:
    guard, events = fixture_guard()
    error = httpx.ReadTimeout("failed with public-test-key")
    forwarded, _ = install(monkeypatch, guard, [error, body()])
    current = session()
    with pytest.raises(ModelError, match="REDACTED"):
        current.generate("tea", creative=True)
    with pytest.raises(ModelError, match="transport_error"):
        current.generate("tea", creative=True)
    assert len(forwarded) == 1
    assert "public-test-key" not in json.dumps(events)


@pytest.mark.parametrize("change", [
    lambda d: d.update(model="wrong"),
    lambda d: d.update(max_tokens=1025),
    lambda d: d.update(max_tokens=True),
    lambda d: d.update(max_tokens=0),
    lambda d: d.update(stream=True),
    lambda d: d.update(thinking={"type": "enabled"}),
    lambda d: d.update(temperature=.7),
    lambda d: d.update(reasoning_effort="low"),
    lambda d: d.update(tools=[]),
    lambda d: d.update(messages=[]),
])
def test_changed_controls_are_blocked_before_original_send(change, monkeypatch) -> None:
    guard, events = fixture_guard()
    forwarded, _ = install(monkeypatch, guard, [body()])
    request = payload()
    change(request)
    with httpx.Client(trust_env=False) as client, pytest.raises(httpx.RequestError):
        client.post(ENDPOINT, json=request)
    assert not forwarded and guard.request_count == 0
    assert len(events) == 1 and events[0]["event"] == "blocked_before_send"


def test_endpoint_and_redirects_are_blocked_without_forwarding(monkeypatch) -> None:
    guard, _ = fixture_guard()
    forwarded, _ = install(monkeypatch, guard, [body()])
    with httpx.Client(trust_env=False) as client, pytest.raises(
        httpx.RequestError, match="endpoint_mismatch",
    ):
        client.post("http://127.0.0.1:8/v1/chat/completions", json=payload())
    assert not forwarded
    guard, _ = fixture_guard()
    forwarded, _ = install(monkeypatch, guard, [body()])
    with httpx.Client(trust_env=False, follow_redirects=True) as client, pytest.raises(
        httpx.RequestError, match="redirects_forbidden",
    ):
        client.post(ENDPOINT, json=payload())
    assert not forwarded


@pytest.mark.parametrize("empty", [False, True])
def test_known_usage_length_or_empty_body_allows_existing_retry(empty, monkeypatch) -> None:
    first = body()
    first["choices"][0]["finish_reason"] = "stop" if empty else "length"
    first["choices"][0]["message"]["content"] = "" if empty else "truncated"
    guard, events = fixture_guard()
    forwarded, _ = install(monkeypatch, guard, [first, body()])
    current = session()
    with pytest.raises(ModelResponseError):
        current.generate("tea", creative=True)
    program = current.generate("tea", creative=True, feedback="retry")
    assert program is not None and program.source == SOURCE
    assert current.stats.cost_known is True and current.stats.calls == 2
    assert len(forwarded) == 2 and guard.halted_reason is None
    assert [e["event"] for e in events] == ["request", "response", "request", "response"]


def test_third_send_is_blocked_and_first_served_model_is_retained(monkeypatch) -> None:
    guard, _ = fixture_guard()
    forwarded, _ = install(monkeypatch, guard, [body(), body(), body()])
    with httpx.Client(trust_env=False) as client:
        client.post(ENDPOINT, json=payload())
        client.post(ENDPOINT, json=payload())
        with pytest.raises(httpx.RequestError, match="request_limit"):
            client.post(ENDPOINT, json=payload())
    assert len(forwarded) == guard.request_count == 2
    assert guard.served_model == "served-flash"


def test_privacy_does_not_mutate_response_or_wire_payload(monkeypatch) -> None:
    response_body = body()
    response_body["choices"][0]["message"].update(
        reasoning_content="PRIVATE_THOUGHT", content="public-test-key",
    )
    response_body["usage"].update(
        completion_tokens_details={"reasoning_tokens": 3}, prompt_cache_hit_tokens=2,
    )
    original = deepcopy(response_body)
    guard, events = fixture_guard()
    forwarded, returned = install(monkeypatch, guard, [response_body])
    request_payload = payload()
    with httpx.Client(trust_env=False) as client:
        response = client.post(
            ENDPOINT, json=request_payload, headers={"Authorization": "Bearer public-test-key"},
        )
    assert response is returned[0] and response.json() == original
    assert json.loads(forwarded[0].content) == request_payload
    logs = json.dumps(events)
    assert "PRIVATE_THOUGHT" not in logs and "public-test-key" not in logs
    saved = events[1]["body"]
    assert saved["choices"][0]["message"]["reasoning_content_omitted"]["bytes"] == 15
    assert saved["usage"]["completion_tokens_details"]["reasoning_tokens"] == 3
    assert saved["usage"]["prompt_cache_hit_tokens"] == 2


def test_evidence_failure_stops_before_original_send(monkeypatch) -> None:
    def fail(event):
        raise OSError("disk full")

    guard = HTTPGuard(
        endpoint=ENDPOINT, requested_model="deepseek-flash", profile=PROFILE, record=fail,
    )
    forwarded, _ = install(monkeypatch, guard, [body()])
    with httpx.Client(trust_env=False) as client, pytest.raises(OSError, match="disk full"):
        client.post(ENDPOINT, json=payload())
    assert not forwarded and guard.request_count == 0
    assert guard.halted_reason == "evidence_write_failed"


@pytest.mark.parametrize("extra_temperature", [False, True])
def test_low_profile_accepts_budget_clipping_but_never_temperature(
    extra_temperature, monkeypatch,
) -> None:
    events = []
    guard = HTTPGuard(
        endpoint=ENDPOINT, requested_model="deepseek-flash", record=events.append,
        profile={"thinking": "enabled", "reasoning_effort": "low", "max_output_tokens": 4096},
    )
    forwarded, _ = install(monkeypatch, guard, [body()])
    request = payload()
    request.update(thinking={"type": "enabled"}, reasoning_effort="low", max_tokens=3399)
    if not extra_temperature:
        request.pop("temperature")
    with httpx.Client(trust_env=False) as client:
        if extra_temperature:
            with pytest.raises(httpx.RequestError, match="request_fields_mismatch"):
                client.post(ENDPOINT, json=request)
        else:
            assert client.post(ENDPOINT, json=request).status_code == 200
    assert len(forwarded) == (0 if extra_temperature else 1)


@pytest.mark.parametrize("limit", [0, 3, True])
def test_constructor_cannot_expand_the_frozen_request_limit(limit) -> None:
    with pytest.raises(ValueError, match="frozen"):
        fixture_guard(max_requests=limit)
