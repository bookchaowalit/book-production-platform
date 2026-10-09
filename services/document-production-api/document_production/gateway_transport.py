"""Central AI Gateway transport for Documents V1B (shared ``brief_adapter_kit`` implementation)."""

from brief_adapter_kit.transport import (
    GATEWAY_CONTRACT,
    GatewayError,
    GatewayReply,
    GatewayTransport,
    HttpGatewayTransport,
    parse_gateway_reply,
)

__all__ = [
    "GATEWAY_CONTRACT",
    "GatewayError",
    "GatewayReply",
    "GatewayTransport",
    "HttpGatewayTransport",
    "parse_gateway_reply",
]
