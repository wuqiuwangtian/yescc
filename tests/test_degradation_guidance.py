"""Explain only observed response limits; keep unknowns and previous attempts separate."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from unittest.mock import patch

import httpx
import pytest

from yescc.fallback.guidance import (
    creative_validation_guidance,
    degradation_guidance,
    model_output_guidance,
)
from yescc.model.base import BudgetExceeded, ModelResponseError
from yescc.model.config import ModelSettings
from yescc.model.session import ModelSession


def truncated(**changes) -> dict:
    return {
        "provider": "deepseek", "finish_reason": "length", "reasoning_tokens": 2000,
        "configured_settings": {"max_tokens": 4096, "thinking": {"type": "enabled"}},
        "request_settings": {"max_tokens": 2048, "thinking": {"type": "enabled"}},
        **changes,
    }


@pytest.mark.parametrize("changes", [
    {"performed": False}, {"performed": 1}, {"passed": True},
    {"passed": None}, {"exit_code": 130},
])
def test_rejection_guidance_requires_a_real_rejected_run(changes) -> None:
    validation = {"performed": True, "passed": False, "exit_code": 1, **changes}
    assert creative_validation_guidance([{"kind": "model", "validation": validation}]) == []


def test_rejection_history_survives_later_compile_failure_without_printing_program_text() -> None:
    attempts = [{
        "kind": "model", "validation": {
            "performed": True, "passed": False, "exit_code": 7,
            "reasons": ["exit_nonzero", "stdout_empty", "UNTRUSTED_REASON"],
        },
        "undefined_behavior_check": {
            "performed": True, "mode": "trap", "stderr": "UNTRUSTED_DIAGNOSTIC",
            "diagnostic_observed": True, "ub_detected": None,
        },
        "stdout": "REJECTED_PROGRAM_OUTPUT",
    }, {"kind": "model", "exit_code": 1, "stderr": "later compile failure"}]
    original = copy.deepcopy(attempts)
    text = "\n".join(creative_validation_guidance(attempts))
    assert "trap" in text and "退出码 7" in text and "没有可见标准输出" in text
    assert "--explain" in text and "UNTRUSTED" not in text and "REJECTED_PROGRAM" not in text
    assert "确认UB" not in text and "确认未定义行为" not in text
    assert attempts == original


def test_requesting_a_check_does_not_claim_it_ran_or_override_budget_reason() -> None:
    text = "\n".join(degradation_guidance(
        "idea", language=None, kind="print_input", offline=False, model_available=True,
        model_attempted=True, budget_blocked=True, rule_builds=0,
        budget_reason="已达到最大候选尝试次数 1",
        attempts=[{
            "kind": "model", "validation": {
                "performed": True, "passed": False, "exit_code": -9,
                "reasons": ["timeout", "output_limit", "stdout_invalid_utf8"],
            },
            "undefined_behavior_check": {"requested": True, "performed": False, "mode": "trap"},
        }],
    ))
    assert "运行超时" in text and "输出超过额度" in text and "不是有效 UTF-8" in text
    assert "最大候选尝试次数 1" in text and "trap" not in text


def test_native_or_unperformed_attempts_do_not_become_creative_rejection_evidence() -> None:
    assert creative_validation_guidance([
        {"kind": "native", "validation": {"performed": True, "passed": False}},
        {"kind": "model", "source_checks": {"performed": True, "passed": False}},
        {"kind": "model", "validation": {"performed": False, "reason": "build_only"}},
    ]) == []


def test_actual_limits_reasoning_and_action_are_based_on_recorded_response() -> None:
    model = {"calls": 1, "cost_known": True, "responses": [truncated()]}
    original = copy.deepcopy(model)
    text = "\n".join(model_output_guidance(model))
    assert "finish_reason=length" in text
    assert "配置上限为 4096" in text and "实际请求上限为 2048" in text
    assert "预算裁剪" in text and "2000 个推理 tokens" in text
    assert "--thinking disabled --reasoning-effort none" in text
    assert "不会自动增加费用上限" in text
    assert "总费用未知" not in text
    assert model == original


def test_old_response_does_not_borrow_latest_configuration_or_invent_reasoning() -> None:
    model = {
        "calls": 2, "cost_known": True, "configured_settings": {"max_tokens": 8192},
        "responses": [{"finish_reason": "length", "request_settings": {"max_tokens": 1024}}],
    }
    text = "\n".join(model_output_guidance(model))
    assert "实际请求上限为 1024" in text
    assert "8192" not in text and "预算裁剪" not in text
    assert "推理 tokens" not in text and "--thinking disabled" not in text


@pytest.mark.parametrize("response_source", ["cache", None])
def test_configuration_and_stale_finish_reason_are_not_http_evidence(response_source) -> None:
    assert model_output_guidance({
        "finish_reason": "length", "response_source": response_source,
        "configured_settings": {"max_tokens": 4096}, "responses": [],
    }) == []


def test_legacy_http_finish_can_be_explained_without_pairing_new_config() -> None:
    text = "\n".join(model_output_guidance({
        "finish_reason": "length", "response_source": "http", "responses": [],
        "configured_settings": {"max_tokens": 8192},
        "request_settings": {"max_tokens": 1024},
    }))
    assert "finish_reason=length" in text and "实际请求上限为 1024" in text
    assert "8192" not in text and "预算裁剪" not in text


def test_openai_completion_limit_does_not_suggest_deepseek_only_flag() -> None:
    response = truncated(
        provider="openai", configured_settings={"max_completion_tokens": 4096,
                                                "reasoning_effort": "low"},
        request_settings={"max_completion_tokens": 2048, "reasoning_effort": "low"},
    )
    text = "\n".join(model_output_guidance({"responses": [response]}))
    assert "4096" in text and "2048" in text and "降低推理等级" in text
    assert "--thinking" not in text


def test_earlier_deepseek_response_does_not_promise_global_flag_compatibility() -> None:
    text = "\n".join(model_output_guidance({
        "provider": "openai", "responses": [truncated()],
    }))
    assert "仅使用 DeepSeek 时" in text and "多供应商配置请分别调整" in text


@pytest.mark.parametrize("cap", [None, False, -1, "2048"])
def test_unknown_or_invalid_token_limits_are_not_printed_as_observations(cap) -> None:
    response = truncated(configured_settings=None, request_settings={"max_tokens": cap},
                         reasoning_tokens=None)
    text = "\n".join(model_output_guidance({"responses": [response]}))
    assert "finish_reason=length" in text
    assert "实际请求上限为" not in text and "预算裁剪" not in text
    assert "个推理 tokens" not in text


def test_unknown_total_cost_is_explicit_even_without_truncation() -> None:
    text = "\n".join(model_output_guidance({"calls": 1, "cost_known": False, "responses": []}))
    assert "模型总费用未知" in text and "仅为已知费用小计" in text
    assert "finish_reason" not in text


def test_offline_path_does_not_reuse_a_historical_model_failure() -> None:
    hints = degradation_guidance(
        "idea", language=None, kind="print_input", offline=True, model_available=True,
        model_attempted=False, budget_blocked=False, rule_builds=0,
        model={"calls": 1, "responses": [truncated()]},
    )
    assert any("本次未发起模型请求" in hint for hint in hints)
    assert not any("length" in hint or "预算裁剪" in hint for hint in hints)


def test_actual_budget_reason_is_preserved_alongside_truncation() -> None:
    reason = "剩余费用不足以支付输入的最大估算费用"
    hints = degradation_guidance(
        "idea", language=None, kind="print_input", offline=False, model_available=True,
        model_attempted=True, budget_blocked=True, rule_builds=0,
        model={"calls": 1, "responses": [truncated()]}, budget_reason=reason,
    )
    assert any("不足以预留下次请求的输入费用" in hint for hint in hints)
    assert not any("详见报告 budget 字段" in hint for hint in hints)


def test_session_response_metadata_survives_next_preflight_configuration_change() -> None:
    settings = ModelSettings(
        "deepseek", "http://127.0.0.1:9/v1", "test-model", "dummy-key", .3, 1.2, 1,
        reasoning_effort="low", max_output_tokens=4096,
    )
    with patch("yescc.model.session.load_settings", return_value=settings):
        session = ModelSession(offline=False, no_cache=True, budget_usd=.005, max_rounds=2)
    response = httpx.Response(200, json={
        "model": "served", "choices": [{"finish_reason": "length", "message": {
            "content": "", "reasoning_content": "PRIVATE_REASONING",
        }}], "usage": {"prompt_tokens": 420, "completion_tokens": 3400,
                       "completion_tokens_details": {"reasoning_tokens": 3400}},
    })
    with patch("httpx.Client.post", return_value=response) as post:
        with pytest.raises(ModelResponseError):
            session.generate("月亮摸鱼", creative=True)
        session.settings_l3 = replace(settings, reasoning_effort=None, max_output_tokens=8192)
        with pytest.raises(BudgetExceeded):
            session.generate("月亮摸鱼", creative=True)
    assert post.call_count == 1
    record = session.stats.responses[0]
    assert record["configured_settings"]["max_tokens"] == 4096
    assert session.stats.configured_settings["max_tokens"] == 8192
    assert record["completion_tokens"] == record["reasoning_tokens"] == 3400
    assert record["usage_known"] is True and record["provider"] == "deepseek"
    text = "\n".join(model_output_guidance(session.stats.as_dict()))
    assert "配置上限为 4096" in text and "8192" not in text
    assert "PRIVATE_REASONING" not in json.dumps(session.stats.as_dict())
