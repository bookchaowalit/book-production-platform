"""Generic fail-closed brief → JSON proposal runner over the Central AI Gateway.

Every tool adapter supplies only its system prompt, the user content, and a
tool-specific grounding/validation step. This runner owns the shared safety
contract (REQ-AI-01..07):

- refuse instruction-like briefs before any spend (prompt-injection guard);
- reserve the call and the worst-case cost atomically before each dispatch;
- at most two calls per job (one JSON repair), per-call timeout <= 30 s,
  job deadline <= 120 s, input <= 8,000 tokens, output <= 2,000 tokens;
- settle the measured cost; an unknown cost stays fully charged;
- treat model output as untrusted: only a JSON object is accepted, and the
  caller must still validate and ground it before building any IR.

The runner never stores or returns the brief, and its outcome carries only an
issue code, never provider text.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from .config import MAX_INPUT_TOKENS, ProviderConfig
from .ledger import BudgetExceeded, BudgetLedger, CostReservation
from .transport import (
    GatewayError,
    GatewayReply,
    GatewayTransport,
    HttpGatewayTransport,
)

PROVIDER_NOT_CONFIGURED = "provider_not_configured"
PROMPT_OVERHEAD_TOKENS = 400
DEFAULT_REPAIR_PROMPT = (
    "Your previous reply was not the required JSON object. Reply again with only that JSON object, following the same rules."
)
INSTRUCTION_LIKE = re.compile(
    r"ignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\s+instructions|\bsystem\s*:|admin\s+mode|you\s+are\s+now"
    r"|developer\s+mode|disregard\s+(?:the\s+)?rules|ไม่สนใจคำสั่ง|ละเว้นคำสั่ง|ลืมคำสั่ง",
    re.IGNORECASE,
)

OutcomeStatus = Literal["ok", "abstained", "rejected"]


@dataclass
class RunOutcome:
    """Result of one bounded proposal run; ``parsed`` is untrusted model JSON."""

    status: OutcomeStatus
    parsed: dict[str, Any] | None = None
    issue_code: str | None = None
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    """Conservative token estimate matching the gateway (characters / 4)."""

    return (len(text) + 3) // 4


def parse_json_object(content: str) -> dict[str, Any] | None:
    """Parse a model reply into a JSON object, tolerating one fenced code block."""

    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class GatewayProposalRunner:
    """Budgeted gateway caller bound to one validated :class:`ProviderConfig`."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        ledger_root: str | Path,
        transport: GatewayTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        ledger_filename: str = "ai_budget.sqlite3",
        extra_instruction_pattern: re.Pattern[str] | None = None,
    ) -> None:
        self.config = config
        self.ledger = BudgetLedger(ledger_root, filename=ledger_filename, max_output_tokens=config.max_output_tokens)
        self.transport: GatewayTransport = transport or HttpGatewayTransport(config)
        self._clock = clock
        self._extra_guard = extra_instruction_pattern

    def instruction_like(self, text: str) -> bool:
        return bool(INSTRUCTION_LIKE.search(text) or (self._extra_guard and self._extra_guard.search(text)))

    def _payload(self, messages: list[dict[str, str]], input_tokens: int) -> dict[str, Any]:
        return {
            "route": self.config.route,
            "prompt_version": self.config.prompt_version,
            "messages": messages,
            "profile": self.config.profile,
            "privacy_class": self.config.privacy_class,
            "token_budget": min(self.config.max_token_budget, input_tokens + self.config.completion_tokens),
            "max_output_tokens": self.config.completion_tokens,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        card = self.config.rate_card
        return (Decimal(input_tokens) * card.input_usd_per_1k + Decimal(output_tokens) * card.output_usd_per_1k) / Decimal(1000)

    def _dispatch(self, job_id: str, messages: list[dict[str, str]], input_tokens: int, timeout: float) -> GatewayReply:
        reservation: CostReservation = self.ledger.reserve_cost(
            job_id,
            amount_usd=self._cost(input_tokens, self.config.completion_tokens),
            caps=self.config.caps.as_cost_caps(),
            rate_card_version=self.config.rate_card.version,
        )
        try:
            reply = self.transport.generate(self._payload(messages, input_tokens), timeout_seconds=timeout)
        except GatewayError as exc:
            not_sent = exc.error_class == PROVIDER_NOT_CONFIGURED
            self.ledger.settle_cost(reservation, actual_usd=Decimal(0) if not_sent else None, input_tokens=None, output_tokens=None)
            raise
        actual = None if reply.input_tokens is None or reply.output_tokens is None else self._cost(reply.input_tokens, reply.output_tokens)
        self.ledger.settle_cost(reservation, actual_usd=actual, input_tokens=reply.input_tokens, output_tokens=reply.output_tokens)
        return reply

    def propose(
        self,
        *,
        job_id: str,
        system_prompt: str,
        user_content: str,
        guard_text: str | None = None,
        first_call_reserved: bool = False,
        repair_prompt: str = DEFAULT_REPAIR_PROMPT,
        accept: Callable[[dict[str, Any]], bool] | None = None,
    ) -> RunOutcome:
        """Run at most two bounded calls and return the first acceptable JSON object.

        ``guard_text`` (default ``user_content``) is scanned for instruction-like
        content; a hit abstains with zero calls. ``accept`` may reject a parsed
        object's *shape* so the single repair call is used for it too.
        """

        if self.instruction_like(guard_text if guard_text is not None else user_content):
            return RunOutcome("abstained", issue_code="instruction_like_content")

        started = self._clock()
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
        input_tokens = sum(estimate_tokens(message["content"]) for message in messages) + PROMPT_OVERHEAD_TOKENS
        if input_tokens > MAX_INPUT_TOKENS or input_tokens + self.config.completion_tokens > self.config.max_token_budget:
            return RunOutcome("rejected", issue_code="input_token_limit_exceeded")

        outcome = RunOutcome("rejected", issue_code="invalid_model_output")
        for attempt in range(2):
            remaining = self.config.job_deadline_seconds - (self._clock() - started)
            if remaining <= 0:
                outcome.issue_code = "deadline_exceeded"
                return outcome
            if attempt > 0 or not first_call_reserved:
                try:
                    self.ledger.reserve(job_id, calls=1, input_tokens=input_tokens, output_tokens=self.config.max_output_tokens)
                except BudgetExceeded:
                    outcome.issue_code = "budget_exceeded" if attempt == 0 else "invalid_model_output"
                    return outcome
            try:
                reply = self._dispatch(job_id, messages, input_tokens, min(float(self.config.timeout_seconds), remaining))
            except BudgetExceeded:
                outcome.issue_code = "budget_exceeded"
                return outcome
            except GatewayError as exc:
                if exc.error_class == PROVIDER_NOT_CONFIGURED:
                    outcome.issue_code = PROVIDER_NOT_CONFIGURED
                else:
                    outcome.model_calls += 1
                    outcome.issue_code = f"gateway_{exc.error_class}"
                return outcome
            outcome.model_calls += 1
            outcome.input_tokens += reply.input_tokens if reply.input_tokens is not None else input_tokens
            outcome.output_tokens += reply.output_tokens if reply.output_tokens is not None else estimate_tokens(reply.content)
            if reply.status == "budget_exceeded":
                outcome.issue_code = "budget_exceeded"
                return outcome
            parsed = parse_json_object(reply.content)
            if parsed is not None and (accept is None or accept(parsed)):
                outcome.status, outcome.parsed, outcome.issue_code = "ok", parsed, None
                return outcome
            messages = [*messages, {"role": "assistant", "content": reply.content[:4_000] or "{}"}, {"role": "user", "content": repair_prompt}]
            input_tokens = sum(estimate_tokens(message["content"]) for message in messages) + PROMPT_OVERHEAD_TOKENS
            if input_tokens > MAX_INPUT_TOKENS or input_tokens + self.config.completion_tokens > self.config.max_token_budget:
                return outcome
        return outcome


__all__ = [
    "DEFAULT_REPAIR_PROMPT",
    "INSTRUCTION_LIKE",
    "PROVIDER_NOT_CONFIGURED",
    "GatewayProposalRunner",
    "RunOutcome",
    "estimate_tokens",
    "parse_json_object",
]
