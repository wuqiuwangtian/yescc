"""测试夹具：本地假 OpenAI 兼容服务器，避免 CI 调用真实模型。"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_model_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """回归测试不继承开发者的付费模型配置、凭据与缓存。"""
    for name in os.environ:
        if name.startswith(("YESCC_MODEL_", "FCOMP_MODEL_")):
            monkeypatch.delenv(name)
    monkeypatch.delenv("YESCC_RUN_TIMEOUT_S", raising=False)
    monkeypatch.setenv("YESCC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("YESCC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YESCC_SUPPRESS_NOTICE", "1")


class FakeModelServer:
    """记录请求并返回预设 JSON 的本地服务。"""

    def __init__(self) -> None:
        self.responses: list[object] = []
        self.requests: list[dict[str, object]] = []
        self.usage: object = {"prompt_tokens": 10, "completion_tokens": 5}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    request = json.loads(raw.decode("utf-8"))
                except ValueError:
                    request = {}
                owner.requests.append(request)
                payload = owner.responses.pop(0) if owner.responses else {"source": ""}
                text = (
                    payload
                    if isinstance(payload, str)
                    else json.dumps(payload, ensure_ascii=False)
                )
                body = json.dumps(
                    {
                        "choices": [{"message": {"role": "assistant", "content": text}}],
                        "usage": owner.usage,
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/v1"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake_model(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeModelServer]:
    server = FakeModelServer()
    server.start()
    for tier in ("L2", "L3"):
        monkeypatch.setenv(f"YESCC_MODEL_{tier}_BASE_URL", server.base_url)
        monkeypatch.setenv(f"YESCC_MODEL_{tier}_NAME", f"fake-{tier.lower()}")
        monkeypatch.setenv(f"YESCC_MODEL_{tier}_API_KEY", "test-key")
        monkeypatch.setenv(f"YESCC_MODEL_{tier}_INPUT_PRICE_PER_MTOK", "1")
        monkeypatch.setenv(f"YESCC_MODEL_{tier}_OUTPUT_PRICE_PER_MTOK", "1")
    monkeypatch.setenv("YESCC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("YESCC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YESCC_SUPPRESS_NOTICE", "1")
    try:
        yield server
    finally:
        server.stop()
