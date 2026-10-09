"""Command line entry point: ``python -m production_hub <command>``.

Exit codes: 0 success, 1 registry check failed, 2 usage or input error,
3 artifact not found, 4 contract violation, 5 store integrity failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml

from . import adapters as _adapters  # noqa: F401 - registers send adapters
from . import workspace
from .contracts import ContractError, artifact_validator
from .flows import FlowError, flow_status, load_flow, load_flow_file, run_flow
from .formats import check_format_registry, detect_runtimes, load_format_registry, route_report, summary
from .hub import ArtifactInput, Hub, Producer
from .ingest import IngestError, ingest_manifest
from .product import ProductError, build_product, product_status
from .registry import check_registries, load_catalog_tool_ids, load_registries
from .send import SendError, send
from .store import StoreError

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_CONTRACT = 4
EXIT_INTEGRITY = 5


def _print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _error(code: str, message: str, exit_code: int) -> int:
    print(json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False), file=sys.stderr)
    return exit_code


def _check() -> int:
    try:
        artifact_validator()
        registries = load_registries()
        catalog_ids = load_catalog_tool_ids()
        format_registry = load_format_registry()
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f"FAIL: could not load Production Hub contracts: {exc}")
        return EXIT_INVALID
    errors = [
        *check_registries(registries, catalog_ids),
        *check_format_registry(format_registry, registries, catalog_ids),
    ]
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return EXIT_INVALID
    implemented = sum(1 for edge in registries.handoffs if edge["status"] == "implemented")
    print(
        "OK: artifact.v1 schema valid; "
        f"{len(registries.kinds)} kinds, {len(registries.tools)} tools, "
        f"{len(registries.handoffs)} handoffs ({implemented} implemented, "
        f"{len(registries.handoffs) - implemented} planned)"
    )
    counts = summary(format_registry)
    print(
        f"OK: formats.v1 {counts['formats']} formats in {counts['domains']} domains, {counts['effects']} effects; "
        f"converters.v1 {counts['converters']} converters "
        + ", ".join(f"{count} {status}" for status, count in sorted(counts["by_status"].items()))
    )
    return EXIT_OK


def _formats(args: argparse.Namespace) -> int:
    registry = load_format_registry()
    rows = [
        {"format": format_id, **{key: entry[key] for key in ("domain", "ext", "text", "roles") if key in entry},
         **({"kinds": entry["kinds"]} if entry.get("kinds") else {})}
        for format_id, entry in registry.formats.items()
        if not args.domain or entry["domain"] == args.domain
    ]
    _print_json({"summary": summary(registry), "formats": rows})
    return EXIT_OK


def _path(args: argparse.Namespace) -> int:
    registry = load_format_registry()
    installed = None if args.no_detect else detect_runtimes(registry)
    try:
        report = route_report(registry, args.source, args.target, implemented_only=args.implemented_only,
                              limit=args.limit, installed=installed)
    except KeyError as exc:
        return _error("unknown_format", f"no format or effect matches {exc.args[0]!r}", EXIT_USAGE)
    _print_json(report)
    return EXIT_OK if report["routes"] else EXIT_INVALID


def _product(args: argparse.Namespace) -> int:
    hub = Hub(args.home)
    try:
        if args.action == "status":
            result = product_status(hub, Path(args.spec))
        else:
            result = build_product(hub, Path(args.spec), dry_run=args.dry_run)
    except ProductError as exc:
        return _error("product_error", str(exc), EXIT_USAGE)
    _print_json(result.to_dict())
    return EXIT_OK


def _runtimes(args: argparse.Namespace) -> int:
    registry = load_format_registry()
    found = detect_runtimes(registry)
    counts: dict[str, int] = {}
    for item in found.values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    _print_json({"summary": counts, "runtimes": found})
    return EXIT_OK


def _import_file(args: argparse.Namespace) -> int:
    hub = Hub(args.home)
    item = ArtifactInput(
        path=Path(args.path),
        kind=args.kind,
        producer=Producer(args.tool, args.tool_version, args.job, args.recipe),
        lineage=tuple(args.input or ()),
        qa_status=args.qa,
        privacy_class=args.privacy,
        project_id=args.project,
    )
    try:
        with hub.transaction() as conn:
            result = hub.register(conn, item)
    except ContractError as exc:
        return _error("contract_violation", str(exc), EXIT_CONTRACT)
    except KeyError as exc:
        return _error("lineage_not_registered", str(exc).strip("'\""), EXIT_NOT_FOUND)
    except StoreError as exc:
        return _error("input_error", str(exc), EXIT_USAGE)
    _print_json({"artifact_id": result.artifact_id, "kind": result.kind, "created": result.created})
    return EXIT_OK


def _ingest(args: argparse.Namespace) -> int:
    hub = Hub(args.home)
    try:
        with hub.transaction() as conn:
            result = ingest_manifest(
                hub,
                conn,
                Path(args.manifest),
                input_files=[Path(item) for item in args.input_file or ()],
                project_id=args.project,
                privacy_class=args.privacy,
            )
    except IngestError as exc:
        return _error("ingest_error", str(exc), EXIT_USAGE)
    except ContractError as exc:
        return _error("contract_violation", str(exc), EXIT_CONTRACT)
    except StoreError as exc:
        return _error("input_error", str(exc), EXIT_USAGE)
    _print_json({
        "format": result.adapter,
        "manifest_id": result.manifest.artifact_id,
        "inputs": [item.artifact_id for item in result.inputs],
        "outputs": [
            {"artifact_id": item.artifact_id, "kind": item.kind, "filename": item.filename, "created": item.created}
            for item in result.outputs
        ],
        "skipped": result.skipped,
    })
    return EXIT_OK


def _workspace(args: argparse.Namespace) -> int:
    hub = Hub(args.home)
    try:
        with hub.transaction() as conn:
            if args.command == "project":
                if args.action == "create":
                    workspace.create_project(conn, args.project_id, args.name or args.project_id, args.client, args.brand)
                    _print_json({"project_id": args.project_id, "created": True})
                elif args.action == "list":
                    _print_json(workspace.list_projects(conn))
                else:
                    _print_json(workspace.project_artifacts(conn, args.project_id))
            elif args.command == "assign":
                workspace.assign(conn, args.project_id, args.artifact_id)
                _print_json({"project_id": args.project_id, "assigned": len(args.artifact_id)})
            elif args.command == "review":
                previous = workspace.set_review(conn, args.project_id, args.artifact_id, args.status, args.comment)
                _print_json({"artifact_id": args.artifact_id, "from": previous, "to": args.status})
            elif args.command == "brand":
                if args.action == "register":
                    result = workspace.register_brand_kit(hub, conn, Path(args.target))
                    _print_json({"artifact_id": result.artifact_id, "created": result.created})
                else:
                    _print_json(workspace.load_brand_kit(hub, conn, args.target))
            elif args.command == "package":
                package = workspace.build_package(
                    hub, conn, args.project_id, Path(args.out), include_personal=args.include_personal
                )
                _print_json({
                    "artifact_id": package.package.artifact_id, "path": str(package.path), "files": package.files,
                })
    except workspace.WorkspaceError as exc:
        return _error("workspace_error", str(exc), EXIT_USAGE)
    except ContractError as exc:
        return _error("contract_violation", str(exc), EXIT_CONTRACT)
    return EXIT_OK


def _send(args: argparse.Namespace) -> int:
    hub = Hub(args.home)
    params = dict(item.split("=", 1) for item in args.param or ())
    try:
        result = send(
            hub, args.artifact_id, args.to, extra_ids=args.with_artifact or (), params=params,
            project_id=args.project, dry_run=args.dry_run,
        )
    except SendError as exc:
        return _error("send_error", str(exc), EXIT_USAGE)
    except ContractError as exc:
        return _error("contract_violation", str(exc), EXIT_CONTRACT)
    payload: dict[str, object] = {"send_id": result.send_id, "handoff": result.handoff_id, "dry_run": result.dry_run,
                                  "work_dir": str(result.work_dir)}
    if result.ingest is not None:
        payload["outputs"] = [
            {"artifact_id": item.artifact_id, "kind": item.kind, "filename": item.filename}
            for item in result.ingest.outputs
        ]
    _print_json(payload)
    return EXIT_OK


def _flow(args: argparse.Namespace) -> int:
    hub = Hub(args.home)
    try:
        if args.action == "run":
            flow_id = load_flow(hub, load_flow_file(Path(args.target)))
            status = run_flow(hub, flow_id)
        elif args.action == "resume":
            status = run_flow(hub, args.target)
        else:
            status = flow_status(hub, args.target)
    except FlowError as exc:
        return _error("flow_error", str(exc), EXIT_USAGE)
    _print_json(status.to_dict())
    return EXIT_OK if status.status in {"completed", "waiting_approval", "running", "pending"} else EXIT_USAGE


def _list(args: argparse.Namespace) -> int:
    hub = Hub(args.home)
    with hub.db.connect() as conn:
        rows = hub.db.list(conn, kind=args.kind, tool_id=args.tool, project_id=args.project, limit=args.limit)
    if args.json:
        _print_json([dict(row) for row in rows])
        return EXIT_OK
    for row in rows:
        print(
            f"{row['artifact_id'][:19]}  {row['kind']:<18} {row['qa_status']:<12} "
            f"{row['tool_id']:<18} {row['filename']}"
        )
    print(f"{len(rows)} artifact(s)")
    return EXIT_OK


def _show(args: argparse.Namespace) -> int:
    hub = Hub(args.home)
    with hub.db.connect() as conn:
        record = hub.db.get(conn, args.artifact_id)
        if record is None:
            return _error("not_found", args.artifact_id, EXIT_NOT_FOUND)
        record["used_by"] = hub.db.outputs(conn, args.artifact_id)
    _print_json(record)
    return EXIT_OK


def _lineage(args: argparse.Namespace) -> int:
    hub = Hub(args.home)
    try:
        with hub.db.connect() as conn:
            tree = hub.lineage_tree(conn, args.artifact_id, args.depth)
    except KeyError:
        return _error("not_found", args.artifact_id, EXIT_NOT_FOUND)
    _print_json(tree)
    return EXIT_OK


def _verify(args: argparse.Namespace) -> int:
    hub = Hub(args.home)
    with hub.db.connect() as conn:
        ids = [row["artifact_id"] for row in conn.execute("SELECT artifact_id FROM artifacts")]
    broken = [artifact_id for artifact_id in ids if not hub.store.verify(artifact_id)]
    if broken:
        for artifact_id in broken:
            print(f"FAIL: missing or corrupted object {artifact_id}")
        return EXIT_INTEGRITY
    print(f"OK: {len(ids)} stored artifact(s) match their sha256")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="production_hub", description=__doc__)
    parser.add_argument("--home", type=Path, default=None, help="Hub home (default .runtime/production-hub)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="validate the artifact schema and registries")
    formats = commands.add_parser("formats", help="list registered file formats (Everything-as-Spec)")
    formats.add_argument("--domain")
    path = commands.add_parser("path", help="find conversion routes between two formats, extensions or effects")
    path.add_argument("source", help="format id, effect id, or extension such as .scad")
    path.add_argument("target")
    path.add_argument("--implemented-only", action="store_true", help="only routes that run in Solo-Empire today")
    path.add_argument("--limit", type=int, default=5)
    path.add_argument("--no-detect", action="store_true", help="skip checking which runtimes are installed")
    commands.add_parser("runtimes", help="show which runtimes the conversion graph needs are installed")
    product = commands.add_parser("product", help="build a product.v1 spec into a hub flow (Everything-as-Spec)")
    product.add_argument("action", choices=["build", "status"])
    product.add_argument("spec", help="path to product.yaml")
    product.add_argument("--dry-run", action="store_true", help="validate and compile without registering or running")

    imp = commands.add_parser("import-file", help="register one file as an artifact")
    imp.add_argument("path")
    imp.add_argument("--kind", required=True)
    imp.add_argument("--tool", required=True, help="producing tool id, or 'external'")
    imp.add_argument("--tool-version", default="unknown")
    imp.add_argument("--job", required=True)
    imp.add_argument("--recipe")
    imp.add_argument("--input", action="append", help="lineage input artifact id (repeatable)")
    imp.add_argument("--qa", default="accepted", choices=["accepted", "rejected", "needs_review"])
    imp.add_argument(
        "--privacy", default="internal", choices=["public", "internal", "client-confidential", "personal"]
    )
    imp.add_argument("--project")

    ing = commands.add_parser("ingest", help="register every output of one tool job manifest")
    ing.add_argument("manifest")
    ing.add_argument("--input-file", action="append", help="input file of the job, registered as lineage")
    ing.add_argument("--project")
    ing.add_argument(
        "--privacy", default="internal", choices=["public", "internal", "client-confidential", "personal"]
    )

    lst = commands.add_parser("list", help="list registered artifacts")
    lst.add_argument("--kind")
    lst.add_argument("--tool")
    lst.add_argument("--project")
    lst.add_argument("--limit", type=int, default=200)
    lst.add_argument("--json", action="store_true")

    show = commands.add_parser("show", help="show one artifact record")
    show.add_argument("artifact_id")

    lin = commands.add_parser("lineage", help="show the input tree of an artifact")
    lin.add_argument("artifact_id")
    lin.add_argument("--depth", type=int, default=20)

    commands.add_parser("verify", help="re-hash every stored object")

    snd = commands.add_parser("send", help="send an artifact to another tool through a registered handoff")
    snd.add_argument("artifact_id")
    snd.add_argument("--to", required=True, help="target tool id")
    snd.add_argument("--with", dest="with_artifact", action="append", help="extra input artifact id")
    snd.add_argument("--param", action="append", help="adapter parameter key=value (repeatable)")
    snd.add_argument("--project")
    snd.add_argument("--dry-run", action="store_true", help="write the request and command without running")

    prj = commands.add_parser("project", help="create, list, or show projects")
    prj.add_argument("action", choices=["create", "list", "show"])
    prj.add_argument("project_id", nargs="?")
    prj.add_argument("--name")
    prj.add_argument("--client")
    prj.add_argument("--brand")

    asg = commands.add_parser("assign", help="add registered artifacts to a project")
    asg.add_argument("project_id")
    asg.add_argument("artifact_id", nargs="+")

    rev = commands.add_parser("review", help="move an artifact's review status in a project")
    rev.add_argument("project_id")
    rev.add_argument("artifact_id")
    rev.add_argument("status", choices=list(workspace.REVIEW_STATES))
    rev.add_argument("--comment")

    brd = commands.add_parser("brand", help="register or show a brand-kit.v1")
    brd.add_argument("action", choices=["register", "show"])
    brd.add_argument("target", help="brand kit JSON path (register) or brand id (show)")

    flow = commands.add_parser("flow", help="run, resume, or inspect a hub-flow.v1 sequence of handoffs")
    flow.add_argument("action", choices=["run", "resume", "status"])
    flow.add_argument("target", help="flow JSON path (run) or flow id (resume/status)")

    pkg = commands.add_parser("package", help="zip a project's approved artifacts for delivery")
    pkg.add_argument("project_id")
    pkg.add_argument("--out", required=True)
    pkg.add_argument("--include-personal", action="store_true")
    return parser


_HANDLERS = {
    "import-file": _import_file,
    "ingest": _ingest,
    "list": _list,
    "show": _show,
    "lineage": _lineage,
    "verify": _verify,
    "send": _send,
    "project": _workspace,
    "assign": _workspace,
    "review": _workspace,
    "brand": _workspace,
    "package": _workspace,
    "flow": _flow,
    "formats": _formats,
    "path": _path,
    "runtimes": _runtimes,
    "product": _product,
}


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # Thai text in messages must never crash a cp1252 Windows console
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "project" and args.action in ("create", "show") and not args.project_id:
        parser.error("project create/show needs a project id")
    if args.command == "check":
        return _check()
    return _HANDLERS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
