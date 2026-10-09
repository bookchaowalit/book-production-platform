"""Cross-tool production pipeline package (T6.3).

Thin, deterministic orchestration over the existing local production CLIs:

``data pipeline (services/data-production-api) -> data viz (same service,
chart build) -> workbook (book-production-platform spreadsheet) -> report
document (services/document-production-api)``.

Work passes between stages through versioned artifact contracts validated at
each boundary; no stage code is imported, copied, or re-owned here.
"""

from .contracts import (
    CONTRACT_CONFIG,
    CONTRACT_DATA_JOB,
    CONTRACT_DATASET_REQUEST,
    CONTRACT_DOCUMENT_MANIFEST,
    CONTRACT_MANIFEST,
    CONTRACT_REPORT_REQUEST,
    CONTRACT_SPREADSHEET_JOB,
    ContractError,
    PipelineReportConfig,
    PipelineReportManifest,
    parse_config_file,
    validate_config_payload,
    validate_manifest_payload,
)
from .runner import EXIT_CODES, RunResult, run_monthly_report

__all__ = [
    "CONTRACT_CONFIG",
    "CONTRACT_DATASET_REQUEST",
    "CONTRACT_DATA_JOB",
    "CONTRACT_DOCUMENT_MANIFEST",
    "CONTRACT_MANIFEST",
    "CONTRACT_REPORT_REQUEST",
    "CONTRACT_SPREADSHEET_JOB",
    "EXIT_CODES",
    "ContractError",
    "PipelineReportConfig",
    "PipelineReportManifest",
    "RunResult",
    "parse_config_file",
    "run_monthly_report",
    "validate_config_payload",
    "validate_manifest_payload",
]
