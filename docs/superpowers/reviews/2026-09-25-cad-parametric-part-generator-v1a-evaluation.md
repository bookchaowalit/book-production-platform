# CAD Parametric Part Generator V1A — Pilot Evaluation

**Date:** 2026-09-25  
**Status:** Local synthetic pilot passed; not a production or engineering release  
**Product commit:** `d0d9ac9ddfea030efeb6f141f0e673a2f2d4db9d` on `main` in the
[book-production-platform checkout](../../..)  
**Plan:** [CAD Parametric Part Generator V1A](../plans/2026-09-25-cad-parametric-part-generator-v1a.md)

## What was evaluated

The pilot converts the bounded `part-ir.v1` mounting-plate input into an
editable FreeCAD document, STEP and STL exports, an isometric PNG, and a
hash-checked QA manifest. The normal route is deterministic local execution
with no model or remote service calls.

The accepted runtime identity from the benchmark and runtime lock is FreeCAD
1.1.3, build `Revision: 20260725 (Git shallow)`, Windows x86-64, with
FreeCADCmd executable SHA-256
`8feb06052673cdcd56df60d856cc5e6fbf8cf862d56f1419e80a558f505a7b23`. The host
was Windows 11 Pro 64-bit, build 26200. The portable runtime remains in the
product repository's ignored `.runtime/cad/` directory and is not committed.

## Acceptance evidence

The synthetic benchmark completed all 20 cases with the expected outcomes:

- 5 fresh accepted builds and 5 exact replay hits;
- 8 expected input rejections and 2 tampered-artifact replay rejections;
- 0 QA failures, 0 unexpected outcomes, and `model_calls=0`;
- 692,453 artifact bytes written and 688,158 bytes reused;
- mean fresh-build latency 3.161 seconds, mean replay latency 0.156 seconds,
  and total benchmark wall time 16.908 seconds.

Each of the five accepted `.FCStd` files was opened in the FreeCAD GUI. All
retained the `Body`, `PlateSketch`, `Pad`, `HoleSketch`, and `Pocket` features
and contained one valid solid. The five PNG previews and five FreeCAD viewport
captures were visually checked for the expected plate and four through-bores.
Local visual-review JSON and screenshots are retained under the ignored product
path `.runtime/cad/pilot/visual-review-task6/`; the benchmark report is under
`.runtime/cad/pilot/benchmark-cad-v1a.json`.

Focused verification passed: 131 CAD tests, Ruff, and strict mypy across 11 CAD
source modules. The earlier full product suite passed with 422 tests and 4
skips. A later full-suite repeat failed only an unrelated Graphics batch
benchmark at 29/30 outputs; it remains an independent intermittent issue.

## Limits and handoff

These measurements are specific to this local Windows host and pinned FreeCAD
runtime. They are not a cross-platform performance guarantee; monetary compute
cost remains unknown because no approved local rate card is configured. The
pilot covers one bounded mounting-plate template and synthetic fixtures. It is
not an OS sandbox for hostile inputs, and geometry QA does not establish load
capacity, tolerance suitability, manufacturing readiness, safety, or regulatory
compliance. Broader CAD, drawings, assemblies, CAM, and real-user inputs need
separate design and verification gates.

The nested product repository has no configured remote or upstream, and no
deployment target exists. The implementation is committed locally on `main`;
there is no published remote release or deployment claim. The root catalog
records the local module boundary without inventing an external repository URL.
