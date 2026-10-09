# Production Hub

Shared artifact contract and cross-tool handoff registry for the Solo Empire
production tools. Design: Production Hub spec (Solo Empire `docs/superpowers/specs/2026-09-30-production-hub-and-tool-coverage-design.md`).

Phase **H0** owns the contracts; phase **H1** adds a local content-addressed
library with lineage and ingest adapters for existing tool job manifests. The
Hub never runs tools or imports their code; tools keep ownership of their IR,
validators, and engines as listed in `catalog/production-tools.yaml` (Solo Empire `catalog/production-tools.yaml`).

## Library (H1)

The library lives in `.runtime/production-hub/` (Git-ignored; override with
`--home` or `PRODUCTION_HUB_HOME`): objects in `store/sha256/<2>/<hex>` and the
index in `hub.db`, separate from the control-plane database. Identical bytes
are stored once. Every registration is validated against `artifact.v1`, and a
failed transaction removes the objects it stored.

```bash
cd services/production-hub
python -m production_hub ingest <tool job manifest> [--input-file <input>] [--project <id>]
python -m production_hub import-file <file> --kind <kind> --tool <tool id|external> --job <id> [--input <artifact id>]
python -m production_hub list [--kind K] [--tool T] [--project P] [--json]
python -m production_hub show <artifact id>
python -m production_hub lineage <artifact id>
python -m production_hub verify
```

Exit codes: `0` ok, `1` registry check failed, `2` usage or input error,
`3` not found, `4` contract violation, `5` store integrity failure.

### Ingest adapters

`ingest` detects the manifest format, re-hashes every listed output, and
refuses the whole manifest when any hash differs from the tool's record. The
manifest itself is registered as `qa.manifest` and referenced from each output
(`attributes.qa_manifest_id`).

| Manifest | Tool | Lineage source |
| --- | --- | --- |
| `graphics-batch.v1` (`manifest-*.json`) | Graphics | explicit `--input-file` only; rejected items are skipped |
| `scene-to-graphics.pipeline.v1` | 3D Scene → Graphics | graphics outputs link to the scene render |
| `scene3d.qa.v1` | 3D Scene | `source_asset_sha256` |
| `spreadsheet-job.v1` | Spreadsheet | explicit `--input-file` only |
| `data-job.v1` | Data + Data Viz | explicit `--input-file` only |
| Documents job `manifest.json` | Documents | explicit `--input-file` only; QA must be `passed` |
| `pipeline-report.v1` | Monthly report chain | previous stage outputs; documents stage uses the chart and workbook hashes in its request |
| `audio-batch-result.v1` (`result.json`) | Audio batch | `tracks[].source_sha256`; rejected tracks are skipped |
| `audio-master.qa.v1` | Audio master | `source_sha256` |

Recorded input hashes link only when that input is already registered;
otherwise they are kept in `attributes.unregistered_inputs`. Video Studio
subtitles and audio extraction write no output manifest, so register those
files with `import-file`.

## Contracts and registries

| File | Contract | Purpose |
| --- | --- | --- |
| `schemas/artifact.v1.schema.json` | `artifact.v1` | One content-addressed file: sha256 id, kind, media type, producer, lineage, QA, privacy |
| `registry/kinds.yaml` | `artifact-kinds.v1` | Kind vocabulary and allowed media types per kind |
| `registry/tool-io.yaml` | `tool-io.v1` | Kinds each cataloged tool produces/consumes today (`produces`, `consumes`) and for planned handoffs (`planned_*`) |
| `registry/handoffs.yaml` | `handoffs.v1` | Cross-tool edges; `implemented` edges cite a versioned request contract and an evidence file |
| `registry/formats.yaml` | `formats.v1` | Everything-as-Spec: file formats by domain (authored/generated/binary, source/ir/artifact, Hub kinds), domain automation caps L0–L6, and real-world effects |
| `registry/converters.yaml` | `converters.v1` | Conversion graph: what turns one format into another, with which tool, at which level, and whether it is implemented, available (needs a runtime) or planned |

`validate_artifact` adds the checks JSON Schema cannot express: the kind must
exist, the media type must be allowed for that kind, a record cannot list
itself as lineage, `created_at` carries a timezone, and `qa.manifest_ref` is a
relative POSIX path.

## Conversion routes

```bash
python -m production_hub formats --domain cad
python -m production_hub path .scad effect.printed-part
python -m production_hub path .srt delivery-package --implemented-only
```

