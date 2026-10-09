# Workflow Production API — Workflow V1A

Local-first **Form-to-Document Flow** boundary. The service accepts a
structured workflow DAG and form values, validates dependencies, typed fields,
permissions, retry caps, decision branches, and SLA timeouts, then emits three
reviewable artifacts:

- a topological `workflow-plan.v1` execution plan (decisions evaluated against
  the form values; steps on a path not taken are `skipped`);
- a deterministic Markdown document rendered from the validated form;
- a BPMN 2.0 diagram (`<prefix>.bpmn`, `application/xml`) with lanes per role,
  gateways, timers, and diagram layout. It opens in Camunda Modeler or bpmn.io
  and is exported non-executable.

Decision steps (`kind: "decision"`) take ordered `branches`
(`target`, `field_id`, `operator` = `eq|ne|gt|gte|lt|lte`, `value`) and a
required `default_target`. Each target depends only on its decision. Any step
may set `timeout_minutes`; a `human_review` step may also set `escalate_to`
(an allowed role). See [docs/PARITY.md](docs/PARITY.md) for how this compares
with Power Automate, Camunda, and Google Forms, and for the roadmap.

V1A is plan-only. It does not call external APIs, publish documents, send
notifications, or invoke an LLM. Every accepted job reports `model_calls=0`.
An idempotency key protects writes and replay verifies the manifest, artifact
hashes, byte sizes, QA result, and pinned recipe metadata.

## Local smoke run

```powershell
python -m pytest services/workflow-production-api/tests packages/workflow-production-ir/tests -q
Set-Location services/workflow-production-api
python -m workflow_production.cli build --input ..\..\workflow.json --output ..\..\.runtime\workflow-production --idempotency-key demo-1
python -m workflow_production.scripts.benchmark --fixture tests\fixtures\workflows_20.json --output-dir ..\..\.runtime\workflow-benchmark
```

The input can be a workflow object or `{ "workflow": { ... } }`. The HTTP
surface is `GET /health` and `POST /v1/workflows/plans` with
`{ "idempotency_key": "...", "workflow": { ... } }`.

The benchmark uses synthetic data and local files only. There is no deployment
target for this pilot.

## AI brief adapter (V1B)

`workflow_production.provider.ProviderWorkflowAdapter` implements the shared
[`brief-adapter.v1`](../../packages/brief-adapter-kit/README.md) pattern
(spec REQ-AI-01..09, REQ-WFL-01/02, AC-WFL-01a). A model *proposes* a
plan-only `WorkflowRequest` DAG from a process description. It may use only
these building blocks:

- the registered step kinds (`collect_form`, `validate`, `human_review`,
  `render_document`);
- one role per step from the role registry (`requester`, `manager`, `hr`,
  `finance`, `ops`, `legal`), which the caller can narrow with
  `allowed_roles`;
- typed form fields and plain names.

The model never supplies form values, permissions, execution settings,
external calls, or markup.

Checks run in this order:

1. **Allowlists.** An unknown key, step kind, role, or field type, markup, or
   a retry above 3 rejects the proposal.
2. **The existing `workflow_ir.validate_request`.** A cycle, a missing
   dependency, a duplicate id, or a missing collect/render step rejects it. An
   invalid DAG is never accepted silently.
3. **Grounding.** Every step carries a verbatim `evidence` quote from the
   description, and that quote must name both the step's action and its role.
   Field labels must be stated, and a retry above 1 must be stated. A step
   without a role, or anything ungrounded, becomes `needs_input`.

Engine-owned normalization and guards are deterministic and never depend on
prompt wording alone:

- Ids belong to the engine. A unique plain-text `step_id` or `field_id` that
  is not snake_case (for example Thai text) is renamed to `step_<n>` or
  `field_<n>`, and its dependencies are remapped. Duplicate, markup-like, or
  non-string ids still reject the proposal.
- The kind of a `validate` or `human_review` step follows its evidence. If
  the quote names only the other of those two kinds, the step takes that
  kind. Both are middle steps, so the DAG shape cannot change.
- An approval or review stated in the description must appear as a
  `human_review` step. Otherwise the result is `needs_input`
  (`stated_review_missing`).
