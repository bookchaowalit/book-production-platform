"""Turn a CSV or XLSX file into the JSON inputs the Data V1A commands take.

Reading uses the shared ``packages/file-intake`` reader (bounded, no formula
evaluation). The table becomes ``csv_text``; column types are still decided
by the normal DatasetRequest validation, never here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

TABLE_SUFFIXES = frozenset({".csv", ".tsv", ".xlsx", ".xlsm"})
FILE_INTAKE_MISSING = "file_intake_missing"


class FileInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_table_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in TABLE_SUFFIXES


def _dataset_id(path: Path) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", path.stem).strip("-_")[:100]
    return slug if slug and slug[0].isalnum() else f"file-{slug}".strip("-")[:100] or "file"


def table_request(path: str | Path, *, sheet: str | None = None) -> dict[str, Any]:
    """A ``dataset-request.v1`` payload (no columns, no chart) built from a table file."""

    try:
        from file_intake import TableReadError, read_table
    except ModuleNotFoundError:
        raise FileInputError(FILE_INTAKE_MISSING, "add packages/file-intake to PYTHONPATH to read CSV/XLSX files") from None
    file = Path(path)
    try:
        table = read_table(file, sheet=sheet)
    except TableReadError as exc:
        raise FileInputError(exc.code, str(exc)) from None
    title = file.stem if table.sheet is None else f"{file.stem} · {table.sheet}"
    return {
        "schema_version": "dataset-request.v1",
        "dataset_id": _dataset_id(file),
        "title": title[:200],
        "csv_text": table.to_csv_text(),
        "delimiter": ",",
        "source_ref": file.name if table.sheet is None else f"{file.name}#{table.sheet}",
    }


def table_envelope(path: str | Path, *, brief: str, sheet: str | None = None, locale: str | None = None) -> dict[str, Any]:
    """The ``propose`` envelope (dataset_id, csv_text, brief) built from a table file."""

    request = table_request(path, sheet=sheet)
    envelope = {"dataset_id": request["dataset_id"], "title": request["title"], "csv_text": request["csv_text"], "brief": brief}
    if locale:
        envelope["locale"] = locale
    return envelope


__all__ = ["FILE_INTAKE_MISSING", "TABLE_SUFFIXES", "FileInputError", "is_table_file", "table_envelope", "table_request"]
