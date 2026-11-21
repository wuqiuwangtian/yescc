"""Replay captured length responses through local HTTP and the real C toolchain.

The seven responses are original evidence, not fresh model quality measurements.
No real credentials or external model endpoints are used by these tests.
"""

from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import json
import os
import threading
from collections.abc import Iterator
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from yescc.cli import main
from yescc.model.config import DEFAULT_BASE_URLS

pytestmark = pytest.mark.e2e

ARCHIVE = Path(__file__).resolve().parents[2] / (
    "docs/评测/data/creative-reasoning-generation-2026-11-14.json.gz"
)
ARCHIVE_SHA = "71cf4861396c1f6dc89cd7b59a681385d269e8f344c2a6074141691fbf317caf"


@lru_cache(maxsize=1)
def _captured_lengths() -> tuple[tuple[str, str, dict[str, Any]], ...]:
    raw = ARCHIVE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ARCHIVE_SHA
    files = {entry["path"]: entry for entry in json.loads(gzip.decompress(raw))["files"]}

    def original(path: str) -> bytes:
        entry = files[path]
        value = base64.b64decode(entry["base64"])
        assert len(value) == entry["bytes"]
        assert hashlib.sha256(value).hexdigest() == entry["sha256"]
        return value

    cases = []
    for path in sorted(files):
        if not path.startswith("runs/") or not path.endswith("/provider-events.jsonl"):
            continue
        events = [json.loads(line) for line in original(path).splitlines()]
        responses = [event["body"] for event in events if event["event"] == "response"]
        if responses and responses[0]["choices"][0]["finish_reason"] == "length":
            directory = path.removesuffix("/provider-events.jsonl")
            cases.append((directory.split("/")[-1],
                          original(directory + "/input.txt").decode("utf-8"), responses[0]))
    assert len(cases) == 7
    return tuple(cases)


class RawModelServer:
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                owner.requests.append(json.loads(raw))
                body = owner.bodies.pop(0) if owner.bodies else {"error": "unexpected request"}
                response = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(200 if "error" not in body else 500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)