- An order stated in the description ("X after Y", "X before Y",
  "หลังจาก", "ก่อนที่") must hold in the DAG. A plan that contradicts it,
  including any plan for a circular stated order, is rejected
  (`dependency_contradicts_brief`). "After that" and "หลังจากนั้น" are read as
  "then", not as a reversed order.

A complete proposal has empty `form_values`. The user fills them in, reviews
the proposal, and then runs `build`.

```text
python -m workflow_production.cli brief-shadow --provider-config .runtime/workflow-provider.json \
  --cases tests/fixtures/workflow_brief30_cases.json --split tests/fixtures/workflow_brief30_split.json \
  --output .runtime/workflow-shadow-report.json
python -m workflow_production.cli brief --input brief.json --output .runtime/workflow-proposal.json \
  --idempotency-key demo-ai-1 --provider-config .runtime/workflow-provider.json
```

The adapter fails closed without a `workflow-provider-config.v1` file
(`provider_not_configured`). `brief` refuses any config whose `mode` is not
`live` (`shadow_mode_only`). Instruction-like descriptions abstain with zero
calls, and each job makes no more than two calls. The default `prompt_version` is
`workflow-brief.v2`. It asks for ASCII snake_case ids and lists each allowed
role with the registry words the grounding accepts for it.

`brief-shadow` evaluates EVAL-WFL-brief30 (30 synthetic cases, dev/held-out
split) and writes a metadata-only report. The gate follows spec §4.2:

- critical exact match ≥ 95% (step kinds, roles, dependencies, and fields);
- `needs_input` recall 100%;
- adversarial silent passes 0, model calls ≤ 2, canary leaks 0;
- for AC-WFL-01a, invalid DAGs passed silently 0 and steps without a role 0;
- form values from the model 0;
- structured cases make zero model calls.

The deterministic `build` path never imports the kit.

The V1B adapter does not propose `decision` steps or timeouts; those kinds and
keys are outside its allowlist, so it rejects them. `brief` writes
`{"workflow": ..., "proposal": {adapter, mode, model_calls, provided,
inferred}}`. `build` copies the `proposal` metadata into the job trace. The
metadata contains no description text.

## Job history, trace & manual steps

Every new `build` attempt (accepted, rejected, or failed; replays excluded)
records the job with [`production-trace`](../../packages/production-trace/README.md):

| File | Where | What |
|---|---|---|
| `jobs/<job_id>/trace.json` | output dir | Input hash, the steps in order with structural parameters and durations, the validated-IR hash, AI proposal metadata, artifact sha256 values, and the QA outcome. Steps: `validate_request`, `compile_workflow`, `route_decisions`, `render_execution_plan`, `render_document`, `render_bpmn`, `write_package`, `run_workflow_qa`, `write_manifest` |
| `jobs/<job_id>/ir.json` | output dir | The exact validated `workflow-ir.v1` the engine executed |
| `jobs/<job_id>/MANUAL-STEPS.md` | output dir | How to build the same thing by hand: Google Forms (each question, type, required, validation, preview values), Camunda Modeler (pool, lanes, each node with its ID/type/lane/column, retries, candidate groups, timers, then every sequence flow with FEEL conditions and the default flow), Google Docs (the document), plus a Power Automate mapping |
| `jobs/<job_id>/<artifacts>` | output dir | Hard links (or copies) of the delivered files, so `verify` re-hashes the same bytes |
| `history.jsonl` | output dir | One metadata line per attempt |

Traces and history never contain form values, names, or document text. Long
values are hashed, and failure notes are codes only. The manual does contain
the values a person must type, so it has the same sensitivity as the document.
The manifest's `metadata.trace_job_id` links an accepted package to its trace.
Tracing never masks a failure: an engine exception is recorded as `failed` and
then re-raised.

```text
python -m production_trace history --root .runtime/workflow-production
python -m production_trace show    --root .runtime/workflow-production <job_id>
python -m production_trace manual  --root .runtime/workflow-production <job_id>
python -m production_trace verify  --root .runtime/workflow-production <job_id>
```

The CLI needs `packages/production-trace` and `packages/workflow-production-ir`
on `PYTHONPATH`, as production-hub and production-pipelines set them.

Recipe `form-to-document-v2` changes the input hash. A replay of a key built
under v1 returns `idempotency_key_reused_with_different_input`, so use a new
key to rebuild.
