import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]
PACKAGE_ROOT = REPO_ROOT / "packages" / "document-production-ir"
SERVICE_ROOT = REPO_ROOT / "services" / "document-production-api"
KIT_ROOT = REPO_ROOT / "packages" / "brief-adapter-kit"
TRACE_ROOT = REPO_ROOT / "packages" / "production-trace"
for path in (PACKAGE_ROOT, KIT_ROOT, TRACE_ROOT, SERVICE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
