"""模型会话：路由、预算、缓存、隐私提示与两级调用。"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import platformdirs

from yescc.model.base import (
    BudgetExceeded,
    Message,
    ModelError,
    ModelResponseError,
    ModelUnavailable,
)
from yescc.model.budget import Budget
from yescc.model.cache import cache_key
from yescc.model.cache import discard as discard_cache
from yescc.model.cache import load as load_cache
from yescc.model.cache import store as store_cache
from yescc.model.config import ModelSettings, load_settings
from yescc.model.openai_compat import OpenAICompatibleProvider
from yescc.model.prompts import (
    PROMPT_VERSION,
    generate_messages,
    parse_json_object,
    repair_messages,
)
from yescc.patch import PatchError, apply_unified_diff


@dataclass
class ModelStats:
    """一次任务内的模型使用统计。"""

    provider: str | None = None
    tier: str | None = None
    name: str | None = None
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    cache_hits: int = 0
    duration_ms: int = 0
    errors: list[str] = field(default_factory=list)
    cost_known: bool = True
    cloud_used: bool = False
    uploaded_bytes: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "tier": self.tier,
            "name": self.name,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "cache_hits": self.cache_hits,
            "duration_ms": self.duration_ms,
            "errors": self.errors[:5],
            "cost_known": self.cost_known,
        }


@dataclass(frozen=True)
class ModelRepair:
    patched_source: str
    assumptions: list[str]
    diff: str


@dataclass(frozen=True)
class ModelProgram:
    source: str
    assumptions: list[str]
    io_contract: str


def _notice_seen_path() -> Path:
    override = os.environ.get("YESCC_CONFIG_DIR")
    root = Path(override) if override else Path(platformdirs.user_config_dir("yescc"))
    return root / "cloud_notice_seen"


def _show_cloud_notice(settings: ModelSettings) -> None:
    if settings.is_local or os.environ.get("YESCC_SUPPRESS_NOTICE") == "1":
        return
    try:
        marker = _notice_seen_path()
        if marker.exists():
            return
        print(
            "yescc: 首次使用云端模型，将把任务所需的源码/输入与诊断发送到 "
            f"{settings.base_url}（模型 {settings.model}）。可用 --offline 禁止联网。",
            file=sys.stderr,
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("seen\n", encoding="utf-8")
    except OSError:
        return


class ModelSession:
    """一次任务内的模型调用器。"""

    def __init__(
        self,
        *,
        offline: bool,
        no_cache: bool,
        budget_usd: float,
        max_rounds: int,
        provider_override: str | None = None,
        model_override: str | None = None,
    ) -> None:
        self.offline = offline
        self.no_cache = no_cache
        self.max_rounds = max_rounds
        self.settings_l2 = (
            None if provider_override or model_override else load_settings("l2")
        )
        self.settings_l3 = load_settings(
            "l3", provider_override=provider_override, model_override=model_override
        )
        self.budget = Budget(max_usd=budget_usd, max_rounds=max_rounds)
        self.stats = ModelStats()
        self.attempts = 0
        self.repair_attempts = 0
        self.last_cache_key: str | None = None
        self.budget_reason: str | None = None
        self.toolchain: dict[str, str] = {}

    @property
    def available(self) -> bool:
        return not self.offline and (self.settings_l2 is not None or self.settings_l3 is not None)

    def _select(self, kind: str, size: int, errors: int) -> tuple[str, ModelSettings]:
        if (
            kind == "repair"
            and self.settings_l2 is not None
            and size <= 8192
            and errors <= 3
            and self.repair_attempts == 0
        ):
            return "l2", self.settings_l2
        if self.settings_l3 is not None:
            return "l3", self.settings_l3
        if self.settings_l2 is not None:
            return "l2", self.settings_l2
        raise ModelUnavailable("没有可用的模型配置")

    def discard_response(self) -> None:
        if self.last_cache_key is not None and not self.no_cache:
            discard_cache(self.last_cache_key)

    def _request_limit(
        self, settings: ModelSettings, messages: list[Message]
    ) -> tuple[int, float, bool]:
        """按 UTF-8 字节数和协议余量保守估算输入，限制输出 token。"""
        self.budget.check()
        input_price = settings.input_price_per_mtok
        output_price = settings.output_price_per_mtok
        known = input_price is not None and output_price is not None
        if not known:
            if not settings.is_local:
                raise BudgetExceeded("云端模型价格未知，请配置输入和输出的 USD/Mtok 单价")
            return 1024, 0.0, False
        assert input_price is not None and output_price is not None
        encoded = json.dumps(
            [{"role": item.role, "content": item.content} for item in messages],
            ensure_ascii=False,
        ).encode("utf-8")
        input_bound = (len(encoded) + 1024) * input_price / 1_000_000
        remaining = self.budget.max_usd - self.budget.spent_usd - input_bound
        if remaining < 0:
            raise BudgetExceeded("剩余费用不足以支付输入的最大估算费用")
        max_tokens = min(1024, int(remaining * 1_000_000 / output_price)) if output_price else 1024
        if max_tokens < 1:
            raise BudgetExceeded("剩余费用不足以生成一个输出 token")
        return max_tokens, input_bound + max_tokens * output_price / 1_000_000, True

    def _call(
        self,
        *,
        kind: str,
        settings: ModelSettings,
        tier: str,
        messages: list[Message],
        cache_payload: dict[str, object],
    ) -> dict[str, object]:
        if self.offline:
            raise ModelUnavailable("离线模式禁用模型请求")
        self.budget.check_deadline()
        if self.attempts >= self.max_rounds:
            raise BudgetExceeded(f"已达到最大候选尝试次数 {self.max_rounds}")
        key = cache_key(
            {
                "kind": kind,
                "prompt_version": PROMPT_VERSION,
                "provider": settings.provider,
                "base_url": settings.base_url,
                "model": settings.model,
                "toolchain": self.toolchain,
                "payload": cache_payload,
            }
        )
        self.last_cache_key = key
        self.stats.provider = settings.provider
        self.stats.tier = tier
        self.stats.name = settings.model
        if not self.no_cache:
            cached = load_cache(key)
            self.budget.check_deadline()
            if cached is not None:
                self.stats.cache_hits += 1
                self.attempts += 1
                return cached

        max_tokens, reserved, prices_known = self._request_limit(settings, messages)
        self.budget.reserve(reserved)
        self.attempts += 1
        _show_cloud_notice(settings)
        self.stats.calls += 1
        if not settings.is_local:
            self.stats.cloud_used = True
            self.stats.uploaded_bytes += sum(len(item.content.encode("utf-8")) for item in messages)
        started = time.perf_counter()
        try:
            completion = OpenAICompatibleProvider(
                replace(settings, timeout_s=min(settings.timeout_s, self.budget.remaining_s))
            ).complete(
                messages, max_tokens=max_tokens, temperature=0.0 if kind != "creative" else 0.4
            )
        except ModelError:
            self.stats.cost_known = False
            raise
        finally:
            self.stats.duration_ms += int((time.perf_counter() - started) * 1000)
        self.stats.prompt_tokens += completion.prompt_tokens
        self.stats.completion_tokens += completion.completion_tokens
        known = prices_known and completion.usage_known
        self.stats.cost_known = self.stats.cost_known and known
        if known:
            self.stats.cost_usd += completion.cost_usd
            self.budget.settle(reserved, completion.cost_usd)
        self.budget.check_deadline()

        data = parse_json_object(completion.text)
        if not self.no_cache:
            store_cache(key, data)
        return data

    def repair(
        self,
        source: str,
        filename: str,
        diagnostics: str,
        feedback: str = "",
    ) -> ModelRepair | None:
        while self.attempts < self.max_rounds:
            self.budget.check_deadline()
            try:
                tier, settings = self._select("repair", len(source), diagnostics.count("error"))
            except ModelUnavailable:
                return None
            messages = repair_messages(source, filename, diagnostics, feedback)
            self.repair_attempts += 1
            try:
                data = self._call(
                    kind="repair",
                    settings=settings,
                    tier=tier,
                    messages=messages,
                    cache_payload={
                        "source": source,
                        "filename": filename,
                        "diagnostics": diagnostics,
                        "feedback": feedback,
                    },
                )
            except BudgetExceeded:
                raise
            except ModelError as exc:
                self.stats.errors.append(str(exc))
                feedback = str(exc)
                continue
            diff = data.get("diff")
            if not isinstance(diff, str) or not diff.strip():
                self.discard_response()
                feedback = "模型未提供可用补丁"
                continue
            try:
                patched = apply_unified_diff(source, diff)
            except PatchError as exc:
                feedback = str(exc)
                self.stats.errors.append(f"patch: {exc}")
                self.discard_response()
                continue
            assumptions = data.get("assumptions")
            assumption_list = (
                [str(item) for item in assumptions] if isinstance(assumptions, list) else []
            )
            return ModelRepair(
                patched_source=patched,
                assumptions=assumption_list,
                diff=diff,
            )
        self.budget_reason = f"已达到最大候选尝试次数 {self.max_rounds}"
        return None

    def generate(self, text: str, *, creative: bool, feedback: str = "") -> ModelProgram | None:
        try:
            tier, settings = self._select("generate", len(text), 0)
        except ModelUnavailable:
            return None
        messages = generate_messages(text, creative=creative, feedback=feedback)
        data = self._call(
            kind="creative" if creative else "generate",
            settings=settings,
            tier=tier,
            messages=messages,
            cache_payload={"text": text, "creative": creative, "feedback": feedback},
        )
        source = data.get("source")
        if not isinstance(source, str) or not source.strip():
            self.discard_response()
            return None
        assumptions = data.get("assumptions")
        io_contract = data.get("io_contract")
        assumption_list = (
            [str(item) for item in assumptions] if isinstance(assumptions, list) else []
        )
        return ModelProgram(
            source=source,
            assumptions=assumption_list,
            io_contract=str(io_contract) if isinstance(io_contract, str) else "",
        )


__all__ = [
    "BudgetExceeded",
    "ModelError",
    "ModelProgram",
    "ModelRepair",
    "ModelResponseError",
    "ModelSession",
    "ModelStats",
    "ModelUnavailable",
]
