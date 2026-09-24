"""Deterministic Graphics V1A Template Batch service."""

import sys
from pathlib import Path

# The monorepo package is intentionally kept as a sibling product boundary;
# this bootstrap makes the local CLI usable without a global editable install.
_ir_root = Path(__file__).parents[3] / "packages" / "graphics-production-ir"
if str(_ir_root) not in sys.path:
    sys.path.insert(0, str(_ir_root))
