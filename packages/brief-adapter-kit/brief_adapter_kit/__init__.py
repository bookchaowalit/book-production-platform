"""Shared fail-closed AI brief → IR proposal kit (``brief-adapter.v1`` pattern).

The model may only *propose* a tool's IR. Every proposal goes through the
Central AI Gateway with an owner-approved rate card and caps, is grounded
against the source brief, and is validated by the tool's deterministic engine
before anything is produced. See README.md for the adapter pattern.
"""

from .config import (
    OwnerApproval,
    ProviderConfig,
    ProviderConfigError,
    RateCard,
    SpendingCaps,
    load_provider_config,
    parse_provider_config,
)
from .ledger import (
    BudgetExceeded,
    BudgetLedger,
    BudgetReservation,
    CostCaps,
    CostReservation,
)
from .runner import (
    PROVIDER_NOT_CONFIGURED,
    GatewayProposalRunner,
    RunOutcome,
    estimate_tokens,
    parse_json_object,
)
from .transport import (
    GatewayError,
    GatewayReply,
    GatewayTransport,
    HttpGatewayTransport,
)

KIT_VERSION = "0.1.0"
BRIEF_ADAPTER_CONTRACT = "brief-adapter.v1"

__all__ = [
    "BRIEF_ADAPTER_CONTRACT",
    "KIT_VERSION",
    "PROVIDER_NOT_CONFIGURED",
    "BudgetExceeded",
    "BudgetLedger",
    "BudgetReservation",
    "CostCaps",
    "CostReservation",
    "GatewayError",
    "GatewayProposalRunner",
    "GatewayReply",
    "GatewayTransport",
    "HttpGatewayTransport",
    "OwnerApproval",
    "ProviderConfig",
    "ProviderConfigError",
    "RateCard",
    "RunOutcome",
    "SpendingCaps",
    "estimate_tokens",
    "load_provider_config",
    "parse_json_object",
    "parse_provider_config",
]
