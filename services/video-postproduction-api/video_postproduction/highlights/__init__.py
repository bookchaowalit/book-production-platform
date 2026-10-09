"""AI highlight proposals (V2, REQ-VID-04).

Importing this package never imports ``brief_adapter_kit``: the structured
route, brief parsing, the engine, and the confirmation gate are kit-free. The
kit is loaded lazily only when a gateway-backed proposal or shadow run is
requested.
"""

from .engine import (
    ClipRange,
    EDLProposal,
    HighlightConfirmation,
    HighlightError,
    HighlightRequest,
    build_confirmed_edl,
)
from .provider import HighlightResult, propose_highlights, propose_structured

__all__ = [
    "ClipRange",
    "EDLProposal",
    "HighlightConfirmation",
    "HighlightError",
    "HighlightRequest",
    "HighlightResult",
    "build_confirmed_edl",
    "propose_highlights",
    "propose_structured",
]
