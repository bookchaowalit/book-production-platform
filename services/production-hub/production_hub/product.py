"""Product-as-Code (``product.v1``, Everything-as-Spec T2).

A ``product.yaml`` names the text/files in git that are the product's
sources and the steps that turn them into deliverables. ``build`` registers
the sources as Hub artifacts, compiles the steps into one ``hub-flow.v1``
and runs it, so gates, resume and lineage come from the flow runner.

The flow id is the product id plus a hash of the spec and the source
contents: rebuilding an unchanged product resumes the same flow (nothing is
re-run), while editing a source or a step starts a new flow.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .flows import FlowError, FlowStatus, flow_status, load_flow, run_flow, validate_flow
from .hub import ArtifactInput, Hub, Producer
from .paths import WORKSPACE_ROOT
from .registry import load_registries
from .workspace import create_project, list_projects

PRODUCT_SCHEMA_VERSION = "product.v1"
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_STEP_ID = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
TOP_KEYS = {"schema_version", "product_id", "name", "description", "project", "sources", "steps"}
SOURCE_KEYS = {"id", "path", "kind", "note"}
STEP_KEYS = {"id", "handoff", "input", "with", "params", "gate", "note"}


class ProductError(ValueError):
    """Raised when a product spec is invalid or cannot be built."""


@dataclass(frozen=True)
class ProductBuild:
    product_id: str
    flow_id: str
    project_id: str
    sources: dict[str, str]
    flow: dict[str, Any]
    status: FlowStatus | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id, "flow_id": self.flow_id, "project_id": self.project_id,
            "sources": self.sources, "flow": self.flow,
            "status": None if self.status is None else self.status.to_dict(),
        }


def load_product(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProductError(f"cannot read product spec: {exc}") from exc
    if not isinstance(data, dict):
        raise ProductError("a product spec must be a mapping")
    return data


def _selector_errors(value: Any, label: str, sources: set[str], earlier: set[str]) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{label}: must be {{source: id}} or {{from: step, kind: ...}}"]
    if "source" in value:
        extra = set(value) - {"source"}
        errors = [f"{label}: unknown keys {', '.join(sorted(extra))}"] if extra else []
        if value["source"] not in sources:
            errors.append(f"{label}: unknown source {value['source']!r}")
        return errors
    if "from" in value:
        extra = set(value) - {"from", "kind", "filter"}
        errors = [f"{label}: unknown keys {', '.join(sorted(extra))}"] if extra else []
        if value["from"] not in earlier:
            errors.append(f"{label}: 'from' must name an earlier step")
        if not isinstance(value.get("kind"), str):
            errors.append(f"{label}: 'from' selectors need a kind")
        return errors
    return [f"{label}: must be {{source: id}} or {{from: step, kind: ...}}"]


def validate_product(spec: Mapping[str, Any], base_dir: Path, repo_root: Path | None = None) -> list[str]:
    """Structural checks before anything is registered; handoff rules come from validate_flow."""
    errors: list[str] = []
    if spec.get("schema_version") != PRODUCT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {PRODUCT_SCHEMA_VERSION}")
    extra = set(spec) - TOP_KEYS
    if extra:
        errors.append(f"unknown keys {', '.join(sorted(map(str, extra)))}")
    if not isinstance(spec.get("product_id"), str) or not _ID.match(spec["product_id"]):
        errors.append("product_id must be lowercase letters, digits and -")
    if not isinstance(spec.get("name"), str) or not spec["name"].strip():
        errors.append("name is required")
    project = spec.get("project") or {}
    if not isinstance(project, Mapping) or (project.get("id") is not None and not _ID.match(str(project["id"]))):
        errors.append("project.id must be lowercase letters, digits and -")
    kinds = load_registries().kinds
    source_ids: set[str] = set()
    root = (repo_root or WORKSPACE_ROOT).resolve()
    for index, source in enumerate(spec.get("sources") or []):
        label = f"sources[{index}]"
        if not isinstance(source, Mapping):
            errors.append(f"{label}: must be a mapping")
            continue
        if set(source) - SOURCE_KEYS:
            errors.append(f"{label}: unknown keys {', '.join(sorted(map(str, set(source) - SOURCE_KEYS)))}")
        source_id = source.get("id")
        if not isinstance(source_id, str) or not _ID.match(source_id) or source_id in source_ids:
            errors.append(f"{label}: id must be unique lowercase-kebab")
        else:
            source_ids.add(source_id)
        if source.get("kind") not in kinds:
            errors.append(f"{label}: unknown artifact kind {source.get('kind')!r}")
        raw = source.get("path")
        if not isinstance(raw, str) or not raw:
            errors.append(f"{label}: path is required")
            continue
        candidate = (base_dir / raw).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            errors.append(f"{label}: {raw} must be a file inside the repository")
    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps must be a non-empty list")
        return errors
    earlier: set[str] = set()
    for index, step in enumerate(steps):
        label = f"steps[{index}]"
        if not isinstance(step, Mapping):
            errors.append(f"{label}: must be a mapping")
            continue
        if set(step) - STEP_KEYS:
            errors.append(f"{label}: unknown keys {', '.join(sorted(map(str, set(step) - STEP_KEYS)))}")
        step_id = step.get("id")
        if not isinstance(step_id, str) or not _STEP_ID.match(step_id) or step_id in earlier:
            errors.append(f"{label}: id must be unique lowercase")
        if ("gate" in step) == ("handoff" in step):
            errors.append(f"{label}: needs exactly one of handoff or gate")
        elif "gate" in step:
            gate = step["gate"]
            if not isinstance(gate, Mapping) or gate.get("from") not in earlier or not gate.get("approve_kinds"):
                errors.append(f"{label}: gate needs from (an earlier step) and approve_kinds")
        else:
            errors.extend(_selector_errors(step.get("input"), f"{label}.input", source_ids, earlier))
            for position, extra_input in enumerate(step.get("with") or []):
                errors.extend(_selector_errors(extra_input, f"{label}.with[{position}]", source_ids, earlier))
            params = step.get("params") or {}
            if not isinstance(params, Mapping):
                errors.append(f"{label}: params must be a mapping")
        if isinstance(step_id, str):
            earlier.add(step_id)
    return errors


def _selector(value: Mapping[str, Any], sources: Mapping[str, str]) -> dict[str, Any]:
    if "source" in value:
        return {"artifact": sources[str(value["source"])]}
    selector: dict[str, Any] = {"from_step": value["from"], "kind": value["kind"]}
    if value.get("filter"):
        selector["filter"] = dict(value["filter"])
    return selector


def compile_flow(spec: Mapping[str, Any], sources: Mapping[str, str]) -> dict[str, Any]:
    """product.v1 steps → hub-flow.v1, with a flow id pinned to the spec and source contents."""
    product_id = str(spec["product_id"])
    project_id = str((spec.get("project") or {}).get("id") or product_id)
    steps: list[dict[str, Any]] = []
    for step in spec["steps"]:
        if "gate" in step:
            steps.append({"id": step["id"], "gate": {"from_step": step["gate"]["from"],
                                                      "approve_kinds": list(step["gate"]["approve_kinds"])}})
            continue
        send: dict[str, Any] = {"handoff": step["handoff"], "input": _selector(step["input"], sources)}
        if step.get("with"):
            send["with"] = [_selector(item, sources) for item in step["with"]]
        if step.get("params"):
            send["params"] = {str(key): str(value) for key, value in step["params"].items()}
        steps.append({"id": step["id"], "send": send})
    pin = hashlib.sha256(json.dumps(
        {"steps": steps, "sources": dict(sorted(sources.items())), "project": project_id},
        sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")).hexdigest()[:10]
    return {"schema_version": "hub-flow.v1", "flow_id": f"{product_id}-{pin}", "project_id": project_id,
            "steps": steps}


def _ensure_project(hub: Hub, spec: Mapping[str, Any], project_id: str) -> None:
    project = spec.get("project") or {}
    with hub.transaction() as conn:
        if project_id in {item["project_id"] for item in list_projects(conn)}:
            return
        create_project(conn, project_id, str(project.get("name") or spec["name"]), project.get("client"),
                       project.get("brand_id"))


def build_product(
    hub: Hub, spec_path: Path, *, dry_run: bool = False, runner: Any = None,
) -> ProductBuild:
    spec_path = Path(spec_path)
    spec = load_product(spec_path)
    errors = validate_product(spec, spec_path.parent)
    if errors:
        raise ProductError("; ".join(errors))
    product_id = str(spec["product_id"])
    project_id = str((spec.get("project") or {}).get("id") or product_id)
    sources: dict[str, str] = {}
    if dry_run:
        for source in spec.get("sources") or []:
            content = (spec_path.parent / source["path"]).read_bytes()
            sources[source["id"]] = "sha256:" + hashlib.sha256(content).hexdigest()
    else:
        _ensure_project(hub, spec, project_id)
        with hub.transaction() as conn:
            for source in spec.get("sources") or []:
                path = spec_path.parent / source["path"]
                registered = hub.register(conn, ArtifactInput(
                    path=path, kind=str(source["kind"]),
                    producer=Producer("external", PRODUCT_SCHEMA_VERSION, f"product:{product_id}:{source['id']}"),
                    project_id=project_id, filename=path.name,
                    attributes={"product_id": product_id, "source_id": source["id"]},
                ))
                sources[source["id"]] = registered.artifact_id
    flow = compile_flow(spec, sources)
    flow_errors = validate_flow(flow)
    if flow_errors:
        raise ProductError("; ".join(flow_errors))
    if dry_run:
        return ProductBuild(product_id, flow["flow_id"], project_id, sources, flow, None)
    try:
        flow_id = load_flow(hub, flow)
        status = run_flow(hub, flow_id) if runner is None else run_flow(hub, flow_id, runner=runner)
    except FlowError as exc:
        raise ProductError(str(exc)) from exc
    return ProductBuild(product_id, flow_id, project_id, sources, flow, status)


def product_status(hub: Hub, spec_path: Path) -> ProductBuild:
    """Status of the flow the current spec and sources compile to (without running anything)."""
    planned = build_product(hub, spec_path, dry_run=True)
    try:
        status = flow_status(hub, planned.flow_id)
    except FlowError:
        status = None
    return ProductBuild(planned.product_id, planned.flow_id, planned.project_id, planned.sources, planned.flow, status)


__all__ = [
    "PRODUCT_SCHEMA_VERSION",
    "ProductBuild",
    "ProductError",
    "build_product",
    "compile_flow",
    "load_product",
    "product_status",
    "validate_product",
]
