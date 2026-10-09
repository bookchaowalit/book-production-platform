"""Versioned, owner-approved provider configuration shared by every brief adapter.

A paid model step may only be dispatched when a configuration validates
(REQ-AI-04/06). It pins the Central AI Gateway endpoint, the *name* of the
environment variable that holds the gateway bearer token (never the token),
a profile, a privacy class, a versioned rate card, per-job/day/month spending
caps, and the owner approval record. ``mode`` starts as ``shadow``: results
are compared but never used until the owner switches the file to ``live``
after a passing held-out shadow report.
"""

from __future__ import annotations

import ipaddress
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, TypeVar
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .ledger import CostCaps

KIT_CONFIG_SCHEMA = "brief-adapter-provider-config.v1"
MAX_CALL_TIMEOUT_SECONDS = 30
MAX_JOB_DEADLINE_SECONDS = 120
# Reasoning models (e.g. effort "max") think before answering. Their reasoning
# tokens are billed as output and count against the provider's completion cap,
# so they get explicit headroom on top of the <= 2,000-token JSON answer. The
# ceilings mirror the Central AI Gateway limits (16,384 output tokens, 120 s).
GATEWAY_MAX_OUTPUT_TOKENS = 16_384
MAX_REASONING_TOKENS = GATEWAY_MAX_OUTPUT_TOKENS - 2_000
REASONING_CALL_TIMEOUT_SECONDS = 120
REASONING_JOB_DEADLINE_SECONDS = 300
ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]
MAX_OUTPUT_TOKENS = 2_000
MAX_INPUT_TOKENS = 8_000

ConfigT = TypeVar("ConfigT", bound="ProviderConfig")


class ProviderConfigError(ValueError):
    """Raised when a provider configuration is missing or invalid."""


class RateCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    currency: Literal["USD"] = "USD"
    input_usd_per_1k: Decimal = Field(gt=0, le=Decimal(1))
    output_usd_per_1k: Decimal = Field(gt=0, le=Decimal(1))


class SpendingCaps(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    per_job_usd: Decimal = Field(gt=0)
    per_day_usd: Decimal = Field(gt=0)
    per_month_usd: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> SpendingCaps:
        if not self.per_job_usd <= self.per_day_usd <= self.per_month_usd:
            raise ValueError("caps must satisfy per_job_usd <= per_day_usd <= per_month_usd")
        return self

    def as_cost_caps(self) -> CostCaps:
        return CostCaps(self.per_job_usd, self.per_day_usd, self.per_month_usd)


class OwnerApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approved_by: str = Field(min_length=1, max_length=120)
    approved_on: date
    reference: str = Field(default="", max_length=200)


class ProviderConfig(BaseModel):
    """The only object that can unlock a gateway-backed proposal for one tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["brief-adapter-provider-config.v1"] = "brief-adapter-provider-config.v1"
    tool_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,63}$")
    mode: Literal["shadow", "live"] = "shadow"
    gateway_url: str
    token_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    route: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,64}$")
    prompt_version: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,64}$")
    profile: Literal["fast", "balanced", "quality"] = "fast"
    privacy_class: Literal["public", "internal"] = "internal"
    max_token_budget: int = Field(default=4_096, ge=2_304, le=MAX_INPUT_TOKENS + GATEWAY_MAX_OUTPUT_TOKENS + 48)
    max_output_tokens: int = Field(default=MAX_OUTPUT_TOKENS, ge=64, le=MAX_OUTPUT_TOKENS)
    reasoning_effort: ReasoningEffort | None = None
    reasoning_tokens: int = Field(default=0, ge=0, le=MAX_REASONING_TOKENS)
    timeout_seconds: int = Field(default=MAX_CALL_TIMEOUT_SECONDS, ge=1, le=REASONING_CALL_TIMEOUT_SECONDS)
    job_deadline_seconds: int = Field(default=MAX_JOB_DEADLINE_SECONDS, ge=1, le=REASONING_JOB_DEADLINE_SECONDS)
    rate_card: RateCard
    caps: SpendingCaps
    approval: OwnerApproval

    @model_validator(mode="after")
    def _reasoning_limits(self) -> ProviderConfig:
        reasoning = self.reasoning_effort not in (None, "none")
        if reasoning and self.reasoning_tokens < 1_000:
            raise ValueError("a reasoning effort needs reasoning_tokens headroom (>= 1000) or the JSON answer is truncated")
        if not reasoning and self.reasoning_tokens:
            raise ValueError("reasoning_tokens requires a reasoning_effort other than none")
        if not reasoning and (self.timeout_seconds > MAX_CALL_TIMEOUT_SECONDS or self.job_deadline_seconds > MAX_JOB_DEADLINE_SECONDS):
            raise ValueError("timeouts above 30 s per call / 120 s per job are allowed only with a reasoning effort")
        if self.max_token_budget < self.max_output_tokens + self.reasoning_tokens + 256:
            raise ValueError("max_token_budget must cover the answer, the reasoning headroom, and the prompt")
        return self

    @property
    def completion_tokens(self) -> int:
        """Tokens the provider may generate per call: JSON answer + reasoning headroom."""

        return self.max_output_tokens + self.reasoning_tokens

    @field_validator("gateway_url")
    @classmethod
    def _gateway_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("gateway_url must not carry credentials, a query, or a fragment")
        host = parts.hostname or ""
        if parts.scheme == "https" and host:
            return value.rstrip("/")
        if parts.scheme == "http" and _is_loopback(host):
            return value.rstrip("/")
        raise ValueError("gateway_url must use https (plain http is accepted only for loopback)")


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_provider_config(raw: Any, model: type[ConfigT] = ProviderConfig) -> ConfigT:  # type: ignore[assignment]
    """Validate a mapping into ``model`` or raise :class:`ProviderConfigError` (field names only)."""

    if isinstance(raw, model):
        return raw
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        fields = sorted({".".join(str(part) for part in error["loc"]) or "$" for error in exc.errors()})
        raise ProviderConfigError(f"invalid provider configuration fields: {', '.join(fields)}") from None


def load_provider_config(path: str | Path, model: type[ConfigT] = ProviderConfig) -> ConfigT:  # type: ignore[assignment]
    """Load a provider configuration file; secrets never live in this file."""

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderConfigError(f"provider configuration could not be read: {type(exc).__name__}") from None
    return parse_provider_config(raw, model)


__all__ = [
    "GATEWAY_MAX_OUTPUT_TOKENS",
    "KIT_CONFIG_SCHEMA",
    "MAX_CALL_TIMEOUT_SECONDS",
    "MAX_INPUT_TOKENS",
    "MAX_JOB_DEADLINE_SECONDS",
    "MAX_OUTPUT_TOKENS",
    "MAX_REASONING_TOKENS",
    "REASONING_CALL_TIMEOUT_SECONDS",
    "REASONING_JOB_DEADLINE_SECONDS",
    "OwnerApproval",
    "ProviderConfig",
    "ProviderConfigError",
    "RateCard",
    "SpendingCaps",
    "load_provider_config",
    "parse_provider_config",
]
