# Graphics V1A — feature parity with Figma, Canva, and Photoshop

Scope: `graphics_production` + `graphics_ir` (tool id `graphics-v1a`). The
engine is a deterministic template compiler: it fills fixed templates with
brand tokens and text, lays text out with Thai-safe wrapping, writes an
editable SVG, and rasterises it through a local Chromium. It is not a
free-form design editor, so "missing" below often means "not a template
feature yet", not "impossible".

Status: **have** = implemented and tested; **partial** = a subset works;
**missing** = not implemented. Priority: P1 next, P2 later, P3 only on demand,
— = out of scope for a deterministic batch engine.

## Capability matrix

| Capability | Figma / Canva / Photoshop | Engine status | Priority | Notes |
|---|---|---|---|---|
| Fixed canvas presets | Frame presets / Custom size / New document | have | — | `square` 1080×1080, `portrait` 1080×1350, `story` 1080×1920 |
| Arbitrary canvas size, landscape 16:9, A4/print sizes | yes | missing | P1 | Template coordinates are authored for a 1080 px width; each new aspect needs its own layout table |
| Resize one design to several sizes | Canva Magic Resize, Figma duplicate frame | **have (new)** | — | `"variants": [...]` on a batch item expands to one item per registered size (`expand_variants`) |
| Templates | thousands | partial (3) | P1 | `quote_card`, `product_card`, `announcement`; new templates also need V1B registry + eval updates |
| Brand kit colours | Brand Kit / Styles | have | — | primary, accent, foreground, background tokens |
| Brand fonts | Brand Kit fonts | partial | P2 | one custom TTF/OTF per item via `font_ref`; no per-layer font roles |
| Logo slot | Brand Kit logo | missing | P1 | needs an image slot per template |
| Text: family, size, colour, alignment | yes | have | — | mirrored 1:1 in the Figma guide |
| Text auto-fit (shrink to fit, never truncate) | Canva auto-resize, Figma plugins | have | — | `fit_text` steps the size down to a template minimum, rejects overflow |
| Thai cluster-safe line breaking | partial in all three | have | — | vowels/tone marks never start a line; guide types the engine's exact lines |
| Font weight / italic | yes | missing | P1 | renderer writes Regular only; a bold headline is the most-requested gap |
| Line height / letter spacing control | yes | partial | P2 | fixed `font_size × 1.2` line height, 0 letter spacing |
| Rich text (mixed styles in one box) | yes | missing | P3 | |
| Shapes | rect, ellipse, polygon, vector | partial | P2 | `rect` (fill + stroke) and `line`; no corner radius, ellipse, gradient, shadow |
| Image placement | place / frame | have | — | contain-fit (`preserveAspectRatio meet`), local files only, unsafe SVG rejected |
| Image crop/cover, masks, filters | yes | missing | P2 | |
| Background removal | Canva/Photoshop AI | — | — | generative/AI, outside the deterministic path |
| Editable output with named layers | `.fig`, `.psd` | have | — | SVG with node ids; the manual guide rebuilds the same layer tree in Figma |
| Groups / components / auto layout | yes | missing | P3 | |
| Export PNG 1x | yes | have | — | per item |
| Export PNG @2x–6x | yes | have | — | `graphics export --scale` |
| Export SVG | Figma/Photoshop | have | — | the batch source of truth |
| Export PDF | yes | partial | P2 | raster PDF at a chosen DPI (`graphics export --dpi`); no vector PDF |
| Export JPEG / WebP | yes | missing | P2 | |
| Print: CMYK, bleed, crop marks | Photoshop / Canva Print | missing | P1 | needed before offering printed deliverables |
| Accessibility contrast check | plugins / Canva checker | **have (new, advisory)** | — | WCAG AA per text layer in the trace QA step and the guide checks; never rejects |
| Bulk create from data | Canva Bulk Create, Figma plugins | have | — | batch JSON / HTTP API, up to 100 items, idempotent replay |
| History, provenance, redo by hand | version history | **have (new)** | — | `trace.json`, `history.jsonl`, `MANUAL-STEPS.md` (Figma steps, Canva/Photoshop alternatives) |
| Collaboration, comments | yes | — | — | not an editor |
| Animation / video | Canva, Figma prototypes | — | — | |

## Implemented in this pass

1. **Multi-size resize** (`expand_variants`): one item with
   `"variants": ["square", "portrait", "story"]` becomes three items with the
   registered canvas sizes and `request_id-<variant>` names. Ambiguous input
   (both `variant` and `variants`, duplicates, non-strings) is left unchanged
   so the request validator rejects it. Tests:
   `tests/graphics/test_trace_manual.py::test_expand_variants_*`.
2. **WCAG contrast advisory** (`qa.contrast_report`): ratio of every text
   layer against the shape painted behind it, compared with AA (4.5:1, or 3:1
   at ≥ 24 px). Recorded in the trace QA step and listed in the guide checks.
   Advisory only, because existing brand kits (accent on white) would fail.
3. **Traceability and manual steps** for batches and print exports (see the
   README section "Job history, trace & manual steps").

Already present before this pass (so not re-implemented): SVG export, raster
PDF/@Nx print export, text auto-fit.

## Roadmap (prioritised)

1. P1 — Bold/weight per text role (renderer `font-weight`, guide, golden review).
2. P1 — Landscape 16:9 and A4 variants with their own layout tables.
3. P1 — Logo slot in the brand kit and templates.
4. P1 — Print profile: bleed, crop marks, CMYK PDF (needs a colour-managed path).
5. P2 — JPEG/WebP export; vector PDF via the browser's print-to-PDF.
6. P2 — Rounded corners, ellipse, gradient fills; image cover/crop.
7. P3 — More templates (each needs V1B registry and eval cases).

## Runtime notes

PNG rasterisation needs a local Chromium-family browser. In environments
without one, rendering tests skip; trace/guide tests replace the rasteriser
with a canvas-sized PNG and therefore verify structure, not pixels.
