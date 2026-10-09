"""Filesystem locations for the cross-tool production pipelines package."""

from __future__ import annotations

from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
SCHEMAS_DIR = SERVICE_ROOT / "schemas"

DATA_SERVICE_ROOT = REPO_ROOT / "services" / "data-production-api"
DATA_IR_ROOT = REPO_ROOT / "packages" / "data-production-ir"
DOCUMENTS_SERVICE_ROOT = REPO_ROOT / "services" / "document-production-api"
DOCUMENTS_IR_ROOT = REPO_ROOT / "packages" / "document-production-ir"
PRODUCTION_TRACE_ROOT = REPO_ROOT / "packages" / "production-trace"
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

__all__ = [
    "DATA_IR_ROOT",
    "DATA_SERVICE_ROOT",
    "DOCUMENTS_IR_ROOT",
    "DOCUMENTS_SERVICE_ROOT",
    "NESTED_PLATFORM_SRC",
    "REPO_ROOT",
    "SCHEMAS_DIR",
    "SERVICE_ROOT",
]
