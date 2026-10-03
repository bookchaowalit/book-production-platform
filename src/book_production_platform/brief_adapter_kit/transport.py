"""Central AI Gateway transport shared by every brief adapter.

This is the only module in the kit that opens a network connection or
reads an environment variable for a credential. It calls
``POST {gateway_url}/api/ai/generate`` (contract ``solo-empire.ai-gateway.v1``)
with a bearer token read from the environment variable named in the provider
configuration. Provider and gateway error bodies are never returned, logged,
or persisted; failures surface as a short error class only.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .config import ProviderConfig

GATEWAY_CONTRACT = "solo-empire.ai-gateway.v1"
MAX_RESPONSE_BYTES = 256_000


class GatewayError(RuntimeError):
    """Gateway failure carrying only a stable, non-sensitive error class."""

    def __init__(self, error_class: str) -> None:
        super().__init__(error_class)
        self.error_class = error_class


@dataclass(frozen=True)
class GatewayReply:
    content: str
    input_tokens: int | None
    output_tokens: int | None
    usage_source: str
    model: str
    status: str


class GatewayTransport(Protocol):
    def generate(self, payload: dict[str, Any], *, timeout_seconds: float) -> GatewayReply:
        """Send one bounded generation request and return the untrusted reply."""


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse_gateway_reply(body: bytes) -> GatewayReply:
    """Validate a gateway response body without trusting its content."""

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GatewayError("invalid_response") from None
    if not isinstance(data, dict) or data.get("contract") != GATEWAY_CONTRACT:
        raise GatewayError("invalid_response")
    content = data.get("content")
    if not isinstance(content, str) or data.get("output_trust") != "untrusted_model_output":
        raise GatewayError("invalid_response")
    status = data.get("status")
    if status not in {"success", "budget_exceeded"}:
        raise GatewayError("invalid_response")
    raw_usage = data.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    raw_model = data.get("model")
    model = raw_model if isinstance(raw_model, str) else "unknown"
    return GatewayReply(
        content=content,
        input_tokens=_optional_int(usage.get("input_tokens")),
        output_tokens=_optional_int(usage.get("output_tokens")),
        usage_source=str(usage.get("source") or "unavailable"),
        model=model[:120],
        status=str(status),
    )


class HttpGatewayTransport:
    """urllib-based transport bound to one validated :class:`ProviderConfig`."""

    def __init__(self, config: ProviderConfig) -> None:
        self._endpoint = f"{config.gateway_url}/api/ai/generate"
        self._token_env = config.token_env

    def generate(self, payload: dict[str, Any], *, timeout_seconds: float) -> GatewayReply:
        token = os.environ.get(self._token_env, "").strip()
        if not token:
            raise GatewayError("provider_not_configured")
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise GatewayError(_http_error_class(exc.code)) from None
        except TimeoutError:
            raise GatewayError("timeout") from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            raise GatewayError("timeout" if isinstance(reason, TimeoutError | socket.timeout) else "network") from None
        except OSError:
            raise GatewayError("network") from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise GatewayError("response_too_large")
        return parse_gateway_reply(body)


def _http_error_class(code: int) -> str:
    if code in {401, 403}:
        return "gateway_auth"
    if code == 429:
        return "rate_limited"
    if code == 400:
        return "gateway_rejected_request"
    if code == 503:
        return "gateway_unavailable"
    if code >= 500:
        return "gateway_error"
    return f"gateway_http_{code}"


__all__ = [
    "GATEWAY_CONTRACT",
    "GatewayError",
    "GatewayReply",
    "GatewayTransport",
    "HttpGatewayTransport",
    "parse_gateway_reply",
]
