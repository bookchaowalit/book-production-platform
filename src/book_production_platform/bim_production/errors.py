"""Stable, privacy-safe failures for BIM production operations."""


class BimProductionError(Exception):
    """Expose a stable code without retaining paths, IFC data, or diagnostics."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
