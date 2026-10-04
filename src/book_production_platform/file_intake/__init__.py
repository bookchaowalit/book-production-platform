"""Shared, bounded file intake for the Solo Empire production tools.

``detect`` says what a file is (by content, checked against its extension),
``routes_for`` says which tools take it, and ``read_table`` turns a CSV or
XLSX file into header + rows. Standard library only; keep the vendored copy in
book-production-platform identical (root parity test).
"""

from .detect import EXTENSIONS, MAX_FILE_BYTES, Detection, detect
from .routes import ROUTES, Route, routes_for
from .tables import MAX_COLUMNS, MAX_ROWS, Table, TableReadError, read_table

__all__ = [
    "EXTENSIONS",
    "MAX_COLUMNS",
    "MAX_FILE_BYTES",
    "MAX_ROWS",
    "ROUTES",
    "Detection",
    "Route",
    "Table",
    "TableReadError",
    "detect",
    "read_table",
    "routes_for",
]
