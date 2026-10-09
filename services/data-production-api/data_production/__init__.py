"""Local-first Data + Data Viz V1A service."""

import sys
from pathlib import Path

_package_root = Path(__file__).resolve().parents[3] / "packages" / "data-production-ir"
if str(_package_root) not in sys.path:
    sys.path.insert(0, str(_package_root))
