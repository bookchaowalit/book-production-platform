"""Which production tool takes which kind of file, and how."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    tool_id: str
    use: str
    command: str
    ready: bool = True
    note: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"tool_id": self.tool_id, "use": self.use, "command": self.command, "ready": self.ready, "note": self.note}


_TABLE = (
    Route("data-viz-v1a", "clean the table and build a chart", "python -m data_production.cli build --input <file>"),
    Route("spreadsheet-v1a", "build an editable XLSX workbook", "book-production spreadsheet from-file --input <file>"),
)
_OCR_NOTE = "needs local Tesseract (tesseract-ocr + tesseract-ocr-tha); without it the import asks for a text file"

ROUTES: dict[str, tuple[Route, ...]] = {
    "csv": _TABLE,
    "xlsx": _TABLE,
    "pdf": (Route("documents-v1a", "propose invoice fields from a text PDF (scanned pages need OCR)", "python -m document_production.cli scan (request source_file)"),),
    "docx": (Route("documents-v1a", "propose invoice fields from the document text", "python -m document_production.cli scan (request source_file)"),),
    "png": (Route("documents-v1a", "propose invoice fields from a scanned image", "python -m document_production.cli scan (request source_file)", note=_OCR_NOTE),
            Route("graphics-v1a", "product image inside a template", "GraphicIR assets.product_image")),
    "jpeg": (Route("documents-v1a", "propose invoice fields from a scanned image", "python -m document_production.cli scan (request source_file)", note=_OCR_NOTE),
             Route("graphics-v1a", "product image inside a template", "GraphicIR assets.product_image")),
    "tiff": (Route("documents-v1a", "propose invoice fields from a scanned image", "python -m document_production.cli scan (request source_file)", note=_OCR_NOTE),),
    "webp": (Route("documents-v1a", "propose invoice fields from a scanned image", "python -m document_production.cli scan (request source_file)", note=_OCR_NOTE),),
    "svg": (Route("graphics-v1a", "vector asset inside a template (scripts and remote links are refused)", "GraphicIR assets.product_image"),),
    "glb": (Route("scene3d-v1a", "product scene render (self-contained GLB only)", "SceneIR asset.path"),),
    "video": (Route("video-studio", "transcript, captions, and first cut", "video studio upload"),),
    "audio": (Route("audio-master-v1a", "single-track mastering", "AudioIR"),
              Route("audio-batch-v1a", "batch loudness and trim", "audio-batch.v1")),
    "ifc": (Route("bim-v1a", "quantity takeoff", "bim-takeoff.v1"),),
    "json": (Route("any", "a typed request for the tool whose schema it matches", "<tool> build --input <file>"),),
}


def routes_for(kind: str) -> tuple[Route, ...]:
    return ROUTES.get(kind, ())


__all__ = ["ROUTES", "Route", "routes_for"]
