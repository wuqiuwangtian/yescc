"""模型会话：路由、预算、缓存、隐私提示与两级调用。"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
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
        self.settings_l2 = load_settings(
            "l2", provider_override=None, model_override=None
        )
        self.settings_l3 = load_settings(
            "l3", provider_override=provider_override, model_override=model_override
        )
        self.budget = Budget(max_usd=budget_usd, max_rounds=max_rounds)
        self.stats = ModelStats()

    @property
    def available(self) -> bool:
        return not self.offline and (self.settings_l2 is not None or self.settings_l3 is not None)

    def _select(self, kind: str, size: int, errors: int) -> tuple[str, ModelSettings]:
        if (
            kind == "repair"
            and self.settings_l2 is not None
            and size <= 8192
            and errors <= 3
        ):
            return "l2", self.settings_l2
        if self.settings_l3 is not None:
            return "l3", self.settings_l3
        if self.settings_l2 is not None:
            return "l2", self.settings_l2
        raise ModelUnavailable("没有可用的模型配置")

    def _call(
        self,
        *,
        kind: str,
        settings: ModelSettings,
        tier: str,
        messages: list[Message],
        cache_payload: dict[str, object],
    ) -> dict[str, object]:
        key = cache_key(
            {
                "kind": kind,
                "prompt_version": PROMPT_VERSION,
                "provider": settings.provider,
                "base_url": settings.base_url,
                "model": settings.model,
                "payload": cache_payload,
            }
        )
        if not self.no_cache:
            cached = load_cache(key)
            if cached is not None:
                self.stats.cache_hits += 1
                return cached

        self.budget.check()
        _show_cloud_notice(settings)
        completion = OpenAICompatibleProvider(settings).complete(
            messages, max_tokens=1024, temperature=0.0 if kind != "creative" else 0.4
        )
        self.budget.record(completion.cost_usd)
        self.stats.provider = settings.provider
        self.stats.tier = tier
        self.stats.name = settings.model
        self.stats.calls += 1
        self.stats.prompt_tokens += completion.prompt_tokens
        self.stats.completion_tokens += completion.completion_tokens
        self.stats.cost_usd += completion.cost_usd
        self.stats.duration_ms += completion.duration_ms

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
        try:
            tier, settings = self._select("repair", len(source), diagnostics.count("error"))
        except ModelUnavailable:
            return None

        while self.budget.calls < self.max_rounds:
            messages = repair_messages(source, filename, diagnostics, feedback)
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
            diff = data.get("diff")
            if not isinstance(diff, str) or not diff.strip():
                return None
            try:
                patched = apply_unified_diff(source, diff)
            except PatchError as exc:
                feedback = str(exc)
                self.stats.errors.append(f"patch: {exc}")
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
