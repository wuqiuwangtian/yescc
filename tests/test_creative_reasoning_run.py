"""Research orchestration refuses drift/replays and retains failed-task evidence."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
from bench import creative_reasoning_run as runner


def test_prepare_is_offline_and_creates_frozen_outside_snapshot(tmp_path: Path) -> None:
    workspace = tmp_path / "study"
    with (
        patch.object(runner, "_secret", side_effect=AssertionError("credential read")),
        patch("httpx.Client.send", side_effect=AssertionError("network forbidden")),
    ):
        freeze = runner.prepare(workspace)
    protocol, verified = runner.verify_workspace(workspace)
    assert verified == freeze
    assert protocol["product_commit"] == freeze["product_commit"]
    assert len(freeze["preflight"]["plans"]) == 42
    phase = json.loads((workspace / "phases/discovery.json").read_text(encoding="utf-8"))
    assert len(phase["review_mapping"]) == 42
    assert set(phase["cases"][0]) == {"id", "input"}
    with pytest.raises(FileExistsError):
        runner.prepare(workspace)


def test_prepare_rejects_inside_repository_and_external_fake_endpoint(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside"):
        runner.prepare(runner.ROOT / "dist/unwanted-runner-test")
    for endpoint in ("https://example.com/v1", "http://127.0.0.1:9/v1?x=1",
                     "http://127.0.0.1:9/other", "http://user@127.0.0.1:9/v1"):
        with pytest.raises(ValueError, match="loopback"):
            runner.prepare(tmp_path / "not-created", fake_endpoint=endpoint)
    assert not (tmp_path / "not-created").exists()


def test_frozen_bytes_are_checked_before_credentials_or_execution(tmp_path: Path) -> None:
    workspace = tmp_path / "study"
    runner.prepare(workspace)
    (workspace / "snapshot/src/yescc/model/prompts.py").write_text("changed")
    with (
        patch.object(runner, "_secret", side_effect=AssertionError("credential read")),
        patch.object(runner, "_execute", side_effect=AssertionError("execution")),
        pytest.raises(ValueError, match="frozen bytes"),
    ):
        runner.run_workspace(workspace)
    assert not (workspace / "discovery-started.json").exists()
    assert not (workspace / "reservations.jsonl").exists()


def test_environment_clears_model_proxy_and_python_inheritance(tmp_path: Path,
                                                             monkeypatch) -> None:
    monkeypatch.setenv("http_proxy", "http://external.invalid:9")
    monkeypatch.setenv("ALL_PROXY", "http://external.invalid:9")
    monkeypatch.setenv("YESCC_MODEL_L2_API_KEY", "not-used")
    monkeypatch.setenv("FCOMP_MODEL_API_KEY", "not-used")
    monkeypatch.setenv("PYTHONPATH", "/must/not/import")
    settings = {"model": "frozen", "input_price_per_mtok": .3,
                "output_price_per_mtok": 1.2, "request_timeout_s": 45, "run_timeout_s": 3}
    env = runner._environment(tmp_path, tmp_path / "task", settings,
                              "http://127.0.0.1:9/v1", "dummy", None)
    assert not any(k.upper().endswith("_PROXY") for k in env)
    assert "YESCC_MODEL_L2_API_KEY" not in env and "FCOMP_MODEL_API_KEY" not in env
    assert "/must/not/import" not in env["PYTHONPATH"]
    assert env["YESCC_MODEL_L3_API_KEY"] == "dummy"
    assert env["YESCC_MODEL_L3_TIMEOUT_S"] == "45"


def _mock_outcome(directory: Path, *, halt: str | None, known: bool = True) -> None:
    runner._write(directory / "guard-state.json", {
        "halted_reason": halt, "served_model": "frozen-served", "request_count": 1,
    })
    runner._write(directory / ".yescc/input/report.json", {
        "model": {"cost_known": known, "calls": 1},
        "result": {"generated": {"source": ".yescc/input/fallback.c"}},
    })
    (directory / ".yescc/input/fallback.c").write_text("int main(void){return 0;}\n")


def test_halted_task_stops_batch_and_keeps_reservation_raw_output(tmp_path: Path) -> None:
    workspace = tmp_path / "study"
    runner.prepare(workspace, fake_endpoint="http://127.0.0.1:9/v1")

    def execute(command, directory, env):
        _mock_outcome(directory, halt="unknown_usage", known=False)
        return 0, b"observed\n", b"diagnostic\n", None

    with (
        patch.object(runner, "_execute", side_effect=execute) as child,
        patch.object(runner, "_secret", side_effect=AssertionError("real key read")),
    ):
        summary = runner.run_workspace(workspace)
        assert child.call_count == 1
        with pytest.raises(FileExistsError):
            runner.run_workspace(workspace)
        assert child.call_count == 1
    assert summary["planned_tasks"] == 42 and summary["completed_tasks"] == 1
    assert summary["stopped_reason"] == "unknown_usage"
    raw_ledger = (workspace / "reservations.jsonl").read_text(encoding="utf-8")
    ledger = [json.loads(x) for x in raw_ledger.splitlines()]
    assert len(ledger) == 1 and ledger[0]["reserved_usd"] == .005
    directory = workspace / "runs" / summary["outcomes"][0]
    assert (directory / "program.stdout").read_bytes() == b"observed\n"
    assert (directory / "program.stderr").read_bytes() == b"diagnostic\n"
    assert (directory / "generated.c").is_file()
    outcome = json.loads((directory / "outcome.json").read_text(encoding="utf-8"))
    assert outcome["source_unchanged"] and outcome["report"]["model"]["cost_known"] is False
    assert "test-key" not in (directory / "environment.json").read_text(encoding="utf-8")


def test_all_tasks_share_served_model_and_append_full_reservations(tmp_path: Path) -> None:
    workspace = tmp_path / "study"
    runner.prepare(workspace, fake_endpoint="http://127.0.0.1:9/v1")
    observed = []

    def execute(command, directory, env):
        observed.append(env["YESCC_EVAL_EXPECTED_MODEL"])
        _mock_outcome(directory, halt=None)
        return 0, b"ok\n", b"", None

    with patch.object(runner, "_execute", side_effect=execute):
        summary = runner.run_workspace(workspace)
    assert observed == [""] + ["frozen-served"] * 41
    assert summary["completed_tasks"] == summary["planned_tasks"] == 42
    assert summary["reservation_slots"] == 42 and summary["stopped_reason"] is None
    assert len((workspace / "reservations.jsonl").read_text(encoding="utf-8").splitlines()) == 42


def test_host_timeout_cleans_child_and_preserves_partial_bytes(tmp_path: Path) -> None:
    before = time.monotonic()
    code, stdout, stderr, reason = runner._execute(
        [sys.executable, "-c", "import time; print('partial',flush=True); time.sleep(10)"],
        tmp_path, dict(os.environ), timeout=.2,
    )
    assert code != 0 and stdout == b"partial" + os.linesep.encode()
    assert stderr == b"" and reason == "host_timeout"
    assert time.monotonic() - before < 5


def test_confirmation_rejects_no_winner_before_opening_sealed(tmp_path: Path) -> None:
    workspace = tmp_path / "study"
    runner.prepare(workspace)
    selection = tmp_path / "selection.json"
    runner._write(selection, {"selected_profile": None, "gates": {}})
    protocol = json.loads((workspace / "snapshot" / runner.PROTOCOL).read_text(encoding="utf-8"))
    sealed = protocol["confirmation"]["sealed_path_outside_repository"]
    original_read = Path.read_bytes

    def no_sealed(path):
        assert str(path) != sealed, "sealed input opened"
        return original_read(path)

    with patch.object(Path, "read_bytes", no_sealed), pytest.raises(ValueError, match="qualified"):
        runner.prepare_confirmation(workspace, selection)


@pytest.mark.e2e
@pytest.mark.parametrize("known_first,expected_tasks", [(False, 1), (True, 2)])
def test_actual_snapshot_cli_known_then_unknown_stops_local_http(
    tmp_path: Path, known_first: bool, expected_tasks: int,
) -> None:
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = {"model": "local-served", "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"source": '#include <stdio.h>\nint main(void) '
                                       '{ puts("LOCAL"); return 0; }\n'}),
            }}]}
            if known_first and len(requests) == 1:
                body["usage"] = {"prompt_tokens": 10, "completion_tokens": 5}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        workspace = tmp_path / "study"
        runner.prepare(workspace, fake_endpoint=f"http://127.0.0.1:{server.server_port}/v1")
        summary = runner.run_workspace(workspace)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert len(requests) == expected_tasks
    assert summary["completed_tasks"] == expected_tasks
    assert summary["stopped_reason"] == "unknown_usage"
    if known_first:
        first_path = workspace / "runs" / summary["outcomes"][0] / "outcome.json"
        first = json.loads(first_path.read_text(encoding="utf-8"))
        assert first["guard"]["halted_reason"] is None
        assert first["guard"]["served_model"] == "local-served"
        assert first["report"]["model"]["cost_known"] is True
    directory = workspace / "runs" / summary["outcomes"][-1]
    assert (directory / "program.stdout").read_bytes() == b"LOCAL" + os.linesep.encode()
    saved = json.loads((directory / "outcome.json").read_text(encoding="utf-8"))
    assert saved["report"]["model"]["cost_known"] is False
    raw_events = (directory / "provider-events.jsonl").read_text(encoding="utf-8")
    events = [json.loads(s) for s in raw_events.splitlines()]
    assert sum(e["event"] == "request" for e in events) == 1
    assert events[-1]["usage_known"] is False
