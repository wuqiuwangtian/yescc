"""Fail-closed HTTP observation for the frozen reasoning experiment.

This research helper never changes the request or response given to YesCC. A bad
response is returned for normal product accounting, then prevents another send.
The caller must clear proxy configuration and freeze this helper before payment.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any, NoReturn

import httpx

JsonRecord = Callable[[dict[str, Any]], None]
Send = Callable[..., httpx.Response]


class HTTPGuard:
    """Validate every attempted send, retain safe evidence, and stop on uncertainty."""

    def __init__(
        self, *, endpoint: str, requested_model: str, profile: Mapping[str, Any],
        record: JsonRecord, expected_model: str | None = None, max_requests: int = 2,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self.endpoint = httpx.URL(endpoint)
        if (self.endpoint.scheme not in {"http", "https"} or not self.endpoint.host
                or self.endpoint.username or self.endpoint.password or self.endpoint.query
                or self.endpoint.fragment):
            raise ValueError("endpoint must be an absolute HTTP URL without credentials/query")
        if type(max_requests) is not int or not 1 <= max_requests <= 2:
            raise ValueError("max_requests must remain within the frozen one/two-request limit")
        self.requested_model = requested_model
        self.profile = dict(profile)
        self.record = record
        self.max_requests = max_requests
        self.served_model = expected_model
        self.request_count = 0
        self.halted_reason: str | None = None
        self._secrets = tuple(secret for secret in secrets if secret)

    def _redact(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def _safe(self, value: Any) -> Any:
        if isinstance(value, dict):
            safe = {}
            for key, item in value.items():
                lower = str(key).lower()
                if lower in {"authorization", "api_key", "apikey", "access_token"}:
                    continue
                if lower == "reasoning_content":
                    if isinstance(item, str):
                        raw = item.encode("utf-8")
                        safe["reasoning_content_omitted"] = {
                            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                        }
                    continue
                safe[self._redact(str(key))] = self._safe(item)
            return safe
        if isinstance(value, list):
            return [self._safe(item) for item in value]
        return self._redact(value) if isinstance(value, str) else value

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self.record(self._safe(event))
        except Exception:
            self.halted_reason = self.halted_reason or "evidence_write_failed"
            raise

    def _block(self, reason: str, request: httpx.Request) -> NoReturn:
        self.halted_reason = self.halted_reason or reason
        self._emit({"event": "blocked_before_send", "reason": self.halted_reason,
                    "request_count": self.request_count})
        raise httpx.RequestError(f"research HTTP guard: {self.halted_reason}", request=request)

    def _validate(
        self, client: httpx.Client, request: httpx.Request, kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        if self.halted_reason is not None:
            self._block(self.halted_reason, request)
        if self.request_count >= self.max_requests:
            self._block("request_limit", request)
        if request.method != "POST" or request.url != self.endpoint:
            self._block("endpoint_mismatch", request)
        if client.follow_redirects or kwargs.get("follow_redirects") is True:
            self._block("redirects_forbidden", request)
        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Bearer "):
            secret = authorization[7:]
            if secret and secret not in self._secrets:
                self._secrets += (secret,)
        try:
            payload = json.loads(request.content)
        except (ValueError, UnicodeDecodeError, httpx.RequestNotRead):
            self._block("request_not_json", request)
        if not isinstance(payload, dict):
            self._block("request_not_object", request)
        expected: dict[str, Any] = {
            "model": self.requested_model, "stream": False,
            "thinking": {"type": self.profile["thinking"]},
        }
        if self.profile["reasoning_effort"] is not None:
            expected["reasoning_effort"] = self.profile["reasoning_effort"]
        if self.profile["thinking"] == "disabled":
            expected["temperature"] = 0.4
        if set(payload) != set(expected) | {"messages", "max_tokens"}:
            self._block("request_fields_mismatch", request)
        if any(payload.get(key) != value for key, value in expected.items()):
            self._block("request_controls_mismatch", request)
        cap = payload.get("max_tokens")
        if type(cap) is not int or not 1 <= cap <= self.profile["max_output_tokens"]:
            self._block("request_token_cap_mismatch", request)
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages or any(
            not isinstance(message, dict) or set(message) != {"role", "content"}
            or message.get("role") not in {"system", "user", "assistant"}
            or not isinstance(message.get("content"), str) for message in messages
        ):
            self._block("request_messages_mismatch", request)
        return payload

    def _observe(self, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            self.halted_reason = "response_not_json"
            return {"body_omitted": True, "cost_known": False}
        if not isinstance(body, dict):
            self.halted_reason = "response_not_object"
            return {"body": body, "cost_known": False}
        usage = body.get("usage")
        known = isinstance(usage, dict) and all(
            type(usage.get(key)) is int and usage[key] >= 0
            for key in ("prompt_tokens", "completion_tokens")
        )
        model = body.get("model")
        if not 200 <= response.status_code < 300:
            self.halted_reason = "http_error"
        elif not known:
            self.halted_reason = "unknown_usage"
        elif not isinstance(model, str) or not model.strip():
            self.halted_reason = "missing_served_model"
        elif self.served_model is not None and model != self.served_model:
            self.halted_reason = "served_model_mismatch"
        else:
            self.served_model = model
        return {
            "body": body, "usage_known": known,
            "cost_known": known and 200 <= response.status_code < 300, "served_model": model,
        }

    def wrap_send(self, original_send: Send) -> Send:
        """Return an unbound replacement suitable for patching Client.send."""
        def guarded_send(
            client: httpx.Client, request: httpx.Request, **kwargs: Any,
        ) -> httpx.Response:
            payload = self._validate(client, request, kwargs)
            self._emit({"event": "request", "index": self.request_count + 1, "payload": payload})
            self.request_count += 1
            try:
                response = original_send(client, request, **kwargs)
                response.read()
            except Exception as exc:
                self.halted_reason = "transport_error"
                self._emit({"event": "transport_error", "index": self.request_count,
                            "type": type(exc).__name__, "message": str(exc), "cost_known": False})
                raise
            observed = self._observe(response)
            self._emit({"event": "response", "index": self.request_count,
                        "status": response.status_code, "halted_reason": self.halted_reason,
                        **observed})
            return response

        return guarded_send
