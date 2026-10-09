import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

VIDEO_POSTPRODUCTION_API_ROOT = REPO_ROOT / "services" / "video-postproduction-api"
TRACE_ROOT = REPO_ROOT / "packages" / "production-trace"
if str(TRACE_ROOT) not in sys.path:
    sys.path.append(str(TRACE_ROOT))
