from __future__ import annotations

import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
PACKAGE_ROOT = REPO_ROOT / "packages" / "data-production-ir"
KIT_ROOT = REPO_ROOT / "packages" / "brief-adapter-kit"
TRACE_ROOT = REPO_ROOT / "packages" / "production-trace"
FILE_INTAKE_ROOT = REPO_ROOT / "packages" / "file-intake"
for path in (PACKAGE_ROOT, KIT_ROOT, TRACE_ROOT, FILE_INTAKE_ROOT, SERVICE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
