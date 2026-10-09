import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

DOCUMENTS_SERVICE_ROOT = REPO_ROOT / "services" / "document-production-api"
DATA_SERVICE_ROOT = REPO_ROOT / "services" / "data-production-api"
DATA_IR_ROOT = REPO_ROOT / "packages" / "data-production-ir"
DOCUMENTS_IR_ROOT = REPO_ROOT / "packages" / "document-production-ir"
NESTED_PLATFORM_SRC = (
    REPO_ROOT
    / "projects"
    / "product"
    / "engineering"
    / "book-dev"
    / "github"
    / "bookchaowalit"
    / "book-production-platform"
    / "src"
)

PIPELINE_DEPENDENCIES_AVAILABLE = True
try:
    import docx  # noqa: F401
    import reportlab  # noqa: F401
    import xlsxwriter  # noqa: F401
except ImportError:  # pragma: no cover - only on hosts without the stage service deps
    PIPELINE_DEPENDENCIES_AVAILABLE = False

STAGE_ROOTS_AVAILABLE = all(
    path.is_dir() for path in (DATA_SERVICE_ROOT, DATA_IR_ROOT, DOCUMENTS_SERVICE_ROOT, DOCUMENTS_IR_ROOT, NESTED_PLATFORM_SRC)
)
