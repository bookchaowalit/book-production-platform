import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).parents[1]
PACKAGE_ROOT = SERVICE_ROOT.parents[1] / "packages" / "marketing-production-ir"
KIT_ROOT = SERVICE_ROOT.parents[1] / "packages" / "brief-adapter-kit"
TRACE_ROOT = SERVICE_ROOT.parents[1] / "packages" / "production-trace"
for path in (SERVICE_ROOT, PACKAGE_ROOT, KIT_ROOT, TRACE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
