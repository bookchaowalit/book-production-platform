"""Turn a CSV or XLSX file into the dataset envelope the Spreadsheet commands take.

Reading uses the vendored shared reader (``book_production_platform.file_intake``):
bounded, no formula evaluation, values kept as the text the file stored. The
engine still validates the dataset and computes every value.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..file_intake import TableReadError, read_table

TABLE_SUFFIXES = frozenset({".csv", ".tsv", ".xlsx", ".xlsm"})


class FileInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_table_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in TABLE_SUFFIXES


def _workbook_id(path: Path) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", path.stem).strip("-_")[:64]
    return slug or "workbook"


def table_dataset(path: str | Path, *, sheet: str | None = None) -> dict[str, Any]:
    """``{name, header, rows}`` with rows keyed by header (blank cells left out)."""

    file = Path(path)
    try:
        table = read_table(file, sheet=sheet)
    except TableReadError as exc:
        raise FileInputError(exc.code, str(exc)) from None
    if "blank_header_cell" in table.notes or "duplicate_header" in table.notes:
        raise FileInputError("dataset_header_invalid", "every column needs a unique, non-blank header")
    rows = [{name: value for name, value in zip(table.header, row, strict=True) if value != ""} for row in table.rows]
    return {"name": table.sheet or file.stem, "header": list(table.header), "rows": rows}


def table_envelope(
    path: str | Path,
    *,
    brief: str | None = None,
    proposal: dict[str, Any] | None = None,
    sheet: str | None = None,
    locale: str = "th-TH",
) -> dict[str, Any]:
    """The ``brief`` envelope; with ``proposal`` it is a structured request (zero model calls)."""

    file = Path(path)
    envelope: dict[str, Any] = {
        "workbook_id": _workbook_id(file),
        "brief": brief or "",
        "locale": locale,
        "dataset": table_dataset(file, sheet=sheet),
    }
    if proposal is not None:
        envelope["proposal"] = proposal
    return envelope


__all__ = ["TABLE_SUFFIXES", "FileInputError", "is_table_file", "table_dataset", "table_envelope"]
