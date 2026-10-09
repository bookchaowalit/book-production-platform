# Document Production IR

`document_ir` is the deterministic, versioned data contract used by the
Documents V1A product. It contains request validation and the normalized
document representation consumed by local renderers. It does not call models,
read files, or perform side effects.

The initial contract supports structured text and numeric input for the pinned
`invoice-v1` recipe. `ScanImportRequest` and the scan proposal models add a
versioned, evidence-backed contract for local PDF, DOCX, and image import; the
package still does not read files or call OCR/model providers.
`ScanImportRequest` (`scan-import-request.v1`) requires exactly one source path:
the original PDF-only `source_pdf`, kept for backward compatibility, or the
additive optional `source_file` for `.pdf`, `.docx`, `.png`, `.jpg`/`.jpeg`,
`.tif`/`.tiff`, or `.webp`. Setting both or neither fails validation.
