---
title: BIM Quantity Takeoff V1A — Design
aliases: [BIM Quantity Takeoff, IFC Quantity Takeoff]
tags: [product, bim, ifc, quantity-takeoff, design]
created: 2026-09-25
updated: 2026-09-25
status: approved-written-design
description: Deterministic, provenance-preserving extraction of explicit IFC element quantities into a reviewable schedule.
related:
  - ../plans/2026-09-22-production-platform-next-products.md
  - 2026-09-25-cad-parametric-part-generator-design.md
---

# BIM Quantity Takeoff V1A

## Decision

Build a local, deterministic BIM Quantity Takeoff adapter in the existing
`book-production-platform` repository. It reads one bounded IFC file and a
versioned `BimTakeoffIR`, validates the IFC structure, extracts only explicit
occurrence-level `IfcElementQuantity` values with their source identity, and
writes JSON, CSV, and QA evidence. It converts supported length, area, volume,
and count quantities to SI without deriving missing values from geometry.

The normal path makes no model calls. A missing or unsupported quantity is
reported as a coverage gap; the system never fills it with a guess. Geometry-
derived quantity calculation is a separate V1B design decision.

## User outcome and acceptance

A user with a locally available IFC2X3 or IFC4 model can request quantities for
an allowlisted set of element classes and receive an editable CSV schedule, a
machine-readable report, and a manifest showing which IFC elements and quantity
sets support each result.

V1A is accepted when synthetic IFC fixtures demonstrate all of the following:

- valid IFC-SPF syntax and schema are accepted for the supported schemas;
- selected elements and explicit occurrence-level quantities are extracted
  with GlobalId, IFC class, quantity-set name, quantity name, and measure kind;
- explicit quantity units or project defaults convert correctly to metres,
  square metres, cubic metres, and unitless counts;
- aggregates combine only rows with the same class, quantity-set name,
  quantity name, measure kind, normalized unit, and method of measurement;
- missing, ambiguous, unsupported, or unnormalizable quantities are visible as
  stable coverage findings and are never inferred;
- malformed syntax, unsupported schemas, invalid identity, malformed IFC unit
  assignments, unsafe paths, oversized input, timeout, and tampered replay fail
  closed without publishing a misleading takeoff;
- repeated identical jobs verify the prior manifest and artifacts rather than
  rebuilding the report, and the normal route reports `model_calls=0`.

This synthetic gate proves deterministic extraction behavior only. It does not
establish measurement accuracy for arbitrary authoring tools or certify
quantities for contracts, tendering, payment, construction, or regulation.

## Scope

### Included

- One local `.ifc` STEP Physical File input. IFCZIP, IFCXML, IFC4X3, linked
  external models, and remote URLs are rejected in V1A.
- Supported schemas: IFC2X3 and IFC4, after a startup/runtime probe confirms the
  pinned parser supports each requested schema.
- A strict `bim-takeoff.v1` `BimTakeoffIR` that selects one or more classes from
  `IfcWall`, `IfcSlab`, `IfcColumn`, `IfcBeam`, `IfcDoor`, and `IfcWindow` and
  requests SI output. It cannot contain scripts, expressions, paths, URLs,
  arbitrary IFC query text, or user-defined measurement formulas.
- Occurrence-level `IfcElementQuantity` extraction for `IfcQuantityLength`,
  `IfcQuantityArea`, `IfcQuantityVolume`, and `IfcQuantityCount` only. Type-level
  inherited quantities are not counted, preventing silent duplication across
  occurrences.
- A per-element quantity ledger, coverage summary, exact-key aggregates, CSV,
  JSON report, QA manifest, content-addressed jobs, and integrity-checked replay.
- Local CLI integration, synthetic fixtures, benchmark, operational notes, and
  bounded parser execution.

### Excluded

- Geometry-derived net/gross area or volume; openings, voids, deductions,
  material layers, waste factors, and measurement-method interpretation.
- Type-level quantity inheritance, classification or cost mapping, pricing,
  scheduling, clash detection, code compliance, model authoring, and IFC edits.
- Uploads, service/API endpoints, database migrations, cloud storage, external
  messages, deployment, and destructive IFC changes.
- LLM, image generation, hosted inference, or any network call in the standard
  path. Natural-language mapping can be considered later as a bounded optional
  adapter; it may not calculate or override quantities.

## Repository and runtime boundary

