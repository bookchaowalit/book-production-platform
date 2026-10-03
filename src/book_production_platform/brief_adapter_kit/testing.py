"""Deterministic test doubles for brief adapters (REQ-AI-08).

``ScriptedTransport`` replays queued replies without any network access so a
tool's fake-adapter tests and shadow-harness tests run offline.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .transport import GatewayReply


class ScriptedTransport:
    """Replay replies: dict → JSON content, str → raw content, Exception → raised.

    ``responder`` (payload → reply) may be given instead of a queue to answer
    from the request, e.g. an oracle keyed by a case canary.
    """

    def __init__(
        self,
        *replies: Any,
        responder: Callable[[dict[str, Any]], Any] | None = None,
        input_tokens: int | None = 900,
        output_tokens: int | None = 120,
    ) -> None:
        self.replies = list(replies)
        self.responder = responder
        self.payloads: list[dict[str, Any]] = []
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def generate(self, payload: dict[str, Any], *, timeout_seconds: float) -> GatewayReply:
        assert timeout_seconds <= 30
        self.payloads.append(payload)
        if self.responder is not None:
            reply = self.responder(payload)
        else:
            reply = self.replies.pop(0) if self.replies else {}
        if isinstance(reply, Exception):
            raise reply
        content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return GatewayReply(content, self.input_tokens, self.output_tokens, "provider", "scripted", "success")


__all__ = ["ScriptedTransport"]