`path` accepts a format id, an effect id or an extension and returns the
shortest routes with their tools, status, required runtimes and any step that
needs human approval. Effects are only reachable through L6 converters marked
`approval: human`; see `docs/superpowers/specs/2026-09-30-everything-as-spec-design.md`.

## HTTP API (H4)

```bash
python -m production_hub.api --port 3460   # binds to 127.0.0.1 only
```

Mutations require the `X-Hub-Client: console` header and, when
`PRODUCTION_HUB_API_TOKEN` is set, a matching `Authorization: Bearer` token.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/health` | service liveness |
| `GET /api/v1/registry` | kinds, tools, handoffs (with adapter availability) |
| `GET /api/v1/artifacts[?kind=&tool=&project=]` | library index |
| `GET /api/v1/artifacts/{id}[/lineage|/content]` | one record, its input tree, or the stored bytes |
| `GET /api/v1/projects`, `GET /api/v1/projects/{id}` | project review state |
| `GET /api/v1/flows`, `GET /api/v1/flows/{id}` | loaded flows and per-step progress |
| `GET /api/v1/flow-templates` | ready-made flow templates with their artifact slots and parameters |
| `POST /api/v1/flow-templates/{template}` | build a `hub-flow.v1` from a template and queue its first run |
| `GET /api/v1/formats`, `/api/v1/runtimes`, `/api/v1/path` | conversion graph |
| `POST /api/v1/send` | queue a send; returns `job_id` immediately (`dry_run: true` answers synchronously) |
| `POST /api/v1/flows` | validate + load a `hub-flow.v1` definition and queue its first run |
| `POST /api/v1/flows/{id}/resume` | queue a resume (409 `flow_busy` while the flow is already queued or running) |
| `GET /api/v1/jobs/{id}` | queued/running/completed/failed with a short log and the final result |
| `POST /api/v1/jobs/{id}/cancel` | cancel an external queued job; executing effects require owner review |

Default development mode (`PRODUCTION_HUB_JOB_MODE=inline`) runs background
jobs on a small thread pool and marks interrupted **inline** jobs failed on
restart. Existing CLI send/flow operations remain synchronous:

```bash
python -m production_hub send <artifact id> --to <tool> [--param k=v ...] [--with <artifact id>] [--dry-run]
python -m production_hub flow run <flow.json>     # validate + run until a gate or failure
python -m production_hub flow resume <flow id>    # continue after an approval or fixed failure
python -m production_hub flow status <flow id>    # step progress
```

### Durable worker pilot

`PRODUCTION_HUB_JOB_MODE=external` makes API submission commit a queued record
before returning **202** with the existing `job_id`, `kind`/`flow_id` and
`status` fields. HTTP does not execute these jobs and restarting HTTP does
not fail them. `GET /api/v1/jobs/{id}` preserves `.v1` status names and result
shape, adding `schema_version: job.v1`, `queue_mode`, `attempts`,
`max_attempts`, `available_at`, `lease_owner`, `lease_until`, `effect_state`
and `cancel_requested`. Payloads, idempotency keys, hashes and lease tokens
are excluded from polling output. Durable error/log messages are bounded
and do not copy raw exceptions or tool output.

This is a **single-host, shared-filesystem SQLite pilot**, with one API
replica and a worker process on the same host and owned persistent volume.
Competing worker claims are serialized transactionally; this does not make
SQLite an HA database or authorize additional service replicas. Separate
Railway/Render services do **not** share volumes by default. Do not deploy API
and worker to different PaaS services using this queue; that requires the
owner's later PostgreSQL job store and object-storage artifact migration.
Keep production migration status pending until those gates pass.

Start both processes with the same explicit owned home outside the root
repository, after the deployment owner verifies mount persistence, backup
and restore. The readiness flag is an owner assertion, not a mount probe:

```bash
PRODUCTION_HUB_JOB_MODE=external python -m production_hub.api --home /srv/production-hub
python -m production_hub.worker --home /srv/production-hub --persistent-volume-confirmed
# Finite scheduler invocation: at most one claim, then exit and close DB connections.
python -m production_hub.worker --home /srv/production-hub --persistent-volume-confirmed --once
```

The worker only dispatches persisted `send`/`flow` jobs through existing
owned adapters; requests cannot supply a shell command. Approval steps in
flows remain enforced by those adapters. This pilot does not extract tool
runtimes, remove their existing declared filesystem dependencies, or create
a new root-repository mount. A remote release still needs packaged tool
runtimes, service identity/authorization, storage pointers and native-renderer
verification. The existing API console/token checks remain in place;
GET polling has its existing local-console access policy and needs the
production identity/access boundary before any public release.

Send, flow and template submission accept an optional body
`idempotency_key`. Keep a stable key for request retries. A matching kind and
canonical payload returns the same job, including its final status;
different payloads return `409 idempotency_conflict`. Cross-process flow
uniqueness blocks another job while a flow is queued, running, or awaiting
effect review (`409 flow_busy`). Idempotency records currently remain for
the life of the database; there is no automatic pruning.

In external mode, template requests hash the template ID and canonical
client body before generating a flow ID. Repeated matching requests reuse
the original job/flow without another allocation. Project, flow and job
inserts share one transaction; a rejected job insert rolls them all back.
Use external mode for idempotent template submissions.
Inline template requests containing `idempotency_key` return
`400 unsupported_idempotency` before allocating a flow; inline template
requests without a key retain development behavior.
Inline keyed flow submissions also reuse an existing running job through
the database's transactional uniqueness check.

Each claim gets a new token, a 60-second lease and a heartbeat every 20
seconds (worker `--lease-seconds` can tune this). Token, owner, unexpired
lease and cancellation checks fence every reservation, progress write and
completion. Only read-only preparation can retry automatically: at most
three attempts, persisted exponential backoff (2, 4 seconds, capped at 300
seconds), and final `failed`. Expired leases before reservation can be
reclaimed within the same attempt cap.

Before invoking a tool, the worker atomically records an effect reservation
in `job_effects`. A crash, exception or lease loss after reservation yields
`status=failed`, `effect_state=needs_review`; the job and flow remain blocked
from automatic retries. This conservative reservation is an intent ledger,
not proof of exactly-once effects and not a transactional remote-delivery
outbox. An external tool may finish after its lease expires; database
fencing cannot revoke external writes.

Cancellation before reservation sets `failed`, `cancel_requested=1` and
prevents execution. Cancellation after reservation invalidates the lease
and requires review; it does not claim to kill a subprocess or roll back an
upload. Owner reconciliation must first stop/verify termination of the old
worker, inspect remote effects and existing send/flow progress, then record
an evidence reference. Reconciliation never executes a tool or retries a job:

```bash
python -m production_hub.worker --home /srv/production-hub --persistent-volume-confirmed \
  --reconcile-job <job-id> --actor <owner-id> --worker-stopped-confirmed \
  --evidence-ref <owned-evidence-reference> --outcome completed \
  --result-json '<verified-original-result-object>'