The product implementation belongs in
``.
Add `bim_ir/` and `bim_production/` packages beside the existing CAD and data
adapters. The root Solo Empire repository owns this spec, implementation plan,
roadmap/catalog entries, and summary evidence; it must not copy IFC fixtures,
source code, or generated takeoff artifacts from the product runtime.

The local portable runtime is FreeCAD 1.1.3 / Python 3.11 with IfcOpenShell
0.8.4. A read-only probe on 2026-09-25 verified the IFC2X3 and IFC4 schema
factories; IFC-SPF write/open; occurrence-level QTO extraction with type
inheritance disabled; project-unit lookup and mm-to-SI scale; property-unit
fallback; and schema validation with zero findings on a valid synthetic IFC4
fixture. Opening malformed SPF was rejected by the parser. The FreeCADCmd
SHA-256 matches the existing runtime lock
(`8feb06052673cdcd56df60d856cc5e6fbf8cf862d56f1419e80a558f505a7b23`), and the
IfcOpenShell native wrapper SHA-256 is
`C505947C7D8C1342195498A0E48F38A5DA35581104DF8B5556F930FBCF297B78`.

Windows cannot import the native wrapper through its current deep path: the
`.pyd` path is 274 characters. The same pinned files work when the worker adds
the existing `site-packages` directory through `GetShortPathNameW` (181
characters) before importing. V1A must use that short path, keep every loaded
module path below the Windows path limit, and fail with a stable runtime-path
error if a usable short path is unavailable. This avoids copying or installing
a second runtime. The bundled package does not include `ifcopenshell.simple_spf`;
`ifcopenshell.open()` provides the syntax-parse gate instead. EXPRESS rule
execution (`express_rules=True`) currently fails because `_pytest` is absent, so
V1A uses schema validation only and does not claim EXPRESS where-rule coverage.
Do not add dependencies or another runtime; return to this design for review if
the short-path bootstrap or required schema/QTO/unit APIs fail in the worker.
Run the parser without the FreeCAD GUI and without modifying the source IFC.

The V1A pilot is limited to the tested local Windows x86-64 runtime. Treat input
as trusted local project data, not hostile uploads. Bound the worker to 120
seconds, IFC input to 64 MiB, and the parsed model to 250,000 IFC instances; use
`shell=False`, closed stdin, a private temporary working directory, bounded
diagnostics, and stable error codes. No OS-level sandbox is claimed.

## Contracts and artifacts

`BimTakeoffIR` is strict JSON with a schema version, safe takeoff identifier,
allowlisted element classes, and fixed SI output policy. Reject duplicate JSON
keys, unknown fields, invalid types, oversized documents, and query-like input.
The IFC source path is a separate CLI argument and is excluded from canonical
IR, manifests, benchmark summaries, and logs. Fingerprints include the source
IFC SHA-256, canonical IR, IFC schema, recipe version, parser/runtime identity,
and report schema version.

An accepted job directory contains exactly:

- `takeoff.json` — schema, source content hash, validation summary, per-element
  quantity records, exact-key aggregates, and coverage gaps;
- `takeoff.csv` — the same per-element records in a spreadsheet-friendly form;
- `qa-manifest.json` — artifact sizes and hashes, model/schema identity,
  quantity counts, coverage findings, runtime identity, `model_calls: 0`, and
  `monetary_cost: "unknown"`.

The report does not include a local path or raw source-file excerpts. Each
quantity row carries the element GlobalId, IFC class, `IfcElementQuantity` name,
quantity name, measure kind, original method of measurement when present,
normalized value, and SI unit. Aggregates sum only exact semantic keys; gross
and net quantities, different methods, or different source QTO names never
merge. Missing QTOs appear in `coverage_gaps` and are not treated as zero.

The output is content-addressed and published atomically only after IFC
validation, extraction, unit checks, and artifact QA succeed. Exact replay
reopens the receipt and all three artifacts and verifies their bytes before
returning `replayed`. A failed or incomplete parse cannot overwrite an accepted
job. Existing output directories are never overwritten.

## Validation, units, and failure behavior

Before launching the parser, validate the strict IR, regular-file input,
symlink/junction boundaries, extension, size, and output path. The worker opens
the IFC-SPF with IfcOpenShell to check syntax, then runs schema validation with
`express_rules=False` in the pinned runtime. V1A does not run EXPRESS where
rules. Unsupported schemas, parse errors,
duplicate or missing GlobalIds on selected elements, invalid numeric values, or
malformed IFC unit assignments are stable failures; no artifact set is
published. Duplicate matching quantity names, unsupported measure kinds, and
unresolved physical units are coverage gaps, not numeric values; other valid
rows may still be published with `partial` status.

