"""The ``artifact.v1`` record contract and its validator.

The JSON Schema in ``schemas/artifact.v1.schema.json`` carries the structure;
``validate_artifact`` adds the checks a schema cannot express: the kind must
exist in the vocabulary, the media type must be allowed for that kind, a
record may not list itself in its lineage, and ``qa.manifest_ref`` must be a
relative POSIX path.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from functools import cache
from pathlib import PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .paths import SCHEMAS_DIR

CONTRACT_ARTIFACT = "artifact.v1"


class ContractError(ValueError):
    """Raised when a record violates a Production Hub contract."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@cache
def artifact_validator() -> Draft202012Validator:
    schema = json.loads((SCHEMAS_DIR / f"{CONTRACT_ARTIFACT}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _is_relative_posix(value: str) -> bool:
    if "\\" in value or ":" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def _is_timestamp(value: str) -> bool:
    # jsonschema only enforces "date-time" when an optional RFC 3339 package is
    # installed, so the contract checks it here instead.
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_artifact(record: Mapping[str, Any], kinds: Mapping[str, Mapping[str, Any]]) -> None:
    """Validate one artifact record against the schema and the kind vocabulary."""
    errors = [
        f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(artifact_validator().iter_errors(record), key=lambda item: list(item.path))
    ]
    if errors:
        raise ContractError(errors)

    kind = record["kind"]
    if kind not in kinds:
        errors.append(f"kind: unknown kind {kind!r}")
    elif record["media_type"] not in kinds[kind]["media_types"]:
        errors.append(f"media_type: {record['media_type']!r} is not allowed for kind {kind!r}")
    if record["artifact_id"] in record["lineage"]:
        errors.append("lineage: an artifact cannot be its own input")
    if not _is_timestamp(record["created_at"]):
        errors.append("created_at: must be an ISO 8601 timestamp with a timezone")
    manifest_ref = record["qa"].get("manifest_ref")
    if manifest_ref is not None and not _is_relative_posix(manifest_ref):
        errors.append("qa/manifest_ref: must be a relative POSIX path inside the store")
    if errors:
        raise ContractError(errors)
