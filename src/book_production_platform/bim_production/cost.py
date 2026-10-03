"""Deterministic cost mapping: takeoff aggregates x an owner-supplied rate table.

``bim-cost-rates.v1`` maps an exact aggregate key (``ifc_class``, ``qto_name``,
``quantity_name``, ``unit`` and optionally ``method``) to a unit rate. Each
published aggregate gets at most one rate; two matching rates fail closed
(``cost_rates_ambiguous``), an aggregate without a rate is listed as
``unpriced`` (never priced at zero), and a rate nobody used is listed as
``unused``. Amounts are ``quantity x rate`` rounded per line to 0.01 with
ROUND_HALF_UP; totals are sums of the rounded lines. No model calls, no
currency conversion, no inflation or waste factors.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from book_production_platform.bim_production.errors import BimProductionError
from book_production_platform.bim_production.manual import build_cost_guide
from book_production_platform.bim_production.reports import REPORT_SCHEMA_VERSION
from book_production_platform.bim_production.tracing import (
    record_artifact,
    record_source_file,
    trace_job_dir,
    trace_root,
)
from book_production_platform.production_trace import TraceRecorder

RATES_SCHEMA_VERSION = "bim-cost-rates.v1"
ESTIMATE_SCHEMA_VERSION = "bim-cost-estimate.v1"
COST_RECIPE_VERSION = "bim-cost-mapping.v1"
MAX_RATES = 500
MAX_INPUT_BYTES = 4 * 1024 * 1024
ESTIMATE_FILES = ("cost-estimate.json", "cost-estimate.csv")
COST_CSV_COLUMNS = (
    "code",
    "ifc_class",
    "qto_name",
    "quantity_name",
    "measure_kind",
    "unit",
    "method",
    "quantity",
    "rate",
    "amount",
    "currency",
)
_CENT = Decimal("0.01")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_RATE = re.compile(r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,4})?$")
_CLASS = re.compile(r"^Ifc[A-Za-z0-9]{1,60}$")
_NAME = re.compile(r"^[A-Za-z0-9_ .:/()-]{1,128}$")
_CODE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_UNITS = frozenset({"m", "m2", "m3", "count"})
_RATE_KEYS = frozenset({"code", "description", "ifc_class", "qto_name", "quantity_name", "unit", "method", "rate"})
_REQUIRED_RATE_KEYS = frozenset({"ifc_class", "qto_name", "quantity_name", "unit", "rate"})


@dataclass(frozen=True, slots=True)
class Rate:
    ifc_class: str
    qto_name: str
    quantity_name: str
    unit: str
    rate: Decimal
    method_specified: bool
    method: str | None
    code: str | None
    description: str | None

    def matches(self, aggregate: Mapping[str, Any]) -> bool:
        return (
            aggregate["ifc_class"] == self.ifc_class
            and aggregate["qto_name"] == self.qto_name
            and aggregate["quantity_name"] == self.quantity_name
            and aggregate["unit"] == self.unit
            and (not self.method_specified or aggregate.get("method") == self.method)
        )


@dataclass(frozen=True, slots=True)
class RateTable:
    currency: str
    rates: tuple[Rate, ...]


@dataclass(frozen=True, slots=True)
class CostJobResult:
    status: str
    total: str | None
    currency: str | None
    line_count: int
    unpriced_count: int
    error_code: str | None = None
    trace_job_id: str | None = field(default=None, compare=False)


def _invalid(code: str) -> BimProductionError:
    return BimProductionError(code)


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(value)


def _load_json(path: Path, code: str) -> tuple[dict[str, Any], bytes]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
            raise _invalid(code)
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except BimProductionError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        raise _invalid(code) from None
    if not isinstance(value, dict):
        raise _invalid(code)
    return value, payload


def parse_rate_table(value: object) -> RateTable:
    """Validate a ``bim-cost-rates.v1`` document strictly."""

    if not isinstance(value, dict) or set(value) != {"schema_version", "currency", "rates"}:
        raise _invalid("invalid_cost_rates")
    if value["schema_version"] != RATES_SCHEMA_VERSION:
        raise _invalid("invalid_cost_rates")
    currency = value["currency"]
    entries = value["rates"]
    if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency):
        raise _invalid("invalid_cost_rates")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_RATES:
        raise _invalid("invalid_cost_rates")
    rates: list[Rate] = []
    seen: set[tuple[str, str, str, str, bool, str | None]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not _REQUIRED_RATE_KEYS <= set(entry) <= _RATE_KEYS:
            raise _invalid("invalid_cost_rates")
        texts = {name: entry[name] for name in ("ifc_class", "qto_name", "quantity_name", "unit", "rate")}
        if not all(isinstance(item, str) for item in texts.values()):
            raise _invalid("invalid_cost_rates")
        method = entry.get("method")
        code = entry.get("code")
        description = entry.get("description")
        if (
            not _CLASS.fullmatch(texts["ifc_class"])
            or not _NAME.fullmatch(texts["qto_name"])
            or not _NAME.fullmatch(texts["quantity_name"])
            or texts["unit"] not in _UNITS
            or not _RATE.fullmatch(texts["rate"])
            or (method is not None and (not isinstance(method, str) or not _NAME.fullmatch(method)))
            or (code is not None and (not isinstance(code, str) or not _CODE.fullmatch(code)))
            or (description is not None and (not isinstance(description, str) or len(description) > 200))
        ):
            raise _invalid("invalid_cost_rates")
        rate = Rate(
            ifc_class=texts["ifc_class"],
            qto_name=texts["qto_name"],
            quantity_name=texts["quantity_name"],
            unit=texts["unit"],
            rate=Decimal(texts["rate"]),
            method_specified="method" in entry,
            method=method,
            code=code,
            description=description,
        )
        key = (rate.ifc_class, rate.qto_name, rate.quantity_name, rate.unit, rate.method_specified, rate.method)
        if key in seen:
            raise _invalid("invalid_cost_rates")
        seen.add(key)
        rates.append(rate)
    return RateTable(currency=currency, rates=tuple(rates))


def _report_aggregates(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    if report.get("schema_version") != REPORT_SCHEMA_VERSION or not isinstance(report.get("aggregates"), list):
        raise _invalid("invalid_takeoff_report")
    aggregates: list[dict[str, Any]] = []
    for item in report["aggregates"]:
        if not isinstance(item, dict) or not {"ifc_class", "qto_name", "quantity_name", "measure_kind", "unit", "method", "value"} <= set(item):
            raise _invalid("invalid_takeoff_report")
        try:
            value = Decimal(str(item["value"]))
        except InvalidOperation:
            raise _invalid("invalid_takeoff_report") from None
        if not value.is_finite():
            raise _invalid("invalid_takeoff_report")
        aggregates.append(dict(item))
    return aggregates


def _key(item: Mapping[str, Any]) -> dict[str, Any]:
    return {name: item.get(name) for name in ("ifc_class", "qto_name", "quantity_name", "measure_kind", "unit", "method")}


def build_cost_estimate(
    report: Mapping[str, Any],
    table: RateTable,
    *,
    report_sha256: str,
    rates_sha256: str,
) -> dict[str, Any]:
    """Price every aggregate with exactly one matching rate; list the rest."""

    aggregates = _report_aggregates(report)
    lines: list[dict[str, Any]] = []
    unpriced: list[dict[str, Any]] = []
    used: set[int] = set()
    subtotals: dict[str, Decimal] = {}
    for aggregate in aggregates:
        matches = [index for index, rate in enumerate(table.rates) if rate.matches(aggregate)]
        if len(matches) > 1:
            raise _invalid("cost_rates_ambiguous")
        if not matches:
            unpriced.append(_key(aggregate))
            continue
        rate = table.rates[matches[0]]
        used.add(matches[0])
        quantity = Decimal(str(aggregate["value"]))
        amount = (quantity * rate.rate).quantize(_CENT, rounding=ROUND_HALF_UP)
        subtotals[aggregate["ifc_class"]] = subtotals.get(aggregate["ifc_class"], Decimal(0)) + amount
        lines.append(
            {
                "code": rate.code,
                "description": rate.description,
                **_key(aggregate),
                "quantity": str(quantity),
                "rate": str(rate.rate),
                "amount": str(amount),
            }
        )
    total = sum((Decimal(line["amount"]) for line in lines), Decimal(0)).quantize(_CENT)
    takeoff_status = report.get("status")
    return {
        "schema_version": ESTIMATE_SCHEMA_VERSION,
        "recipe_version": COST_RECIPE_VERSION,
        "takeoff_id": report.get("takeoff_id"),
        "takeoff_status": takeoff_status,
        "report_sha256": report_sha256,
        "rates_sha256": rates_sha256,
        "currency": table.currency,
        "status": "complete" if not unpriced and takeoff_status == "complete" else "partial",
        "rounding": "per line, 0.01, ROUND_HALF_UP",
        "lines": lines,
        "subtotals": [{"ifc_class": name, "amount": str(amount)} for name, amount in sorted(subtotals.items())],
        "total": str(total),
        "unpriced": unpriced,
        "unused_rates": [
            rate.code or f"{rate.ifc_class}/{rate.qto_name}/{rate.quantity_name}/{rate.unit}"
            for index, rate in enumerate(table.rates)
            if index not in used
        ],
    }


def render_estimate_json(estimate: Mapping[str, Any]) -> bytes:
    return (json.dumps(estimate, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def render_estimate_csv(estimate: Mapping[str, Any]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(COST_CSV_COLUMNS)
    for line in estimate["lines"]:
        writer.writerow(
            [line["code"] or "", line["ifc_class"], line["qto_name"], line["quantity_name"], line["measure_kind"],
             line["unit"], line["method"] or "", line["quantity"], line["rate"], line["amount"], estimate["currency"]]
        )
    return buffer.getvalue().encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def build_cost_estimate_job(report_path: Path, rates_path: Path, output_dir: Path) -> CostJobResult:
    """Write ``cost-estimate.json`` + ``.csv`` and a production trace; raise on invalid input."""

    trace = TraceRecorder(
        tool_id="bim-v1a-cost", engine_version=COST_RECIPE_VERSION, recipe_version=COST_RECIPE_VERSION,
        schema_version=RATES_SCHEMA_VERSION,
    )
    output = Path(output_dir)
    estimate: dict[str, Any] | None = None
    try:
        with trace.step("validate", "load_takeoff_report"):
            report, report_bytes = _load_json(report_path, "invalid_takeoff_report")
        with trace.step("validate", "load_rate_table") as step:
            rates_doc, rates_bytes = _load_json(rates_path, "invalid_cost_rates")
            table = parse_rate_table(rates_doc)
            step.params = {"currency": table.currency, "rates": len(table.rates)}
        report_sha = hashlib.sha256(report_bytes).hexdigest()
        rates_sha = hashlib.sha256(rates_bytes).hexdigest()
        with trace.step("price", "map_aggregates_to_rates", rounding="0.01 ROUND_HALF_UP") as step:
            estimate = build_cost_estimate(report, table, report_sha256=report_sha, rates_sha256=rates_sha)
            step.params.update({"lines": len(estimate["lines"]), "unpriced": len(estimate["unpriced"])})
        if output.is_symlink():
            raise _invalid("unsafe_output_path")
        output.mkdir(parents=True, exist_ok=True)
        with trace.step("render", "write_estimate", formats=["json", "csv"]) as step:
            _atomic_write(output / "cost-estimate.json", render_estimate_json(estimate))
            _atomic_write(output / "cost-estimate.csv", render_estimate_csv(estimate))
            step.outputs = list(ESTIMATE_FILES)
    except BimProductionError as error:
        _trace_cost_failure(trace, output, report_path, rates_path, error.code)
        raise
    except OSError:
        _trace_cost_failure(trace, output, report_path, rates_path, "publication_failed")
        raise BimProductionError("publication_failed") from None

    result = CostJobResult(
        status=estimate["status"], total=estimate["total"], currency=estimate["currency"],
        line_count=len(estimate["lines"]), unpriced_count=len(estimate["unpriced"]),
    )
    try:
        job_dir = trace_job_dir(output, trace.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        record_source_file(trace, "takeoff_report", report_path, role="source")
        record_source_file(trace, "cost_rates", rates_path, role="source")
        trace.ir_document(rates_doc, schema=RATES_SCHEMA_VERSION)
        for name in ESTIMATE_FILES:
            record_artifact(trace, output / name, job_dir=job_dir, media_type="application/json" if name.endswith(".json") else "text/csv")
        trace.finish(
            status="accepted", job_dir=job_dir, output_root=trace_root(output), guide=build_cost_guide(estimate),
            qa={"status": estimate["status"], "checks": estimate["unpriced"]},
        )
    except (OSError, ValueError, TypeError, KeyError):
        return result
    return replace(result, trace_job_id=trace.job_id)


def _trace_cost_failure(trace: TraceRecorder, output: Path, report_path: Path, rates_path: Path, code: str) -> None:
    """Record the failed run; never mask the original error."""

    try:
        if output.is_symlink():
            return
        job_dir = trace_job_dir(output, trace.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        for name, path in (("takeoff_report", report_path), ("cost_rates", rates_path)):
            if path.is_file() and not path.is_symlink():
                record_source_file(trace, name, path)
        trace.record_step("outcome", "failed", note=code)
        trace.finish(status="failed", job_dir=job_dir, output_root=trace_root(output))
    except (OSError, ValueError, TypeError):
        pass


__all__ = [
    "COST_CSV_COLUMNS",
    "ESTIMATE_SCHEMA_VERSION",
    "RATES_SCHEMA_VERSION",
    "CostJobResult",
    "RateTable",
    "build_cost_estimate",
    "build_cost_estimate_job",
    "parse_rate_table",
    "render_estimate_csv",
    "render_estimate_json",
]
