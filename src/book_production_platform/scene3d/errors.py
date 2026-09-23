"""Stable, safe error types shared by the 3D scene pipeline."""

INVALID_GLB = "invalid_glb"
UNSUPPORTED_GLB_FEATURE = "unsupported_glb_feature"
UNSAFE_GLB_RESOURCE = "unsafe_glb_resource"
RESOURCE_CAP_EXCEEDED = "resource_cap_exceeded"
INVALID_RENDER = "invalid_render"


class SceneInputError(ValueError):
    """A rejected SceneIR or asset with a machine-readable failure code."""

    def __init__(self, code: str, message: str = "Scene input was rejected") -> None:
        self.code = code
        super().__init__(message)
