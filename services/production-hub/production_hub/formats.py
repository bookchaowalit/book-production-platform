"""Everything-as-Spec registries: file formats, effects and the conversion graph.

``registry/formats.yaml`` names every format by domain and says whether it is
authored text, generated text or binary; ``registry/converters.yaml`` lists
what turns one format into another (implemented in Solo-Empire, available as
an open tool, or planned) and the automation level it reaches. The checker
keeps both consistent with the artifact kinds, tools and handoffs, and
``find_paths`` answers "how do I get from X to Y" over that graph.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .paths import REGISTRY_DIR
from .registry import HUB_TOOL, Registries

LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5", "L6")
TEXT_CLASSES = {"authored", "generated", "binary"}
ROLES = {"source", "ir", "artifact"}
CONVERTER_STATUSES = {"implemented", "available", "planned"}
EFFECT_PREFIX = "effect."
DOMAIN_KEYS = {"label", "max_level", "note"}
FORMAT_KEYS = {"domain", "ext", "media", "text", "roles", "kinds", "note"}
EFFECT_KEYS = {"domain", "description"}
CONVERTER_KEYS = {"id", "from", "with", "to", "tool", "level", "status", "requires", "handoff", "approval", "note"}
RUNTIME_KEYS = {"label", "commands", "env", "paths", "python_modules", "manual"}
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_EFFECT_ID = re.compile(r"^effect\.[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_PATH_HOPS = 6


@dataclass(frozen=True)
class FormatRegistry:
    levels: dict[str, str]
    domains: dict[str, dict[str, Any]]
    formats: dict[str, dict[str, Any]]
    effects: dict[str, dict[str, Any]]
    converters: list[dict[str, Any]]
    runtimes: dict[str, dict[str, Any]]

    def domain_of(self, node: str) -> str | None:
        entry = self.formats.get(node) or self.effects.get(node)
        return None if entry is None else str(entry.get("domain"))


def _load(path: Path, schema_version: str) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != schema_version:
        raise ValueError(f"{path.name}: expected schema_version {schema_version}")
    return data


def load_format_registry(registry_dir: Path = REGISTRY_DIR) -> FormatRegistry:
    formats = _load(registry_dir / "formats.yaml", "formats.v1")
    converters = _load(registry_dir / "converters.yaml", "converters.v1")
    runtimes = _load(registry_dir / "runtimes.yaml", "runtimes.v1")
    return FormatRegistry(
        levels=formats["levels"], domains=formats["domains"], formats=formats["formats"],
        effects=formats.get("effects") or {}, converters=converters["converters"],
        runtimes=runtimes["runtimes"],
    )


def _level(value: Any) -> int:
    return LEVELS.index(value) if value in LEVELS else -1


def _unknown_keys(prefix: str, entry: Any, allowed: set[str]) -> list[str]:
    # an unquoted comma in a YAML flow mapping silently turns text into extra keys
    if not isinstance(entry, Mapping):
        return [f"{prefix}: must be a mapping"]
    extra = sorted(str(key) for key in entry if key not in allowed)
    return [f"{prefix}: unknown keys {', '.join(extra)} (quote values that contain commas)"] if extra else []


def _check_keys(reg: FormatRegistry) -> list[str]:
    errors: list[str] = []
    for table, allowed, name in ((reg.domains, DOMAIN_KEYS, "domain"), (reg.formats, FORMAT_KEYS, "format"),
                                 (reg.effects, EFFECT_KEYS, "effect"), (reg.runtimes, RUNTIME_KEYS, "runtime")):
        for key, entry in table.items():
            errors.extend(_unknown_keys(f"{name} {key}", entry, allowed))
    for converter in reg.converters:
        errors.extend(_unknown_keys(f"converter {converter.get('id')}", converter, CONVERTER_KEYS))
    return errors


def _check_formats(reg: FormatRegistry, kinds: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if list(reg.levels) != list(LEVELS):
        errors.append(f"formats: levels must be exactly {', '.join(LEVELS)}")
    for domain_id, domain in reg.domains.items():
        if not _ID.match(domain_id):
            errors.append(f"domain {domain_id}: id must be lowercase-kebab")
        if domain.get("max_level") not in LEVELS:
            errors.append(f"domain {domain_id}: max_level must be one of {', '.join(LEVELS)}")
        if not domain.get("label"):
            errors.append(f"domain {domain_id}: needs a label")
    covered: set[str] = set()
    for format_id, entry in reg.formats.items():
        prefix = f"format {format_id}"
        if not _ID.match(format_id):
            errors.append(f"{prefix}: id must be lowercase-kebab")
        if entry.get("domain") not in reg.domains:
            errors.append(f"{prefix}: unknown domain {entry.get('domain')!r}")
        extensions = entry.get("ext")
        if not isinstance(extensions, list) or not extensions or not all(isinstance(ext, str) and ext for ext in extensions):
            errors.append(f"{prefix}: ext must be a non-empty list")
        if not isinstance(entry.get("media"), str) or "/" not in entry["media"]:
            errors.append(f"{prefix}: media must be a media type")
        if entry.get("text") not in TEXT_CLASSES:
            errors.append(f"{prefix}: text must be one of {', '.join(sorted(TEXT_CLASSES))}")
        roles = entry.get("roles")
        if not isinstance(roles, list) or not roles or not set(roles) <= ROLES:
            errors.append(f"{prefix}: roles must be a non-empty subset of {', '.join(sorted(ROLES))}")
        for kind in entry.get("kinds") or []:
            if kind not in kinds:
                errors.append(f"{prefix}: unknown artifact kind {kind!r}")
            covered.add(kind)
    for kind in sorted(set(kinds) - covered):
        errors.append(f"kind {kind}: no format stores it (add it to a format's kinds)")
    for effect_id, entry in reg.effects.items():
        if not _EFFECT_ID.match(effect_id):
            errors.append(f"effect {effect_id}: id must start with 'effect.'")
        if entry.get("domain") not in reg.domains:
            errors.append(f"effect {effect_id}: unknown domain {entry.get('domain')!r}")
        if not entry.get("description"):
            errors.append(f"effect {effect_id}: needs a description")
    return errors


def _check_converters(reg: FormatRegistry, hub: Registries, catalog_ids: Iterable[str]) -> list[str]:
    errors: list[str] = []
    known_tools = {*catalog_ids, HUB_TOOL}
    handoffs = {edge["id"]: edge for edge in hub.handoffs}
    linked: dict[str, str] = {}
    seen: set[str] = set()
    reachable_effects: set[str] = set()
    for converter in reg.converters:
        converter_id = str(converter.get("id"))
        prefix = f"converter {converter_id}"
        if not _ID.match(converter_id) or converter_id in seen:
            errors.append(f"{prefix}: id must be unique lowercase-kebab")
        seen.add(converter_id)
        status = converter.get("status")
        if status not in CONVERTER_STATUSES:
            errors.append(f"{prefix}: status must be one of {', '.join(sorted(CONVERTER_STATUSES))}")
        level = converter.get("level")
        if level not in LEVELS or level in ("L0", "L5"):
            errors.append(f"{prefix}: level must be L1, L2, L3, L4 or L6")
        sources = [*(converter.get("from") or []), *(converter.get("with") or [])]
        if not converter.get("from"):
            errors.append(f"{prefix}: needs at least one input format")
        for node in sources:
            if node not in reg.formats:
                errors.append(f"{prefix}: unknown input format {node!r}")
        targets = converter.get("to") or []
        if not targets:
            errors.append(f"{prefix}: needs at least one output")
        effects = [node for node in targets if str(node).startswith(EFFECT_PREFIX)]
        for node in targets:
            if node not in reg.formats and node not in reg.effects:
                errors.append(f"{prefix}: unknown output {node!r}")
            domain = reg.domain_of(node)
            if domain in reg.domains and _level(level) > _level(reg.domains[domain]["max_level"]):
                errors.append(
                    f"{prefix}: {level} exceeds domain {domain} max_level {reg.domains[domain]['max_level']}"
                )
        if effects:
            reachable_effects.update(effects)
            if level != "L6" or converter.get("approval") != "human":
                errors.append(f"{prefix}: effects need level L6 and approval: human")
            if len(effects) != len(targets):
                errors.append(f"{prefix}: an effect converter outputs effects only")
        elif level == "L6":
            errors.append(f"{prefix}: L6 converters must produce an effect")
        if status == "implemented":
            tool = converter.get("tool")
            if tool not in known_tools:
                errors.append(f"{prefix}: implemented converters must use a catalog tool or {HUB_TOOL}")
            elif tool != HUB_TOOL:
                produced = set(hub.tools.get(tool, {}).get("produces", []))
                for node in targets:
                    node_kinds = set((reg.formats.get(node) or {}).get("kinds") or [])
                    if node_kinds and not node_kinds & produced:
                        errors.append(f"{prefix}: {tool} does not produce any kind stored as {node}")
        if status in ("available", "planned") and converter.get("tool") in known_tools and not (
            converter.get("requires") or converter.get("handoff") or status == "planned"
        ):
            errors.append(f"{prefix}: available converters name the runtime they require")
        handoff = converter.get("handoff")
        if handoff is not None:
            edge = handoffs.get(handoff)
            if edge is None:
                errors.append(f"{prefix}: unknown handoff {handoff!r}")
            else:
                if (edge["status"] == "implemented") != (status == "implemented"):
                    errors.append(f"{prefix}: status {status} disagrees with handoff {handoff} ({edge['status']})")
                if handoff in linked:
                    errors.append(f"{prefix}: handoff {handoff} is already linked by {linked[handoff]}")
                linked[handoff] = converter_id
    for handoff_id in sorted(set(handoffs) - set(linked)):
        errors.append(f"handoff {handoff_id}: no converter links it")
    for effect_id in sorted(set(reg.effects) - reachable_effects):
        errors.append(f"effect {effect_id}: no converter reaches it")
    return errors


_PROBES = ("commands", "env", "paths", "python_modules")


def _check_runtimes(reg: FormatRegistry) -> list[str]:
    errors: list[str] = []
    required = {item for converter in reg.converters for item in converter.get("requires") or []}
    for runtime in sorted(required - set(reg.runtimes)):
        errors.append(f"runtime {runtime}: required by a converter but missing from runtimes.yaml")
    for runtime in sorted(set(reg.runtimes) - required):
        errors.append(f"runtime {runtime}: no converter requires it")
    for runtime_id, entry in reg.runtimes.items():
        if not entry.get("label"):
            errors.append(f"runtime {runtime_id}: needs a label")
        probes = [key for key in _PROBES if entry.get(key)]
        if bool(entry.get("manual")) == bool(probes):
            errors.append(f"runtime {runtime_id}: use detection probes or manual: true, not both or neither")
    return errors


def check_format_registry(reg: FormatRegistry, hub: Registries, catalog_ids: Iterable[str]) -> list[str]:
    return [*_check_keys(reg), *_check_formats(reg, hub.kinds), *_check_converters(reg, hub, catalog_ids),
            *_check_runtimes(reg)]


def _module_present(python: str, module: str) -> bool:
    code = f"import importlib.util,sys;sys.exit(0 if importlib.util.find_spec({module!r}) else 1)"
    try:
        return subprocess.run([python, "-c", code], capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def detect_runtime(entry: Mapping[str, Any], pythons: Iterable[str] = ()) -> dict[str, Any]:
    """Locate one runtime without running it (besides a module lookup)."""
    if entry.get("manual"):
        return {"status": "manual", "found": None}
    for variable in entry.get("env") or []:
        value = os.environ.get(variable)
        if value and Path(value).is_file():
            return {"status": "installed", "found": value}
    for command in entry.get("commands") or []:
        located = shutil.which(command)
        if located:
            return {"status": "installed", "found": located}
    for pattern in entry.get("paths") or []:
        matches = sorted(glob.glob(os.path.expanduser(pattern)))
        if matches:
            return {"status": "installed", "found": matches[-1]}
    for module in entry.get("python_modules") or []:
        for python in pythons:
            if _module_present(python, module):
                return {"status": "installed", "found": f"{python} :: {module}"}
    return {"status": "missing", "found": None}


def _pythons() -> list[str]:
    from .adapters import _bpp_python

    return list(dict.fromkeys([sys.executable, _bpp_python()]))


def detect_runtimes(reg: FormatRegistry, pythons: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
    interpreters = list(_pythons() if pythons is None else pythons)
    return {runtime_id: {"label": entry["label"], **detect_runtime(entry, interpreters)}
            for runtime_id, entry in sorted(reg.runtimes.items())}


def summary(reg: FormatRegistry) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for converter in reg.converters:
        by_status[converter["status"]] = by_status.get(converter["status"], 0) + 1
    by_text: dict[str, int] = {}
    for entry in reg.formats.values():
        by_text[entry["text"]] = by_text.get(entry["text"], 0) + 1
    return {"domains": len(reg.domains), "formats": len(reg.formats), "effects": len(reg.effects),
            "converters": len(reg.converters), "by_status": by_status, "by_text": by_text}


def resolve_node(reg: FormatRegistry, name: str) -> list[str]:
    """Accept a format id, an effect id, or a file extension such as '.stl'."""
    if name in reg.formats or name in reg.effects:
        return [name]
    exact = sorted(format_id for format_id, entry in reg.formats.items() if name in entry["ext"])
    if exact:  # whole file names such as product.yaml, Dockerfile or SKILL.md
        return exact
    extension = name.lower() if name.startswith(".") else f".{name.lower()}"
    return sorted(format_id for format_id, entry in reg.formats.items() if extension in entry["ext"])


def find_paths(
    reg: FormatRegistry, start: str, goal: str, *, statuses: Iterable[str] = CONVERTER_STATUSES, limit: int = 5,
    installed: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[list[dict[str, Any]]]:
    """Shortest conversion routes (fewest hops, then fewest non-implemented steps)."""
    allowed = set(statuses)
    edges: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for converter in reg.converters:
        if converter["status"] not in allowed:
            continue
        for source in converter["from"]:
            for target in converter["to"]:
                if target != source:
                    edges.setdefault(source, []).append((target, converter))
    found: list[list[dict[str, Any]]] = []
    queue: deque[tuple[str, list[dict[str, Any]], frozenset[str]]] = deque([(start, [], frozenset({start}))])
    shortest: int | None = None
    while queue:
        node, route, visited = queue.popleft()
        if shortest is not None and len(route) > shortest:
            break
        if node == goal and route:
            found.append(route)
            shortest = len(route)
            continue
        if len(route) >= MAX_PATH_HOPS:
            continue
        for target, converter in edges.get(node, []):
            if target in visited:
                continue
            step = {"from": node, "to": target, "converter": converter["id"], "tool": converter["tool"],
                    "status": converter["status"], "level": converter["level"],
                    "requires": list(converter.get("requires") or []), "approval": converter.get("approval")}
            if installed is not None:
                step["missing"] = [item for item in step["requires"]
                                   if installed.get(item, {}).get("status") != "installed"]
                step["runnable"] = converter["status"] == "implemented" or (
                    converter["status"] == "available" and not step["missing"])
            queue.append((target, [*route, step], visited | {target}))
    found.sort(key=lambda route: sum(step["status"] != "implemented" for step in route))
    return found[:limit]


def route_report(
    reg: FormatRegistry, source: str, target: str, *, implemented_only: bool = False, limit: int = 5,
    installed: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Routes between two names (ids, effects or extensions) with readiness on this machine."""
    starts, goals = resolve_node(reg, source), resolve_node(reg, target)
    if not starts or not goals:
        raise KeyError(source if not starts else target)
    statuses = ["implemented"] if implemented_only else sorted(CONVERTER_STATUSES)
    routes: list[dict[str, Any]] = []
    for start in starts:
        for goal in goals:
            for route in find_paths(reg, start, goal, statuses=statuses, limit=limit, installed=installed):
                routes.append({
                    "from": start, "to": goal, "hops": len(route),
                    "ready": all(step["status"] == "implemented" for step in route),
                    "runnable_now": installed is not None and all(step.get("runnable") for step in route
                                                                 if step["approval"] != "human"),
                    "needs_approval": any(step["approval"] == "human" for step in route),
                    "requires": sorted({item for step in route for item in step["requires"]}),
                    "missing": sorted({item for step in route for item in step.get("missing", [])}),
                    "steps": route,
                })
    routes.sort(key=lambda item: (item["hops"], not item["ready"], not item["runnable_now"], len(item["missing"])))
    return {"source": source, "target": target, "routes": routes[:limit]}


__all__ = [
    "LEVELS",
    "FormatRegistry",
    "check_format_registry",
    "detect_runtime",
    "detect_runtimes",
    "find_paths",
    "load_format_registry",
    "resolve_node",
    "route_report",
    "summary",
]
