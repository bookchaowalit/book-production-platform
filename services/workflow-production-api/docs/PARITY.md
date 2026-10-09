# Workflow V1A — feature parity with real process tools

Honest comparison of `workflow-v1a` (deterministic, **plan-only**
form-to-document engine, recipe `form-to-document-v2`) against Microsoft Power
Automate, Camunda (Modeler + engine), and Google Forms. "Have" means the
engine produces it deterministically today, with QA. "Partial" means a
limited version exists. "Missing" means it does not exist.

V1A never runs a step, calls an API, sends a message, or deploys. It plans the
process, renders the document, and exports the diagram. Gaps marked "by design"
follow from that.

## Capability matrix

| # | Capability | Power Automate | Camunda | Google Forms | workflow-v1a | Priority |
|---|---|---|---|---|---|---|
| 1 | Typed form fields (text/number/date/yes-no), required | Microsoft Forms | Camunda Forms | have | **have** (text, integer, decimal, date, boolean) | — |
| 2 | Choice lists (dropdown/radio/checkbox options) | have | have | have | **missing** (boolean only) | P1 |
| 3 | Field validation rules (min/max, regex, length) | have | have | have | **partial** (type checks only) | P1 |
| 4 | Steps with dependencies (DAG), cycle detection | have | have | n/a | **have** | — |
| 5 | Roles / lanes, permission allow-list | partial (assignees) | have (lanes, candidate groups) | n/a | **have** | — |
| 6 | Human approval step | have (Approvals) | have (user task) | n/a | **have** (`human_review`, role required) | — |
| 7 | Conditional branches (exclusive gateway, default path) | have (Condition/Switch) | have | section-based branching | **have** (new in v2: `decision` step, ordered conditions eq/ne/gt/gte/lt/lte, required default, validated by the DAG validator, evaluated deterministically against form values) | — |
| 8 | Parallel split / join | have | have | n/a | **partial** (fan-out via dependencies; joins export as inclusive gateways; no explicit parallel gateway step) | P2 |
| 9 | Retry policy | have (count + interval) | have (retries + backoff) | n/a | **partial** (`max_attempts` ≤ 3; no interval/backoff) | P2 |
| 10 | SLA / timeouts, escalation | have (timeout + run-after) | have (boundary timer) | n/a | **have** (new in v2: `timeout_minutes`, `escalate_to` role for reviews → BPMN boundary timer, non-interrupting escalation) | — |
| 11 | BPMN 2.0 export with diagram | n/a | have (native) | n/a | **have** (new in v2: `.bpmn` with pool, lanes, DI layout, FEEL conditions, zeebe retries/candidate groups; opens in Camunda Modeler/bpmn.io) | — |
| 12 | Generated document from form values | have (Word template) | via connectors | n/a | **have** (Markdown) | — |
| 13 | DOCX/PDF document output | have | via connectors | n/a | **missing** (hand off to documents-v1a) | P2 |
| 14 | Execute the flow / connectors / API calls | have | have | n/a | **missing** (by design: plan-only, `external_effects_enabled = false`) | P3 |
| 15 | Notifications (e-mail/Teams/LINE) | have | via connectors | e-mail on submit | **missing** (by design) | P3 |
| 16 | Loops / multi-instance (per line item) | have (Apply to each) | have | n/a | **missing** | P2 |
| 17 | Sub-processes / reusable flows | have (child flows) | have (call activity) | n/a | **missing** | P3 |
| 18 | Run history / audit trail | have | have (Operate) | responses sheet | **have** (`history.jsonl`, `trace.json`, replay-safe manifest, `production-trace verify`) | — |
| 19 | Manual build guide | n/a | n/a | n/a | **have** (`MANUAL-STEPS.md`: Google Forms + Camunda Modeler + Google Docs, Power Automate mapping) | — |
| 20 | AI-proposed workflow from a description | Copilot | partial | n/a | **have** (V1B adapter, grounded, shadow-gated). It does **not** propose decisions or timers: unknown step kinds are rejected (fail-closed) | P2 |

## Implemented in this round (recipe `form-to-document-v2`)

1. **Conditional branches (exclusive gateway).** A `decision` step has ordered
   `branches` (`target`, `field_id`, `operator`, `value`) and a required
   `default_target`. The validator rejects:
   - no conditions or no default;
   - a duplicate target or an unknown target;
   - a target that depends on anything other than its decision;
   - a step after a decision that is not one of its paths;
   - an unknown field;
   - an order operator (`gt`/`gte`/`lt`/`lte`) on a text or boolean field;
   - a value of the wrong type;
   - a decision that does not follow `collect_form`;
   - form values that route around `render_document`.

   `route_workflow` evaluates the decisions. The plan marks steps on the path
   not taken as `skipped`. A step whose inputs were all skipped is skipped too,
   and a merge still runs when one of its inputs ran. QA allows only
   `planned`/`skipped`, never a skipped `render_document`, and checks that each
   decision's `selected_target` is one of its paths.
2. **SLA timeouts and escalation.** `timeout_minutes` (1–43,200) on any
   non-decision step. `escalate_to` (an allowed role, `human_review` only,
   timeout required) appears in the plan and in the BPMN as a boundary timer
   `PT<n>M`.
3. **BPMN 2.0 XML export.** The third artifact is `<prefix>.bpmn`
   (`application/xml`). The file includes:
   - one participant, and one lane per role (`system` for steps without a role);
   - `userTask` for collect/review and `serviceTask` for validate/render, with
     `zeebe:taskDefinition` retries;
   - `exclusiveGateway` with FEEL conditions and a default flow;
   - `inclusiveGateway` joins;
   - boundary timers, with an escalation user task when `escalate_to` is set
     and a "Timed out" end event otherwise;
   - full BPMNDI layout.

   `isExecutable="false"` (plan-only). `run_bpmn_qa` checks the export
   against the plan: node types, every dependency flow (also through joins),
   start/end events, flow refs, timers, gateway default, and DI shapes/edges.
   It also refuses any DTD or entity. Replay re-runs this QA.

## Roadmap (prioritised, not implemented)

- **P1 Choice fields** (`choice` with an options list) and **validation rules**
  (min/max, length, pattern). Both are deterministic and map 1:1 to Google
  Forms and Camunda Forms.
- **P2 V1B adapter support for decisions/timeouts.** The adapter would need
  grounding for each condition (field, operator, threshold quoted in the
  description) before `decision` joins its allowlist.
- **P2 Explicit parallel gateway, retry interval/backoff, multi-instance
  steps.**
- **P2 DOCX/PDF document output** by handing the rendered document IR to
  `documents-v1a`.
- **P3 Execution, connectors, notifications, sub-processes.** Out of scope
  for a plan-only engine. These would belong to a separate, approval-gated
  runtime.