Use an explicit unit attached to the quantity when present, otherwise the
project default unit. Convert only recognized, dimensionally matching units to
SI; if neither source provides a usable unit for a physical quantity, emit a
`quantity_unit_missing` or `quantity_unit_unsupported` coverage gap and omit
that value. `IfcQuantityCount` is unitless. Do not infer units from project
names, numeric magnitude, geometry, or a model's display settings. Quantity
values preserve the source value under deterministic unit conversion and
canonical serialization; they are not engineering measurements with a
guaranteed tolerance.

An element with no supported occurrence-level QTO is listed as unreported. The
report remains reviewable as `partial` only when the IFC itself validates and
at least one requested quantity is supported; a model with no measurable
requested quantities returns `needs_input` and is not published as an accepted
takeoff. Unsupported quantity kinds, duplicate matching quantities, missing or
unsupported physical units, and absent quantities become stable coverage gaps;
their values are omitted from records and aggregates. A malformed unit
assignment that makes the IFC itself invalid remains a validation failure.
Partial status must be represented in CLI exit behavior, report, and manifest so
automation cannot mistake it for complete coverage.

Expected fatal errors include `invalid_bim_takeoff_ir`, `ifc_path_invalid`,
`ifc_input_too_large`, `unsupported_ifc_schema`, `ifc_parse_invalid`,
`ifc_schema_invalid`, `ifc_identity_invalid`, `worker_deadline_exceeded`,
`takeoff_integrity_error`, and
`publication_failed`. Stable coverage reasons include `quantity_missing`,
`quantity_ambiguous`, `quantity_type_unsupported`, `quantity_unit_missing`, and
`quantity_unit_unsupported`.

## Security, privacy, and cost

- Keep all IFC input, worker temp data, manifests, and outputs local under the
  owning product's ignored `.runtime/bim/` area. Never upload a model or send it
  to an external parser/provider.
- Do not execute embedded text, scripts, macros, URLs, linked resources, or
  external references in an IFC file. Disable network access for the worker.
- Do not include local paths, raw file excerpts, names, addresses, or other
  building metadata in telemetry. The local takeoff output can contain element
  identifiers and remains user-controlled project data.
- The standard route uses deterministic Python/IfcOpenShell logic and reports
  `model_calls=0`. Monetary cost remains unknown without a local compute rate
  card. Wall time and bytes are measured separately in the synthetic benchmark.
- Rollback is to disable the `bim` CLI route and retain already published local
  jobs; no destructive migration or external side effect exists.

## Verification and operating gate

The implementation plan must reproduce the short-path runtime preflight in the
actual worker before product code is accepted, with checks for runtime hashes,
imports, and the available schema/QTO/unit APIs. It must also include unit tests
with generated or synthetic IFC2X3/IFC4 models, rejection tests
for malformed/untrusted input, explicit/per-project unit conversion tests,
provenance and partial-coverage tests, exact-key aggregation tests, deterministic
replay/tamper checks, and a synthetic acceptance benchmark. The benchmark must
compare expected outcomes and quantity totals, report `model_calls=0`, keep
paths/raw IFC values out of its summary, and retain only local ignored artifacts.
Open representative reports and CSVs and inspect a valid source IFC with the
pinned tool before claiming pilot readiness.

Verification must run the focused BIM tests, Ruff, strict mypy, and the owning
product's normal checks. Any unrelated intermittent package failure is reported
separately and must not be attributed to BIM without evidence. The root catalog
and documentation guards are separate handoff checks; no product code belongs
in the root repository.

## Approval state

The user approved this revised written spec on 2026-09-25 and authorized
implementation by replying “please continue” to the implementation-plan
review gate. The accepted execution method is Native on `main`.

## References

- [IfcOpenShell validation](https://docs.ifcopenshell.org/ifcopenshell-python/validation.html) — SPF syntax and schema validation APIs; V1A runs schema validation without EXPRESS where rules.
- [IfcOpenShell element utilities](https://docs.ifcopenshell.org/autoapi/ifcopenshell/util/element/index.html) — QTO retrieval helpers.
- [IfcOpenShell unit utilities](https://docs.ifcopenshell.org/autoapi/ifcopenshell/util/unit/index.html) — property-specific and project default unit access.
