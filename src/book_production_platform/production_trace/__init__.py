"""Per-job production history, step trace, and manual-reproduction guide.

Stdlib only, so every deterministic engine can import it without extra
dependencies. See README.md for the contract.
"""

from .guide import GuideLayer, GuideStep, ManualGuide, render_manual_markdown
from .timecode import frames_to_timecode, seconds_to_timecode
from .trace import (
    HISTORY_FILENAME,
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
    "HISTORY_FILENAME",
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
