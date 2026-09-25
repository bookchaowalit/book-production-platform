"""Occurrence-level quantity records, exact-key aggregates, and report status."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from book_production_platform.bim_production.units import (
    COVERAGE_REASONS,
    CoverageGap,
    UnitMetadata,
    normalize_quantity,
)

MAX_EXTRACTION_RECORDS = 4000

RecordObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class QuantityRow:
    """One provenance-preserving occurrence quantity in canonical SI."""

    global_id: str
    ifc_class: str
    qto_name: str
    quantity_name: str
    measure_kind: str
    method: str | None
    value: Decimal
    unit: str


@dataclass(frozen=True, slots=True)
class ExtractionFinding:
    """A stable coverage finding with its bounded source coordinates."""

    reason: str
    global_id: str | None
    ifc_class: str | None
    qto_name: str | None
    quantity_name: str | None


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """The full extraction ledger: supported rows plus coverage findings."""

    rows: tuple[QuantityRow, ...]
    findings: tuple[ExtractionFinding, ...]


@dataclass(frozen=True, slots=True)
class AggregateRow:
    """A sum over rows sharing the exact semantic aggregate key."""

    ifc_class: str
    qto_name: str
    quantity_name: str
    measure_kind: str
    unit: str
    method: str | None
    value: Decimal


REPORT_STATUS = Literal["complete", "partial", "needs_input"]


def _finding(record: RecordObject) -> ExtractionFinding:
    return ExtractionFinding(
        reason=str(record["gap"]),
        global_id=_optional_str(record.get("global_id")),
        ifc_class=_optional_str(record.get("ifc_class")),
        qto_name=_optional_str(record.get("qto_name")),
        quantity_name=_optional_str(record.get("quantity_name")),
    )


def _optional_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _unit_metadata(record: RecordObject) -> UnitMetadata | None:
    unit = record.get("unit")
    if unit is None:
        return None
    if not isinstance(unit, dict):
        raise TypeError("malformed unit metadata")
    return UnitMetadata(
        kind=str(unit["kind"]),
        name=str(unit["name"]),
        prefix=_optional_str(unit.get("prefix")),
        scale=str(unit["scale"]),
        dimension=int(unit["dimension"]),
    )


def build_extraction(records: object) -> ExtractionResult:
    """Validate worker records, normalize values, and split rows from gaps."""

    if not isinstance(records, list) or len(records) > MAX_EXTRACTION_RECORDS:
        raise TypeError("extraction records are missing or exceed their bound")
    rows: list[QuantityRow] = []
    findings: list[ExtractionFinding] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "global_id",
            "ifc_class",
            "qto_name",
            "quantity_name",
            "measure_kind",
            "method",
            "raw_value",
            "unit",
            "gap",
        }:
            raise ValueError("malformed extraction record")
        gap = record.get("gap")
        if gap is not None:
            if gap not in COVERAGE_REASONS:
                raise ValueError("unknown coverage reason")
            findings.append(_finding(record))
            continue
        normalized = normalize_quantity(
            record["raw_value"], str(record["measure_kind"]), _unit_metadata(record)
        )
        if isinstance(normalized, CoverageGap):
            findings.append(
                ExtractionFinding(
                    reason=normalized.reason,
                    global_id=str(record["global_id"]),
                    ifc_class=str(record["ifc_class"]),
                    qto_name=str(record["qto_name"]),
                    quantity_name=str(record["quantity_name"]),
                )
            )
            continue
        rows.append(
            QuantityRow(
                global_id=str(record["global_id"]),
                ifc_class=str(record["ifc_class"]),
                qto_name=str(record["qto_name"]),
                quantity_name=str(record["quantity_name"]),
                measure_kind=str(record["measure_kind"]),
                method=_optional_str(record.get("method")),
                value=normalized.value,
                unit=normalized.unit,
            )
        )
    return ExtractionResult(rows=tuple(rows), findings=tuple(findings))


def aggregate_rows(rows: tuple[QuantityRow, ...]) -> tuple[AggregateRow, ...]:
    """Sum rows only when the full exact semantic key matches."""

    totals: dict[tuple[str, str, str, str, str, str | None], Decimal] = {}
    order: list[tuple[str, str, str, str, str, str | None]] = []
    for row in rows:
        key = (
            row.ifc_class,
            row.qto_name,
            row.quantity_name,
            row.measure_kind,
            row.unit,
            row.method,
        )
        if key not in totals:
            order.append(key)
            totals[key] = row.value
        else:
            totals[key] = totals[key] + row.value
    return tuple(
        AggregateRow(
            ifc_class=key[0],
            qto_name=key[1],
            quantity_name=key[2],
            measure_kind=key[3],
            unit=key[4],
            method=key[5],
            value=totals[key],
        )
        for key in order
    )


def report_status(result: ExtractionResult) -> REPORT_STATUS:
    """Complete only without gaps; needs_input when nothing is supported."""

    if not result.rows:
        return "needs_input"
    if result.findings:
        return "partial"
    return "complete"
