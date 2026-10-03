# BIM V1A — feature parity with Revit, Bonsai (BlenderBIM), and Solibri

Scope: `bim_production` + `bim_ir` (tool id `bim-v1a`). The engine is a
deterministic quantity-takeoff reader: one local IFC2X3/IFC4 file plus a strict
`bim-takeoff.v1` request; a pinned IfcOpenShell worker reads only
occurrence-level `IfcElementQuantity` values, the parent normalises them to SI
with exact decimals, groups by an exact semantic key, and publishes
`takeoff.json` / `takeoff.csv` / `qa-manifest.json` content-addressed. It never
authors or edits BIM models and never derives quantities from geometry.

Status: **have** = implemented and tested; **partial** = a subset works;
**missing** = not implemented. Priority: P1 next, P2 later, P3 only on demand,
— = out of scope.

Honesty note: the pinned FreeCAD + IfcOpenShell runtime is Windows-only and not
present on the host used for this change. New classes are verified at the IR,
schema, and worker-allowlist level and through the stubbed worker; the real
worker tests stay skipped here. Bonsai / Calc click paths in the manual guide
are written against Bonsai 0.8 / Calc 24.x and are not runtime-verified.

## Capability matrix

| Capability | Revit / Bonsai / Solibri | Engine status | Priority | Notes |
|---|---|---|---|---|
| Open IFC2X3 / IFC4 | yes | have | — | header + schema gate, 64 MiB / 250 000 instances |
| IFC4X3, IFCZIP, IFCXML | partial | missing | P2 | |
| Schema validation | Solibri rules / IfcOpenShell validate | have | — | `ifcopenshell.validate`, no EXPRESS rules |
| Select by IFC class | Schedules by category / Search / Model tree | **have (extended)** | — | 18 classes (was 6): + IfcBuildingElementProxy, IfcCovering, IfcCurtainWall, IfcFooting, IfcMember, IfcPile, IfcPlate, IfcRailing, IfcRamp, IfcRoof, IfcSpace, IfcStair. All exist in IFC2X3 and IFC4; none is a subtype of another, so no occurrence is selected twice |
| MEP classes (IfcFlowSegment, IfcFlowTerminal, …) | yes | missing | P2 | IFC2X3/IFC4 class names diverge (IfcDuctSegment is IFC4-only); needs per-schema allowlist |
| Read Qto quantities (length, area, volume, count) | yes | have | — | exact SI conversion, explicit unit before project default |
| Weight / time quantities (`IfcQuantityWeight`, `IfcQuantityTime`) | yes | missing | P1 | reported as `quantity_type_unsupported` today; weight needs a mass-unit path in `units.py` |
| Type-level quantities | yes | missing | P2 | deliberately ignored (double-count risk) |
| Geometry-derived quantities (no Qto) | Revit computes, Bonsai "Calculate quantities" | — | — | out of scope: never estimate; missing Qto is a coverage gap |
| Group / sum per class | Schedule totals / Information Takeoff | have | — | exact key `class + qto + quantity + kind + unit + method` |
| Coverage gaps instead of zeros | partial | have | — | missing / ambiguous / unsupported unit or type |
| Schedule export CSV | Export schedule / Spreadsheet export | have | — | RFC 4180, CRLF, deterministic bytes |
| Cost mapping (BOQ pricing) | Revit + add-ins, Bonsai cost schedules, Solibri + 5D tools | **have (new)** | — | `bim cost`: owner rate table `bim-cost-rates.v1`, exact-key match, per-line ROUND_HALF_UP to 0.01, `unpriced` never zero, ambiguous rates fail closed, `cost-estimate.json/.csv` + trace + Calc guide |
| Price database / regional rates | 5D tools | — | — | owner supplies rates; no bundled prices |
| Group by storey / zone / material | Schedule fields / filters | missing | P1 | needs spatial containment + material association in the worker; most-requested QS breakdown |
| Classification codes (Uniclass, OmniClass) | yes | missing | P2 | could feed rate-table matching by code |
| Clash detection, rule checking | Solibri core | — | — | out of scope |
| Model authoring / editing | Revit, Bonsai | — | — | read-only by design |
| Traceability per run | — | **have (new)** | — | `trace.json` (input hashes, worker summary, extract/report/qa/publish), `history.jsonl`, `ir.json`; failed and raised runs recorded |
| "How would I do this by hand" | — | **have (new)** | — | `MANUAL-STEPS.md` for Bonsai + Calc (every published row and aggregate), with FreeCAD BIM / IfcOpenShell / Revit / Solibri equivalents |

## Implemented in this change

1. Trace + history + manual guide for every takeoff run with a valid IR.
2. IFC class allowlist 6 → 18 (IR model, JSON schema, worker allowlist kept in
   sync and tested). No BIM V1B brief adapter exists, so there is no adapter
   allowlist to update. Caution in README: IfcRoof/IfcStair can aggregate
   IfcSlab/IfcStairFlight children that carry their own Qto; selecting and
   pricing both double counts.
3. Deterministic cost mapping (`bim_production/cost.py`, `bim cost` CLI).

## Roadmap (prioritised)

1. P1 — Group by building storey (IfcRelContainedInSpatialStructure) as an
   optional aggregate key.
2. P1 — `IfcQuantityWeight` (kg) with exact mass-unit conversion.
3. P2 — Per-schema MEP class allowlist.
4. P2 — Classification references as an optional rate-table key.
5. P2 — IFC4X3 schema support.
