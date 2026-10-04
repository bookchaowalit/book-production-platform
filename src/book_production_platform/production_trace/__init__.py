"""Per-job production history, step trace, and manual-reproduction guide.

Stdlib only, so every deterministic engine can import it without extra
dependencies. See README.md for the contract.
"""

from .ai_manual import AI_MANUAL_FILENAME, AI_MANUAL_STATUS_FILENAME
from .guide import GuideLayer, GuideStep, ManualGuide, render_manual_markdown
from .timecode import frames_to_timecode, seconds_to_timecode
from .trace import (
    HISTORY_FILENAME,
    MANUAL_FACTS_FILENAME,
    MANUAL_FILENAME,
    TRACE_FILENAME,
    TRACE_SCHEMA,
    TraceRecorder,
    TraceStep,
    append_history,
    read_history,
    sha256_file,
    sha256_json,
    verify_trace,
)

__all__ = [
    "AI_MANUAL_FILENAME",
    "AI_MANUAL_STATUS_FILENAME",
    "GUIDE_SIDECARS",
    "HISTORY_FILENAME",
    "MANUAL_FACTS_FILENAME",
    "MANUAL_FILENAME",
    "TRACE_FILENAME",
    "TRACE_SCHEMA",
    "GuideLayer",
    "GuideStep",
    "ManualGuide",
    "TraceRecorder",
    "TraceStep",
    "append_history",
    "frames_to_timecode",
    "read_history",
    "render_manual_markdown",
    "seconds_to_timecode",
    "sha256_file",
    "sha256_json",
    "verify_trace",
]

# Every guide file a job may hold next to its artifacts (deterministic, facts, AI-written).
GUIDE_SIDECARS = (MANUAL_FILENAME, MANUAL_FACTS_FILENAME, AI_MANUAL_FILENAME, AI_MANUAL_STATUS_FILENAME)