# If completion cannot be confirmed, final abandonment keeps the job failed:
python -m production_hub.worker --home /srv/production-hub --persistent-volume-confirmed \
  --reconcile-job <job-id> --actor <owner-id> --worker-stopped-confirmed \
  --evidence-ref <owned-evidence-reference> --outcome abandoned
```

Successful review sets `effect_state=reconciled` and records owner/evidence
events in `job_events`. Only then can the owner intentionally submit a new
job or resume a flow with a **new** request key and the existing approval
gates. Do not place private transcripts, credentials or raw payloads in
evidence references. Queue age/attempts/lease state are observable through
polling and timestamps; a metrics exporter, retention policy and distributed
correlation instrumentation remain later migration work.

Send parameters: every adapter declares `required_params` and, for the
console, `optional_params` with allowed choices and defaults (exposed on each
`sends` entry of `GET /api/v1/artifacts/{id}` and enforced centrally, so a
value outside its choices is rejected before any tool runs).

## Check

```bash
task production-hub:check
```

The check validates the schema, requires one `tool-io` entry per catalog tool,
rejects unknown kinds, and verifies every handoff: the source tool must
produce the kind and the target tool must consume it (shipped kinds for
`implemented` edges, shipped or planned kinds for `planned` edges). It also
checks the format registry: every kind has a format, every handoff is linked by
exactly one converter with a matching status, implemented converters only
claim formats their tool produces, effects are L6 with human approval, and no
converter exceeds its domain's `max_level`.

## Development

```bash
cd services/production-hub
PYTHONPATH=. python -m pytest
python -m ruff check .
python -m mypy production_hub tests
```

## Adding a handoff

1. Add any new kind to `registry/kinds.yaml`.
2. Declare it in the source tool's `produces` and the target tool's `consumes`
   in `registry/tool-io.yaml` (or the `planned_*` lists while it is planned).
3. Add the edge to `registry/handoffs.yaml`. Switch it to `implemented` only
   when a versioned request contract and evidence file exist.
4. Run `task production-hub:check`.