@pytest.fixture
def raw_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RawModelServer]:
    monkeypatch.chdir(tmp_path)
    for key in os.environ:
        if key.upper().endswith("_PROXY"):
            monkeypatch.delenv(key)
    server = RawModelServer()
    endpoint = f"http://127.0.0.1:{server.server.server_port}/v1"
    monkeypatch.setitem(DEFAULT_BASE_URLS, "deepseek", endpoint)
    for key, value in {
        "PROVIDER": "deepseek", "NAME": "deepseek-flash", "BASE_URL": endpoint,
        "API_KEY": "dummy-key", "INPUT_PRICE_PER_MTOK": "0.3",
        "OUTPUT_PRICE_PER_MTOK": "1.2", "TIMEOUT_S": "3",
    }.items():
        monkeypatch.setenv(f"YESCC_MODEL_L3_{key}", value)
    original_send = httpx.Client.send

    def local_only(client: httpx.Client, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        assert request.url.port == server.server.server_port
        return original_send(client, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", local_only)
    server.thread.start()
    try:
        yield server
    finally:
        server.server.shutdown()
        server.server.server_close()
        server.thread.join()


def _arguments(mode: str = "run", budget: str = "0.005", rounds: str = "2") -> list[str]:
    return (["run"] if mode == "run" else []) + [
            "idea.txt", "--play", "--provider", "deepseek", "--model", "deepseek-flash",
            "--thinking", "enabled", "--reasoning-effort", "low", "--max-output-tokens", "4096",
            "--budget", budget, "--max-rounds", rounds, "--no-cache"]


def _report(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / ".yescc/idea/report.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _captured_lengths(), ids=[c[0] for c in _captured_lengths()])
def test_captured_length_preserves_original_output_and_explains_actual_request(
    case: tuple[str, str, dict[str, Any]], tmp_path: Path, raw_model: RawModelServer,
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    case_id, text, body = case
    source = tmp_path / "idea.txt"
    source.write_bytes(text.encode("utf-8"))
    raw_model.bodies.append(copy.deepcopy(body))
    assert main(_arguments()) == 0
    output = capfdbinary.readouterr()
    assert output.out == text.encode("utf-8") + os.linesep.encode()
    assert source.read_bytes() == text.encode("utf-8")
    assert len(raw_model.requests) == 1
    request = raw_model.requests[0]
    cap = request["max_tokens"]
    assert 1024 < cap < 4096
    assert request["thinking"] == {"type": "enabled"}
    assert request["reasoning_effort"] == "low"
    assert "temperature" not in request
    saved = _report(tmp_path)
    assert saved["result"]["fallback"]["kind"] == "print_input"
    assert saved["model"]["calls"] == saved["budget"]["attempts"] == 1
    assert saved["model"]["cost_known"] is True
    response = saved["model"]["responses"][0]
    assert response["configured_settings"]["max_tokens"] == 4096
    assert response["request_settings"]["max_tokens"] == cap
    assert response["completion_tokens"] == body["usage"]["completion_tokens"]
    reasoning = body["usage"]["completion_tokens_details"]["reasoning_tokens"]
    assert response["reasoning_tokens"] == reasoning
    hints = saved["result"]["fallback"]["guidance"]
    rendered = "\n".join(hints)
    assert "finish_reason=length" in rendered
    assert f"实际请求上限为 {cap} tokens" in rendered
    assert "配置上限为 4096 tokens，受本次预算裁剪" in rendered
    assert f"{reasoning} 个推理 tokens" in rendered
    assert saved["budget"]["blocked_reason"] == "剩余费用不足以支付输入的最大估算费用"
    assert "剩余预算不足以预留下次请求的输入费用" in rendered
    assert "--thinking disabled --reasoning-effort none" in rendered
    assert output.err == b""
    assert "reasoning_content" not in json.dumps(saved)
    assert "dummy-key" not in json.dumps(saved)
    evidence = os.environ.get("YESCC_DEGRADATION_EVIDENCE_DIR")
    if evidence:
        target = Path(evidence)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{case_id}.json").write_text(json.dumps({
            "case_id": case_id, "source_archive_sha256": ARCHIVE_SHA,
            "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "replayed_response": body, "request": request,
            "stdout_base64": base64.b64encode(output.out).decode("ascii"),
            "stderr_base64": base64.b64encode(output.err).decode("ascii"),
            "actual_http_requests_to_local_fake": len(raw_model.requests),
            "report": saved,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_build_json_and_explain_share_guidance_without_extra_requests(
    tmp_path: Path, raw_model: RawModelServer, capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    _, text, body = _captured_lengths()[0]
    (tmp_path / "idea.txt").write_bytes(text.encode("utf-8"))
    raw_model.bodies.append(copy.deepcopy(body))
    assert main([*_arguments("build"), "--json"]) == 0
    output = capfdbinary.readouterr()
    assert output.err == b""
    saved = json.loads(output.out)
    assert saved == _report(tmp_path)
    report_path = tmp_path / ".yescc/idea/report.json"
    original = report_path.read_bytes()
    assert main(["idea.txt", "--explain"]) == 0
    explained = capfdbinary.readouterr()
    assert explained.err == b""
    for hint in saved["result"]["fallback"]["guidance"]:
        assert hint.encode("utf-8") in explained.out
    assert report_path.read_bytes() == original
    assert len(raw_model.requests) == 1


def test_length_then_success_keeps_program_bytes_and_does_not_show_failed_guidance(
    tmp_path: Path, raw_model: RawModelServer, capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    text = "雨伞梦见一杯茶"
    (tmp_path / "idea.txt").write_bytes(text.encode("utf-8"))
    failed = copy.deepcopy(_captured_lengths()[0][2])
    failed["usage"] = {"prompt_tokens": 10, "completion_tokens": 32,
                       "completion_tokens_details": {"reasoning_tokens": 32}}
    failed["choices"][0]["message"]["reasoning_content"] = "PRIVATE_REASONING"
    source = ('#include <stdio.h>\nint main(void) { fputs("GOOD\\n", stdout); '
              'fputs("RAW\\n", stderr); return 0; }\n')
    success = {"model": "deepseek-flash", "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"source": source}),
    }}], "usage": {"prompt_tokens": 10, "completion_tokens": 64}}
    raw_model.bodies.extend([failed, success])
    assert main(_arguments(budget="0.05")) == 0
    output = capfdbinary.readouterr()
    assert output.out == b"GOOD" + os.linesep.encode()
    assert output.err.startswith(b"RAW" + os.linesep.encode())
    assert b"finish_reason=length" not in output.err
    assert b"PRIVATE_REASONING" not in output.out + output.err
    saved = _report(tmp_path)
    assert saved["result"]["fallback"]["kind"] == "model_creative"
    assert "guidance" not in saved["result"]["fallback"]
    assert len(raw_model.requests) == saved["model"]["calls"] == 2
    assert "PRIVATE_REASONING" not in json.dumps(saved)
    assert (tmp_path / "idea.txt").read_bytes() == text.encode("utf-8")


def test_unknown_usage_does_not_describe_known_subtotal_as_total_cost(
    tmp_path: Path, raw_model: RawModelServer, capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    _, text, body = _captured_lengths()[0]
    (tmp_path / "idea.txt").write_bytes(text.encode("utf-8"))
    unknown = copy.deepcopy(body)
    unknown["usage"] = {}
    raw_model.bodies.append(unknown)
    assert main(_arguments(rounds="1")) == 0
    output = capfdbinary.readouterr()
    saved = _report(tmp_path)
    assert saved["model"]["cost_known"] is False
    assert saved["model"]["cost_usd"] == 0
    assert saved["model"]["responses"][0]["usage_known"] is False
    hints = saved["result"]["fallback"]["guidance"]
    rendered = "\n".join(hints)
    assert "模型总费用未知" in rendered and "仅为已知费用小计" in rendered
    assert output.err == b""
    assert len(raw_model.requests) == 1


def test_native_success_never_calls_model_or_adds_degradation_messages(
    tmp_path: Path, raw_model: RawModelServer, monkeypatch: pytest.MonkeyPatch,
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    source = tmp_path / "native.c"
    source.write_bytes(b'#include <stdio.h>\nint main(void) { fputs("NATIVE\\n", stdout); '
                       b'fputs("NATIVEERR\\n", stderr); return 0; }\n')
    original = source.read_bytes()
    monkeypatch.setenv("YESCC_MODEL_L3_REASONING_EFFORT", "invalid-only-if-used")
    assert main(["run", "native.c"]) == 0
    output = capfdbinary.readouterr()
    assert output.out == b"NATIVE" + os.linesep.encode()
    assert output.err == b"NATIVEERR" + os.linesep.encode()
    assert source.read_bytes() == original and not raw_model.requests
