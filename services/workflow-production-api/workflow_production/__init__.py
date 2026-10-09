"""Deterministic Workflow V1A Form-to-Document Flow service."""

from __future__ import annotations

import sys
from pathlib import Path

# Keep the source checkout runnable before the IR package is installed.
_ir_root = Path(__file__).resolve().parents[3] / "packages" / "workflow-production-ir"
if _ir_root.is_dir() and str(_ir_root) not in sys.path:
    sys.path.insert(0, str(_ir_root))
