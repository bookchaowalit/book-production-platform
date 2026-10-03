# CAD V1A — feature parity with FreeCAD, Fusion 360, and SolidWorks

Scope: `cad_production` + `cad_ir` (tool id `cad-v1a`). The engine is a
deterministic, single-family parametric generator: one strict `part-ir.v1`
(rectangular mounting plate, four corner through-holes) is built headlessly in
a pinned FreeCAD Part Design worker, verified independently (native document,
STEP volume, closed STL, preview), and published content-addressed. It is not
an interactive modeller, so "missing" below usually means "not a part family
or feature in the IR yet", not "impossible".

Status: **have** = implemented and tested; **partial** = a subset works;
**missing** = not implemented. Priority: P1 next, P2 later, P3 only on demand,
— = out of scope for a deterministic batch engine.

Honesty note: FreeCAD is not installed on the CI/dev host used for this
change. Everything marked "have (new)" below is verified at the PartIR / spec
level (pure Python, deterministic tests); the FreeCAD worker itself was not
re-run. The real-FreeCAD tests stay skipped unless the pinned runtime exists.

## Capability matrix

| Capability | FreeCAD / Fusion 360 / SolidWorks | Engine status | Priority | Notes |
|---|---|---|---|---|
| Parametric sketch + constraints | Sketcher / Sketch / Sketch with relations | have | — | 12 constraints per sketch, fully constrained, checked in the worker |
| Extrude (Pad / Extrude / Boss) | yes | have | — | Pad Dimension = thickness |
| Cut through all (Pocket / Extrude Cut / Cut-Extrude) | yes | have | — | Pocket Type = Through all |
| Native file | FCStd / f3d / sldprt | have | — | `part.FCStd` with the same feature tree a person would build |
| Neutral export STEP (AP214, mm) | yes | have | — | STEP volume re-measured, ≤ 0.1 % error |
| Mesh export STL | yes | have | — | closed, positive-volume mesh check |
| Preview image | thumbnails / render | have | — | deterministic isometric PNG |
| Feature history traceability | timeline / feature tree | **have (new)** | — | `trace.json` per run: worker invocation summary (sketch constraints, Pad length, Pocket type), QA values, artifact sha256; `history.jsonl` for every run |
| "How would I do this by hand" | tutorials | **have (new)** | — | `MANUAL-STEPS.md`: FreeCAD 1.0 Part Design click steps with exact values, Fusion 360 / Onshape / SolidWorks equivalents |
| 2D drawing with dimensions | TechDraw / Drawing / Drawing (.slddrw) | **have (new)** | — | `drawing.svg` (1:1, mm) + `drawing.dxf` (R12): top + side view, overall and offset dimensions, `4x Ø THRU ALL` callout, hole table, title block. Dimensions are annotation geometry, not associative DXF `DIMENSION` entities |
| Mass / volume / surface properties | Mass properties / Inspect / Mass Properties | **have (new)** | — | analytical volume, surface area, bounding box, centre of mass, webs; mass for a nominal density table (aluminium 6061, S235, 304, PMMA, ABS, PLA) — estimate, not QA |
| Part families beyond the plate | unlimited | missing | P1 | candidates: L-bracket, flat bar with slot, round flange (bolt circle). Each needs IR + worker macro + worker QA + V1B adapter allowlist + eval cases |
| Hole patterns beyond four-corner | Hole Wizard patterns, rectangular / circular pattern | missing | P1 | rectangular N×M grid and bolt circle are the next deterministic patterns; QA currently requires exactly 4 bores |
| Hole types (counterbore, countersink, tapped) | Hole feature / Hole Wizard | missing | P2 | needs PartDesign::Hole in the macro and STEP checks per type |
| Fillets / chamfers on edges | Fillet / Chamfer | missing | P2 | needs IR params + worker + volume formula update; today the guide says "edges sharp, do not add" |
| Slots / cut-outs / pockets with depth | yes | missing | P2 | |
| Material and appearance in the native file | Material / Appearance | missing | P2 | mass table exists only in the manual guide; FCStd has no material |
| Tolerances / GD&T on drawing | TechDraw GD&T / MBD | missing | P3 | |
| Assemblies, joints | Assembly / Joints / Mates | — | — | out of scope for a single-part generator |
| Sheet metal, bending | Sheet Metal workbench | missing | P3 | |
| FEA / stress | FEM / Simulation | — | — | the README states QA is not engineering certification |
| CAM toolpaths | Path / Manufacture / CAM | — | — | DXF is the hand-off to CAM |
| Free-form modelling, surfaces | yes | — | — | not a parametric-family feature |
| AI brief → PartIR proposal (V1B) | — | have | — | allowlist unchanged by this work: `mounting-plate.v1` / `four-corner.v1` |

## Implemented in this change

1. **Traceability for every run** (accepted, replayed, rejected after the
   PartIR is valid): `.trace/history.jsonl`, `.trace/jobs/<id>/trace.json`,
   `ir.json`, `MANUAL-STEPS.md`; artifacts are hashed in place (relative paths)
   so `production_trace verify` detects tampering of the published files.
2. **Dimensioned 2D drawing** (`cad_production/drawing.py`) from PartIR.
3. **Analytical properties** (`cad_production/properties.py`) used by the
   guide and drawing; mass table for quoting.
4. **AI provenance**: `cad brief` writes `<output>.proposal.json`; `cad build`
   records it only when the PartIR hash matches.

## Roadmap (prioritised)

1. P1 — Rectangular N×M and bolt-circle hole patterns (IR enum + macro +
   QA bore count + guide loop + V1B allowlist + eval cases).
2. P1 — Second part family (L-bracket) with its own validator, macro, QA.
3. P2 — Edge fillet/chamfer parameters with closed-form volume update.
4. P2 — Counterbore/countersink holes via `PartDesign::Hole`.
5. P2 — Material assignment written into FCStd; mass in the QA manifest.
6. P3 — Associative DXF `DIMENSION` entities / TechDraw page in the FCStd.

## Recommended change to the shared trace package (not done here)

`production_trace.TraceRecorder.artifact()` records only `file.name`, and
`verify_trace` resolves it next to `trace.json`. Tools whose published
directory must hold an exact artifact set (CAD, BIM, Game) therefore append
artifact records with a path relative to the trace directory
(`cad_production/tracing.py`). A `base_dir=` option on `artifact()` would remove
that per-tool helper; it must be made in the canonical
`packages/production-trace/` and mirrored to the vendored copy.
