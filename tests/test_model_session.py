"""模型候选必须有限收敛，预算在发请求前生效，路由与报告可核对。"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from tests.conftest import FakeModelServer
from tests.e2e.test_model_flow import BROKEN_C, HELLO_SOURCE, REPAIR_DIFF
from yescc.model.base import BudgetExceeded, Completion, ModelError
from yescc.model.config import ModelSettings, load_settings
from yescc.model.session import ModelSession


def _session(**kwargs) -> ModelSession:
    return ModelSession(
        offline=False,
        no_cache=kwargs.get("no_cache", True),
        budget_usd=kwargs.get("budget", 0.05),
        max_rounds=2,
        model_override=kwargs.get("model"),
    )


def test_cached_invalid_patch_is_bounded_and_discarded(fake_model: FakeModelServer) -> None:
    session = _session(no_cache=False)
    invalid = {"diff": "@@ -1 +1 @@\n-wrong\n+replacement\n"}
    with (
        patch(
            "yescc.model.session.load_cache",
            side_effect=[invalid, invalid, AssertionError("超过候选尝试上限")],
        ) as cached,
        patch("yescc.model.session.discard_cache") as discarded,
    ):
        assert session.repair(BROKEN_C, "main.c", "error") is None
    assert cached.call_count == discarded.call_count == 2
    assert session.stats.cache_hits == 2
    assert not fake_model.requests


def test_cache_hits_obey_deadline(fake_model: FakeModelServer) -> None:
    session = _session(no_cache=False)
    session.budget.started -= session.budget.max_wall_s + 1
    with patch("yescc.model.session.load_cache") as cached, pytest.raises(BudgetExceeded):
        session.generate("输出 hello", creative=False)
    cached.assert_not_called()
    assert not fake_model.requests


@pytest.mark.parametrize("first", [{"diff": ""}, {"diff": "invalid patch"}])
def test_failed_l2_repair_escalates_to_l3(first, fake_model: FakeModelServer) -> None:
    fake_model.responses.extend([first, {"diff": REPAIR_DIFF}])
    repaired = _session().repair(BROKEN_C, "main.c", "error")
    assert repaired is not None and 'printf("hi\\n");' in repaired.patched_source
    assert [request["model"] for request in fake_model.requests] == ["fake-l2", "fake-l3"]


def test_compiler_feedback_escalates_after_valid_l2_patch(fake_model: FakeModelServer) -> None:
    fake_model.responses.extend([{"diff": REPAIR_DIFF}, {"diff": REPAIR_DIFF}])
    session = _session()
    assert session.repair(BROKEN_C, "main.c", "error") is not None
    assert session.repair(BROKEN_C, "main.c", "error", feedback="still invalid") is not None
    assert [request["model"] for request in fake_model.requests] == ["fake-l2", "fake-l3"]


def test_explicit_model_bypasses_l2(fake_model: FakeModelServer) -> None:
    fake_model.responses.append({"diff": REPAIR_DIFF})
    assert _session(model="chosen-model").repair(BROKEN_C, "main.c", "error") is not None
    assert fake_model.requests[0]["model"] == "chosen-model"


def test_failed_requests_consume_call_budget(fake_model: FakeModelServer) -> None:
    session = _session()
    with patch(
        "yescc.model.session.OpenAICompatibleProvider.complete", side_effect=ModelError("HTTP 500")
    ) as complete:
        assert session.repair(BROKEN_C, "main.c", "error") is None
        with pytest.raises(BudgetExceeded):
            session.generate("hello", creative=True)
    assert complete.call_count == session.budget.calls == session.stats.calls == 2
    assert session.budget.spent_usd > 0
    assert session.stats.cost_known is False


def test_budget_blocks_unaffordable_input_before_request(fake_model: FakeModelServer) -> None:
    with pytest.raises(BudgetExceeded):
        _session(budget=0.0000001).generate("输出 hello", creative=False)
    assert not fake_model.requests


def test_budget_caps_requested_output(fake_model: FakeModelServer) -> None:
    fake_model.responses.append({"source": HELLO_SOURCE})
    session = _session(budget=0.002)
    assert session.generate("输出 hello", creative=False) is not None
    assert 0 < fake_model.requests[0]["max_tokens"] < 1024
    assert session.budget.spent_usd <= 0.002


def test_provider_overcharge_rejects_result(fake_model: FakeModelServer) -> None:
    session = _session(budget=0.01)
    completion = Completion('{"source": "int main(void) {}"}', 1, 1, 0.12, 1)
    with (
        patch("yescc.model.session.OpenAICompatibleProvider.complete", return_value=completion),
        pytest.raises(BudgetExceeded, match="超过任务上限"),
    ):
        session.generate("输出 hello", creative=False)
    assert session.stats.cost_usd == pytest.approx(0.12)


def test_unknown_cloud_price_blocks_upload(fake_model: FakeModelServer) -> None:
    configured = load_settings("l3")
    assert configured is not None
    remote = replace(configured, base_url="https://example.invalid/v1", input_price_per_mtok=None)
    with patch("yescc.model.session.load_settings", return_value=remote):
        session = _session()
    with pytest.raises(BudgetExceeded, match="价格未知"):
        session.generate("输出 hello", creative=False)
    assert session.stats.cloud_used is False
    assert session.stats.uploaded_bytes == 0
    assert not fake_model.requests


def test_unknown_local_price_is_reported_as_unknown(fake_model: FakeModelServer) -> None:
    configured = load_settings("l3")
    assert configured is not None
    local = replace(configured, input_price_per_mtok=None, output_price_per_mtok=None)
    fake_model.responses.append({"source": HELLO_SOURCE})
    with patch("yescc.model.session.load_settings", return_value=local):
        session = _session()
    assert session.generate("输出 hello", creative=False) is not None
    assert session.stats.as_dict()["cost_known"] is False
    assert session.stats.cloud_used is False
    assert session.stats.uploaded_bytes == 0


def test_cloud_upload_and_request_timeout_are_recorded(fake_model: FakeModelServer) -> None:
    configured = load_settings("l3")
    assert configured is not None
    remote = replace(configured, base_url="https://example.invalid/v1", timeout_s=60)
    completion = Completion('{"source": "int main(void) {}"}', 1, 1, 0.000002, 1)
    with patch("yescc.model.session.load_settings", return_value=remote):
        session = _session()
    session.budget.max_wall_s = 0.5
    with (
        patch("yescc.model.session._show_cloud_notice"),
        patch("yescc.model.session.OpenAICompatibleProvider") as provider,
    ):
        provider.return_value.complete.return_value = completion
        assert session.generate("输出 hello", creative=False) is not None
    assert session.stats.cloud_used is True
    messages = provider.return_value.complete.call_args.args[0]
    assert session.stats.uploaded_bytes == sum(len(m.content.encode("utf-8")) for m in messages)
    assert 0 < provider.call_args.args[0].timeout_s <= 0.5


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": -1, "completion_tokens": 5}])
def test_missing_usage_keeps_cost_reservation(usage, fake_model: FakeModelServer) -> None:
    fake_model.usage = usage
    fake_model.responses.append({"source": HELLO_SOURCE})
    session = _session()
    assert session.generate("输出 hello", creative=False) is not None
    assert session.budget.spent_usd > 0
    assert session.stats.cost_known is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [("http://localhost:11434/v1", True), ("http://127.0.0.1:8000/v1", True),
     ("http://[::1]/v1", True), ("http://localhost.example.com/v1", False),
     ("http://127.0.0.1.example.com/v1", False)],
)
def test_local_classification_uses_hostname(url: str, expected: bool) -> None:
    settings = ModelSettings("test", url, "test", None, None, None, 1)
    assert settings.is_local is expected


@pytest.mark.parametrize("price", ["nan", "inf", "-1", "oops"])
def test_invalid_prices_are_unknown(
    price: str, fake_model: FakeModelServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YESCC_MODEL_L3_INPUT_PRICE_PER_MTOK", price)
    settings = load_settings("l3")
    assert settings is not None and settings.input_price_per_mtok is None


def test_provider_override_selects_provider_endpoint(fake_model: FakeModelServer) -> None:
    settings = load_settings("l3", provider_override="deepseek")
    assert settings is not None
    assert settings.base_url == "https://api.deepseek.com/v1"
    assert settings.model == "deepseek-chat"


def test_cache_is_invalidated_by_toolchain_change(fake_model: FakeModelServer) -> None:
    fake_model.responses.extend([{"source": HELLO_SOURCE}, {"source": HELLO_SOURCE}])
    first = _session(no_cache=False)
    first.toolchain = {"version": "1"}
    assert first.generate("输出 hello", creative=False) is not None
    second = _session(no_cache=False)
    second.toolchain = {"version": "2"}
    assert second.generate("输出 hello", creative=False) is not None
    assert len(fake_model.requests) == 2
