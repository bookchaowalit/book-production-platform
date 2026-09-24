"""Stable errors for Audio Master operations."""


class AudioProductionError(ValueError):
    """A rejected AudioIR, source, tool run, or stored artifact."""

    def __init__(self, code: str, message: str = "Audio job was rejected") -> None:
        self.code = code
        super().__init__(f"{code}: {message}")
