"""Load and cross-check the Production Hub registries.

Three registries live under ``registry/``:

- ``kinds.yaml``: the artifact kind vocabulary and allowed media types.
- ``tool-io.yaml``: produced/consumed kinds per cataloged tool.
- ``handoffs.yaml``: cross-tool edges, implemented or planned.

``check_registries`` returns human-readable errors; an empty list means the
registries agree with each other and with ``catalog/production-tools.yaml``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .paths import CATALOG_PATH, REGISTRY_DIR, REPO_ROOT, WORKSPACE_ROOT

EXTERNAL_TOOL = "external"
HUB_TOOL = "production-hub"
HANDOFF_STATUSES = {"implemented", "planned"}
TOOL_IO_FIELDS = ("produces", "consumes", "planned_produces", "planned_consumes")
_CONTRACT_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.v[0-9]+$")
_KIND_PATTERN = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")


@dataclass(frozen=True)
class Registries:
    kinds: dict[str, dict[str, Any]]
    tools: dict[str, dict[str, list[str]]]
    handoffs: list[dict[str, Any]]


def _load_yaml(path: Path, schema_version: str) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != schema_version:
        raise ValueError(f"{path.name}: expected schema_version {schema_version}")
    return data


def load_registries(registry_dir: Path = REGISTRY_DIR) -> Registries:
    kinds = _load_yaml(registry_dir / "kinds.yaml", "artifact-kinds.v1")["kinds"]
    tools = _load_yaml(registry_dir / "tool-io.yaml", "tool-io.v1")["tools"]
    handoffs = _load_yaml(registry_dir / "handoffs.yaml", "handoffs.v1")["handoffs"]
    return Registries(kinds=kinds, tools=tools, handoffs=handoffs)


def load_catalog_tool_ids(catalog_path: Path = CATALOG_PATH) -> list[str]:
    data = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    return [str(tool["id"]) for tool in data["tools"]]


def _check_kinds(kinds: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for kind, spec in kinds.items():
        if not _KIND_PATTERN.match(kind):
            errors.append(f"kinds: invalid kind name {kind!r}")
        media_types = spec.get("media_types") if isinstance(spec, dict) else None
        if not media_types or not all(isinstance(item, str) and "/" in item for item in media_types):
            errors.append(f"kinds: {kind!r} needs at least one media type")
        if not isinstance(spec, dict) or not spec.get("description"):
            errors.append(f"kinds: {kind!r} needs a description")
    return errors


def _check_tools(
    tools: Mapping[str, Mapping[str, Any]], kinds: Mapping[str, Any], catalog_ids: list[str]
) -> list[str]:
    errors: list[str] = []
    missing = sorted(set(catalog_ids) - set(tools))
    extra = sorted(set(tools) - set(catalog_ids))
    if missing:
        errors.append(f"tool-io: catalog tools without an entry: {', '.join(missing)}")
    if extra:
        errors.append(f"tool-io: entries not in the catalog: {', '.join(extra)}")
    for tool_id, spec in tools.items():
        for field in TOOL_IO_FIELDS:
            values = spec.get(field)
            if not isinstance(values, list):
                errors.append(f"tool-io: {tool_id}.{field} must be a list")
                continue
            for kind in values:
                if kind not in kinds:
                    errors.append(f"tool-io: {tool_id}.{field} uses unknown kind {kind!r}")
            if len(set(values)) != len(values):
                errors.append(f"tool-io: {tool_id}.{field} lists a kind twice")
        for shipped, planned in (("produces", "planned_produces"), ("consumes", "planned_consumes")):
            overlap = set(spec.get(shipped) or []) & set(spec.get(planned) or [])
            if overlap:
                errors.append(f"tool-io: {tool_id} lists {sorted(overlap)} as both {shipped} and {planned}")
    return errors


def _check_handoffs(
    handoffs: Sequence[Mapping[str, Any]],
    tools: Mapping[str, Mapping[str, Any]],
    kinds: Mapping[str, Any],
    evidence_roots: Sequence[Path],
) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in handoffs:
        edge_id = str(edge.get("id", "<missing id>"))
        prefix = f"handoffs: {edge_id}"
        if edge_id in seen_ids:
            errors.append(f"{prefix}: duplicate id")
        seen_ids.add(edge_id)
        status = edge.get("status")
        from_tool, from_kind, to_tool = edge.get("from_tool"), edge.get("from_kind"), edge.get("to_tool")
        if status not in HANDOFF_STATUSES:
            errors.append(f"{prefix}: status must be one of {sorted(HANDOFF_STATUSES)}")
            continue
        key = (str(from_tool), str(from_kind), str(to_tool))
        if key in seen_edges:
            errors.append(f"{prefix}: duplicate edge {from_tool} {from_kind} -> {to_tool}")
        seen_edges.add(key)
        if from_kind not in kinds:
            errors.append(f"{prefix}: unknown kind {from_kind!r}")
            continue
        implemented = status == "implemented"

        if from_tool != EXTERNAL_TOOL:
            source = tools.get(str(from_tool))
            if source is None:
                errors.append(f"{prefix}: unknown from_tool {from_tool!r}")
            else:
                produced = set(source["produces"]) | (set() if implemented else set(source["planned_produces"]))
                if from_kind not in produced:
                    errors.append(f"{prefix}: {from_tool} does not produce {from_kind}")

        if to_tool != HUB_TOOL:
            target = tools.get(str(to_tool))
            if target is None:
                errors.append(f"{prefix}: unknown to_tool {to_tool!r}")
            else:
                consumed = set(target["consumes"]) | (set() if implemented else set(target["planned_consumes"]))
                if from_kind not in consumed:
                    errors.append(f"{prefix}: {to_tool} does not consume {from_kind}")
        if to_tool == from_tool:
            errors.append(f"{prefix}: an edge must connect two different tools")

        contract = edge.get("request_contract")
        if implemented:
            if not isinstance(contract, str) or not _CONTRACT_PATTERN.match(contract):
                errors.append(f"{prefix}: implemented edges need a versioned request_contract")
            evidence = edge.get("evidence")
            if not isinstance(evidence, str) or not any((root / evidence).is_file() for root in evidence_roots):
                errors.append(f"{prefix}: implemented edges need an existing evidence file")
        elif contract != "tbd" and not (isinstance(contract, str) and _CONTRACT_PATTERN.match(contract)):
            errors.append(f"{prefix}: request_contract must be a versioned contract or 'tbd'")
    return errors


def check_registries(
    registries: Registries, catalog_ids: list[str], repo_root: Path | None = None
) -> list[str]:
    # Evidence may live in this checkout or in the Solo Empire workspace docs.
    evidence_roots = (repo_root,) if repo_root else (REPO_ROOT, WORKSPACE_ROOT)
    return [
        *_check_kinds(registries.kinds),
        *_check_tools(registries.tools, registries.kinds, catalog_ids),
        *_check_handoffs(registries.handoffs, registries.tools, registries.kinds, evidence_roots),
    ]
