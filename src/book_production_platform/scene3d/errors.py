"""Stable, safe error types shared by the 3D scene pipeline."""


class SceneInputError(ValueError):
    """A rejected SceneIR or asset with a machine-readable failure code."""

    def __init__(self, code: str, message: str = "Scene input was rejected") -> None:
        self.code = code
        super().__init__(message)
