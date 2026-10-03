"""Deterministic column-mapping + formula-intent compiler for Spreadsheet V1B.

An AI brief adapter (or a structured caller) may only *propose* three things:
a registered template, a mapping from source header to template parameter,
and formula intents from a fixed allowlist (sum, average, count, group-by,
lookup). This module validates such a proposal against the dataset header
and rows, and the engine — never the model — turns intents into allowlisted
formulas in the ``spreadsheet-intents.v1`` language. The result is a normal
``workbook-request.v1`` that the V1A recipe builds, evaluates, and QA-checks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from book_production_platform.spreadsheet_ir import ColumnSpec, parse_typed_value
from book_production_platform.spreadsheet_ir.models import ColumnType

from .formulas import INTENT_FORMULA_LANGUAGE

ProposalStatus = Literal["complete", "needs_input", "rejected"]
INTENT_IDS = ("sum", "average", "count", "group-by", "lookup")
GROUP_AGGREGATES = ("sum", "count")
MAX_INTENTS = 10
MAX_GROUPS = 50
MAX_HEADER_COLUMNS = 100
MAX_ROWS = 1000
DATA_SHEET = "Detail"
ANALYSIS_SHEET = "Analysis"
NUMERIC_TYPES = frozenset(("integer", "decimal"))

# Registered workbook templates: parameter -> (data type, required).
TEMPLATES: dict[str, dict[str, tuple[ColumnType, bool]]] = {
    "sales-summary": {
        "date": ("date", True),
        "category": ("text", True),
        "amount": ("decimal", True),
        "quantity": ("integer", False),
    },
    "expense-tracker": {
        "date": ("date", True),
        "category": ("text", True),
        "amount": ("decimal", True),
        "vendor": ("text", False),
    },
    "inventory-list": {
        "sku": ("text", True),
        "item": ("text", True),
        "quantity": ("integer", True),
        "unit_cost": ("decimal", False),
    },
}
_INTENT_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # intent -> (required keys, optional keys) besides "intent"
    "sum": (frozenset({"column"}), frozenset()),
    "average": (frozenset({"column"}), frozenset()),
    "count": (frozenset({"column"}), frozenset()),
    "group-by": (frozenset({"group_column", "aggregate"}), frozenset({"column"})),
    "lookup": (frozenset({"key_column", "key", "column"}), frozenset()),
}
# Any formula text proposed as a value: "=..." or a spreadsheet function call.
RAW_FORMULA = re.compile(
    r"^\s*[=+@]|\b(?:SUM|SUMIFS?|AVERAGE(?:IFS?)?|COUNTA?|COUNTIFS?|V?LOOKUP|HLOOKUP|XLOOKUP|INDEX|MATCH|IF|IFS|INDIRECT|OFFSET"
    r"|HYPERLINK|WEBSERVICE|IMPORT[A-Z]*|FILTER|QUERY|TEXT|ROUND[A-Z]*|MAX|MIN|SUMPRODUCT|CONCAT[A-Z]*)\s*\(",
    re.IGNORECASE,
)
_UNSAFE_LABEL = re.compile(r"^[=+\-@]")


@dataclass
class Dataset:
    """Source table: an ordered header and rows keyed by header text (synthetic or user data)."""

    name: str
    header: list[str]
    rows: list[dict[str, Any]]


@dataclass
class IntentIssue:
    code: str
    path: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path}


@dataclass
class IntentCompilation:
    status: ProposalStatus
    proposal: dict[str, Any] | None = None
    request: dict[str, Any] | None = None
    missing: list[str] = field(default_factory=list)
    issues: list[IntentIssue] = field(default_factory=list)


def parse_dataset(raw: Any) -> Dataset:
    """Validate a dataset envelope; raises ``ValueError`` with a stable code."""

    envelope: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    if not envelope:
        raise ValueError("dataset_invalid")
    header = envelope.get("header")
    rows = envelope.get("rows", [])
    name = str(envelope.get("name") or "data")
    if not isinstance(header, list) or not header or len(header) > MAX_HEADER_COLUMNS:
        raise ValueError("dataset_header_invalid")
    if not all(isinstance(item, str) and item.strip() for item in header) or len(set(header)) != len(header):
        raise ValueError("dataset_header_invalid")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS or not all(isinstance(row, Mapping) for row in rows):
        raise ValueError("dataset_rows_invalid")
    known = set(header)
    for row in rows:
        if set(row) - known:
            raise ValueError("dataset_rows_invalid")
    return Dataset(name=name, header=list(header), rows=[dict(row) for row in rows])


def contains_raw_formula(value: Any, *, allowed: frozenset[str] = frozenset()) -> bool:
    """True when any string in ``value`` (recursively) is formula text, except exact ``allowed`` strings."""

    if isinstance(value, str):
        return value not in allowed and bool(RAW_FORMULA.search(value))
    if isinstance(value, Mapping):
        return any(contains_raw_formula(key, allowed=allowed) or contains_raw_formula(item, allowed=allowed) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_raw_formula(item, allowed=allowed) for item in value)
    return False


def _column_letter(number: int) -> str:
    output = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        output = chr(ord("A") + remainder) + output
    return output


def _quote(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def _criterion_safe(text: str) -> bool:
    return bool(text.strip()) and not re.search(r"[*?~]|^[<>=+\-.0-9]", text) and text.upper() not in {"TRUE", "FALSE"}


def _normalize_intent(raw: Any, index: int, issues: list[IntentIssue]) -> dict[str, str] | None:
    path = f"intents[{index}]"
    if not isinstance(raw, Mapping) or not isinstance(raw.get("intent"), str):
        issues.append(IntentIssue("intent_invalid", path))
        return None
    intent = raw["intent"].strip().lower()
    if intent not in _INTENT_FIELDS:
        issues.append(IntentIssue("intent_not_allowed", path))
        return None
    required, optional = _INTENT_FIELDS[intent]
    keys = set(raw) - {"intent"}
    if not required <= keys or keys - required - optional or not all(isinstance(raw[key], str) for key in keys):
        issues.append(IntentIssue("intent_invalid", path))
        return None
    normalized = {"intent": intent, **{key: str(raw[key]).strip() for key in sorted(keys)}}
    if intent == "group-by" and (
        normalized["aggregate"] not in GROUP_AGGREGATES or (normalized["aggregate"] == "sum") != ("column" in normalized)
    ):
        issues.append(IntentIssue("intent_invalid", path))
        return None
    return normalized


def normalize_proposal(raw: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[IntentIssue], list[str]]:
    """Shape-check an untrusted proposal; returns (proposal, rejection issues, model-declared missing)."""

    issues: list[IntentIssue] = []
    if contains_raw_formula({key: value for key, value in raw.items() if key != "mapping"}) or (
        isinstance(raw.get("mapping"), Mapping) and contains_raw_formula(list(raw["mapping"].values()))
    ):
        return None, [IntentIssue("raw_formula_forbidden", "proposal")], []
    unknown = set(raw) - {"template_id", "mapping", "intents", "missing", "title"}
    if unknown:
        return None, [IntentIssue("proposal_field_not_allowed", "proposal")], []
    missing = [item for item in raw.get("missing", []) if isinstance(item, str)] if isinstance(raw.get("missing"), list) else []
    template_id = raw.get("template_id")
    if template_id is not None and (not isinstance(template_id, str) or template_id not in TEMPLATES):
        return None, [IntentIssue("template_not_allowed", "template_id")], missing
    mapping = raw.get("mapping", {})
    if not isinstance(mapping, Mapping) or not all(isinstance(key, str) and isinstance(value, str) for key, value in mapping.items()):
        return None, [IntentIssue("mapping_invalid", "mapping")], missing
    intents_raw = raw.get("intents", [])
    if not isinstance(intents_raw, list) or len(intents_raw) > MAX_INTENTS:
        return None, [IntentIssue("intent_invalid", "intents")], missing
    intents = [_normalize_intent(item, index, issues) for index, item in enumerate(intents_raw)]
    if issues:
        return None, issues, missing
    title = raw.get("title")
    proposal = {
        "template_id": template_id,
        "mapping": {key.strip(): value.strip() for key, value in mapping.items()},
        "intents": [intent for intent in intents if intent is not None],
        **({"title": title.strip()[:120]} if isinstance(title, str) and title.strip() and not _UNSAFE_LABEL.match(title.strip()) else {}),
    }
    return proposal, [], missing


def _label(intent: Mapping[str, str], labels: Mapping[str, str], group: str | None = None) -> str:
    name = intent["intent"].upper()
    if intent["intent"] == "group-by":
        target = labels.get(intent.get("column", ""), labels[intent["group_column"]])
        text = f"{intent['aggregate'].upper()} {target} [{group}]"
    elif intent["intent"] == "lookup":
        text = f"LOOKUP {labels[intent['column']]} [{intent['key']}]"
    else:
        text = f"{name} {labels[intent['column']]}"
    return "'" + text if _UNSAFE_LABEL.match(text) else text


def compile_proposal(
    proposal: Mapping[str, Any],
    dataset: Dataset,
    *,
    workbook_id: str,
    locale: str = "en-US",
    brief: str | None = None,
    grounded: Any = None,
) -> IntentCompilation:
    """Validate a normalized proposal against ``dataset`` and compile a workbook request.

    ``grounded`` (``(value, brief) -> bool``) is required for brief-derived
    proposals: lookup keys must be stated in the brief. Structured callers
    pass ``brief=None``.
    """

    issues: list[IntentIssue] = []
    missing: list[str] = []
    template_id = proposal.get("template_id")
    if not isinstance(template_id, str) or template_id not in TEMPLATES:
        return IntentCompilation("needs_input", dict(proposal), missing=["template_id"], issues=[IntentIssue("template_missing", "template_id")])
    template = TEMPLATES[template_id]
    header = set(dataset.header)

    mapping: dict[str, str] = {}
    used_params: set[str] = set()
    for source, parameter in proposal.get("mapping", {}).items():
        if source not in header:
            issues.append(IntentIssue("unknown_column", "mapping"))
            continue
        if parameter not in template:
            issues.append(IntentIssue("unknown_template_parameter", "mapping"))
            continue
        if parameter in used_params:
            issues.append(IntentIssue("parameter_mapped_twice", "mapping"))
            continue
        used_params.add(parameter)
        mapping[source] = parameter
    by_param = {parameter: source for source, parameter in mapping.items()}
    missing.extend(parameter for parameter, (_, required) in template.items() if required and parameter not in by_param)

    for source, parameter in mapping.items():
        data_type = template[parameter][0]
        for row in dataset.rows:
            try:
                parse_typed_value(row.get(source), data_type)
            except ValueError:
                issues.append(IntentIssue("mapping_type_mismatch", f"mapping.{parameter}"))
                break

    intents: list[dict[str, str]] = list(proposal.get("intents", []))
    if not intents:
        missing.append("intents")
    if not dataset.rows:
        issues.append(IntentIssue("dataset_empty", "dataset"))

    for index, intent in enumerate(intents):
        path = f"intents[{index}]"
        columns = [intent[key] for key in ("column", "group_column", "key_column") if key in intent]
        if any(column not in by_param for column in columns):
            issues.append(IntentIssue("intent_column_unmapped", path))
            continue
        value_column = intent.get("column")
        if value_column is not None and intent["intent"] != "group-by" and template[value_column][0] not in NUMERIC_TYPES:
            issues.append(IntentIssue("intent_column_not_numeric", path))
        if intent["intent"] == "group-by":
            if template[intent["group_column"]][0] != "text":
                issues.append(IntentIssue("group_column_not_text", path))
            if value_column is not None and template[value_column][0] not in NUMERIC_TYPES:
                issues.append(IntentIssue("intent_column_not_numeric", path))
        if intent["intent"] == "lookup":
            if template[intent["key_column"]][0] != "text":
                issues.append(IntentIssue("lookup_key_column_not_text", path))
                continue
            key = intent["key"]
            if brief is not None and (grounded is None or not grounded(key, brief)):
                issues.append(IntentIssue("lookup_key_ungrounded", path))
                continue
            source = by_param[intent["key_column"]]
            hits = [row for row in dataset.rows if isinstance(row.get(source), str) and row[source].casefold() == key.casefold()]
            if not _criterion_safe(key):
                issues.append(IntentIssue("lookup_key_unsupported", path))
            elif len(hits) != 1:
                issues.append(IntentIssue("lookup_key_not_unique" if hits else "lookup_key_not_found", path))
            elif hits[0].get(by_param[intent["column"]]) in (None, ""):
                issues.append(IntentIssue("lookup_value_missing", path))

    normalized = {"template_id": template_id, "mapping": dict(sorted(mapping.items())), "intents": intents}
    if "title" in proposal:
        normalized["title"] = proposal["title"]
    if issues or missing:
        return IntentCompilation("needs_input", normalized, missing=sorted(set(missing)), issues=issues)
    try:
        request = _workbook_request(normalized, dataset, template, by_param, workbook_id=workbook_id, locale=locale)
    except ValueError as exc:
        return IntentCompilation("rejected", normalized, issues=[IntentIssue(str(exc), "intents")])
    return IntentCompilation("complete", normalized, request=request)


def _workbook_request(
    proposal: Mapping[str, Any],
    dataset: Dataset,
    template: Mapping[str, tuple[ColumnType, bool]],
    by_param: Mapping[str, str],
    *,
    workbook_id: str,
    locale: str,
) -> dict[str, Any]:
    parameters = [parameter for parameter in template if parameter in by_param]
    columns = [
        ColumnSpec(key=parameter, label=by_param[parameter], data_type=template[parameter][0], required=template[parameter][1])
        for parameter in parameters
    ]
    letters = {parameter: _column_letter(index) for index, parameter in enumerate(parameters, start=1)}
    labels = {parameter: by_param[parameter] for parameter in parameters}
    end = len(dataset.rows) + 1

    def span(parameter: str) -> str:
        return f"{DATA_SHEET}!{letters[parameter]}2:{letters[parameter]}{end}"

    rows = [{parameter: row.get(by_param[parameter]) for parameter in parameters} for row in dataset.rows]
    analysis_rows: list[dict[str, Any]] = []
    formulas: list[dict[str, str]] = []

    def emit(label: str, expression: str, result_type: str) -> None:
        if len(formulas) >= 100:
            raise ValueError("too_many_formulas")
        analysis_rows.append({"metric": label, "value": None})
        formulas.append({"target": f"{ANALYSIS_SHEET}!B{len(analysis_rows) + 1}", "expression": expression, "result_type": result_type, "source_ref": "intent"})

    for intent in proposal["intents"]:
        kind = intent["intent"]
        column = intent.get("column")
        numeric_type = template[column][0] if column else "integer"
        if kind == "sum":
            emit(_label(intent, labels), f"=SUM({span(column)})", numeric_type)
        elif kind == "average":
            emit(_label(intent, labels), f"=AVERAGE({span(column)})", "decimal")
        elif kind == "count":
            emit(_label(intent, labels), f"=COUNT({span(column)})", "integer")
        elif kind == "group-by":
            group_param = intent["group_column"]
            groups: list[str] = []
            seen: set[str] = set()
            for row in rows:
                value = row.get(group_param)
                if not isinstance(value, str) or not value.strip():
                    continue
                if not _criterion_safe(value):
                    raise ValueError("group_value_unsupported")
                folded = value.casefold()
                if folded in seen:
                    if value not in groups:
                        raise ValueError("group_value_case_collision")
                    continue
                seen.add(folded)
                groups.append(value)
            if not groups or len(groups) > MAX_GROUPS:
                raise ValueError("group_count_unsupported")
            for group in groups:
                label = _label(intent, labels, group)
                if intent["aggregate"] == "sum":
                    emit(label, f"=SUMIF({span(group_param)},{_quote(group)},{span(column)})", numeric_type)
                else:
                    emit(label, f"=COUNTIF({span(group_param)},{_quote(group)})", "integer")
        else:
            expression = f"=INDEX({span(column)},MATCH({_quote(intent['key'])},{span(intent['key_column'])},0))"
            emit(_label(intent, labels), expression, numeric_type)

    title = str(proposal.get("title") or template_title(str(proposal["template_id"])))
    return {
        "schema_version": "workbook-request.v1",
        "workbook_id": workbook_id,
        "title": title,
        "locale": locale,
        "tables": [
            {"name": "data", "columns": [column.model_dump() for column in columns], "rows": rows, "source_ref": "input"},
            {
                "name": ANALYSIS_SHEET,
                "columns": [
                    {"key": "metric", "label": "Metric", "data_type": "text", "required": True},
                    {"key": "value", "label": "Value", "data_type": "text", "required": False},
                ],
                "rows": analysis_rows,
                "source_ref": "intent",
            },
        ],
        "formulas": formulas,
        "template_parameters": {"template_id": str(proposal["template_id"]), "formula_language": INTENT_FORMULA_LANGUAGE},
    }


def template_title(template_id: str) -> str:
    return template_id.replace("-", " ").title()


__all__ = [
    "ANALYSIS_SHEET",
    "INTENT_IDS",
    "RAW_FORMULA",
    "TEMPLATES",
    "Dataset",
    "IntentCompilation",
    "IntentIssue",
    "compile_proposal",
    "contains_raw_formula",
    "normalize_proposal",
    "parse_dataset",
]
